"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import type { VerifyAnalytics as VA } from "@zoiko/api-client";
import { Banner, Button, Card, Code, TextField } from "@zoiko/ui";
import Link from "next/link";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { CAUSE, shown, windowParams, type WindowState } from "./logic";
import { denied, Figures, Freshness, Tile, Tiles, Withheld } from "./parts";

type Props = { w: WindowState };
const SESSION = ["M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08", "M09", "M10"];

function useQ(w: WindowState, metric_ids: string[], extra: Partial<VA.QueryIn> = {}) {
  const body: VA.QueryIn = { ...windowParams(w), metric_ids, ...extra };
  return useCellQuery(["verify-analytics", "query", body], (api) => api.verifyAnalytics.query(body));
}

function pick(r: VA.QueryResult, id: string): VA.Governed | undefined {
  return r.results.find((g) => g.metric_id === id);
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <h2 className="mb-3 text-base font-semibold text-ink">{title}</h2>
      {children}
    </Card>
  );
}

function Loaded<T>({ q, children }: { q: UseQueryResult<T>; children: (d: T) => React.ReactNode }) {
  if (q.isError && denied(q.error)) return <Withheld error={q.error} />;
  return <QueryView query={q}>{children}</QueryView>;
}

/** WF-02.01 */
export function OverviewView({ w }: Props) {
  const q = useCellQuery(["verify-analytics", "overview", w], (api) => api.verifyAnalytics.overview(windowParams(w)));
  return (
    <Loaded q={q}>
      {(o) => (
        <div className="space-y-5">
          <Freshness quality={o.quality} sources={o.freshness} scope={o.scope} />
          <Tiles>
            {o.tiles.map((g) => (
              <Tile key={g.metric_id} g={g} dataLabel={o.scope.data_label} />
            ))}
          </Tiles>
          <div className="grid gap-5 lg:grid-cols-2">
            <Section title="Where sessions stand now">
              <Figures caption="Sessions by current state" rows={o.decisions} labelKey="state" labelTitle="State" valueKey="count" unit="count" />
              <p className="mt-2 text-xs text-ink-muted">A review decision is never counted as approved, and the business's own account decision is not a Zoiko state.</p>
            </Section>
            <Section title="Review timeliness">
              <Tile g={o.review} dataLabel={o.scope.data_label} hint={`${String(o.review.open_reviews ?? 0)} open reviews`} />
            </Section>
          </div>
          <p className="text-xs text-ink-muted">
            Reference <Code>{o.query_fingerprint.slice(0, 16)}</Code>. Cases are opened in <Link className="underline" href="/admin/idv-cases">Verification cases</Link>.
          </p>
        </div>
      )}
    </Loaded>
  );
}

/** WF-02.02 */
export function ConversionView({ w }: Props) {
  const [cohort, setCohort] = useState<"created" | "started">("created");
  const q = useQ(w, ["M02", "M03", "M04"], { cohort, dimension: "method" });
  return (
    <Loaded q={q}>
      {(r) => (
        <div className="space-y-5">
          <Freshness quality={r.quality} sources={[]} scope={r.scope} />
          <fieldset className="flex gap-4 text-sm">
            <legend className="sr-only">Funnel population</legend>
            {(["created", "started"] as const).map((c) => (
              <label key={c} className="flex items-center gap-1">
                <input type="radio" name="cohort" checked={cohort === c} onChange={() => setCohort(c)} /> {c === "created" ? "Every session created" : "Sessions that began a capture"}
              </label>
            ))}
          </fieldset>
          <Tiles>
            {["M02", "M03", "M04"].map((id) => (pick(r, id) ? <Tile key={id} g={pick(r, id)!} dataLabel={r.scope.data_label} /> : null))}
          </Tiles>
          <Section title="Funnel (matured sessions only)">
            <Figures caption="Share of the cohort reaching each step" rows={r.funnel.steps} labelKey="step" labelTitle="Step" countKey="count" />
          </Section>
          <Section title="Where sessions stopped">
            <Figures caption="Observed end of sessions that reached no decision" rows={r.funnel.drop_off} labelKey="reason" labelTitle="Observed end" valueKey="count" unit="count" />
            <p className="mt-2 text-xs text-ink-muted">Only what events show is classified. Missing instrumentation is Unknown, never abandonment; why a person left is not inferred.</p>
          </Section>
          <Section title="Submission rate by method">
            <Figures caption="Submission rate by capture method" rows={r.breakdowns.M03 ?? []} labelKey="method" labelTitle="Method" countKey="denominator" />
          </Section>
        </div>
      )}
    </Loaded>
  );
}

