"use client";

import { Banner, Button, Card, TextField } from "@zoiko/ui";
import { Fingerprint } from "lucide-react";
import { useState, useSyncExternalStore } from "react";

import { useRuntime } from "@/lib/runtime";
import { browserSupportsWebAuthn, completeAppSession, getPasskey, passkeyErrorMessage } from "@/lib/webauthn";

const noSubscribe = () => () => {};

function safePath(v: string | undefined) {
  return v && v.startsWith("/") && !v.startsWith("//") && !v.includes("\\") ? v : "/requests";
}

/** Operators sign in with an email and password (when the cell has operator logins and ZID_OPERATOR_PASSWORD_SIGNIN=true) or a
 * workforce passkey: no federation shortcut, no recovery link on this origin (operator recovery is never self-service here). */
export function OperatorSignIn({ returnTo, reason, passwordSignIn }: { returnTo?: string; reason?: string; passwordSignIn?: boolean }) {
  const { flow } = useRuntime();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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

  async function signInWithPassword() {
    setPending(true);
    setFailure(null);
    try {
      await flow.operatorPasswordSignIn(email, password);
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
        <p className="mt-1 text-sm text-ink-muted">Operator accounts only. Customer accounts cannot sign in here.</p>
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
      {passwordSignIn ? (
        <Card>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void signInWithPassword();
            }}
          >
            <p className="text-sm text-ink-muted">Sign in with your operator email and password.</p>
            <TextField inputClassName="min-h-11" label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" isRequired />
            <TextField inputClassName="min-h-11" label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" isRequired />
            <Button size="lg" type="submit" className="w-full" pending={pending} isDisabled={!email || !password}>
              Sign in with email and password
            </Button>
          </form>
        </Card>
      ) : null}
    </div>
  );
}
