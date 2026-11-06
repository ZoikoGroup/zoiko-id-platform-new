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
  type RouteStepUp,
  type StepUpChallenge,
  type StepUpDraft,
} from "@zoiko/api-client";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Button, Dialog, ErrorState, ToastProvider } from "@zoiko/ui";
import { Fingerprint, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { RouterProvider } from "react-aria-components";

import {
  StepUpCancelled,
  TIER_COPY,
  clock,
  isActionBound,
  refusalText,
  secondsLeft,
  whyRequired,
} from "./step-up";
import {
  completeAppSession,
  getPasskey,
  passkeyErrorMessage,
} from "./webauthn";

export type Subject = {
  sub: string;
  tenantId?: string;
  acr?: string;
  authTime?: number;
};
type SessionInfo =
  | { authenticated: true; subject: Subject; csrf: string }
  | { authenticated: false };

type Runtime = {
  api: CellApi;
  flow: FlowApi;
  clientId: string;
  session: SessionInfo | undefined;
  refreshSession: () => Promise<void>;
  /** Runs a cell call; on STEP_UP_REQUIRED asks for a passkey, refreshes tokens and retries once. */
  protect: <T>(run: () => Promise<T>) => Promise<T>;
  /** Asks for a fresh passkey check now (for an answer that says "step up first"). Resolves true once confirmed. */
  confirmIdentity: () => Promise<boolean>;
  /** Asks the server whether this command needs its own confirmation. If so, shows exactly what will happen, checks the passkey and
   * resolves the proof to send with the command; resolves undefined when none is needed; rejects with StepUpCancelled if declined. */
  proveAction: (draft: StepUpDraft) => Promise<string | undefined>;
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

export function makeQueryClient(onLoginRequired: () => void) {
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
        retry: (count, e) =>
          !(e instanceof ApiError && e.status < 500) && count < 2,
      },
      mutations: { retry: false },
    },
  });
}

