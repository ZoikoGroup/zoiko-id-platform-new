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
        description="Enterprise sign-in through external OIDC and SAML providers, and the OpenID Federation anchors they can be resolved through."
      />
      <Banner tone="info" icon={Fingerprint} className="mb-6">
        Every change on this page is a critical (R3) action: the cell asks for a fresh passkey check before applying it, and your tenant&apos;s
        policy may also require a second administrator&apos;s approval.
      </Banner>
      <Tabs
        label="Identity provider configuration"
        tabs={[
          { id: "integrations", label: "Integrations", content: <IntegrationsSection /> },
          { id: "anchors", label: "Trust anchors", content: <TrustAnchorsSection /> },
        ]}
      />
    </>
  );
}
