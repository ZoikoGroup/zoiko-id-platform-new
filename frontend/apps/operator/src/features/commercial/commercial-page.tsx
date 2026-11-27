"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Badge, Banner, PageHeader, Tabs } from "@zoiko/ui";
import { Receipt, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { ActionForm } from "./action-form";
import { ACTIONS, GROUP_LABEL, type Action } from "./actions";
import { EligibilityView, FinanceAuthorityView, FinanceOverviewView, FinanceRequestsView } from "./finance-page";
import { useDirectMode } from "./mode";
import { ActivationRefundsView, DataDeletionStatusCard } from "./gap-views";
import { MarketView } from "./market-views";
import { PlanOffersView } from "./plan-offers";
import { PlansAndPrices } from "./plans-prices";
import { DisputesView, ReconciliationView, RefundsView } from "./payments-page";
import { AuditView, COMMERCIAL_KEY, ExceptionsView, PaymentEventsView, PlanRequestsView, PriceBooksView, ServiceRequestsView } from "./views";

function Actions({ group }: { group: Action["group"] }) {
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: COMMERCIAL_KEY });
  return (
    <div className="space-y-4">
      {ACTIONS.filter((a) => a.group === group).map((a) => (
        <ActionForm key={a.id} action={a} onDone={refresh} />
      ))}
    </div>
  );
}

type Sub = { id: string; label: string; content: ReactNode };

/** One section of the page: a line that says what it is for, then its own few tabs. */
function Section({ label, intro, tabs }: { label: string; intro: string; tabs: Sub[] }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">{intro}</p>
      <Tabs label={label} tabs={tabs} />
    </div>
  );
}

/** Commercial, in five sections instead of one row of fourteen tabs. Nothing was removed: each tab sits under the section it belongs to, and the identity verification tabs now live on their own page. */
export function CommercialPage() {
  const direct = useDirectMode();
  return (
    <>
      <PageHeader
        icon={Receipt}
        title="Commercial"
        actions={
          direct ? (
            <Badge tone="info" size="sm">
              <span className="inline-flex items-center gap-1.5">
                <ShieldCheck aria-hidden className="size-3.5" />
                Single operator: every change is logged under your name
              </span>
            </Badge>
          ) : undefined
        }
        description={
          direct
            ? "What customers pay, who is on which plan, billing and payments, and the suppliers behind it. You make and approve every change yourself."
            : "What customers pay, who is on which plan, billing and payments, and the suppliers behind it. Each action needs its own approved grant, and some steps need a different operator to approve."
        }
      />
      <Banner tone="info" className="mb-5">
        A price change applies to new purchases only. Companies that already have a plan keep the price they signed, and every change is saved with your name.
      </Banner>
      <Tabs
        label="Commercial"
        tabs={[
          {
            id: "pricing",
            label: "Pricing",
            content: (
              <Section
                label="Pricing"
                intro="What customers pay for each plan. Start with Plans and prices; price books are the advanced way to change the same thing."
                tabs={[
                  { id: "prices", label: "Plans and prices", content: <PlansAndPrices /> },
                  {
                    id: "catalogue",
                    label: "Price books (advanced)",
                    content: (
                      <div className="space-y-4">
                        <PriceBooksView />
                        <details className="rounded-lg border border-border bg-surface">
                          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink-muted">Advanced: change a price book by its ID</summary>
                          <div className="space-y-4 p-4">
                            <Actions group="catalogue" />
                          </div>
                        </details>
                      </div>
                    ),
                  },
                ]}
              />
            ),
          },
          {
            id: "customers",
            label: "Customers",
            content: (
              <Section
                label="Customers"
                intro="The plan each company is on, the requests to change it, and the quotes and orders that put a company on a plan."
                tabs={[
                  {
                    id: "plans",
                    label: GROUP_LABEL.plans,
                    content: (
                      <div className="space-y-4">
                        <PlanRequestsView />
                        <PlanOffersView />
                        <ServiceRequestsView />
                        <Actions group="plans" />
                        <DataDeletionStatusCard />
                      </div>
                    ),
                  },
                  { id: "sales", label: GROUP_LABEL.sales, content: <Actions group="sales" /> },
                ]}
              />
            ),
          },
          {
            id: "billing",
            label: "Billing and payments",
            content: (
              <Section
                label="Billing and payments"
                intro="Invoices and billing runs, the payments that arrive, and putting things right when a payment goes wrong: credits, refunds and disputes."
                tabs={[
                  { id: "billing-runs", label: GROUP_LABEL.billing, content: <Actions group="billing" /> },
                  {
                    id: "payments",
                    label: GROUP_LABEL.payments,
                    content: (
                      <div className="space-y-4">
                        <PaymentEventsView />
                        <ActivationRefundsView />
                        <Actions group="payments" />
                      </div>
                    ),
                  },
                  {
                    id: "finance",
                    label: "Credits, refunds and disputes",
                    content: (
                      <Tabs
                        label="Finance"
                        tabs={[
                          { id: "f-overview", label: "Overview", content: <FinanceOverviewView /> },
                          { id: "f-requests", label: "Credits", content: <FinanceRequestsView /> },
                          { id: "f-refunds", label: "Refunds", content: <RefundsView /> },
                          { id: "f-disputes", label: "Disputes", content: <DisputesView /> },
                          { id: "f-recon", label: "Reconciliation", content: <ReconciliationView /> },
                          { id: "f-authority", label: "Policy and roles", content: <FinanceAuthorityView /> },
                          { id: "f-eligibility", label: "Eligibility", content: <EligibilityView /> },
                        ]}
                      />
                    ),
                  },
                ]}
              />
            ),
          },
          {
            id: "suppliers",
            label: "Suppliers and markets",
            content: (
              <Section
                label="Suppliers and markets"
                intro="The outside providers the platform depends on, the checks that they stay reliable, and which markets are open for sale."
                tabs={[
                  { id: "vendors", label: GROUP_LABEL.vendors, content: <Actions group="vendors" /> },
                  {
                    id: "assurance",
                    label: GROUP_LABEL.assurance,
                    content: (
                      <div className="space-y-4">
                        <ExceptionsView />
                        <Actions group="assurance" />
                      </div>
                    ),
                  },
                  { id: "market", label: "Market release", content: <MarketView /> },
                ]}
              />
            ),
          },
          { id: "audit", label: "Audit and reports", content: <AuditView /> },
        ]}
      />
    </>
  );
}
