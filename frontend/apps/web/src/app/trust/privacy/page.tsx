import type { Metadata } from "next";

import type { PrivacyFeed } from "@/features/trust-center/model";
import { PrivacyView } from "@/features/trust-center/views";
import { trustFeed } from "@/server/trust";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Privacy and subprocessors", description: "Subprocessors, retention, biometrics and transfers for your market." };

export default async function Page({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  const raw = (await searchParams).market ?? "";
  const market = /^[A-Za-z]{2}$/.test(raw) ? raw.toUpperCase() : "";
  const feed = await trustFeed<PrivacyFeed>(`/public/trust/v1/privacy${market ? `?market=${market}` : ""}`);
  return (
    <article className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-ink">Privacy and subprocessors</h1>
        <p className="text-ink-muted">What we hold, who helps us process it, and where. Records are approved by Privacy and Vendor Management before they appear.</p>
      </div>
      <form method="get" className="flex items-end gap-2" aria-label="Choose a market">
        <div>
          <label htmlFor="market" className="block text-sm font-medium text-ink">Market (two-letter country code)</label>
          <input id="market" name="market" defaultValue={market} maxLength={2} pattern="[A-Za-z]{2}" className="mt-1 w-24 rounded-md border border-line bg-surface p-2 uppercase text-ink" />
        </div>
        <button type="submit" className="rounded-md border border-line px-3 py-2 text-ink">Show</button>
      </form>
      <PrivacyView feed={feed} />
    </article>
  );
}
