"use client";

import type { FamilyTextRevision, FxRateSet, PublicNoticeRow, RateCardSummary, SalesLead, MarketMatrixSummary, MarketRolloutItem, QuoteRequestItem } from "@zoiko/api-client";
import { Badge, Banner, Card, CopyId, DataTable, EmptyState, type Column } from "@zoiko/ui";
import { Globe2 } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";

const when = (v: string | null) => (v ? new Date(v).toLocaleString("en-GB") : "—");

/** The market matrix and the controls around it (ZID-COM-WF-2026-11 WF-I01, WF-I05): what is active, what still needs signing, where each market is in its
 * rollout, and the deal desk's quote requests. Reading only; every change is an action in the catalogue tab, decided by the cell. */
export function MarketView() {
  return (
    <div className="space-y-4">
      <Banner tone="info" icon={Globe2} title="A matrix is a release, not a price list">
        Rows are references to signed evidence. Four different people sign it, none of them its author, and an active matrix never changes. A market is sold only once its rollout reaches canary or production.
      </Banner>
      <MatricesCard />
      <RolloutCard />
      <FxCard />
      <PricingPageCard />
      <RateCardsCard />
      <NoticesCard />
      <FamilyTextCard />
      <LeadsCard />
      <QuoteRequestsCard />
    </div>
  );
}

function MatricesCard() {
  const list = useCellQuery([...COMMERCIAL_KEY, "matrices"], (api) => api.commercial.marketMatrices());
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<MarketMatrixSummary>[] = [
    { key: "v", header: "Version", pinned: true, render: (m) => `v${m.version}`, sortValue: (m) => m.version },
    { key: "s", header: "Status", render: (m) => <Badge tone={m.status === "ACTIVE" ? "ok" : m.status === "DRAFT" ? "info" : "neutral"} size="xs">{m.status}</Badge> },
    { key: "i", header: "Matrix ID", render: (m) => <CopyId value={m.matrix_id} label="matrix" /> },
    { key: "e", header: "Effective from", render: (m) => when(m.effective_from) },
    { key: "n", header: "Note", render: (m) => m.note ?? "—" },
    { key: "d", header: "Detail", render: (m) => <button type="button" className="text-sm font-medium text-brand-700 underline" onClick={() => setOpen(open === m.matrix_id ? null : m.matrix_id)}>{open === m.matrix_id ? "Hide" : "Show"}</button> },
  ];
  return (
    <Card title="Market matrices" description="Newest first. Copy an ID into the actions to add rows, sign, activate or withdraw." padded={false}>
      <QueryView query={list}>
        {(d) => (
          <>
            <DataTable<MarketMatrixSummary> caption="Market matrices" columns={columns} rows={d.items} rowKey={(m) => m.matrix_id} empty={<EmptyState title="No matrix yet" icon={Globe2}>Create a draft in the actions below. Until one is active, nothing is sellable.</EmptyState>} />
            {open ? <MatrixDetail id={open} /> : null}
          </>
        )}
      </QueryView>
    </Card>
  );
}

function MatrixDetail({ id }: { id: string }) {
  const detail = useCellQuery([...COMMERCIAL_KEY, "matrix", id], (api) => api.commercial.marketMatrix(id));
  return (
    <QueryView query={detail}>
      {(m) => (
        <div className="space-y-3 border-t border-border p-4 text-sm">
          <p>
            Signed by {m.approvals.length} of 4.
            {m.missing_approvals.length ? ` Still needed: ${m.missing_approvals.join(", ")}.` : " All four roles have signed."}
          </p>
          {m.incomplete_rows.length ? (
            <Banner tone="warn" title="Incomplete rows block activation" live="polite">
              {m.incomplete_rows.map((r) => `${r.family} ${r.country}: ${r.problems.join(", ")}`).join("; ")}
            </Banner>
          ) : null}
          {m.rights_alerts.length ? (
            <Banner tone="warn" title="Rights to renew" live="polite">
              {m.rights_alerts.map((a) => `${a.family} ${a.country} ${a.kind.toLowerCase().replace("_", " ")} ${a.state.toLowerCase()} on ${a.expires}`).join("; ")}
            </Banner>
          ) : null}
          <ul className="list-disc pl-5 text-xs text-ink-muted">
            {m.rows.map((r) => (
              <li key={`${r.family}-${r.country}`}>
                {r.family} in {r.country}: {r.seller_entity}, {r.merchant_route}, {r.currency}, tax {r.tax_class}, region {r.data_region}
              </li>
            ))}
          </ul>
        </div>
      )}
    </QueryView>
  );
}

