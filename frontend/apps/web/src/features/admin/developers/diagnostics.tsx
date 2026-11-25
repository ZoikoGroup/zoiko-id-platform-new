"use client";

import { Banner, Button, Card, DateTime, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { scopeChangeState, scopeWords, suiteHeadline, verdictWords } from "./developer-logic";

function useApps() {
  const apps = useCellQuery(["developers", "applications"], (api) => api.verifyApi.applications());
  const [id, setId] = useState("");
  const list = apps.data?.items ?? [];
  return { apps, list, chosen: id || list[0]?.application_id || "", setId };
}

function AppPicker({ p }: { p: ReturnType<typeof useApps> }) {
  return (
    <QueryView query={p.apps} loadingRows={1}>
      {(d) => <Select label="Application" value={p.chosen} onChange={p.setId} options={d.items.map((a) => ({ id: a.application_id, label: `${a.name} (${a.environment.toLowerCase()})` }))} />}
    </QueryView>
  );
}

/** Each endpoint's health from its newest deliveries, with what to do. */
export function EndpointHealthSection() {
  const q = useCellQuery(["developers", "endpoint-health"], (api) => api.verifyApi.endpointHealth());
  return (
    <Card title="Webhook endpoint health" description="Read from each endpoint's newest deliveries. It names the main problem and what to do about it.">
      <QueryView query={q} loadingRows={2}>
        {(d) => (
          <div className="space-y-3">
            {d.items.length === 0 ? <p className="text-sm text-ink-muted">No endpoint has had a delivery yet.</p> : null}
            {d.items.map((e) => (
              <div key={e.endpoint_id} className="rounded border border-line p-3 text-sm">
                <p className="font-medium">{e.host}: {verdictWords(e.verdict)}</p>
                <p className="text-ink-muted">{e.advice}</p>
                <p className="text-xs text-ink-muted">
                  {Object.entries(e.counts).filter(([, n]) => n > 0).map(([k, n]) => `${k.toLowerCase().replaceAll("_", " ")}: ${n}`).join(", ") || "no attempts"}. Signature confirmations from your server: {e.partner_confirmed_signature_events}.
                </p>
              </div>
            ))}
            <p className="text-xs text-ink-muted">{d.note}</p>
          </div>
        )}
      </QueryView>
    </Card>
  );
}

/** What this application's server called. Route templates, status and error codes only. */
export function RequestExplorerSection() {
  const p = useApps();
  const [status, setStatus] = useState("");
  const q = useCellQuery(["developers", "requests", p.chosen, status], (api) => api.verifyApi.requestLog(p.chosen, { status: status ? Number(status) : undefined, limit: 50 }), { enabled: p.chosen !== "" });
  return (
    <Card title="Request explorer" description="What your server called, newest first. Only the route, status, error code and trace id are kept: no body, no headers, no person data.">
      <div className="space-y-4">
        <AppPicker p={p} />
        <TextField label="Only this status (for example 429)" value={status} onChange={setStatus} />
        {p.chosen ? (
          <QueryView query={q} loadingRows={4}>
            {(d) => (
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-ink-muted">
                    <th className="py-1 pr-3">When</th>
                    <th className="pr-3">Call</th>
                    <th className="pr-3">Status</th>
                    <th className="pr-3">Error</th>
                    <th>Trace</th>
                  </tr>
                </thead>
                <tbody>
                  {d.items.map((r) => (
                    <tr key={r.trace_id + r.occurred_at} className="border-t border-line">
                      <td className="py-1 pr-3"><DateTime value={r.occurred_at} /></td>
                      <td className="pr-3 font-mono text-xs">{r.method} {r.route}</td>
                      <td className="pr-3">{r.status}</td>
                      <td className="pr-3">{r.error_code ?? ""}</td>
                      <td className="font-mono text-xs">{r.trace_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryView>
        ) : null}
      </div>
    </Card>
  );
}

/** One verification followed through requests, route decision, decisions and webhook deliveries. */
export function TraceSection() {
  const [id, setId] = useState("");
  const [asked, setAsked] = useState("");
  const q = useCellQuery(["developers", "trace", asked], (api) => api.verifyApi.sessionTrace(asked), { enabled: asked !== "" });
  return (
    <Card title="Trace a verification" description="Follow one session through your calls, the route decision, the decisions and the webhook deliveries. Ids, states and times only.">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <TextField label="Session ID" value={id} onChange={setId} mono />
          <Button isDisabled={!id.trim()} onPress={() => setAsked(id.trim())}>Trace</Button>
        </div>
        {asked ? (
          <QueryView query={q} loadingRows={3}>
            {(t) => (
              <div className="space-y-2 text-sm">
                <ol className="space-y-1">
                  {t.steps.map((s, i) => (
                    <li key={i}><DateTime value={s.at} />: <span className="font-medium">{s.kind.toLowerCase().replaceAll("_", " ")}</span>, {s.summary} <span className="font-mono text-xs text-ink-muted">{s.id}</span></li>
                  ))}
                </ol>
                {t.missing_links.length ? <p className="text-ink-muted">No record of: {t.missing_links.map((m) => m.toLowerCase().replaceAll("_", " ")).join(", ")}.</p> : null}
                <p className="text-xs text-ink-muted">{t.note}</p>
              </div>
            )}
          </QueryView>
        ) : null}
      </div>
    </Card>
  );
}

/** What an application may do, as versions. Removals can be staged; widening production needs a second person. */
export function ScopeHistorySection() {
  const p = useApps();
  const q = useCellQuery(["developers", "scopes", p.chosen], (api) => api.verifyApi.scopeChanges(p.chosen), { enabled: p.chosen !== "" });
  const [reason, setReason] = useState("");
  const [drop, setDrop] = useState("");
  const propose = useCellMutation((api) => api.verifyApi.proposeScopeChange(p.chosen, { scopes: (q.data?.scopes_now ?? []).filter((s) => s !== drop), reason: reason.trim() }), { invalidate: [["developers", "scopes", p.chosen]], success: "Recorded" });
  const approve = useCellMutation((api, id: string) => api.verifyApi.approveScopeChange(id), { invalidate: [["developers", "scopes", p.chosen]], success: "Approved" });
  return (
    <Card title="Application scopes" description="What each application may do, kept as versions. Removing a scope takes effect on the next call, even for a token already issued. Widening a production application needs a second person.">
      <div className="space-y-4">
        <AppPicker p={p} />
        {p.chosen ? (
          <QueryView query={q} loadingRows={3}>
            {(h) => (
              <div className="space-y-3">
                <ul className="list-disc pl-5 text-sm">{h.scopes_now.map((s) => <li key={s}>{scopeWords(s)} <span className="font-mono text-xs text-ink-muted">{s}</span></li>)}</ul>
                <div className="flex flex-wrap items-end gap-3">
                  <Select label="Remove a scope" value={drop} onChange={setDrop} options={[{ id: "", label: "Choose one" }, ...h.scopes_now.map((s) => ({ id: s, label: scopeWords(s) }))]} />
                  <TextField label="Why" value={reason} onChange={setReason} />
                  <Button isDisabled={!drop || reason.trim().length < 3 || h.scopes_now.length < 2} pending={propose.isPending} onPress={() => propose.mutate(undefined)}>Remove it</Button>
                </div>
                {propose.error ? <ApiErrorView error={propose.error} /> : null}
                <ol className="space-y-2 text-sm">
                  {h.changes.map((c) => {
                    const st = scopeChangeState(c, null);
                    return (
                      <li key={c.change_id} className="rounded border border-line p-2">
                        Version {c.version}, {c.kind.toLowerCase()}: {c.reason}. {st.state} <DateTime value={c.effective_at} />
                        {st.canApprove ? <Button size="xs" variant="secondary" onPress={() => approve.mutate(c.change_id)}>Approve</Button> : null}
                      </li>
                    );
                  })}
                </ol>
                {approve.error ? <ApiErrorView error={approve.error} /> : null}
              </div>
            )}
          </QueryView>
        ) : null}
      </div>
    </Card>
  );
}

/** The scored synthetic run for an application: a test result, never a certification. */
export function CertificationSection() {
  const p = useApps();
  const q = useCellQuery(["developers", "certification", p.chosen], (api) => api.verifyApi.certification(p.chosen), { enabled: p.chosen !== "" });
  return (
    <Card title="Sandbox test run" description="Your server is scored against every synthetic scenario, including the awkward ones. A pass is kept with its digest. It is not a certification and does not approve production.">
      <div className="space-y-4">
        <AppPicker p={p} />
        {p.chosen ? (
          <QueryView query={q} loadingRows={2}>
            {(c) => (
              <div className="space-y-2 text-sm">
                <Banner tone={c.suite.met ? "ok" : "info"} title={suiteHeadline(c.suite)}>
                  {c.suite.statement ?? c.suite.reason ?? ""}
                </Banner>
                <ul className="list-disc pl-5 text-ink-muted">
                  {c.runs.map((r) => <li key={r.run_id}>{r.passed ? "Passed" : "Did not pass"} {r.personas_passed} of {r.personas_total}, <DateTime value={r.ran_at} /> <span className="font-mono text-xs">{r.report_digest.slice(0, 20)}</span></li>)}
                </ul>
                <p className="text-xs text-ink-muted">Send what your server did to POST /v1/verify-api/fixtures/report to score it first, then record the run for this application.</p>
              </div>
            )}
          </QueryView>
        ) : null}
      </div>
    </Card>
  );
}

const TRY_OPERATIONS = [
  { id: "whoami", label: "Who am I (no scope needed)" },
  { id: "products", label: "List products" },
  { id: "workflows", label: "List workflows" },
  { id: "list_sessions", label: "List sessions" },
  { id: "create_session", label: "Create a test session" },
];

/** Run one call against a SANDBOX application. Production is refused by the server, not by this screen. */
export function TryItSection() {
  const p = useApps();
  const [op, setOp] = useState("whoami");
  const run = useCellMutation((api) => api.verifyApi.tryIt(p.chosen, { operation: op }));
  return (
    <Card title="Try it" description="Run one call as a sandbox application and see the exact request and answer. It never touches production and calls no provider.">
      <div className="space-y-4">
        <AppPicker p={p} />
        <Select label="Call" value={op} onChange={setOp} options={TRY_OPERATIONS} />
        <Button isDisabled={!p.chosen} pending={run.isPending} onPress={() => run.mutate(undefined)}>Run it</Button>
        {run.error ? <ApiErrorView error={run.error} /> : null}
        {run.data ? (
          <div className="space-y-2 text-sm">
            <p className="font-mono text-xs">{run.data.request.method} {run.data.request.path} answered {run.data.status}</p>
            <pre className="max-h-72 overflow-auto rounded bg-surface-sunken p-3 text-xs">{JSON.stringify(run.data.response, null, 2)}</pre>
            <p className="text-xs text-ink-muted">{run.data.note}</p>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/** What may be said publicly about the developer platform, and what may not, with the reason. */
export function ClaimsSection() {
  const q = useCellQuery(["developers", "claims"], (api) => api.verifyApi.claims());
  return (
    <Card title="Public claims" description="Built from the evidence the platform holds. Anything not listed under claims may not be published.">
      <QueryView query={q} loadingRows={3}>
        {(c) => (
          <div className="space-y-4 text-sm">
            <div>
              <p className="mb-1 font-medium">May be said</p>
              <ul className="list-disc pl-5">{c.claims.map((x) => <li key={x.id}>{x.statement} <span className="text-xs text-ink-muted">({x.basis}: {x.evidence})</span></li>)}</ul>
            </div>
            <div>
              <p className="mb-1 font-medium">May not be said</p>
              <ul className="list-disc pl-5">{c.not_claimed.map((x) => <li key={x.claim}>{x.claim}. <span className="text-ink-muted">{x.reason}</span></li>)}</ul>
            </div>
            <p className="text-xs text-ink-muted">{c.rule}</p>
          </div>
        )}
      </QueryView>
    </Card>
  );
}
