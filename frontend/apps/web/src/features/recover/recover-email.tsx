"use client";

import { Banner, ButtonLink } from "@zoiko/ui";
import { useEffect, useRef, useState } from "react";

import { Enroll } from "@/features/enroll/enroll";

type Phase = { kind: "confirming" } | { kind: "enroll"; ticket: string } | { kind: "invalid" } | { kind: "idle" };

const INVALID = "This link is no longer valid. Ask for a new one.";

/** The emailed recovery link: redeems its single-use token (the only thing the URL carries), then runs the passkey enrollment from the
 * returned ticket, which also signs the person in. The ticket stays in an HttpOnly cookie; the page reloads once so the account's workspace
 * takes effect and the token leaves the address bar. Every way the link can fail shows the same message. */
export function RecoverEmail({ token, ticket }: { token: string | null; ticket: string | null }) {
  const [phase, setPhase] = useState<Phase>(token ? { kind: "confirming" } : ticket ? { kind: "enroll", ticket } : { kind: "idle" });
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // React may run effects twice in development; the link must be redeemed once
    started.current = true;
    if (!token) return;
    void (async () => {
      try {
        const res = await fetch("/api/registration/recovery-confirm", {
          method: "POST",
          headers: { "content-type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ token }),
        });
        const body = (await res.json().catch(() => ({}))) as { enrollment_ticket?: string };
        if (res.status !== 200 || !body.enrollment_ticket) {
          setPhase({ kind: "invalid" });
          return;
        }
        window.location.replace("/recover/email");
      } catch {
        setPhase({ kind: "invalid" });
      }
    })();
  }, [token]);

  if (phase.kind === "confirming") {
    return (
      <div className="space-y-3 text-center" role="status">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Checking your link…</h1>
        <p className="text-sm text-ink-muted">One moment.</p>
      </div>
    );
  }
  if (phase.kind === "enroll") {
    return (
      <div className="space-y-5">
        <div className="space-y-2 text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Create a new passkey</h1>
          <p className="text-sm text-ink-muted">Your email is confirmed. Set up a passkey on this device and you&apos;ll be signed in.</p>
        </div>
        <Enroll ticket={phase.ticket} next="/overview" />
      </div>
    );
  }
  return (
    <div className="space-y-4 text-center">
      <h1 className="text-2xl font-semibold tracking-tight text-ink">{phase.kind === "invalid" ? "This link can't be used" : "Sign back in"}</h1>
      {phase.kind === "invalid" ? (
        <Banner tone="warn" live="assertive" title={INVALID} />
      ) : (
        <p className="text-sm text-ink-muted">Open the link we emailed you. Links work once and expire after 15 minutes.</p>
      )}
      <ButtonLink href="/recover" size="lg" className="w-full min-h-11">
        Email me a new link
      </ButtonLink>
    </div>
  );
}
