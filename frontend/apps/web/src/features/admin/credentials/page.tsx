"use client";

import { Banner, PageHeader, Tabs } from "@zoiko/ui";
import { Fingerprint } from "lucide-react";

import { ConfigurationsSection } from "./configurations";
import { IssuedSection } from "./issued";
import { IssuerTrustSection } from "./issuer-trust";
import { PresentationPanel } from "./presentation";
import { RelyingPartiesSection } from "./relying-parties";

export function CredentialsAdminPage() {
  return (
    <>
      <PageHeader
        title="Credentials"
        description="Verifiable credentials this tenant issues and accepts: issued credentials, configurations, issuer trust, relying parties and presentation requests."
      />
      <Banner tone="info" icon={Fingerprint} className="mb-6">
        Status changes, configurations, issuer trust and relying-party registrations are critical (R3) actions: the cell asks for a fresh
        passkey check before applying them.
      </Banner>
      <Tabs
        label="Credential administration"
        tabs={[
          { id: "issued", label: "Issued", content: <IssuedSection /> },
          { id: "configurations", label: "Configurations", content: <ConfigurationsSection /> },
          { id: "trust", label: "Issuer trust", content: <IssuerTrustSection /> },
          { id: "rps", label: "Relying parties", content: <RelyingPartiesSection /> },
          { id: "presentations", label: "Presentations", content: <PresentationPanel /> },
        ]}
      />
    </>
  );
}
