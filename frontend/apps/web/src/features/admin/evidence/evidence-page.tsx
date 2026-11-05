"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { AuditSection } from "./audit";
import { BulkExportsSection } from "./bulk-exports";
import { CasesSection } from "./cases";
import { HoldsSection } from "./holds";
import { KeysSection, ResolveHandleSection } from "./keys-identity";
import { ProvenanceSection } from "./provenance";
import { ReceiptsSection } from "./receipts";

export function EvidencePage() {
  return (
    <>
      <PageHeader
        title="Evidence & audit"
        description="Pseudonymous decision receipts, protected audit records, ledger integrity, signed exports, legal holds, evidence cases and provenance."
      />
      <Tabs
        label="Evidence"
        tabs={[
          { id: "receipts", label: "Receipts", content: <ReceiptsSection /> },
          { id: "bulk", label: "Bulk exports", content: <BulkExportsSection /> },
          { id: "audit", label: "Audit & integrity", content: <AuditSection /> },
          { id: "holds", label: "Legal holds", content: <HoldsSection /> },
          { id: "cases", label: "Cases", content: <CasesSection /> },
          { id: "provenance", label: "Provenance", content: <ProvenanceSection /> },
          {
            id: "keys",
            label: "Keys & identity",
            content: (
              <div className="space-y-5">
                <KeysSection />
                <ResolveHandleSection />
              </div>
            ),
          },
        ]}
      />
    </>
  );
}
