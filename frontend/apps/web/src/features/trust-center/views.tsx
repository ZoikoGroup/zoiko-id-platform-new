import type { ReactNode } from "react";

import { availabilityText, fieldLabel, KIND_LABEL, valueText, type AdvisoryFeed, type CertFeed, type CertRow, type ClaimsFeed, type PrivacyFeed, type StatusFeed } from "./model";

function Unavailable({ what }: { what: string }) {
  return <p role="status" className="text-ink-muted">{what} could not be loaded right now. Nothing is shown rather than something unverified. Try again shortly.</p>;
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h2 id={id} className="text-xl font-semibold text-ink">{title}</h2>
      {children}
    </section>
  );
}

export function ClaimsView({ feed }: { feed: ClaimsFeed | null }) {
  if (feed === null) return <Unavailable what="The statements" />;
  return (
    <Section id="statements" title="What we say, and what backs it">
      <p className="text-sm text-ink-muted">As of <time dateTime={feed.as_of}>{new Date(feed.as_of).toUTCString()}</time>. {feed.note}</p>
      {feed.items.length === 0 ? (
        <p className="rounded-lg border border-line p-4 text-ink">No statement is currently approved for publication. We publish nothing we cannot back with current evidence.</p>
      ) : (
        <ul className="space-y-3">
          {feed.items.map((c) => (
            <li key={c.claim_id} className="rounded-lg border border-line p-4">
              <p className="text-ink">{c.text}</p>
              <p className="mt-1 text-sm text-ink-muted">
                Applies to {c.scope.service} in {c.scope.markets.join(", ")}. In effect since <time dateTime={c.effective_as_of}>{c.effective_as_of.slice(0, 10)}</time>
                {c.valid_until ? `, reviewed until ${c.valid_until}` : ""}.
              </p>
              {c.evidence.length ? (
                <ul className="mt-1 list-disc pl-5 text-sm text-ink-muted">
                  {c.evidence.map((e, i) => (
                    <li key={i}>{e.scheme}, {e.type.replaceAll("_", " ")} by {e.issuer}, period ends {e.period_end}{e.held_by !== "zoiko" ? ` (held by ${e.held_by}, not Zoiko iD)` : ""}{e.restricted ? ". Full document on request under NDA." : "."}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function CertList({ rows, label }: { rows: CertRow[]; label: string }) {
  if (rows.length === 0) return <p className="text-ink-muted">None.</p>;
  return (
    <ul className="space-y-3" aria-label={label}>
      {rows.map((r, i) => (
        <li key={i} className="rounded-lg border border-line p-4">
          <p className="font-medium text-ink">{r.scheme}: {r.kind}</p>
          <p className="text-sm text-ink-muted">
            {r.is_zoiko ? "Issued to Zoiko iD." : `Issued to ${r.issued_to}, a supplier. This is the supplier's, not Zoiko iD's own.`} Covers {r.legal_entity}, {r.products.join(", ")}
            {r.regions.length ? ` in ${r.regions.join(", ")}` : ""}, from {r.period_start} to {r.period_end}. Assessed by {r.assessor ?? r.issuer}.
            {r.exclusions.length ? ` Excludes ${r.exclusions.join(", ")}.` : ""} {r.full_report ?? ""}
          </p>
        </li>
      ))}
    </ul>
  );
}

export function CertificationsView({ feed }: { feed: CertFeed | null }) {
  if (feed === null) return <Unavailable what="The assessments" />;
  return (
    <div className="space-y-8">
      <p className="text-sm text-ink-muted">{feed.note}</p>
      <Section id="current" title="Current"><CertList rows={feed.current} label="Current assessments" /></Section>
      <Section id="history" title="Earlier or no longer in effect"><CertList rows={feed.history} label="Earlier assessments" /></Section>
    </div>
  );
}

export function PrivacyView({ feed }: { feed: PrivacyFeed | null }) {
  if (feed === null) return <Unavailable what="The privacy disclosures" />;
  const kinds = ["subprocessor", "retention", "biometric", "transfer"] as const;
  return (
    <div className="space-y-8">
      <p className="text-sm text-ink-muted">{feed.market ? `Showing ${feed.market} and global records.` : "Showing every market."} Notice periods and retention come from the contract and the law that apply to you, never from this page alone.</p>
      {kinds.map((k) => (
        <Section key={k} id={`k-${k}`} title={KIND_LABEL[k] ?? k}>
          {feed[k].length === 0 ? <p className="text-ink-muted">{feed.empty[k] ?? "Not yet published for this market."}</p> : (
            <ul className="space-y-3">
              {feed[k].map((r, i) => (
                <li key={i} className="rounded-lg border border-line p-4">
                  <p className="font-medium text-ink">{String(r.subject)} <span className="text-ink-muted">({String(r.market)})</span></p>
                  <dl className="mt-1 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
                    {Object.entries(r).filter(([key]) => !["subject", "market", "revision"].includes(key)).map(([key, v]) => (
                      <div key={key} className="contents"><dt className="text-ink-muted">{fieldLabel(key)}</dt><dd className="text-ink">{valueText(v)}</dd></div>
                    ))}
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </Section>
      ))}
    </div>
  );
}

export function StatusView({ feed }: { feed: StatusFeed | null }) {
  if (feed === null) return <Unavailable what="The service status" />;
  return (
    <div className="space-y-6">
      <p className="text-sm text-ink-muted">As of <time dateTime={feed.as_of}>{new Date(feed.as_of).toUTCString()}</time>. {feed.definition}</p>
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Measured availability by service</caption>
        <thead><tr><th scope="col" className="py-2 pr-4">Service</th><th scope="col" className="py-2 pr-4">Measured</th><th scope="col" className="py-2">Basis</th></tr></thead>
        <tbody>
          {feed.services.map((s, i) => (
            <tr key={i} className="border-t border-line align-top"><th scope="row" className="py-2 pr-4 font-medium">{s.service}{s.region ? ` (${s.region})` : ""}</th><td className="py-2 pr-4">{availabilityText(s)}</td><td className="py-2 text-ink-muted">{s.note}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="rounded-lg border border-line p-4 text-sm text-ink-muted">{feed.sla}</p>
    </div>
  );
}

export function AdvisoriesView({ feed }: { feed: AdvisoryFeed | null }) {
  if (feed === null) return <Unavailable what="The advisories" />;
  if (feed.items.length === 0) return <p className="rounded-lg border border-line p-4 text-ink">There are no published advisories.</p>;
  return (
    <ul className="space-y-6">
      {feed.items.map((a) => (
        <li key={a.advisory_id}>
          <article aria-label={a.latest_title} className="space-y-3 rounded-lg border border-line p-4">
            <h2 className="text-lg font-semibold text-ink">{a.latest_title}</h2>
            <ol className="space-y-3">
              {a.versions.map((v) => (
                <li key={v.seq} className="text-sm">
                  <p className="font-medium text-ink">{v.kind === "CORRECTION" ? "Correction" : v.kind === "PUBLISH" ? "Published" : v.kind.charAt(0) + v.kind.slice(1).toLowerCase()} <time dateTime={v.created_at} className="font-normal text-ink-muted">{v.created_at.slice(0, 16).replace("T", " ")} UTC</time></p>
                  {v.correction_reason ? <p className="text-ink-muted">Why corrected: {v.correction_reason}</p> : null}
                  <p className="text-ink">{v.body}</p>
                </li>
              ))}
            </ol>
          </article>
        </li>
      ))}
    </ul>
  );
}
