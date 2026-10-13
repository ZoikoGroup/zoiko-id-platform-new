"use client";

import { Button, ZoikoMark } from "@zoiko/ui";

/** Unexpected render error. Shows only the digest for support, never a stack trace. */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="main" className="flex min-h-dvh flex-col items-center justify-center gap-4 px-4 text-center">
      <ZoikoMark />
      <h1 className="text-xl font-semibold text-ink">Something went wrong</h1>
      <p className="max-w-sm text-sm text-ink-muted">The page couldn&apos;t be displayed. Try again, or contact support with the reference below.</p>
      {error.digest ? <p className="font-mono text-xs text-ink-subtle">Reference: {error.digest}</p> : null}
      <Button onPress={reset}>Try again</Button>
    </main>
  );
}
