"use client";

import { Banner, Button, ButtonLink, Card } from "@zoiko/ui";
import { Fingerprint } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { useRuntime } from "@/lib/runtime";
import {
  browserSupportsWebAuthn,
  completeAppSession,
  createPasskey,
  getPasskey,
  passkeyErrorMessage,
} from "@/lib/webauthn";

const noSubscribe = () => () => {};

/** Where to land after the first sign-in: a same-site path only (never `//host` or a URL); the
 * company details from registration (`carry`, already validated) ride along. */
function landing(next: string | undefined): string {
  return next && /^\/[A-Za-z0-9/_-]*$/.test(next) && !next.startsWith("//") ? next : "/account";
}

/** First passkey for an account, from a one-time enrollment ticket; then signs straight in. */
export function Enroll({
  ticket,
  workspace,
  selected,
  label,
  company,
  next,
  carry,
}: {
  ticket?: string;
  workspace?: string;
  selected?: boolean;
  label?: string;
  company?: string;
  next?: string;
  carry?: string;
}) {
  const { flow } = useRuntime();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [registered, setRegistered] = useState(false);
  const supported = useSyncExternalStore(noSubscribe, browserSupportsWebAuthn, () => true);

  // An invitee's browser has no company selected yet, and the page learns its company when the server renders it.
  // So choose the company the link names, then load the page again once (`selected`) so every request below
  // carries the right one. A company that cannot be found stops here with a clear message.
  const [selectFailed, setSelectFailed] = useState(false);
  useEffect(() => {
    if (!workspace || selected) return;
    void (async () => {
      try {
        const res = await fetch("/api/workspace", {
          method: "POST",
          headers: { "content-type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ workspace }),
        });
        if (!res.ok) throw new Error("not found");
        const url = new URL(window.location.href);
        url.searchParams.set("selected", "1");
        window.location.replace(url.toString());
      } catch {
        setSelectFailed(true);
      }
    })();
  }, [workspace, selected]);

  async function signIn() {
    const options = await flow.passkeyOptions();
    await flow.passkeyVerify(await getPasskey(options));
    await completeAppSession();
    window.location.assign(`${landing(next)}?welcome=1${carry ? `&${carry}` : ""}`);
  }

  async function enroll() {
    setPending(true);
    setFailure(null);
    try {
      if (!registered) {
        const options = await flow.registrationOptions({ enrollment_ticket: ticket ?? null });
        await flow.registrationVerify(await createPasskey(options), label ? `${label.slice(0, 50)}'s passkey` : undefined);
        setRegistered(true);
      }
      await signIn();
    } catch (e) {
      setFailure(passkeyErrorMessage(e));
      setPending(false);
    }
  }

  if (workspace && !selected) {
    return (
      <div className="space-y-3 text-center" role="status">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{selectFailed ? "Company not found" : "Preparing your invitation…"}</h1>
        <p className="text-sm text-ink-muted">
          {selectFailed ? "The company named in this link could not be found. Ask the person who invited you for a new link." : "Selecting your company."}
        </p>
      </div>
    );
  }

  if (!ticket) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Enrollment link needed</h1>
        <p className="text-sm text-ink-muted">Open the passkey setup link you were sent. Links work once and expire.</p>
        <ButtonLink href="/sign-in" variant="ghost" size="sm">
          Already have a passkey? Sign in
        </ButtonLink>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Set up your passkey</h1>
        {company ? <p className="mt-1 text-sm font-medium text-ink">{company}</p> : null}
        <p className="mt-1 text-sm text-ink-muted">Create a passkey on this device or a security key. You&apos;ll use it to sign in.</p>
      </div>

      {registered && !failure ? (
        <Banner tone="info" title="Passkey created">
          Confirm it once more to sign in.
        </Banner>
      ) : null}
      {failure ? (
        <Banner tone="danger" live="assertive" title={registered ? "Couldn't sign you in" : "Couldn't create your passkey"}>
          {failure}
        </Banner>
      ) : null}
      {!supported ? (
        <Banner tone="warn" title="This browser can't use passkeys">
          Use a current version of Chrome, Edge, Safari or Firefox.
        </Banner>
      ) : null}

      <Card>
        <Button
          size="lg"
          className="w-full"
          pending={pending}
          isDisabled={!supported}
          icon={<Fingerprint aria-hidden className="size-5" />}
          onPress={() => void enroll()}
        >
          {registered ? "Sign in with your new passkey" : "Create passkey"}
        </Button>
      </Card>
    </div>
  );
}
