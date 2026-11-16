"use client";

import type { ConformanceCase, ConformanceOutcome, ConformanceRun } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, Tabs, TextField, type Column } from "@zoiko/ui";
import { ListChecks } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { ExportDialog } from "./claims";
import { Facts, Hash, Mono, REG, Reasons, RegError, Verdict } from "./common";

const COVERAGE_TONE = { COVERED: "ok", PARTIAL: "warn", GAP: "danger" } as const;
const OUTCOME_TONE: Record<ConformanceOutcome, "ok" | "warn" | "danger" | "neutral"> = { PASS: "ok", PARTIAL: "warn", NOT_RUN: "neutral", FAIL: "danger", GAP: "danger" };

function MatrixTab() {
  const q = useCellQuery(REG.matrix, (api) => api.regulatory.conformanceMatrix());
  const columns: Column<ConformanceCase>[] = [
    { key: "case", header: "Case", pinned: true, render: (c) => <Mono>{c.case}</Mono>, sortValue: (c) => c.case },
    { key: "title", header: "Scenario", render: (c) => c.title },
    { key: "expected", header: "Expected", render: (c) => <span className="text-ink-muted">{c.expected}</span> },
    { key: "coverage", header: "Coverage", render: (c) => <Badge tone={COVERAGE_TONE[c.coverage]}>{c.coverage}</Badge>, sortValue: (c) => c.coverage },
    { key: "tests", header: "Tests", render: (c) => c.tests.length },
    { key: "note", header: "What is missing", render: (c) => (c.note ? <span className="text-xs">{c.note}</span> : "-") },
  ];
  return (
    <QueryView query={q}>
      {(d) => (
        <div className="space-y-3">
          <p className="flex flex-wrap items-center gap-2 text-sm">
            {Object.entries(d.counts).map(([k, n]) => (
              <Badge key={k} tone={COVERAGE_TONE[k as keyof typeof COVERAGE_TONE] ?? "neutral"}>
                {k}: {n}
              </Badge>
            ))}
            <span className="text-ink-muted">
              matrix <Hash value={d.matrix_hash} />
            </span>
          </p>
          <Card padded={false}>
            <DataTable caption="Section 12 acceptance cases" columns={columns} rows={d.cases} rowKey={(c) => c.case} />
          </Card>
        </div>
      )}
    </QueryView>
  );
}

type CaseResult = ConformanceRun["results"][number];

/** Worst first, so a failure is the first thing a reviewer reads. */
const OUTCOME_ORDER: ConformanceOutcome[] = ["FAIL", "GAP", "PARTIAL", "NOT_RUN", "PASS"];

function Tests({ ids, failed = false }: { ids: string[]; failed?: boolean }) {
  if (!ids.length) return <span className="text-ink-subtle">-</span>;
  return (
    <ul className="space-y-0.5">
      {ids.map((t) => (
        <li key={t} className={failed ? "font-mono text-xs break-all text-danger-fg" : "font-mono text-xs break-all"}>
          {t}
        </li>
      ))}
    </ul>
  );
}