function RolloutCard() {
  const list = useCellQuery([...COMMERCIAL_KEY, "rollout"], (api) => api.commercial.marketRollout());
  const columns: Column<MarketRolloutItem>[] = [
    { key: "f", header: "Family", pinned: true, render: (r) => r.family, sortValue: (r) => r.family },
    { key: "c", header: "Country", render: (r) => r.country, sortValue: (r) => r.country },
    { key: "s", header: "Stage", render: (r) => <Badge tone={r.stage === "CANARY" || r.stage === "PRODUCTION" ? "ok" : r.stage === "PAUSED" || r.stage === "ROLLED_BACK" ? "danger" : "neutral"} size="xs">{r.stage ?? "NOT STARTED"}</Badge> },
    { key: "b", header: "Last moved by", render: (r) => (r.decided_by ? <CopyId value={r.decided_by} label="person" /> : "—") },
    { key: "a", header: "When", render: (r) => when(r.decided_at) },
  ];
  return (
    <Card title="Rollout" description="Only canary and production are sold. A paused or rolled-back market stops selling at once." padded={false}>
      <QueryView query={list}>
        {(d) => <DataTable<MarketRolloutItem> caption="Rollout of each market" columns={columns} rows={d.items} rowKey={(r) => `${r.family}-${r.country}`} empty={<EmptyState title="No active matrix" icon={Globe2}>Rollout starts after a matrix is active.</EmptyState>} />}
      </QueryView>
    </Card>
  );
}

function QuoteRequestsCard() {
  const list = useCellQuery([...COMMERCIAL_KEY, "quote-requests"], (api) => api.commercial.quoteRequests());
  const columns: Column<QuoteRequestItem>[] = [
    { key: "w", header: "Sent", pinned: true, render: (q) => when(q.quote_requested_at), sortValue: (q) => q.quote_requested_at ?? "" },
    { key: "t", header: "Company", render: (q) => <CopyId value={q.tenant_id} label="tenant" /> },
    { key: "p", header: "Purpose", render: (q) => q.purpose.toLowerCase().replaceAll("_", " ") },
    { key: "f", header: "Services", render: (q) => q.families.join(", ") },
    { key: "m", header: "Markets", render: (q) => [q.subject_country, q.payer_country, q.operating_region].filter(Boolean).join(" / ") || "—" },
    { key: "x", header: "Needs a conversation", render: (q) => q.quote_snapshot?.exceptions?.map((e) => `${e.family} (${e.state.toLowerCase().replaceAll("_", " ")})`).join(", ") || "—" },
    { key: "h", header: "Reference", render: (q) => <span className="font-mono text-xs">{q.scope_hash.slice(0, 12)}</span> },
  ];
  return (
    <Card title="Quote requests" description="Saved configurations sent for a quote, with what was judged at the time. Nothing here is a price or a commitment." padded={false}>
      <QueryView query={list}>
        {(d) => <DataTable<QuoteRequestItem> caption="Quote requests from companies" columns={columns} rows={d.items} rowKey={(q) => q.draft_id} empty={<EmptyState title="No requests" icon={Globe2}>A request appears when a company sends a saved configuration.</EmptyState>} />}
      </QueryView>
    </Card>
  );
}

function FxCard() {
  const list = useCellQuery([...COMMERCIAL_KEY, "fx-sets"], (api) => api.commercial.fxRateSets());
  const columns: Column<FxRateSet>[] = [
    { key: "v", header: "Version", pinned: true, render: (f) => `v${f.version}`, sortValue: (f) => f.version },
    { key: "s", header: "Status", render: (f) => <Badge tone={f.status === "ACTIVE" ? "ok" : f.status === "DRAFT" ? "info" : "neutral"} size="xs">{f.status}</Badge> },
    { key: "i", header: "Rate set ID", render: (f) => <CopyId value={f.set_id} label="rate set" /> },
    { key: "src", header: "Source", render: (f) => `${f.source_ref} (as of ${f.as_of})` },
    { key: "r", header: "USD per unit", render: (f) => Object.entries(f.rates).map(([c, r]) => `${c} ${r}`).join(", ") || "none yet" },
  ];
  return (
    <Card title="FX rate sets" description="Used only to total reports in dollars. There are no default rates: with no active set, nothing but dollars is added." padded={false}>
      <QueryView query={list}>
        {(d) => <DataTable<FxRateSet> caption="FX rate sets" columns={columns} rows={d.items} rowKey={(f) => f.set_id} empty={<EmptyState title="No rate set" icon={Globe2}>Create a draft with a named source in the actions below.</EmptyState>} />}
      </QueryView>
    </Card>
  );
}

