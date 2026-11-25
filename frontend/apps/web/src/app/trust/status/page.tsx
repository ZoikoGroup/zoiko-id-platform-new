import type { Metadata } from "next";

import { StatusView } from "@/features/trust-center/views";
import type { StatusFeed } from "@/features/trust-center/model";
import { trustFeed } from "@/server/trust";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Service status", description: "Measured availability, with how it is counted. A service with no validated measurement says so." };

export default async function Page() {
  const feed = await trustFeed<StatusFeed>("/public/status/v1/availability");
  return (
    <article className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-ink">Service status</h1>
        <p className="text-ink-muted">Measured availability, with how it is counted. A service with no validated measurement says so.</p>
      </div>
      <StatusView feed={feed} />
    </article>
  );
}
