"use client";

import type { ScreeningExportFetched, ScreeningReviewCaseDetail, ScreeningDisposition, ScreeningProposal, ScreeningQueueItem } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { Mono, RegError } from "../regulatory/common";

import { ACTIONS_FOR, askProblem, CASE_STATE, codes, DISPOSITION, EDD_ITEMS, EDD_STATE, EMPTY_FORM, INSUFFICIENT_CODES, needsChecker, proposeProblem, reasonText, shortId, type ProposeForm } from "./case-logic";

const KEY = ["screening", "cases"] as const;
const STATES = ["", "REVIEW_REQUIRED", "SOURCE_UNAVAILABLE", "NO_CANDIDATE", "CLOSED_CLEAR_FOR_SCOPE", "CLOSED_RESTRICTED", "CLOSED_UNRESOLVED"] as const;

function Dimensions({ d }: { d: ScreeningReviewCaseDetail }) {
  const m = d.model;
  const rows: [string, string][] = [
    ["Coverage", m.coverage_status.replaceAll("_", " ").toLowerCase()],
    ["Sanctions", `${m.sanctions.coverage.replaceAll("_", " ").toLowerCase()}${m.sanctions.result ? `, ${m.sanctions.result.replaceAll("_", " ").toLowerCase()}` : ""}`],
    ["PEP and associates", m.pep_rca.coverage.replaceAll("_", " ").toLowerCase()],
    ["Adverse media", m.adverse_media.coverage.replaceAll("_", " ").toLowerCase()],
    ["Match", m.match_status.replaceAll("_", " ").toLowerCase()],
    ["Disposition", DISPOSITION[m.decision.status]],
    ["Review", m.review.replaceAll("_", " ").toLowerCase()],
  ];
  return (
    <Card title="The four answers" description="Coverage, match, disposition and review are separate. A no-candidate result covers only the sources and snapshots named below, and is never a finding of no risk.">
      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3 border-b border-border py-1">
            <dt className="text-ink-muted">{k}</dt>
            <dd className="font-medium text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {m.decision.reason_codes.length ? <ul className="mt-2 list-disc pl-5 text-sm">{m.decision.reason_codes.map((c) => <li key={c}>{reasonText(c)}</li>)}</ul> : null}
      <ul className="mt-2 space-y-1 text-sm text-ink-muted">
        {m.source_results.map((s) => (
          <li key={s.source_id}>
            <Mono>{s.source_id}</Mono> {s.snapshot ? `snapshot ${s.snapshot.slice(0, 19)}…` : "not checked"}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Pending({ p, caseId }: { p: ScreeningProposal; caseId: string }) {
  const refresh = { invalidate: [KEY as never, [...KEY, caseId] as never] };
  const finalise = useCellMutation<void, unknown>((api) => api.regulatory.screeningFinalise(p.proposal_id), { ...refresh, success: "Finalised" });
  const withdraw = useCellMutation<void, unknown>((api) => api.regulatory.screeningWithdraw(p.proposal_id), { ...refresh, success: "Withdrawn" });
  return (
    <li className="space-y-2 rounded-control border border-border p-3">
      <p>
        <Badge tone="info">Waiting for a second person</Badge> {DISPOSITION[p.disposition]} ({p.action.toLowerCase()}), proposed as {p.maker_role} on <DateTime value={p.created_at} />
      </p>
      <p>{p.rationale}</p>
      <p className="text-ink-muted">Reasons: {p.reason_codes.join(", ")}. Evidence: {p.evidence_refs.join(", ") || "none"}.</p>
      <div className="flex gap-2">
        <Button pending={finalise.isPending} onPress={() => finalise.mutate()}>Finalise as the second person</Button>
        <Button variant="secondary" pending={withdraw.isPending} onPress={() => withdraw.mutate()}>Withdraw (the maker only)</Button>
      </div>
      {finalise.error ? <RegError error={finalise.error} /> : null}
      {withdraw.error ? <RegError error={withdraw.error} /> : null}
    </li>
  );
}

function Information({ d }: { d: ScreeningReviewCaseDetail }) {
  const key = ["screening", "cases", d.request_id, "information"] as const;
  const q = useCellQuery([...key], (api) => api.regulatory.screeningInformationRequests(d.request_id));
  const refresh = { invalidate: [[...key]] as never[] };
  const [items, setItems] = useState<string[]>([]);
  const [days, setDays] = useState("14");
  const ask = useCellMutation((api, v: { items: string[]; days: number }) => api.regulatory.screeningAskInformation(d.request_id, v.items, v.days), { ...refresh, success: "Asked", onSuccess: () => setItems([]) });
  const review = useCellMutation((api, v: { id: string; accepted: boolean; code?: string }) => api.regulatory.screeningReviewInformation(v.id, v.accepted, v.code), { ...refresh, success: "Reviewed" });
  const problem = askProblem(items, Number(days));
  const toggle = (id: string) => setItems((x) => (x.includes(id) ? x.filter((i) => i !== id) : [...x, id]));
  return (
    <Card title="Information from the customer" description="Ask for named items. The customer is told what is wanted and by when, never why. Someone other than you reviews the answer, and silence clears nothing.">
      <div className="space-y-3 text-sm">
        <QueryView query={q}>
          {(r) =>
            r.items.length ? (
              <ul className="space-y-2">
                {r.items.map((i) => (
                  <li key={i.edd_id} className="space-y-1">
                    <Badge tone={i.state === "ACCEPTED" ? "ok" : i.state === "REQUESTED" || i.state === "RECEIVED" ? "info" : "warn"}>{EDD_STATE[i.state] ?? i.state}</Badge> due <DateTime value={i.due_at} /> ({i.reminders} reminder{i.reminders === 1 ? "" : "s"})
                    {i.still_needed.length ? <div className="text-ink-muted">Still wanted: {i.still_needed.map((x) => EDD_ITEMS[x] ?? x).join(", ")}</div> : null}
                    {i.state === "RECEIVED" ? (
                      <div className="flex flex-wrap gap-2">
                        <Button pending={review.isPending} onPress={() => review.mutate({ id: i.edd_id, accepted: true })}>Accept the answer</Button>
                        {INSUFFICIENT_CODES.map((c) => (
                          <Button key={c} variant="secondary" pending={review.isPending} onPress={() => review.mutate({ id: i.edd_id, accepted: false, code: c })}>Not enough: {c.toLowerCase().replaceAll("_", " ")}</Button>
                        ))}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-muted">Nothing has been asked for on this case.</p>
            )
          }
        </QueryView>
        <fieldset className="space-y-1">
          <legend className="font-medium">Ask for</legend>
          {Object.entries(EDD_ITEMS).map(([id, label]) => (
            <label key={id} className="flex items-center gap-2">
              <input type="checkbox" checked={items.includes(id)} onChange={() => toggle(id)} /> {label}
            </label>
          ))}
        </fieldset>
        <TextField label="Days to answer (3 to 60)" value={days} onChange={setDays} />
        {items.length > 0 && problem ? <p className="text-danger">{problem}</p> : null}
        <Button pending={ask.isPending} isDisabled={problem !== null} onPress={() => ask.mutate({ items, days: Number(days) })}>Ask the customer</Button>
        {ask.error ? <RegError error={ask.error} /> : null}
        {review.error ? <RegError error={review.error} /> : null}
      </div>
    </Card>
  );
}

function Propose({ d }: { d: ScreeningReviewCaseDetail }) {
  const [f, setF] = useState<ProposeForm>(EMPTY_FORM);
  const problem = f.rationale || f.reasons ? proposeProblem(f) : null;
  const propose = useCellMutation(
    (api, form: ProposeForm) =>
      api.regulatory.screeningPropose(d.request_id, {
        disposition: form.disposition,
        action: form.action,
        expected_revision: d.revision,
        rationale: form.rationale.trim(),
        reason_codes: codes(form.reasons),
        evidence_refs: form.evidence.split(/[\n,]+/).map((e) => e.trim()).filter(Boolean),
        legal_citation: form.citation.trim() || null,
        review_by: form.reviewBy ? new Date(form.reviewBy).toISOString() : null,
      }),
    { invalidate: [KEY as never, [...KEY, d.request_id] as never], success: (r) => (r.state === "DECIDED" ? "Decision recorded" : "Proposed: a second person must finalise it"), onSuccess: () => setF(EMPTY_FORM) },
  );
  const set = (patch: Partial<ProposeForm>) => setF((x) => ({ ...x, ...patch }));
  return (
    <Card title="Propose a decision" description="A hold or a request for evidence takes effect at once. Anything that clears, restricts or prohibits waits for a second person, and the freeze, block and report acts are Legal's alone.">
      <div className="space-y-3 text-sm">
        <Select
          label="Decision"
          value={f.disposition}
          onChange={(v) => set({ disposition: v as ScreeningDisposition, action: ACTIONS_FOR[v as ScreeningDisposition][0]! })}
          options={(Object.keys(DISPOSITION) as ScreeningDisposition[]).map((x) => ({ id: x, label: DISPOSITION[x] }))}
        />
        <Select label="Act" value={f.action} onChange={(v) => set({ action: v as ProposeForm["action"] })} options={ACTIONS_FOR[f.disposition].map((a) => ({ id: a, label: a.toLowerCase() }))} />
        <TextField label="Reason (what you found and why it supports this)" multiline rows={3} value={f.rationale} onChange={(v) => set({ rationale: v })} />
        <TextField label="Reason codes (for example CONFLICTING_DOB)" value={f.reasons} onChange={(v) => set({ reasons: v })} />
        <TextField label="Evidence references (comma separated)" value={f.evidence} onChange={(v) => set({ evidence: v })} />
        <TextField label="Legal citation (a prohibition or a legal act)" value={f.citation} onChange={(v) => set({ citation: v })} />
        <label className="block" htmlFor="review-by">Look at this again by</label>
        <input id="review-by" type="datetime-local" value={f.reviewBy} onChange={(e) => set({ reviewBy: e.target.value })} className="rounded-control border border-border bg-surface p-2 text-ink" />
        {needsChecker(f.disposition, f.action) ? <p className="text-ink-muted">This needs a second person, who is not you.</p> : null}
        {problem ? <p className="text-danger">{problem}</p> : null}
        <Button pending={propose.isPending} isDisabled={proposeProblem(f) !== null} onPress={() => propose.mutate(f)}>Submit</Button>
        {propose.error ? <RegError error={propose.error} /> : null}
      </div>
    </Card>
  );
}

function Exports({ d }: { d: ScreeningReviewCaseDetail }) {
  const [purpose, setPurpose] = useState("");
  const [sensitive, setSensitive] = useState(false);
  const [exportId, setExportId] = useState("");
  const request = useCellMutation(
    (api, v: { purpose: string; sensitive: boolean }) => api.regulatory.screeningExportRequest(d.request_id, { purpose: v.purpose, include_sensitive: v.sensitive, ttl_days: 7 }),
    { success: (r) => (r.state === "READY" ? "Package built" : "Waiting for a second person to approve the sensitive material"), onSuccess: (r) => setExportId(r.export_id) },
  );
  const approve = useCellMutation<void, unknown>((api) => api.regulatory.screeningExportApprove(exportId), { success: "Approved and built" });
  const fetch = useCellMutation<void, ScreeningExportFetched>((api) => api.regulatory.screeningExportFetch(exportId), {});
  return (
    <Card title="Evidence package" description="A package states its purpose, is watermarked and expires. It holds what was checked and decided. Sensitive material (the subject, the candidates) needs a second person who is not you, and even then leaves only as hashes.">
      <div className="space-y-3 text-sm">
        <TextField label="What is it for? (at least 10 characters)" value={purpose} onChange={setPurpose} />
        <label className="flex items-center gap-2"><input type="checkbox" checked={sensitive} onChange={() => setSensitive((v) => !v)} />Include the sensitive material (needs a second approver)</label>
        <Button pending={request.isPending} isDisabled={purpose.trim().length < 10} onPress={() => request.mutate({ purpose: purpose.trim(), sensitive })}>Request the package</Button>
        {request.error ? <RegError error={request.error} /> : null}
        {exportId ? (
          <div className="space-y-2 rounded-control border border-border p-3">
            <p>Package <Mono>{exportId.slice(0, 8)}</Mono></p>
            <div className="flex gap-2">
              <Button variant="secondary" pending={approve.isPending} onPress={() => approve.mutate()}>Approve (a different person)</Button>
              <Button variant="secondary" pending={fetch.isPending} onPress={() => fetch.mutate()}>Fetch and check</Button>
            </div>
            {approve.error ? <RegError error={approve.error} /> : null}
            {fetch.error ? <RegError error={fetch.error} /> : null}
            {fetch.data ? (
              <p>
                {fetch.data.verified ? <Badge tone="ok">Intact</Badge> : <Badge tone="danger">Does not verify</Badge>} Holds {fetch.data.bundle.included.length} items and leaves out {fetch.data.bundle.excluded.length}. Expires {new Date(fetch.data.bundle.expires_at).toLocaleString()}.
                {fetch.data.problems.length ? ` Problems: ${fetch.data.problems.join(", ")}.` : ""}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function Watch({ d }: { d: ScreeningReviewCaseDetail }) {
  const [days, setDays] = useState("30");
  const [watchId, setWatchId] = useState("");
  const create = useCellMutation((api, n: number) => api.regulatory.screeningWatchCreate(d.request_id, { cadence_days: n, triggers: ["SCHEDULE", "LIST_UPDATE"] }), {
    success: "Watch created, inactive until two others approve it",
    onSuccess: (r) => setWatchId(r.watch_id),
  });
  const status = useCellQuery(["screening", "watch", watchId], (api) => api.regulatory.screeningWatch(watchId), { enabled: !!watchId });
  const approve = useCellMutation<void, unknown>((api) => api.regulatory.screeningWatchApprove(watchId), { invalidate: [["screening", "watch", watchId] as never], success: "Approval recorded" });
  const activate = useCellMutation<void, unknown>((api) => api.regulatory.screeningWatchActivate(watchId), { invalidate: [["screening", "watch", watchId] as never], success: "Switched on" });
  return (
    <Card title="Ongoing screening" description="Screens this subject again when the cadence runs out. Two people other than its creator approve it, and it runs only where ongoing monitoring is approved for the market. It is shown as continuous only while every source has a completed screening inside the cadence: an attempt does not count.">
      <div className="space-y-3 text-sm">
        <TextField label="Screen again every (days)" value={days} onChange={setDays} />
        <Button pending={create.isPending} isDisabled={!/^\d{1,3}$/.test(days) || Number(days) < 1 || Number(days) > 365} onPress={() => create.mutate(Number(days))}>Create the watch</Button>
        {create.error ? <RegError error={create.error} /> : null}
        {watchId ? (
          <div className="space-y-2 rounded-control border border-border p-3">
            <div className="flex gap-2">
              <Button variant="secondary" pending={approve.isPending} onPress={() => approve.mutate()}>Approve</Button>
              <Button pending={activate.isPending} onPress={() => activate.mutate()}>Switch on</Button>
            </div>
            {approve.error ? <RegError error={approve.error} /> : null}
            {activate.error ? <RegError error={activate.error} /> : null}
            <QueryView query={status}>
              {(w) => (
                <p>
                  {w.active ? <Badge tone="ok">On</Badge> : <Badge tone="warn">Off</Badge>} {w.approvals} of 2 approvals. {w.runs} runs. {w.continuous ? "Monitored continuously: every source has a recent success." : "Not shown as continuous: a source has no recent completed screening."}
                </p>
              )}
            </QueryView>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function Detail({ id }: { id: string }) {
  const q = useCellQuery([...KEY, id], (api) => api.regulatory.screeningCase(id));
  return (
    <QueryView query={q}>
      {(d) => (
        <div className="space-y-4">
          <Card title={`Case ${shortId(d.request_id)} · ${d.market} · ${d.purpose.toLowerCase()}`} description="Restricted. Reading this case is recorded.">
            <div className="space-y-2 text-sm">
              <p>
                <Badge tone={CASE_STATE[d.state].tone}>{CASE_STATE[d.state].text}</Badge> Revision {d.revision}.{" "}
                {d.chain_intact ? "The decision chain is intact." : <span className="text-danger">The decision chain does not verify.</span>}
              </p>
              {d.subject ? (
                <p>
                  <strong>{d.subject.name}</strong> ({d.subject.kind.toLowerCase()})
                  {d.subject.birth ? `, born ${d.subject.birth}` : ""}
                  {d.subject.countries.length ? `, ${d.subject.countries.join(", ")}` : ""}. Identity from {d.subject.identity_source.replaceAll("_", " ").toLowerCase()}.
                </p>
              ) : null}
              <Banner tone="info" title="A candidate is not a finding">
                A similar name, a nationality or a language is not proof of a listing. Check the identifiers before deciding.
              </Banner>
            </div>
          </Card>
          <Dimensions d={d} />
          <Card title="Candidates" description="What matched, which list and version, and why. Candidate evidence only.">
            <DataTable<ScreeningReviewCaseDetail["candidates"][number]>
              caption="Candidates"
              rows={d.candidates}
              rowKey={(c) => c.candidate_id}
              empty="No candidate."
              columns={[
                { key: "n", header: "Listed as", pinned: true, render: (c) => c.matched_name },
                { key: "l", header: "List", render: (c) => <Mono>{c.list_id}</Mono> },
                { key: "s", header: "Strength", render: (c) => <Badge tone={c.strength === "STRONG" ? "danger" : "warn"}>{c.strength.toLowerCase()}</Badge> },
                { key: "c", header: "Score", render: (c) => c.score.toFixed(2) },
                { key: "r", header: "Why", render: (c) => c.reasons.map((r) => r.replaceAll("_", " ").toLowerCase()).join(", ") },
              ]}
            />
          </Card>
          {d.proposals.length ? (
            <Card title="Waiting for a second person">
              <ul className="space-y-3 text-sm">{d.proposals.map((p) => <Pending key={p.proposal_id} p={p} caseId={d.request_id} />)}</ul>
            </Card>
          ) : null}
          <Card title="Decisions" description="Each decision cites the one before. Nothing is edited.">
            <DataTable<ScreeningReviewCaseDetail["decisions"][number]>
              caption="Decision chain"
              rows={d.decisions}
              rowKey={(r) => String(r.revision)}
              empty="No decision yet."
              columns={[
                { key: "v", header: "No.", pinned: true, render: (r) => r.revision },
                { key: "d", header: "Decision", render: (r) => DISPOSITION[r.disposition] },
                { key: "k", header: "Kind", render: (r) => r.kind.toLowerCase() },
                { key: "w", header: "When", render: (r) => <DateTime value={r.decided_at} /> },
                { key: "h", header: "Hash", render: (r) => <Mono>{r.case_hash.slice(0, 12)}…</Mono> },
              ]}
            />
          </Card>
          <Propose d={d} />
          <Information d={d} />
          <Exports d={d} />
          <Watch d={d} />
        </div>
      )}
    </QueryView>
  );
}

export function ScreeningCasesPage() {
  const [state, setState] = useState<string>("REVIEW_REQUIRED");
  const [open, setOpen] = useState<string | null>(null);
  const q = useCellQuery([...KEY, "queue", state], (api) => api.regulatory.screeningQueue(state || undefined));
  const columns: Column<ScreeningQueueItem>[] = [
    { key: "i", header: "Case", pinned: true, render: (r) => <Mono>{shortId(r.request_id)}</Mono> },
    { key: "s", header: "State", render: (r) => <Badge tone={CASE_STATE[r.state].tone}>{CASE_STATE[r.state].text}</Badge> },
    { key: "t", header: "Subject", render: (r) => r.subject_type.toLowerCase() },
    { key: "m", header: "Market", render: (r) => r.market },
    { key: "p", header: "Purpose", render: (r) => r.purpose.toLowerCase() },
    { key: "a", header: "Opened", render: (r) => <DateTime value={r.created_at} /> },
    { key: "o", header: "", render: (r) => <Button variant="secondary" onPress={() => setOpen(r.request_id)}>Open</Button> },
  ];
  return (
    <>
      <PageHeader
        title="Screening cases"
        description="The cases waiting for a person, across customers, oldest first. The list shows a case alias, never a name. A decision is proposed by one person and finalised by another; a prohibition and the freeze, block and report acts are Legal's."
        breadcrumbs={[{ label: "Screening" }, { label: "Cases" }]}
      />
      <div className="space-y-4">
        <Select label="Show" value={state} onChange={setState} options={STATES.map((s) => ({ id: s, label: s ? s.replaceAll("_", " ").toLowerCase() : "all" }))} />
        <QueryView query={q}>{(d) => <DataTable<ScreeningQueueItem> caption="Cases, oldest first" rows={d.items} rowKey={(r) => r.request_id} empty="Nothing is waiting." columns={columns} />}</QueryView>
        {open ? <Detail id={open} /> : null}
      </div>
    </>
  );
}
