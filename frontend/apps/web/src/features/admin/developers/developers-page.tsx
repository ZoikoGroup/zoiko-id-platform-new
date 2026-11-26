"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { DeliveriesSection } from "./deliveries";
import { DeveloperHomeSection, ErrorCatalogSection, SdkRegisterSection } from "./developer-home";
import { CertificationSection, ClaimsSection, EndpointHealthSection, RequestExplorerSection, ScopeHistorySection, TraceSection, TryItSection } from "./diagnostics";
import { EndpointsSection } from "./endpoints";
import { SandboxSection } from "./sandbox";
import { RateLimitsSection } from "./rate-limits";
import { UsageSection } from "./usage";
import { VerifyApiSection } from "./verify-api";
import { VerifyGuideSection } from "./verify-guide";

export function DevelopersPage() {
  return (
    <>
      <PageHeader
        title="Developers"
        description="The Verify API (applications and credentials for your own server), webhook endpoints and deliveries, API usage, your request limit, and how to verify the signatures on every delivery. Applications and API credentials are under Applications."
      />
      <Tabs
        label="Developers"
        tabs={[
          { id: "home", label: "First verification", content: <DeveloperHomeSection /> },
          { id: "verify-api", label: "Verify API", content: <VerifyApiSection /> },
          { id: "endpoints", label: "Webhook endpoints", content: <EndpointsSection /> },
          { id: "deliveries", label: "Delivery log", content: <DeliveriesSection /> },
          { id: "usage", label: "Usage", content: <UsageSection /> },
          { id: "limits", label: "Rate limits", content: <RateLimitsSection /> },
          { id: "sandbox", label: "Test identities", content: <SandboxSection /> },
          { id: "verify", label: "Verify signatures", content: <VerifyGuideSection /> },
          { id: "scopes", label: "Scopes", content: <ScopeHistorySection /> },
          { id: "health", label: "Endpoint health", content: <EndpointHealthSection /> },
          { id: "requests", label: "Requests", content: <RequestExplorerSection /> },
          { id: "trace", label: "Trace", content: <TraceSection /> },
          { id: "suite", label: "Test run", content: <CertificationSection /> },
          { id: "try", label: "Try it", content: <TryItSection /> },
          { id: "sdks", label: "SDKs", content: <SdkRegisterSection /> },
          { id: "claims", label: "Public claims", content: <ClaimsSection /> },
          { id: "errors", label: "Errors", content: <ErrorCatalogSection /> },
        ]}
      />
    </>
  );
}