/** Everything the reviewer signs (section 12 / WF-08): the run's identity and hashes, every case outcome with its tests, and earlier reviews. */
function RunDetail({ run }: { run: ConformanceRun }) {
  const counts = OUTCOME_ORDER.map((o) => [o, run.results.filter((r) => r.outcome === o).length] as const).filter(([, n]) => n > 0);
  const failed = run.results.filter((r) => r.outcome === "FAIL");
  const columns: Column<CaseResult>[] = [
    { key: "case", header: "Case", pinned: true, render: (r) => <Mono>{r.case}</Mono>, sortValue: (r) => r.case },
    {
      key: "outcome",
      header: "Outcome",
      render: (r) => <Badge tone={OUTCOME_TONE[r.outcome]}>{r.outcome}</Badge>,
      sortValue: (r) => OUTCOME_ORDER.indexOf(r.outcome),
    },
    { key: "failed", header: "Failed tests", render: (r) => <Tests ids={r.failed} failed /> },
    { key: "not_run", header: "Not run", render: (r) => <Tests ids={r.not_run} /> },
    {
      key: "passed",
      header: "Passed tests",
      render: (r) =>
        r.passed.length ? (
          <details>
            <summary className="cursor-pointer text-xs">{r.passed.length} passed</summary>
            <Tests ids={r.passed} />
          </details>
        ) : (
          <span className="text-ink-subtle">-</span>
        ),
    },
  ];
  return (
    <div className="space-y-4">
      <Facts
        items={[
          ["Run", <Mono key="id">{run.run_id}</Mono>],
          ["Commit", <span key="c" className="font-mono text-xs break-all">{run.commit}</span>],
          ["Environment", run.environment],
          ["Recorded by", <Mono key="r">{run.recorded_by}</Mono>],
          ["Started", <DateTime key="s" value={run.started_at} />],
          ["Finished", <DateTime key="f" value={run.finished_at} />],
          ["Matrix hash", <span key="m" className="font-mono text-xs break-all">{run.matrix_hash}</span>],
          ["Run hash (what you sign)", <span key="h" className="font-mono text-xs break-all">{run.run_hash}</span>],
          ["P0 exit", <Verdict key="p" ok={run.p0_exit.met} yes="Met" no="Not met" />],
          ["Why not", <Reasons key="w" reasons={run.p0_exit.reasons} />],
        ]}
      />
      {run.environment.endsWith("+dirty") ? (
        <Banner tone="warn">This run was made from uncommitted changes, so it is not evidence about the commit alone.</Banner>
      ) : null}
      {failed.length ? (
        <Banner tone="danger" title={`${failed.length} ${failed.length === 1 ? "case failed" : "cases failed"}`} live="polite">
          {failed.map((r) => r.case).join(", ")} failed in this run. Read the failed tests below before you sign.
        </Banner>
      ) : null}
      <p className="flex flex-wrap items-center gap-2 text-sm" aria-label="Outcomes by count">
        {counts.map(([o, n]) => (
          <Badge key={o} tone={OUTCOME_TONE[o]}>
            {o}: {n}
          </Badge>
        ))}
        <span className="text-ink-muted">of {run.results.length} cases</span>
      </p>
      <Card padded={false}>
        <DataTable caption="Case outcomes in this run" columns={columns} rows={run.results} rowKey={(r) => r.case} />
      </Card>
      <section aria-label="Reviews" className="space-y-1">
        <h3 className="text-sm font-medium">Reviews</h3>
        {run.reviews.length ? (
          run.reviews.map((r) => (
            <p key={`${r.reviewer}-${r.reviewed_at}`} className="text-sm">
              <Verdict ok={r.decision === "ACCEPTED"} yes="Accepted" no="Rejected" /> by <Mono>{r.reviewer}</Mono> · <DateTime value={r.reviewed_at} /> · {r.note_ref}
            </p>
          ))
        ) : (
          <p className="text-sm text-ink-subtle">Not reviewed yet.</p>
        )}
      </section>
    </div>
  );
}

