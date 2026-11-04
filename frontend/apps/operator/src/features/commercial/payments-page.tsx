"use client";

import type { DisputeCase, DisputeEvidenceItem, ReconBoard, ReconRow, RefundCase } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, KeyValues, Select, TextField, type Column } from "@zoiko/ui";
import { CheckCircle2, Landmark, Scale, Undo2, XCircle } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { CapabilityHint } from "../recovery-shared";
import { roleLabel } from "./finance-logic";
import {
  DISPUTE_STATE,
  EVIDENCE_LABEL,
  LEG_LABEL,
  MATCH_LABEL,
  PERIOD_STATE,
  REFUND_CATEGORIES,
  REFUND_STATE,
  deadlineText,
  disputeActions,
  emptyRefundForm,
  lastPeriod,
  money,
  parseLegs,
  refundActions,
  refundBody,
  rolesStillNeeded,
  validPeriod,
  validateRefund,
  type RefundForm,
} from "./payments-logic";

export const PAYMENTS_KEY = ["operator", "payments"];
const CAP = "commercial.finance.manage";

function Failure({ m, title }: { m: { isError: boolean; error: unknown; reset: () => void }; title: string }) {
  if (!m.isError) return null;
  return (
    <div className="space-y-2">
      <CapabilityHint error={m.error as never} capability={CAP} />
      <ApiErrorView error={m.error as never} title={title} onRetry={() => m.reset()} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------------------------------- F03-A refunds

/** F03-A. A refund is limited to the overpayment, signed by the tier the policy picks, queued by someone else, and sent by the worker, never by this screen. */
export function RefundsView() {
  const [state, setState] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const list = useCellQuery([...PAYMENTS_KEY, "refunds", state], (api) => api.payments.refunds(state || undefined));
  const columns: Column<RefundCase>[] = [
    { key: "a", header: "Amount", render: (r) => money(r.amount_minor, r.currency), sortValue: (r) => r.amount_minor },
    { key: "c", header: "Category", render: (r) => r.category.toLowerCase().replace("_", " ") },
    { key: "s", header: "State", render: (r) => <Badge tone={REFUND_STATE[r.state].tone}>{REFUND_STATE[r.state].label}</Badge>, sortValue: (r) => r.state },
    { key: "n", header: "Signed", render: (r) => `${r.approvals.filter((a) => a.decision === "APPROVE").length} of ${r.required_roles.length}` },
    { key: "w", header: "Asked", render: (r) => new Date(r.created_at).toLocaleDateString("en-GB"), sortValue: (r) => r.created_at },
  ];
  return (
    <div className="space-y-4">
      <OpenRefund />
      <Card
        title="Refunds"
        description="Select one to read exactly what is being approved and what the processor has said."
        padded={false}
        actions={
          <Select
            label="State"
            hideLabel
            options={[{ id: "", label: "All states" }, ...Object.entries(REFUND_STATE).map(([id, s]) => ({ id, label: s.label }))]}
            value={state}
            onChange={setState}
            className="w-56"
          />
        }
      >
        <QueryView query={list}>
          {(d) => (
            <DataTable<RefundCase>
              caption="Refunds"
              columns={columns}
              rows={d.items}
              rowKey={(r) => r.refund_id}
              onRowAction={(r) => setOpen(r.refund_id)}
              empty={<EmptyState title="No refunds" icon={Undo2}>Open one above.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
      {open ? <RefundDetail id={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}

function OpenRefund() {
  const [f, setF] = useState<RefundForm>(emptyRefundForm());
  const [shown, setShown] = useState(false);
  const errors = validateRefund(f);
  const err = (k: keyof RefundForm) => (shown ? errors[k] : undefined);
  const set = (k: keyof RefundForm, v: string) => setF((c) => ({ ...c, [k]: v }));
  const open = useCellMutation((api, form: RefundForm) => api.payments.openRefund(refundBody(form)), {
    invalidate: [PAYMENTS_KEY],
    success: "Refund requested. The approvers its tier names can now sign it.",
    onSuccess: () => {
      setF(emptyRefundForm());
      setShown(false);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setShown(true);
    if (Object.keys(errors).length === 0) open.mutate(f);
  };
  return (
    <Card title="Ask for a refund" description="Only the amount the customer overpaid can go back. A refund and a credit note for the same money are never both granted, and a payment in dispute cannot be refunded.">
      <form onSubmit={submit} noValidate className="grid gap-3 sm:grid-cols-2" aria-label="Ask for a refund">
        <TextField label="Organization tenant ID" value={f.tenant} onChange={(v) => set("tenant", v)} errorMessage={err("tenant")} mono isRequired />
        <TextField label="Payment ID" value={f.payment} onChange={(v) => set("payment", v)} errorMessage={err("payment")} mono isRequired />
        <TextField label="Amount" value={f.amount} onChange={(v) => set("amount", v)} errorMessage={err("amount")} inputMode="decimal" isRequired />
        <Select label="Category" options={[...REFUND_CATEGORIES]} value={f.category} onChange={(v) => set("category", v)} />
        <TextField label="Evidence reference" value={f.evidence} onChange={(v) => set("evidence", v)} errorMessage={err("evidence")} />
        <TextField label="Reason" value={f.reason} onChange={(v) => set("reason", v)} errorMessage={err("reason")} isRequired />
        <div className="sm:col-span-2">
          <Button type="submit" isDisabled={open.isPending}>
            Ask for the refund
          </Button>
        </div>
      </form>
      <Failure m={open} title="The refund was not requested" />
    </Card>
  );
}

function RefundDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const live = useCellQuery([...PAYMENTS_KEY, "refund", id], (api) => api.payments.refund(id));
  const [role, setRole] = useState("");
  const [reason, setReason] = useState("");
  const decide = useCellMutation(
    (api, v: { r: RefundCase; decision: "APPROVE" | "REJECT" }) =>
      api.payments.decideRefund(v.r.refund_id, { role: role || rolesStillNeeded(v.r)[0]!, decision: v.decision, snapshot_hash: v.r.snapshot_hash, reason: reason.trim() || null }),
    { invalidate: [PAYMENTS_KEY], success: (x) => (x.state === "REJECTED" ? "Rejected" : x.state === "APPROVED" ? "Fully approved. A finance executor can queue it." : "Your signature is recorded.") },
  );
  const queue = useCellMutation((api, r: RefundCase) => api.payments.executeRefund(r.refund_id), {
    invalidate: [PAYMENTS_KEY],
    success: "Queued. The worker sends it once; this screen sends nothing to the processor.",
  });
  const cancel = useCellMutation((api, r: RefundCase) => api.payments.cancelRefund(r.refund_id), { invalidate: [PAYMENTS_KEY], success: "Refund withdrawn", onSuccess: onClose });
  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Refund" size="lg">
      <QueryView query={live}>
        {(r) => {
          const need = rolesStillNeeded(r);
          const actions = refundActions(r);
          const chosen = role || need[0] || "";
          return (
            <div className="space-y-4">
              <Banner tone={REFUND_STATE[r.state].tone === "danger" ? "danger" : "info"} title={r.label}>
                Never read this as: {r.never_claim.toLowerCase()}.
              </Banner>
              <KeyValues
                columns={2}
                items={[
                  ["Amount", money(r.amount_minor, r.currency)],
                  ["Refundable now", money(r.overlap.refundable_minor, r.currency)],
                  ["Exposure counted (USD)", (r.exposure_usd_minor / 100).toLocaleString("en-US", { minimumFractionDigits: 2 })],
                  ["Needs", r.required_roles.map(roleLabel).join(" and ")],
                  ["Category", r.category.toLowerCase().replace("_", " ")],
                  ["Evidence", r.evidence_ref ?? "—"],
                  ["Open dispute", r.overlap.open_dispute ? "Yes: it cannot be refunded" : "No"],
                  ["Already credited", money(r.overlap.credited_minor, r.currency)],
                  ["Processor reference", r.processor.refund_ref ?? "—"],
                  ["Processor said", r.processor.state ?? "—"],
                  ["Last verified", r.processor.last_verified_at ? new Date(r.processor.last_verified_at).toLocaleString("en-GB") : "not yet"],
                  ["Times sent", r.processor.attempts],
                ]}
              />
              <p className="text-sm text-ink">{r.reason}</p>
              <p className="break-all font-mono text-2xs text-ink-subtle">You sign exactly this record, SHA-256 {r.snapshot_hash}. If it changes, earlier signatures stop counting.</p>
              {r.state === "UNKNOWN" ? (
                <Banner tone="danger" title="Outcome unknown">
                  The processor did not answer. Do not issue another refund: the next pass asks the processor what it holds for this case and only sends again if it holds nothing.
                </Banner>
              ) : null}
              {r.approvals.length ? (
                <ul className="space-y-1 text-sm" aria-label="Signatures">
                  {r.approvals.map((a) => (
                    <li key={`${a.role}-${a.principal_id}`} className="flex items-center gap-2">
                      {a.decision === "APPROVE" ? <CheckCircle2 aria-hidden className="size-4 text-ok-fg" /> : <XCircle aria-hidden className="size-4 text-danger-fg" />}
                      {a.decision === "APPROVE" ? "Approved" : "Rejected"} as {roleLabel(a.role)} · {new Date(a.decided_at).toLocaleString("en-GB")}
                    </li>
                  ))}
                </ul>
              ) : null}
              {actions.includes("sign") ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Select label="Sign as" options={need.map((x) => ({ id: x, label: roleLabel(x) }))} value={chosen} onChange={setRole} description="You must hold this role, and be a different person from the requester and the other signers." />
                  <TextField label="Reason (required to reject)" value={reason} onChange={setReason} />
                  <div className="flex gap-2 sm:col-span-2">
                    <Button isDisabled={decide.isPending} onPress={() => decide.mutate({ r, decision: "APPROVE" })}>
                      Approve
                    </Button>
                    <Button variant="secondary" isDisabled={decide.isPending || !reason.trim()} onPress={() => decide.mutate({ r, decision: "REJECT" })}>
                      Reject
                    </Button>
                  </div>
                </div>
              ) : null}
              {actions.includes("queue") ? (
                <div className="flex flex-wrap items-center gap-3">
                  <Button isDisabled={queue.isPending} onPress={() => queue.mutate(r)}>
                    Queue it for sending
                  </Button>
                  <span className="text-xs text-ink-subtle">Only a finance executor who neither asked nor signed. Everything it rested on is checked again now.</span>
                </div>
              ) : null}
              {actions.includes("cancel") ? (
                <Button variant="secondary" isDisabled={cancel.isPending} onPress={() => cancel.mutate(r)}>
                  Withdraw request
                </Button>
              ) : null}
              <Failure m={decide} title="That was not recorded" />
              <Failure m={queue} title="It was not queued" />
              <Failure m={cancel} title="It was not withdrawn" />
            </div>
          );
        }}
      </QueryView>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------------------------------------------------- F03-B disputes

/** F03-B. A case with an owner and the processor's own deadline. It closes only when the processor says so. */
export function DisputesView() {
  const [open, setOpen] = useState<string | null>(null);
  const [openOnly, setOpenOnly] = useState("open");
  const list = useCellQuery([...PAYMENTS_KEY, "disputes", openOnly], (api) => api.payments.disputes(openOnly === "open"));
  const columns: Column<DisputeCase>[] = [
    { key: "a", header: "Amount", render: (d) => money(d.amount_minor, d.currency), sortValue: (d) => d.amount_minor },
    { key: "r", header: "Reason", render: (d) => d.reason ?? "—" },
    { key: "s", header: "State", render: (d) => <Badge tone={DISPUTE_STATE[d.state]?.tone ?? "neutral"}>{DISPUTE_STATE[d.state]?.label ?? d.state}</Badge>, sortValue: (d) => d.state },
    { key: "d", header: "Deadline", render: (d) => deadlineText(d.deadline), sortValue: (d) => d.deadline.due_by ?? "9999" },
    { key: "o", header: "Owner", render: (d) => (d.owner ? d.owner.slice(0, 8) : "Nobody yet") },
  ];
  return (
    <div className="space-y-4">
      <Banner tone="info" icon={Scale}>
        A dispute is won, lost or closed by the processor and by nobody here. Recording that evidence was handed to the processor&apos;s dashboard is a record of a hand-off, not an outcome.
      </Banner>
      <Card
        title="Disputes"
        padded={false}
        actions={
          <Select
            label="Show"
            hideLabel
            options={[
              { id: "open", label: "Open cases" },
              { id: "all", label: "All cases" },
            ]}
            value={openOnly}
            onChange={setOpenOnly}
            className="w-44"
          />
        }
      >
        <QueryView query={list}>
          {(d) => (
            <DataTable<DisputeCase>
              caption="Disputes"
              columns={columns}
              rows={d.items}
              rowKey={(x) => x.dispute_id}
              onRowAction={(x) => setOpen(x.dispute_id)}
              empty={<EmptyState title="No disputes" icon={Scale}>Cases arrive from the processor&apos;s events.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
      {open ? <DisputeDetail id={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}

function DisputeDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const live = useCellQuery([...PAYMENTS_KEY, "dispute", id], (api) => api.payments.dispute(id));
  const [owner, setOwner] = useState("");
  const [item, setItem] = useState<DisputeEvidenceItem>("terms_or_consent");
  const [ref, setRef] = useState("");
  const [reason, setReason] = useState("");
  const [handoff, setHandoff] = useState("");
  const done = { invalidate: [PAYMENTS_KEY] };
  const assign = useCellMutation((api, who: string) => api.payments.assignDispute(id, who.trim()), { ...done, success: "Owner set" });
  const evidence = useCellMutation((api, v: { item: DisputeEvidenceItem; ref: string }) => api.payments.addEvidence(id, v), { ...done, success: "Evidence recorded", onSuccess: () => setRef("") });
  const decide = useCellMutation((api, strategy: "ACCEPT" | "CONTEST") => api.payments.decideDispute(id, { strategy, reason: reason.trim() }), { ...done, success: "Decision recorded" });
  const record = useCellMutation((api, r: string) => api.payments.recordSubmission(id, r.trim()), { ...done, success: "Hand-off recorded. It is not verified until the processor says so." });
  const verify = useCellMutation((api) => api.payments.verifySubmission(id), { ...done, success: "Asked the processor" });
  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Dispute" size="lg">
      <QueryView query={live}>
        {(d) => {
          const actions = disputeActions(d);
          return (
            <div className="space-y-4">
              <Banner tone={d.deadline.status === "PASSED" || d.deadline.status === "NEAR" ? "danger" : "info"} title="Deadline from the processor">
                {deadlineText(d.deadline)}
              </Banner>
              <KeyValues
                columns={2}
                items={[
                  ["State", <Badge key="s" tone={DISPUTE_STATE[d.state]?.tone ?? "neutral"}>{DISPUTE_STATE[d.state]?.label ?? d.state}</Badge>],
                  ["Amount", money(d.amount_minor, d.currency)],
                  ["Reason (processor)", d.reason ?? "—"],
                  ["Processor status", d.provider_status],
                  ["Strategy", d.strategy ? `${d.strategy.toLowerCase()}: ${d.strategy_reason ?? ""}` : "not decided"],
                  ["Hand-off", d.submission.recorded_at ? `${d.submission.reference} (${d.submission.verified_at ? "verified" : "not verified"})` : "none recorded"],
                  ["Funds withdrawn", money(d.outcome.funds_withdrawn_minor, d.currency)],
                  ["Refunds on this payment", d.overlap.refunds.length ? d.overlap.refunds.map((x) => `${money(x.amount_minor, d.currency)} ${x.state.toLowerCase()}`).join(", ") : "none"],
                  ["Last synced", new Date(d.last_synced_at).toLocaleString("en-GB")],
                  ["Mode", d.livemode ? "live" : "test"],
                ]}
              />
              <section aria-label="Evidence checklist">
                <h3 className="mb-1 text-sm font-semibold text-ink">Evidence checklist</h3>
                <ul className="space-y-1 text-sm">
                  {Object.entries(d.evidence.items).map(([k, v]) => (
                    <li key={k} className="flex items-center gap-2">
                      {v.present ? <CheckCircle2 aria-hidden className="size-4 text-ok-fg" /> : <XCircle aria-hidden className="size-4 text-ink-subtle" />}
                      {EVIDENCE_LABEL[k] ?? k}: {v.present ? v.ref : "missing"}
                    </li>
                  ))}
                </ul>
              </section>
              {actions.includes("assign") ? (
                <div className="flex items-end gap-2">
                  <TextField label="Owner (principal ID)" value={owner} onChange={setOwner} mono className="flex-1" />
                  <Button isDisabled={assign.isPending || !owner.trim()} onPress={() => assign.mutate(owner)}>Assign</Button>
                </div>
              ) : null}
              {actions.includes("evidence") ? (
                <div className="grid gap-2 sm:grid-cols-[14rem_1fr_auto] sm:items-end">
                  <Select label="Evidence item" options={Object.entries(EVIDENCE_LABEL).map(([k, v]) => ({ id: k, label: v }))} value={item} onChange={(v) => setItem(v as DisputeEvidenceItem)} />
                  <TextField label="Where it is kept (reference)" value={ref} onChange={setRef} />
                  <Button isDisabled={evidence.isPending || !ref.trim()} onPress={() => evidence.mutate({ item, ref: ref.trim() })}>Record</Button>
                </div>
              ) : null}
              {actions.includes("decide") ? (
                <div className="space-y-2">
                  <TextField label="Why (required)" value={reason} onChange={setReason} />
                  <div className="flex gap-2">
                    <Button isDisabled={decide.isPending || !reason.trim() || !d.evidence.complete} onPress={() => decide.mutate("CONTEST")}>Contest</Button>
                    <Button variant="secondary" isDisabled={decide.isPending || !reason.trim()} onPress={() => decide.mutate("ACCEPT")}>Accept the dispute</Button>
                  </div>
                  {!d.evidence.complete ? <p className="text-xs text-ink-subtle">Contesting needs all five evidence items.</p> : null}
                </div>
              ) : null}
              {actions.includes("record") ? (
                <div className="flex items-end gap-2">
                  <TextField label="Hand-off reference (dashboard ticket or case)" value={handoff} onChange={setHandoff} className="flex-1" />
                  <Button isDisabled={record.isPending || !handoff.trim()} onPress={() => record.mutate(handoff)}>Record the hand-off</Button>
                </div>
              ) : null}
              {actions.includes("verify") ? (
                <Button variant="secondary" isDisabled={verify.isPending} onPress={() => verify.mutate(undefined as never)}>
                  Ask the processor what it holds
                </Button>
              ) : null}
              <section aria-label="Timeline">
                <h3 className="mb-1 text-sm font-semibold text-ink">Timeline</h3>
                <ol className="space-y-1 text-xs text-ink-muted">
                  {d.timeline.map((e, i) => (
                    <li key={i}>
                      {new Date(e.at).toLocaleString("en-GB")} · {e.kind.toLowerCase().replace("_", " ")}
                    </li>
                  ))}
                </ol>
              </section>
              {[assign, evidence, decide, record, verify].map((m, i) => (
                <Failure key={i} m={m} title="That was not recorded" />
              ))}
            </div>
          );
        }}
      </QueryView>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------------------------------------------- F04 reconciliation

/** F04. Provider receipt, payout, bank settlement and the ledger are separate facts. A period closes with two different signatures on the digest that was read. */
export function ReconciliationView() {
  const [entity, setEntity] = useState("");
  const [period, setPeriod] = useState(() => lastPeriod(new Date()));
  const [go, setGo] = useState<{ entity: string; period: string } | null>(null);
  const ok = entity.trim() !== "" && validPeriod(period);
  return (
    <div className="space-y-4">
      <Banner tone="info" icon={Landmark}>
        This is controlled finance evidence, not the statutory ledger. Provider confirmation is not bank settlement, and none of these totals is revenue.
      </Banner>
      <Card title="Period" description="The issuing entity and the month to reconcile.">
        <form
          className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            if (ok) setGo({ entity: entity.trim(), period });
          }}
          aria-label="Choose a period"
        >
          <TextField label="Issuing entity" value={entity} onChange={setEntity} isRequired />
          <TextField label="Period (YYYY-MM)" value={period} onChange={setPeriod} errorMessage={period && !validPeriod(period) ? "Use YYYY-MM." : undefined} isRequired />
          <Button type="submit" isDisabled={!ok}>Open the board</Button>
        </form>
      </Card>
      {go ? <Board key={`${go.entity}-${go.period}`} entity={go.entity} period={go.period} /> : null}
    </div>
  );
}

function Board({ entity, period }: { entity: string; period: string }) {
  const key = [...PAYMENTS_KEY, "board", entity, period];
  const board = useCellQuery(key, (api) => api.payments.board(entity, period));
  const exceptions = useCellQuery([...key, "exceptions"], (api) => api.payments.exceptions(entity, period));
  const [csv, setCsv] = useState("");
  const [role, setRole] = useState("");
  const [why, setWhy] = useState("");
  const [impact, setImpact] = useState("");
  const [resolution, setResolution] = useState<Record<string, string>>({});
  const parsed = parseLegs(csv);
  const done = { invalidate: [PAYMENTS_KEY] };
  const [statement, setStatement] = useState("");
  const [payouts, setPayouts] = useState("");
  const importStatement = useCellMutation((api) => api.payments.importBankStatement(entity, statement), {
    ...{ invalidate: [PAYMENTS_KEY] },
    success: (x) => `${x.added} bank lines added, ${x.skipped} already there, ${x.unreadable_entries} not read`,
    onSuccess: () => setStatement(""),
  });
  const importPayouts = useCellMutation((api) => api.payments.importStripePayouts(entity, payouts.split(/[\s,]+/).filter(Boolean)), {
    ...{ invalidate: [PAYMENTS_KEY] },
    success: (x) => `${x.added} payout lines added, ${x.skipped} already there, ${x.unreadable_transactions} not read`,
    onSuccess: () => setPayouts(""),
  });
  const importLegs = useCellMutation((api) => api.payments.importLegs(entity, parsed.legs), { ...done, success: (x) => `${x.added} added, ${x.skipped} already there`, onSuccess: () => setCsv("") });
  const prepare = useCellMutation((api) => api.payments.prepareClose(entity, period), { ...done, success: (x) => (x.state === "READY_FOR_CLOSE" ? "Ready for close. Two people now sign." : "It cannot close yet. The blockers are listed.") });
  const sign = useCellMutation((api, v: { role: string; hash: string }) => api.payments.signClose(entity, period, { role: v.role, snapshot_hash: v.hash }), {
    ...done,
    success: (x) => (x.state === "CLOSED" ? "Closed and locked" : "Your signature is recorded"),
  });
  const reopen = useCellMutation((api) => api.payments.requestReopen(entity, period, { reason: why.trim(), impact: impact.trim() }), { ...done, success: "Reopen requested. Two other people must approve it." });
  const approve = useCellMutation((api, r: string) => api.payments.approveReopen(entity, period, r), { ...done, success: (x) => (x.state === "OPEN" ? "Reopened" : "Your approval is recorded") });
  const resolve = useCellMutation((api, v: { id: string; text: string }) => api.payments.resolveException(v.id, v.text.trim()), { ...done, success: "Explained" });
  return (
    <QueryView query={board}>
      {(b: ReconBoard) => {
        const columns: Column<ReconRow>[] = [
          { key: "k", header: "Item", render: (r) => r.kind.toLowerCase() },
          { key: "r", header: "Reference", render: (r) => <span className="font-mono text-xs">{r.ref}</span> },
          { key: "a", header: "Amount", render: (r) => money(r.amount_minor, r.currency), sortValue: (r) => r.amount_minor },
          { key: "d", header: "Date", render: (r) => r.occurred_on },
          { key: "s", header: "State", render: (r) => <Badge tone={r.resolved ? "neutral" : MATCH_LABEL[r.state].tone}>{r.resolved ? "Explained" : MATCH_LABEL[r.state].label}</Badge> },
          { key: "l", header: "Evidence held", render: (r) => (r.legs.length ? r.legs.map((l) => LEG_LABEL[l] ?? l).join(", ") : "none") },
          { key: "w", header: "Why", render: (r) => r.reason ?? "—" },
        ];
        const closed = b.state === "CLOSED";
        return (
          <div className="space-y-4">
            <Card title={`${b.issuing_entity} · ${b.period}`} description={b.settlement_note}>
              <div className="space-y-3">
                <Badge tone={PERIOD_STATE[b.state].tone}>{PERIOD_STATE[b.state].label}</Badge>
                <KeyValues
                  columns={2}
                  items={[
                    ["Items", b.summary.items],
                    ["Matched", b.summary.MATCHED],
                    ["Provisionally matched", b.summary.PROVISIONALLY_MATCHED],
                    ["Unmatched", b.summary.UNMATCHED],
                    ...Object.entries(b.totals_minor).map(([c, t]): [string, string] => [`Payments, refunds, disputes (${c})`, `${money(t.payments, c)} · ${money(t.refunds, c)} · ${money(t.disputes, c)}`]),
                  ]}
                />
                {b.blockers.length ? (
                  <Banner tone="danger" title="What stops this period closing">
                    <ul className="list-disc pl-5">{b.blockers.map((x) => <li key={x}>{x}</li>)}</ul>
                  </Banner>
                ) : (
                  <Banner tone="ok" title="Nothing blocks a close">The signatures below bind this digest: {b.snapshot_hash.slice(0, 16)}…</Banner>
                )}
              </div>
            </Card>
            <Card title="Money events" padded={false}>
              <DataTable<ReconRow> caption="Reconciliation board" columns={columns} rows={b.rows} rowKey={(r) => r.item_id} empty={<EmptyState title="No money events in this period" icon={Landmark}>Payments and confirmed refunds appear here.</EmptyState>} />
            </Card>
            {!closed ? (
              <Card title="Evidence import" description="Paste lines: leg_type, ref, link_ref, amount_minor, currency, occurred_on. Leg types are PAYOUT_LINE, BANK_LINE, GL_JOURNAL. A repeat of a line is skipped.">
                <div className="space-y-2">
                  <TextField label="Lines" value={csv} onChange={setCsv} multiline mono />
                  {parsed.errors.length ? <p role="alert" className="text-sm text-danger-fg">{parsed.errors.slice(0, 3).join(" ")}</p> : null}
                  <Button isDisabled={importLegs.isPending || !parsed.legs.length || parsed.errors.length > 0} onPress={() => importLegs.mutate(undefined as never)}>
                    Import {parsed.legs.length || ""} line{parsed.legs.length === 1 ? "" : "s"}
                  </Button>
                </div>
              </Card>
            ) : null}
            {!closed ? (
              <Card title="Import from the source" description="The processor's own payout detail, and a bank statement in ISO 20022 camt.053. Each becomes its own kind of evidence; one is never filled in from the other.">
                <div className="space-y-4">
                  <div className="space-y-2">
                    <TextField label="Stripe payout IDs (po_…), separated by spaces or commas" value={payouts} onChange={setPayouts} mono />
                    <Button variant="secondary" isDisabled={importPayouts.isPending || !payouts.trim()} onPress={() => importPayouts.mutate(undefined as never)}>
                      Read the payouts from the processor
                    </Button>
                  </div>
                  <div className="space-y-2">
                    <TextField label="Bank statement (camt.053 XML)" value={statement} onChange={setStatement} multiline mono />
                    <Button variant="secondary" isDisabled={importStatement.isPending || statement.trim().length < 20} onPress={() => importStatement.mutate(undefined as never)}>
                      Import the statement
                    </Button>
                  </div>
                </div>
              </Card>
            ) : null}
            <QueryView query={exceptions}>
              {(x) =>
                x.items.length ? (
                  <Card title="Exceptions" description="An exception stays on this list until a person explains it.">
                    <ul className="space-y-3">
                      {x.items.map((e) => (
                        <li key={e.exception_id} className="text-sm">
                          <p>
                            {e.source_kind.toLowerCase()} · {e.reason}
                          </p>
                          {e.resolution ? (
                            <p className="text-ink-muted">Explained: {e.resolution}</p>
                          ) : closed ? null : (
                            <div className="mt-1 flex items-end gap-2">
                              <TextField label="How it was explained" value={resolution[e.exception_id] ?? ""} onChange={(v) => setResolution((c) => ({ ...c, [e.exception_id]: v }))} className="flex-1" />
                              <Button isDisabled={resolve.isPending || !(resolution[e.exception_id] ?? "").trim()} onPress={() => resolve.mutate({ id: e.exception_id, text: resolution[e.exception_id]! })}>Explain</Button>
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </Card>
                ) : null
              }
            </QueryView>
            {b.state === "OPEN" ? (
              <Button isDisabled={prepare.isPending} onPress={() => prepare.mutate(undefined as never)}>
                Check whether it can close
              </Button>
            ) : null}
            {b.state === "READY_FOR_CLOSE" ? (
              <Card title="Sign the close" description="The Controller signs first, then the CFO or Group CFO, a different person. If anything moved since you read it, the signature is refused.">
                <div className="flex flex-wrap items-end gap-2">
                  <Select label="Sign as" options={["CONTROLLER", "CFO", "GROUP_CFO"].map((r) => ({ id: r, label: roleLabel(r) }))} value={role || "CONTROLLER"} onChange={setRole} />
                  <Button isDisabled={sign.isPending} onPress={() => sign.mutate({ role: role || "CONTROLLER", hash: b.snapshot_hash })}>
                    Sign ({b.snapshot_hash.slice(0, 8)})
                  </Button>
                </div>
              </Card>
            ) : null}
            {closed ? (
              <Card title="Closed" description="Locked in the database. Reopening needs a reason, its impact and two approvals from other people.">
                <div className="space-y-2">
                  <TextField label="Reason" value={why} onChange={setWhy} />
                  <TextField label="Impact" value={impact} onChange={setImpact} />
                  <Button variant="secondary" isDisabled={reopen.isPending || !why.trim() || !impact.trim()} onPress={() => reopen.mutate(undefined as never)}>
                    Ask to reopen
                  </Button>
                </div>
              </Card>
            ) : null}
            {b.state === "REOPEN_REQUESTED" ? (
              <Card title="Reopen requested" description="Two people from the reopening roles approve, neither the one who asked.">
                <div className="flex flex-wrap items-end gap-2">
                  <Select label="Approve as" options={["CFO", "FOUNDER"].map((r) => ({ id: r, label: roleLabel(r) }))} value={role || "CFO"} onChange={setRole} />
                  <Button isDisabled={approve.isPending} onPress={() => approve.mutate(role || "CFO")}>Approve the reopen</Button>
                </div>
              </Card>
            ) : null}
            {[importLegs, importStatement, importPayouts, prepare, sign, reopen, approve, resolve].map((m, i) => (
              <Failure key={i} m={m} title="That was not recorded" />
            ))}
          </div>
        );
      }}
    </QueryView>
  );
}
