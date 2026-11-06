"use client";

import type { CommitmentDelegate, CommitmentDelegateKind, CommitmentRequest } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, KeyValues, Select, TextField, type Column } from "@zoiko/ui";
import { Handshake } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import {
  DELEGATE_KIND_LABEL,
  STATE_COPY,
  TIER_COPY,
  authorityLabel,
  canDecide,
  commitmentStepUp,
  delegateGrantStepUp,
  delegateRevokeStepUp,
  requestableOrders,
  signaturesLeft,
  validCap,
  waitingReason,
} from "./commitment-logic";
import { PersonLabel, usePeople } from "../team/person-label";
import { BILLING_KEY, money } from "./shared";

const KEY = [...BILLING_KEY, "commitments"] as const;

/** B05. A new or larger commitment is approved here before it can start. The server works out the amount from the signed order and who
 * may sign; the person sees the exact figures and hash they are approving. */
export function CommitmentsSection({ canRequest, canDelegate, canSeeDelegates }: { canRequest: boolean; canDelegate: boolean; canSeeDelegates: boolean }) {
  const requests = useCellQuery(KEY, (api) => api.billing.commitments());
  const me = useRuntime().session;
  const principal = me?.authenticated ? me.subject.sub : undefined;
  return (
    <div className="space-y-4">
      <QueryView query={requests}>
        {(data) => (
          <>
            {canRequest ? <NewRequest requests={data.items} /> : null}
            <RequestList items={data.items} principal={principal} />
          </>
        )}
      </QueryView>
      {canSeeDelegates ? <Delegates canGive={canDelegate} /> : null}
    </div>
  );
}

function NewRequest({ requests }: { requests: CommitmentRequest[] }) {
  const orders = useCellQuery([...BILLING_KEY, "orders"], (api) => api.billing.orders());
  const [orderId, setOrderId] = useState("");
  const ask = useCellMutation((api, id: string) => api.billing.requestCommitment(id), {
    invalidate: [KEY],
    success: "Approval requested. The people who can sign are asked.",
    onSuccess: () => setOrderId(""),
  });
  const options = requestableOrders(orders.data?.items ?? [], requests);
  if (!options.length) return null;
  return (
    <Card title="Ask for approval" description="Pick a signed order that has not started. The amount and the approvers come from the order, not from you.">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (orderId) ask.mutate(orderId);
        }}
      >
        <Select
          label="Order"
          options={[{ id: "", label: "Choose an order" }, ...options.map((o) => ({ id: o.order_id, label: `${o.plan} · ${o.term_months} months · ${o.order_id.slice(0, 8)}` }))]}
          value={orderId}
          onChange={setOrderId}
          className="min-w-72"
        />
        <Button type="submit" isDisabled={!orderId || ask.isPending}>
          Ask for approval
        </Button>
      </form>
      {ask.isError ? <ApiErrorView error={ask.error} title="The approval was not requested" onRetry={() => ask.reset()} /> : null}
    </Card>
  );
}

