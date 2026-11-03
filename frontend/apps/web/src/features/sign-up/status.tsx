"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import type { RegistrationStatus } from "@zoiko/api-client";
import { Banner, Button, ButtonLink, Card } from "@zoiko/ui";
import { CheckCircle2, UserCheck } from "lucide-react";

import { accountLabels, GENERIC_MESSAGE, serverMessage, stepView } from "./logic";

class NoRequest extends Error {}

async function fetchStatus(): Promise<RegistrationStatus> {
  const res = await fetch("/api/registration/status", { cache: "no-store", credentials: "same-origin" });
  const body = (await res.json().catch(() => ({}))) as RegistrationStatus & { error?: { code?: string } };
  if (res.status === 404) throw new NoRequest();
  if (!res.ok) throw new Error(serverMessage(body.error?.code));
  return body;
}

/** Where a sign-up request stands and what to do next, from the server's own answer. The two facts the document keeps apart are always
 * shown separately: the account is active, the identity is not verified. */
export function SignUpStatus() {
  const status = useQuery({
    queryKey: ["registration", "status"],
    queryFn: fetchStatus,
    refetchInterval: (q) => (q.state.data?.next_step === "VERIFY_EMAIL" ? 8000 : false),
    retry: false,
  });
  const resend = useMutation({
    mutationFn: async () => {
      await fetch("/api/registration/resend", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({}), // the request is this browser's own: the server reads it from its HttpOnly cookie
      });
    },
  });

  if (status.isError && status.error instanceof NoRequest) {
    return (
      <Banner tone="info" title="Open the link in your email">
        We sent a link to the address you gave. If you haven&apos;t started yet, <a className="font-medium text-brand-700 hover:underline" href="/sign-up">create your Zoiko iD</a>.
      </Banner>
    );
  }
  if (status.isError) {
    return (
      <Banner tone="warn" live="polite" title="We couldn't check your request">
        {status.error instanceof Error ? status.error.message : "Try again in a moment."} <a className="font-medium text-brand-700 hover:underline" href="/sign-up">Start again</a>
      </Banner>
    );
  }
  if (!status.data) {
    return (
      <p role="status" className="text-sm text-ink-muted">
        Checking…
      </p>
    );
  }
  const view = stepView(status.data);
  const labels = accountLabels(status.data);
  return (
    <div className="space-y-4" data-testid="signup-status">
      <ul className="flex flex-wrap gap-2 text-xs font-medium" aria-label="Your account">
        <li className={`inline-flex min-h-7 items-center gap-1 rounded-full border px-2.5 ${status.data.account === "ACTIVE" ? "border-ok-fg/30 text-ok-fg" : "border-border text-ink-muted"}`}>
          <UserCheck aria-hidden className="size-3.5" /> {labels.account}
        </li>
        <li className="inline-flex min-h-7 items-center gap-1 rounded-full border border-border px-2.5 text-ink-muted">{labels.identity}</li>
      </ul>
      <Banner tone={view.tone === "ok" ? "ok" : view.tone === "danger" ? "danger" : view.tone === "warn" ? "warn" : "info"} live="polite" title={view.title}>
        {view.body}
      </Banner>
      {view.resend ? (
        <Card>
          <div className="space-y-2">
            <Button variant="secondary" size="lg" className="w-full min-h-11" pending={resend.isPending} onPress={() => resend.mutate()}>
              Send a new link
            </Button>
            {resend.isSuccess ? (
              <p role="status" className="flex items-center gap-1 text-sm text-ink-muted">
                <CheckCircle2 aria-hidden className="size-4" /> {GENERIC_MESSAGE}
              </p>
            ) : null}
          </div>
        </Card>
      ) : null}
      {view.action ? (
        <ButtonLink href={view.action.href} size="lg" className="w-full min-h-11">
          {view.action.label}
        </ButtonLink>
      ) : null}
    </div>
  );
}
