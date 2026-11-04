"use client";

import type { BillingState } from "@zoiko/api-client";
import { Banner, PageHeader, Tabs } from "@zoiko/ui";
import { Wallet } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { AccessSection, ACCESS_KEY } from "./access";
import { BudgetsSection } from "./budgets";
import { CommitmentsSection } from "./commitments";
import { InvoicesSection } from "./invoices";
import { OverviewSection } from "./overview";
import { PaymentSection } from "./payment";
import { PlanFeaturesSection } from "./plan-features-section";
import { PreviewSection } from "./preview";
import { ProfileSection } from "./profile";
import { BILLING_KEY, STATE_COPY } from "./shared";
import { SubscriptionSection } from "./subscription";
import { UsageSection } from "./usage";

export function BillingPage() {
  // Stripe sends the buyer back with ?payment=saved|cancelled: open the Payment tab so the return is handled there.
  const returning = useSearchParams().get("payment") !== null;
  // The menu follows the person's effective billing permissions (ZID-COM-CL-002); the server still decides every call.
  const access = useCellQuery(ACCESS_KEY, (api) => api.billing.access());
  const can = (key: string) => !access.data?.enforced || access.data.permissions.includes(key);
  // The overview offers one next action; its target is a tab id, so the tabs are controlled here.
  const [tab, setTab] = useState<string | undefined>(returning ? "payment" : undefined);
  return (
    <>
      <PageHeader
        title="Billing"
        description="Your plan and what it includes, usage against it, the bill so far, invoices and budgets. Figures come from the platform's ledger; this page never decides what you can use."
      />
      {can("billing.overview.read") ? <StateBanner /> : null}
      <Tabs
        label="Billing"
        selectedTab={tab ?? (can("billing.overview.read") ? "overview" : "features")}
        onSelectionChange={setTab}
        tabs={[
          can("billing.overview.read") ? { id: "overview", label: "Overview", content: <OverviewSection goto={setTab} /> } : null,
          can("billing.overview.read") ? { id: "subscription", label: "Subscription", content: <SubscriptionSection /> } : null,
          { id: "features", label: "Plan features", content: <PlanFeaturesSection /> },
          { id: "usage", label: "Usage", content: <UsageSection /> },
          can("billing.overview.read") ? { id: "preview", label: "Bill preview", content: <PreviewSection /> } : null,
          can("billing.invoice.read") ? { id: "invoices", label: "Invoices", content: <InvoicesSection /> } : null,
          can("billing.overview.read")
            ? {
                id: "commitments",
                label: "Commitments",
                content: <CommitmentsSection canRequest={can("billing.subscription.request_change")} canDelegate={can("billing.access.delegate")} canSeeDelegates={can("billing.audit.read")} />,
              }
            : null,
          can("billing.overview.read") ? { id: "profile", label: "Billing profile", content: <ProfileSection canEdit={can("billing.profile.manage")} canSeeHistory={can("billing.audit.read")} /> } : null,
          can("billing.payment_method.read_masked") ? { id: "payment", label: "Payment", content: <PaymentSection /> } : null,
          { id: "budgets", label: "Budgets", content: <BudgetsSection /> },
          { id: "access", label: "Billing access", content: <AccessSection /> },
        ].filter((t): t is NonNullable<typeof t> => t !== null)}
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
