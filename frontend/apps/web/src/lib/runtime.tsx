"use client";

/** Client runtime: session (display identity + CSRF), typed API clients, React Query, and the
 * step-up interceptor that turns the cell's STEP_UP_REQUIRED into an in-place passkey check. */
import {
  ApiError,
  createCellApi,
  createFlowApi,
  createTransport,
  type CellApi,
  type FlowApi,
} from "@zoiko/api-client";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, ErrorState, ToastProvider } from "@zoiko/ui";
import { Fingerprint } from "lucide-react";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { RouterProvider } from "react-aria-components";

import { completeAppSession, getPasskey, passkeyErrorMessage } from "./webauthn";

export type Subject = { sub: string; tenantId?: string; acr?: string; authTime?: number };
type SessionInfo = { authenticated: true; subject: Subject; csrf: string } | { authenticated: false };

type Runtime = {
  api: CellApi;
  flow: FlowApi;
  clientId: string;
  session: SessionInfo | undefined;
  refreshSession: () => Promise<void>;
  /** Runs a cell call; on STEP_UP_REQUIRED asks for a passkey, refreshes tokens and retries once. */
  protect: <T>(run: () => Promise<T>) => Promise<T>;
  signOut: () => Promise<void>;
};

const RuntimeContext = createContext<Runtime | null>(null);

export function useRuntime(): Runtime {
  const r = useContext(RuntimeContext);
  if (!r) throw new Error("useRuntime outside RuntimeProvider");
  return r;
}

export function useApi(): CellApi {
  return useRuntime().api;
}

function makeQueryClient(onLoginRequired: () => void) {
  const handle = (e: unknown) => {
    if (e instanceof ApiError && e.isLoginRequired) onLoginRequired();
  };
  return new QueryClient({
    queryCache: new QueryCache({ onError: handle }),
    mutationCache: new MutationCache({ onError: handle }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        // Never retry client errors (403/404/409/422): they are answers, not glitches.
        retry: (count, e) => !(e instanceof ApiError && e.status < 500) && count < 2,
      },
      mutations: { retry: false },
    },
  });
}

export function RuntimeProvider({ clientId, children }: { clientId: string; children: ReactNode }) {
  const router = useRouter();
  const redirecting = useRef(false);
  const [queryClient] = useState(() =>
    makeQueryClient(() => {
      if (redirecting.current || typeof window === "undefined") return;
      if (window.location.pathname.startsWith("/sign-in")) return;
      redirecting.current = true;
      const returnTo = window.location.pathname + window.location.search;
      window.location.assign(`/sign-in?returnTo=${encodeURIComponent(returnTo)}&reason=expired`);
    }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider navigate={(href) => router.push(href)}>
        <ToastProvider>
          <RuntimeInner clientId={clientId}>{children}</RuntimeInner>
        </ToastProvider>
      </RouterProvider>
    </QueryClientProvider>
  );
}

function RuntimeInner({ clientId, children }: { clientId: string; children: ReactNode }) {
  const queryClient = useQueryClient();
  const sessionQuery = useQuery({
    queryKey: ["session"],
    queryFn: async (): Promise<SessionInfo> => {
      const res = await fetch("/api/auth/session", { cache: "no-store", credentials: "same-origin" });
      return (await res.json()) as SessionInfo;
    },
    staleTime: 60_000,
  });
  const csrfRef = useRef<string | undefined>(undefined);
  csrfRef.current = sessionQuery.data?.authenticated ? sessionQuery.data.csrf : undefined;

  const api = useMemo(() => createCellApi(createTransport({ baseUrl: "/api/bff", csrf: () => csrfRef.current })), []);
  const flow = useMemo(() => createFlowApi(createTransport({ baseUrl: "" }), clientId), [clientId]);

  const [stepUp, setStepUp] = useState<null | { resolve: (ok: boolean) => void; reason?: string }>(null);
  const [stepUpError, setStepUpError] = useState<unknown>(null);
  const [stepUpPending, setStepUpPending] = useState(false);

  const refreshSession = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["session"] });
  }, [queryClient]);

  const protect = useCallback(
    async <T,>(run: () => Promise<T>): Promise<T> => {
      try {
        return await run();
      } catch (e) {
        if (!(e instanceof ApiError) || !e.isStepUpRequired) throw e;
        const ok = await new Promise<boolean>((resolve) => setStepUp({ resolve, reason: e.message }));
        if (!ok) throw e;
        return run();
      }
    },
    [],
  );

  const signOut = useCallback(async () => {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfRef.current ? { "x-zid-csrf": csrfRef.current } : {},
    }).catch(() => undefined);
    queryClient.clear();
    window.location.assign("/signed-out");
  }, [queryClient]);

  async function performStepUp() {
    setStepUpPending(true);
    setStepUpError(null);
    try {
      const optionsJSON = await flow.stepUpOptions();
      const credential = await getPasskey(optionsJSON);
      await flow.passkeyVerify(credential);
      await completeAppSession();
      await refreshSession();
      stepUp?.resolve(true);
      setStepUp(null);
    } catch (e) {
      setStepUpError(e instanceof ApiError ? e : new Error(passkeyErrorMessage(e)));
    } finally {
      setStepUpPending(false);
    }
  }

  const value = useMemo<Runtime>(
    () => ({ api, flow, clientId, session: sessionQuery.data, refreshSession, protect, signOut }),
    [api, flow, clientId, sessionQuery.data, refreshSession, protect, signOut],
  );

  return (
    <RuntimeContext.Provider value={value}>
      {children}
      <Dialog
        isOpen={stepUp !== null}
        onOpenChange={(open) => {
          if (!open && !stepUpPending) {
            stepUp?.resolve(false);
            setStepUp(null);
            setStepUpError(null);
          }
        }}
        title="Confirm it's you"
        size="sm"
        footer={
          <>
            <Button
              variant="secondary"
              isDisabled={stepUpPending}
              onPress={() => {
                stepUp?.resolve(false);
                setStepUp(null);
                setStepUpError(null);
              }}
            >
              Cancel
            </Button>
            <Button pending={stepUpPending} icon={<Fingerprint aria-hidden className="size-4" />} onPress={() => void performStepUp()}>
              Verify with passkey
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p>This action needs a fresh, high-assurance sign-in. Your entries on this page are kept.</p>
          {stepUpError ? <ErrorState error={stepUpError} title="Verification failed" /> : null}
        </div>
      </Dialog>
    </RuntimeContext.Provider>
  );
}
