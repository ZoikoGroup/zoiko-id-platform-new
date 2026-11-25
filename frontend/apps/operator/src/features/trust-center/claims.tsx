"use client";

import type { TrustClaim, TrustClaimRow, TrustRole } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { Mono, RegError } from "../regulatory/common";

import { blockerText, offers, ROLES, shortHash, STATUS } from "./logic";

const KEY = ["trust", "claims"] as const;
const TYPES = ["third_party_assessment", "certification", "numerical", "sla", "functionality", "legal", "customer_endorsement", "roadmap"].map((t) => ({ id: t, label: t.replaceAll("_", " ") }));

function NewClaim({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ exact_text: "", claim_type: "third_party_assessment", entity_id: "", service: "", markets: "US", channels: "trust_center", digests: "", valid_until: "" });
  const draft = useCellMutation(
    (api, v: typeof f) =>
      api.trustCenter.draft({
        exact_text: v.exact_text, locale: "en-US", claim_type: v.claim_type, entity_id: v.entity_id, service: v.service,
        markets: v.markets.split(/[ ,]+/).filter(Boolean), channels: v.channels.split(/[ ,]+/).filter(Boolean),
        evidence_digests: v.digests.split(/[ ,\n]+/).filter(Boolean), valid_until: v.valid_until || null,
      }),
    { invalidate: [KEY as never], success: "Draft saved", onSuccess: onDone },
  );
  return (
    <Card title="Draft a claim" description="Write the exact sentence a visitor would read. Evidence is linked by digest. Nothing is public until two reviewers in different roles approve this exact text and a third person publishes it.">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); draft.mutate(f); }}>
        <TextField label="Exact sentence" multiline rows={3} isRequired value={f.exact_text} onChange={(v) => setF({ ...f, exact_text: v })} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Select label="Kind of claim" options={TYPES} value={f.claim_type} onChange={(v) => setF({ ...f, claim_type: v })} description="A sentence that sounds like a certification is treated as one whatever you pick." />
          <TextField label="Legal entity" isRequired value={f.entity_id} onChange={(v) => setF({ ...f, entity_id: v })} />
          <TextField label="Service" isRequired value={f.service} onChange={(v) => setF({ ...f, service: v })} />
          <TextField label="Markets (two-letter codes)" value={f.markets} onChange={(v) => setF({ ...f, markets: v })} />
          <TextField label="Where it appears" value={f.channels} onChange={(v) => setF({ ...f, channels: v })} description="For example trust_center, website, sales_deck." />
          <TextField label="Valid until" type="date" value={f.valid_until} onChange={(v) => setF({ ...f, valid_until: v })} />
        </div>
        <TextField label="Evidence digests" multiline rows={2} mono value={f.digests} onChange={(v) => setF({ ...f, digests: v })} description="One SHA-256 digest per line, from the Evidence screen." />
        {draft.error ? <RegError error={draft.error} /> : null}
        <Button type="submit" pending={draft.isPending}>Save draft</Button>
      </form>
    </Card>
  );
}

function Detail({ id, revision, onChanged }: { id: string; revision: number; onChanged: () => void }) {
  const q = useCellQuery<TrustClaim>([...KEY, id, revision], (api) => api.trustCenter.claim(id, revision));
  return <QueryView query={q}>{(c) => <DetailBody c={c} onChanged={onChanged} />}</QueryView>;
}