export function RuntimeProvider({
  clientId,
  children,
}: {
  clientId: string;
  children: ReactNode;
}) {
  const router = useRouter();
  const [queryClient] = useState(() => {
    let redirecting = false;
    return makeQueryClient(() => {
      if (redirecting || typeof window === "undefined") return;
      if (window.location.pathname.startsWith("/sign-in")) return;
      redirecting = true;
      const returnTo = window.location.pathname + window.location.search;
      window.location.assign(
        `/sign-in?returnTo=${encodeURIComponent(returnTo)}&reason=expired`,
      );
    });
  });
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

const loadSession = async (): Promise<SessionInfo> => {
  const res = await fetch("/api/auth/session", { cache: "no-store", credentials: "same-origin" });
  return (await res.json()) as SessionInfo;
};

function RuntimeInner({
  clientId,
  children,
}: {
  clientId: string;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const sessionQuery = useQuery({
    queryKey: ["session"],
    queryFn: loadSession,
    staleTime: 60_000,
  });
  const csrfRef = useRef<string | undefined>(undefined);
  csrfRef.current = sessionQuery.data?.authenticated
    ? sessionQuery.data.csrf
    : undefined;

  // The cell's central step-up guard names the change to prove; the proof is made here and the same request is retried once (ZID-ENG-DEC-009).
  const routeProver = useRef<((need: RouteStepUp) => Promise<string | undefined>) | null>(null);
  const api = useMemo(
    () =>
      createCellApi(
        createTransport({
          baseUrl: "/api/bff",
          // a write sent before the session has loaded would carry no token and be refused ("could not be verified"): wait for it
          csrf: async () => {
            if (csrfRef.current) return csrfRef.current;
            const s = await queryClient.fetchQuery({ queryKey: ["session"], queryFn: loadSession, staleTime: 0 });
            return s.authenticated ? s.csrf : undefined;
          },
          onRouteStepUp: (need) => routeProver.current?.(need) ?? Promise.resolve(undefined),
        }),
      ),
    [queryClient],
  );
  const flow = useMemo(
    () => createFlowApi(createTransport({ baseUrl: "" }), clientId),
    [clientId],
  );

  const [stepUp, setStepUp] = useState<null | {
    resolve: (ok: boolean) => void;
    reason?: string;
  }>(null);
  const [stepUpError, setStepUpError] = useState<unknown>(null);
  const [stepUpPending, setStepUpPending] = useState(false);
  const pendingStepUp = useRef<Promise<boolean> | null>(null);

  const refreshSession = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["session"] });
  }, [queryClient]);

  const protect = useCallback(async <T,>(run: () => Promise<T>): Promise<T> => {
    try {
      return await run();
    } catch (e) {
      if (!(e instanceof ApiError) || !e.isStepUpRequired || isActionBound(e))
        throw e; // a command's own proof is not a fresh sign-in
      // Calls failing together (e.g. a page's queries) share one passkey prompt.
      pendingStepUp.current ??= new Promise<boolean>((resolve) =>
        setStepUp({ resolve, reason: e.message }),
      ).finally(() => {
        pendingStepUp.current = null;
      });
      const ok = await pendingStepUp.current;
      if (!ok) throw e;
      return run();
    }
  }, []);

  const confirmIdentity = useCallback((): Promise<boolean> => {
    pendingStepUp.current ??= new Promise<boolean>((resolve) =>
      setStepUp({ resolve }),
    ).finally(() => {
      pendingStepUp.current = null;
    });
    return pendingStepUp.current;
  }, []);

  type Pending = {
    challenge: Extract<StepUpChallenge, { required: true }>;
    resolve: (proof: string) => void;
    reject: (e: unknown) => void;
  };
  const [action, setAction] = useState<Pending | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [actionPending, setActionPending] = useState(false);
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!action) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [action]);

  const proveAction = useCallback(
    async (draft: StepUpDraft): Promise<string | undefined> => {
      const challenge = await api.stepUp.challenge(draft);
      if (!challenge.required) return undefined;
      return new Promise<string>((resolve, reject) => {
        setActionError(null);
        setAction({ challenge, resolve, reject });
      });
    },
    [api],
  );
  routeProver.current = (need) => proveAction({ action: need.action, resource: need.resource, change: need.change });

  function closeAction(reason?: unknown) {
    if (action) {
      void api.stepUp
        .cancel(action.challenge.challenge_id)
        .catch(() => undefined); // best effort: it grants nothing either way
      action.reject(reason ?? new StepUpCancelled());
    }
    setAction(null);
    setActionError(null);
  }

  async function performAction() {
    if (!action) return;
    setActionPending(true);
    setActionError(null);
    try {
      const credential = await getPasskey(action.challenge.public_key);
      const proof = await api.stepUp.verify(
        action.challenge.challenge_id,
        credential,
      );
      action.resolve(proof.proof_id);
      setAction(null);
    } catch (e) {
      setActionError(
        e instanceof ApiError
          ? new Error(refusalText(e.code))
          : new Error(passkeyErrorMessage(e)),
      );
    } finally {
      setActionPending(false);
    }
  }

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
      setStepUpError(
        e instanceof ApiError ? e : new Error(passkeyErrorMessage(e)),
      );
    } finally {
      setStepUpPending(false);
    }
  }

  const value = useMemo<Runtime>(
    () => ({
      api,
      flow,
      clientId,
      session: sessionQuery.data,
      refreshSession,
      protect,
      confirmIdentity,
      proveAction,
      signOut,
    }),
    [
      api,
      flow,
      clientId,
      sessionQuery.data,
      refreshSession,
      protect,
      confirmIdentity,
      proveAction,
      signOut,
    ],
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
            <Button
              pending={stepUpPending}
              icon={<Fingerprint aria-hidden className="size-4" />}
              onPress={() => void performStepUp()}
            >
              Verify with passkey
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p>
            This action needs a fresh, high-assurance sign-in. Your entries on
            this page are kept.
          </p>
          {stepUpError ? (
            <ErrorState error={stepUpError} title="Verification failed" />
          ) : null}
        </div>
      </Dialog>
      <Dialog
        isOpen={action !== null}
        onOpenChange={(open) => {
          if (!open && !actionPending) closeAction();
        }}
        title={action ? TIER_COPY[action.challenge.tier].title : "Confirm"}
        size="md"
        footer={
          <>
            <Button
              variant="secondary"
              isDisabled={actionPending}
              onPress={() => closeAction()}
            >
              Cancel
            </Button>
            <Button
              pending={actionPending}
              isDisabled={
                action
                  ? secondsLeft(action.challenge.expires_at, now) === 0
                  : true
              }
              icon={<Fingerprint aria-hidden className="size-4" />}
              onPress={() => void performAction()}
            >
              Use my passkey
            </Button>
          </>
        }
      >
        {action ? (
          <div className="space-y-3 text-sm">
            <p className="text-ink-muted">
              {TIER_COPY[action.challenge.tier].body}
            </p>
            <div className="rounded-md border border-line p-3">
              <p className="flex items-center gap-2 font-medium">
                <ShieldCheck aria-hidden className="size-4" />
                What will happen
              </p>
              <p className="mt-1">{action.challenge.summary}</p>
            </div>
            <p role="timer" aria-live="off" className="text-xs text-ink-muted">
              Time left to confirm:{" "}
              {clock(secondsLeft(action.challenge.expires_at, now))}
            </p>
            <details className="text-xs text-ink-muted">
              <summary className="cursor-pointer">Why this is required</summary>
              <p className="mt-1">
                {whyRequired(action.challenge.tier, action.challenge.why)}
              </p>
            </details>
            <div role="status" aria-live="polite">
              {actionError ? (
                <ErrorState error={actionError} title="Confirmation failed" />
              ) : null}
            </div>
          </div>
        ) : null}
      </Dialog>
    </RuntimeContext.Provider>
  );
}
