"use client";

import { PageHeader, Tabs } from "@zoiko/ui";
import { Gavel } from "lucide-react";

import { ApprovalsPanel } from "./approvals";
import { GrantsPanel } from "./grants";

export function AuthorityPage() {
  return (
    <>
      <PageHeader
        icon={Gavel}
        title="Authority & approvals"
        description="Issue and revoke authority grants, inspect their chains, and decide on transaction-bound approval requests."
      />
      <Tabs
        label="Authority sections"
        tabs={[
          { id: "grants", label: "Grants", content: <GrantsPanel /> },
          { id: "approvals", label: "Approvals", content: <ApprovalsPanel /> },
        ]}
      />
    </>
  );
}
