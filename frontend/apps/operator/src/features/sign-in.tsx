"use client";

import { Banner, Button, Card, TextField } from "@zoiko/ui";
import { useState } from "react";

import { useRuntime } from "@/lib/runtime";
import { completeAppSession, passkeyErrorMessage } from "@/lib/webauthn";

/** The company sign-in address shown to a customer who opened this page by mistake. Only a plain http(s) address is ever used; anything else, or nothing, shows no link. */
export function companySignInHref(raw: string | undefined): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch {
    return null;
  }
}

function safePath(v: string | undefined) {
  return v && v.startsWith("/") && !v.startsWith("//") && !v.includes("\\") ? v : "/";
}

/** Operators sign in with an email and password only (the cell must have operator logins configured): no passkey on this page,
 * no federation shortcut, no recovery link on this origin (operator recovery is never self-service here). */
export function OperatorSignIn({ returnTo, reason, companyUrl }: { returnTo?: string; reason?: string; companyUrl?: string }) {
  const { flow } = useRuntime();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const target = safePath(returnTo);
  const companyHref = companySignInHref(companyUrl);

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
      {companyHref ? (
        <p className="text-center text-sm text-ink-muted">
          Have a company account instead?{" "}
          <a className="font-medium text-brand-700 underline" href={companyHref}>
            Sign in on the company site
          </a>
          .
        </p>
      ) : null}
    </div>
  );
}
