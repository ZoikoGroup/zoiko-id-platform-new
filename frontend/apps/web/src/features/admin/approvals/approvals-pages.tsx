"use client";

import { ApiError, type ApprovalCase, type ApprovalCaseState, type GovernanceReviewer } from "@zoiko/api-client";
import {
  Badge,
  Banner,
  Button,
  Card,
  CopyId,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  PageHeader,
  Select,
  TextField,
  type Column,
} from "@zoiko/ui";
import { Check, ClipboardCheck, Copy, Scale, UserCheck, X } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { approvalRefusalText, approveLabel, approvedVersusApplied, caseNotice } from "@/lib/approvals";
import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";
import { StepUpCancelled } from "@/lib/step-up";

import {
  REVIEWER_IS_NOT,
  RELATIONSHIPS,
  STATE_WORDS,
  actionWords,
  attempt,
  canCancel,
  canExecute,
  canRevise,
  canVote,
  emptyReviewerForm,
  executionKey,
  payloadRows,
  reviewerFormErrors,
  reviewerStatusTone,
  stateTone,
  toAppointment,
  voteDraft,
  type ReviewerForm,
} from "./logic";

const CASES_KEY = ["approval-cases"];
const caseKey = (id: string) => ["approval-cases", id];
const REVIEWERS_KEY = ["governance", "reviewers"];
const caseHref = (id: string) => `/admin/approvals/${encodeURIComponent(id)}`;

/** A refusal from the server in the records' own words (ZID-ENG-DEC-010 section 08). A cancelled passkey prompt is not an error. */
function Refusal({ error, title }: { error: unknown; title: string }) {
  if (error instanceof StepUpCancelled) return null;
  if (error instanceof ApiError && error.code.startsWith("APPROVAL_")) {
    return (
      <Banner tone="warn" title={title} live="polite">
        {approvalRefusalText(error.code)}
      </Banner>
    );
  }
  if (error instanceof ApiError && error.code.startsWith("VOTER_")) {
    return (
      <Banner tone="warn" title={title} live="polite">
        {approvalRefusalText(error.code)}
      </Banner>
    );
  }
  return <ApiErrorView error={error} title={title} />;
}

const STATE_FILTERS: { id: string; label: string }[] = [
  { id: "", label: "Every state" },
  ...(["PENDING_APPROVAL", "PARTIALLY_APPROVED", "APPROVED_READY", "FAILED", "EXECUTED", "REJECTED", "EXPIRED", "INVALIDATED", "CANCELLED"] as ApprovalCaseState[]).map(
    (s) => ({ id: s, label: STATE_WORDS[s] }),
  ),
];

function StateBadge({ c }: { c: Pick<ApprovalCase, "state" | "hold"> }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Badge tone={stateTone(c.state)}>{STATE_WORDS[c.state]}</Badge>
      {c.hold ? <Badge tone="warn">{c.hold === "BLOCKED_INSUFFICIENT_APPROVERS" ? "Held: no independent approvers" : "Held: no independent reviewer"}</Badge> : null}
    </span>
  );
}

