"use client";

import type { RouteAttemptView, RouteDecisionView, RouteRelease, RoutingCapability } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, DateTime, KeyValues, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { CLAIM_GUARD, GATES, attemptActions, attemptHelp, capabilityGaps, capabilityNext, coverageRows, dataPathLine, decisionHeadline, evidenceLine, hopsOutside, parseHops, preflightHeadline, reasonLine, rejectionLines, releaseNext } from "./routing-logic";

const CAP_KEY = ["admin", "routing", "capabilities"];
const REL_KEY = ["admin", "routing", "releases"];

/** Coverage by tier, from cells only. Nothing here is typed in by hand. */
export function CoverageSection() {
  const [method, setMethod] = useState("");
  const cov = useCellQuery(["admin", "routing", "coverage", method], (api) => api.routing.coverage(method || undefined));
  return (
    <Card title="Coverage" description="Countries and document types counted from approved, current cells. A cell with missing or expired evidence is unknown and is not counted as available.">
      <div className="space-y-4">
        <Select
          label="Method"
          value={method}
          onChange={setMethod}
          options={[{ id: "", label: "All methods" }, { id: "DOCUMENT", label: "Document" }, { id: "DOCUMENT_FACE", label: "Document and face" }, { id: "NFC_CHIP", label: "Chip read" }, { id: "EID", label: "eID" }, { id: "REGISTRY", label: "Registry" }]}
        />
        <QueryView query={cov} loadingRows={3}>
          {(c) => (
            <div className="space-y-3">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-ink-muted">
                    <th className="py-1 pr-3">Status</th>
                    <th className="pr-3">Countries</th>
                    <th>Document types</th>
                  </tr>
                </thead>
                <tbody>
                  {coverageRows(c).map((r) => (
                    <tr key={r.tier} className="border-t border-line">
                      <td className="py-1 pr-3">{r.words}</td>
                      <td className="pr-3">{r.countries}</td>
                      <td>{r.specimens}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-sm text-ink-muted">
                {c.unknown_cells} {c.unknown_cells === 1 ? "cell is" : "cells are"} unknown because evidence is missing. As of <DateTime value={c.as_of} />.
              </p>
            </div>
          )}
        </QueryView>
      </div>
    </Card>
  );
}

/** Providers, their evidence and where they would process data. A second person approves. */
export function ProvidersSection() {
  const list = useCellQuery(CAP_KEY, (api) => api.routing.capabilities());
  const [allowed, setAllowed] = useState("uk");
  const approve = useCellMutation((api, c: RoutingCapability) => api.routing.approveCapability(c.provider_id, c.method, "Evidence reviewed"), { invalidate: [CAP_KEY], success: "Approved" });
  const suspend = useCellMutation((api, c: RoutingCapability) => api.routing.suspendCapability(c.provider_id, c.method, "Suspended by an operator"), { invalidate: [CAP_KEY], success: "Suspended" });
  const allowedList = allowed.split(",").map((x) => x.trim()).filter(Boolean);
  // eslint-disable-next-line react-hooks/purity -- reads the clock to judge expiry
  const now = new Date();
  const columns: Column<RoutingCapability>[] = [
    { key: "p", header: "Provider", pinned: true, render: (c) => <span className="font-medium">{c.provider_id}</span>, sortValue: (c) => c.provider_id },
    { key: "m", header: "Method", render: (c) => c.method.toLowerCase().replaceAll("_", " ") },
    { key: "s", header: "State", render: (c) => <StatusBadge state={c.state} kind="Provider capability" /> },
    { key: "path", header: "Data path", render: (c) => { const d = dataPathLine(c, allowedList); return <span className={d.ok ? "" : "text-danger"}>{d.text}</span>; } },
    { key: "gaps", header: "Missing or weak", render: (c) => capabilityGaps(c, now).join(" ") || "Nothing recorded as missing" },
    {
      key: "act",
      header: "Next",
      align: "end",
      render: (c) => {
        const n = capabilityNext(c, null);
        return (
          <div className="flex items-center justify-end gap-2">
            <span className="text-xs text-ink-muted">{n.why}</span>
            {n.approve ? <Button size="xs" variant="secondary" onPress={() => approve.mutate(c)}>Approve</Button> : null}
            {c.state === "APPROVED" ? <Button size="xs" variant="secondary" onPress={() => suspend.mutate(c)}>Suspend</Button> : null}
          </div>
        );
      },
    },
  ];
  return (
    <Card title="Providers and data paths" description="A provider is eligible only with qualification, contract and a signed legal determination of its whole data path, each dated. Hosting in a region is not the same as processing only there.">
      <div className="space-y-4">
        <TextField label="Regions you allow (comma separated)" value={allowed} onChange={setAllowed} />
        <QueryView query={list} loadingRows={3}>
          {(d) => <DataTable caption="Provider capabilities" columns={columns} rows={d.items} rowKey={(c) => `${c.provider_id}/${c.method}`} />}
        </QueryView>
        {(approve.error ?? suspend.error) ? <ApiErrorView error={(approve.error ?? suspend.error)!} /> : null}
        <DataFlowCard allowed={allowedList} providers={[...new Set((list.data?.items ?? []).map((c) => c.provider_id))]} />
      </div>
    </Card>
  );
}

/** A provider's registered data-flow graph. A change stops routing to it until its legal determination covers the new graph. */
function DataFlowCard({ allowed, providers }: { allowed: string[]; providers: string[] }) {
  const [provider, setProvider] = useState("");
  const [text, setText] = useState("");
  const flow = useCellQuery(["admin", "routing", "data-flow", provider], (api) => api.routing.dataFlow(provider), { enabled: provider !== "" });
  const parsed = parseHops(text);
  const save = useCellMutation((api) => api.routing.recordDataFlow(provider, parsed.hops), { invalidate: [["admin", "routing", "data-flow", provider]], success: "Recorded" });
  return (
    <div className="space-y-3 rounded border border-line p-3">
      <p className="text-sm font-medium">Data flow</p>
      <p className="text-sm text-ink-muted">Every place data enters, travels, rests, is reached by support, analysed or measured, with its region. Changing the graph stops routing to the provider until legal has covered the new one.</p>
      <Select label="Provider" value={provider} onChange={setProvider} options={[{ id: "", label: "Choose a provider" }, ...providers.map((p) => ({ id: p, label: p }))]} />
      {provider ? (
        <QueryView query={flow} loadingRows={2}>
          {(f) => (
            <div className="space-y-1 text-sm">
              <p className="font-mono text-xs break-all">
                Version {f.version}, {f.graph_digest}
              </p>
              <ul className="list-disc pl-5">
                {f.hops.map((h, i) => (
                  <li key={i} className={allowed.includes(h.region) ? "" : "text-danger"}>
                    {h.kind.toLowerCase().replaceAll("_", " ")}: {h.party}, {h.region}, {h.data_class.toLowerCase()}
                    {allowed.includes(h.region) ? "" : " (not an allowed region)"}
                  </li>
                ))}
              </ul>
              {hopsOutside(f, allowed).length ? <p className="text-danger">{hopsOutside(f, allowed).length} step(s) are outside the allowed regions, so this provider is not used.</p> : null}
            </div>
          )}
        </QueryView>
      ) : null}
      {provider ? (
        <>
          <TextField label="New version, one step per line: kind, party, region, data class" multiline rows={5} mono value={text} onChange={setText} placeholder={"ingress, provider, uk, media\nstorage, provider, uk, media"} errorMessage={text && parsed.problems.length ? parsed.problems[0] : undefined} />
          <Button isDisabled={!text.trim() || parsed.problems.length > 0} pending={save.isPending} onPress={() => save.mutate(undefined)}>
            Record this version
          </Button>
          {save.data ? <p className="text-sm text-ink-muted">{save.data.changed ? `Recorded as version ${save.data.version}. ${save.data.routing_effect ?? ""}` : "No change: that is the graph already on record."}</p> : null}
          {save.error ? <ApiErrorView error={save.error} /> : null}
        </>
      ) : null}
    </div>
  );
}

const SAMPLE = { product: "idv", purpose: "kyc", issuer_country: "GB", method: "DOCUMENT_FACE", channel: "web", allowed_regions: ["uk"], workflow_id: "default", workflow_version: "1" } as const;

/** What a proposed capability change would do, before anyone ships it. Nothing is stored and no provider is called. */
export function SimulationSection() {
  const [country, setCountry] = useState("GB");
  const [channel, setChannel] = useState("web");
  const [required, setRequired] = useState("yes");
  const [dropChannel, setDropChannel] = useState("");
  const [provider, setProvider] = useState("");
  const caps = useCellQuery(CAP_KEY, (api) => api.routing.capabilities());
  const run = useCellMutation((api) => {
    const proposed = provider && dropChannel ? [{ provider_id: provider.split("/")[0], method: provider.split("/")[1], manifest: { ...(caps.data?.items.find((c) => `${c.provider_id}/${c.method}` === provider) ?? {}), channels: (caps.data?.items.find((c) => `${c.provider_id}/${c.method}` === provider)?.channels ?? []).filter((x) => x !== dropChannel) } }] : [];
    return api.routing.preflight([{ ...SAMPLE, allowed_regions: [...SAMPLE.allowed_regions], issuer_country: country, channel: channel as "web", required: required === "yes" }], proposed);
  });
  const h = run.data ? preflightHeadline(run.data) : null;
  return (
    <Card title="Simulation and change impact" description="Try a change on a group of applicants and see who loses coverage first. It reads registered data only: nothing is stored, no provider is called, no customer is told.">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Issuing country (two letters)" value={country} onChange={setCountry} />
          <Select label="Channel" value={channel} onChange={setChannel} options={[{ id: "web", label: "Web" }, { id: "ios", label: "iOS" }, { id: "android", label: "Android" }]} />
          <Select label="Is this group required?" value={required} onChange={setRequired} options={[{ id: "yes", label: "Yes: a loss blocks release" }, { id: "no", label: "No" }]} />
          <Select label="Change: provider" value={provider} onChange={setProvider} options={[{ id: "", label: "No change (baseline)" }, ...(caps.data?.items ?? []).map((c) => ({ id: `${c.provider_id}/${c.method}`, label: `${c.provider_id} (${c.method})` }))]} />
          <Select label="Change: it stops supporting" value={dropChannel} onChange={setDropChannel} options={[{ id: "", label: "Nothing" }, { id: "web", label: "Web" }, { id: "ios", label: "iOS" }, { id: "android", label: "Android" }]} />
        </div>
        <Button pending={run.isPending} onPress={() => run.mutate(undefined)}>Run the simulation</Button>
        {run.error ? <ApiErrorView error={run.error} /> : null}
        {run.data && h ? (
          <div className="space-y-3">
            <Banner tone={h.tone} title={h.text}>{run.data.note}</Banner>
            <ul className="space-y-1 text-sm">
              {run.data.rows.map((r, i) => (
                <li key={i}>
                  {r.scenario.issuer_country} / {r.scenario.channel}: {r.change.toLowerCase().replaceAll("_", " ")} (before {r.before ?? "no route"}, after {r.after ?? "no route"}){r.after_no_route_reasons.length ? `. ${r.after_no_route_reasons.map(reasonLine).join(" ")}` : ""}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function DecisionView({ d }: { d: RouteDecisionView }) {
  const h = decisionHeadline(d);
  return (
    <div className="space-y-4">
      <Banner tone={h.tone} title={h.text}>
        {d.no_route_reasons.length ? <ul className="list-disc pl-5">{d.no_route_reasons.map((r) => <li key={r}>{reasonLine(r)}</li>)}</ul> : d.caveat}
      </Banner>
      <KeyValues
        items={[
          ["Session", d.session_id],
          ["Route version", d.route_version],
          ["Resolver", d.algorithm_version],
          ["Recorded", <DateTime key="t" value={d.created_at} />],
          ["Decision digest", <span key="d" className="font-mono text-xs break-all">{d.decision_digest}</span>],
        ]}
      />
      {d.ranked.length ? (
        <div>
          <p className="mb-1 text-sm font-medium">Candidates that passed every hard gate, best first</p>
          <ol className="list-decimal pl-5 text-sm">
            {d.ranked.map((r) => <li key={r.provider_id}>{r.provider_id}: rank {r.rank}, p95 {r.p95_latency_ms} ms</li>)}
          </ol>
        </div>
      ) : null}
      {rejectionLines(d).length ? (
        <div>
          <p className="mb-1 text-sm font-medium">Rejected, and at which gate</p>
          <ul className="space-y-1 text-sm">
            {rejectionLines(d).map((r) => <li key={r.provider}><span className="font-mono text-xs">{r.provider}</span>: {r.gates.join("; ")}. {r.reasons.join(" ")}</li>)}
          </ul>
        </div>
      ) : null}
      {d.lineage ? <p className="text-sm text-ink-muted">Re-routed after {d.lineage.failed_provider} failed before anything was sent (decision {d.lineage.previous_decision.slice(0, 8)}).</p> : null}
      {d.notes.length ? <p className="text-sm text-ink-muted">{d.notes.join(" ")}</p> : null}
    </div>
  );
}

function AttemptView({ a }: { a: RouteAttemptView }) {
  const [reason, setReason] = useState("");
  const adv = useCellMutation((api, target: string) => api.routing.advance(a.attempt_id, { target, reason: reason || undefined }), { invalidate: [["admin", "routing", "attempt", a.attempt_id]], success: "Recorded" });
  return (
    <div className="space-y-2 rounded border border-line p-3">
      <p className="text-sm"><span className="font-medium">{a.provider_id}</span>: {attemptHelp(a.state)}</p>
      <ul className="text-xs text-ink-muted">
        {a.history.map((h) => <li key={h.seq}>{h.seq}. {h.from ?? "start"} to {h.to} by {h.actor.toLowerCase()}{h.reason ? ` (${h.reason})` : ""}</li>)}
      </ul>
      {attemptActions(a).length ? (
        <div className="flex flex-wrap items-end gap-2">
          <TextField label="Reason" value={reason} onChange={setReason} />
          {attemptActions(a).map((x) => <Button key={x.target} size="sm" variant="secondary" pending={adv.isPending} onPress={() => adv.mutate(x.target)}>{x.label}</Button>)}
        </div>
      ) : null}
      <div className="rounded bg-surface-sunken p-2 text-sm">
        <p className="font-medium">What the applicant is told: {a.applicant.title}</p>
        <p className="text-ink-muted">{a.applicant.body}</p>
        <p className="text-xs text-ink-muted">Next step for them: {a.applicant.next.toLowerCase().replaceAll("_", " ")}. {a.applicant.keeps_evidence ? "Their evidence is kept." : "They are asked to capture again."}</p>
      </div>
      <p className="text-xs text-ink-muted">{a.status_note}</p>
      {adv.error ? <ApiErrorView error={adv.error} /> : null}
    </div>
  );
}

/** One route decision from its recorded inputs, with every excluded candidate, and the attempts made under it. */
export function TraceSection() {
  const [id, setId] = useState("");
  const [asked, setAsked] = useState("");
  const q = useCellQuery(["admin", "routing", "decision", asked], (api) => api.routing.decision(asked), { enabled: asked !== "" });
  return (
    <Card title="Route trace and recovery" description="Why a provider was chosen and why every other one was not, from the recorded inputs. Routing selected, provider submitted, result pending and the final outcome are four different things.">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <TextField label="Route decision ID" value={id} onChange={setId} mono />
          <Button isDisabled={!id.trim()} onPress={() => setAsked(id.trim())}>Show</Button>
        </div>
        {asked ? (
          <QueryView query={q} loadingRows={3}>
            {(d) => (
              <div className="space-y-4">
                <DecisionView d={d} />
                {d.evidence && d.evidence.length ? (
                  <div className="text-sm">
                    <p className="mb-1 font-medium">Recorded in the evidence ledger</p>
                    <ul className="list-disc pl-5 text-ink-muted">
                      {d.evidence.map((e, i) => (
                        <li key={i}>
                          {evidenceLine(e)} at <DateTime value={e.occurred_at} />
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <p className="text-sm text-ink-muted">No evidence record was found for this decision.</p>
                )}
                {(d.attempts ?? []).map((a) => <AttemptLoader key={a.attempt_id} id={a.attempt_id} />)}
              </div>
            )}
          </QueryView>
        ) : null}
      </div>
    </Card>
  );
}

function AttemptLoader({ id }: { id: string }) {
  const q = useCellQuery(["admin", "routing", "attempt", id], (api) => api.routing.attempt(id));
  return <QueryView query={q} loadingRows={1}>{(a) => <AttemptView a={a} />}</QueryView>;
}

/** Route releases: preflight first, independent approvals of the exact dependency set, then live; a rollback never changes a recorded decision. */
export function ReleasesSection() {
  const list = useCellQuery(REL_KEY, (api) => api.routing.releases());
  const [risk, setRisk] = useState("HIGH");
  const create = useCellMutation((api) => api.routing.createRelease({ risk: risk as "LOW" | "HIGH" }), { invalidate: [REL_KEY], success: "Draft created" });
  const approve = useCellMutation((api, v: { id: string; role: string }) => api.routing.approveRelease(v.id, v.role), { invalidate: [REL_KEY], success: "Approved" });
  const publish = useCellMutation((api, id: string) => api.routing.publishRelease(id), { invalidate: [REL_KEY], success: "Published" });
  const rollback = useCellMutation((api, id: string) => api.routing.rollbackRelease(id, "Rolled back by an operator"), { invalidate: [REL_KEY], success: "Rolled back" });
  const [role, setRole] = useState("PRODUCT");
  const columns: Column<RouteRelease>[] = [
    { key: "id", header: "Release", pinned: true, render: (r) => <span className="font-mono text-xs">{r.release_id.slice(0, 8)}</span> },
    { key: "s", header: "State", render: (r) => <StatusBadge state={r.state} kind="Route release" /> },
    { key: "risk", header: "Risk", render: (r) => r.risk.toLowerCase() },
    { key: "ap", header: "Approved by", render: (r) => r.approvals.map((a) => a.role.toLowerCase()).join(", ") || "nobody yet" },
    { key: "at", header: "Recorded", render: (r) => <DateTime value={r.recorded_at} /> },
    {
      key: "act",
      header: "Next",
      align: "end",
      render: (r) => {
        const n = releaseNext(r, null);
        return (
          <div className="flex items-center justify-end gap-2">
            <span className="text-xs text-ink-muted">{n.why}</span>
            {n.approve ? <Button size="xs" variant="secondary" onPress={() => approve.mutate({ id: r.release_id, role })}>Approve as {role.toLowerCase()}</Button> : null}
            {n.publish ? <Button size="xs" onPress={() => publish.mutate(r.release_id)}>Publish</Button> : null}
            {r.state === "LIVE" ? <Button size="xs" variant="secondary" onPress={() => rollback.mutate(r.release_id)}>Roll back</Button> : null}
          </div>
        );
      },
    },
  ];
  return (
    <Card title="Route releases" description="A release pins the exact set of providers it was approved with. If one changes, the approvals no longer apply. People who made a release cannot approve or publish it.">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Risk" value={risk} onChange={setRisk} options={[{ id: "LOW", label: "Low: product and security" }, { id: "HIGH", label: "High: product, security, privacy and legal" }]} />
          <Button pending={create.isPending} onPress={() => create.mutate(undefined)}>Draft a release</Button>
          <Select label="Sign as" value={role} onChange={setRole} options={["PRODUCT", "SECURITY", "PRIVACY", "LEGAL"].map((x) => ({ id: x, label: x.toLowerCase() }))} />
        </div>
        <p className="text-sm text-ink-muted">Run the simulation first; a release whose required groups lose coverage does not advance.</p>
        <QueryView query={list} loadingRows={3}>
          {(d) => <DataTable caption="Route releases" columns={columns} rows={d.items} rowKey={(r) => r.release_id} />}
        </QueryView>
        {(create.error ?? approve.error ?? publish.error ?? rollback.error) ? <ApiErrorView error={(create.error ?? approve.error ?? publish.error ?? rollback.error)!} /> : null}
      </div>
    </Card>
  );
}

/** The public number, and the provider invoice matched to the attempt ledger. */
export function ClaimsCostSection() {
  const claim = useCellQuery(["admin", "routing", "claim"], (api) => api.routing.claims(), { });
  return (
    <div className="space-y-5">
      <Card title="Public coverage number" description={CLAIM_GUARD}>
        <QueryView query={claim} loadingRows={2}>
          {(c) => (
            <div className="space-y-2">
              <p className="text-2xl font-semibold">{c.countries} countries, {c.specimens} document types</p>
              <p className="text-sm text-ink-muted">{c.basis}. As of <DateTime value={c.as_of} />. {c.caveat}</p>
            </div>
          )}
        </QueryView>
      </Card>
      <ReconcileCard />
    </div>
  );
}

function ReconcileCard() {
  const [text, setText] = useState("");
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => l.split(",").map((x) => x.trim()));
  const valid = lines.length > 0 && lines.every((p) => p.length === 4 && /^\d+$/.test(p[2] ?? "") && /^[A-Z]{3}$/.test(p[3] ?? ""));
  const run = useCellMutation((api) => api.routing.reconcile(lines.map((p) => ({ provider_id: p[0] ?? "", external_operation_id: p[1] ?? "", amount_minor: Number(p[2]), currency: p[3] ?? "" }))));
  return (
    <Card title="Provider invoice against the attempt ledger" description="Paste the provider's lines as provider, operation id, amount in minor units, currency. Anything that does not match is held for a person. Nothing is adjusted automatically.">
      <div className="space-y-3">
        <TextField label="Invoice lines" multiline rows={5} mono value={text} onChange={setText} placeholder="provider-a, op-123, 100, EUR" />
        <Button isDisabled={!valid} pending={run.isPending} onPress={() => run.mutate(undefined)}>Match</Button>
        {run.error ? <ApiErrorView error={run.error} /> : null}
        {run.data ? (
          <div className="space-y-1 text-sm">
            <p>{run.data.matched} matched. {run.data.held.length} held. {run.data.attempts_without_vendor_line.length} attempts have no invoice line.</p>
            <ul className="list-disc pl-5">{run.data.held.map((h) => <li key={h.operation + h.reason}>{h.operation}: {h.reason.toLowerCase().replaceAll("_", " ")}</li>)}</ul>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export const GATE_LIST = Object.entries(GATES);
