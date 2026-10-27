"use client";

import { Banner, Button, Card } from "@zoiko/ui";
import { Fingerprint } from "lucide-react";
import { useState, useSyncExternalStore } from "react";

import { useRuntime } from "@/lib/runtime";
import { browserSupportsWebAuthn, completeAppSession, createPasskey, getPasskey, passkeyErrorMessage } from "@/lib/webauthn";

const noSubscribe = () => () => {};

/** First passkey for an operator, from the single-use enrollment link the bootstrap prints. It runs on the console's own
 * origin with the console's own OAuth client (`zoiko-operator`): a ticket belongs to the operator tenant, so the
 * customer app (another client, another tenant) could never redeem it. After registering, the operator is signed in. */
export function OperatorEnroll({ ticket }: { ticket?: string }) {
  const { flow } = useRuntime();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [registered, setRegistered] = useState(false);
  const supported = useSyncExternalStore(noSubscribe, browserSupportsWebAuthn, () => true);

  async function enroll() {
    setPending(true);
    setFailure(null);
    try {
      if (!registered) {
        const options = await flow.registrationOptions({ enrollment_ticket: ticket ?? null });
        await flow.registrationVerify(await createPasskey(options), "Operator passkey");
        setRegistered(true);
      }
      await flow.passkeyVerify(await getPasskey(await flow.passkeyOptions()));
      await completeAppSession();
      window.location.assign("/requests");
    } catch (e) {
      setFailure(passkeyErrorMessage(e));
      setPending(false);
    }
  }

  if (!ticket) {
    return (
      <div className="space-y-3 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Enrollment link needed</h1>
        <p className="text-sm text-ink-muted">Open the single-use link your administrator generated for you. Customer accounts cannot enroll here.</p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Set up your operator passkey</h1>
        <p className="mt-1 text-sm text-ink-muted">Create a passkey on this managed device or a security key. You will use it to sign in and to confirm sensitive actions.</p>
      </div>
      {failure ? (
        <Banner tone="danger" live="assertive" title="Couldn't create your passkey">
          {failure}
        </Banner>
      ) : null}
      {!supported ? <Banner tone="warn" title="This browser can't use passkeys" /> : null}
      <Card>
        <Button size="lg" className="w-full" pending={pending} isDisabled={!supported} icon={<Fingerprint aria-hidden className="size-5" />} onPress={() => void enroll()}>
          Create passkey
        </Button>
      </Card>
    </div>
  );
}
