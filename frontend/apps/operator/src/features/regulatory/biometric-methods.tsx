"use client";

import type { BiometricMethod, BiometricMethodVersion, BiometricProposal, BiometricScope, BiometricState, BiometricStepDownIn } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, TextField, type Column, type Tone } from "@zoiko/ui";
import { Fingerprint, ScanFace } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { COUNTRY_OPTIONS, Mono, opt, REG, RegError } from "./common";

/** Biometric method lifecycle and kill switch (ZID-DTV-WF-04 section 06; CR-107). A step up is proposed and approved by two people, one step at a
 * time; a step down (pause, lower state, retire) is one person and takes effect at once. Approval is not activation: a live session also needs the
 * tenant's own approved DPIA, retention for every biometric asset class, the country overlay and no breach pause. */

type Row = { current: BiometricMethodVersion; versions: BiometricMethodVersion[] };
type Target = BiometricProposal["target"];

const STATE_TONE: Record<BiometricState, Tone> = {
  OFF: "neutral",
  PAUSED: "danger",
  LAB_ONLY: "info",
  PILOT_GATED: "warn",
  PRODUCTION_ELIGIBLE: "ok",
};
const STATE_LABEL: Record<BiometricState, string> = {
  OFF: "Off",
  PAUSED: "Paused",
  LAB_ONLY: "Lab only",
  PILOT_GATED: "Pilot",
  PRODUCTION_ELIGIBLE: "Production eligible",
};
/** The one step up from each state; a resume from PAUSED starts again at LAB_ONLY. */
export const NEXT_UP: Partial<Record<BiometricState, Target>> = {
  OFF: "LAB_ONLY",
  PAUSED: "LAB_ONLY",
  LAB_ONLY: "PILOT_GATED",
  PILOT_GATED: "PRODUCTION_ELIGIBLE",
};
const RANK: Record<BiometricState, number> = { OFF: 0, PAUSED: 0, LAB_ONLY: 1, PILOT_GATED: 2, PRODUCTION_ELIGIBLE: 3 };

/** The step-down targets a scope in this state may move to now. */
export function stepDownTargets(state: BiometricState, pilotTenant: string | null): BiometricStepDownIn["target"][] {
  if (state === "OFF") return [];
  if (state === "PAUSED") return ["OFF"];
  const lower = (["PILOT_GATED", "LAB_ONLY"] as const).filter((s) => RANK[s] < RANK[state] && (s !== "PILOT_GATED" || pilotTenant !== null));
  return ["PAUSED", ...lower, "OFF"];
}

export const METHOD_ERROR_HELP: Record<string, string> = {
  METHOD_APPROVED_BY_PROPOSER: "You proposed this step up. A different person approves it.",
  METHOD_PROPOSAL_STALE: "The scope moved since this was proposed (for example it was paused). Reject it and propose again from the current state.",
  METHOD_PROPOSAL_EXPIRED: "This proposal lapsed. Propose again.",
  METHOD_PROPOSAL_OPEN: "This scope already has an open proposal. Decide that one first.",
  METHOD_STEP_NOT_ALLOWED: "Only one step up at a time: OFF or PAUSED to Lab only, then Pilot, then Production eligible.",
  METHOD_PILOT_NEEDS_TENANT: "A pilot names the one tenant it runs for.",
  METHOD_DPIA_REFERENCE_REQUIRED: "Pilot and production need the DPIA reference.",
  METHOD_EVALUATION_REFERENCE_REQUIRED: "Pilot and production need the independent evaluation reference.",
  METHOD_SCOPE_ID_REQUIRED: "Name the new scope (lowercase letters, digits and hyphens).",
  METHOD_SCOPE_ID_TAKEN: "This exact scope already has an id. Propose against that scope.",
  METHOD_STEP_NOT_DOWN: "That is not a step down. A step up needs a proposal.",
};

export function scopeText(s: BiometricScope): string {
  return [`${s.engine_id}@${s.engine_version}`, s.country, s.purpose, s.pilot_tenant ? `tenant ${s.pilot_tenant.slice(0, 8)}…` : "every tenant"].join(" · ");
}

export function StateBadge({ state }: { state: BiometricState }) {
  return <Badge tone={STATE_TONE[state]}>{STATE_LABEL[state]}</Badge>;
}

function MethodLabel({ method }: { method: BiometricMethod }) {
  return <span>{method === "FACE_MATCH" ? "Face match" : "Liveness"}</span>;
}

