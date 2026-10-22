"use client";

import type { PrincipalItem, VerificationAssertion } from "@zoiko/api-client";
import { AssuranceBadge, Card, CopyId, DataTable, DateTime, EmptyState, KeyValues, StatusBadge, humanize, type Column } from "@zoiko/ui";
import { ScanFace } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/cell";

const columns: Column<VerificationAssertion>[] = [
  { key: "claim", header: "Claim", pinned: true, render: (a) => humanize(a.claim_type), sortValue: (a) => a.claim_type },
  { key: "level", header: "Assurance", render: (a) => <AssuranceBadge kind="Identity" level={a.assurance_level} />, sortValue: (a) => a.assurance_level },
  { key: "status", header: "Status", render: (a) => <StatusBadge state={a.status} kind="Assertion" />, sortValue: (a) => a.status },
  { key: "method", header: "Method", render: (a) => humanize(a.verification_method) },
  { key: "verified", header: "Verified", render: (a) => <DateTime value={a.verified_at} />, sortValue: (a) => a.verified_at ?? "" },
  { key: "fresh", header: "Fresh until", render: (a) => <DateTime value={a.fresh_until} />, sortValue: (a) => a.fresh_until ?? "" },
  { key: "id", header: "Assertion ID", render: (a) => <CopyId value={a.assertion_id} label="assertion ID" />, defaultHidden: true },
];

export function AssuranceCard({ principal }: { principal: PrincipalItem }) {
  const id = principal.principal_id;
  const assurance = useCellQuery(["admin", "principal", id, "assurance"], (api) => api.verification.assurance(id));
  const assertions = useCellQuery(["admin", "principal", id, "assertions"], (api) => api.verification.assertions(id));

  return (
    <Card title="Identity assurance" description="Read-only. Verified claims behind this principal's identity assurance." padded={false}>
      <div className="border-b border-border p-5">
        <QueryView query={assurance} loadingRows={1} notFound={<p className="text-sm text-ink-muted">No assurance recorded.</p>}>
          {(a) => {
            const level = a.identity_proofing?.level ?? null;
            return (
              <KeyValues
                items={[["Identity assurance", <AssuranceBadge key="l" kind="Identity" level={level === "IAL-Z0" ? null : level} />]]}
              />
            );
          }}
        </QueryView>
      </div>
      <QueryView query={assertions} loadingRows={3}>
        {(data) => (
          <DataTable
            caption="Verification assertions"
            columns={columns}
            rows={data.items}
            rowKey={(a) => a.assertion_id}
            dense
            empty={
              <EmptyState compact icon={ScanFace} title="No verification assertions">
                This principal has not completed an identity verification.
              </EmptyState>
            }
          />
        )}
      </QueryView>
    </Card>
  );
}
