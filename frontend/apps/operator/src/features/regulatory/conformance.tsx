"use client";

import type { ConformanceCase, ConformanceOutcome, ConformanceRun } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, Tabs, TextField, type Column } from "@zoiko/ui";
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

function RunDialog({ run, onClose }: { run: ConformanceRun | null; onClose: () => void }) {
  const [decision, setDecision] = useState<"ACCEPTED" | "REJECTED">("ACCEPTED");
  const [note, setNote] = useState("");
  const [exporting, setExporting] = useState<string | null>(null);
  const review = useCellMutation((api, r: ConformanceRun) => api.regulatory.reviewConformanceRun(r.run_id, decision, r.run_hash, note.trim()), {
    invalidate: [REG.runs],
    success: "Review recorded against the run hash",
    onSuccess: () => setNote(""),
  });
  const notPassing = run?.results.filter((r) => r.outcome !== "PASS") ?? [];
  return (
    <Dialog isOpen={run !== null} onOpenChange={(o) => !o && onClose()} title={run?.run_id ?? ""} size="xl">
      {run ? (
        <div className="space-y-4">
          <Facts
            items={[
              ["Commit", <Mono key="c">{run.commit.slice(0, 12)}</Mono>],
              ["Environment", run.environment],
              ["Recorded by", <Mono key="r">{run.recorded_by}</Mono>],
              ["Run hash", <Hash key="h" value={run.run_hash} />],
              ["P0 exit", <Verdict key="p" ok={run.p0_exit.met} yes="Met" no="Not met" />],
              ["Why not", <Reasons key="w" reasons={run.p0_exit.reasons} />],
            ]}
          />
          {run.environment.endsWith("+dirty") ? (
            <Banner tone="warn">This run was made from uncommitted changes, so it is not evidence about the commit alone.</Banner>
          ) : null}
          <ul className="flex flex-wrap gap-1" aria-label="Cases not passing">
            {notPassing.map((r) => (
              <li key={r.case}>
                <Badge tone={OUTCOME_TONE[r.outcome]}>
                  {r.case} {r.outcome}
                </Badge>
              </li>
            ))}
          </ul>
          {run.reviews.map((r) => (
            <p key={r.reviewed_at} className="text-sm">
              <Verdict ok={r.decision === "ACCEPTED"} yes="Accepted" no="Rejected" /> by <Mono>{r.reviewer}</Mono> · <DateTime value={r.reviewed_at} /> · {r.note_ref}
            </p>
          ))}
          <form
            aria-label="Review"
            className="flex flex-wrap items-end gap-2 border-t border-border pt-4"
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
            <TextField label="Note reference" value={note} onChange={setNote} isRequired />
            <Button type="submit" pending={review.isPending} isDisabled={note.trim().length < 3}>
              Sign run hash
            </Button>
            <Button variant="secondary" onPress={() => setExporting(`run:${run.run_id}`)}>
              Export proof
            </Button>
          </form>
          {review.error ? <RegError error={review.error} /> : null}
          <ExportDialog bundleId={exporting} onClose={() => setExporting(null)} />
        </div>
      ) : null}
    </Dialog>
  );
}

function RunsTab() {
  const q = useCellQuery(REG.runs, (api) => api.regulatory.conformanceRuns());
  const [openId, setOpenId] = useState<string | null>(null);
  const open = q.data?.items.find((r) => r.run_id === openId) ?? null;
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
      <RunDialog run={open} onClose={() => setOpenId(null)} />
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
