import type { Metadata } from "next";

import { CertificationsView } from "@/features/trust-center/views";
import type { CertFeed } from "@/features/trust-center/model";
import { trustFeed } from "@/server/trust";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Assessments", description: "What was assessed, by whom, for which entity, product and period. A report is never called a certification." };

export default async function Page() {
  const feed = await trustFeed<CertFeed>("/public/trust/v1/certifications");
  return (
    <article className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-ink">Assessments</h1>
        <p className="text-ink-muted">What was assessed, by whom, for which entity, product and period. A report is never called a certification.</p>
      </div>
      <CertificationsView feed={feed} />
    </article>
  );
}
