"use client";

import type { FairnessState, Measure, ModelKind, ModelVersion } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { FlaskConical, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { PRIVACY_KEY } from "./shared";

/** Models and fairness evidence (ZID-DTV-WF-04 B07; ZID-PRI-004 PR-16; CR-109, CR-110). Each model version has its latest independent evaluation,
 * every measure with its confidence interval, and a fairness state. A biometric method runs only on an ACTIVE version whose evaluation still holds.
 * No state says "pass": a version within its recorded operating points is "passes, with limits", because no evaluation shows a model is bias-free.
 * Protected characteristics may be audit attributes of an evaluation; they are never live features. */

const MODELS_KEY = [...PRIVACY_KEY, "models"];

/** The measures each kind needs (mirrors the server's KINDS). */
export const KIND_MEASURES: Record<ModelKind, string[]> = {
  face_match: ["fmr", "fnmr", "failure_to_acquire"],
  pad: ["apcer", "bpcer", "failure_to_acquire"],
  fraud: ["false_positive_rate", "overturn_rate"],
  document: ["false_reject_rate", "failure_to_acquire"],
};

export const FAIRNESS: Record<FairnessState, { label: string; tone: "ok" | "warn" | "danger" | "neutral" | "info" }> = {
  NOT_RUN: { label: "Not evaluated", tone: "neutral" },
  DATA_INSUFFICIENT: { label: "Data insufficient", tone: "warn" },
  PASS_WITH_LIMITS: { label: "Passes, with limits", tone: "ok" },
  FAIL: { label: "Fails", tone: "danger" },
  REEVALUATION_DUE: { label: "Re-evaluation due", tone: "warn" },
};

function pct(v: number): string {
  return `${(v * 100).toFixed(2)}%`;
}

function Measures({ m }: { m: ModelVersion }) {
  const e = m.latest_evaluation;
  if (!e) return <span className="text-ink-subtle">—</span>;
  return (
    <ul className="space-y-0.5 text-xs">
      {KIND_MEASURES[m.kind].map((k) => {
        const v = e.metrics[k] as Measure | undefined;
        return (
          <li key={k}>
            <span className="font-mono">{k}</span>: {v && typeof v === "object" ? `${pct(v.value)} (95% CI ${pct(v.ci_low)}–${pct(v.ci_high)})` : "missing"}
          </li>
        );
      })}
    </ul>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- register */

function RegisterDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [ref, setRef] = useState("");
  const [version, setVersion] = useState("");
  const [kind, setKind] = useState<ModelKind>("face_match");
  const [provider, setProvider] = useState("");
  const [features, setFeatures] = useState("");
  const reg = useCellMutation(
    (api) =>
      api.privacy.v2.registerModel({
        model_ref: ref.trim(),
        version: version.trim(),
        kind,
        provider_service_ref: provider.trim() || null,
        live_features: features
          .split(/[\s,]+/)
          .map((f) => f.trim())
          .filter(Boolean),
      }),
    {
      invalidate: [MODELS_KEY],
      success: "Model version registered",
      onSuccess: () => {
        setRef("");
        setVersion("");
        setFeatures("");
        onClose();
      },
    },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Register a model version"
      description="For a biometric method the model reference is the engine id or the provider, and the version is the exact one in service."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={reg.isPending} isDisabled={!/^[a-z0-9][a-z0-9_.-]{1,62}$/.test(ref.trim()) || !version.trim()} onPress={() => reg.mutate(undefined)}>
            Register
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Model reference" description="lowercase, e.g. veriff or deepface-arcface" value={ref} onChange={setRef} mono isRequired />
        <TextField label="Version" value={version} onChange={setVersion} mono isRequired />
        <Select
          label="Kind"
          value={kind}
          onChange={(v) => setKind((["face_match", "pad", "fraud", "document"].find((k) => k === v) ?? "face_match") as ModelKind)}
          options={[
            { id: "face_match", label: "Face match (for the face match method)" },
            { id: "pad", label: "Presentation-attack detection (for liveness)" },
            { id: "fraud", label: "Fraud" },
            { id: "document", label: "Document" },
          ]}
        />
        <TextField label="Provider service" description="optional" value={provider} onChange={setProvider} />
        <TextField
          label="Live features"
          description="what the model reads in production, comma separated; a protected characteristic or proxy is refused"
          value={features}
          onChange={setFeatures}
          className="sm:col-span-2"
        />
      </div>
      {reg.error ? <ApiErrorView error={reg.error} /> : null}
    </Dialog>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- evaluation */

type MeasureRow = { value: string; low: string; high: string };

function EvaluationDialog({ model, onClose }: { model: ModelVersion | null; onClose: () => void }) {
  const names = model ? KIND_MEASURES[model.kind] : [];
  const [evaluator, setEvaluator] = useState("");
  const [independent, setIndependent] = useState(false);
  const [cohorts, setCohorts] = useState("2");
  const [alt, setAlt] = useState(false);
  const [flagged, setFlagged] = useState(false);
  const [report, setReport] = useState("");
  const [audit, setAudit] = useState("");
  const [rows, setRows] = useState<Record<string, MeasureRow>>({});
  const row = (k: string): MeasureRow => rows[k] ?? { value: "", low: "", high: "" };
  const setCell = (k: string, f: keyof MeasureRow) => (v: string) => setRows((p) => ({ ...p, [k]: { ...row(k), [f]: v } }));
  const metrics: Record<string, Measure | boolean> = { disparity_flagged: flagged };
  let valid = true;
  for (const k of names) {
    const r = row(k);
    const [v, lo, hi] = [Number(r.value), Number(r.low), Number(r.high)];
    if ([r.value, r.low, r.high].some((x) => x.trim() === "") || !(lo >= 0 && lo <= v && v <= hi && hi <= 1)) valid = false;
    else metrics[k] = { value: v, ci_low: lo, ci_high: hi };
  }
  const record = useCellMutation(
    (api) =>
      api.privacy.v2.recordEvaluation({
        model_ref: model!.model_ref,
        version: model!.version,
        evaluator_ref: evaluator.trim(),
        independent,
        cohorts: Number(cohorts),
        metrics,
        accessible_alternative_confirmed: alt,
        audit_attributes: audit
          .split(/[\s,]+/)
          .map((a) => a.trim())
          .filter(Boolean),
        report_ref: report.trim() || null,
      }),
    {
      invalidate: [MODELS_KEY],
      success: "Evaluation recorded",
      onSuccess: () => {
        setRows({});
        setEvaluator("");
        onClose();
      },
    },
  );
  return (
    <Dialog
      isOpen={model !== null}
      onOpenChange={(o) => !o && onClose()}
      title={`Record an evaluation of ${model?.model_ref ?? ""}@${model?.version ?? ""}`}
      description="Rates from the evaluator's report, as fractions from 0 to 1, each with its 95% confidence interval. Supplier claims do not count."
      size="xl"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={record.isPending} isDisabled={!valid || !evaluator.trim() || Number.isNaN(Number(cohorts))} onPress={() => record.mutate(undefined)}>
            Record
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <TextField label="Evaluator" value={evaluator} onChange={setEvaluator} isRequired />
          <TextField label="Cohorts compared" value={cohorts} onChange={setCohorts} inputMode="numeric" />
          <TextField label="Report reference" value={report} onChange={setReport} />
        </div>
        <div className="space-y-2">
          {names.map((k) => (
            <div key={k} className="grid items-end gap-3 sm:grid-cols-4">
              <span className="font-mono text-sm">{k}</span>
              <TextField label={`${k} value`} value={row(k).value} onChange={setCell(k, "value")} inputMode="decimal" />
              <TextField label={`${k} CI low`} value={row(k).low} onChange={setCell(k, "low")} inputMode="decimal" />
              <TextField label={`${k} CI high`} value={row(k).high} onChange={setCell(k, "high")} inputMode="decimal" />
            </div>
          ))}
        </div>
        {!valid ? <p className="text-xs text-ink-muted">Each measure needs a value inside its interval, all between 0 and 1.</p> : null}
        <div className="flex flex-col gap-2">
          <Checkbox isSelected={independent} onChange={setIndependent}>
            The evaluator is independent of Zoiko and of the supplier
          </Checkbox>
          <Checkbox isSelected={alt} onChange={setAlt}>
            An accessible non-biometric alternative was confirmed
          </Checkbox>
          <Checkbox isSelected={flagged} onChange={setFlagged}>
            The evaluator flagged a disparity between cohorts
          </Checkbox>
        </div>
        <TextField
          label="Audit attributes"
          description="cohort labels used only for this fairness audit (they never become live features)"
          value={audit}
          onChange={setAudit}
        />
        {record.error ? <ApiErrorView error={record.error} /> : null}
      </div>
    </Dialog>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- list */

export function ModelsSection() {
  const q = useCellQuery(MODELS_KEY, (api) => api.privacy.v2.models());
  const [registering, setRegistering] = useState(false);
  const [evaluating, setEvaluating] = useState<ModelVersion | null>(null);
  const promote = useCellMutation((api, m: ModelVersion) => api.privacy.v2.promoteModel(m.model_ref, m.version), {
    invalidate: [MODELS_KEY],
    success: "Promoted: this version is now the one in service",
  });
  const cols: Column<ModelVersion>[] = [
    { key: "ref", header: "Model", pinned: true, render: (m) => <span className="font-mono text-xs">{`${m.model_ref}@${m.version}`}</span> },
    { key: "kind", header: "Kind", render: (m) => <span className="font-mono text-xs">{m.kind}</span> },
    { key: "state", header: "State", render: (m) => <StatusBadge state={m.state} kind="Model" /> },
    {
      key: "fairness",
      header: "Fairness",
      render: (m) => (
        <span className="flex flex-col gap-1">
          <Badge tone={FAIRNESS[m.fairness.state].tone}>{FAIRNESS[m.fairness.state].label}</Badge>
          {m.fairness.reasons.length ? <span className="font-mono text-2xs text-ink-muted">{m.fairness.reasons.join(", ")}</span> : null}
        </span>
      ),
    },
    { key: "measures", header: "Measures", render: (m) => <Measures m={m} /> },
    {
      key: "evaluated",
      header: "Evaluated",
      render: (m) =>
        m.latest_evaluation ? (
          <span className="text-xs">
            <DateTime value={m.latest_evaluation.created_at} style="date" /> · {m.latest_evaluation.evaluator_ref} · {m.latest_evaluation.cohorts} cohorts
          </span>
        ) : (
          "—"
        ),
    },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (m) => (
        <span className="inline-flex gap-1.5">
          <Button size="xs" variant="secondary" onPress={() => setEvaluating(m)} aria-label={`Record evaluation for ${m.model_ref}@${m.version}`}>
            Record evaluation
          </Button>
          {m.state === "REGISTERED" || m.state === "HELD" ? (
            <Button size="xs" isDisabled={m.fairness.state !== "PASS_WITH_LIMITS"} pending={promote.isPending} onPress={() => promote.mutate(m)} aria-label={`Promote ${m.model_ref}@${m.version}`}>
              Promote
            </Button>
          ) : null}
        </span>
      ),
    },
  ];
  return (
    <div className="space-y-5">
      <Banner tone="info" title="Never a pass">
        A version within its recorded operating points &quot;passes, with limits&quot;. Promotion needs an independent evaluation; a model whose provider reports another version is held.
      </Banner>
      <Card
        padded={false}
        title="Models and fairness"
        description="Each version's latest independent evaluation. A biometric method runs only on the ACTIVE version, and only while its evaluation holds."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setRegistering(true)}>
            Register version
          </Button>
        }
      >
        <QueryView query={q}>
          {(d) => <DataTable caption="Models" columns={cols} rows={d.items} rowKey={(m) => m.model_id} empty={<EmptyState icon={FlaskConical} title="No models registered" />} />}
        </QueryView>
        {promote.error ? <ApiErrorView error={promote.error} title="Not promoted" /> : null}
      </Card>
      <RegisterDialog isOpen={registering} onClose={() => setRegistering(false)} />
      <EvaluationDialog model={evaluating} onClose={() => setEvaluating(null)} />
    </div>
  );
}
