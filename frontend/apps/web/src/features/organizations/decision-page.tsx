"use client";

import { ApiError, type OrgApprovalCase, type OrgDecision } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, CopyId, DateTime, Dialog, EmptyState, LoadingRows, PageHeader, TextField } from "@zoiko/ui";
import { Check, MessageSquareWarning, Scale, X } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { attempt, executionKey } from "@/features/admin/approvals/logic";
import { approvalRefusalText, approveLabel, approvedVersusApplied, caseNotice } from "@/lib/approvals";
import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import {
  GOVERNANCE_HREF,
  canApply,
  canDecide,
  canRequestChanges,
  canResubmit,
  decisionKey,
  deltaRows,
  holdWords,
  inboxHref,
  isRequesterView,
  levelWords,
  needsReason,
  needsStepUp,
  progressWords,
  refusalKind,
  requestHref,
  statusTone,
  stepUpDraft,
} from "./logic";
import { AttachmentsCard } from "./attachments";
import { StatusWord } from "./parts";
import { Refusal, ReviseDialog, Row, ShownOnce, useOrgId } from "./shared";

const KEY = ["org-approvals"];

/** WF-13C-03: the exact change, its evidence summary, who has approved, and the decision. The server decides quorum and eligibility. */
export function DecisionPage({ orgParam, requestId }: { orgParam: string; requestId: string }) {
  const { orgId, loading, error } = useOrgId(orgParam);
  const q = useCellQuery([...KEY, "case", orgId, requestId], (api) => api.org.approval(orgId as string, requestId), {
    enabled: Boolean(orgId),
    refetchInterval: 15_000,
  });
  if (!orgId) {
    if (loading) return <LoadingRows rows={4} />;
    return error ? <ApiErrorView error={error} /> : <EmptyState title="No organization">Sign in to an organization to see its approvals.</EmptyState>;
  }
  return (
    <QueryView query={q} notFound={<EmptyState title="No such request">It does not exist, or it is not one you can open.</EmptyState>}>
      {(c) => <Decision orgId={orgId} c={c} refetch={() => void q.refetch()} />}
    </QueryView>
  );
}