function MethodErr({ error }: { error: unknown }) {
  const code = (error as { code?: string } | null)?.code;
  if (code && METHOD_ERROR_HELP[code]) {
    return (
      <Banner tone="danger" title="Not done" live="assertive">
        {METHOD_ERROR_HELP[code]}
      </Banner>
    );
  }
  return <RegError error={error} />;
}

const REF_OK = (v: string) => /^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$/.test(v.trim());

/* ------------------------------------------------------------------------------------------------------------------------------ propose */

function ProposeDialog({ from, isOpen, onClose }: { from: BiometricMethodVersion | null; isOpen: boolean; onClose: () => void }) {
  const blank = {
    scope_id: "",
    method: "FACE_MATCH" as BiometricMethod,
    engine_id: "",
    engine_version: "",
    country: "GB",
    purpose: "IDENTITY_VERIFICATION",
    pilot_tenant: "",
    dpia_ref: "",
    evaluation_ref: "",
    reason: "",
    evidence_ref: "",
  };
  const [f, setF] = useState(blank);
  const set = (k: keyof typeof blank) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const scope: BiometricScope = from
    ? from.scope
    : {
        method: f.method,
        engine_id: f.engine_id.trim(),
        engine_version: f.engine_version.trim(),
        country: f.country,
        purpose: f.purpose.trim().toUpperCase(),
        pilot_tenant: opt(f.pilot_tenant) ?? null,
      };
  const target: Target = (from ? NEXT_UP[from.state] : "LAB_ONLY") ?? "LAB_ONLY";
  const needsEvidence = target !== "LAB_ONLY";
  const propose = useCellMutation(
    (api) =>
      api.regulatory.proposeBiometricMethod({
        scope_id: from ? undefined : f.scope_id.trim(),
        scope,
        target,
        dpia_ref: opt(f.dpia_ref) ?? null,
        evaluation_ref: opt(f.evaluation_ref) ?? null,
        reason: f.reason.trim(),
        evidence_ref: f.evidence_ref.trim(),
      }),
    {
      invalidate: [REG.biometricMethods],
      success: "Proposed: a different person must approve it",
      onSuccess: () => {
        setF(blank);
        onClose();
      },
    },
  );
  const ready =
    (from !== null || (/^[a-z0-9][a-z0-9-]{0,127}$/.test(f.scope_id.trim()) && scope.engine_id && scope.engine_version)) &&
    f.reason.trim().length >= 10 &&
    REF_OK(f.evidence_ref) &&
    (!needsEvidence || (REF_OK(f.dpia_ref) && REF_OK(f.evaluation_ref))) &&
    (target !== "PILOT_GATED" || scope.pilot_tenant);
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={from ? `Propose ${from.scope_id}: ${STATE_LABEL[from.state]} → ${STATE_LABEL[target]}` : "Propose a new biometric method scope (Lab only)"}
      description="A proposal changes nothing. A different person approves it, and only while the scope is still in the state it was proposed from."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button onPress={() => propose.mutate(undefined)} pending={propose.isPending} isDisabled={!ready}>
            Propose
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {from ? (
          <p className="text-sm sm:col-span-2">
            <MethodLabel method={from.scope.method} /> · {scopeText(from.scope)}
          </p>
        ) : (
          <>
            <TextField label="Scope ID" description="lowercase, e.g. face-gb-acme-2-1" value={f.scope_id} onChange={set("scope_id")} mono isRequired />
            <Select
              label="Method"
              value={f.method}
              onChange={(v) => setF((p) => ({ ...p, method: v === "LIVENESS" ? "LIVENESS" : "FACE_MATCH" }))}
              options={[
                { id: "FACE_MATCH", label: "Face match (1:1)" },
                { id: "LIVENESS", label: "Liveness (presentation-attack detection)" },
              ]}
            />
            <TextField label="Engine ID" description="as in the engine gate, e.g. deepface-arcface" value={f.engine_id} onChange={set("engine_id")} mono isRequired />
            <TextField label="Engine version" description="the exact model version" value={f.engine_version} onChange={set("engine_version")} mono isRequired />
            <Select label="Country" value={f.country} onChange={set("country")} options={COUNTRY_OPTIONS} />
            <TextField label="Purpose" value={f.purpose} onChange={set("purpose")} mono isRequired />
            <TextField
              label="Pilot tenant"
              description="blank = every tenant; a pilot later needs one tenant"
              value={f.pilot_tenant}
              onChange={set("pilot_tenant")}
              mono
              className="sm:col-span-2"
            />
          </>
        )}
        {needsEvidence ? (
          <>
            <TextField label="DPIA reference" value={f.dpia_ref} onChange={set("dpia_ref")} mono isRequired />
            <TextField label="Independent evaluation reference" value={f.evaluation_ref} onChange={set("evaluation_ref")} mono isRequired />
          </>
        ) : null}
        <TextField label="Evidence reference" description="ticket, report or decision" value={f.evidence_ref} onChange={set("evidence_ref")} isRequired />
        <TextField label="Reason" description="at least 10 characters" value={f.reason} onChange={set("reason")} multiline rows={2} isRequired className="sm:col-span-2" />
      </div>
      {propose.error ? <MethodErr error={propose.error} /> : null}
    </Dialog>
  );
}