function RequestList({ items, principal }: { items: CommitmentRequest[]; principal: string | undefined }) {
  const [open, setOpen] = useState<CommitmentRequest | null>(null);
  const columns: Column<CommitmentRequest>[] = [
    { key: "order", header: "Order", pinned: true, render: (r) => <span className="font-mono text-xs">{r.order_id.slice(0, 8)}</span>, sortValue: (r) => r.order_id },
    { key: "exposure", header: "Commitment", render: (r) => money(r.exposure, r.currency), sortValue: (r) => r.exposure_usd_minor },
    { key: "term", header: "Term", render: (r) => `${r.term_months} months`, sortValue: (r) => r.term_months },
    { key: "tier", header: "Tier", render: (r) => TIER_COPY[r.tier].label, sortValue: (r) => r.tier },
    { key: "state", header: "State", render: (r) => <Badge tone={STATE_COPY[r.state].tone}>{STATE_COPY[r.state].label}</Badge>, sortValue: (r) => r.state },
    { key: "when", header: "Asked", render: (r) => new Date(r.created_at).toLocaleDateString("en-GB"), sortValue: (r) => r.created_at },
  ];
  return (
    <>
      <Card title="Commitment approvals" description="Nothing starts until the people it names have signed the exact figures." padded={false}>
        <DataTable<CommitmentRequest>
          caption="Commitment approval requests"
          columns={columns}
          rows={items}
          rowKey={(r) => r.request_id}
          onRowAction={(r) => setOpen(r)}
          empty={<EmptyState title="No commitment needs approval" icon={Handshake}>A new or larger commitment appears here when it is put forward.</EmptyState>}
        />
      </Card>
      {open ? <Detail id={open.request_id} fallback={open} principal={principal} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

/** Re-reads the request when it opens, so the hash on screen is the server's current one, then lets the person sign or refuse it. */
function Detail({ id, fallback, principal, onClose }: { id: string; fallback: CommitmentRequest; principal: string | undefined; onClose: () => void }) {
  const person = usePeople();
  const live = useCellQuery([...KEY, id], (api) => api.billing.commitment(id));
  const r = live.data ?? fallback;
  const { proveAction } = useRuntime();
  const tenant = useCellQuery(["tenant", "me"], (api) => api.tenant.me());
  const [reason, setReason] = useState("");
  const [now] = useState(() => Date.now());

  const decide = useCellMutation(
    async (api, decision: "approve" | "reject") => {
      if (!tenant.data) throw new Error("The organization could not be read. Try again.");
      const proof = await proveAction(commitmentStepUp(tenant.data.tenant_id, r, decision));
      return api.billing.decideCommitment(r.request_id, decision, r.snapshot_hash, reason, proof);
    },
    { invalidate: [KEY], success: (x) => (x.state === "PENDING_APPROVAL" ? "Your signature is recorded. Another person has to sign." : x.state === "APPROVED" ? "Approved" : "Rejected") },
  );
  const cancel = useCellMutation((api) => api.billing.cancelCommitment(r.request_id), { invalidate: [KEY], success: "Request withdrawn", onSuccess: onClose });

  const waiting = waitingReason(r, principal, now);
  const decidable = canDecide(r, principal, now) && !decide.isPending;
  const left = signaturesLeft(r);

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title="Commitment approval"
      description={TIER_COPY[r.tier].help}
      size="lg"
      footer={
        r.state === "PENDING_APPROVAL" ? (
          <>
            <Button variant="secondary" isDisabled={cancel.isPending} onPress={() => cancel.mutate(undefined)}>
              Withdraw request
            </Button>
            <Button variant="secondary" isDisabled={!decidable || reason.trim().length === 0} onPress={() => decide.mutate("reject")}>
              Reject
            </Button>
            <Button isDisabled={!decidable} onPress={() => decide.mutate("approve")}>
              Approve
            </Button>
          </>
        ) : undefined
      }
    >
      <div className="space-y-4">
        <KeyValues
          columns={2}
          items={[
            ["State", <Badge key="s" tone={STATE_COPY[r.state].tone}>{STATE_COPY[r.state].label}</Badge>],
            ["Tier", TIER_COPY[r.tier].label],
            ["Monthly", money(r.monthly, r.currency)],
            ["Term", `${r.term_months} months`],
            ["Commitment", money(r.exposure, r.currency)],
            ["Expires", new Date(r.expires_at).toLocaleDateString("en-GB")],
            ["Signs", r.needs.length ? r.needs.map(authorityLabel).join(" and ") : "One authorised approver"],
            ["Signatures still needed", String(left)],
          ]}
        />
        <p className="break-all font-mono text-2xs text-ink-subtle">
          What you approve is exactly this record, SHA-256 {r.snapshot_hash}. If it changes, you are asked again.
        </p>
        {r.review_required ? (
          <Banner tone={r.reviewed_at ? "ok" : "warn"} title="Non-standard terms">
            {r.reviewed_at ? `A Zoiko Controller or CFO reviewed these terms on ${new Date(r.reviewed_at).toLocaleDateString("en-GB")}.` : "A Zoiko Controller or CFO has to review these terms before anyone signs."}
          </Banner>
        ) : null}
        {r.approvals.length ? (
          <div>
            <h3 className="mb-1 text-sm font-medium text-ink">Signed so far</h3>
            <ul className="space-y-1 text-sm">
              {r.approvals.map((a) => (
                <li key={`${a.principal_id}-${a.decided_at}`}>
                  {a.decision === "APPROVE" ? "Approved" : "Rejected"} as {authorityLabel(a.authority)} · <PersonLabel person={person(a.principal_id)} fallbackId={a.principal_id} /> ·{" "}
                  {new Date(a.decided_at).toLocaleString("en-GB")}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {waiting ? <Banner tone="info">{waiting}</Banner> : null}
        {r.state === "PENDING_APPROVAL" ? (
          <TextField label="Reason (required to reject)" value={reason} onChange={setReason} multiline rows={2} />
        ) : null}
        {decide.isError ? <ApiErrorView error={decide.error} title="Your decision was not recorded" onRetry={() => decide.reset()} /> : null}
        {cancel.isError ? <ApiErrorView error={cancel.error} title="The request was not withdrawn" onRetry={() => cancel.reset()} /> : null}
      </div>
    </Dialog>
  );
}

function Delegates({ canGive }: { canGive: boolean }) {
  const delegates = useCellQuery([...KEY, "delegates"], (api) => api.billing.delegates());
  const members = useCellQuery(["tenant", "members"], (api) => api.tenant.members(), { enabled: canGive });
  const tenant = useCellQuery(["tenant", "me"], (api) => api.tenant.me(), { enabled: canGive });
  const { proveAction } = useRuntime();
  const [who, setWho] = useState("");
  const [kind, setKind] = useState<CommitmentDelegateKind>("BILLING_SPEND");
  const [cap, setCap] = useState("");
  const [until, setUntil] = useState("");

  const give = useCellMutation(
    async (api, v: { principal_id: string; kind: CommitmentDelegateKind; cap_usd: string; until: string }) => {
      if (!tenant.data) throw new Error("The organization could not be read. Try again.");
      const proof = await proveAction(delegateGrantStepUp(tenant.data.tenant_id, v));
      return api.billing.giveDelegate(
        { principal_id: v.principal_id, kind: v.kind, cap_usd: v.cap_usd, effective_to: v.until ? new Date(`${v.until}T23:59:59`).toISOString() : null },
        proof,
      );
    },
    { invalidate: [KEY], success: "Limit given.", onSuccess: () => setCap("") },
  );
  const revoke = useCellMutation(
    async (api, id: string) => {
      if (!tenant.data) throw new Error("The organization could not be read. Try again.");
      const proof = await proveAction(delegateRevokeStepUp(tenant.data.tenant_id, id));
      return api.billing.revokeDelegate(id, proof);
    },
    { invalidate: [KEY], success: "Limit taken away." },
  );

  const person = usePeople();
  const people = (members.data?.items ?? [])
    .filter((m) => m.status === "ACTIVE")
    .map((m) => ({ id: m.principal_id, label: `${m.person?.label ?? m.principal_id.slice(0, 8)} · ${m.role.charAt(0)}${m.role.slice(1).toLowerCase()}` }));
  const valid = who !== "" && validCap(cap);

  const columns: Column<CommitmentDelegate>[] = [
    { key: "who", header: "Person", pinned: true, render: (d) => <PersonLabel person={person(d.principal_id)} fallbackId={d.principal_id} />, sortValue: (d) => d.principal_id },
    { key: "kind", header: "Authority", render: (d) => DELEGATE_KIND_LABEL[d.kind], sortValue: (d) => d.kind },
    { key: "cap", header: "Limit (USD)", render: (d) => money(d.cap_usd, "USD"), sortValue: (d) => Number(d.cap_usd) },
    { key: "until", header: "Until", render: (d) => (d.effective_to ? d.effective_to.slice(0, 10) : "No end date"), sortValue: (d) => d.effective_to ?? "" },
    { key: "state", header: "State", render: (d) => (d.revoked_at ? "Taken away" : "Active"), sortValue: (d) => (d.revoked_at ? 1 : 0) },
    ...(canGive
      ? [
          {
            key: "act",
            header: "",
            render: (d: CommitmentDelegate) =>
              d.revoked_at ? null : (
                <Button variant="secondary" isDisabled={revoke.isPending} onPress={() => revoke.mutate(d.delegate_id)}>
                  Take away
                </Button>
              ),
          } satisfies Column<CommitmentDelegate>,
        ]
      : []),
  ];

  return (
    <>
      {canGive ? (
        <Card title="Who may approve commitments" description="The Owner always may. A Billing Admin may sign up to a limit you set, and a Procurement Approver signs within a limit too. Each grant needs your passkey.">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              if (valid) give.mutate({ principal_id: who, kind, cap_usd: cap.trim(), until });
            }}
          >
            <Select label="Person" options={[{ id: "", label: "Choose a member" }, ...people]} value={who} onChange={setWho} className="min-w-64" />
            <Select
              label="Authority"
              options={(Object.keys(DELEGATE_KIND_LABEL) as CommitmentDelegateKind[]).map((id) => ({ id, label: DELEGATE_KIND_LABEL[id] }))}
              value={kind}
              onChange={(v) => setKind(v as CommitmentDelegateKind)}
              className="min-w-56"
            />
            <TextField label="Limit (USD)" value={cap} onChange={setCap} inputMode="decimal" className="w-40" />
            <TextField label="Until (optional)" type="date" value={until} onChange={setUntil} className="w-48" />
            <Button type="submit" isDisabled={!valid || give.isPending}>
              Give limit
            </Button>
          </form>
          {give.isError ? <ApiErrorView error={give.error} title="The limit was not given" onRetry={() => give.reset()} /> : null}
        </Card>
      ) : null}
      <QueryView query={delegates}>
        {(data) => (
          <Card title="Commitment limits given" padded={false}>
            <DataTable<CommitmentDelegate>
              caption="Commitment approval limits"
              columns={columns}
              rows={data.items}
              rowKey={(d) => d.delegate_id}
              empty={<EmptyState title="No limits given" icon={Handshake}>Only Owners can sign commitments until an Owner gives a limit.</EmptyState>}
            />
            {revoke.isError ? <ApiErrorView error={revoke.error} title="The limit was not taken away" onRetry={() => revoke.reset()} /> : null}
          </Card>
        )}
      </QueryView>
    </>
  );
}
