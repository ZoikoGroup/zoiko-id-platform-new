"use client";

import { PageHeader, Tabs } from "@zoiko/ui";
import { useState } from "react";

import { DeliveriesSection } from "./deliveries";
import { DeveloperHomeSection, ErrorCatalogSection, SdkRegisterSection } from "./developer-home";
import { CertificationSection, ClaimsSection, EndpointHealthSection, RequestExplorerSection, ScopeHistorySection, TraceSection, TryItSection } from "./diagnostics";
import { EndpointsSection } from "./endpoints";
import { SandboxSection } from "./sandbox";
import { RateLimitsSection } from "./rate-limits";
import { UsageSection } from "./usage";
import { VerifyApiSection } from "./verify-api";
import { VerifyGuideSection } from "./verify-guide";

/** Everything a first customer does not need on day one. It stays one click away, under a single tab, so the bar above shows only the steps of getting started. */
export function MoreSection() {
  return (
    <Tabs
      label="More developer tools"
      tabs={[
        { id: "deliveries", label: "Delivery log", content: <DeliveriesSection /> },
        { id: "usage", label: "Usage", content: <UsageSection /> },
        { id: "limits", label: "Rate limits", content: <RateLimitsSection /> },
        { id: "sandbox", label: "Test identities", content: <SandboxSection /> },
        { id: "verify", label: "Verify signatures", content: <VerifyGuideSection /> },
        { id: "scopes", label: "Scopes", content: <ScopeHistorySection /> },
        { id: "health", label: "Endpoint health", content: <EndpointHealthSection /> },
        { id: "requests", label: "Requests", content: <RequestExplorerSection /> },
        { id: "trace", label: "Trace", content: <TraceSection /> },
        { id: "sdks", label: "SDKs", content: <SdkRegisterSection /> },
        { id: "claims", label: "Public claims", content: <ClaimsSection /> },
        { id: "errors", label: "Errors", content: <ErrorCatalogSection /> },
      ]}
    />
  );
}

export function DevelopersPage() {
  const [tab, setTab] = useState("home");
  return (
    <>
      <PageHeader
        title="Developers"
        description="Verify your own customers from your own server. New here? Stay on Get started: it shows the one thing to do next, and takes you to it."
      />
      <Tabs
        label="Developers"
        selectedTab={tab}
        onSelectionChange={setTab}
        tabs={[
          { id: "home", label: "Get started", content: <DeveloperHomeSection onGo={setTab} /> },
          { id: "verify-api", label: "Applications and keys", content: <VerifyApiSection /> },
          { id: "endpoints", label: "Webhook endpoints", content: <EndpointsSection /> },
          { id: "try", label: "Try it", content: <TryItSection /> },
          { id: "suite", label: "Test run", content: <CertificationSection /> },
          { id: "more", label: "More", content: <MoreSection /> },
        ]}
      />
    </>
  );
}