/* ------------------------------------------------------------------------------------------------------------------------------ decide */

function DecideDialog({ proposal, approve, onClose }: { proposal: BiometricProposal | null; approve: boolean; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const decide = useCellMutation(
    async (api, p: BiometricProposal): Promise<unknown> =>
      approve ? api.regulatory.approveBiometricProposal(p.proposal_id, reason.trim()) : api.regulatory.rejectBiometricProposal(p.proposal_id, reason.trim()),
    {
      invalidate: [REG.biometricMethods],
      success: approve ? "Approved: the scope moved one step up" : "Proposal rejected",
      onSuccess: () => {
        setReason("");
        onClose();
      },
    },
  );
  return (
    <Dialog
      isOpen={proposal !== null}
      onOpenChange={(o) => !o && onClose()}
      title={`${approve ? "Approve" : "Reject"} ${proposal?.scope_id ?? ""} → ${proposal ? STATE_LABEL[proposal.target] : ""}`}
      description={approve ? "You must not be the proposer. Approval is not activation: the tenant's DPIA, retention and country overlay still decide each session." : undefined}
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant={approve ? "primary" : "danger"} onPress={() => proposal && decide.mutate(proposal)} pending={decide.isPending} isDisabled={reason.trim().length < 10}>
            {approve ? "Approve" : "Reject"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {proposal ? (
          <p className="text-sm text-ink-muted">
            Proposed by <Mono>{proposal.proposed_by}</Mono>: {proposal.reason}
          </p>
        ) : null}
        <TextField label="Reason" description="at least 10 characters" value={reason} onChange={setReason} multiline rows={2} isRequired />
        {decide.error ? <MethodErr error={decide.error} /> : null}
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------------------------------------------------------------------ step down */

function StepDownDialog({ scope, onClose }: { scope: BiometricMethodVersion | null; onClose: () => void }) {
  const targets = scope ? stepDownTargets(scope.state, scope.scope.pilot_tenant) : [];
  const [target, setTarget] = useState<BiometricStepDownIn["target"]>("PAUSED");
  const [reason, setReason] = useState("");
  const [ref, setRef] = useState("");
  const chosen = targets.includes(target) ? target : (targets[0] ?? "OFF");
  const step = useCellMutation(
    (api, s: BiometricMethodVersion) => api.regulatory.stepDownBiometricMethod(s.scope_id, { target: chosen, reason: reason.trim(), evidence_ref: ref.trim() }),
    {
      invalidate: [REG.biometricMethods],
      success: "Done: it takes effect for new sessions now",
      onSuccess: () => {
        setReason("");
        setRef("");
        onClose();
      },
    },
  );
  return (
    <Dialog
      isOpen={scope !== null}
      onOpenChange={(o) => !o && onClose()}
      title={`Step down ${scope?.scope_id ?? ""}`}
      description="Takes effect at once, by you alone. A resume from Paused is a new two-person approval starting at Lab only."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant="danger" onPress={() => scope && step.mutate(scope)} pending={step.isPending} isDisabled={reason.trim().length < 10 || !REF_OK(ref)}>
            {chosen === "PAUSED" ? "Pause now" : chosen === "OFF" ? "Retire" : "Step down"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Select
          label="New state"
          value={chosen}
          onChange={(v) => setTarget((targets.find((t) => t === v) ?? "PAUSED") as BiometricStepDownIn["target"])}
          options={targets.map((t) => ({ id: t, label: STATE_LABEL[t] }))}
        />
        <TextField label="Evidence reference" description="incident, ticket or report" value={ref} onChange={setRef} isRequired />
        <TextField label="Reason" description="at least 10 characters" value={reason} onChange={setReason} multiline rows={2} isRequired />
        {step.error ? <MethodErr error={step.error} /> : null}
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------------------------------------------------------------------ page */

export function BiometricMethodsPage() {
  const q = useCellQuery(REG.biometricMethods, (api) => api.regulatory.biometricMethods());
  const [proposing, setProposing] = useState<{ from: BiometricMethodVersion | null } | null>(null);
  const [deciding, setDeciding] = useState<{ p: BiometricProposal; approve: boolean } | null>(null);
  const [stepping, setStepping] = useState<BiometricMethodVersion | null>(null);

  const scopeCols: Column<Row>[] = [
    { key: "id", header: "Scope", pinned: true, render: (r) => <Mono>{r.current.scope_id}</Mono>, sortValue: (r) => r.current.scope_id },
    { key: "method", header: "Method", render: (r) => <MethodLabel method={r.current.scope.method} /> },
    { key: "state", header: "State", render: (r) => <StateBadge state={r.current.state} /> },
    { key: "scope", header: "Engine · country · purpose · tenant", render: (r) => scopeText(r.current.scope) },
    { key: "version", header: "Version", render: (r) => `v${r.current.version}` },
    { key: "since", header: "Since", render: (r) => <DateTime value={r.current.effective_at} /> },
    { key: "by", header: "By", render: (r) => <Mono>{r.current.recorded_by}</Mono> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (r) => (
        <span className="inline-flex gap-1.5">
          {NEXT_UP[r.current.state] ? (
            <Button size="xs" variant="secondary" onPress={() => setProposing({ from: r.current })} aria-label={`Propose next step for ${r.current.scope_id}`}>
              Propose {STATE_LABEL[NEXT_UP[r.current.state]!]}
            </Button>
          ) : null}
          {stepDownTargets(r.current.state, r.current.scope.pilot_tenant).length ? (
            <Button size="xs" variant="danger" onPress={() => setStepping(r.current)} aria-label={`Step down ${r.current.scope_id}`}>
              Pause / step down
            </Button>
          ) : null}
        </span>
      ),
    },
  ];
  const proposalCols: Column<BiometricProposal>[] = [
    { key: "id", header: "Scope", pinned: true, render: (p) => <Mono>{p.scope_id}</Mono> },
    { key: "method", header: "Method", render: (p) => <MethodLabel method={p.scope.method} /> },
    { key: "step", header: "Step", render: (p) => <StateBadge state={p.target} /> },
    { key: "scope", header: "Scope", render: (p) => scopeText(p.scope) },
    { key: "refs", header: "DPIA / evaluation", render: (p) => <Mono>{[p.dpia_ref, p.evaluation_ref].filter(Boolean).join(" · ") || "-"}</Mono> },
    { key: "by", header: "Proposed by", render: (p) => <Mono>{p.proposed_by}</Mono> },
    { key: "at", header: "When", render: (p) => <DateTime value={p.proposed_at} /> },
    {
      key: "actions",
      header: "Decide",
      align: "end",
      render: (p) => (
        <span className="inline-flex gap-1.5">
          <Button size="xs" onPress={() => setDeciding({ p, approve: true })} aria-label={`Approve ${p.scope_id}`}>
            Approve
          </Button>
          <Button size="xs" variant="secondary" onPress={() => setDeciding({ p, approve: false })} aria-label={`Reject ${p.scope_id}`}>
            Reject
          </Button>
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Biometric methods"
        description="Where each face match or liveness engine may run: one state per engine version, country, purpose and tenant. Nothing is on unless recorded here. A step up is two people, one step at a time; a pause is one person, at once."
        actions={<Button onPress={() => setProposing({ from: null })}>Propose a new scope</Button>}
      />
      <QueryView query={q}>
        {(d) => (
          <div className="space-y-5">
            <Card padded={false} title="Open proposals" description="Waiting for a second person. You cannot approve your own.">
              <DataTable
                caption="Open proposals"
                columns={proposalCols}
                rows={d.open_proposals}
                rowKey={(p) => p.proposal_id}
                empty={<EmptyState icon={Fingerprint} title="No open proposals" />}
              />
            </Card>
            <Card padded={false} title="Scopes" description="The state in force now. A paused scope always wins over any other scope that matches a session.">
              <DataTable
                caption="Biometric method scopes"
                columns={scopeCols}
                rows={d.items}
                rowKey={(r) => r.current.scope_id}
                empty={
                  <EmptyState icon={ScanFace} title="No biometric method is on anywhere">
                    Every face match and liveness method is OFF until a scope is approved.
                  </EmptyState>
                }
              />
            </Card>
          </div>
        )}
      </QueryView>
      <ProposeDialog from={proposing?.from ?? null} isOpen={proposing !== null} onClose={() => setProposing(null)} />
      <DecideDialog proposal={deciding?.p ?? null} approve={deciding?.approve ?? true} onClose={() => setDeciding(null)} />
      <StepDownDialog scope={stepping} onClose={() => setStepping(null)} />
    </>
  );
}