/** The review sits under the detail and stays locked until the reviewer says they have read it; it signs the hash of the run as loaded here. */
function ReviewForm({ run }: { run: ConformanceRun }) {
  const [decision, setDecision] = useState<"ACCEPTED" | "REJECTED">("ACCEPTED");
  const [note, setNote] = useState("");
  const [read, setRead] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const review = useCellMutation((api, r: ConformanceRun) => api.regulatory.reviewConformanceRun(r.run_id, decision, r.run_hash, note.trim()), {
    invalidate: [REG.runs],
    success: "Review recorded against the run hash",
    onSuccess: () => {
      setNote("");
      setRead(false);
    },
  });
  const failing = run.results.some((r) => r.outcome === "FAIL");
  return (
    <div className="space-y-3 border-t border-border pt-4">
      <Checkbox isSelected={read} onChange={setRead} description="Every case outcome, its failed and not-run tests, and the run hash above">
        I have read this run's outcomes
      </Checkbox>
      {failing && decision === "ACCEPTED" ? (
        <Banner tone="warn">Accepting records that you reviewed this run as it is. It does not turn a failed case into a pass, and P0 exit stays unmet.</Banner>
      ) : null}
      <form
        aria-label="Review"
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          review.mutate(run);
        }}
      >
        <Select
          label="Decision"
          value={decision}
          onChange={(v) => setDecision(v === "REJECTED" ? "REJECTED" : "ACCEPTED")}
          options={[
            { id: "ACCEPTED", label: "Accept" },
            { id: "REJECTED", label: "Reject" },
          ]}
        />
        <TextField label="Note reference" description="where your review notes are kept" placeholder="QA-REVIEW-2026-11-14" value={note} onChange={setNote} isRequired />
        <Button type="submit" pending={review.isPending} isDisabled={!read || note.trim().length < 3}>
          Sign run hash
        </Button>
        <Button variant="secondary" onPress={() => setExporting(`run:${run.run_id}`)}>
          Export proof
        </Button>
      </form>
      {review.error ? <RegError error={review.error} /> : null}
      <ExportDialog bundleId={exporting} onClose={() => setExporting(null)} />
    </div>
  );
}

function RunView({ runId }: { runId: string }) {
  const q = useCellQuery([...REG.runs, runId], (api) => api.regulatory.conformanceRun(runId));
  return (
    <QueryView query={q}>
      {(run) => (
        <div className="space-y-4">
          <RunDetail run={run} />
          <ReviewForm run={run} />
        </div>
      )}
    </QueryView>
  );
}

function RunDialog({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  return (
    <Dialog isOpen={runId !== null} onOpenChange={(o) => !o && onClose()} title={runId ? `Run ${runId}` : ""} size="xl">
      {runId ? <RunView key={runId} runId={runId} /> : null}
    </Dialog>
  );
}

function RunsTab() {
  const q = useCellQuery(REG.runs, (api) => api.regulatory.conformanceRuns());
  const [openId, setOpenId] = useState<string | null>(null);
  const columns: Column<ConformanceRun>[] = [
    { key: "id", header: "Run", pinned: true, render: (r) => <Mono>{r.run_id}</Mono> },
    { key: "when", header: "Finished", render: (r) => <DateTime value={r.finished_at} />, sortValue: (r) => r.finished_at },
    { key: "env", header: "Environment", render: (r) => r.environment },
    { key: "pass", header: "Passed", render: (r) => `${r.results.filter((x) => x.outcome === "PASS").length}/${r.results.length}` },
    { key: "reviews", header: "Reviews", render: (r) => r.reviews.length },
    { key: "p0", header: "P0 exit", render: (r) => <Verdict ok={r.p0_exit.met} yes="Met" no="Not met" /> },
    {
      key: "open",
      header: "Actions",
      align: "end",
      render: (r) => (
        <Button size="xs" onPress={() => setOpenId(r.run_id)} aria-label={`Open run ${r.run_id}`}>
          Open
        </Button>
      ),
    },
  ];
  return (
    <>
      <Card padded={false}>
        <QueryView query={q}>
          {(d) => (
            <DataTable
              caption="Conformance runs"
              columns={columns}
              rows={d.items}
              rowKey={(r) => r.run_id}
              empty={
                <EmptyState icon={ListChecks} title="No runs recorded">
                  Run tools/scripts/conformance_run.py and record its output with POST /v1/regulatory/conformance-runs.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>
      <RunDialog runId={openId} onClose={() => setOpenId(null)} />
    </>
  );
}

export function ConformancePage() {
  return (
    <>
      <PageHeader
        title="Conformance"
        description="The 48 section 12 acceptance cases, the tests that prove each, and recorded runs. A run counts as P0 exit only when every P0 case passes and a different person accepts its exact hash."
      />
      <Tabs
        label="Conformance"
        tabs={[
          { id: "matrix", label: "Test matrix", content: <MatrixTab /> },
          { id: "runs", label: "Runs", content: <RunsTab /> },
        ]}
      />
    </>
  );
}
