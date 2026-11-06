"use client";

import type { BillingOrder, Catalog, CatalogPlan, PlanRequest } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Check, FileSignature, Layers, Send } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { losses, monthlyFee, reviewRows } from "./plan-review";
import { BILLING_KEY, count, money } from "./shared";

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


/** One plan as a card: the name, the monthly fee, what it includes and a single action. Every figure is the published catalogue's; a figure it does
 * not state reads "Agreed per order", and the fee reads "Agreed in the order" when no price book is approved. */
function PlanCard({ plan, isCurrent, price, onAsk, canAsk, hasOpen }: { plan: CatalogPlan; isCurrent: boolean; price: { amount: string | null; currency: string | undefined }; onAsk: () => void; canAsk: boolean; hasOpen: boolean }) {
  const free = price.amount !== null && Number(price.amount) === 0;
  const l = plan.limits;
  const included: [string, string][] = [
    ["People", count(l.mah_included)],
    ["Sign-ins", count(l.auth_events_included)],
    ["Access decisions", count(l.policy_decisions_included)],
    ["Machine grants", count(l.m2m_grants_included)],
    ["SSO connections", count(l.sso_included)],
    ["Production environments", count(l.production_environments)],
  ];
  return (
    <article
      aria-label={`${plan.label} plan`}
      className={`flex flex-col rounded-lg border bg-surface p-5 shadow-sm ${isCurrent ? "border-brand-600 ring-2 ring-brand-600/20" : "border-border"}`}
    >
      <header className="flex items-start justify-between gap-2">
        <h3 className="text-lg font-semibold text-ink">{plan.label}</h3>
        {isCurrent ? <Badge tone="ok">Your plan</Badge> : null}
      </header>
      <p className="mt-3 flex items-baseline gap-1">
        {price.amount === null || !price.currency ? (
          <span className="text-base font-medium text-ink-muted">Agreed in the order</span>
        ) : free ? (
          <span className="text-3xl font-semibold text-ink">Free</span>
        ) : (
          <>
            {plan.platform_fee_is_floor ? <span className="text-sm text-ink-muted">From</span> : null}
            <span className="text-3xl font-semibold tabular-nums text-ink">{money(price.amount, price.currency)}</span>
            <span className="text-sm text-ink-muted">/ month</span>
          </>
        )}
      </p>
      <p className="mt-1 text-xs text-ink-subtle">Platform fee. Usage above what is included is charged at the rates below.</p>
      <ul className="mt-4 flex-1 space-y-2 border-t border-border pt-4 text-sm">
        {included.map(([label, value]) => (
          <li key={label} className="flex items-start gap-2">
            <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-ok-fg" />
            <span>
              <span className="font-medium text-ink">{value}</span> <span className="text-ink-muted">{label.toLowerCase()}</span>
            </span>
          </li>
        ))}
        <li className="flex items-start gap-2">
          <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-ok-fg" />
          <span className="text-ink-muted">
            Support: <span className="font-medium text-ink">{l.support_level}</span>
          </span>
        </li>
      </ul>
      <div className="mt-5">
        {isCurrent ? (
          <Button className="w-full" variant="secondary" isDisabled>
            Current plan
          </Button>
        ) : canAsk ? (
          <Button className="w-full" variant="primary" isDisabled={hasOpen} onPress={onAsk}>
            {hasOpen ? "A request is open" : "Review and ask for this plan"}
          </Button>
        ) : (
          <p className="text-center text-xs text-ink-muted">Only an owner or billing admin can ask for a plan.</p>
        )}
      </div>
    </article>
  );
}

