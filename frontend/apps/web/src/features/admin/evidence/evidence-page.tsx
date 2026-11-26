"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { AuditSection } from "./audit";
import { BulkExportsSection } from "./bulk-exports";
import { CasesSection } from "./cases";
import { HoldsSection } from "./holds";
import { KeysSection, ResolveHandleSection } from "./keys-identity";
import { ProvenanceSection } from "./provenance";
import { ReceiptsSection } from "./receipts";
import { AuthorityAtDecisionSection, ClaimsRegisterSection, ExportRequestsSection, ForensicSection, StatusListSection, TrustInspectorSection } from "./wf08-sections";

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
          { id: "authority", label: "Authority at decision", content: <AuthorityAtDecisionSection /> },
          { id: "forensic", label: "Forensic reconstruction", content: <ForensicSection /> },
          { id: "export-requests", label: "Export tiers", content: <ExportRequestsSection /> },
          { id: "status", label: "Status list", content: <StatusListSection /> },
          { id: "trust", label: "Trust inspector", content: <TrustInspectorSection /> },
          { id: "claims", label: "Claims register", content: <ClaimsRegisterSection /> },
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
