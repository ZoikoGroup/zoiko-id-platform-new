import type { Metadata } from "next";

import { CLAIM_LABEL, checkedOn, outcome, parseLink, type PublicView } from "@/features/verify/view";
import { server } from "@/server/bff";

export const metadata: Metadata = { title: "Check a credential", robots: { index: false, follow: false } };
export const dynamic = "force-dynamic";

/** A person with no account opens the link a holder shared. The cell answers only under a grant that is still in force, and with
 * one answer for every failure, so this page does the same: a bad, expired or withdrawn link all read the same. */
async function load(ref: string, grant: string): Promise<PublicView | null> {
  const { env } = server();
  try {
    const url = `${env.cellApiUrl.replace(/\/$/, "")}/credentials/assertions/${ref}/verify?grant=${encodeURIComponent(grant)}`;
    const res = await fetch(url, { cache: "no-store", headers: { accept: "application/json" } });
    if (!res.ok) return null;
    const body = (await res.json()) as { data?: PublicView } & PublicView;
    return body.data ?? body;
  } catch {
    return null;
  }
}

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ ref: string }>;
  searchParams: Promise<{ grant?: string }>;
}) {
  const link = parseLink(decodeURIComponent((await params).ref), (await searchParams).grant);
  const view = link ? await load(link.ref, link.grant) : null;

  if (!view) {
    return (
      <section className="space-y-3">
        <h1 className="text-2xl font-bold tracking-tight text-ink">This link cannot be used</h1>
        <p role="status" className="text-ink-muted">
          The link is not valid, has expired, or the person who shared it has withdrawn it. Ask them for a new one.
        </p>
      </section>
    );
  }

  const o = outcome(view);
  const date = checkedOn(view.source_checked_at);
  return (
    <section aria-labelledby="result-h" className="space-y-5">
      <h1 id="result-h" className="text-2xl font-bold tracking-tight text-ink">
        {CLAIM_LABEL[view.claim_type ?? ""] ?? "A credential"}
      </h1>
      <div role="status" data-tone={o.tone} className="rounded-control border border-border-strong p-4">
        <p className="text-lg font-semibold text-ink">{o.headline}</p>
        <p className="mt-1 text-sm text-ink-muted">{o.detail}</p>
      </div>
      <dl className="space-y-2 text-sm">
        {date ? (
          <div className="flex gap-2">
            <dt className="font-medium">Checked on</dt>
            <dd>{date}</dd>
          </div>
        ) : null}
        {view.credential_status ? (
          <div className="flex gap-2">
            <dt className="font-medium">Status at that time</dt>
            <dd>{view.credential_status.toLowerCase()}</dd>
          </div>
        ) : null}
      </dl>
      <p className="text-xs text-ink-muted">
        This page shows only what the holder chose to share. It never shows a name, a date of birth or a document, and it is not a
        certification or an endorsement.
      </p>
    </section>
  );
}
