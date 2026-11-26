"use client";

import { Banner, PageHeader, Tabs } from "@zoiko/ui";
import { useState } from "react";

import { ActionsSection, CredentialsSection, MandateSigningSection, ProvidersSection, RiskSection, StopsSection } from "./sections";

type Tab = "stops" | "actions" | "providers" | "credentials" | "mandates" | "risk";

/** Agent controls (ZID-CP-WF07, CR-169). What each part can and cannot do is said where it is used. */
export function AgentControlsPage() {
  const [tab, setTab] = useState<Tab>("stops");
  return (
    <>
      <PageHeader
        title="Agent controls"
        description="Stop agents, see what became of the calls they were allowed to make, bind outside identity providers, issue agent credentials, confirm mandates and see what deserves a look."
      />
      <Banner tone="info" title="Identity, not authority">
        Nothing on this page lets an agent do anything. A credential or a provider's claim says who an agent is. What it may do is decided at the gateway, call by call.
      </Banner>
      <div className="mt-4">
        <Tabs
          label="Agent controls"
          selectedTab={tab}
          onSelectionChange={(id) => setTab(id as Tab)}
          tabs={[
            { id: "stops", label: "Stops", content: <StopsSection /> },
            { id: "actions", label: "Calls and receipts", content: <ActionsSection /> },
            { id: "providers", label: "Outside providers", content: <ProvidersSection /> },
            { id: "credentials", label: "Credentials", content: <CredentialsSection /> },
            { id: "mandates", label: "Mandate signing", content: <MandateSigningSection /> },
            { id: "risk", label: "Places to look", content: <RiskSection /> },
          ]}
        />
      </div>
    </>
  );
}
