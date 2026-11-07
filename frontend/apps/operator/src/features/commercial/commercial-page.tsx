"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Badge, Banner, PageHeader, Tabs } from "@zoiko/ui";
import { Receipt, ShieldCheck } from "lucide-react";

import { ActionForm } from "./action-form";
import { ACTIONS, GROUP_LABEL, type Action } from "./actions";
import { EligibilityView, FinanceAuthorityView, FinanceOverviewView, FinanceRequestsView } from "./finance-page";
import { useDirectMode } from "./mode";
import { ActivationRefundsView, DataDeletionStatusCard } from "./gap-views";
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
            ? "Price books, plans, quotes and orders, billing runs, suppliers and revenue assurance. You make and approve every change here yourself."
            : "Price books, plans, quotes and orders, billing runs, suppliers and revenue assurance. Every action needs its own approved, unexpired grant; dual-control steps need a different operator."
        }
      />
      <Banner tone="info" className="mb-5">
        Rates here are draft proposals until a price book is approved. Nothing on this page publishes a price, promises a region or SLA, or reaches a customer before that
        approval.
      </Banner>
      <Tabs
        label="Commercial"
        tabs={[
          {
            id: "catalogue",
            label: GROUP_LABEL.catalogue,
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
          {
            id: "plans",
            label: GROUP_LABEL.plans,
            content: (
              <div className="space-y-4">
                <PlanRequestsView />
                <ServiceRequestsView />
                <Actions group="plans" />
                <DataDeletionStatusCard />
              </div>
            ),
          },
          { id: "sales", label: GROUP_LABEL.sales, content: <Actions group="sales" /> },
          { id: "billing", label: GROUP_LABEL.billing, content: <Actions group="billing" /> },
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
            label: "Finance",
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
          { id: "audit", label: "Audit and reports", content: <AuditView /> },
        ]}
      />
    </>
  );
}
