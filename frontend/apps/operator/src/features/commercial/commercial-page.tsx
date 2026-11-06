"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Banner, PageHeader, Tabs } from "@zoiko/ui";

import { ActionForm } from "./action-form";
import { ACTIONS, GROUP_LABEL, type Action } from "./actions";
import { EligibilityView, FinanceAuthorityView, FinanceOverviewView, FinanceRequestsView } from "./finance-page";
import { useDirectMode } from "./mode";
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
        title="Commercial"
        description={
          direct
            ? "Price books, plans, quotes and orders, billing runs, suppliers and revenue assurance. You can make and approve every change here yourself."
            : "Price books, plans, quotes and orders, billing runs, suppliers and revenue assurance. Every action needs its own approved, unexpired grant; dual-control steps need a different operator."
        }
      />
      {direct ? (
        <Banner tone="warn" className="mb-4" title="Direct mode">
          This account can approve and change commercial settings without access requests or a second approver. Every change is still recorded
          under your name in the commercial change log.
        </Banner>
      ) : null}
      <Banner tone="info" className="mb-4">
        Rates here are draft proposals until Finance approves a price book. Nothing on this page publishes a price, promises a region or SLA, or
        reaches a customer before the approvals the platform enforces.
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
                <Actions group="catalogue" />
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
