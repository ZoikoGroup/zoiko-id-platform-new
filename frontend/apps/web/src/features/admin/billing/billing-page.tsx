"use client";

import type { BillingState } from "@zoiko/api-client";
import { Banner, PageHeader, Tabs } from "@zoiko/ui";
import { Wallet } from "lucide-react";
import { useSearchParams } from "next/navigation";

import { QueryView, useCellQuery } from "@/lib/cell";

import { BudgetsSection } from "./budgets";
import { InvoicesSection } from "./invoices";
import { PaymentSection } from "./payment";
import { PreviewSection } from "./preview";
import { BILLING_KEY, STATE_COPY } from "./shared";
import { SubscriptionSection } from "./subscription";
import { UsageSection } from "./usage";

export function BillingPage() {
  // Stripe sends the buyer back with ?payment=saved|cancelled: open the Payment tab so the return is handled there.
  const returning = useSearchParams().get("payment") !== null;
  return (
    <>
      <PageHeader
        title="Billing"
        description="Your plan and what it includes, usage against it, the bill so far, invoices and budgets. Figures come from the platform's ledger; this page never decides what you can use."
      />
      <StateBanner />
      <Tabs
        label="Billing"
        defaultTab={returning ? "payment" : undefined}
        tabs={[
          { id: "subscription", label: "Subscription", content: <SubscriptionSection /> },
          { id: "usage", label: "Usage", content: <UsageSection /> },
          { id: "preview", label: "Bill preview", content: <PreviewSection /> },
          { id: "invoices", label: "Invoices", content: <InvoicesSection /> },
          { id: "payment", label: "Payment", content: <PaymentSection /> },
          { id: "budgets", label: "Budgets", content: <BudgetsSection /> },
        ]}
      />
    </>
  );
}

function StateBanner() {
  const state = useCellQuery([...BILLING_KEY, "state"], (api) => api.billing.state());
  return (
    <QueryView query={state} loadingRows={1}>
      {(s) => <StateView s={s} />}
    </QueryView>
  );
}

function StateView({ s }: { s: BillingState }) {
  if (s.state === "ACTIVE" && !s.safety_hold) return null;
  const tone = s.state === "PAST_DUE" ? "warn" : "danger";
  return (
    <Banner tone={tone} icon={Wallet} title={`Billing state: ${s.state.replace("_", " ").toLowerCase()}`} live="polite">
      {s.safety_hold ? "A safety hold is in place on this account. " : ""}
      {STATE_COPY[s.state] ?? ""}
      {s.restricted.length ? (
        <span className="mt-1 block text-xs">Restricted now: {s.restricted.join(", ")}.</span>
      ) : (
        <span className="mt-1 block text-xs">Nothing is restricted right now.</span>
      )}
      {s.never_restricted.length ? <span className="mt-1 block text-xs">Never restricted for billing: {s.never_restricted.join(", ")}.</span> : null}
    </Banner>
  );
}
