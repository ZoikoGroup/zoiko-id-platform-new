"use client";

import type { BillingOrder, Catalog, CatalogPlan, PlanRequest } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Check, CreditCard, FileSignature, Layers, Send } from "lucide-react";
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

/** Where the company's ask for a different price stands, in words. Asking changes nothing by itself: the team answers with a price for this company, the company buys at it and pays,
 * and only then does the plan change. */
function PlanRequestStatus({ request, onBuy }: { request: PlanRequest; onBuy: (plan: string) => void }) {
  const withdraw = useCellMutation((api, id: string) => api.billing.withdrawPlanRequest(id), {
    invalidate: [REQUESTS_KEY],
    success: "Request withdrawn",
  });
  const plan = request.plan.toLowerCase();
  if (request.status === "OPEN") {
    return (
      <Banner
        tone="info"
        title="Your request is with our team"
        action={
          <Button size="sm" variant="secondary" pending={withdraw.isPending} onPress={() => withdraw.mutate(request.request_id)}>
            Withdraw
          </Button>
        }
      >
        You asked about the {plan} plan on <DateTime value={request.created_at} />
        {request.desired_amount ? `, hoping to pay ${money(request.desired_amount, "USD")} a month` : ""}. Our team will reply with a price for your company. Your plan does not change until you buy it and pay.
        {withdraw.error ? <ApiErrorView error={withdraw.error} /> : null}
      </Banner>
    );
  }
  if (request.status === "ACCEPTED") {
    const o = request.offer;
    if (o?.state === "OPEN") {
      return (
        <Banner
          tone="ok"
          title={`Our team agreed a price for you: ${money(o.amount, o.currency)} a month for ${plan}`}
          action={
            <Button size="sm" onPress={() => onBuy(request.plan)}>
              Buy at your price
            </Button>
          }
        >
          {Number(o.reduction_percent) > 0 ? `That is ${o.reduction_percent}% below the list price of ${money(o.list_amount, o.currency)}. ` : ""}
          This price is for your company only and is open until <DateTime value={o.expires_at} />. Your plan changes only after you pay.
          {request.decision_note ? ` Note from our team: ${request.decision_note}` : ""}
        </Banner>
      );
    }
    if (o?.state === "PENDING_APPROVAL") {
      return (
        <Banner tone="info" title={`Our team is confirming your price for ${plan}`}>
          You will be told as soon as it is ready. Nothing is charged until you buy.
        </Banner>
      );
    }
    if (o?.state === "USED") {
      return (
        <Banner tone="ok" title={`You bought ${plan} at your agreed price`}>
          {money(o.amount, o.currency)} a month, from your request on <DateTime value={request.created_at} />.
        </Banner>
      );
    }
    if (o?.state === "WITHDRAWN") {
      return (
        <Banner tone="warn" title={`The price our team offered for ${plan} was withdrawn`}>
          You can still buy the plan at the list price, or ask again.
        </Banner>
      );
    }
    return (
      <Banner tone="ok" title={`Your request for ${plan} was accepted`}>
        Nothing changes until you buy the plan and pay. {request.decision_note ? `Note from our team: ${request.decision_note}` : ""}
      </Banner>
    );
  }
  if (request.status === "DECLINED") {
    return (
      <Banner tone="warn" title={`Your request for ${plan} was not accepted`}>
        {request.decision_note ?? "Our team did not give a reason."} You can buy the plan at the list price, or ask again.
      </Banner>
    );
  }
  return null;
}


/** One plan as a card: the name, the monthly fee, what it includes and a single action. Every figure is the published catalogue's; a figure it does
 * not state reads "Agreed per order", and the fee reads "Agreed in the order" when no price book is approved. */
