"use client";

import type { GovRequest } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, KeyValues, PageHeader, Select, type Column } from "@zoiko/ui";
import { Stamp } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { GovernanceNav } from "./shell";
import { GOV_KEY, KIND_LABEL, codeText, govKey, requestStateText, reviewAvailability, signatureProgress, slotLabel } from "./logic";

type Filter = "mine" | "all" | "expiring";
const REASONS = [
  { id: "RISK_TOO_HIGH", label: "Risk is too high" },
  { id: "SCOPE_TOO_WIDE", label: "Scope is too wide" },
  { id: "EVIDENCE_INSUFFICIENT", label: "Evidence is insufficient" },
  { id: "NOT_NEEDED", label: "Not needed" },
  { id: "POLICY_CONFLICT", label: "Conflicts with company policy" },
  { id: "OTHER", label: "Other (explain to the requester directly)" },
];

/** Before and after, as lists a reviewer can read: what is added, removed or changed, and which limits move. */
function Diff({ delta }: { delta: Record<string, unknown> | undefined }) {
  if (!delta) return <p className="text-sm text-ink-muted">No difference is recorded for this request.</p>;
  const list = (k: string) => (Array.isArray(delta[k]) ? (delta[k] as Record<string, unknown>[]) : []);
  const row = (g: Record<string, unknown>) => `${String(g.tool_id)} on ${String(g.resource_kind)} ${String(g.resource_selector)} for ${String(g.purpose)} in ${String(g.region)} until ${String(g.valid_until).slice(0, 10)}`;
  const quotas = (delta.quotas ?? {}) as Record<string, { from: unknown; to: unknown }>;
  const ai = delta.ai_enabled as { from: boolean; to: boolean } | undefined;
  const budget = "after" in delta;
  return (
    <div className="space-y-3 text-sm">
      {budget ? (
        <p>
          Cap {String(delta.scope ?? "")} per {String(delta.period ?? "")}: <b>{String(delta.before ?? "none")}</b> to <b>{String(delta.after)}</b> {String(delta.currency ?? "")}
        </p>
      ) : null}
      {list("added_grants").map((g, i) => <p key={`a${i}`}><Badge size="xs" tone="warn">Added</Badge> {row(g)}</p>)}
      {list("changed_grants").map((g, i) => <p key={`c${i}`}><Badge size="xs" tone="warn">Changed</Badge> {row(g)}</p>)}
      {list("removed_grants").map((g, i) => <p key={`r${i}`}><Badge size="xs" tone="ok">Removed</Badge> {row(g)}</p>)}
      {Object.entries(quotas).map(([k, v]) => (
        <p key={k}>{k.replaceAll("_", " ")}: <b>{String(v.from ?? "not set")}</b> to <b>{String(v.to)}</b></p>
      ))}
      {ai && ai.to !== ai.from ? <p>AI explanations: <b>{ai.from ? "on" : "off"}</b> to <b>{ai.to ? "on" : "off"}</b></p> : null}
    </div>
  );
}

