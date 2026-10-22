"use client";

import { Banner, Button, ButtonLink, Card } from "@zoiko/ui";
import { Fingerprint } from "lucide-react";
import { useState, useSyncExternalStore } from "react";

import { useRuntime } from "@/lib/runtime";
import {
  browserSupportsWebAuthn,
  completeAppSession,
  createPasskey,
  getPasskey,
  passkeyErrorMessage,
} from "@/lib/webauthn";

const noSubscribe = () => () => {};

/** First passkey for an account, from a one-time enrollment ticket; then signs straight in. */
export function Enroll({ ticket, label, company }: { ticket?: string; label?: string; company?: string }) {
  const { flow } = useRuntime();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [registered, setRegistered] = useState(false);
  const supported = useSyncExternalStore(noSubscribe, browserSupportsWebAuthn, () => true);

  async function signIn() {
    const options = await flow.passkeyOptions();
    await flow.passkeyVerify(await getPasskey(options));
    await completeAppSession();
    window.location.assign("/account?welcome=1");
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
