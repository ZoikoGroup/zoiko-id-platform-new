"use client";

import { Banner, Button, Card, TextField } from "@zoiko/ui";
import { useState } from "react";

import { useRuntime } from "@/lib/runtime";
import { completeAppSession, passkeyErrorMessage } from "@/lib/webauthn";

function safePath(v: string | undefined) {
  return v && v.startsWith("/") && !v.startsWith("//") && !v.includes("\\") ? v : "/";
}

/** Operators sign in with an email and password only (the cell must have operator logins configured): no passkey on this page,
 * no federation shortcut, no recovery link on this origin (operator recovery is never self-service here). */
export function OperatorSignIn({ returnTo, reason }: { returnTo?: string; reason?: string }) {
  const { flow } = useRuntime();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const target = safePath(returnTo);

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
      <Card>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void signInWithPassword();
          }}
        >
          <TextField inputClassName="min-h-11" label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" isRequired />
          <TextField inputClassName="min-h-11" label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" isRequired />
          <Button size="lg" type="submit" className="w-full" pending={pending} isDisabled={!email || !password}>
            Sign in
          </Button>
        </form>
      </Card>
    </div>
  );
}
