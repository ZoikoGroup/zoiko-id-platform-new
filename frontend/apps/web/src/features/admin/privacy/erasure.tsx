"use client";

import type { ErasureClassState, ErasureJob, ErasurePlanItem, ErasureScope, ErasureTask } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Eraser, Search } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { PRIVACY_KEY } from "./shared";

/** Biometric erasure console (ZID-DTV-WF-04 B05; ZID-PRI-004 sections 12 and 13; CR-108). A dry run shows what an erasure would do, in deletion
 * order (raw media, then templates, then results, then other data; providers' copies; backups last), without doing it. A job is complete only when
 * every task is acknowledged; each biometric class says whether it is purged. Backups are attested only once nothing else is outstanding. */

const ERASURE_KEY = [...PRIVACY_KEY, "erasure"];
const SCOPES: { id: ErasureScope; label: string }[] = [
  { id: "ALL", label: "Everything (first party, providers, backups)" },
  { id: "FIRST_PARTY_ONLY", label: "First-party stores only" },
  { id: "PROVIDERS_ONLY", label: "Providers only" },
];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const ACTION_LABEL: Record<string, { label: string; tone: "ok" | "info" | "warn" | "danger" | "neutral" }> = {
  DELETE: { label: "Delete", tone: "info" },
  CRYPTO_SHRED: { label: "Crypto-shred", tone: "info" },
  ANONYMIZE: { label: "Anonymize", tone: "info" },
  ARCHIVE_RESTRICTED: { label: "Archive (restricted)", tone: "warn" },
  HOLD: { label: "Kept: hold", tone: "warn" },
  PROVIDER_DELETION: { label: "Provider deletes", tone: "info" },
  ALREADY_DISPOSED: { label: "Already disposed", tone: "neutral" },
  OUT_OF_SCOPE: { label: "Out of scope", tone: "neutral" },
  CANNOT_DISPOSE: { label: "Cannot dispose", tone: "danger" },
};

const CLASS_TONE: Record<ErasureClassState["state"], "ok" | "warn" | "danger" | "neutral"> = { PURGED: "ok", PENDING: "neutral", HELD: "warn", FAILED: "danger" };