function PricingPageCard() {
  const state = useCellQuery([...COMMERCIAL_KEY, "pricing-page"], (api) => api.commercial.pricingPage());
  return (
    <Card title="Public pricing page packaging" description="The plan packaging buyers see is marked as being finalised until an appointed Founder and CCO, as different people, sign the page file by its hash.">
      <QueryView query={state}>
        {(d) => (
          <dl className="space-y-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <dt className="font-medium">Status</dt>
              <dd>
                <Badge tone={d.status === "APPROVED" ? "ok" : "info"} size="xs">
                  {d.status}
                </Badge>
              </dd>
            </div>
            <div>
              <dt className="font-medium">Signed by</dt>
              <dd>{d.signed_by.length ? d.signed_by.join(", ") : "nobody yet"}</dd>
            </div>
            <div>
              <dt className="font-medium">Still needed</dt>
              <dd>{d.missing.length ? d.missing.join(", ") : "none"}</dd>
            </div>
            <div>
              <dt className="font-medium">Page version and content hash</dt>
              <dd className="flex flex-wrap items-center gap-2">
                {d.version} <CopyId value={d.content_hash} label="content hash" />
              </dd>
            </div>
          </dl>
        )}
      </QueryView>
    </Card>
  );
}

function RateCardsCard() {
  const list = useCellQuery([...COMMERCIAL_KEY, "rate-cards"], (api) => api.commercial.rateCards());
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<RateCardSummary>[] = [
    { key: "v", header: "Version", pinned: true, render: (c) => `v${c.version}`, sortValue: (c) => c.version },
    { key: "s", header: "Status", render: (c) => <Badge tone={c.status === "ACTIVE" ? "ok" : c.status === "DRAFT" ? "info" : "neutral"} size="xs">{c.status}</Badge> },
    { key: "i", header: "Rate card ID", render: (c) => <CopyId value={c.card_id} label="rate card" /> },
    { key: "e", header: "Effective from", render: (c) => when(c.effective_from) },
    { key: "n", header: "Note", render: (c) => c.note ?? "—" },
    { key: "o", header: "Lines", render: (c) => <button type="button" className="font-medium text-brand-700 underline" onClick={() => setOpen(open === c.card_id ? null : c.card_id)}>{open === c.card_id ? "Hide" : "Show"}</button> },
  ];
  return (
    <Card title="Family rate cards" description="The rate for each service, payer country and period that buyers see and the estimate uses. There are no default rates. The CFO, CCO and Founder sign, as three different people." padded={false}>
      <QueryView query={list}>
        {(d) => (
          <>
            <DataTable<RateCardSummary> caption="Family rate cards" columns={columns} rows={d.items} rowKey={(c) => c.card_id} empty={<EmptyState title="No rate card" icon={Globe2}>With none active, every service reads Quote required. Create a draft in the actions below.</EmptyState>} />
            {open ? <RateCardDetailView id={open} /> : null}
          </>
        )}
      </QueryView>
    </Card>
  );
}