/** WF-02.03 */
export function FirstAttemptView({ w }: Props) {
  const [dim, setDim] = useState<"method" | "market">("method");
  const q = useQ(w, ["M06"], { dimension: dim });
  return (
    <Loaded q={q}>
      {(r) => (
        <div className="space-y-5">
          <Freshness quality={r.quality} sources={[]} scope={r.scope} />
          <Tiles>{pick(r, "M06") ? <Tile g={pick(r, "M06")!} dataLabel={r.scope.data_label} hint="Measured Zoiko figure. No target is approved and no vendor benchmark is shown as ours." /> : null}</Tiles>
          <Section title="First-attempt completion by cohort">
            <label className="mb-2 block text-sm">
              Break down by{" "}
              <select className="rounded border border-border px-2 py-1" value={dim} onChange={(e) => setDim(e.target.value as "method" | "market")}>
                <option value="method">Capture method</option>
                <option value="market">Market</option>
              </select>
            </label>
            <Figures caption={`First-attempt completion by ${dim}`} rows={r.breakdowns.M06 ?? []} labelKey={dim} labelTitle={dim === "method" ? "Method" : "Market"} countKey="denominator" />
          </Section>
          <Section title="Why first attempts were not accepted">
            <Figures caption="Recorded result of first attempts that were not accepted" rows={r.first_attempt_causes.map((c) => ({ ...c, cause: CAUSE[String(c.cause)] ?? String(c.cause) }))}
              labelKey="cause" labelTitle="Cause" valueKey="count" unit="count" />
            <p className="mt-2 text-xs text-ink-muted">Device, browser and accessibility-mode cohorts are not shown: those signals are not collected, and no protected trait is inferred.</p>
          </Section>
        </div>
      )}
    </Loaded>
  );
}

/** WF-02.04 */
export function DecisionsView({ w }: Props) {
  const q = useQ(w, ["M04", "M05", "M07", "M08", "M09"]);
  return (
    <Loaded q={q}>
      {(r) => (
        <div className="space-y-5">
          <Freshness quality={r.quality} sources={[]} scope={r.scope} />
          <Tiles>
            {["M04", "M05", "M07", "M09"].map((id) => (pick(r, id) ? <Tile key={id} g={pick(r, id)!} dataLabel={r.scope.data_label} /> : null))}
          </Tiles>
          <Tiles>{pick(r, "M08") ? <Tile g={pick(r, "M08")!} dataLabel={r.scope.data_label} hint="From the notice shown to the final decision; reviewed cases included." /> : null}</Tiles>
          <Banner tone="info" title="Reviewing a case">
            Reviews, rechecks and decisions stay in <Link className="underline" href="/admin/idv-cases">Verification cases</Link>, under their own approvals. Nothing here changes a decision.
          </Banner>
        </div>
      )}
    </Loaded>
  );
}

/** WF-02.05 */
export function RiskView({ w }: Props) {
  const q = useQ(w, ["M10"]);
  return (
    <Loaded q={q}>
      {(r) => {
        const g = pick(r, "M10");
        const series = (g?.series ?? {}) as Record<string, number | null>;
        return (
          <div className="space-y-5">
            <Freshness quality={r.quality} sources={[]} scope={r.scope} />
            <Section title="Signals and findings, kept apart">
              <Figures caption="Counts in this window" unit="count" valueKey="count" labelKey="series" labelTitle="Series"
                rows={[
                  { series: "Capture risk signals (unreviewed)", count: series.risk_signals ?? null, suppressed: series.risk_signals === null },
                  { series: "Reviewer-confirmed findings", count: series.confirmed_findings ?? null, suppressed: series.confirmed_findings === null },
                  { series: "Overturned or superseded decisions", count: series.overturned ?? null, suppressed: series.overturned === null },
                ]} />
              <p className="mt-2 text-xs text-ink-muted">A signal is not fraud, and nothing here claims fraud was prevented. No cross-company link is shown.</p>
            </Section>
          </div>
        );
      }}
    </Loaded>
  );
}

