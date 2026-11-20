"use client";

import { Banner, Button } from "@zoiko/ui";

/** If this screen fails to draw, say what failed instead of the browser's generic "This page couldn't load". */
export default function CountriesError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="space-y-3 p-4">
      <Banner tone="danger" title="Country features could not be drawn">
        {error.message || "Unknown error"}
        {error.digest ? ` (reference ${error.digest})` : ""}
      </Banner>
      <pre className="max-h-64 overflow-auto rounded bg-sunken p-3 text-xs text-ink-muted">{(error.stack ?? "").slice(0, 1500)}</pre>
      <Button variant="secondary" onPress={reset}>
        Try again
      </Button>
    </div>
  );
}
