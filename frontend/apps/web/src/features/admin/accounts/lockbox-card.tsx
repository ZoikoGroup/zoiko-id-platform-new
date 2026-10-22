"use client";

import type { LockboxIn, PrincipalItem, RecoveryPolicy } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Code, CopyId, Dialog, EmptyState, KeyValues, RevisionChip, RiskBadge, TextField } from "@zoiko/ui";
import { Lock, LockOpen, Stamp } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, listInput, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ACTION = "authentication.recovery.policy.manage";

export const policyKey = (principalId: string) => ["admin", "recovery-policy", principalId];

/** The exact change string the approval request must bind to (mirrors the cell's `lockbox_change`). */
function changeFor(approvers: string[], k: number, hours: number) {
  return `lockbox:k=${k}:h=${hours}:${[...approvers].map((a) => a.toLowerCase()).sort().join(",")}`;
}

function ApprovalHint({ principalId, change }: { principalId: string; change: string }) {
  return (
    <Banner tone="info" icon={Stamp} title="Second-administrator approval required">
      <span className="block">Create an approval request under Authority → Approvals for exactly this change, have it approved, then paste its ID:</span>
      <span className="mt-2 block space-y-1 text-xs">
        <span className="block">
          Action <Code>{ACTION}</Code>
        </span>
        <span className="block">
          Resource <Code>principal:{principalId}</Code>
        </span>
        <span className="block break-all">
          Change <Code>{change}</Code>
        </span>
      </span>
    </Banner>
  );
}

function EditDialog({
  principalId,
  current,
  isOpen,
  onClose,
}: {
  principalId: string;
  current: RecoveryPolicy | null;
  isOpen: boolean;
  onClose: () => void;
}) {
  const [approvers, setApprovers] = useState((current?.approvers ?? []).join("\n"));
  const [k, setK] = useState(String(current?.k ?? 2));
  const [hours, setHours] = useState(String(current?.cooling_off_hours ?? 168));
  const [approval, setApproval] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const put = useCellMutation((api, body: LockboxIn) => api.recoveryAdmin.putPolicy(principalId, body), {
    invalidate: [policyKey(principalId)],
    success: "Lockbox policy saved",
    onSuccess: () => close(),
  });

  function close() {
    setApproval("");
    setFormError(null);
    put.reset();
    onClose();
  }

  const list = listInput(approvers);
  const kNum = Number(k);
  const hNum = Number(hours);

  function submit() {
    setFormError(null);
    if (list.length < 2 || list.length > 5) return setFormError("Name between 2 and 5 approvers.");
    const bad = list.find((a) => !UUID_RE.test(a));
    if (bad) return setFormError(`"${bad}" is not a principal UUID.`);
    if (new Set(list.map((a) => a.toLowerCase())).size !== list.length) return setFormError("Approvers must be distinct.");
    if (list.some((a) => a.toLowerCase() === principalId.toLowerCase())) return setFormError("The account holder cannot approve their own recovery.");
    if (!Number.isInteger(kNum) || kNum < 2 || kNum > 5 || kNum > list.length) return setFormError("Threshold k must be 2–5 and at most the number of approvers.");
    if (!Number.isInteger(hNum) || hNum < 168 || hNum > 720) return setFormError("Cooling-off must be 168–720 hours (7–30 days).");
    put.mutate({ approvers: list, k: kNum, cooling_off_hours: hNum, approval_request_id: approval.trim() || null });
  }

  const validShape = list.length >= 2 && Number.isInteger(kNum) && Number.isInteger(hNum);

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !put.isPending && close()}
      title={current ? "Change lockbox policy" : "Place under lockbox recovery"}
      description="RC-4: recovery needs k of n named approvers plus a cooling-off period."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={put.isPending} onPress={close}>
            Cancel
          </Button>
          <Button variant="danger" pending={put.isPending} onPress={submit}>
            Save policy
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RiskBadge risk="R3" />
        <TextField
          label="Approvers"
          isRequired
          multiline
          rows={4}
          mono
          value={approvers}
          onChange={setApprovers}
          description="2–5 principal IDs, one per line or comma separated."
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Threshold (k)" isRequired type="number" inputMode="numeric" value={k} onChange={setK} description="Approvals needed, 2–5." />
          <TextField
            label="Cooling-off (hours)"
            isRequired
            type="number"
            inputMode="numeric"
            value={hours}
            onChange={setHours}
            description="168–720. The account holder is notified and can cancel during this time."
          />
        </div>
        {validShape ? <ApprovalHint principalId={principalId} change={changeFor(list, kNum, hNum)} /> : null}
        <TextField label="Approval request ID" value={approval} onChange={setApproval} mono description="Required: the approval must match this exact policy." />
        {formError ? (
          <Banner tone="danger" title="Check the form" live="polite">
            {formError}
          </Banner>
        ) : null}
        {put.error ? <ApiErrorView error={put.error} /> : null}
      </div>
    </Dialog>
  );
}

