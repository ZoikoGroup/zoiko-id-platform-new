import type { Metadata } from "next";

import { AdvisoriesView } from "@/features/trust-center/views";
import type { AdvisoryFeed } from "@/features/trust-center/model";
import { trustFeed } from "@/server/trust";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Advisories", description: "Incident notices, with every version and every correction kept." };

export default async function Page() {
  const feed = await trustFeed<AdvisoryFeed>("/public/trust/v1/advisories");
  return (
    <article className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-ink">Advisories</h1>
        <p className="text-ink-muted">Incident notices, with every version and every correction kept.</p>
      </div>
      <AdvisoriesView feed={feed} />
    </article>
  );
}