function PlanCard({ plan, isCurrent, price, deal, onAsk, canAsk, hasOpen, onBuy }: { plan: CatalogPlan; isCurrent: boolean; price: { amount: string | null; currency: string | undefined }; deal: { amount: string; list: string; currency: string; until: string | null } | null; onAsk: () => void; canAsk: boolean; hasOpen: boolean; onBuy: (() => void) | null }) {
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
      {deal ? (
        <div className="mt-3">
          <Badge tone="ok" size="xs">
            Price agreed for your company
          </Badge>
          <p className="mt-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-0">
            <span className="text-3xl font-semibold tabular-nums text-ink">{money(deal.amount, deal.currency)}</span>
            <span className="text-sm text-ink-muted">/ month</span>
            <span className="text-sm tabular-nums text-ink-subtle line-through">{money(deal.list, deal.currency)}</span>
          </p>
          {deal.until ? <p className="mt-0.5 text-xs text-ink-muted">Open until {new Date(deal.until).toLocaleDateString("en-GB")}</p> : null}
        </div>
      ) : null}
      {!deal ? (
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
      ) : null}
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
        ) : (
          <div className="space-y-2">
            {onBuy ? (
              <Button className="w-full" variant="primary" icon={<CreditCard aria-hidden className="size-4" />} onPress={onBuy}>
                {deal ? "Buy at your price" : `Buy ${plan.label}`}
              </Button>
            ) : null}
            {canAsk ? (
              <Button className="w-full" variant={onBuy ? "secondary" : "primary"} isDisabled={hasOpen} onPress={onAsk}>
                {hasOpen ? "A request is open" : onBuy ? "Ask for a different price" : "Review and ask for this plan"}
              </Button>
            ) : null}
            {!onBuy && !canAsk ? <p className="text-center text-xs text-ink-muted">Only an owner or billing admin can buy or ask for a plan.</p> : null}
          </div>
        )}
      </div>
    </article>
  );
}

/** The plan side by side with the one the company has now: fee first, then what each includes. Shared by asking for a plan and buying one. */
function ReviewSection({ plan, current, catalog, children }: { plan: CatalogPlan; current: CatalogPlan | undefined; catalog: Catalog | undefined; children: React.ReactNode }) {
  const rows = reviewRows(current, plan);
  const fee = monthlyFee(catalog, plan.plan);
  const currentFee = current ? monthlyFee(catalog, current.plan) : null;
  const currency = catalog?.pricing?.currency;
  return (
    <section aria-label="Review of the change">
      <h3 className="mb-1 text-sm font-medium text-ink">{current ? `${current.label} to ${plan.label}` : plan.label}</h3>
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
      <p className="mt-2 text-xs text-ink-muted">{children}</p>
    </section>
  );
}

/** The plans a company can buy here: Launch, Business and Scale. Developer is automatic and Enterprise is a contract, so both stay a request to our team. */
const SELF_SERVE = new Set(["LAUNCH", "BUSINESS", "SCALE"]);

/** Buying a plan (CR-147): the exact amount from the approved price book, then Stripe's own page. The plan changes only after Stripe confirms the payment,
 * never because this page was reached. The buyer must tick that they authorise the charge; nothing is pre-ticked. */