/** WF-02.06 */
export function ProvidersView({ w }: Props) {
  const q = useQ(w, ["M11"]);
  const cov = useCellQuery(["verify-analytics", "coverage"], (api) => api.verifyAnalytics.coverage());
  return (
    <div className="space-y-5">
      <Loaded q={q}>
        {(r) => {
          const g = pick(r, "M11");
          return (
            <>
              <Freshness quality={r.quality} sources={[]} scope={r.scope} />
              <Tiles>{g ? <Tile g={g} dataLabel={r.scope.data_label} /> : null}</Tiles>
              <Section title="Measured service quality">
                <Figures caption="Technical error rate by provider" rows={(g?.providers as VA.Row[]) ?? []} labelKey="provider" labelTitle="Provider" countKey="denominator" />
                <p className="mt-2 text-xs text-ink-muted">Zoiko's own measurements, not vendor claims. Providers serve different methods and markets, so these rows are not a ranking.</p>
              </Section>
            </>
          );
        }}
      </Loaded>
      <Section title="Configured coverage">
        <QueryView query={cov}>
          {(c) => (
            <table className="w-full text-sm">
              <caption className="sr-only">Markets and their routes</caption>
              <thead><tr className="text-left text-ink-muted"><th scope="col">Market</th><th scope="col">Routes</th><th scope="col">Face comparison</th><th scope="col">Review SLA</th></tr></thead>
              <tbody>
                {c.markets.map((m) => (
                  <tr key={m.market} className="border-t border-border">
                    <th scope="row" className="py-1.5 text-left font-normal">{m.market}</th>
                    <td>{m.routes.map((x) => x.route).join(", ")}</td>
                    <td>{m.face_comparison_authorized ? "Authorized" : "Not authorized"}</td>
                    <td>{m.review_sla_hours ? `${m.review_sla_hours} h` : "Not set"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryView>
        <p className="mt-2 text-xs text-ink-muted">A market without a published policy is absent, never shown as available. Routing changes are made by Zoiko operators, not here.</p>
      </Section>
    </div>
  );
}

/** WF-02.07 */
export function EconomicsView({ w }: Props) {
  const q = useCellQuery(["verify-analytics", "economics", w], (api) => api.verifyAnalytics.economics(windowParams(w)));
  return (
    <Loaded q={q}>
      {(e) => (
        <div className="space-y-5">
          <Tiles>
            <div className="rounded-lg border border-border p-4">
              <p className="text-sm font-medium text-ink-muted">Approved subjects</p>
              <p className="mt-1 text-2xl font-semibold tabular">{e.approved_subjects === null ? "Withheld" : shown(e.approved_subjects, "count")}</p>
            </div>
            <div className="rounded-lg border border-border p-4">
              <p className="text-sm font-medium text-ink-muted">Billable verification units ({e.billable_units.month.slice(0, 7)})</p>
              <p className="mt-1 text-2xl font-semibold tabular">{e.billable_units.net ?? "No data"}</p>
              <p className="text-xs text-ink-muted">{e.billable_units.meter} · {e.billable_units.frozen ? "month closed" : "month open"}</p>
            </div>
          </Tiles>
          {e.finance_visible ? (
            <Section title="Cost and yield">
              {(e.M13 ?? []).map((g, i) => (
                <Tile key={i} g={{ ...g, name: `Cost per approved subject${g.currency ? ` (${g.currency})` : ""}` }} hint={g.note} />
              ))}
              {e.M14 ? <div className="mt-3"><Tile g={e.M14} hint={e.M14.note} /></div> : null}
              <p className="mt-2 text-xs text-ink-muted">Supplier cost is never the invoice price. Amounts stay in their own currency until a rate source is approved.</p>
            </Section>
          ) : (
            <Banner tone="info" title="Cost and price need billing access">Quantities are shown. An Owner can give billing access under Billing.</Banner>
          )}
          <p className="text-xs text-ink-muted">Invoices, credits and payment are in <Link className="underline" href="/admin/billing">Billing</Link>; supplier spend in <Link className="underline" href="/admin/verification-spend">Verification spend</Link>.</p>
        </div>
      )}
    </Loaded>
  );
}

/** WF-02.08 */
export function DeliveryView({ w }: Props) {
  const q = useQ(w, ["M12"]);
  const ops = useCellQuery(["verify-analytics", "operations"], (api) => api.verifyAnalytics.operations(7));
  return (
    <div className="space-y-5">
      <Loaded q={q}>
        {(r) => {
          const g = pick(r, "M12");
          return g ? (
            <Tiles>
              <Tile g={g} dataLabel={r.scope.data_label} hint="Delivered means the recipient answered 2xx; queued or attempted is not delivered." />
              <Tile g={{ ...g, metric_id: "M12d", name: "Delivery delay", unit: "ms", value: (g.delay_p50_ms as number | null) ?? null, p95: (g.delay_p95_ms as number | null) ?? null }} />
              <div className="rounded-lg border border-border p-4 text-sm">
                <p className="font-medium text-ink-muted">Dead-lettered</p>
                <p className="mt-1 text-2xl font-semibold tabular">{g.dead_lettered === null ? "Withheld" : String(g.dead_lettered ?? 0)}</p>
              </div>
            </Tiles>
          ) : null;
        }}
      </Loaded>
      <Loaded q={ops}>
        {(o) => <Tiles><Tile g={o.availability} hint={`Last ${o.availability.window_days} days; client errors (4xx) are not counted against availability.`} /></Tiles>}
      </Loaded>
      <p className="text-xs text-ink-muted">Endpoints, the delivery log and replay are in <Link className="underline" href="/admin/developers">Developers</Link>.</p>
    </div>
  );
}

/** WF-02.09 */
export function IncidentsView() {
  const q = useCellQuery(["verify-analytics", "operations"], (api) => api.verifyAnalytics.operations(7));
  return (
    <Loaded q={q}>
      {(o) => (
        <div className="space-y-5">
          <Freshness quality={o.freshness.quality} sources={o.freshness.sources} />
          <Section title="Incidents">
            {o.incidents.length ? (
              <ul className="space-y-3 text-sm">
                {o.incidents.map((i) => (
                  <li key={i.incident_id} className="border-t border-border pt-2">
                    <p className="font-medium">{i.title} · {i.severity} · {i.status.replaceAll("_", " ").toLowerCase()}</p>
                    <p className="text-xs text-ink-muted">Incident <Code>{i.incident_id.slice(0, 8)}</Code> · declared {new Date(i.declared_at).toLocaleString()}{i.resolved_at ? ` · resolved ${new Date(i.resolved_at).toLocaleString()}` : ""}</p>
                    {i.updates.map((u) => <p key={u.at} className="mt-1">{u.note}</p>)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-muted">No incidents recorded. This is not a claim of 100% availability.</p>
            )}
          </Section>
          <Section title="Service level objectives">
            <table className="w-full text-sm">
              <caption className="sr-only">Objectives by service</caption>
              <thead><tr className="text-left text-ink-muted"><th scope="col">Service</th><th scope="col">Owner</th><th scope="col">Objectives</th></tr></thead>
              <tbody>
                {o.slos.map((s) => (
                  <tr key={s.service_id} className="border-t border-border">
                    <th scope="row" className="py-1.5 text-left font-normal">{s.name}</th>
                    <td>{s.owner}</td>
                    <td>{s.slos.map((x) => `${x.sli} ${(x.target * 100).toFixed(2)}%`).join("; ") || "None defined"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-ink-muted">Targets are proposed objectives, not measured achievement.</p>
          </Section>
        </div>
      )}
    </Loaded>
  );
}

/** WF-02.10 */
export function ReportsView({ w }: Props) {
  const [purpose, setPurpose] = useState("");
  const [created, setCreated] = useState<VA.ExportCreated | null>(null);
  const list = useCellQuery(["verify-analytics", "exports"], (api) => api.verifyAnalytics.exports());
  const freeze = useCellMutation(
    async (api, fmt: "csv" | "json") => {
      const snap = await api.verifyAnalytics.query({ ...windowParams(w), metric_ids: SESSION, snapshot: true });
      return api.verifyAnalytics.createExport({ snapshot_id: snap.snapshot_id ?? "", format: fmt, purpose });
    },
    { invalidate: [["verify-analytics", "exports"]], onSuccess: (r) => setCreated(r) },
  );
  const revoke = useCellMutation((api, id: string) => api.verifyAnalytics.revokeExport(id), { invalidate: [["verify-analytics", "exports"]], success: "Export revoked" });
  async function download(c: VA.ExportCreated) {
    const res = await fetch(`/api/bff/v1/verify-analytics/exports/${encodeURIComponent(c.export_id)}/download`, { headers: { "X-Download-Token": c.download_token } });
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `command-center-${c.export_id.slice(0, 8)}.${c.format}`;
    a.click();
    URL.revokeObjectURL(url);
    setCreated(null);
    void list.refetch();
  }
  return (
    <div className="space-y-5">
      <Section title="Export the current figures">
        <p className="mb-3 text-sm text-ink-muted">The figures are frozen with their definition versions and watermark, so the file always matches what was shown. Withheld groups stay withheld. A file can be downloaded once, within 24 hours.</p>
        <TextField label="Purpose" value={purpose} onChange={setPurpose} description="Why this export is needed; it is recorded." />
        <div className="mt-3 flex gap-2">
          <Button isDisabled={purpose.trim().length < 3 || freeze.isPending} onPress={() => freeze.mutate("csv")}>Create CSV</Button>
          <Button variant="secondary" isDisabled={purpose.trim().length < 3 || freeze.isPending} onPress={() => freeze.mutate("json")}>Create JSON</Button>
        </div>
        {freeze.isError ? <Withheld error={freeze.error} /> : null}
        {created ? (
          <Banner tone="ok" title="Export ready">
            <Button onPress={() => void download(created)}>Download now</Button> The link works once and expires {new Date(created.expires_at).toLocaleString()}.
          </Banner>
        ) : null}
      </Section>
      <Section title="My exports">
        <Loaded q={list}>
          {(l) => (
            <table className="w-full text-sm">
              <caption className="sr-only">Exports I created</caption>
              <thead><tr className="text-left text-ink-muted"><th scope="col">Created</th><th scope="col">Purpose</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Action</span></th></tr></thead>
              <tbody>
                {l.items.map((x) => (
                  <tr key={x.export_id} className="border-t border-border">
                    <td className="py-1.5">{new Date(x.created_at).toLocaleString()}</td>
                    <td>{x.purpose}</td>
                    <td>{x.status.toLowerCase()}</td>
                    <td>{x.status === "READY" ? <Button size="sm" variant="secondary" onPress={() => revoke.mutate(x.export_id)}>Revoke</Button> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Loaded>
      </Section>
    </div>
  );
}

/** WF-02.11 */
export function DefinitionsView() {
  const defs = useCellQuery(["verify-analytics", "definitions"], (api) => api.verifyAnalytics.definitions());
  const quality = useCellQuery(["verify-analytics", "quality"], (api) => api.verifyAnalytics.quality());
  const corr = useCellQuery(["verify-analytics", "corrections"], (api) => api.verifyAnalytics.corrections());
  const [eventId, setEventId] = useState("");
  const [reason, setReason] = useState("");
  const ask = useCellMutation((api, b: { event_id: string; reason: string }) => api.verifyAnalytics.requestCorrection(b), { invalidate: [["verify-analytics", "corrections"]], success: "Correction requested" });
  const approve = useCellMutation((api, id: string) => api.verifyAnalytics.approveCorrection(id), { invalidate: [["verify-analytics", "corrections"]], success: "Correction approved" });
  return (
    <div className="space-y-5">
      <Loaded q={quality}>{(qd) => <Freshness quality={qd.quality} sources={qd.sources} />}</Loaded>
      <Section title="Metric definitions">
        <Loaded q={defs}>
          {(d) => (
            <table className="w-full text-sm">
              <caption className="sr-only">Versioned metric definitions</caption>
              <thead><tr className="text-left text-ink-muted"><th scope="col">Metric</th><th scope="col">Formula</th><th scope="col">Exclusions</th><th scope="col">Owner · version · status</th></tr></thead>
              <tbody>
                {d.metrics.map((m) => (
                  <tr key={m.metric_id} className="border-t border-border align-top">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal">{m.metric_id} {m.name}</th>
                    <td className="pr-3">{m.numerator}{m.denominator !== "-" ? ` ÷ ${m.denominator}` : ""}</td>
                    <td className="pr-3">{m.exclusions.join("; ")}</td>
                    <td>{m.owner} · v{m.version} · {m.status.toLowerCase()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Loaded>
      </Section>
      <Section title="Corrections">
        <p className="mb-3 text-sm text-ink-muted">A fact is left out of the metrics only after a second person approves. Exported snapshots never change.</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Fact id" value={eventId} onChange={setEventId} />
          <TextField label="Reason" value={reason} onChange={setReason} />
        </div>
        <div className="mt-3"><Button isDisabled={eventId.length !== 36 || reason.length < 10} onPress={() => ask.mutate({ event_id: eventId, reason })}>Request correction</Button></div>
        {ask.isError ? <Withheld error={ask.error} /> : null}
        <Loaded q={corr}>
          {(c) => (
            <ul className="mt-3 space-y-2 text-sm">
              {c.items.map((x) => (
                <li key={x.correction_id} className="border-t border-border pt-2">
                  <Code>{x.event_id.slice(0, 8)}</Code> {x.reason} · {x.approved_by ? "approved" : "waiting for approval"}{" "}
                  {!x.approved_by ? <Button size="sm" variant="secondary" onPress={() => approve.mutate(x.correction_id)}>Approve</Button> : null}
                </li>
              ))}
            </ul>
          )}
        </Loaded>
      </Section>
    </div>
  );
}

/** WF-02.12 */
export function AccessView() {
  const me = useCellQuery(["verify-analytics", "access", "me"], (api) => api.verifyAnalytics.access());
  const owner = (me.data?.permissions ?? []).includes("analytics.access.delegate");
  const grants = useCellQuery(["verify-analytics", "grants"], (api) => api.verifyAnalytics.grants(), { enabled: owner });
  const [grantee, setGrantee] = useState("");
  const [bundle, setBundle] = useState("ANALYTICS_VIEWER");
  const give = useCellMutation((api, b: { grantee_principal_id: string; bundle: string }) => api.verifyAnalytics.give(b), { invalidate: [["verify-analytics", "grants"]], success: "Access given" });
  const take = useCellMutation((api, id: string) => api.verifyAnalytics.takeAway(id), { invalidate: [["verify-analytics", "grants"]], success: "Access taken away" });
  return (
    <div className="space-y-5">
      <Section title="My analytics access">
        <QueryView query={me}>
          {(a) => (
            <ul className="list-disc ps-5 text-sm">
              {a.permissions.length ? a.permissions.map((p) => <li key={p}>{a.catalogue[p] ?? p}</li>) : <li>None</li>}
              <li>{a.finance ? "Cost and price: visible (billing access)" : "Cost and price: not visible (needs billing access)"}</li>
            </ul>
          )}
        </QueryView>
        <p className="mt-2 text-xs text-ink-muted">
          Figures for groups smaller than the minimum are withheld, and so is the next smallest group when only one would be, so a total cannot reveal one person. The analytics store holds pseudonymous ids and codes only; every drill, export and access change is in the <Link className="underline" href="/admin/evidence">audit record</Link>.
        </p>
      </Section>
      {owner ? (
        <Section title="Who has analytics access">
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label="Member principal id" value={grantee} onChange={setGrantee} />
            <label className="text-sm">
              Bundle
              <select className="mt-1 block w-full rounded border border-border px-2 py-1" value={bundle} onChange={(e) => setBundle(e.target.value)}>
                {Object.keys(me.data?.bundles ?? {}).map((b) => <option key={b} value={b}>{b.replaceAll("_", " ").toLowerCase()}</option>)}
              </select>
            </label>
          </div>
          <div className="mt-3"><Button isDisabled={grantee.length !== 36} onPress={() => give.mutate({ grantee_principal_id: grantee, bundle })}>Give access</Button></div>
          {give.isError ? <Withheld error={give.error} /> : null}
          <Loaded q={grants}>
            {(g) => (
              <ul className="mt-3 space-y-2 text-sm">
                {g.items.map((x) => (
                  <li key={x.grant_id} className="border-t border-border pt-2">
                    <Code>{x.grantee.slice(0, 8)}</Code> {x.bundle.replaceAll("_", " ").toLowerCase()}{" "}
                    <Button size="sm" variant="secondary" onPress={() => take.mutate(x.grant_id)}>Take away</Button>
                  </li>
                ))}
              </ul>
            )}
          </Loaded>
        </Section>
      ) : null}
    </div>
  );
}