function AskDialog({ plan, current, catalog, onClose }: { plan: CatalogPlan; current: CatalogPlan | undefined; catalog: Catalog | undefined; onClose: () => void }) {
  const [note, setNote] = useState("");
  const [understood, setUnderstood] = useState(false);
  const rows = reviewRows(current, plan);
  const lost = losses(current, plan);
  const fee = monthlyFee(catalog, plan.plan);
  const currentFee = current ? monthlyFee(catalog, current.plan) : null;
  const currency = catalog?.pricing?.currency;
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
          <Button pending={ask.isPending} isDisabled={!understood} icon={<Send aria-hidden className="size-4" />} onPress={() => ask.mutate(undefined)}>
            Send request
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <section aria-label="Review of the change">
          <h3 className="mb-1 text-sm font-medium text-ink">
            {current ? `${current.label} to ${plan.label}` : plan.label}
          </h3>
          <table className="w-full text-sm">
            <caption className="sr-only">What changes if this plan is agreed</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
                <th scope="col" className="py-2">What</th>
                <th scope="col" className="py-2">Now</th>
                <th scope="col" className="py-2">{plan.label}</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-border">
                <th scope="row" className="py-2 text-left font-normal">Platform fee</th>
                <td className="py-2">{currentFee !== null && currency ? (Number(currentFee) === 0 ? "Free" : `${money(currentFee, currency)} / month`) : "—"}</td>
                <td className="py-2">{fee !== null && currency ? (Number(fee) === 0 ? "Free" : `${plan.platform_fee_is_floor ? "From " : ""}${money(fee, currency)} / month`) : "Agreed in the order"}</td>
              </tr>
              {rows.map((r) => (
                <tr key={r.label} className="border-b border-border last:border-0">
                  <th scope="row" className="py-2 text-left font-normal">{r.label}</th>
                  <td className="py-2">{r.from}</td>
                  <td className="py-2">
                    {r.to}
                    {r.change === "MORE" ? <span className="sr-only"> (more)</span> : r.change === "LESS" ? <span className="sr-only"> (less)</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-ink-muted">
            This is the published plan, not a quote. The price, term, tax and start date are fixed only in the signed order, and a figure shown as agreed per order is not yet known.
          </p>
        </section>
        {lost.length ? (
          <Banner tone="warn" title="You would lose some of what you have now">
            <ul className="list-disc pl-5">
              {lost.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </Banner>
        ) : null}
        <TextField
          label="What do you need it for? (optional)"
          value={note}
          onChange={setNote}
          multiline
          rows={4}
          description="For example: two more people with single sign-on, or a higher volume of access decisions."
        />
        <Checkbox isSelected={understood} onChange={setUnderstood}>
          I understand this only asks our team for the plan. Nothing is charged and nothing changes until I sign an order.
        </Checkbox>
        {ask.error ? <ApiErrorView error={ask.error} title="The request was not sent" /> : null}
      </div>
    </Dialog>
  );
}

/** The plans on offer and what each includes, from the same catalogue the public pricing page uses. A company can ask
 * for any plan other than its own; the commercial team answers with an order. */
export function PlansCard({ current }: { current: string }) {
  // Asking for a plan needs billing.subscription.request_change; without it the button is not offered (the cell still decides).
  const access = useCellQuery([...BILLING_KEY, "access"], (api) => api.billing.access());
  const mayAsk = !access.data?.enforced || access.data.permissions.includes("billing.subscription.request_change");
  const catalog = useCellQuery([...BILLING_KEY, "catalog"], (api) => api.billing.catalog());
  const requests = useCellQuery(REQUESTS_KEY, (api) => api.billing.planRequests());
  const [asking, setAsking] = useState<CatalogPlan | null>(null);
  const latest = requests.data?.items[0];
  // Each plan's monthly platform fee, from the approved price book the catalogue came with (none until one is approved).
  const pricing = catalog.data?.pricing ?? null;
  const fee = new Map((pricing?.lines ?? []).filter((l) => l.sku === "ZID-BASE").map((l) => [l.plan, l.unit_rate]));
  const price = (p: CatalogPlan): string => {
    const rate = fee.get(p.plan);
    if (rate === undefined || !pricing) return "—";
    if (Number(rate) === 0) return "Free";
    return `${p.platform_fee_is_floor ? "From " : ""}${money(rate, pricing.currency)} / month`;
  };
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
    { key: "price", header: "Price", render: (p) => <span className="tabular-nums">{price(p)}</span>, sortValue: (p) => Number(fee.get(p.plan) ?? -1) },
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
        p.plan === current || !mayAsk ? null : (
          <Button size="xs" variant="secondary" isDisabled={Boolean(open)} onPress={() => setAsking(p)}>
            {open ? "Request open" : "Ask for this plan"}
          </Button>
        ),
    },
  ];
  return (
    <Card title="Choose a plan" description="What each plan includes. Asking for one sends a request to our team: nothing is charged and your plan does not change until an order is signed." padded={false}>
      {latest && latest.status !== "WITHDRAWN" ? (
        <div className="border-b border-border p-4">
          <PlanRequestStatus request={latest} />
        </div>
      ) : null}
      <QueryView query={catalog} loadingRows={2}>
        {(c) => (
          <>
            {c.plans.length === 0 ? (
              <EmptyState title="No plans published" icon={Layers} compact>
                Plans appear once a price book is approved.
              </EmptyState>
            ) : (
              <div className="grid gap-4 p-4 sm:grid-cols-2 xl:grid-cols-3">
                {c.plans.map((p) => (
                  <PlanCard
                    key={p.plan}
                    plan={p}
                    isCurrent={p.plan === current}
                    price={{ amount: fee.get(p.plan) ?? null, currency: pricing?.currency }}
                    canAsk={mayAsk}
                    hasOpen={Boolean(open)}
                    onAsk={() => setAsking(p)}
                  />
                ))}
              </div>
            )}
            <details className="border-t border-border">
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-brand-700">Compare every detail side by side</summary>
              <DataTable<CatalogPlan>
                caption="Available plans"
                columns={columns}
                rows={c.plans}
                rowKey={(p) => p.plan}
                empty={<EmptyState title="No plans published" icon={Layers} compact>Plans appear once a price book is approved.</EmptyState>}
              />
            </details>
            <RatesTable catalog={c} />
            <p className="border-t border-border px-4 py-3 text-xs text-ink-muted">{c.note}</p>
          </>
        )}
      </QueryView>
      {asking ? <AskDialog plan={asking} current={catalog.data?.plans.find((p) => p.plan === current)} catalog={catalog.data} onClose={() => setAsking(null)} /> : null}
    </Card>
  );
}


/** What each plan charges beyond what it includes, in plain words. Only metered items appear; the monthly fee is in the
 * Price column above. Rates come from the approved price book, so nothing is shown until one is approved. */
const RATE_LABEL: Record<string, string> = {
  "ZID-HUM": "Each person above the included amount",
  "ZID-AUTH": "1,000 sign-ins above the included amount",
  "ZID-PDP": "10,000 access decisions above the included amount",
  "ZID-M2M": "1,000 machine grants above the included amount",
  "ZID-VCI": "Each credential issued",
  "ZID-VCP": "Each credential check",
  "ZID-FED": "Each extra single sign-on connection, per month",
  "ZID-SCIM": "Each extra provisioning source, per month",
};
const RATE_ORDER = Object.keys(RATE_LABEL);

/** A rate keeps its small decimals (0.015 must not read as 0.02), unlike a bill amount. */
function rate(amount: string, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency, currencyDisplay: "code", minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(Number(amount));
}

function RatesTable({ catalog }: { catalog: Catalog }) {
  const pricing = catalog.pricing;
  if (!pricing) return null;
  const byKey = new Map(pricing.lines.map((l) => [`${l.sku}|${l.plan}`, l]));
  const plans = catalog.plans.filter((p) => pricing.lines.some((l) => l.plan === p.plan && l.sku !== "ZID-BASE"));
  const skus = RATE_ORDER.filter((sku) => pricing.lines.some((l) => l.sku === sku));
  if (plans.length === 0 || skus.length === 0) return null;
  return (
    <div className="border-t border-border px-4 py-3">
      <h3 className="text-sm font-semibold text-ink">What you pay above the included amounts</h3>
      <p className="mt-1 text-xs text-ink-muted">Each plan includes the amounts in the table above. Beyond them, usage is charged at these rates.</p>
      <table className="mt-3 w-full text-sm">
        <caption className="sr-only">Rates above the included amounts, by plan</caption>
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
            <th scope="col" className="py-2">
              Item
            </th>
            {plans.map((p) => (
              <th key={p.plan} scope="col" className="py-2 text-right">
                {p.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {skus.map((sku) => (
            <tr key={sku} className="border-b border-border last:border-0">
              <th scope="row" className="py-2 text-left font-normal">
                {RATE_LABEL[sku]}
              </th>
              {plans.map((p) => {
                const line = byKey.get(`${sku}|${p.plan}`);
                return (
                  <td key={p.plan} className="tabular py-2 text-right">
                    {line?.unit_rate ? rate(line.unit_rate, pricing.currency) : "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
