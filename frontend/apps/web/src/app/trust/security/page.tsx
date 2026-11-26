import type { Metadata } from "next";

import { ReportForm } from "@/features/trust-center/report-form";

export const metadata: Metadata = { title: "Report a vulnerability", description: "How to tell the security team about a weakness, safely." };

export default function Page() {
  return (
    <article className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-ink">Report a vulnerability</h1>
        <p className="text-ink-muted">If you found a weakness, we want to hear about it. Tell us what you found; we will give you a reference and a target for our first reply. We do not currently operate a paid reward program. Our machine-readable contact is at <a className="text-brand-700 underline" href="/.well-known/security.txt">/.well-known/security.txt</a>.</p>
      </div>
      <ReportForm />
    </article>
  );
}
