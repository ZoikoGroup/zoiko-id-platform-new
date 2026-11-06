"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { AdaptersSection } from "./adapters";
import { RelianceDecisionPanel, ReviewDecisionPanel } from "./decisions";
import { PrincipalAssurancePanel } from "./principal";
import { BulkStartSection } from "./bulk-start";
import { ProfilesSection } from "./profiles";

export function VerificationAdminPage() {
  return (
    <>
      <PageHeader
        title="Identity verification"
        description="Verification profiles, provider adapters, manual review and reliance decisions, and each principal's identity assurance."
      />
      <Tabs
        label="Identity verification administration"
        tabs={[
          { id: "profiles", label: "Profiles", content: <ProfilesSection /> },
          { id: "adapters", label: "Adapters", content: <AdaptersSection /> },
          { id: "bulk", label: "Bulk start", content: <BulkStartSection /> },
          {
            id: "decisions",
            label: "Review & reliance",
            content: (
              <div className="grid gap-6 xl:grid-cols-2">
                <ReviewDecisionPanel />
                <RelianceDecisionPanel />
              </div>
            ),
          },
          { id: "principal", label: "Principal lookup", content: <PrincipalAssurancePanel /> },
        ]}
      />
    </>
  );
}
