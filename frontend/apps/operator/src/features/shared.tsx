"use client";

import type { JitGrant, OperatorCapability } from "@zoiko/api-client";
import { Badge, Button, DateTime, Dialog, KeyValues, RiskBadge, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/op";

export const GRANTS_KEY = ["operator", "grants"];
export const CAPS_KEY = ["operator", "capabilities"];

export function useCapabilities() {
  return useCellQuery(CAPS_KEY, (api) => api.capabilities());
}

export function grantColumns(extra: Column<JitGrant>[] = []): Column<JitGrant>[] {
  return [
    {
      key: "capability",
      header: "Capability",
      pinned: true,
      render: (g) => (
        <span className="inline-flex flex-col">
          <span className="font-mono text-xs">{g.capability}</span>
          <span className="text-2xs text-ink-subtle">{g.resource_scope}</span>
        </span>
      ),
      sortValue: (g) => g.capability,
    },
    {
      key: "state",
      header: "State",
      render: (g) => (
        <span className="inline-flex items-center gap-1.5">
          <StatusBadge state={g.state} kind="JIT grant" />
          {g.break_glass ? <Badge tone="danger">break-glass</Badge> : null}
        </span>
      ),
    },
    { key: "risk", header: "Risk", render: (g) => <RiskBadge risk={g.risk_class as "R0" | "R1" | "R2" | "R3"} /> },
    { key: "tenant", header: "Tenant", render: (g) => <span className="font-mono text-2xs">{g.target_tenant_id ?? "platform"}</span> },
    { key: "case", header: "Case", render: (g) => <span className="font-mono text-xs">{g.case_ref}</span> },
    {
      key: "approvals",
      header: "Approvals",
      render: (g) => (g.break_glass ? "—" : `${g.approvals_received ?? (g.state === "REQUESTED" ? 0 : g.required_approvals)}/${g.required_approvals}`),
    },
    { key: "lockbox", header: "Lockbox", render: (g) => (g.lockbox_required ? <Badge tone="warn">customer approval</Badge> : "—") },
    { key: "expires", header: "Ends", render: (g) => (g.expires_at ? <DateTime value={g.expires_at} /> : `${g.duration_minutes} min after activation`) },
    ...extra,
  ];
}

const CELL_HINT = "Requests are bound to the cell and environment you are signed into.";

export function AccessRequestDialog({
  isOpen,
  onClose,
  breakGlass = false,
}: {
  isOpen: boolean;
  onClose: () => void;
  breakGlass?: boolean;
}) {
  const caps = useCapabilities();
  const options = (caps.data?.items ?? []).filter((c) => !breakGlass || c.break_glass_allowed);
  const [capability, setCapability] = useState<string>("");
  const [tenant, setTenant] = useState("");
  const [scope, setScope] = useState("");
  const [caseRef, setCaseRef] = useState("");
  const [reason, setReason] = useState("");
  const [minutes, setMinutes] = useState("30");
  const selected: OperatorCapability | undefined = options.find((c) => c.capability === capability);
  const create = useCellMutation(
    (api) => {
      const body = {
        capability,
        case_ref: caseRef.trim(),
        reason: reason.trim(),
        target_tenant_id: tenant.trim() || null,
        resource_scope: scope.trim(),
        duration_minutes: Number(minutes),
      };
      return breakGlass ? api.breakGlass(body) : api.create(body);
    },
    {
      invalidate: [GRANTS_KEY],
      success: breakGlass ? "Break-glass active: Security has been alerted and a post-review is pending" : "Access requested: an independent approver must approve it",
      onSuccess: () => close(),
    },
  );
  function close() {
    if (create.isPending) return;
    setTenant("");
    setScope("");
    setCaseRef("");
    setReason("");
    create.reset();
    onClose();
  }
  const minReason = breakGlass ? 20 : 8;
  const max = breakGlass ? Math.min(selected?.max_duration_minutes ?? 60, caps.data?.break_glass_max_minutes ?? 60) : (selected?.max_duration_minutes ?? 60);
  const valid =
    selected &&
    caseRef.trim() &&
    scope.trim() &&
    reason.trim().length >= minReason &&
    Number(minutes) >= 1 &&
    Number(minutes) <= max &&
    (selected.scope !== "TENANT" || tenant.trim().length === 36);
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title={breakGlass ? "Emergency break-glass access" : "Request just-in-time access"}
      description={
        breakGlass
          ? "Active at once without approvals or the customer lockbox. Security is alerted immediately and an independent post-review is mandatory."
          : `No standing privilege: access is approved by someone else, bounded in time and audited. ${CELL_HINT}`
      }
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={close}>
            Cancel
          </Button>
          <Button variant={breakGlass ? "danger" : "primary"} pending={create.isPending} isDisabled={!valid} onPress={() => create.mutate(undefined)}>
            {breakGlass ? "Activate break-glass" : "Request access"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Select
          label="Capability"
          value={capability || null}
          onChange={setCapability}
          options={options.map((c) => ({ id: c.capability, label: c.capability, description: c.description }))}
        />
        {selected ? (
          <KeyValues
            columns={3}
            items={[
              ["Risk", <RiskBadge key="r" risk={selected.risk_class as "R2" | "R3"} />],
              ["Approvals", selected.required_approvals === 2 ? "Two people" : "One independent approver"],
              ["Maximum", `${selected.max_duration_minutes} min`],
              ["Customer data", selected.customer_data ? "Yes: lockbox where required" : "No"],
              ["Scope", selected.scope],
              ["Evidence", selected.evidence_class],
            ]}
          />
        ) : null}
        {selected && selected.scope !== "PLATFORM" ? (
          <TextField label="Customer tenant ID" value={tenant} onChange={setTenant} mono isRequired={selected.scope === "TENANT"} />
        ) : null}
        <TextField label="Resource scope" value={scope} onChange={setScope} mono isRequired placeholder="principal:*" />
        <TextField label="Case / ticket reference" value={caseRef} onChange={setCaseRef} mono isRequired />
        <TextField label={breakGlass ? "Emergency justification" : "Reason"} value={reason} onChange={setReason} isRequired multiline rows={3} description={`At least ${minReason} characters.`} />
        <TextField label="Duration (minutes)" value={minutes} onChange={setMinutes} inputMode="numeric" description={`At most ${max} minutes.`} />
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Dialog>
  );
}
