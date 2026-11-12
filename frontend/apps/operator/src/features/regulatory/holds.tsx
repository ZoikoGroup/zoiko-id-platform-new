"use client";

import type { SuspensionScope, SuspensionTrigger, SuspensionVersion } from "@zoiko/api-client";
import { Banner, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { OctagonX } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { Mono, opt, REG, Reasons, RegError } from "./common";

const TRIGGERS: SuspensionTrigger[] = ["CERTIFICATION_WITHDRAWN", "PROVIDER_BREACH", "ENTITLEMENT_WITHDRAWN", "TRUST_LIST_STALE", "LICENSE_SUSPENDED", "MANUAL"];

type Row = { current: SuspensionVersion; versions: SuspensionVersion[] };

function scopeText(s: SuspensionScope): string {
  return [s.provider, s.service_id, s.scheme, s.country, s.subdivision, s.purpose, s.profession, s.credential_class].filter(Boolean).join(" · ");
}

function HoldDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const blank = { suspension_id: "", provider: "", service_id: "", scheme: "", country: "", purpose: "", reason: "", evidence_ref: "" };
  const [f, setF] = useState(blank);
  const [trigger, setTrigger] = useState<SuspensionTrigger>("CERTIFICATION_WITHDRAWN");
  const [previewed, setPreviewed] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const set = (k: keyof typeof blank) => (v: string) => {
    setF((p) => ({ ...p, [k]: v }));
    setConfirmed(false);
  };
  const scope = {
    provider: f.provider.trim(),
    service_id: opt(f.service_id.toUpperCase()),
    scheme: opt(f.scheme.toUpperCase()),
    country: opt(f.country.toUpperCase()),
    purpose: opt(f.purpose.toUpperCase()),
  };
  const scopeKey = JSON.stringify(scope);
  const impact = useCellQuery(["regulatory", "impact", previewed], (api) => api.regulatory.suspensionImpact(JSON.parse(previewed!) as typeof scope), {
    enabled: previewed !== null,
  });
  const apply = useCellMutation(
    (api) => api.regulatory.suspend({ suspension_id: f.suspension_id.trim(), scope, trigger, reason: f.reason.trim(), evidence_ref: f.evidence_ref.trim() }),
    {
      invalidate: [REG.suspensions, REG.readiness, REG.attestations],
      success: "Hold applied: matching calls are refused from now",
      onSuccess: () => {
        setF(blank);
        setPreviewed(null);
        setConfirmed(false);
        onClose();
      },
    },
  );
  const ready = f.suspension_id && f.provider && f.reason.trim().length >= 10 && f.evidence_ref.trim().length >= 3 && previewed === scopeKey && confirmed;
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Apply a hold (kill switch)"
      description="Stops every call that matches the scope at once, in every mode, including shadow. Preview what it touches first."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant="danger" onPress={() => apply.mutate(undefined)} pending={apply.isPending} isDisabled={!ready}>
            Apply hold
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Hold ID" description="lowercase, e.g. hold-acme-rtr" value={f.suspension_id} onChange={set("suspension_id")} mono isRequired />
        <TextField label="Provider" value={f.provider} onChange={set("provider")} mono isRequired />
        <TextField label="Service" description="blank = every service" value={f.service_id} onChange={set("service_id")} mono />
        <TextField label="Scheme" description="blank = every scheme" value={f.scheme} onChange={set("scheme")} mono />
        <TextField label="Country" description="blank = every country" value={f.country} onChange={set("country")} />
        <TextField label="Purpose" description="blank = every purpose" value={f.purpose} onChange={set("purpose")} mono />
        <Select label="Trigger" value={trigger} onChange={(v) => setTrigger(TRIGGERS.find((t) => t === v) ?? "MANUAL")} options={TRIGGERS.map((t) => ({ id: t, label: t }))} />
        <TextField label="Evidence reference" description="incident or register reference" value={f.evidence_ref} onChange={set("evidence_ref")} isRequired />
        <TextField label="Reason" description="at least 10 characters" value={f.reason} onChange={set("reason")} multiline rows={2} isRequired className="sm:col-span-2" />
      </div>
      <div className="mt-4 space-y-3 border-t border-border pt-4">
        <Button variant="secondary" onPress={() => setPreviewed(scopeKey)} isDisabled={!f.provider}>
          Preview impact
        </Button>
        {previewed !== null ? (
          <QueryView query={impact}>
            {(d) => (
              <div className="space-y-2 text-sm">
                {previewed !== scopeKey ? <Banner tone="warn">The scope changed since the preview. Preview again.</Banner> : null}
                <p>
                  Markets: <Reasons reasons={d.impact.markets} />
                </p>
                <p>
                  Entitlements: <Reasons reasons={d.impact.entitlements} />
                </p>
                <p>
                  Certifications: <Reasons reasons={d.impact.certifications} />
                </p>
                <p>
                  Public claims: <Reasons reasons={d.impact.claims} />
                </p>
                <Checkbox isSelected={confirmed} onChange={setConfirmed}>
                  I have read what this hold stops
                </Checkbox>
              </div>
            )}
          </QueryView>
        ) : null}
      </div>
      {apply.error ? <RegError error={apply.error} /> : null}
    </Dialog>
  );
}

