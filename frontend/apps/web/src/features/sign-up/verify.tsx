"use client";

import type { RegistrationConfirmed } from "@zoiko/api-client";
import { Banner, ButtonLink } from "@zoiko/ui";
import { useEffect, useRef, useState } from "react";

import { Enroll } from "@/features/enroll/enroll";

import { SignUpStatus } from "./status";
import { accountLabels, EXPIRED_MESSAGE } from "./logic";

type Phase =
  | { kind: "confirming" }
  | { kind: "enroll"; ticket: string; address: string }
  | { kind: "invalid" }
  | { kind: "idle" };

/** The emailed link: redeems its single-use token (the only thing the URL carries), then runs the passkey enrollment from the returned
 * ticket. The ticket is kept in this tab's session storage for the reload that lets the new account's workspace cookie take effect, and
 * is removed the moment it is read. Every way a link can fail shows the same message and never says whose account it was. */
export function Verify({ token, held }: { token: string | null; held: { ticket: string; address: string } | null }) {
  const [phase, setPhase] = useState<Phase>(
    token ? { kind: "confirming" } : held ? { kind: "enroll", ticket: held.ticket, address: held.address } : { kind: "idle" },
  );
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // React may run effects twice in development; the link must be redeemed once
    started.current = true;
    if (!token) return;
    void (async () => {
      try {
        const res = await fetch("/api/registration/confirm", {
          method: "POST",
          headers: { "content-type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ token }),
        });
        const body = (await res.json().catch(() => ({}))) as Partial<RegistrationConfirmed>;
        if (res.status !== 200 || !body.enrollment_ticket) {
          setPhase({ kind: "invalid" });
          return;
        }
        // the server holds the ticket in an HttpOnly cookie; reloading drops the link's token from the address bar and lets this
        // page render with the new account's workspace in place
        window.location.replace("/sign-up/verify"); // the link's token leaves the address bar; the new workspace cookie is now in place
      } catch {
        setPhase({ kind: "invalid" });
      }
    })();
  }, [token]);

  if (phase.kind === "confirming") {
    return (
      <div className="space-y-3 text-center" role="status">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Confirming your email…</h1>
        <p className="text-sm text-ink-muted">One moment.</p>
      </div>
    );
  }
  if (phase.kind === "invalid") {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">This link can&apos;t be used</h1>
        <Banner tone="warn" live="assertive" title={EXPIRED_MESSAGE} />
        <ButtonLink href="/sign-up" size="lg" className="w-full min-h-11">
          Request a new link
        </ButtonLink>
      </div>
    );
  }
  if (phase.kind === "enroll") {
    const labels = accountLabels({ account: "ACTIVE", identity: "NOT_VERIFIED" });
    return (
      <div className="space-y-5">
        <div className="space-y-2 text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Email confirmed</h1>
          {phase.address ? <p className="text-sm text-ink-muted">{phase.address}</p> : null}
          <ul className="flex flex-wrap justify-center gap-2 text-xs font-medium" aria-label="Your account">
            <li className="inline-flex min-h-7 items-center rounded-full border border-ok-fg/30 px-2.5 text-ok-fg">{labels.account}</li>
            <li className="inline-flex min-h-7 items-center rounded-full border border-border px-2.5 text-ink-muted">{labels.identity}</li>
          </ul>
        </div>
        <Enroll ticket={phase.ticket} next="/sign-up/status" />
        <p className="text-center text-xs text-ink-subtle">
          If a passkey isn&apos;t available on this device, a governed recovery path is used. We never accept email or SMS codes as a substitute.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Verify your email</h1>
        <p className="mt-1 text-sm text-ink-muted">Open the link we emailed you. Links work once and expire.</p>
      </div>
      <SignUpStatus />
    </div>
  );
}
