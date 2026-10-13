"use client";

import { Banner, Button, ButtonLink, Card } from "@zoiko/ui";
import { Building2, Fingerprint, LifeBuoy } from "lucide-react";
import { useEffect, useState } from "react";

import { useRuntime } from "@/lib/runtime";
import { browserSupportsWebAuthn, completeAppSession, getPasskey, passkeyErrorMessage } from "@/lib/webauthn";

const ERRORS: Record<string, string> = {
  login_required: "Sign in to continue.",
  login_expired: "The sign-in took too long. Please try again.",
  access_denied: "Sign-in was cancelled or denied.",
  unknown_connection: "That sign-in option is not available.",
  token_exchange_failed: "Sign-in could not be completed. Please try again.",
  invalid_id_token: "Sign-in could not be verified. Please try again.",
  temporarily_unavailable: "Sign-in is temporarily unavailable.",
};

function safePath(v: string | undefined) {
  return v && v.startsWith("/") && !v.startsWith("//") && !v.includes("\\") ? v : "/account";
}

export function SignIn({
  returnTo,
  error,
  reason,
  connections,
}: {
  returnTo?: string;
  error?: string;
  reason?: string;
  connections: { id: string; label: string }[];
}) {
  const { flow } = useRuntime();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(error ? (ERRORS[error] ?? "Sign-in failed. Please try again.") : null);
  const [supported, setSupported] = useState(true);
  const target = safePath(returnTo);

  useEffect(() => setSupported(browserSupportsWebAuthn()), []);

  async function signInWithPasskey() {
    setPending(true);
    setFailure(null);
    try {
      const options = await flow.passkeyOptions();
      const credential = await getPasskey(options);
      await flow.passkeyVerify(credential);
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
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Sign in</h1>
        <p className="mt-1 text-sm text-ink-muted">Use the passkey on this device or your security key.</p>
      </div>

      {reason === "expired" && !failure ? (
        <Banner tone="info" title="Your session ended">
          Sign in again to pick up where you left off.
        </Banner>
      ) : null}
      {failure ? (
        <Banner tone="danger" live="assertive" title="Couldn't sign you in">
          {failure}
        </Banner>
      ) : null}
      {!supported ? (
        <Banner tone="warn" title="This browser can't use passkeys">
          Use a current version of Chrome, Edge, Safari or Firefox, or sign in with your organization.
        </Banner>
      ) : null}

      <Card>
        <div className="space-y-3">
          <Button
            size="lg"
            className="w-full"
            pending={pending}
            isDisabled={!supported}
            icon={<Fingerprint aria-hidden className="size-5" />}
            onPress={() => void signInWithPasskey()}
          >
            Sign in with a passkey
          </Button>
          {connections.length ? (
            <>
              <div className="flex items-center gap-3 py-1 text-xs text-ink-subtle">
                <span className="h-px flex-1 bg-border" />
                or
                <span className="h-px flex-1 bg-border" />
              </div>
              {connections.map((c) => (
                <a
                  key={c.id}
                  href={`/auth/federation/${encodeURIComponent(c.id)}?returnTo=${encodeURIComponent(target)}`}
                  className="flex h-11 w-full items-center justify-center gap-2 rounded-control border border-border-strong bg-surface text-sm font-medium text-ink shadow-sm hover:bg-hover focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:outline-none"
                >
                  <Building2 aria-hidden className="size-4" />
                  Continue with {c.label}
                </a>
              ))}
            </>
          ) : null}
        </div>
      </Card>

      <div className="flex justify-center">
        <ButtonLink href="/recover" variant="ghost" size="sm" icon={<LifeBuoy aria-hidden className="size-4" />}>
          Lost your passkey? Recover your account
        </ButtonLink>
      </div>
    </div>
  );
}
