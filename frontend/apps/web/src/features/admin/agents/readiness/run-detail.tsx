"use client";

import type { ReadinessExplanation, ReadinessRunDetail } from "@zoiko/api-client";
import { Badge, Banner, Button, ButtonLink, Card, CopyId, DateTime, Dialog, PageHeader, Tabs, TextField } from "@zoiko/ui";
import { useState } from "react";

import { useCellMutation, useCellQuery } from "@/lib/cell";

import { AgentEvidenceTimeline } from "./components";
import { READINESS_KEY, RUN_STATE_TEXT, reasonText, timelineSteps } from "./logic";
import { PauseAgentControl } from "./pause-control";
import { ReadinessQuery, Redacted, WriteError } from "./shared";

function Counts({ run }: { run: ReadinessRunDetail }) {
  const s = run.summary;
  if (!s || s.records_read === undefined) return <p className="text-sm text-ink-muted">No summary was recorded for this run.</p>;
  const proposals = (s.proposals_created ?? 0) + (s.proposals_existing ?? 0);
  return (
    <p className="text-sm font-medium">
      Checked {s.records_read} permitted records · {s.findings ?? 0} issues flagged · {proposals} review proposals · {s.privileges_changed ?? 0} privileges changed
    </p>
  );
}

function downloadJson(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Sections 07 and 09 step 8. Shown only when present, labelled, and always below the rule result it explains. */
function ExplanationCard({ explanation }: { explanation: ReadinessExplanation }) {
  const fromModel = explanation.source === "MODEL";
  return (
    <Card
      title={fromModel ? "AI explanation" : "Explanation (fixed text)"}
      description={
        fromModel
          ? "Written by a model from the rule codes and counts, then checked against them. It does not change the result above, which is authoritative."
          : "The AI explanation was not used for this run, so fixed text built from the result is shown. It does not change the result above."
      }
    >
      <p className="text-sm">{explanation.text}</p>
      <p className="mt-2 text-xs text-ink-subtle">
        {fromModel ? `Model ${explanation.model ?? "unknown"}. ` : `Reason: ${FALLBACK_TEXT[explanation.fallback_reason ?? ""] ?? explanation.fallback_reason}. `}
        Not a decision and not evidence of one.
      </p>
    </Card>
  );
}

const FALLBACK_TEXT: Record<string, string> = {
  MODEL_NOT_CONFIGURED: "no approved model is configured",
  MODEL_TIMEOUT: "the model did not answer in time",
  MODEL_UNAVAILABLE: "the model was unavailable",
  UNSUPPORTED_CLAIM: "the model's text did not match the result",
};

/** WF-AG-03 "Export permitted evidence": the server decides what is permitted and records the export with its purpose. */
function ExportEvidenceControl({ agentId, runId }: { agentId: string; runId: string }) {
  const [open, setOpen] = useState(false);
  const [purpose, setPurpose] = useState("");
  const [error, setError] = useState<unknown>(null);
  const exporter = useCellMutation((api, why: string) => api.accessReadiness.exportEvidence(agentId, runId, why), { success: "Evidence exported" });
  return (
    <>
      <Button variant="secondary" size="lg" onPress={() => setOpen(true)}>
        Export permitted evidence
      </Button>
      <Dialog
        isOpen={open}
        onOpenChange={setOpen}
        size="md"
        title="Export permitted evidence"
        description="References and digests only. The export is recorded with its purpose. Without the review role it is redacted."
        footer={
          <>
            <Button variant="secondary" size="lg" isDisabled={exporter.isPending} onPress={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              size="lg"
              pending={exporter.isPending}
              isDisabled={purpose.trim().length < 3}
              onPress={() => {
                setError(null);
                exporter
                  .mutateAsync(purpose.trim())
                  .then((out) => {
                    downloadJson(`access-readiness-evidence-${runId}.json`, out);
                    setOpen(false);
                    setPurpose("");
                  })
                  .catch(setError);
              }}
            >
              Export
            </Button>
          </>
        }
      >
        <div className="space-y-4 text-sm">
          <TextField label="Purpose of the export" isRequired value={purpose} onChange={setPurpose} description="At least 3 characters, for example a case or audit reference." />
          {error ? <WriteError error={error} /> : null}
        </div>
      </Dialog>
    </>
  );
}

/** WF-AG-03. Read only for most observers. A role that cannot see the run sees that it is redacted, never a run with nothing in it. */
export function RunDetailPage({ agentId, runId }: { agentId: string; runId: string }) {
  const run = useCellQuery([...READINESS_KEY, "run", agentId, runId], (api) => api.accessReadiness.run(agentId, runId));
  const header = (
    <PageHeader
      title="Access readiness run"
      description={
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span>Agent <CopyId value={agentId} label="agent" /></span>
          <span>Run <CopyId value={runId} label="run" /></span>
        </span>
      }
      breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Access readiness", href: "/admin/agents/readiness" }, { label: "Run" }]}
    />
  );
  return (
    <>
      {header}
      <ReadinessQuery query={run} forbidden={<Redacted what="This run" />}>
        {(r) => <RunBody agentId={agentId} run={r} />}
      </ReadinessQuery>
    </>
  );
}

function RunBody({ agentId, run }: { agentId: string; run: ReadinessRunDetail }) {
  const base = [...READINESS_KEY, "run", agentId, run.run_id];
  const decisions = useCellQuery([...base, "decisions"], (api) => api.accessReadiness.decisions(agentId, run.run_id));
  const evidence = useCellQuery([...base, "evidence"], (api) => api.accessReadiness.evidence(agentId, run.run_id));
  const setup = useCellQuery([...READINESS_KEY, "setup", agentId], (api) => api.accessReadiness.setup(agentId));
  const [failure, setFailure] = useState<unknown>(null);
  const pause = useCellMutation((api, reason: string) => api.accessReadiness.pause(agentId, reason), { invalidate: [READINESS_KEY], success: "Future runs paused" });
  const resume = useCellMutation((api) => api.accessReadiness.resume(agentId), { invalidate: [READINESS_KEY], success: "Future runs resumed" });
  const retry = useCellMutation((api) => api.accessReadiness.retryRun(agentId, run.run_id), { invalidate: [READINESS_KEY], success: "Retry queued" });
  const steps = timelineSteps(run, decisions.data?.items, evidence.data);
  const first = run.proposals[0];
  const refused = run.state === "DENIED";
  const integrity = evidence.data ? (evidence.data.verified ? "Verified" : evidence.data.integrity_exceptions.length ? "Integrity exception" : "No evidence recorded") : evidence.isError ? "Redacted" : "Loading";

  const overview = (
    <div className="space-y-4">
      {refused ? (
        <Banner tone="danger" title="Denied by policy" live="polite">
          The run was refused before it read anything. No records were checked.
          <ul className="mt-1 list-disc ps-5">
            {run.denial_codes.map((c) => (
              <li key={c}>
                {reasonText(c)} <code className="font-mono text-xs">{c}</code>
              </li>
            ))}
          </ul>
        </Banner>
      ) : null}
      {run.state === "FAILED" ? <Banner tone="danger" title="The run failed">It stopped without a complete result. Nothing was changed.</Banner> : null}
      {!refused ? <Counts run={run} /> : null}
      {run.explanation ? <ExplanationCard explanation={run.explanation} /> : null}
      <AgentEvidenceTimeline steps={steps} />
      {run.proposals.length ? (
        <Card title="Review proposals from this run">
          <ul className="divide-y divide-border text-sm">
            {run.proposals.map((p) => (
              <li key={p.proposal_id} className="flex flex-wrap items-center gap-2 py-2">
                <span className="font-medium">{p.rule_id} · {p.finding_code}</span>
                <Badge>{p.status.replace("_", " ").toLowerCase()}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );

  const decisionsTab = (
    <ReadinessQuery query={decisions} forbidden={<Redacted what="Policy decisions" />}>
      {(d) =>
        d.items.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-start text-sm">
              <caption className="sr-only">Policy decisions for this run</caption>
              <thead>
                <tr className="text-xs uppercase text-ink-subtle">
                  <th scope="col" className="py-2 pe-3 text-start">Time</th>
                  <th scope="col" className="pe-3 text-start">Action</th>
                  <th scope="col" className="pe-3 text-start">Resource</th>
                  <th scope="col" className="pe-3 text-start">Outcome</th>
                  <th scope="col" className="text-start">Reason</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((x) => (
                  <tr key={x.decision_id} className="border-t border-border align-top">
                    <td className="py-2 pe-3"><DateTime value={x.decided_at} /></td>
                    <td className="pe-3 font-mono text-xs">{x.action}</td>
                    <td className="break-all pe-3 font-mono text-xs">{x.resource}</td>
                    <td className="pe-3"><Badge tone={x.outcome === "ALLOW" ? "ok" : "danger"}>{x.outcome === "ALLOW" ? "Allowed" : "Denied"}</Badge></td>
                    <td>{reasonText(x.reason_code)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-ink-muted">No policy decisions were recorded for this run.</p>
        )
      }
    </ReadinessQuery>
  );

  const evidenceTab = (
    <ReadinessQuery query={evidence} forbidden={<Redacted what="Evidence" />}>
      {(e) => (
        <div className="space-y-3">
          {e.verified ? (
            <Banner tone="ok" title="Evidence chain verified">Every link was replayed just now and matched.</Banner>
          ) : e.integrity_exceptions.length ? (
            <Banner tone="danger" title="Integrity exception" live="assertive">
              A link in the evidence chain does not verify. Do not rely on this run&apos;s evidence.
              <ul className="mt-1 list-disc ps-5 font-mono text-xs">{e.integrity_exceptions.map((x) => <li key={x}>{x}</li>)}</ul>
            </Banner>
          ) : (
            <Banner tone="warn" title="No evidence recorded">There is nothing to verify, so this run is not shown as verified.</Banner>
          )}
          <ul className="divide-y divide-border text-sm">
            {e.items.map((x) => (
              <li key={x.event_id} className="py-2">
                <span className="font-medium">{x.seq}. {x.event_type}</span>{" "}
                <span className="text-xs text-ink-muted">{x.classification} · {x.region} · <DateTime value={x.recorded_at} /></span>
                <code className="block break-all font-mono text-xs text-ink-subtle">{x.payload_digest}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </ReadinessQuery>
  );

  const technicalTab = (
    <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
      {(
        [
          ["Attempt", run.attempt],
          ["Mode", run.mode],
          ["Purpose", run.purpose],
          ["Rule set", run.rule_set_version],
          ["Policy hash", run.policy_hash],
          ["Run definition hash", run.run_definition_hash],
          ["Trace ID", run.trace_id ?? "none"],
          ["Worker attestation", run.worker_attestation_id ?? "none"],
          ["Created", run.created_at],
          ["Finished", run.terminal_at ?? "not finished"],
        ] as [string, string | number][]
      ).map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-ink-muted">{k}</dt>
          <dd className="break-all font-mono text-xs">{v}</dd>
        </div>
      ))}
    </dl>
  );

  return (
    <div className="grid gap-5 min-[901px]:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="min-w-0 space-y-4">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={refused || run.state === "FAILED" ? "danger" : run.state === "COMPLETED" ? "ok" : "neutral"}>{RUN_STATE_TEXT[run.state]}</Badge>
          <span className="text-ink-muted">Region {run.region}</span>
        </p>
        <Tabs
          label="Run details"
          tabs={[
            { id: "overview", label: "Overview", content: overview },
            { id: "decisions", label: "Policy Decisions", content: decisionsTab },
            { id: "evidence", label: "Evidence", content: evidenceTab },
            { id: "technical", label: "Technical Events", content: technicalTab },
          ]}
        />
      </div>
      <aside aria-label="Run context" className="min-w-0 space-y-4">
        <Card title="Context">
          <dl className="space-y-3 text-sm">
            <div>
              <dt className="text-ink-muted">Policy snapshot</dt>
              <dd className="break-all font-mono text-xs">{run.policy_hash}</dd>
            </div>
            <div>
              <dt className="text-ink-muted">Sponsor</dt>
              <dd>{setup.data?.agent ? (setup.data.agent.sponsor_active ? "Active" : "Not active") : setup.isError ? "Redacted" : "Loading"}</dd>
            </div>
            <div>
              <dt className="text-ink-muted">Model used</dt>
              <dd>
                {run.explanation?.source === "MODEL"
                  ? `Yes, for the explanation only (${run.explanation.model ?? "unknown"})`
                  : run.explanation
                    ? "No. A fixed explanation was used."
                    : "No. AI explanation is off."}
              </dd>
            </div>
            <div>
              <dt className="text-ink-muted">Evidence completeness</dt>
              <dd>{integrity}</dd>
            </div>
          </dl>
        </Card>
        <Card title="Actions" description="The server checks your role for each one.">
          <div className="flex flex-col gap-2">
            {first ? (
              <ButtonLink size="lg" href={`/admin/agent-review/${encodeURIComponent(first.proposal_id)}`}>
                Open review proposal
              </ButtonLink>
            ) : null}
            <ExportEvidenceControl agentId={agentId} runId={run.run_id} />
            {run.state === "FAILED" ? (
              <Button
                variant="secondary"
                size="lg"
                pending={retry.isPending}
                onPress={() => {
                  setFailure(null);
                  retry.mutateAsync(undefined).catch(setFailure);
                }}
              >
                Retry run
              </Button>
            ) : null}
            <PauseAgentControl
              paused={setup.data?.status === "PAUSED"}
              pending={pause.isPending || resume.isPending}
              onPause={(reason) => {
                setFailure(null);
                pause.mutateAsync(reason).catch(setFailure);
              }}
              onResume={() => {
                setFailure(null);
                resume.mutateAsync(undefined).catch(setFailure);
              }}
            />
            {failure ? <WriteError error={failure} /> : null}
          </div>
        </Card>
      </aside>
    </div>
  );
}