function BuyDialog({ plan, current, catalog, onClose, onFallback }: { plan: CatalogPlan; current: CatalogPlan | undefined; catalog: Catalog | undefined; onClose: () => void; onFallback: () => void }) {
  const offer = useCellQuery([...BILLING_KEY, "plan-offer", plan.plan], (api) => api.billing.planOffer(plan.plan));
  const [agreed, setAgreed] = useState(false);
  const [legalName, setLegalName] = useState("");
  const [country, setCountry] = useState("");
  const lost = losses(current, plan);
  const o = offer.data;
  const needsDetails = Boolean(o?.needs_billing_details);
  const detailsOk = !needsDetails || (legalName.trim().length >= 2 && /^[A-Za-z]{2}$/.test(country.trim()));
  const buy = useCellMutation(
    (api) =>
      api.billing.purchasePlan(plan.plan, {
        expected_amount: o?.amount ?? "",
        expected_currency: o?.currency ?? "",
        ...(needsDetails ? { legal_name: legalName.trim(), country: country.trim().toUpperCase() } : {}),
      }),
    { onSuccess: (r) => window.location.assign(r.url) },
  );
  const off = offer.error === null && o !== undefined && !o.purchase_enabled;
  return (
    <Dialog
      isOpen
      onOpenChange={(open) => !open && !buy.isPending && onClose()}
      title={`Buy the ${plan.label} plan`}
      description="You review the exact amount, then pay on Stripe's secure page. Your plan changes only after Stripe confirms the payment."
      footer={
        <>
          <Button variant="secondary" isDisabled={buy.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={buy.isPending} isDisabled={!o || off || !agreed || !detailsOk} icon={<CreditCard aria-hidden className="size-4" />} onPress={() => buy.mutate(undefined)}>
            {o ? `Pay ${money(o.amount, o.currency)} on Stripe` : "Pay on Stripe"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {offer.isPending ? <p role="status" className="text-sm text-ink-muted">Checking today's price…</p> : null}
        {offer.error ? (
          <>
            <ApiErrorView error={offer.error} title="This plan cannot be bought here" />
            <Button variant="secondary" onPress={onFallback}>
              Ask our team instead
            </Button>
          </>
        ) : null}
        {off ? (
          <Banner tone="info" title="Buying online is not switched on yet">
            Our team sets your plan up for now. You can ask for it instead and nothing is charged.
            <div className="mt-2">
              <Button variant="secondary" onPress={onFallback}>
                Ask our team instead
              </Button>
            </div>
          </Banner>
        ) : null}
        {o ? (
          <>
            {o.negotiated ? (
              <Banner tone="ok" title="This is the price our team agreed for your company">
                {money(o.amount, o.currency)} a month instead of the list price of {money(o.list_amount, o.currency)}.
                {o.offer_expires_at ? ` It is open until ${new Date(o.offer_expires_at).toLocaleDateString("en-GB")}.` : ""}
              </Banner>
            ) : null}
            <ReviewSection plan={plan} current={current} catalog={catalog}>
              {o.negotiated ? "The table shows the list price; you pay your agreed price." : "The price is today's published price."} Tax is added where it applies and shown on Stripe's page before you pay.
            </ReviewSection>
            <section aria-label="What you pay" className="rounded-md border border-border bg-surface-sunken p-3 text-sm">
              <p className="flex items-baseline justify-between gap-3">
                <span className="font-medium text-ink">Due today</span>
                <span className="text-xl font-semibold tabular-nums text-ink">{money(o.amount, o.currency)}</span>
              </p>
              <p className="mt-1 text-xs text-ink-muted">
                This is the first month's platform fee. It renews each month and is credited against your invoice. Usage above what is included is billed on the invoice at the rates shown. You can change or end the plan at the end of a billing period.
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
            {needsDetails ? (
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="sm:col-span-2">
                  <TextField label="Company legal name" description="The company the invoice is issued to." value={legalName} onChange={setLegalName} />
                </div>
                <TextField label="Country" description="Two letters, for example DE." value={country} onChange={setCountry} />
              </div>
            ) : (
              <p className="text-xs text-ink-muted">Invoiced to {o.billing_name}.</p>
            )}
            <Checkbox isSelected={agreed} onChange={setAgreed}>
              I authorise this charge of {money(o.amount, o.currency)} and the monthly renewal.
            </Checkbox>
          </>
        ) : null}
        {buy.error ? <ApiErrorView error={buy.error} title="Nothing was charged" /> : null}
      </div>
    </Dialog>
  );
}

function AskDialog({ plan, current, catalog, onClose }: { plan: CatalogPlan; current: CatalogPlan | undefined; catalog: Catalog | undefined; onClose: () => void }) {
  const [note, setNote] = useState("");
  const [want, setWant] = useState("");
  const [understood, setUnderstood] = useState(false);
  const lost = losses(current, plan);
  const wantOk = want.trim() === "" || /^\d{1,7}(\.\d{1,2})?$/.test(want.trim().replace(/^\$/, "").replaceAll(",", ""));
  const ask = useCellMutation((api) => api.billing.requestPlan(plan.plan, note, want.trim().replace(/^\$/, "").replaceAll(",", "")), {
    invalidate: [REQUESTS_KEY],
    success: "Request sent. Our team will reply with a price",
    onSuccess: onClose,
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !ask.isPending && onClose()}
      title={`Ask for a different price: ${plan.label}`}
      description="Tell our team what you would like to pay. They reply with a price for your company, you buy the plan at that price, and your plan changes only after you pay."
      footer={
        <>
          <Button variant="secondary" isDisabled={ask.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={ask.isPending} isDisabled={!understood || !wantOk} icon={<Send aria-hidden className="size-4" />} onPress={() => ask.mutate(undefined)}>
            Send request
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <ReviewSection plan={plan} current={current} catalog={catalog}>
          This is the list price. A different price for your company is agreed with our team before you pay, and tax is added where it applies.
        </ReviewSection>
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
          label="Price you would like to pay a month (optional)"
          value={want}
          onChange={setWant}
          inputMode="decimal"
          placeholder="for example 4000"
          description="In US dollars. It helps our team, and it is not a promise either way."
          errorMessage={wantOk ? undefined : "Use numbers only, with at most two decimals."}
        />
        <TextField
          label="What do you need it for? (optional)"
          value={note}
          onChange={setNote}
          multiline
          rows={4}
          description="For example: two more people with single sign-on, or a higher volume of access decisions."
        />
        <Checkbox isSelected={understood} onChange={setUnderstood}>
          I understand this only asks our team for a price. Nothing is charged and nothing changes until I buy the plan and pay.
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
  // What each plan costs this company today: the list price, or a lower price the team agreed for it. The cell also says which plans cannot be bought here.
  const offers = useCellQuery([...BILLING_KEY, "plan-offers"], (api) => api.billing.planOffers());
  const offerOf = (code: string) => offers.data?.items.find((x) => x.plan === code);
  const dealOf = (code: string) => {
    const o = offerOf(code);
    return o?.buyable && o.negotiated && o.amount && o.list_amount ? { amount: o.amount, list: o.list_amount, currency: o.currency ?? "USD", until: o.offer_expires_at ?? null } : null;
  };
  const [asking, setAsking] = useState<CatalogPlan | null>(null);
  const [buying, setBuying] = useState<CatalogPlan | null>(null);
  // Buying needs the payment permission as well; the cell decides regardless. Only the bigger self-serve plans are offered for purchase: a move to a smaller one
  // takes effect at the end of a period and is arranged with our team.
  const mayBuy = !access.data?.enforced || access.data.permissions.includes("billing.payment.initiate");
  const rank = (code: string) => catalog.data?.plans.findIndex((p) => p.plan === code) ?? -1;
  const buyable = (p: CatalogPlan) => mayBuy && Boolean(pricingReady) && SELF_SERVE.has(p.plan) && (offerOf(p.plan)?.buyable ?? rank(p.plan) > rank(current));
  const latest = requests.data?.items[0];
  // Each plan's monthly platform fee, from the approved price book the catalogue came with (none until one is approved).
  const pricing = catalog.data?.pricing ?? null;
  const pricingReady = pricing !== null;
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
    <Card title="Choose a plan" description="Buy a plan at its price now, or ask our team for a different price for your company. Either way your plan changes only after you pay." padded={false}>
      {latest && latest.status !== "WITHDRAWN" ? (
        <div className="border-b border-border p-4">
          <PlanRequestStatus
            request={latest}
            onBuy={(code) => {
              const pl = catalog.data?.plans.find((x) => x.plan === code);
              if (pl) setBuying(pl);
            }}
          />
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
                    deal={dealOf(p.plan)}
                    hasOpen={Boolean(open)}
                    onAsk={() => setAsking(p)}
                    onBuy={buyable(p) ? () => setBuying(p) : null}
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
      {buying ? (
        <BuyDialog
          plan={buying}
          current={catalog.data?.plans.find((p) => p.plan === current)}
          catalog={catalog.data}
          onClose={() => setBuying(null)}
          onFallback={() => {
            setAsking(buying);
            setBuying(null);
          }}
        />
      ) : null}
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