export function ClassStates({ classes }: { classes: ErasureClassState[] }) {
  if (!classes.length) return <span className="text-ink-subtle">No biometric data</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {classes.map((c) => (
        <Badge key={c.asset_class} tone={CLASS_TONE[c.state]}>
          <span className="font-mono">{c.asset_class.replace("biometric.", "")}</span>: {c.state.toLowerCase()}
        </Badge>
      ))}
    </span>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- dry run */

function Preview() {
  const [subject, setSubject] = useState("");
  const [scope, setScope] = useState<ErasureScope>("ALL");
  const run = useCellMutation((api) => api.privacy.v2.previewErasure(subject.trim(), scope));
  const cols: Column<ErasurePlanItem>[] = [
    { key: "order", header: "#", pinned: true, render: (r) => String((run.data?.plan.indexOf(r) ?? 0) + 1) },
    { key: "target", header: "Object", render: (r) => <span className="font-mono text-xs">{r.target}</span> },
    { key: "class", header: "Biometric class", render: (r) => (r.asset_class ? <span className="font-mono text-xs">{r.asset_class}</span> : "—") },
    {
      key: "action",
      header: "Would",
      render: (r) => {
        const a = ACTION_LABEL[r.action] ?? { label: r.action, tone: "neutral" as const };
        return <Badge tone={a.tone}>{a.label}</Badge>;
      },
    },
    { key: "why", header: "Detail", render: (r) => <span className="text-xs">{r.why ?? (r.provider ? `${r.provider}${r.sla_days != null ? `, ${r.sla_days} days` : ""}` : "")}</span> },
  ];
  return (
    <Card
      padded={false}
      title="Dry run"
      description="What an erasure would do now, in deletion order. Nothing is deleted."
      actions={
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            run.mutate(undefined);
          }}
        >
          <TextField label="Subject principal ID" value={subject} onChange={setSubject} mono />
          <Select label="Scope" value={scope} onChange={(v) => setScope((SCOPES.find((s) => s.id === v)?.id ?? "ALL") as ErasureScope)} options={SCOPES} />
          <Button size="sm" variant="secondary" type="submit" icon={<Search aria-hidden className="size-4" />} isDisabled={!UUID.test(subject.trim())} pending={run.isPending}>
            Preview
          </Button>
        </form>
      }
    >
      {run.error ? <ApiErrorView error={run.error} /> : null}
      {run.data ? (
        <div className="space-y-2 pb-3">
          <Banner tone="info">{run.data.statement}</Banner>
          <DataTable caption="Erasure plan" columns={cols} rows={run.data.plan} rowKey={(r) => r.target} empty={<EmptyState icon={Search} title="No recorded personal data for this subject" />} />
          {run.data.backups ? (
            <p className="px-5 text-xs text-ink-muted">
              Backups are attested last{run.data.backups.window_days != null ? `, within ${run.data.backups.window_days} days` : " (no backup window set by the DPO yet)"}.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="px-5 py-4 text-sm text-ink-muted">Enter a subject to preview.</p>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- jobs */

function OpenJob({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [subject, setSubject] = useState("");
  const [scope, setScope] = useState<ErasureScope>("ALL");
  const [reason, setReason] = useState("");
  const [basis, setBasis] = useState("");
  const open = useCellMutation((api) => api.privacy.v2.openErasureJob({ subject_ref: subject.trim(), scope, reason: reason.trim(), legal_basis: basis.trim() }), {
    invalidate: [ERASURE_KEY],
    success: "Erasure job opened",
    onSuccess: () => {
      setSubject("");
      setReason("");
      setBasis("");
      onClose();
    },
  });
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Open an erasure job"
      description="First-party stores are disposed now, in deletion order; held data stays held; providers and backups each get a task. Run a dry run first."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant="danger" pending={open.isPending} isDisabled={!UUID.test(subject.trim()) || reason.trim().length < 3 || basis.trim().length < 3} onPress={() => open.mutate(undefined)}>
            Erase
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Subject principal ID" value={subject} onChange={setSubject} mono isRequired />
        <Select label="Scope" value={scope} onChange={(v) => setScope((SCOPES.find((s) => s.id === v)?.id ?? "ALL") as ErasureScope)} options={SCOPES} />
        <TextField label="Reason" description="e.g. the rights request it fulfils" value={reason} onChange={setReason} isRequired />
        <TextField label="Legal basis" value={basis} onChange={setBasis} isRequired />
      </div>
      {open.error ? <ApiErrorView error={open.error} /> : null}
    </Dialog>
  );
}

function AttestDialog({ job, task, onClose }: { job: ErasureJob | null; task: ErasureTask | null; onClose: () => void }) {
  const [statement, setStatement] = useState("");
  const attest = useCellMutation((api) => api.privacy.v2.attestBackup(job!.job_id, task!.task_id, statement.trim()), {
    invalidate: [ERASURE_KEY],
    success: "Backup erasure attested",
    onSuccess: () => {
      setStatement("");
      onClose();
    },
  });
  const code = (attest.error as { code?: string } | null)?.code;
  return (
    <Dialog
      isOpen={task !== null}
      onOpenChange={(o) => !o && onClose()}
      title="Attest the backup erasure"
      description="Backups are attested last: only once every first-party and provider task is acknowledged or held."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={attest.isPending} isDisabled={statement.trim().length < 3} onPress={() => attest.mutate(undefined)}>
            Attest
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Statement" description="what was purged from which backups, and when" value={statement} onChange={setStatement} multiline rows={3} isRequired />
        {code === "BACKUP_BEFORE_SOURCES" ? (
          <Banner tone="danger" title="Not yet" live="assertive">
            A provider or first-party deletion is still outstanding. Backups are attested only after those are settled.
          </Banner>
        ) : attest.error ? (
          <ApiErrorView error={attest.error} />
        ) : null}
      </div>
    </Dialog>
  );
}

export function ErasureSection() {
  const q = useCellQuery(ERASURE_KEY, (api) => api.privacy.v2.erasureJobs());
  const [opening, setOpening] = useState(false);
  const [attesting, setAttesting] = useState<{ job: ErasureJob; task: ErasureTask } | null>(null);
  const cols: Column<ErasureJob>[] = [
    { key: "created", header: "Opened", pinned: true, render: (j) => <DateTime value={j.created_at} /> },
    { key: "subject", header: "Subject", render: (j) => <span className="font-mono text-xs">{j.subject_ref.slice(0, 8)}…</span> },
    { key: "state", header: "Job", render: (j) => <StatusBadge state={j.state} kind="Erasure job" /> },
    { key: "classes", header: "Biometric classes", render: (j) => <ClassStates classes={j.biometric_classes} /> },
    {
      key: "outstanding",
      header: "Outstanding",
      render: (j) => {
        const open = j.tasks.filter((t) => t.state !== "ACKNOWLEDGED");
        return open.length ? (
          <span className="flex flex-wrap gap-1">
            {open.map((t) => (
              <Badge key={t.task_id} tone={t.state === "HELD" ? "warn" : t.state === "FAILED" ? "danger" : "neutral"}>
                {t.target_kind.toLowerCase().replaceAll("_", " ")}: {t.target_ref} ({t.state.toLowerCase()})
                {t.failure === "RETRIES_EXHAUSTED" ? `, no receipt after ${t.attempts ?? 0} requests: alerted` : t.attempts ? `, asked again ${t.attempts}×` : ""}
              </Badge>
            ))}
          </span>
        ) : (
          "None"
        );
      },
    },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (j) => {
        const backup = j.tasks.find((t) => t.target_kind === "BACKUP" && (t.state === "PENDING" || t.state === "FAILED"));
        return backup ? (
          <Button size="xs" variant="secondary" onPress={() => setAttesting({ job: j, task: backup })}>
            Attest backups
          </Button>
        ) : null;
      },
    },
  ];
  return (
    <div className="space-y-5">
      <Preview />
      <Card
        padded={false}
        title="Erasure jobs"
        description="Complete only when every task is acknowledged. A biometric class is purged only when its stores, its providers and the backups are."
        actions={
          <Button size="sm" variant="danger" icon={<Eraser aria-hidden className="size-4" />} onPress={() => setOpening(true)}>
            Open erasure job
          </Button>
        }
      >
        <QueryView query={q}>
          {(d) => <DataTable caption="Erasure jobs" columns={cols} rows={d.items} rowKey={(j) => j.job_id} empty={<EmptyState icon={Eraser} title="No erasure jobs" />} />}
        </QueryView>
      </Card>
      <OpenJob isOpen={opening} onClose={() => setOpening(false)} />
      <AttestDialog job={attesting?.job ?? null} task={attesting?.task ?? null} onClose={() => setAttesting(null)} />
    </div>
  );
}