function DetailBody({ c, onChanged }: { c: TrustClaim; onChanged: () => void }) {
  const [role, setRole] = useState<TrustRole>("security");
  const [reason, setReason] = useState("");
  const can = offers(c);
  const done = { invalidate: [KEY as never], onSuccess: onChanged };
  const approve = useCellMutation((api, r: TrustRole) => api.trustCenter.approve(c.claim_id, c.revision, r, c.evidence_digests), { ...done, success: "Approval recorded" });
  const refuse = useCellMutation((api, v: { r: TrustRole; why: string }) => api.trustCenter.refuse(c.claim_id, c.revision, v.r, v.why), { ...done, success: "Refusal recorded" });
  const publish = useCellMutation<void, unknown>((api) => api.trustCenter.publish(c.claim_id, c.revision, c.content_hash), { ...done, success: "Published through the gate" });
  const rollback = useCellMutation<void, unknown>((api) => api.trustCenter.rollback(c.claim_id), { ...done, success: "Rolled back to the last approved revision" });
  const withdraw = useCellMutation((api, why: string) => api.trustCenter.withdraw(c.claim_id, why, c.revision), { ...done, success: "Withdrawn. Purge every cached copy now." });
  const s = STATUS[c.status] ?? { tone: "neutral" as const, text: c.status };
  return (
    <Card title={`Revision ${c.revision}`} description={`Hash ${shortHash(c.content_hash)}. Every approval is for this exact hash; any change to the text, scope, evidence, places or date needs a new revision.`}>
      <div className="space-y-3 text-sm">
        <p><Badge tone={s.tone}>{s.text}</Badge> <span className="text-ink-muted">Treated as: {c.effective_class.replaceAll("_", " ")}</span></p>
        <blockquote className="rounded-md border border-line bg-surface-muted p-3 text-ink">{c.exact_text}</blockquote>
        <p className="text-ink-muted">Covers {c.scope.entity_id}, {c.scope.service}, {c.scope.markets.join(", ")} ({c.scope.environment}). Appears in {c.channels.join(", ")}. {c.valid_until ? `Valid until ${c.valid_until}.` : "No end date."}</p>
        <div>
          <p className="font-medium text-ink">Approvals for this exact hash</p>
          {c.approvals.filter((a) => a.this_version).length === 0 ? <p className="text-ink-muted">None yet. {c.still_needed[0]}</p> : (
            <ul className="list-disc pl-5">{c.approvals.filter((a) => a.this_version).map((a, i) => <li key={i}>{a.role}: {a.refused ? `refused (${a.reason})` : "approved"} <span className="text-ink-muted">at {new Date(a.decided_at).toLocaleString()}</span></li>)}</ul>
          )}
        </div>
        {c.blockers.length ? (
          <Banner tone="warn" title="What stops this being published today">
            <ul className="list-disc pl-5">{c.blockers.map((b, i) => <li key={i}>{blockerText(b.code, b.detail)}</li>)}</ul>
          </Banner>
        ) : c.status === "PUBLISHED" ? null : <Banner tone="ok" title="Ready: every check passes today" />}
        {can.approve ? (
          <div className="flex flex-wrap items-end gap-2">
            <Select label="Review as" options={ROLES.map((r) => ({ id: r, label: r }))} value={role} onChange={(v) => setRole(v as TrustRole)} description="You can act only in a role you hold." />
            <Button variant="secondary" pending={approve.isPending} onPress={() => approve.mutate(role)}>Approve</Button>
            <TextField label="Reason to refuse" value={reason} onChange={setReason} />
            <Button variant="secondary" pending={refuse.isPending} isDisabled={reason.trim().length < 3} onPress={() => refuse.mutate({ r: role, why: reason })}>Refuse</Button>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {can.publish ? <Button pending={publish.isPending} onPress={() => publish.mutate()}>Publish</Button> : null}
          {can.rollback ? <Button variant="secondary" pending={rollback.isPending} onPress={() => rollback.mutate()}>Roll back to last approved</Button> : null}
          {can.withdraw ? (
            <Button variant="secondary" pending={withdraw.isPending} isDisabled={reason.trim().length < 3} onPress={() => withdraw.mutate(reason)}>Withdraw now</Button>
          ) : null}
        </div>
        {can.withdraw ? <p className="text-xs text-ink-muted">Withdrawing needs only one responder and a reason in the box above; it takes effect at once and a review by someone else is then due.</p> : null}
        {[approve, refuse, publish, rollback, withdraw].map((m, i) => (m.error ? <RegError key={i} error={m.error} /> : null))}
        <div>
          <p className="font-medium text-ink">History</p>
          <ul className="list-disc pl-5 text-ink-muted">{c.history.map((h) => <li key={h.revision}>Revision {h.revision}: {(STATUS[h.status] ?? { text: h.status }).text} <Mono>{shortHash(h.content_hash)}</Mono></li>)}</ul>
        </div>
      </div>
    </Card>
  );
}

export function TrustClaimsPage() {
  const q = useCellQuery([...KEY, "list"], (api) => api.trustCenter.claims());
  const [sel, setSel] = useState<{ id: string; revision: number } | null>(null);
  const [adding, setAdding] = useState(false);
  const cols: Column<TrustClaimRow>[] = [
    { key: "text", header: "Claim", render: (r) => r.exact_text, sortValue: (r) => r.exact_text },
    { key: "type", header: "Kind", render: (r) => r.claim_type.replaceAll("_", " ") },
    { key: "status", header: "State", render: (r) => <Badge tone={(STATUS[r.status] ?? { tone: "neutral" as const }).tone}>{(STATUS[r.status] ?? { text: r.status }).text}</Badge>, sortValue: (r) => r.status },
    { key: "rev", header: "Revision", render: (r) => r.revision },
    { key: "until", header: "Valid until", render: (r) => r.valid_until ?? "No end date" },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        title="Public claims"
        description="Every public statement is a record: exact words, scope, evidence and an end date. It goes live only when two reviewers in different roles approve this exact text and someone else publishes it. A claim whose evidence lapses leaves the public page at once."
        breadcrumbs={[{ label: "Trust Center" }, { label: "Claims" }]}
        actions={<Button onPress={() => setAdding((v) => !v)}>{adding ? "Close" : "Draft a claim"}</Button>}
      />
      {adding ? <NewClaim onDone={() => setAdding(false)} /> : null}
      <QueryView query={q}>
        {(d) => (
          <DataTable
            caption="Public claims, newest revision of each"
            columns={cols}
            rows={d.items}
            rowKey={(r) => `${r.claim_id}-${r.revision}`}
            onRowAction={(r) => setSel({ id: r.claim_id, revision: r.revision })}
            selectedKey={sel ? `${sel.id}-${sel.revision}` : null}
            empty="No claims yet. Nothing unapproved is ever shown to the public."
          />
        )}
      </QueryView>
      {sel ? <Detail id={sel.id} revision={sel.revision} onChanged={() => undefined} /> : null}
    </div>
  );
}
