"use client";

import type { BreachCase, BreachIn } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { ShieldAlert } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { PRIVACY_KEY } from "./shared";

/** Breach cases and biometric containment (ZID-DTV-WF-04 B09; ZID-PRI-004 sections 09 and 24; CR-108). A case carries one regulator clock per
 * affected jurisdiction (counsel's hours; never guessed) and can pause biometric capture there at once: while it is paused, no face match or
 * liveness runs for this organization in those jurisdictions. Someone other than whoever paused it releases the pause, with a reason. */

const BREACH_KEY = [...PRIVACY_KEY, "breaches"];
type Severity = BreachIn["severity"];
const SEVERITIES: Severity[] = ["LOW", "MODERATE", "HIGH", "CRITICAL"];
/** CR-108 (proposed): HIGH and CRITICAL pause biometric capture unless the person opening the case says otherwise. */
export const pausesByDefault = (s: Severity) => s === "HIGH" || s === "CRITICAL";

const SEVERITY_TONE: Record<Severity, "neutral" | "info" | "warn" | "danger"> = { LOW: "neutral", MODERATE: "info", HIGH: "warn", CRITICAL: "danger" };

export const BREACH_ERROR_HELP: Record<string, string> = {
  RELEASED_BY_WHOEVER_PAUSED: "You paused capture on this case. A different person releases it.",
  CAPTURE_NOT_PAUSED: "Capture is not paused on this case.",
  CAPTURE_ALREADY_PAUSED_OR_RELEASED: "Capture was already paused on this case. A released pause is not re-applied on the same case.",
  BREACH_CLOSED: "This case is closed.",
  DATA_CLASS_INVALID: "Use data class codes only, such as biometric.face_template or identity.document. Never a name or other personal data.",
  NOTIFICATIONS_OUTSTANDING: "A regulator clock is still running or overdue. Record the notification before closing.",
  ALREADY_NOTIFIED: "This jurisdiction's notification is already recorded.",
};

function Err({ error }: { error: unknown }) {
  const code = (error as { code?: string } | null)?.code;
  if (code && BREACH_ERROR_HELP[code]) return <Banner tone="danger" title="Not done" live="assertive">{BREACH_ERROR_HELP[code]}</Banner>;
  return <ApiErrorView error={error} />;
}

export function CaptureBadge({ state }: { state: BreachCase["capture_state"] }) {
  if (state === "PAUSED") return <Badge tone="danger">Biometrics paused</Badge>;
  if (state === "RELEASED") return <Badge tone="neutral">Pause released</Badge>;
  return <Badge tone="neutral">Not paused</Badge>;
}

function Clocks({ c }: { c: BreachCase }) {
  return (
    <span className="flex flex-wrap gap-1">
      {c.clocks.map((k) => (
        <Badge key={k.jurisdiction} tone={k.state === "OVERDUE" ? "danger" : k.state === "RUNNING" ? "warn" : k.state === "NOTIFIED" ? "ok" : "neutral"}>
          {k.jurisdiction}: {k.state === "NOT_SET_BY_COUNSEL" ? "counsel pending" : k.state.toLowerCase()}
        </Badge>
      ))}
    </span>
  );
}

/** CR-113 (QA-034): data class codes, such as biometric.face_template. Never a name or other personal data. */
export const DATA_CLASS = /^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,62})?$/;
export const parseClasses = (raw: string) =>
  Array.from(new Set(raw.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean)));

function DataClasses({ c }: { c: BreachCase }) {
  const list = c.data_classes ?? [];
  if (list.length === 0) return <span className="text-xs text-ink-muted">Not recorded</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {list.map((d) => (
        <Badge key={d} tone="neutral">
          {d}
        </Badge>
      ))}
    </span>
  );
}

/* ---------------------------------------------------------------------------------------------------------------------------- open */

function OpenDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [summary, setSummary] = useState("");
  const [severity, setSeverity] = useState<Severity>("HIGH");
  const [jurisdictions, setJurisdictions] = useState("");
  const [estimate, setEstimate] = useState("");
  const [detected, setDetected] = useState("");
  const [pause, setPause] = useState<boolean | null>(null);
  const [classes, setClasses] = useState("");
  const dc = parseClasses(classes);
  const willPause = pause ?? pausesByDefault(severity);
  const js = jurisdictions
    .split(/[\s,]+/)
    .map((j) => j.trim().toUpperCase())
    .filter(Boolean);
  const open = useCellMutation(
    (api) =>
      api.privacy.v2.openBreach({
        summary: summary.trim(),
        severity,
        jurisdictions: js,
        subjects_estimate: estimate.trim() ? Number(estimate) : null,
        detected_at: new Date(detected).toISOString(),
        pause_biometric_capture: pause,
        data_classes: dc,
      }),
    {
      invalidate: [BREACH_KEY, [...PRIVACY_KEY, "biometric"]],
      success: willPause ? "Case opened: biometric capture is paused in its jurisdictions" : "Case opened",
      onSuccess: () => {
        setSummary("");
        setJurisdictions("");
        setEstimate("");
        setDetected("");
        setPause(null);
        setClasses("");
        onClose();
      },
    },
  );
  const ready = summary.trim().length >= 3 && js.length > 0 && js.every((j) => /^[A-Z]{2}$/.test(j)) && detected !== "" && !Number.isNaN(new Date(detected).getTime()) && dc.every((d) => DATA_CLASS.test(d));
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Open a breach case"
      description="Regulator clocks start from the detection time, per jurisdiction, from counsel's configuration."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant="danger" pending={open.isPending} isDisabled={!ready} onPress={() => open.mutate(undefined)}>
            Open case
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Summary" value={summary} onChange={setSummary} multiline rows={2} isRequired className="sm:col-span-2" />
        <Select label="Severity" value={severity} onChange={(v) => setSeverity((SEVERITIES.find((s) => s === v) ?? "HIGH") as Severity)} options={SEVERITIES.map((s) => ({ id: s, label: s }))} />
        <TextField label="Jurisdictions" description="country codes, e.g. GB, FR" value={jurisdictions} onChange={setJurisdictions} isRequired />
        <TextField label="Detected at" type="datetime-local" value={detected} onChange={setDetected} isRequired />
        <TextField label="People affected (estimate)" value={estimate} onChange={setEstimate} inputMode="numeric" />
        <TextField
          label="Affected data classes"
          description="codes, e.g. biometric.face_template, identity.document. More can be added later; none is ever removed."
          value={classes}
          onChange={setClasses}
          className="sm:col-span-2"
          mono
        />
        <div className="sm:col-span-2">
          <Checkbox isSelected={willPause} onChange={setPause}>
            Pause biometric capture in these jurisdictions now
          </Checkbox>
          <p className="mt-1 text-xs text-ink-muted">
            {pause === null
              ? `Default for ${severity}: ${pausesByDefault(severity) ? "paused" : "not paused"}.`
              : "You chose this explicitly."}{" "}
            While paused, no face match or liveness runs for your organization there.
          </p>
        </div>
      </div>
      {open.error ? <Err error={open.error} /> : null}
    </Dialog>
  );
}

/* ---------------------------------------------------------------------------------------------------------------------------- actions */