function RateCardDetailView({ id }: { id: string }) {
  const q = useCellQuery([...COMMERCIAL_KEY, "rate-card", id], (api) => api.commercial.rateCard(id));
  return (
    <div className="border-t border-border p-4 text-sm">
      <QueryView query={q}>
        {(d) => (
          <div className="space-y-3">
            <p>
              Signed: {d.approvals.length ? d.approvals.map((a) => a.role).join(", ") : "nobody yet"}. Still needed: {d.missing_approvals.length ? d.missing_approvals.join(", ") : "none"}.
            </p>
            {d.lines.length === 0 ? (
              <p className="text-ink-muted">No lines yet.</p>
            ) : (
              <ul className="space-y-1">
                {d.lines.map((l) => (
                  <li key={`${l.family}-${l.country}-${l.period}`}>
                    <span className="font-medium">{l.family} {l.country} {l.period.toLowerCase()}</span>: {l.currency} {l.unit_amount} per {l.charge_unit}, {l.included_units.toLocaleString("en")} included
                    {l.minimum_amount ? `, ${l.minimum_model === "FLOOR" ? "minimum spend" : "platform fee"} ${l.currency} ${l.minimum_amount}` : ""}. <span className="text-ink-muted">Source {l.source_ref}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </QueryView>
    </div>
  );
}

function NoticesCard() {
  const list = useCellQuery([...COMMERCIAL_KEY, "public-notices"], (api) => api.commercial.publicNotices());
  const columns: Column<PublicNoticeRow>[] = [
    { key: "v", header: "Version", pinned: true, render: (n) => n.version },
    { key: "s", header: "Status", render: (n) => <Badge tone={n.status === "ACTIVE" ? "ok" : n.status === "DRAFT" ? "info" : "neutral"} size="xs">{n.status}</Badge> },
    { key: "i", header: "Notice ID", render: (n) => <CopyId value={n.notice_id} label="notice" /> },
    { key: "t", header: "Text", render: (n) => n.body },
    { key: "a", header: "Activated", render: (n) => when(n.activated_at) },
  ];
  return (
    <Card title="Inquiry privacy notice" description="Legal's text shown beside the public inquiry form. Until one is activated, visitors see a marked draft and each inquiry records that draft's version." padded={false}>
      <QueryView query={list}>
        {(d) => (
          <>
            <p className="px-4 pt-3 text-sm">
              Shown now: <span className="font-mono">{d.served.version}</span> ({d.served.status === "DRAFT" ? "draft, pending legal approval" : "active"}).
            </p>
            <DataTable<PublicNoticeRow> caption="Inquiry privacy notices" columns={columns} rows={d.items} rowKey={(n) => n.notice_id} empty={<EmptyState title="No notice proposed" icon={Globe2}>Propose Legal's text in the actions below.</EmptyState>} />
          </>
        )}
      </QueryView>
    </Card>
  );
}

function FamilyTextCard() {
  const list = useCellQuery([...COMMERCIAL_KEY, "family-texts"], (api) => api.commercial.familyTexts());
  const columns: Column<FamilyTextRevision>[] = [
    { key: "f", header: "Family", pinned: true, render: (r) => `${r.family} v${r.version}`, sortValue: (r) => `${r.family}${String(r.version).padStart(4, "0")}` },
    { key: "s", header: "Status", render: (r) => <Badge tone={r.status === "ACTIVE" ? "ok" : r.status === "DRAFT" ? "info" : "neutral"} size="xs">{r.status}</Badge> },
    { key: "i", header: "Revision ID", render: (r) => <CopyId value={r.rev_id} label="revision" /> },
    { key: "n", header: "Name and unit", render: (r) => `${r.name} (${r.unit})` },
    { key: "q", header: "Sales qualifier", render: (r) => r.sales_qualifier || "—" },
    { key: "w", header: "Why", render: (r) => r.reason },
  ];
  return (
    <Card title="Family wording" description="Only the name, unit and sales qualifier a buyer reads. What a family needs or does stays in the reviewed catalogue file." padded={false}>
      <QueryView query={list}>
        {(d) => <DataTable<FamilyTextRevision> caption="Family wording revisions" columns={columns} rows={d.items} rowKey={(r) => r.rev_id} empty={<EmptyState title="No revisions" icon={Globe2}>Buyers see the wording in the reviewed catalogue file until a revision is activated.</EmptyState>} />}
      </QueryView>
    </Card>
  );
}

function LeadsCard() {
  const list = useCellQuery([...COMMERCIAL_KEY, "sales-leads"], (api) => api.commercial.salesLeads());
  const columns: Column<SalesLead>[] = [
    { key: "w", header: "Received", pinned: true, render: (l) => when(l.created_at), sortValue: (l) => l.created_at ?? "" },
    { key: "s", header: "Status", render: (l) => <Badge tone={l.status === "OFFER_SENT" ? "ok" : l.status === "SPAM" || l.status === "CLOSED" ? "neutral" : "info"} size="xs">{l.status.replace("_", " ")}</Badge> },
    { key: "i", header: "Inquiry ID", render: (l) => <CopyId value={l.lead_id} label="inquiry" /> },
    { key: "k", header: "Kind", render: (l) => (l.partner_type ? `${l.kind.toLowerCase()} (${l.partner_type.toLowerCase().replaceAll("_", " ")})` : l.kind.toLowerCase()) },
    { key: "c", header: "Company", render: (l) => `${l.company} (${l.country})` },
    { key: "p", header: "Contact", render: (l) => `${l.contact_name}, ${l.work_email}` },
    { key: "v", header: "Volume", render: (l) => l.volume_band.toLowerCase().replaceAll("_", " ") },
    { key: "f", header: "Services asked about", render: (l) => l.capabilities.join(", ") || "—" },
  ];
  return (
    <Card title="Sales inquiries" description="From the public pricing page. Each is a request for a conversation; none has created an account, quote, contract or payment." padded={false}>
      <QueryView query={list}>
        {(d) => <DataTable<SalesLead> caption="Sales inquiries" columns={columns} rows={d.items} rowKey={(l) => l.lead_id} empty={<EmptyState title="No inquiries" icon={Globe2}>An inquiry appears when someone sends the form on the pricing page.</EmptyState>} />}
      </QueryView>
    </Card>
  );
}
