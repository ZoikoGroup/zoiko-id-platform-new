"use client";

import { Banner, Button, Card, TextField } from "@zoiko/ui";
import { Mail } from "lucide-react";
import { useState, type FormEvent } from "react";

/** "Forgot how to sign in?": the address the account was made with gets a single-use link to create a new passkey. The answer is the same
 * for every address, so this never says whether an account exists. */
export function EmailRecovery() {
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setFailure(null);
    try {
      const res = await fetch("/api/registration/recovery", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email: email.trim() }),
      });
      if (res.status === 429) setFailure("Too many tries. Wait a few minutes and try again.");
      else if (res.ok) setSent(true);
      else setFailure("We couldn't send that. Check the address and try again.");
    } catch {
      setFailure("The service is temporarily unavailable. Try again in a moment.");
    } finally {
      setPending(false);
    }
  }

  if (sent) {
    return (
      <Banner tone="info" title="Check your inbox">
        If that address belongs to an account, we&apos;ve sent a link to set up a new passkey. It works once and expires in 15 minutes.
      </Banner>
    );
  }
  return (
    <Card>
      <form onSubmit={(e) => void submit(e)} className="space-y-4">
        <p className="text-sm text-ink-muted">Forgot how to sign in? We&apos;ll email the address your account was made with a link to set up a new passkey.</p>
        {failure ? <Banner tone="danger" live="assertive" title={failure} /> : null}
        <TextField label="Email address" type="email" value={email} onChange={setEmail} isRequired autoComplete="email" inputClassName="min-h-11" />
        <Button type="submit" size="lg" className="w-full" pending={pending} isDisabled={!email.includes("@")} icon={<Mail aria-hidden className="size-5" />}>
          Email me a sign-in link
        </Button>
      </form>
    </Card>
  );
}
