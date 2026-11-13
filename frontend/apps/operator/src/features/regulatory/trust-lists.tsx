"use client";

import type { TrustListsView } from "@zoiko/api-client";
import { Badge, Banner, Card, DataTable, EmptyState, PageHeader, type Column } from "@zoiko/ui";
import { FileSignature } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/op";

import { Facts, Reasons, REG } from "./common";

type Row = TrustListsView["lists"][number];

/** WF-05: qualified signature and seal validation rests only on authenticated trusted lists; legal interpretation is kept apart. */
export function TrustListsPage() {
  const q = useCellQuery([...REG.readiness, "trust-lists"], (api) => api.regulatory.trustLists());
  const columns: Column<Row>[] = [
    { key: "name", header: "Trusted list", pinned: true, render: (r) => <span className="font-mono text-xs">{r.name}</span>, sortValue: (r) => r.name },
    {
      key: "auth",
      header: "Signature",
      render: (r) => (r.authenticated ? <Badge tone="ok">Verified</Badge> : <Badge tone="danger">Not verified: ignored</Badge>),
    },
    { key: "services", header: "Services", render: (r) => r.services },
    { key: "qc", header: "Qualified CA services", render: (r) => r.qualified_ca_services },
    { key: "granted", header: "Granted now", render: (r) => r.granted_now },
    { key: "problem", header: "Problem", render: (r) => r.problem ?? "-" },
  ];
  return (
    <>
      <PageHeader
        title="Trust lists"
        description="WF-05: what the signature and seal validator relies on. Qualified status comes only from trusted lists whose signature was verified; an unverified list counts for nothing."
      />
      <QueryView query={q}>
        {(d) => (
          <div className="space-y-5">
            <Banner tone="info">{d.interpretation}</Banner>
            {!d.qualified_status_assessable ? (
              <Banner tone="warn" title="Qualified status cannot be assessed">
                <Reasons reasons={d.reasons} /> Every signature is reported as technically valid or not, with qualified status NOT_ASSESSED.
              </Banner>
            ) : null}
            <Card title="Configuration">
              <Facts
                items={[
                  ["Trust anchors", String(d.anchors)],
                  ["Timestamp authority roots", String(d.tsa_roots)],
                  ["Revocation lists", String(d.crls)],
                  ["Platform timestamp authority", d.timestamp_authority_configured ? "configured" : "not configured"],
                ]}
              />
            </Card>
            <Card padded={false} title="Trusted lists">
              <DataTable
                caption="Trusted lists"
                columns={columns}
                rows={d.lists}
                rowKey={(r) => r.name}
                empty={
                  <EmptyState icon={FileSignature} title="No trusted lists loaded">
                    Run the trusted-list refresh tool to download and verify the EU lists.
                  </EmptyState>
                }
              />
            </Card>
          </div>
        )}
      </QueryView>
    </>
  );
}
