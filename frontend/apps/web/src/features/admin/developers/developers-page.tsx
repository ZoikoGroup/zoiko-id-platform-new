"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { DeliveriesSection } from "./deliveries";
import { EndpointsSection } from "./endpoints";
import { RateLimitsSection } from "./rate-limits";
import { UsageSection } from "./usage";
import { VerifyGuideSection } from "./verify-guide";

export function DevelopersPage() {
  return (
    <>
      <PageHeader
        title="Developers"
        description="Webhook endpoints and deliveries, API usage, your request limit, and how to verify the signatures on every delivery. Applications and API credentials are under Applications."
      />
      <Tabs
        label="Developers"
        tabs={[
          { id: "endpoints", label: "Webhook endpoints", content: <EndpointsSection /> },
          { id: "deliveries", label: "Delivery log", content: <DeliveriesSection /> },
          { id: "usage", label: "Usage", content: <UsageSection /> },
          { id: "limits", label: "Rate limits", content: <RateLimitsSection /> },
          { id: "verify", label: "Verify signatures", content: <VerifyGuideSection /> },
        ]}
      />
    </>
  );
}
