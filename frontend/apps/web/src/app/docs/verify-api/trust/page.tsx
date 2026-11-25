import type { Metadata } from "next";

import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Verify API trust statements",
  description: "What Zoiko iD says publicly about the Verify API, each with what backs it, and what it does not say.",
};

type Claim = { id: string; statement: string; basis: string; evidence: string };
type NotClaimed = { claim: string; reason: string };
type Feed = { as_of: string; claims: Claim[]; not_claimed: NotClaimed[]; rule: string };

async function load(): Promise<Feed | null> {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/verify-api/claims`, { cache: "no-store", headers: { accept: "application/json" } });
    if (!res.ok) return null;
    return (await res.json()) as Feed;
  } catch {
    return null;
  }
}

/** Public and read-only: every sentence comes from the claims feed, which is built from records. Nothing here is written by hand. */
export default async function Page() {
  const feed = await load();
  return (
    <article className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-ink">Verify API trust statements</h1>
        <p className="text-ink-muted">Each statement below is built from a record the platform holds today. What we do not say is listed too, with the reason.</p>
      </div>
      {feed === null ? (
        <p role="status" className="text-ink-muted">The statements could not be loaded right now. Try again shortly.</p>
      ) : (
        <>
          <p className="text-sm text-ink-muted">As of {feed.as_of}. {feed.rule}</p>
          <section aria-labelledby="may" className="space-y-3">
            <h2 id="may" className="text-xl font-semibold text-ink">What may be said</h2>
            <ul className="space-y-3">
              {feed.claims.map((c) => (
                <li key={c.id} className="rounded-lg border border-line p-4">
                  <p className="text-ink">{c.statement}</p>
                  <p className="mt-1 text-sm text-ink-muted">Basis: {c.basis}. Evidence: {c.evidence}</p>
                </li>
              ))}
            </ul>
          </section>
          <section aria-labelledby="maynot" className="space-y-3">
            <h2 id="maynot" className="text-xl font-semibold text-ink">What is not said</h2>
            <ul className="space-y-3">
              {feed.not_claimed.map((c) => (
                <li key={c.claim} className="rounded-lg border border-line p-4">
                  <p className="text-ink">{c.claim}</p>
                  <p className="mt-1 text-sm text-ink-muted">{c.reason}</p>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </article>
  );
}
