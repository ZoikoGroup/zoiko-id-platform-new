"use client";

import { CHECK_METHODS, type CheckMethod, type CheckMethodProposal, type CheckMethodVersion } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, TextField, type Column, type Tone } from "@zoiko/ui";
import { BadgeCheck } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { COUNTRY_OPTIONS, Mono, REG, RegError } from "./common";

/** WF-05 check method lifecycle and kill switch (ZID-DTV-WF-05 §04, §27, A12; CR-112): email, phone, carrier, address, postal and device methods.
 * The same rules as biometric methods: a step up is two people, one step at a time; a pause is one person, at once. Approval is not activation: the
 * deployment's adapter, approved retention for every data class, a licensed entitlement and the country overlay still decide each case. */

type State = CheckMethodVersion["state"];
type Target = CheckMethodProposal["target"];
const TONE: Record<State, Tone> = { OFF: "neutral", PAUSED: "danger", LAB_ONLY: "info", PILOT_GATED: "warn", PRODUCTION_ELIGIBLE: "ok" };
const NEXT_UP: Partial<Record<State, Target>> = { OFF: "LAB_ONLY", PAUSED: "LAB_ONLY", LAB_ONLY: "PILOT_GATED", PILOT_GATED: "PRODUCTION_ELIGIBLE" };
const REF_OK = (v: string) => /^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$/.test(v.trim());
type Row = { current: CheckMethodVersion; versions: CheckMethodVersion[] };

function ProposeDialog({ from, isOpen, onClose }: { from: CheckMethodVersion | null; isOpen: boolean; onClose: () => void }) {
  const [f, setF] = useState({ scope_id: "", method: "EMAIL_CHALLENGE" as CheckMethod, engine_id: "", engine_version: "1", country: "GB", purpose: "ONBOARDING", pilot: "" });
  const [refs, setRefs] = useState({ dpia: "", evaluation: "", evidence: "", reason: "" });
  const target: Target = from ? (NEXT_UP[from.state] ?? "LAB_ONLY") : "LAB_ONLY";
  const live = target !== "LAB_ONLY";
  const scope = from
    ? { ...from.scope, pilot_tenant: from.scope.pilot_tenant ?? undefined }
    : { method: f.method, engine_id: f.engine_id.trim(), engine_version: f.engine_version.trim(), country: f.country, purpose: f.purpose.trim(), ...(f.pilot.trim() ? { pilot_tenant: f.pilot.trim() } : {}) };
  const propose = useCellMutation(
    (api) =>
      api.regulatory.proposeCheckMethod({
        ...(from ? {} : { scope_id: f.scope_id.trim() }),
        scope: scope as never,
        target,
        ...(live ? { dpia_ref: refs.dpia.trim(), evaluation_ref: refs.evaluation.trim() } : {}),
        reason: refs.reason.trim(),
        evidence_ref: refs.evidence.trim(),
      }),
    { invalidate: [REG.checkMethods], success: "Proposed: a second person must approve it", onSuccess: onClose },
  );
  const ok = refs.reason.trim().length >= 10 && REF_OK(refs.evidence) && (!live || (REF_OK(refs.dpia) && REF_OK(refs.evaluation))) && (from || (f.scope_id.trim() && f.engine_id.trim()));
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={from ? `Propose ${target} for ${from.scope_id}` : "Propose a new check method scope (Lab only)"}
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>Cancel</Button>
          <Button onPress={() => propose.mutate(undefined)} pending={propose.isPending} isDisabled={!ok}>Propose</Button>
        </>
      }
    >
      <div className="space-y-3">
        {!from ? (
          <>
            <TextField label="Scope id" description="lower case, e.g. email-gb-onboarding" value={f.scope_id} onChange={(v) => setF((p) => ({ ...p, scope_id: v }))} isRequired />
            <Select label="Method" value={f.method} onChange={(v) => setF((p) => ({ ...p, method: (CHECK_METHODS.find((m) => m === v) ?? "EMAIL_CHALLENGE") as CheckMethod }))} options={CHECK_METHODS.map((m) => ({ id: m, label: m }))} />
            <TextField label="Adapter (engine id)" value={f.engine_id} onChange={(v) => setF((p) => ({ ...p, engine_id: v }))} isRequired />
            <TextField label="Adapter version" value={f.engine_version} onChange={(v) => setF((p) => ({ ...p, engine_version: v }))} isRequired />
            <Select label="Country" value={f.country} onChange={(v) => setF((p) => ({ ...p, country: String(v) }))} options={COUNTRY_OPTIONS} />
            <TextField label="Purpose" value={f.purpose} onChange={(v) => setF((p) => ({ ...p, purpose: v }))} isRequired />
            <TextField label="Pilot tenant (optional)" value={f.pilot} onChange={(v) => setF((p) => ({ ...p, pilot: v }))} />
          </>
        ) : null}
        {live ? (
          <>
            <TextField label="DPIA reference" value={refs.dpia} onChange={(v) => setRefs((p) => ({ ...p, dpia: v }))} isRequired />
            <TextField label="QA or certification reference" value={refs.evaluation} onChange={(v) => setRefs((p) => ({ ...p, evaluation: v }))} isRequired />
          </>
        ) : null}
        <TextField label="Evidence reference" value={refs.evidence} onChange={(v) => setRefs((p) => ({ ...p, evidence: v }))} isRequired />
        <TextField label="Reason" description="at least 10 characters" value={refs.reason} onChange={(v) => setRefs((p) => ({ ...p, reason: v }))} multiline rows={2} isRequired />
        {propose.error ? <RegError error={propose.error} /> : null}
      </div>
    </Dialog>
  );
}

