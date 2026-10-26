"use client";

import type { BillingOrder, CatalogPlan, PlanRequest } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { FileSignature, Layers, Send } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { BILLING_KEY, count } from "./shared";

/** The signed orders behind this company's plan: what was agreed, from when, for how long. */
export function OrdersCard() {
  const orders = useCellQuery([...BILLING_KEY, "orders"], (api) => api.billing.orders());
  const columns: Column<BillingOrder>[] = [
    { key: "plan", header: "Plan", pinned: true, render: (o) => <span className="font-medium">{o.plan}</span>, sortValue: (o) => o.plan },
    { key: "status", header: "Status", render: (o) => <StatusBadge state={o.status} kind="Order" />, sortValue: (o) => o.status },
    { key: "from", header: "Effective from", render: (o) => new Date(o.effective_from).toLocaleDateString("en-GB"), sortValue: (o) => o.effective_from },
    { key: "term", header: "Term", render: (o) => `${o.term_months} months` },
    {
      key: "pilot",
      header: "Pilot",
      render: (o) => (o.pilot ? <Badge tone="info">Pilot{o.pilot_ends_at ? ` until ${new Date(o.pilot_ends_at).toLocaleDateString("en-GB")}` : ""}</Badge> : "—"),
    },
    { key: "lines", header: "Lines", render: (o) => count(o.lines.length), sortValue: (o) => o.lines.length },
  ];
  return (
    <Card
      title="Signed orders"
      description="The agreements behind your plan. An amendment is a new order that refers to the one it changes; nothing is edited in place."
      padded={false}
    >
      <QueryView query={orders} loadingRows={2}>
        {(d) => (
          <DataTable<BillingOrder>
            caption="Signed orders"
            columns={columns}
            rows={d.items}
            rowKey={(o) => o.order_id}
            empty={
              <EmptyState title="No signed order" icon={FileSignature} compact>
                You are on the default plan. A signed order appears here when you agree a different plan with us.
              </EmptyState>
            }
          />
        )}
      </QueryView>
    </Card>
  );
}

const REQUESTS_KEY = [...BILLING_KEY, "plan-requests"];

/** Where the company's ask for a different plan stands, in words. Asking changes nothing by itself. */
function PlanRequestStatus({ request }: { request: PlanRequest }) {
  const withdraw = useCellMutation((api, id: string) => api.billing.withdrawPlanRequest(id), {
    invalidate: [REQUESTS_KEY],
    success: "Request withdrawn",
  });
  if (request.status === "OPEN") {
    return (
      <Banner
        tone="info"
        title="Your plan request is with our team"
        action={
          <Button size="sm" variant="secondary" pending={withdraw.isPending} onPress={() => withdraw.mutate(request.request_id)}>
            Withdraw
          </Button>
        }
      >
        You asked for the {request.plan.toLowerCase()} plan on <DateTime value={request.created_at} />. Our team will get back to you with an order to sign. Your plan does not change until you sign it.
        {withdraw.error ? <ApiErrorView error={withdraw.error} /> : null}
      </Banner>
    );
  }
  if (request.status === "ACCEPTED") {
    return (
      <Banner tone="ok" title={`Your request for ${request.plan.toLowerCase()} was accepted`}>
        We will send you an order to sign. {request.decision_note ? `Note from our team: ${request.decision_note}` : ""}
      </Banner>
    );
  }
  if (request.status === "DECLINED") {
    return (
      <Banner tone="warn" title={`Your request for ${request.plan.toLowerCase()} was not accepted`}>
        {request.decision_note ?? "Our team did not give a reason."} You can ask again.
      </Banner>
    );
  }
  return null;
}

function AskDialog({ plan, onClose }: { plan: CatalogPlan; onClose: () => void }) {
  const [note, setNote] = useState("");
  const ask = useCellMutation((api) => api.billing.requestPlan(plan.plan, note), {
    invalidate: [REQUESTS_KEY],
    success: "Request sent to our team",
    onSuccess: onClose,
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !ask.isPending && onClose()}
      title={`Ask for the ${plan.label} plan`}
      description="This tells our team what you want. Nothing is charged and your plan does not change until you sign an order."
      footer={
        <>
          <Button variant="secondary" isDisabled={ask.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={ask.isPending} icon={<Send aria-hidden className="size-4" />} onPress={() => ask.mutate(undefined)}>
            Send request
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField
          label="What do you need it for? (optional)"
          value={note}
          onChange={setNote}
          multiline
          rows={4}
          description="For example: two more people with single sign-on, or a higher volume of access decisions."
        />
        {ask.error ? <ApiErrorView error={ask.error} title="The request was not sent" /> : null}
      </div>
    </Dialog>
  );
}

/** The plans on offer and what each includes, from the same catalogue the public pricing page uses. A company can ask
 * for any plan other than its own; the commercial team answers with an order. */
export function PlansCard({ current }: { current: string }) {
  const catalog = useCellQuery([...BILLING_KEY, "catalog"], (api) => api.billing.catalog());
  const requests = useCellQuery(REQUESTS_KEY, (api) => api.billing.planRequests());
  const [asking, setAsking] = useState<CatalogPlan | null>(null);
  const latest = requests.data?.items[0];
  const open = requests.data?.items.find((r) => r.status === "OPEN");
  const columns: Column<CatalogPlan>[] = [
    {
      key: "plan",
      header: "Plan",
      pinned: true,
      render: (p) => (
        <span className="inline-flex items-center gap-2 font-medium">
          {p.label}
          {p.plan === current ? <Badge tone="ok">Your plan</Badge> : null}
          {open?.plan === p.plan ? <Badge tone="info">Requested</Badge> : null}
        </span>
      ),
    },
    { key: "mah", header: "People", render: (p) => count(p.limits.mah_included), sortValue: (p) => p.limits.mah_included ?? -1 },
    { key: "auth", header: "Sign-ins", render: (p) => count(p.limits.auth_events_included), sortValue: (p) => p.limits.auth_events_included ?? -1 },
    { key: "dec", header: "Decisions", render: (p) => count(p.limits.policy_decisions_included), sortValue: (p) => p.limits.policy_decisions_included ?? -1 },
    { key: "sso", header: "SSO connections", render: (p) => count(p.limits.sso_included), sortValue: (p) => p.limits.sso_included ?? -1 },
    { key: "support", header: "Support", render: (p) => p.limits.support_level },
    {
      key: "ask",
      header: "Change",
      align: "end",
      render: (p) =>
        p.plan === current ? null : (
          <Button size="xs" variant="secondary" isDisabled={Boolean(open)} onPress={() => setAsking(p)}>
            {open ? "Request open" : "Ask for this plan"}
          </Button>
        ),
    },
  ];
  return (
    <Card title="Plans" description="What each plan includes. Figures marked — are agreed per order." padded={false}>
      {latest && latest.status !== "WITHDRAWN" ? (
        <div className="border-b border-border p-4">
          <PlanRequestStatus request={latest} />
        </div>
      ) : null}
      <QueryView query={catalog} loadingRows={2}>
        {(c) => (
          <>
            <DataTable<CatalogPlan>
              caption="Available plans"
              columns={columns}
              rows={c.plans}
              rowKey={(p) => p.plan}
              empty={<EmptyState title="No plans published" icon={Layers} compact>Plans appear once a price book is approved.</EmptyState>}
            />
            <p className="border-t border-border px-4 py-3 text-xs text-ink-muted">{c.note}</p>
          </>
        )}
      </QueryView>
      {asking ? <AskDialog plan={asking} onClose={() => setAsking(null)} /> : null}
    </Card>
  );
}