/** The inbox: every approval case of the organization, newest first. What a person may do with one is on its page. */
export function ApprovalsInboxPage() {
  const [state, setState] = useState("");
  const list = useCellQuery([...CASES_KEY, "list", state], (api) => api.approvals.list(state ? { state: state as ApprovalCaseState, limit: 100 } : { limit: 100 }), {
    refetchInterval: 15_000,
  });
  const columns: Column<ApprovalCase>[] = [
    {
      key: "what",
      header: "Request",
      render: (c) => (
        <Link className="font-medium text-brand-700 underline-offset-2 hover:underline" href={caseHref(c.case_id)}>
          {actionWords(c.action_id)}
        </Link>
      ),
      sortValue: (c) => c.action_id,
    },
    { key: "tier", header: "Risk", render: (c) => <Badge tone={c.tier === "A2" ? "danger" : "warn"}>{c.tier === "A2" ? "Critical (A2)" : "Sensitive (A1)"}</Badge>, sortValue: (c) => c.tier },
    { key: "state", header: "State", render: (c) => <StateBadge c={c} />, sortValue: (c) => c.state },
    { key: "progress", header: "Approvals", render: (c) => `${c.approvals_received} of ${c.approvals_required}`, sortValue: (c) => c.approvals_received },
    { key: "requester", header: "Requested by", render: (c) => <CopyId value={c.requester_principal_id} label="requester" />, defaultHidden: true },
    { key: "expires", header: "Expires", render: (c) => <DateTime value={c.expires_at} />, sortValue: (c) => c.expires_at },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        icon={ClipboardCheck}
        title="Approvals"
        description="Sensitive and critical changes need independent people. A critical change needs two people other than the one who asked. Nothing changes until an approved request is applied."
        actions={
          <Link className="text-sm text-brand-700 underline-offset-2 hover:underline" href="/admin/approvals/governance">
            Governance and reviewers
          </Link>
        }
      />
      <Card title="Requests">
        <div className="mb-3 max-w-xs">
          <Select label="Show" value={state} onChange={setState} options={STATE_FILTERS} />
        </div>
        <QueryView query={list}>
          {(d) =>
            d.items.length === 0 ? (
              <EmptyState title="No approval requests">Requests for owner changes, privileged roles and recovery appear here.</EmptyState>
            ) : (
              <DataTable caption="Approval requests" columns={columns} rows={d.items} rowKey={(c) => c.case_id} />
            )
          }
        </QueryView>
      </Card>
    </div>
  );
}

