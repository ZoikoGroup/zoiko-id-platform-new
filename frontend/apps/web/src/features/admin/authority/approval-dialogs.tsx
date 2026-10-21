"use client";

import type { ApprovalCreated, ApprovalDecisionCreate, ApprovalDecisionResult, ApprovalListItem, ApprovalMode, ApprovalRequestCreate } from "@zoiko/api-client";
import { Banner, Button, Code, CopyId, DateTime, Dialog, KeyValues, RadioCards, Select, StatusBadge, TextField, humanize } from "@zoiko/ui";
import { CheckCircle2, Stamp } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, listInput, useCellMutation } from "@/lib/cell";

import { FormProblem, GrantPicker, RISK_OPTIONS, UUID_RE, useMe } from "./shared";
import { TransactionContextField } from "./transaction-context";

export const APPROVALS_KEY = ["authority", "approvals"];

const MODES: { id: ApprovalMode; description: string }[] = [
  { id: "SINGLE", description: "One approver holding any of the roles." },
  { id: "ALL_OF", description: "One approver for every listed role." },
  { id: "K_OF_N", description: "Any k distinct approvers holding a listed role." },
  { id: "ROLE_PAIR", description: "Two approvers holding two different listed roles." },
  { id: "EXTERNAL_CONFIRMATION", description: "Confirmed outside the platform." },
];

const TTL: { id: string; label: string }[] = [
  { id: "900", label: "15 minutes" },
  { id: "3600", label: "1 hour" },
  { id: "14400", label: "4 hours" },
  { id: "86400", label: "1 day" },
  { id: "604800", label: "7 days (maximum)" },
];

/* ------------------------------------------------------------------------------------------------
 * Create
 * ---------------------------------------------------------------------------------------------- */

export function CreateApprovalDialog({ onClose }: { onClose: () => void }) {
  const [subject, setSubject] = useState("");
  const [tc, setTc] = useState("");
  const [mode, setMode] = useState<ApprovalMode>("SINGLE");
  const [roles, setRoles] = useState("");
  const [k, setK] = useState("2");
  const [ttl, setTtl] = useState("3600");
  const [problem, setProblem] = useState<string | null>(null);
  const [created, setCreated] = useState<{ result: ApprovalCreated; tc: string } | null>(null);

  const create = useCellMutation((api, body: ApprovalRequestCreate) => api.authority.createApproval(body), {
    invalidate: [APPROVALS_KEY],
    success: "Approval request opened",
    onSuccess: (r, body) => setCreated({ result: r, tc: body.transaction_context }),
  });

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const r = listInput(roles);
    const kn = Number(k);
    if (!subject.trim()) return setProblem("Describe what the request is about.");
    if (!tc.trim()) return setProblem("Enter the transaction context the approval is bound to.");
    if (!r.length) return setProblem("List at least one approving role.");
    if (mode === "K_OF_N" && (!Number.isInteger(kn) || kn < 1 || kn > 16)) return setProblem("k must be between 1 and 16.");
    setProblem(null);
    create.mutate({ subject_ref: subject.trim(), transaction_context: tc, mode, roles: r, k: mode === "K_OF_N" ? kn : 1, ttl_seconds: Number(ttl) });
  }

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="New approval request"
      description="Ask a second administrator to approve one exact change."
      size="lg"
      footer={
        created ? (
          <Button onPress={onClose}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
              Cancel
            </Button>
            <Button pending={create.isPending} onPress={() => submit()}>
              Open request
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="space-y-4">
          <Banner tone="ok" icon={CheckCircle2} title="Request opened">
            Share the request ID and the exact transaction context with the approvers. Once it is approved, resubmit the protected action
            with this approval request ID.
          </Banner>
          <KeyValues
            columns={1}
            items={[
              ["Approval request ID", <CopyId key="id" value={created.result.approval_request_id} short={false} label="approval request" />],
              ["State", <StatusBadge key="s" state={created.result.state} kind="Approval" />],
              ["Expires", <DateTime key="e" value={created.result.expires_at} />],
              ["Transaction context", <Code key="tc">{created.tc}</Code>],
            ]}
          />
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-5" aria-label="New approval request">
          <TextField
            label="Subject"
            isRequired
            value={subject}
            onChange={setSubject}
            mono
            placeholder="payment:pay_123"
            description="What the request is about, shown in the approvals list. Up to 256 characters."
          />
          <TransactionContextField value={tc} onChange={setTc} />
          <RadioCards
            label="Approval mode"
            value={mode}
            onChange={(v) => setMode(v as ApprovalMode)}
            columns={2}
            options={MODES.map((m) => ({ id: m.id, label: humanize(m.id), description: m.description }))}
          />
          <div className="grid gap-4 sm:grid-cols-3">
            <TextField
              label="Approving roles"
              isRequired
              value={roles}
              onChange={setRoles}
              mono
              className="sm:col-span-2"
              description="Comma separated. An approver qualifies when the actions of their own valid grant include the role."
            />
            {mode === "K_OF_N" ? <TextField label="k (approvals needed)" value={k} onChange={setK} inputMode="numeric" mono /> : null}
            <Select label="Expires after" value={ttl} onChange={setTtl} options={TTL} />
          </div>
          <p className="text-xs text-ink-muted">You cannot approve your own request, and every approver must act under a grant they hold.</p>
          <FormProblem>{problem}</FormProblem>
          {create.error ? <ApiErrorView error={create.error} title="The request was not opened" /> : null}
        </form>
      )}
    </Dialog>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Decide
 * ---------------------------------------------------------------------------------------------- */

