import type { Metadata } from "next";

import { SECTIONS } from "@/features/docs/verify-api-docs";
import { DocsView } from "@/features/docs/verify-api-docs-view";

export const metadata: Metadata = {
  title: "Verify API documentation",
  description: "Verify your own customers from your own server: quickstart, the sandbox, webhooks and errors.",
};

/** The Verify API documentation: public, so a developer can read it before they have an account. */
export default function Page() {
  return (
    <article className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-ink">Verify API</h1>
        <p className="text-ink-muted">Check your own customers from your own server. Start in the sandbox; nothing real is checked there.</p>
        <p className="text-sm">
          <a className="text-brand-600 underline" href="/docs/verify-api/openapi.json" download="zoiko-verify-api.openapi.json">
            Download the OpenAPI description
          </a>
        </p>
      </div>
      <DocsView sections={SECTIONS} />
    </article>
  );
}
