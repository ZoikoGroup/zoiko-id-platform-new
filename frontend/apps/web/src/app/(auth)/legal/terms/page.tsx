import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { TERMS } from "@/lib/legal";

export const metadata: Metadata = { title: "Terms of Service" };

/** The Terms of Service. Legal has not supplied them yet, so this page does not exist until `TERMS.published` is set (see `lib/legal.ts`):
 * nobody is shown, or asked to accept, text that was never approved. When Legal publishes, replace the body below with their text. */
export default function TermsPage() {
  if (!TERMS.published) notFound();
  return (
    <article className="space-y-5 text-sm leading-6 text-ink">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Terms of Service</h1>
        <p className="text-ink-muted">Version {TERMS.version}</p>
      </header>
    </article>
  );
}