function ReleaseDialog({ c, onClose }: { c: BreachCase | null; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const release = useCellMutation((api, x: BreachCase) => api.privacy.v2.releaseCapture(x.breach_id, reason.trim()), {
    invalidate: [BREACH_KEY, [...PRIVACY_KEY, "biometric"]],
    success: "Pause released: biometric methods follow their other gates again",
    onSuccess: () => {
      setReason("");
      onClose();
    },
  });
  return (
    <Dialog
      isOpen={c !== null}
      onOpenChange={(o) => !o && onClose()}
      title="Release the biometric pause"
      description="Only someone other than whoever paused it. The case itself stays open."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={release.isPending} isDisabled={reason.trim().length < 10} onPress={() => c && release.mutate(c)}>
            Release
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Why it is safe again" description="at least 10 characters" value={reason} onChange={setReason} multiline rows={2} isRequired />
        {release.error ? <Err error={release.error} /> : null}
      </div>
    </Dialog>
  );
}

function NotifyDialog({ c, onClose }: { c: BreachCase | null; onClose: () => void }) {
  const pending = c?.clocks.filter((k) => k.state !== "NOTIFIED").map((k) => k.jurisdiction) ?? [];
  const [j, setJ] = useState("");
  const [at, setAt] = useState("");
  const [ref, setRef] = useState("");
  const chosen = pending.includes(j) ? j : (pending[0] ?? "");
  const notify = useCellMutation(
    (api, x: BreachCase) => api.privacy.v2.notifyBreach(x.breach_id, { jurisdiction: chosen, notified_at: new Date(at).toISOString(), reference: ref.trim() }),
    {
      invalidate: [BREACH_KEY],
      success: "Notification recorded",
      onSuccess: () => {
        setAt("");
        setRef("");
        onClose();
      },
    },
  );
  return (
    <Dialog
      isOpen={c !== null}
      onOpenChange={(o) => !o && onClose()}
      title="Record a regulator notification"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={notify.isPending} isDisabled={!chosen || !at || ref.trim().length < 2} onPress={() => c && notify.mutate(c)}>
            Record
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Select label="Jurisdiction" value={chosen} onChange={setJ} options={pending.map((p) => ({ id: p, label: p }))} />
        <TextField label="Notified at" type="datetime-local" value={at} onChange={setAt} isRequired />
        <TextField label="Regulator reference" value={ref} onChange={setRef} isRequired />
        {notify.error ? <Err error={notify.error} /> : null}
      </div>
    </Dialog>
  );
}

function DataClassesDialog({ c, onClose }: { c: BreachCase | null; onClose: () => void }) {
  const [raw, setRaw] = useState("");
  const dc = parseClasses(raw);
  const add = useCellMutation((api, x: BreachCase) => api.privacy.v2.addBreachDataClasses(x.breach_id, dc), {
    invalidate: [BREACH_KEY],
    success: "Data classes added to the case",
    onSuccess: () => {
      setRaw("");
      onClose();
    },
  });
  return (
    <Dialog
      isOpen={c !== null}
      onOpenChange={(o) => !o && onClose()}
      title="Add affected data classes"
      description="What the investigation found the incident touched. Classes are added, never removed."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={add.isPending} isDisabled={dc.length === 0 || !dc.every((d) => DATA_CLASS.test(d))} onPress={() => c && add.mutate(c)}>
            Add
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {c ? (
          <p className="text-sm">
            Recorded now: <DataClasses c={c} />
          </p>
        ) : null}
        <TextField label="Data classes" description="codes, e.g. biometric.live_selfie" value={raw} onChange={setRaw} mono isRequired />
        {add.error ? <Err error={add.error} /> : null}
      </div>
    </Dialog>
  );
}

/* ---------------------------------------------------------------------------------------------------------------------------- list */

export function BreachesSection() {
  const [openOnly, setOpenOnly] = useState(true);
  const q = useCellQuery([...BREACH_KEY, openOnly], (api) => api.privacy.v2.breaches(openOnly));
  const [opening, setOpening] = useState(false);
  const [releasing, setReleasing] = useState<BreachCase | null>(null);
  const [notifying, setNotifying] = useState<BreachCase | null>(null);
  const [classing, setClassing] = useState<BreachCase | null>(null);
  const pause = useCellMutation((api, c: BreachCase) => api.privacy.v2.pauseCapture(c.breach_id), {
    invalidate: [BREACH_KEY, [...PRIVACY_KEY, "biometric"]],
    success: "Biometric capture paused in this case's jurisdictions",
  });
  const close = useCellMutation((api, c: BreachCase) => api.privacy.v2.closeBreach(c.breach_id), { invalidate: [BREACH_KEY, [...PRIVACY_KEY, "biometric"]], success: "Case closed" });
  const [suspending, setSuspending] = useState(false);
  const dossier = useCellMutation((api, c: BreachCase) => api.privacy.v2.breachDossier(c.breach_id), {
    onSuccess: (d, c) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }));
      a.download = `breach-${c.breach_id}-dossier.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    },
  });
  const cols: Column<BreachCase>[] = [
    { key: "detected", header: "Detected", pinned: true, render: (c) => <DateTime value={c.detected_at} /> },
    { key: "summary", header: "Summary", render: (c) => <span className="text-sm">{c.summary}</span> },
    { key: "severity", header: "Severity", render: (c) => <Badge tone={SEVERITY_TONE[c.severity]}>{c.severity}</Badge> },
    { key: "state", header: "State", render: (c) => <StatusBadge state={c.state} kind="Breach case" /> },
    { key: "capture", header: "Biometrics", render: (c) => <CaptureBadge state={c.capture_state} /> },
    { key: "classes", header: "Data classes", render: (c) => <DataClasses c={c} /> },
    { key: "clocks", header: "Regulator clocks", render: (c) => <Clocks c={c} /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (c) =>
        c.state === "CLOSED" ? null : (
          <span className="inline-flex flex-wrap justify-end gap-1.5">
            {c.capture_state === "NOT_PAUSED" ? (
              <Button size="xs" variant="danger" pending={pause.isPending} onPress={() => pause.mutate(c)} aria-label={`Pause biometrics for case detected ${c.detected_at}`}>
                Pause biometrics
              </Button>
            ) : null}
            {c.capture_state === "PAUSED" ? (
              <Button size="xs" variant="secondary" onPress={() => setReleasing(c)} aria-label={`Release biometric pause for case detected ${c.detected_at}`}>
                Release pause
              </Button>
            ) : null}
            {c.clocks.some((k) => k.state !== "NOTIFIED") ? (
              <Button size="xs" variant="secondary" onPress={() => setNotifying(c)}>
                Record notification
              </Button>
            ) : null}
            <Button size="xs" variant="secondary" onPress={() => setClassing(c)} aria-label={`Add data classes for case detected ${c.detected_at}`}>
              Add data classes
            </Button>
            <Button size="xs" variant="secondary" pending={close.isPending} onPress={() => close.mutate(c)}>
              Close
            </Button>
            <Button size="xs" variant="secondary" pending={dossier.isPending} onPress={() => dossier.mutate(c)} aria-label={`Export dossier for case detected ${c.detected_at}`}>
              Dossier
            </Button>
          </span>
        ),
    },
  ];
  return (
    <div className="space-y-5">
      <Card
        padded={false}
        title="Breach cases"
        description="Incidents with their regulator clocks. Pausing biometrics stops face match and liveness for your organization in the case's jurisdictions at once."
        actions={
          <span className="flex items-center gap-3">
            <Checkbox isSelected={openOnly} onChange={setOpenOnly}>
              Open only
            </Checkbox>
            <Button size="sm" variant="secondary" onPress={() => setSuspending(true)}>
              Suspend a processor
            </Button>
            <Button size="sm" variant="danger" onPress={() => setOpening(true)}>
              Open a case
            </Button>
          </span>
        }
      >
        <QueryView query={q}>
          {(d) => <DataTable caption="Breach cases" columns={cols} rows={d.items} rowKey={(c) => c.breach_id} empty={<EmptyState icon={ShieldAlert} title="No breach cases" />} />}
        </QueryView>
        {pause.error ? <Err error={pause.error} /> : null}
        {close.error ? <Err error={close.error} /> : null}
        {dossier.error ? <Err error={dossier.error} /> : null}
      </Card>
      <OpenDialog isOpen={opening} onClose={() => setOpening(false)} />
      <ReleaseDialog c={releasing} onClose={() => setReleasing(null)} />
      <NotifyDialog c={notifying} onClose={() => setNotifying(null)} />
      <DataClassesDialog c={classing} onClose={() => setClassing(null)} />
      <SuspendProcessorDialog isOpen={suspending} onClose={() => setSuspending(false)} />
    </div>
  );
}

/** B09 containment (CR-110): revoke a connector by suspending the processor it sends to. Transfers to a suspended processor are refused. */
function SuspendProcessorDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [id, setId] = useState("");
  const suspend = useCellMutation((api) => api.privacy.suspendProcessor(id.trim()), {
    invalidate: [[...PRIVACY_KEY, "processors"], [...PRIVACY_KEY, "transfers"]],
    success: "Processor suspended: transfers to it are refused",
    onSuccess: () => {
      setId("");
      onClose();
    },
  });
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Suspend a processor"
      description="Containment: every transfer to this processor is refused from now. It is re-approved on the Processors tab."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant="danger" pending={suspend.isPending} isDisabled={!id.trim()} onPress={() => suspend.mutate(undefined)}>
            Suspend
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Processor ID" value={id} onChange={setId} mono isRequired />
        {suspend.error ? <Err error={suspend.error} /> : null}
      </div>
    </Dialog>
  );
}
