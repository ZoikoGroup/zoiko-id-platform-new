"use client";

import { Banner, Button, Card } from "@zoiko/ui";
import { Fingerprint } from "lucide-react";
import { useState, useSyncExternalStore } from "react";

import { useRuntime } from "@/lib/runtime";
import { browserSupportsWebAuthn, completeAppSession, getPasskey, passkeyErrorMessage } from "@/lib/webauthn";

const noSubscribe = () => () => {};

function safePath(v: string | undefined) {
  return v && v.startsWith("/") && !v.startsWith("//") && !v.includes("\\") ? v : "/requests";
}

/** Operators sign in with their workforce passkey only: no federation shortcut, no recovery
 * link on this origin (operator recovery is a workforce IdP process, never self-service here). */
export function OperatorSignIn({ returnTo, reason }: { returnTo?: string; reason?: string }) {
  const { flow } = useRuntime();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const supported = useSyncExternalStore(noSubscribe, browserSupportsWebAuthn, () => true);
  const target = safePath(returnTo);

  async function signIn() {
    setPending(true);
    setFailure(null);
    try {
      const options = await flow.passkeyOptions();
      await flow.passkeyVerify(await getPasskey(options));
      await completeAppSession();
      window.location.assign(target);
    } catch (e) {
      setFailure(passkeyErrorMessage(e));
      setPending(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Operator sign-in</h1>
        <p className="mt-1 text-sm text-ink-muted">Your workforce passkey on a managed device. Customer accounts cannot sign in here.</p>
      </div>
      {reason === "expired" && !failure ? <Banner tone="info" title="Your operator session ended">Sign in again to continue.</Banner> : null}
      {failure ? (
        <Banner tone="danger" live="assertive" title="Couldn't sign you in">
          {failure}
        </Banner>
      ) : null}
      {!supported ? <Banner tone="warn" title="This browser can't use passkeys" /> : null}
      <Card>
        <Button size="lg" className="w-full" pending={pending} isDisabled={!supported} icon={<Fingerprint aria-hidden className="size-5" />} onPress={() => void signIn()}>
          Sign in with a passkey
        </Button>
      </Card>
    </div>
  );
}