function Decision({ orgId, c, refetch }: { orgId: string; c: OrgApprovalCase; refetch: () => void }) {
  const { proveAction } = useRuntime();
  const [dialog, setDialog] = useState<OrgDecision | null>(null);
  const [reviseOpen, setReviseOpen] = useState(false);
  const [once, setOnce] = useState<Record<string, unknown> | null>(null);
  const [tries] = useState(attempt);
  const runs = useRef(0);
  const busy = useRef(false);

  const decide = useCellMutation(
    async (api, v: { decision: OrgDecision; reason?: string }) => {
      if (busy.current) throw new Error("A decision is already being sent.");
      busy.current = true;
      try {
        let proof: string | undefined;
        if (needsStepUp(v.decision)) {
          proof = await proveAction(stepUpDraft(c, v.decision === "APPROVE" ? "APPROVE" : "REJECT"));
          if (!proof) throw new Error("A passkey confirmation is required for this decision.");
        }
        // If-Match is the version this person read: if the request changed since, the server answers 409 and records nothing.
        return await api.org.decide(orgId, c.case_id, { decision: v.decision, reason: v.reason }, c.request_hash, {
          proof,
          idempotencyKey: decisionKey(c.case_id, v.decision, tries),
        });
      } finally {
        busy.current = false;
      }
    },
    {
      invalidate: [KEY],
      success: (r) => (r.state === "NEEDS_CHANGES" ? "Sent back for changes" : r.state === "REJECTED" ? "Denied" : "Your approval was recorded"),
      onSuccess: () => setDialog(null),
    },
  );
  const execute = useCellMutation((api, key: string) => api.approvals.execute(c.case_id, key), {
    invalidate: [KEY],
    success: (r) => (r.outcome === "EXECUTED" ? "Applied" : "Not applied"),
    onSuccess: (r) => setOnce(r.shown_once ?? null),
  });
  const cancel = useCellMutation((api) => api.approvals.cancel(c.case_id), { invalidate: [KEY], success: "Request withdrawn" });

  const conflict = decide.error instanceof ApiError && refusalKind(decide.error.code, decide.error.status) === "conflict";
  const decidable = canDecide(c) && !conflict;
  const when = approvedVersusApplied(c);
  const { facts, change } = deltaRows(c);
  const why = c.you && !c.you.may_vote && c.you.reasons.length > 0 ? approvalRefusalText(c.you.reasons[0]) : null;
  const hold = holdWords(c.execution_hold);

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Scale}
        title={c.summary}
        description={c.reason}
        breadcrumbs={[{ label: "Approvals", href: inboxHref(orgId) }, { label: "Request" }]}
        meta={
          <>
            <StatusWord word={c.status_label} tone={statusTone(c.state)} />
            <Badge tone={c.level === "A2" ? "danger" : "warn"}>{levelWords(c.level)}</Badge>
            <Badge tone="neutral">Version {c.version}</Badge>
          </>
        }
      />

      <Banner tone={c.state === "EXECUTED" ? "ok" : c.hold || c.execution_hold ? "warn" : "info"} title={progressWords(c)} live="polite">
        {caseNotice(c)}
      </Banner>
      {hold ? (
        <Banner tone="warn" title="This change is held" live="polite">
          {hold}
        </Banner>
      ) : null}
      {conflict ? (
        <Banner
          tone="warn"
          title="This request changed"
          live="assertive"
          action={
            <Button
              size="sm"
              className="min-h-11"
              onPress={() => {
                decide.reset();
                refetch();
              }}
            >
              Refresh
            </Button>
          }
        >
          This request changed since you opened it. The form is disabled. Refresh to read the current version before you decide.
        </Banner>
      ) : null}

      <Card title="The change you are asked to authorize" description="Immutable. Your decision covers exactly this version; any change is a new request.">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {facts.map((f) => (
            <Row key={f.label} label={f.label}>
              {f.label === "Expires" ? <DateTime value={f.value} /> : <span className={f.label === "Request id" || f.label === "Requested by" || f.label === "Policy version" ? "break-all font-mono text-xs" : undefined}>{f.value}</span>}
            </Row>
          ))}
          {c.beneficiary_principal_id ? (
            <Row label="Change is for">
              <CopyId value={c.beneficiary_principal_id} label="account" />
            </Row>
          ) : null}
        </dl>
        <h3 className="mt-4 text-sm font-semibold text-ink">Proposed change</h3>
        <dl className="mt-1 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {change.map((r) => (
            <Row key={r.label} label={r.label}>
              <span className="break-all font-mono text-xs">{r.value}</span>
            </Row>
          ))}
        </dl>
        <p className="mt-3 rounded-md bg-sunken px-3 py-2 text-sm text-ink-muted">
          <strong className="text-ink">Check before approving:</strong> {c.gate}
        </p>
        {c.gates.length ? (
          <ul className="mt-2 space-y-1 text-sm" aria-label="Checks made by the platform">
            {c.gates.map((g) => (
              <li key={g.code}>
                <Badge tone={g.ok ? "ok" : g.blocking ? "danger" : "info"}>{g.ok ? "Checked" : g.blocking ? "Not met" : "For your information"}</Badge> <span className="text-ink-muted">{g.note}</span>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="mt-2 text-xs text-ink-muted">
          Request fingerprint <span className="font-mono">{c.request_hash.slice(0, 16)}…</span>
        </p>
      </Card>

      <AttachmentsCard orgId={orgId} requestId={c.case_id} reference={c.evidence.reference ?? null} />

      <Card title="Approval progress">
        {isRequesterView(c) ? (
          <p className="mb-2 text-sm text-ink">
            <Badge tone="neutral">Not eligible</Badge> {why ?? approvalRefusalText("VOTER_IS_REQUESTER")}
          </p>
        ) : null}
        {c.votes.length === 0 ? (
          <p className="text-sm text-ink-muted">No decisions yet.</p>
        ) : (
          <ul className="divide-y divide-border text-sm">
            {c.votes.map((v) => (
              <li key={v.vote_id} className="flex flex-wrap items-center gap-2 py-2">
                <Badge tone={v.vote === "APPROVE" ? "ok" : "danger"}>{v.vote === "APPROVE" ? "Approved" : "Rejected"}</Badge>
                <span>
                  Person <span className="font-mono text-xs">{v.person_ref}</span> as {v.authority_basis.toLowerCase()}
                </span>
                <span className="text-ink-muted">
                  confirmed with a {v.step_up_tier ?? "passkey"} proof, <DateTime value={v.decided_at} />
                </span>
              </li>
            ))}
          </ul>
        )}
        <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          <Row label="Approved">{when.approved ? <DateTime value={when.approved} /> : "Not yet"}</Row>
          <Row label="Applied">{when.applied ? <DateTime value={when.applied} /> : "Not yet"}</Row>
        </dl>
        {c.hold ? (
          <p className="mt-3 text-sm text-ink-muted">
            <strong className="text-ink">Independent approvers required.</strong> Nobody independent can decide this yet.{" "}
            <Link className="inline-flex min-h-11 items-center text-brand-700 underline" href={GOVERNANCE_HREF}>
              Invite or appoint independent approvers
            </Link>
            . It is never waived.
          </p>
        ) : null}
      </Card>

      <Card title="Your decision">
        {!isRequesterView(c) && why ? (
          <p className="mb-3 text-sm text-ink-muted" id="why-no-decision">
            {why}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button
            className="min-h-11"
            icon={<Check aria-hidden className="size-4" />}
            isDisabled={!decidable}
            pending={decide.isPending && decide.variables?.decision === "APPROVE"}
            aria-describedby={!decidable && why ? "why-no-decision" : undefined}
            onPress={() => decide.mutate({ decision: "APPROVE" })}
          >
            {approveLabel(c)}
          </Button>
          <Button variant="secondary" className="min-h-11" icon={<X aria-hidden className="size-4" />} isDisabled={!decidable || decide.isPending} onPress={() => setDialog("REJECT")}>
            Reject
          </Button>
          <Button variant="secondary" className="min-h-11" icon={<MessageSquareWarning aria-hidden className="size-4" />} isDisabled={!canRequestChanges(c) || conflict || decide.isPending} onPress={() => setDialog("REQUEST_CHANGES")}>
            Request changes
          </Button>
          {canApply(c) ? (
            <Button
              variant="secondary"
              className="min-h-11"
              pending={execute.isPending}
              onPress={() => {
                runs.current += 1;
                execute.mutate(executionKey(c.case_id, `${tries}-${runs.current}`));
              }}
            >
              {c.state === "FAILED" ? "Run again" : "Apply this change"}
            </Button>
          ) : null}
          {canResubmit(c) ? (
            <Button variant="secondary" className="min-h-11" onPress={() => setReviseOpen(true)}>
              Revise and resubmit
            </Button>
          ) : null}
          {isRequesterView(c) && ["PENDING_APPROVAL", "PARTIALLY_APPROVED", "APPROVED_READY", "FAILED"].includes(c.state) ? (
            <Button variant="secondary" className="min-h-11" pending={cancel.isPending} onPress={() => cancel.mutate(undefined)}>
              Withdraw
            </Button>
          ) : null}
        </div>
        <div className="mt-3 space-y-2" aria-live="polite">
          {decide.error && !conflict ? <Refusal error={decide.error} title="Your decision was not recorded" /> : null}
          {execute.error ? <Refusal error={execute.error} title="The change was not applied" /> : null}
          {cancel.error ? <Refusal error={cancel.error} title="The request was not withdrawn" /> : null}
          {execute.data?.outcome === "FAILED" ? (
            <Banner tone="warn" title="The change was not applied">
              {execute.data.guidance ?? "The approved request is unchanged and can be run again until it expires."}
            </Banner>
          ) : null}
        </div>
      </Card>

      {once ? <ShownOnce secret={once} onDone={() => setOnce(null)} /> : null}
      <ReasonDialog
        decision={dialog}
        pending={decide.isPending}
        error={decide.error && !conflict ? decide.error : null}
        onClose={() => setDialog(null)}
        onSubmit={(reason) => dialog && decide.mutate({ decision: dialog, reason })}
      />
      <ReviseDialog c={c} isOpen={reviseOpen} onOpenChange={setReviseOpen} onDone={(id) => (window.location.href = requestHref(orgId, id))} />
    </div>
  );
}

/** Reject and request changes both need a reason; request changes also voids every vote on this version. */
function ReasonDialog({
  decision,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  decision: OrgDecision | null;
  pending: boolean;
  error: unknown;
  onClose: () => void;
  onSubmit: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const reject = decision === "REJECT";
  const missing = needsReason(decision ?? "APPROVE") && !reason.trim();
  return (
    <Dialog
      isOpen={decision === "REJECT" || decision === "REQUEST_CHANGES"}
      onOpenChange={(o) => !o && onClose()}
      title={reject ? "Reject this request" : "Request changes"}
      description={
        reject
          ? "The request ends. Nothing changes. Say why so the requester can start a better one."
          : "This version ends and every approval already given to it is void. The requester revises it into a new version."
      }
      footer={
        <>
          <Button variant="secondary" className="min-h-11" onPress={onClose}>
            Cancel
          </Button>
          <Button
            className="min-h-11"
            pending={pending}
            onPress={() => {
              setTouched(true);
              if (!missing) onSubmit(reason.trim());
            }}
          >
            {reject ? "Reject request" : "Send back for changes"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Reason" value={reason} onChange={setReason} multiline rows={4} isRequired errorMessage={touched && missing ? "Give a reason." : undefined} inputClassName="min-h-11" />
        {error ? <Refusal error={error} title="That was not recorded" /> : null}
      </div>
    </Dialog>
  );
}
