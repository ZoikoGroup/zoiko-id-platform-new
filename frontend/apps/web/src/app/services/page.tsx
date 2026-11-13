import type { Metadata } from "next";

import { DELIVERY_RULE, OFFERINGS_HERO, OFFERINGS_SUBHERO, OfferingsList, type PublicOffering } from "@/features/offerings/offerings-list";
import { server } from "@/server/bff";

export const metadata: Metadata = { title: "Services" };
export const dynamic = "force-dynamic";

async function loadOfferings(): Promise<PublicOffering[] | null> {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/offerings`, { cache: "no-store", headers: { accept: "application/json" } });
    return res.ok ? ((await res.json()) as { items: PublicOffering[] }).items : null;
  } catch {
    return null;
  }
}

export default async function Page() {
  const items = await loadOfferings();
  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h1 className="text-3xl font-bold tracking-tight text-ink">{OFFERINGS_HERO}</h1>
        <p className="max-w-2xl text-lg text-ink-muted">{OFFERINGS_SUBHERO}</p>
      </section>
      {items === null ? (
        <p role="status" className="rounded-control border border-border p-4 text-sm">
          The list of services is not available right now. Please try again later.
        </p>
      ) : (
        <OfferingsList items={items} />
      )}
      <p className="max-w-3xl text-xs text-ink-subtle">{DELIVERY_RULE}</p>
    </div>
  );
}