function LiftDialog({ principalId, isOpen, onClose }: { principalId: string; isOpen: boolean; onClose: () => void }) {
  const [approval, setApproval] = useState("");
  const lift = useCellMutation((api, id: string | null) => api.recoveryAdmin.liftPolicy(principalId, id), {
    invalidate: [policyKey(principalId)],
    success: "Lockbox policy lifted",
    onSuccess: () => close(),
  });
  function close() {
    setApproval("");
    lift.reset();
    onClose();
  }
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !lift.isPending && close()}
      title="Lift lockbox policy?"
      size="md"
      role="alertdialog"
      footer={
        <>
          <Button variant="secondary" isDisabled={lift.isPending} onPress={close}>
            Cancel
          </Button>
          <Button variant="danger" pending={lift.isPending} onPress={() => lift.mutate(approval.trim() || null)}>
            Lift policy
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RiskBadge risk="R3" />
        <p className="text-sm text-ink-muted">
          Lifting downgrades this account&apos;s recovery to the standard classes, so it is approved like setting it. It is refused while a
          recovery case is open.
        </p>
        <ApprovalHint principalId={principalId} change="lift" />
        <TextField label="Approval request ID" isRequired value={approval} onChange={setApproval} mono />
        {lift.error ? <ApiErrorView error={lift.error} /> : null}
      </div>
    </Dialog>
  );
}

export function LockboxCard({ principal }: { principal: PrincipalItem }) {
  const id = principal.principal_id;
  const policy = useCellQuery(policyKey(id), (api) => api.recoveryAdmin.policy(id));
  const [editing, setEditing] = useState<RecoveryPolicy | "new" | null>(null);
  const [lifting, setLifting] = useState(false);

  return (
    <Card title="Lockbox recovery (RC-4)" description="For specially protected accounts: recovery requires k-of-n named approvers and a cooling-off period.">
      <QueryView
        query={policy}
        loadingRows={2}
        notFound={
          <EmptyState
            compact
            icon={LockOpen}
            title="No lockbox policy"
            action={
              <Button size="sm" variant="secondary" icon={<Lock aria-hidden className="size-4" />} onPress={() => setEditing("new")}>
                Place under lockbox
              </Button>
            }
          >
            This account uses the standard recovery classes.
          </EmptyState>
        }
      >
        {(p) => (
          <div className="space-y-4">
            <KeyValues
              columns={4}
              items={[
                ["Class", <Badge key="c" tone="brand">{p.recovery_class ?? "None"}</Badge>],
                ["Threshold", <span key="k" className="tabular">{p.k ?? "—"} of {p.approvers?.length ?? 0}</span>],
                ["Cooling-off", p.cooling_off_hours ? `${p.cooling_off_hours} h (${Math.round(p.cooling_off_hours / 24)} days)` : null],
                ["Revision", p.revision !== undefined ? <RevisionChip key="r" kind="Policy" value={p.revision} /> : null],
              ]}
            />
            <div>
              <div className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Approvers</div>
              <ul className="mt-1 flex flex-wrap gap-3">
                {(p.approvers ?? []).map((a) => (
                  <li key={a}>
                    <CopyId value={a} label="approver ID" />
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <Button size="sm" variant="danger-outline" onPress={() => setLifting(true)}>
                Lift policy
              </Button>
              <Button size="sm" variant="secondary" onPress={() => setEditing(p)}>
                Change policy
              </Button>
            </div>
          </div>
        )}
      </QueryView>
      {editing ? (
        <EditDialog principalId={id} current={editing === "new" ? null : editing} isOpen onClose={() => setEditing(null)} />
      ) : null}
      <LiftDialog principalId={id} isOpen={lifting} onClose={() => setLifting(false)} />
    </Card>
  );
}
