"use client";

import type { DrExercise } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { FlaskConical, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { CapabilityHint, seconds } from "./recovery-shared";

const KEY = ["platform", "exercises"];
const KINDS = [
  "FAILOVER",
  "MIGRATION",
  "FAILBACK",
  "RESTORE",
  "AZ_LOSS",
  "PROCESS_KILL",
  "BROKER_DEGRADATION",
  "KMS_OUTAGE",
  "CONTROL_PLANE_LOSS",
  "PARTITION",
].map((k) => ({ id: k, label: k.replace(/_/g, " ").toLowerCase() }));

function localInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function Record({ onClose }: { onClose: () => void }) {
  const now = new Date();
  const [kind, setKind] = useState("FAILOVER");
  const [cell, setCell] = useState("");
  const [scenario, setScenario] = useState("");
  const [rto, setRto] = useState("");
  const [rpo, setRpo] = useState("");
  const [findings, setFindings] = useState("");
  const [start, setStart] = useState(localInput(new Date(now.getTime() - 3600_000)));
  const [end, setEnd] = useState(localInput(now));
  const n = (v: string) => (v.trim() === "" ? null : Number(v));
  const record = useCellMutation(
    (api) =>
      api.platform.recordExercise({
        kind,
        exercised_cell_id: cell.trim(),
        scenario: scenario.trim(),
        observed_rto_s: n(rto),
        observed_rpo_s: n(rpo),
        findings: findings
          .split("\n")
          .map((f) => f.trim())
          .filter(Boolean),
        started_at: new Date(start).toISOString(),
        completed_at: new Date(end).toISOString(),
      }),
    { invalidate: [KEY], success: (r) => (r.passed ? "Exercise passed" : "Exercise failed its targets"), onSuccess: onClose },
  );
  const valid = /^[a-z0-9-]{1,40}$/.test(cell.trim()) && scenario.trim().length >= 4 && new Date(end) >= new Date(start);
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !record.isPending && onClose()}
      title="Record a DR exercise"
      description="Pass or fail is computed against the recovery targets, never declared. A missed target raises an SLO breach."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={record.isPending} isDisabled={!valid} onPress={() => record.mutate(undefined)}>
            Record
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Kind" value={kind} onChange={setKind} options={KINDS} />
          <TextField label="Cell" isRequired mono value={cell} onChange={setCell} placeholder="eu-1-dr" />
        </div>
        <TextField label="Scenario" isRequired value={scenario} onChange={setScenario} placeholder="Regional loss game day" />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Observed RTO (s)" inputMode="decimal" value={rto} onChange={setRto} />
          <TextField label="Observed RPO (s)" inputMode="decimal" value={rpo} onChange={setRpo} />
          <TextField label="Started" type="datetime-local" value={start} onChange={setStart} />
          <TextField label="Completed" type="datetime-local" value={end} onChange={setEnd} />
        </div>
        <TextField label="Findings (one per line)" multiline rows={3} value={findings} onChange={setFindings} description="Any finding fails the exercise." />
        {record.error ? (
          <>
            <ApiErrorView error={record.error} />
            <CapabilityHint error={record.error} capability="platform.cell.manage" />
          </>
        ) : null}
      </div>
    </Dialog>
  );
}

/** DR exercises and game days (RESILIENCE-001 section 27; SRE-001 section 25). */
export function ExercisesPage() {
  const ex = useCellQuery(KEY, (api) => api.platform.exercises());
  const [recording, setRecording] = useState(false);
  const columns: Column<DrExercise>[] = [
    { key: "at", header: "Completed", render: (e) => <DateTime value={e.completed_at} />, sortValue: (e) => e.completed_at },
    { key: "kind", header: "Kind", render: (e) => e.kind.replace(/_/g, " ").toLowerCase() },
    { key: "cell", header: "Cell", render: (e) => <span className="font-mono text-xs">{e.cell_id}</span> },
    { key: "scenario", header: "Scenario", render: (e) => e.scenario },
    { key: "rto", header: "RTO", render: (e) => `${seconds(e.observed_rto_s)} / ${seconds(e.target_rto_s)}` },
    { key: "rpo", header: "RPO", render: (e) => `${seconds(e.observed_rpo_s)} / ${seconds(e.target_rpo_s)}` },
    { key: "passed", header: "Result", render: (e) => <Badge tone={e.passed ? "ok" : "danger"}>{e.passed ? "Passed" : "Failed"}</Badge> },
  ];
  return (
    <>
      <PageHeader
        icon={FlaskConical}
        title="DR exercises"
        description="Observed recovery time and data loss against targets (failover 5 min / 30 s; restore 4 h / 5 min)."
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setRecording(true)}>
            Record exercise
          </Button>
        }
      />
      <Card padded={false}>
        <QueryView query={ex}>
          {(r) =>
            r.exercises.length ? (
              <DataTable caption="DR exercises" columns={columns} rows={r.exercises} rowKey={(e) => e.exercise_id} />
            ) : (
              <EmptyState title="No exercises recorded" icon={FlaskConical}>
                Record each drill and real recovery so readiness is proven, not assumed.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {recording ? <Record onClose={() => setRecording(false)} /> : null}
    </>
  );
}