/** WF review dialog as a page: the actual requester, the proposed values, region, reason, risk, votes and the server's own notice. */
export function ApprovalCasePage({ caseId }: { caseId: string }) {
  const { proveAction } = useRuntime();
  const q = useCellQuery(caseKey(caseId), (api) => api.approvals.read(caseId), { refetchInterval: 10_000 });
  const [reviseOpen, setReviseOpen] = useState(false);
  const [once, setOnce] = useState<Record<string, unknown> | null>(null);
  const [tries] = useState(attempt);
  const [runs, setRuns] = useState(0);
  const refresh = { invalidate: [caseKey(caseId), CASES_KEY] };

  const vote = useCellMutation(async (api, v: { c: ApprovalCase; vote: "APPROVE" | "REJECT" }) => {
    const proof = await proveAction(voteDraft(v.c, v.vote));
    if (!proof) throw new Error("A passkey confirmation is required for a vote.");
    return api.approvals.vote(v.c.case_id, v.vote, v.c.request_hash, proof);
  }, { ...refresh, success: (c) => (c.state === "REJECTED" ? "Rejected" : "Your vote was recorded") });
  const cancel = useCellMutation((api) => api.approvals.cancel(caseId), { ...refresh, success: "Request withdrawn" });
  const execute = useCellMutation((api, key: string) => api.approvals.execute(caseId, key), {
    ...refresh,
    success: (r) => (r.outcome === "EXECUTED" ? "Applied" : "Not applied"),
    // the enrollment link is kept in this component only, shown once, never written to the query cache or storage
    onSuccess: (r) => setOnce(r.shown_once ?? null),
  });

  return (
    <div className="space-y-6">
      <QueryView query={q}>
        {(c) => {
          const when = approvedVersusApplied(c);
          return (
            <>
              <PageHeader
                icon={Scale}
                title={actionWords(c.action_id)}
                description={c.summary}
                breadcrumbs={[{ label: "Approvals", href: "/admin/approvals" }, { label: "Request" }]}
                meta={
                  <>
                    <StateBadge c={c} />
                    <Badge tone={c.tier === "A2" ? "danger" : "warn"}>{c.tier === "A2" ? "Critical (A2): two independent approvals" : "Sensitive (A1): one independent approval"}</Badge>
                    {c.version > 1 ? <Badge tone="info">Version {c.version}</Badge> : null}
                  </>
                }
              />
              <Banner tone={c.state === "EXECUTED" ? "ok" : c.hold ? "warn" : "info"} title={`${c.approvals_received} of ${c.approvals_required} independent approvals`} live="polite">
                {caseNotice(c)}
              </Banner>

              <Card title="What is being asked">
                <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                  <Row label="Requested by"><CopyId value={c.requester_principal_id} label="requester" /></Row>
                  {c.beneficiary_principal_id ? <Row label="For"><CopyId value={c.beneficiary_principal_id} label="account" /></Row> : null}
                  <Row label="Region">{c.region}</Row>
                  <Row label="Reason">{c.reason}</Row>
                  <Row label="Expires"><DateTime value={c.expires_at} /></Row>
                  <Row label="Policy version"><span className="font-mono text-xs">{c.policy_version}</span></Row>
                </dl>
                <h3 className="mt-4 text-sm font-semibold text-ink">Proposed change</h3>
                <dl className="mt-1 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                  {payloadRows(c.payload).map((r) => (
                    <Row key={r.label} label={r.label}><span className="break-all font-mono text-xs">{r.value}</span></Row>
                  ))}
                </dl>
                <p className="mt-3 rounded-md bg-sunken px-3 py-2 text-sm text-ink-muted">
                  <strong className="text-ink">Check before approving:</strong> {c.gate}
                </p>
                <p className="mt-2 text-xs text-ink-subtle">
                  Request fingerprint <span className="font-mono">{c.request_hash.slice(0, 16)}…</span>. Your approval covers exactly this version; any change is a new request.
                </p>
              </Card>

              <Card title="Approvals so far">
                {c.votes.length === 0 ? (
                  <p className="text-sm text-ink-muted">No votes yet.</p>
                ) : (
                  <ul className="divide-y divide-border text-sm">
                    {c.votes.map((v) => (
                      <li key={v.vote_id} className="flex flex-wrap items-center gap-2 py-2">
                        <Badge tone={v.vote === "APPROVE" ? "ok" : "danger"}>{v.vote === "APPROVE" ? "Approved" : "Rejected"}</Badge>
                        <span>Person <span className="font-mono text-xs">{v.person_ref}</span> as {v.authority_basis.toLowerCase()}</span>
                        <span className="text-ink-muted">confirmed with a {v.step_up_tier ?? "passkey"} proof, <DateTime value={v.decided_at} /></span>
                      </li>
                    ))}
                  </ul>
                )}
                <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                  <Row label="Approved">{when.approved ? <DateTime value={when.approved} /> : "Not yet"}</Row>
                  <Row label="Applied">{when.applied ? <DateTime value={when.applied} /> : "Not yet"}</Row>
                </dl>
              </Card>

              <Card title="Your decision">
                {c.you && !c.you.may_vote && c.you.reasons.length > 0 ? (
                  <p className="mb-3 text-sm text-ink-muted" id="why-no-vote">{approvalRefusalText(c.you.reasons[0])}</p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    icon={<Check aria-hidden className="size-4" />}
                    isDisabled={!canVote(c)}
                    pending={vote.isPending}
                    aria-describedby={!canVote(c) ? "why-no-vote" : undefined}
                    onPress={() => vote.mutate({ c, vote: "APPROVE" })}
                  >
                    {approveLabel(c)}
                  </Button>
                  <Button variant="secondary" icon={<X aria-hidden className="size-4" />} isDisabled={!canVote(c)} pending={vote.isPending} onPress={() => vote.mutate({ c, vote: "REJECT" })}>
                    Reject
                  </Button>
                  {canExecute(c) ? (
                    <Button
                      variant="secondary"
                      pending={execute.isPending}
                      onPress={() => {
                        setRuns((n) => n + 1);
                        execute.mutate(executionKey(c.case_id, `${tries}-${runs}`));
                      }}
                    >
                      {c.state === "FAILED" ? "Run again" : "Apply this change"}
                    </Button>
                  ) : null}
                  {canRevise(c) ? (
                    <Button variant="secondary" onPress={() => setReviseOpen(true)}>Change the request</Button>
                  ) : null}
                  {canCancel(c) ? (
                    <Button variant="secondary" pending={cancel.isPending} onPress={() => cancel.mutate(undefined)}>Withdraw</Button>
                  ) : null}
                </div>
                {c.hold ? (
                  <p className="mt-3 text-sm text-ink-muted">
                    Nobody independent can decide this yet. <Link className="text-brand-700 underline" href="/admin/approvals/governance">Appoint a verified independent reviewer</Link>. It is never waived.
                  </p>
                ) : null}
                <div className="mt-3 space-y-2">
                  {vote.error ? <Refusal error={vote.error} title="Your vote was not recorded" /> : null}
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
              <ReviseDialog c={c} isOpen={reviseOpen} onOpenChange={setReviseOpen} onDone={(id) => (window.location.href = caseHref(id))} />
            </>
          );
        }}
      </QueryView>
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-ink-subtle">{label}</dt>
      <dd className="text-ink">{children}</dd>
    </div>
  );
}