function LiftDialog({ hold, onClose }: { hold: SuspensionVersion | null; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [ref, setRef] = useState("");
  const lift = useCellMutation((api, h: SuspensionVersion) => api.regulatory.liftSuspension(h.suspension_id, reason.trim(), ref.trim()), {
    invalidate: [REG.suspensions, REG.readiness, REG.attestations],
    success: "Hold lifted",
    onSuccess: () => {
      setReason("");
      setRef("");
      onClose();
    },
  });
  return (
    <Dialog
      isOpen={hold !== null}
      onOpenChange={(o) => !o && onClose()}
      title={`Lift ${hold?.suspension_id ?? ""}`}
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button onPress={() => hold && lift.mutate(hold)} pending={lift.isPending} isDisabled={reason.trim().length < 10 || ref.trim().length < 3}>
            Lift hold
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Why it is safe again" description="at least 10 characters" value={reason} onChange={setReason} multiline rows={2} isRequired />
        <TextField label="Evidence reference" value={ref} onChange={setRef} isRequired />
        {lift.error ? <RegError error={lift.error} /> : null}
      </div>
    </Dialog>
  );
}

export function HoldsPage() {
  const [activeOnly, setActiveOnly] = useState(true);
  const q = useCellQuery([...REG.suspensions, activeOnly], (api) => api.regulatory.suspensions(activeOnly));
  const [applying, setApplying] = useState(false);
  const [lifting, setLifting] = useState<SuspensionVersion | null>(null);
  const columns: Column<Row>[] = [
    { key: "id", header: "Hold", pinned: true, render: (r) => <Mono>{r.current.suspension_id}</Mono>, sortValue: (r) => r.current.suspension_id },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={r.current.status} kind="Hold" /> },
    { key: "scope", header: "Scope", render: (r) => scopeText(r.current.scope) },
    { key: "trigger", header: "Trigger", render: (r) => <Mono>{r.current.trigger}</Mono> },
    { key: "since", header: "Since", render: (r) => <DateTime value={r.current.effective_at} /> },
    { key: "by", header: "By", render: (r) => <Mono>{r.current.recorded_by}</Mono> },
    {
      key: "lift",
      header: "Actions",
      align: "end",
      render: (r) =>
        r.current.status === "ACTIVE" ? (
          <Button size="xs" variant="secondary" onPress={() => setLifting(r.current)} aria-label={`Lift ${r.current.suspension_id}`}>
            Lift
          </Button>
        ) : null,
    },
  ];
  return (
    <>
      <PageHeader
        title="Holds"
        description="The kill switch (WF-06). A hold is enforced in every mode: it stops matching provider calls, closes markets that rely on them and withdraws claims that cite them."
        actions={
          <Button variant="danger" onPress={() => setApplying(true)}>
            Apply hold
          </Button>
        }
      />
      <Card padded={false} title="Holds" actions={<Checkbox isSelected={activeOnly} onChange={setActiveOnly}>Active only</Checkbox>}>
        <QueryView query={q}>
          {(d) => (
            <DataTable
              caption="Holds"
              columns={columns}
              rows={d.items}
              rowKey={(r) => r.current.suspension_id}
              empty={<EmptyState icon={OctagonX} title="No holds">Nothing is suspended.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
      <HoldDialog isOpen={applying} onClose={() => setApplying(false)} />
      <LiftDialog hold={lifting} onClose={() => setLifting(null)} />
    </>
  );
}
