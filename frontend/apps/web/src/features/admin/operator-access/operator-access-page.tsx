"use client";

import type { LockboxRequest, OperatorAccessPolicy } from "@zoiko/api-client";
import {
  Banner,
  Button,
  Card,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  PageHeader,
  RiskBadge,
  StatusBadge,
  Switch,
  TextField,
  type Column,
} from "@zoiko/ui";
import { LockKeyhole } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, listInput, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const LOCKBOX_KEY = ["admin", "lockbox"];
const POLICY_KEY = ["admin", "operator-policy"];

export function OperatorAccessPage() {
  return (
    <>
      <PageHeader
        title="Operator access"
        description="Zoiko staff have no standing access to your data. Approve or deny their just-in-time requests (customer lockbox), and set where operators may be located."
      />
      <div className="space-y-5">
        <LockboxSection />
        <PolicySection />
      </div>
    </>
  );
}

function LockboxSection() {
  const requests = useCellQuery(LOCKBOX_KEY, (api) => api.lockbox.requests());
  const [deciding, setDeciding] = useState<{ lb: LockboxRequest; approve: boolean } | null>(null);
  const [reason, setReason] = useState("");
  const decide = useCellMutation(
    (api, d: { lb: LockboxRequest; approve: boolean }) =>
      d.approve ? api.lockbox.approve(d.lb.lockbox_request_id, { reason: reason.trim() }) : api.lockbox.deny(d.lb.lockbox_request_id, { reason: reason.trim() }),
    {
      invalidate: [LOCKBOX_KEY],
      success: (r) => (r.state === "APPROVED" ? "Access approved" : r.state === "REVOKED" ? "Approval withdrawn: access stopped" : "Access denied"),
      onSuccess: () => {
        setDeciding(null);
        setReason("");
      },
    },
  );

  const columns: Column<LockboxRequest>[] = [
    {
      key: "capability",
      header: "Requested access",
      pinned: true,
      render: (l) => (
        <span className="inline-flex flex-col">
          <span className="font-mono text-xs">{l.capability}</span>
          <span className="text-2xs text-ink-subtle">{l.resource_scope}</span>
        </span>
      ),
    },
    { key: "state", header: "State", render: (l) => <StatusBadge state={l.state} kind="Lockbox request" /> },
    {
      key: "case",
      header: "Case and reason",
      render: (l) => (
        <span className="inline-flex max-w-80 flex-col text-xs">
          <span className="font-mono">{l.case_ref}</span>
          <span className="truncate text-ink-subtle" title={l.reason}>{l.reason}</span>
        </span>
      ),
    },
    { key: "duration", header: "Duration", render: (l) => `${l.duration_minutes} min` },
    { key: "expires", header: "Decide by", render: (l) => <DateTime value={l.expires_at} /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (l) =>
        l.state === "PENDING" ? (
          <span className="flex justify-end gap-1.5">
            <Button size="xs" onPress={() => setDeciding({ lb: l, approve: true })}>
              Approve
            </Button>
            <Button size="xs" variant="danger-outline" onPress={() => setDeciding({ lb: l, approve: false })}>
              Deny
            </Button>
          </span>
        ) : l.state === "APPROVED" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setDeciding({ lb: l, approve: false })}>
            Withdraw
          </Button>
        ) : null,
    },
  ];

  const withdrawing = deciding && !deciding.approve && deciding.lb.state === "APPROVED";
  return (
    <>
      <Card padded={false} title="Customer lockbox" description="Every decision is R3, evidenced in your tenant's own audit trail, and you can withdraw an approval at any time.">
        <QueryView query={requests}>
          {(d) => (
            <DataTable
              caption="Lockbox requests"
              columns={columns}
              rows={d.items}
              rowKey={(l) => l.lockbox_request_id}
              empty={<EmptyState icon={LockKeyhole} title="No operator access requests">When Zoiko support needs your data for a case, the request appears here first.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
      <Dialog
        isOpen={deciding !== null}
        onOpenChange={(o) => !o && !decide.isPending && setDeciding(null)}
        title={deciding?.approve ? "Approve operator access?" : withdrawing ? "Withdraw your approval?" : "Deny operator access?"}
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" onPress={() => setDeciding(null)}>
              Cancel
            </Button>
            <Button variant={deciding?.approve ? "primary" : "danger"} pending={decide.isPending} onPress={() => deciding && decide.mutate(deciding)}>
              {deciding?.approve ? "Approve" : withdrawing ? "Withdraw" : "Deny"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <RiskBadge risk="R3" />
          <p className="text-sm text-ink-muted">
            {deciding?.approve
              ? `The operator may use ${deciding.lb.capability} on ${deciding.lb.resource_scope} for at most ${deciding.lb.duration_minutes} minutes, and never past this approval's validity.`
              : withdrawing
                ? "The operator's access stops on their next request."
                : "The operator cannot activate this access."}
          </p>
          <TextField label="Reason (optional)" value={reason} onChange={setReason} />
          {decide.error ? <ApiErrorView error={decide.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}

function PolicySection() {
  const policy = useCellQuery(POLICY_KEY, (api) => api.lockbox.policy());
  return <QueryView query={policy}>{(p) => <PolicyForm key={p.revision} policy={p} />}</QueryView>;
}

function PolicyForm({ policy }: { policy: OperatorAccessPolicy }) {
  const [lockbox, setLockbox] = useState(policy.lockbox_required);
  const [jurisdictions, setJurisdictions] = useState(policy.allowed_operator_jurisdictions.join(", "));
  const save = useCellMutation(
    (api) => api.lockbox.setPolicy({ lockbox_required: lockbox, allowed_operator_jurisdictions: listInput(jurisdictions).map((j) => j.toUpperCase()) }),
    { invalidate: [POLICY_KEY], success: "Operator access policy saved" },
  );
  return (
    <Card
      title="Operator access policy"
      description="Applies to every operator request for this tenant. Sovereign (SRP-2/3) tenants are also restricted by their residency profile."
      footer={
        <Button size="sm" pending={save.isPending} onPress={() => save.mutate(undefined)}>
          Save policy
        </Button>
      }
    >
      <div className="space-y-4">
        <Switch isSelected={lockbox} onChange={setLockbox}>
          Require my approval (lockbox) before an operator reads customer data
        </Switch>
        {!lockbox ? <Banner tone="warn" title="Operators will not need your approval for data access">Approvals by Zoiko staff and the audit trail still apply.</Banner> : null}
        <TextField
          label="Permitted operator jurisdictions"
          value={jurisdictions}
          onChange={setJurisdictions}
          mono
          description="ISO 3166-1 alpha-2 codes, e.g. GB, IE. Empty: no restriction beyond your residency profile."
        />
        <p className="text-xs text-ink-subtle">Policy revision {policy.revision}</p>
        {save.error ? <ApiErrorView error={save.error} /> : null}
      </div>
    </Card>
  );
}
