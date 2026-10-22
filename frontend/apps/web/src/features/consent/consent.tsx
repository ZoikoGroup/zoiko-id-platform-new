"use client";

import { ApiError, type ConsentRequest } from "@zoiko/api-client";
import { useQuery } from "@tanstack/react-query";
import { Banner, Button, ButtonLink, Card, Code, LoadingRows, RelativeTime } from "@zoiko/ui";
import { CheckCircle2, ShieldQuestion } from "lucide-react";
import { useState } from "react";

import { useRuntime } from "@/lib/runtime";

import { isKnownScope, scopeDescription } from "./scopes";

type Loaded = { ok: true; request: ConsentRequest } | { ok: false; error: unknown };

/** Why a consent request can't be shown. The cell answers 404 for unknown, expired, used, or
 * another session's requests alike, so the copy stays generic. */
function loadFailure(e: unknown): { title: string; body: string } {
  if (e instanceof ApiError && (e.code === "login_required" || e.status === 401)) {
    return { title: "Your sign-in ended", body: "Go back to the app and start again. You'll be asked to sign in." };
  }
  if (e instanceof ApiError && e.code === "access_denied") {
    return {
      title: "This request belongs to another sign-in",
      body: "Finish it in the browser where you started, or go back to the app and start again.",
    };
  }
  if (e instanceof ApiError && e.status === 404) {
    return {
      title: "This request has expired",
      body: "Permission requests are valid for 10 minutes and can be answered once. Go back to the app and start again.",
    };
  }
  return { title: "Couldn't load this request", body: e instanceof Error ? e.message : "Please go back to the app and try again." };
}

/** Third-party OAuth consent. The cell redirects here with `?consent_request=<ref>`; the decision is
 * made by the same cell session that started the authorization and returns the client redirect. */
export function Consent({ consentRef }: { consentRef?: string }) {
  const { flow } = useRuntime();
  // Errors are returned as data so the app-wide "sign in again" redirect never fires here:
  // signing in again would start a different session, which can't answer this request.
  const query = useQuery({
    queryKey: ["consent-request", consentRef],
    queryFn: async (): Promise<Loaded> => {
      try {
        return { ok: true, request: await flow.consentRequest(consentRef!) };
      } catch (error) {
        return { ok: false, error };
      }
    },
    enabled: Boolean(consentRef),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const [deciding, setDeciding] = useState<null | "approve" | "deny">(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function decide(decision: "approve" | "deny") {
    if (!consentRef) return;
    setDeciding(decision);
    setFailure(null);
    try {
      const { redirect_to } = await flow.decideConsent(consentRef, decision);
      window.location.assign(redirect_to);
    } catch (e) {
      setFailure(e instanceof ApiError && e.status === 404 ? loadFailure(e).body : e instanceof Error ? e.message : "Please try again.");
      setDeciding(null);
    }
  }

  if (!consentRef) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Nothing to approve</h1>
        <p className="text-sm text-ink-muted">This page opens when an app asks to use your Zoiko iD. Go back to the app and try again.</p>
        <ButtonLink href="/account" variant="ghost" size="sm">
          Go to my account
        </ButtonLink>
      </div>
    );
  }

  if (query.isPending) return <LoadingRows rows={4} label="Loading request" />;

  const data = query.data;
  if (!data || !data.ok) {
    const f = loadFailure(data?.error ?? query.error);
    return (
      <div className="space-y-4">
        <Banner tone="warn" live="polite" title={f.title}>
          {f.body}
        </Banner>
      </div>
    );
  }

  const { request } = data;
  const busy = deciding !== null;

  return (
    <div className="space-y-5">
      <div className="text-center">
        <span className="mx-auto flex size-11 items-center justify-center rounded-full bg-sunken">
          <ShieldQuestion aria-hidden className="size-5 text-ink-muted" />
        </span>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-ink">Allow access?</h1>
        <p className="mt-1 text-sm text-ink-muted">
          <span className="break-all font-mono font-medium text-ink">{request.client_id}</span> wants to use your Zoiko iD.
        </p>
      </div>

      {failure ? (
        <Banner tone="danger" live="assertive" title="Couldn't record your answer">
          {failure}
        </Banner>
      ) : null}

      <Card title="This app will be able to">
        <ul className="space-y-2.5" aria-label="Requested permissions">
          {request.scopes.map((s) => (
            <li key={s} className="flex items-start gap-2.5 text-sm">
              <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-brand-600" />
              <span className="min-w-0">
                <span className="block text-ink">{scopeDescription(s)}</span>
                {isKnownScope(s) ? (
                  <span className="text-xs text-ink-subtle">
                    <Code>{s}</Code>
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-4 border-t border-border pt-3 text-xs text-ink-subtle">
          Only allow apps you trust. You can remove this app&apos;s access at any time under <strong>My account → Connected apps</strong>.
          This request expires <RelativeTime value={request.expires_at} />.
        </p>
      </Card>

      <div className="grid grid-cols-2 gap-3">
        <Button variant="secondary" size="lg" pending={deciding === "deny"} isDisabled={busy} onPress={() => void decide("deny")}>
          Deny
        </Button>
        <Button size="lg" pending={deciding === "approve"} isDisabled={busy} onPress={() => void decide("approve")}>
          Allow
        </Button>
      </div>
    </div>
  );
}
