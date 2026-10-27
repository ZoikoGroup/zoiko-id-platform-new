"use client";

import { Banner, PageHeader, Tabs } from "@zoiko/ui";
import { Fingerprint } from "lucide-react";

import { IntegrationsSection } from "./integrations";
import { TrustAnchorsSection } from "./trust-anchors";

export function IdentityProvidersPage() {
  return (
    <>
      <PageHeader
        title="Identity providers"
        description="Let people sign in with the account your company already uses, such as Microsoft, Okta or Google. Pick your provider and follow the steps."
      />
      <Banner tone="info" icon={Fingerprint} className="mb-6">
        Changes here affect how everyone signs in, so we ask you to confirm with your passkey before saving each one.
      </Banner>
      <Tabs
        label="Identity provider configuration"
        tabs={[
          { id: "integrations", label: "Providers", content: <IntegrationsSection /> },
          { id: "anchors", label: "Trust anchors", content: <TrustAnchorsSection /> },
        ]}
      />
    </>
  );
}
