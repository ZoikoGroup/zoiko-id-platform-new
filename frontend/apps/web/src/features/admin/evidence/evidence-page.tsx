"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { AuditSection } from "./audit";
import { HoldsSection } from "./holds";
import { ReceiptsSection } from "./receipts";

export function EvidencePage() {
  return (
    <>
      <PageHeader
        title="Evidence & audit"
        description="Pseudonymous decision receipts, protected audit records, ledger integrity, signed exports and legal holds."
      />
      <Tabs
        label="Evidence"
        tabs={[
          { id: "receipts", label: "Receipts", content: <ReceiptsSection /> },
          { id: "audit", label: "Audit & integrity", content: <AuditSection /> },
          { id: "holds", label: "Legal holds", content: <HoldsSection /> },
        ]}
      />
    </>
  );
}
