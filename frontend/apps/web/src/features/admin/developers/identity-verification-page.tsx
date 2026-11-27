"use client";

import { PageHeader, Tabs } from "@zoiko/ui";
import { useState } from "react";

import { ConnectGuideSection } from "./connect-guide";
import { DeliveriesSection } from "./deliveries";
import { DeveloperHomeSection } from "./developer-home";
import { CertificationSection, TryItSection } from "./diagnostics";
import { MoreSection } from "./developers-page";
import { EndpointsSection } from "./endpoints";
import { OverviewSection } from "./overview";
import { ResultAddress } from "./result-address";
import { VerifyApiSection } from "./verify-api";

/** The one place for the identity verification product: everything a company needs to add it to another project, in the order it needs it. The Developers page keeps the same parts for people who prefer
 * the engineering view. */
export function IdentityVerificationPage() {
  const [tab, setTab] = useState("home");
  return (
    <>
      <PageHeader
        title="Identity verification"
        description="Verify your own customers from your own project. The Overview shows how many checks ran and how they ended; the other tabs hold the keys, the connection guide, the webhooks and every verification."
      />
      <Tabs
        label="Identity verification"
        selectedTab={tab}
        onSelectionChange={setTab}
        tabs={[
          {
            id: "home",
            label: "Overview",
            content: (
              <div className="space-y-6">
                <OverviewSection onGo={setTab} />
                <DeveloperHomeSection onGo={setTab} />
              </div>
            ),
          },
          { id: "verify-api", label: "Applications and keys", content: <VerifyApiSection show="applications" /> },
          { id: "connect", label: "Connect your project", content: <ConnectGuideSection /> },
          {
            id: "endpoints",
            label: "Webhooks",
            content: (
              <div className="space-y-6">
                <ResultAddress />
                <DeliveriesSection />
                <details className="rounded-lg border border-border p-3">
                  <summary className="cursor-pointer text-sm font-medium text-ink">More options: several addresses, pause or retire one</summary>
                  <div className="mt-4">
                    <EndpointsSection identityOnly />
                  </div>
                </details>
              </div>
            ),
          },
          { id: "results", label: "Verifications and usage", content: <VerifyApiSection show="activity" /> },
          {
            id: "try",
            label: "Test",
            content: (
              <div className="space-y-6">
                <TryItSection />
                <CertificationSection />
              </div>
            ),
          },
          { id: "more", label: "More", content: <MoreSection /> },
        ]}
      />
    </>
  );
}