function DecideDialog({ proposal, approve, onClose }: { proposal: CheckMethodProposal | null; approve: boolean; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const decide = useCellMutation(
    (api, p: CheckMethodProposal): Promise<unknown> =>
      approve ? api.regulatory.approveCheckProposal(p.proposal_id, reason.trim()) : api.regulatory.rejectCheckProposal(p.proposal_id, reason.trim()),
    { invalidate: [REG.checkMethods], success: approve ? "Approved: one step up" : "Proposal rejected", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={proposal !== null}
      onOpenChange={(o) => !o && onClose()}
      title={`${approve ? "Approve" : "Reject"} ${proposal?.scope_id ?? ""} → ${proposal?.target ?? ""}`}
      description={approve ? "You must not be the proposer." : undefined}
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>Cancel</Button>
          <Button variant={approve ? "primary" : "danger"} onPress={() => proposal && decide.mutate(proposal)} pending={decide.isPending} isDisabled={reason.trim().length < 10}>
            {approve ? "Approve" : "Reject"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Reason" description="at least 10 characters" value={reason} onChange={setReason} multiline rows={2} isRequired />
        {decide.error ? <RegError error={decide.error} /> : null}
      </div>
    </Dialog>
  );
}

function StepDownDialog({ scope, onClose }: { scope: CheckMethodVersion | null; onClose: () => void }) {
  const [target, setTarget] = useState("PAUSED");
  const [reason, setReason] = useState("");
  const [ref, setRef] = useState("");
  const step = useCellMutation((api, s: CheckMethodVersion) => api.regulatory.stepDownCheckMethod(s.scope_id, { target, reason: reason.trim(), evidence_ref: ref.trim() }), {
    invalidate: [REG.checkMethods],
    success: "Done: it takes effect for new cases now",
    onSuccess: onClose,
  });
  return (
    <Dialog
      isOpen={scope !== null}
      onOpenChange={(o) => !o && onClose()}
      title={`Step down ${scope?.scope_id ?? ""}`}
      description="Takes effect at once, by you alone. A resume from Paused is a new two-person approval."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>Cancel</Button>
          <Button variant="danger" onPress={() => scope && step.mutate(scope)} pending={step.isPending} isDisabled={reason.trim().length < 10 || !REF_OK(ref)}>
            {target === "PAUSED" ? "Pause now" : target === "OFF" ? "Retire" : "Step down"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Select label="New state" value={target} onChange={(v) => setTarget(String(v))} options={["PAUSED", "OFF", "LAB_ONLY"].map((t) => ({ id: t, label: t }))} />
        <TextField label="Evidence reference" value={ref} onChange={setRef} isRequired />
        <TextField label="Reason" description="at least 10 characters" value={reason} onChange={setReason} multiline rows={2} isRequired />
        {step.error ? <RegError error={step.error} /> : null}
      </div>
    </Dialog>
  );
}

export function CheckMethodsPage() {
  const q = useCellQuery(REG.checkMethods, (api) => api.regulatory.checkMethods());
  const [proposing, setProposing] = useState<{ from: CheckMethodVersion | null } | null>(null);
  const [deciding, setDeciding] = useState<{ p: CheckMethodProposal; approve: boolean } | null>(null);
  const [stepping, setStepping] = useState<CheckMethodVersion | null>(null);
  const scopeCols: Column<Row>[] = [
    { key: "id", header: "Scope", pinned: true, render: (r) => <Mono>{r.current.scope_id}</Mono> },
    { key: "method", header: "Method", render: (r) => r.current.scope.method },
    { key: "state", header: "State", render: (r) => <Badge tone={TONE[r.current.state]}>{r.current.state}</Badge> },
    { key: "scope", header: "Adapter · country · purpose · tenant", render: (r) => `${r.current.scope.engine_id}@${r.current.scope.engine_version} · ${r.current.scope.country} · ${r.current.scope.purpose} · ${r.current.scope.pilot_tenant ?? "all tenants"}` },
    { key: "since", header: "Since", render: (r) => <DateTime value={r.current.effective_at} /> },
    {
      key: "act",
      header: "Actions",
      align: "end",
      render: (r) => (
        <span className="inline-flex gap-1.5">
          {NEXT_UP[r.current.state] ? <Button size="xs" variant="secondary" onPress={() => setProposing({ from: r.current })}>Propose {NEXT_UP[r.current.state]}</Button> : null}
          {r.current.state !== "OFF" && r.current.state !== "PAUSED" ? <Button size="xs" variant="danger" onPress={() => setStepping(r.current)}>Pause / step down</Button> : null}
        </span>
      ),
    },
  ];
  const proposalCols: Column<CheckMethodProposal>[] = [
    { key: "id", header: "Scope", pinned: true, render: (p) => <Mono>{p.scope_id}</Mono> },
    { key: "method", header: "Method", render: (p) => p.scope.method },
    { key: "step", header: "Step", render: (p) => <Badge tone={TONE[p.target]}>{p.target}</Badge> },
    { key: "by", header: "Proposed by", render: (p) => <Mono>{p.proposed_by}</Mono> },
    { key: "at", header: "When", render: (p) => <DateTime value={p.proposed_at} /> },
    {
      key: "act",
      header: "Decide",
      align: "end",
      render: (p) => (
        <span className="inline-flex gap-1.5">
          <Button size="xs" onPress={() => setDeciding({ p, approve: true })}>Approve</Button>
          <Button size="xs" variant="secondary" onPress={() => setDeciding({ p, approve: false })}>Reject</Button>
        </span>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title="Check methods"
        description="Where each email, phone, address, postal and device check may run: one state per adapter version, country, purpose and tenant. Nothing is on unless recorded here."
        actions={<Button onPress={() => setProposing({ from: null })}>Propose a new scope</Button>}
      />
      <QueryView query={q}>
        {(d) => (
          <div className="space-y-5">
            <Card padded={false} title="Open proposals" description="Waiting for a second person. You cannot approve your own.">
              <DataTable caption="Open proposals" columns={proposalCols} rows={d.open_proposals} rowKey={(p) => p.proposal_id} empty={<EmptyState icon={BadgeCheck} title="No open proposals" />} />
            </Card>
            <Card padded={false} title="Scopes" description="The state in force now. A paused scope always wins.">
              <DataTable caption="Check method scopes" columns={scopeCols} rows={d.items} rowKey={(r) => r.current.scope_id} empty={<EmptyState icon={BadgeCheck} title="No check method is on anywhere" />} />
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