export function DecideApprovalDialog({ target, onClose }: { target: ApprovalListItem | null; onClose: () => void }) {
  const me = useMe();
  const [requestId, setRequestId] = useState(target?.approval_request_id ?? "");
  const [verdict, setVerdict] = useState<"APPROVE" | "DENY">("APPROVE");
  const [grant, setGrant] = useState("");
  const [tc, setTc] = useState("");
  const [risk, setRisk] = useState("3");
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<ApprovalDecisionResult | null>(null);

  const decide = useCellMutation((api, v: { id: string; body: ApprovalDecisionCreate }) => api.authority.decideApproval(v.id, v.body), {
    invalidate: [APPROVALS_KEY],
    success: (r) => `Decision recorded: ${humanize(r.state)}`,
    onSuccess: (r) => setResult(r),
  });

  const ownRequest = Boolean(target && me && target.requester_principal_id === me);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!UUID_RE.test(requestId.trim())) return setProblem("Enter the approval request ID.");
    if (!UUID_RE.test(grant)) return setProblem("Choose the grant you are acting under.");
    if (!tc.trim()) return setProblem("Enter the exact transaction context of the request.");
    setProblem(null);
    decide.mutate({ id: requestId.trim(), body: { verdict, authority_grant_id: grant, transaction_context: tc, risk_class: Number(risk) } });
  }

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !decide.isPending && onClose()}
      title="Decide on approval request"
      description="Your decision is recorded against your own grant and bound to the transaction context."
      size="lg"
      footer={
        result ? (
          <Button onPress={onClose}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" isDisabled={decide.isPending} onPress={onClose}>
              Cancel
            </Button>
            <Button variant={verdict === "DENY" ? "danger" : "primary"} pending={decide.isPending} isDisabled={ownRequest} onPress={() => submit()}>
              {verdict === "DENY" ? "Deny request" : "Approve request"}
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-4">
          <Banner tone={result.authority_valid ? "ok" : "warn"} icon={Stamp} title={`Request is ${humanize(result.state).toLowerCase()}`}>
            {result.authority_valid
              ? "Your grant was valid at decision time, so your decision counts toward the request."
              : "Your grant was not valid at the requested risk class, so your decision was recorded but does not count."}
          </Banner>
          <ApprovalOutcome view={result} />
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-5" aria-label="Decide on approval request">
          {target ? (
            <KeyValues
              items={[
                ["Request", <CopyId key="id" value={target.approval_request_id} label="approval request" />],
                ["Subject", <Code key="s">{target.subject_ref}</Code>],
                ["Mode", humanize(target.mode)],
                ["Approvals", <span key="a" className="tabular">{`${target.approvals_received} of ${target.required_approvals}`}</span>],
                ["Roles", target.roles.join(", ")],
                ["Expires", <DateTime key="e" value={target.expires_at} />],
              ]}
            />
          ) : (
            <TextField label="Approval request ID" isRequired value={requestId} onChange={setRequestId} mono />
          )}
          {ownRequest ? (
            <Banner tone="warn" title="This is your own request">
              Self-approval is not allowed. Another administrator must decide on it.
            </Banner>
          ) : null}
          <RadioCards
            label="Decision"
            value={verdict}
            onChange={(v) => setVerdict(v as "APPROVE" | "DENY")}
            columns={2}
            options={[
              { id: "APPROVE", label: "Approve", description: "Counts toward the required approvals." },
              { id: "DENY", label: "Deny", description: "Rejects this exact transaction." },
            ]}
          />
          <GrantPicker
            label="Acting under my grant"
            isRequired
            value={grant}
            onChange={setGrant}
            granteeId={me}
            description="Your roles are the actions of this grant's effective authority, resolved by the cell now."
          />
          <TransactionContextField value={tc} onChange={setTc} />
          <Select
            label="Resolve my grant at risk class"
            value={risk}
            onChange={setRisk}
            options={RISK_OPTIONS}
            description="Approvals for critical actions need a grant valid at R3."
          />
          <FormProblem>{problem}</FormProblem>
          {decide.error ? <ApiErrorView error={decide.error} title="The decision was not recorded" /> : null}
        </form>
      )}
    </Dialog>
  );
}

export function ApprovalOutcome({ view }: { view: { approval_request_id: string; state: string; codes: string[]; counted_approvers: string[] | number } }) {
  const counted = Array.isArray(view.counted_approvers) ? view.counted_approvers.length : view.counted_approvers;
  return (
    <KeyValues
      items={[
        ["Request", <CopyId key="id" value={view.approval_request_id} label="approval request" />],
        ["State", <StatusBadge key="s" state={view.state} kind="Approval" />],
        ["Approvals counted", <span key="c" className="tabular">{counted}</span>],
        [
          "Codes",
          view.codes.length ? (
            <span key="codes" className="flex flex-wrap gap-1">
              {view.codes.map((c) => (
                <Code key={c}>{c}</Code>
              ))}
            </span>
          ) : (
            "—"
          ),
        ],
      ]}
    />
  );
}