function RequestPanel({ req, onClose }: { req: GovRequest; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const avail = reviewAvailability(req);
  const state = requestStateText(req);
  const done = { invalidate: [GOV_KEY] };
  const decide = useCellMutation<"approve" | "reject" | "clarify", { state: string }>(
    (api, d) => api.agentGovernance.decide(req.request_id, d, { policy_hash: req.policy_hash, reason: d === "approve" ? undefined : reason }),
    { ...done, onSuccess: (r) => setResult(r.state) },
  );
  const apply = useCellMutation<void, { version: number }>((api) => api.agentGovernance.apply(req.request_id), { ...done, success: (r) => `Version ${r.version} is now live` });

  return (
    <Card
      title={`${req.ref ?? req.request_id.slice(0, 8)}: ${KIND_LABEL[req.kind]}`}
      description="Review the exact change. A signature applies to this version only."
      actions={<Button size="sm" variant="secondary" onPress={onClose}>Close</Button>}
    >
      <div className="space-y-4">
        <KeyValues
          columns={3}
          items={[
            ["Status", <Badge key="s" tone={state.tone}>{state.label}</Badge>],
            ["Signatures", `${signatureProgress(req)} (${req.required_slots.map(slotLabel).join(", ")})`],
            ["Still needed", req.open_slots.length ? req.open_slots.map(slotLabel).join(", ") : "None"],
            ["Requested by", <span key="r" className="font-mono text-xs">{req.requested_by.slice(0, 8)}…</span>],
            ["Expires (UTC)", req.expires_at.replace("T", " ").slice(0, 19)],
            ["Policy hash", <span key="h" className="break-all font-mono text-2xs">{req.policy_hash}</span>],
          ]}
        />
        <div>
          <h3 className="mb-2 text-sm font-semibold text-ink">What changes</h3>
          <Diff delta={req.delta} />
        </div>
        {req.evidence_ref ? <p className="text-xs text-ink-subtle">Compile and test evidence: <span className="font-mono">{req.evidence_ref.slice(0, 8)}…</span></p> : <Banner tone="danger" title="Evidence is missing">This request cannot be signed without its evidence.</Banner>}
        {!avail.canReview && avail.why ? <Banner tone="info" title="Review unavailable">{avail.why}</Banner> : null}
        {req.state === "APPROVED" && req.subject === "POLICY" ? (
          <Banner tone="info" title="Approved, but not live">
            Approval does not enable anything by itself. The version goes live when it is applied, and only while the agent is active and the approval is still fresh.
          </Banner>
        ) : null}
        {req.state === "PENDING" ? (
          <div className="space-y-3">
            <Select label="Reason (needed to reject or ask a question)" placeholder="Choose a reason…" options={REASONS} value={reason || null} onChange={setReason} />
            <div className="flex flex-wrap gap-2">
              <Button isDisabled={!avail.canReview} pending={decide.isPending && decide.variables === "approve"} onPress={() => decide.mutate("approve")}>
                Approve this exact version
              </Button>
              <Button variant="secondary" isDisabled={!avail.canReview || !reason} onPress={() => decide.mutate("clarify")}>Ask a question</Button>
              <Button variant="danger" isDisabled={!avail.canReview || !reason} onPress={() => decide.mutate("reject")}>Reject</Button>
            </div>
            <p className="text-xs text-ink-subtle">Approving asks for a fresh passkey check. The server checks you again at the moment you sign.</p>
          </div>
        ) : null}
        {req.state === "APPROVED" && req.subject === "POLICY" ? (
          <Button pending={apply.isPending} onPress={() => apply.mutate()}>Make this version live</Button>
        ) : null}
        {result ? <Banner tone="ok" title={`Recorded: ${result.toLowerCase()}`} live="polite">The server confirmed it. Approved is not live until applied.</Banner> : null}
        {decide.error ? <ApiErrorView error={decide.error} /> : null}
        {apply.error ? <ApiErrorView error={apply.error} /> : null}
        {decide.error ? <p className="text-xs text-ink-muted">{codeText((decide.error as { code?: string }).code)}</p> : null}
      </div>
    </Card>
  );
}

export function ApprovalCenter() {
  const [filter, setFilter] = useState<Filter>("mine");
  const [selected, setSelected] = useState<string | null>(null);
  const requests = useCellQuery(govKey("requests"), (a) => a.agentGovernance.requests(), { refetchInterval: 30000 });

  const rows = (requests.data?.items ?? []).filter((r) => {
    if (filter === "mine") return r.state === "PENDING" && r.can_review;
    if (filter === "expiring") return r.state === "PENDING" && Date.parse(r.expires_at) - requests.dataUpdatedAt < 4 * 3600 * 1000;
    return true;
  });
  const current = (requests.data?.items ?? []).find((r) => r.request_id === selected) ?? null;

  const columns: Column<GovRequest>[] = [
    { key: "ref", header: "Request", pinned: true, render: (r) => <span className="font-mono text-xs">{r.ref ?? r.request_id.slice(0, 8)}</span> },
    { key: "kind", header: "Change", render: (r) => KIND_LABEL[r.kind] },
    { key: "agent", header: "Agent", render: (r) => <span className="font-mono text-xs">{r.agent_id.slice(0, 8)}…</span> },
    { key: "state", header: "Status", render: (r) => { const s = requestStateText(r); return <Badge size="xs" tone={s.tone}>{s.label}</Badge>; } },
    { key: "sigs", header: "Signatures", render: (r) => signatureProgress(r) },
    { key: "mine", header: "You", render: (r) => (r.review_unavailable ? "Your request" : r.can_review ? "You can review" : "—") },
    { key: "exp", header: "Expires (UTC)", render: (r) => <span className="tabular text-xs">{r.expires_at.replace("T", " ").slice(0, 16)}</span> },
  ];

  return (
    <>
      <PageHeader
        icon={Stamp}
        title="Agent change approvals"
        description="Nothing an agent is allowed to do changes until independent people sign the exact version. Your own requests are visible here but you cannot approve them."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Governance", href: "/admin/agents/governance" }, { label: "Approvals" }]}
      />
      <GovernanceNav current="approvals" />
      <div className="space-y-4">
        <Card
          padded={false}
          title="Queue"
          actions={
            <Select
              label="Show"
              options={[
                { id: "mine", label: "Needs my review" },
                { id: "all", label: "All requests" },
                { id: "expiring", label: "Expiring within 4 hours" },
              ]}
              value={filter}
              onChange={(v) => setFilter(v as Filter)}
            />
          }
        >
          <QueryView query={requests}>
            {() => (
              <DataTable
                caption="Agent change requests"
                columns={columns}
                rows={rows}
                rowKey={(r) => r.request_id}
                onRowAction={(r) => setSelected(r.request_id)}
                selectedKey={selected}
                empty={<EmptyState icon={Stamp} title={filter === "mine" ? "Nothing waiting for you" : "No requests"}>Requests appear here when someone drafts a change to an agent.</EmptyState>}
              />
            )}
          </QueryView>
        </Card>
        {current ? <RequestPanel key={current.request_id + current.state + current.signatures.length} req={current} onClose={() => setSelected(null)} /> : null}
      </div>
    </>
  );
}