/** A secret the change produced (an enrollment link). Held in memory for this view only, shown once, never stored. */
function ShownOnce({ secret, onDone }: { secret: Record<string, unknown>; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const ticket = typeof secret.enrollment_ticket === "string" ? secret.enrollment_ticket : null;
  return (
    <Banner tone="warn" title="Copy this now. It is shown once." live="polite">
      <div className="space-y-2">
        <p>This single-use link is not stored and cannot be shown again. Give it only to the person it is for.</p>
        {ticket ? (
          <div className="flex flex-wrap items-center gap-2">
            <code className="break-all rounded bg-sunken px-2 py-1 text-xs">{ticket}</code>
            <Button
              size="sm"
              variant="secondary"
              icon={copied ? <Check aria-hidden className="size-4" /> : <Copy aria-hidden className="size-4" />}
              onPress={() => void navigator.clipboard.writeText(ticket).then(() => setCopied(true))}
            >
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        ) : null}
        {typeof secret.expires_at === "string" ? <p className="text-xs">Expires <DateTime value={secret.expires_at} />.</p> : null}
        <Button size="sm" onPress={onDone}>I have copied it</Button>
      </div>
    </Banner>
  );
}

/** A material change is a new version: every vote on the old one is void and the person who changes it can never vote. */
function ReviseDialog({ c, isOpen, onOpenChange, onDone }: { c: ApprovalCase; isOpen: boolean; onOpenChange: (o: boolean) => void; onDone: (id: string) => void }) {
  const [text, setText] = useState(() => JSON.stringify(c.payload, null, 2));
  const [reason, setReason] = useState("");
  const parsed = (() => {
    try {
      const v: unknown = JSON.parse(text);
      return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  })();
  const revise = useCellMutation((api) => api.approvals.revise(c.case_id, parsed ?? {}, reason.trim()), {
    invalidate: [CASES_KEY],
    success: "A new version was created",
    onSuccess: (n) => onDone(n.case_id),
  });
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      title="Change the request"
      description="This creates a new version. Approvals already given to this version are void, and you will not be able to approve the new one."
      footer={
        <>
          <Button variant="secondary" onPress={() => onOpenChange(false)}>Cancel</Button>
          <Button isDisabled={!parsed || !reason.trim()} pending={revise.isPending} onPress={() => revise.mutate(undefined)}>Create new version</Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Proposed change (JSON)" value={text} onChange={setText} multiline rows={6} mono errorMessage={parsed ? undefined : "This is not a valid JSON object."} />
        <TextField label="Why it changed" value={reason} onChange={setReason} isRequired />
        {revise.error ? <Refusal error={revise.error} title="The new version was not created" /> : null}
      </div>
    </Dialog>
  );
}

/** Solo status, the appointed independent reviewers, and the form to appoint one. Held requests are never waived. */
export function GovernancePage() {
  const solo = useCellQuery(["governance", "solo"], (api) => api.approvals.soloStatus());
  const reviewers = useCellQuery(REVIEWERS_KEY, (api) => api.approvals.reviewers());
  const [form, setForm] = useState<ReviewerForm>(emptyReviewerForm);
  const [shown, setShown] = useState(false);
  const errors = reviewerFormErrors(form);
  const appoint = useCellMutation((api) => api.approvals.appointReviewer(toAppointment(form)), {
    invalidate: [REVIEWERS_KEY, CASES_KEY, ["governance", "solo"]],
    success: "Reviewer proposed. Their appointment needs an independent approval.",
    onSuccess: () => {
      setForm(emptyReviewerForm);
      setShown(false);
    },
  });
  const revoke = useCellMutation((api, id: string) => api.approvals.revokeReviewer(id), { invalidate: [REVIEWERS_KEY, ["governance", "solo"]], success: "Appointment ended" });
  const set = (k: keyof ReviewerForm) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const err = (k: keyof ReviewerForm) => (shown ? errors[k] : undefined);
  const columns: Column<GovernanceReviewer>[] = [
    { key: "who", header: "Reviewer", render: (r) => <CopyId value={r.principal_id} label="reviewer account" /> },
    { key: "rel", header: "Relationship", render: (r) => RELATIONSHIPS.find((x) => x.id === r.relationship)?.label ?? r.relationship },
    { key: "status", header: "Status", render: (r) => <Badge tone={reviewerStatusTone(r)}>{r.status === "PROPOSED" ? "Proposed, not yet active" : r.status === "ACTIVE" ? "Active" : r.status === "REVOKED" ? "Ended" : "Expired"}</Badge> },
    { key: "via", header: "Verified by", render: (r) => (r.activated_via === "PLATFORM_VERIFICATION" ? "Zoiko iD verification" : r.activated_via === "A1_APPROVAL" ? "Independent approval" : "Not yet"), defaultHidden: true },
    { key: "exp", header: "Valid until", render: (r) => <DateTime value={r.expires_at} /> },
    {
      key: "act",
      header: "",
      render: (r) =>
        r.status === "PROPOSED" || r.status === "ACTIVE" ? (
          <Button size="sm" variant="secondary" pending={revoke.isPending} onPress={() => revoke.mutate(r.reviewer_id)}>End appointment</Button>
        ) : null,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        icon={UserCheck}
        title="Governance and independent reviewers"
        description="Who can independently approve your organization's sensitive and critical changes."
        breadcrumbs={[{ label: "Approvals", href: "/admin/approvals" }, { label: "Governance" }]}
      />
      <QueryView query={solo}>
        {(s) => (
          <Banner tone={s.solo_mode ? "info" : "ok"} title={s.solo_mode ? "Solo organization" : `${s.eligible_human_member_count} people can approve`}>
            {s.message ?? `Critical changes need ${s.a2_requires}.`} Being a solo organization never lowers this: a critical change still needs two other people, and it waits until they exist.
            {s.independent_routes.length ? " You have an appointed independent reviewer." : ""}
            {!s.legal_form_resolved ? " Your legal form and who may represent the company are decided by the company verification, not by the number of members." : ""}
          </Banner>
        )}
      </QueryView>
      <Card title="Appointed reviewers">
        <p className="mb-3 text-sm text-ink-muted">{REVIEWER_IS_NOT}</p>
        <QueryView query={reviewers}>
          {(d) =>
            d.items.length === 0 ? (
              <EmptyState title="No reviewers appointed">Appoint a verified outside person so critical changes can be approved without hiring.</EmptyState>
            ) : (
              <DataTable caption="Independent reviewers" columns={columns} rows={d.items} rowKey={(r) => r.reviewer_id} />
            )
          }
        </QueryView>
        <p className="mt-3 text-xs text-ink-subtle">
          An appointment is only a proposal until an independent person approves it, or Zoiko iD verifies the reviewer&apos;s identity and authority. You cannot appoint yourself. Zoiko iD Assured Review is not available.
        </p>
      </Card>
      <Card title="Appoint a reviewer">
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            setShown(true);
            if (Object.keys(errors).length === 0) appoint.mutate(undefined);
          }}
        >
          <TextField label="Reviewer's account id" value={form.principal} onChange={set("principal")} errorMessage={err("principal")} mono isRequired description="They need an account in this organization and a passkey." />
          <Select label="How they relate to you" value={form.relationship} onChange={set("relationship")} options={RELATIONSHIPS} errorMessage={err("relationship")} isRequired />
          <TextField label="Documented authority" value={form.basis} onChange={set("basis")} errorMessage={err("basis")} multiline rows={3} isRequired description="For example a licence, a trust deed or a directorship on the register." />
          <TextField label="Independence declaration" value={form.declaration} onChange={set("declaration")} errorMessage={err("declaration")} multiline rows={3} isRequired description="State that they have no tie that would stop them judging independently." />
          <TextField className="sm:col-span-2" label="People they have a relationship with (optional)" value={form.conflicts} onChange={set("conflicts")} errorMessage={err("conflicts")} multiline rows={2} mono description="Account ids, one per line. They cannot approve those people's requests." />
          <div className="sm:col-span-2">
            <Button type="submit" pending={appoint.isPending}>Propose this reviewer</Button>
          </div>
          {appoint.error ? <div className="sm:col-span-2"><Refusal error={appoint.error} title="The reviewer was not proposed" /></div> : null}
        </form>
      </Card>
      {revoke.error ? <Refusal error={revoke.error} title="The appointment was not ended" /> : null}
    </div>
  );
}
