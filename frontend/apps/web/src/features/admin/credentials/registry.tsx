"use client";

import type { TrustRegistry } from "@zoiko/api-client";
import { Banner, Card, DataTable, EmptyState, StatusBadge, type Column } from "@zoiko/ui";
import { Library } from "lucide-react";

import { useCellQuery } from "@/lib/cell";

import { AdminQueryView } from "../identity-providers/shared";

export const REGISTRY_KEY = ["admin", "credentials", "trust-registry"];

type Issuer = TrustRegistry["issuers"][number];
type Source = TrustRegistry["sources"][number];

const list = (v: string[]) => (v.length ? v.join(", ") : "—");

const ISSUER_COLUMNS: Column<Issuer>[] = [
  { key: "name", header: "Issuer", render: (i) => <span className="font-medium text-ink">{i.name}</span> },
  { key: "kind", header: "Kind", render: (i) => i.kind },
  { key: "countries", header: "Countries", render: (i) => list(i.countries) },
  { key: "claims", header: "Claim types", render: (i) => list(i.claim_types) },
  { key: "authority", header: "Legal basis", render: (i) => <StatusBadge state={i.authority === "OFFICIAL" ? "ACTIVE" : "UNKNOWN"} size="xs" /> },
  { key: "status", header: "Status", render: (i) => (i.revoked_on ? `Revoked ${i.revoked_on}` : "In force") },
];

const SOURCE_COLUMNS: Column<Source>[] = [
  { key: "source", header: "Source", render: (s) => <span className="font-mono text-xs">{s.source_id}</span> },
  { key: "claims", header: "Claim types", render: (s) => list(s.claim_types) },
  { key: "countries", header: "Countries", render: (s) => list(s.countries) },
  { key: "purposes", header: "Permitted purposes", render: (s) => list(s.purposes) },
  { key: "regions", header: "Processing regions", render: (s) => list(s.processing_regions) },
  { key: "kind", header: "Kind", render: (s) => `${s.simulated ? "Simulated (never counted as coverage)" : s.primary ? "Primary source" : "Aggregator"} · ${s.sensitivity}` },
  { key: "valid", header: "Contract", render: (s) => `${s.valid_from} → ${s.valid_until ?? "open"}` },
];

/** WF-04: what the platform trusts, read-only. It changes only through reviewed configuration, never from this page. */
export function RegistrySection() {
  const q = useCellQuery(REGISTRY_KEY, (api) => api.credentials.trustRegistry());
  return (
    <AdminQueryView query={q}>
      {(r) => (
        <div className="space-y-6">
          <Banner tone="info" icon={Library}>
            Read only. Issuers, sources, policies and certifications change only through reviewed configuration. Registry v{r.issuer_registry_version}, packs v{r.packs_version}.
          </Banner>
          <Card title={`Issuers (${r.issuers.length})`}>
            {r.issuers.length ? (
              <DataTable caption="Issuers" columns={ISSUER_COLUMNS} rows={r.issuers} rowKey={(i) => i.issuer_id} />
            ) : (
              <EmptyState title="No issuers registered">Nothing can be resolved to a trusted issuer until owners register one.</EmptyState>
            )}
          </Card>
          <Card title={`Source contracts (${r.sources.length})`}>
            {r.sources.length ? (
              <DataTable caption="Source contracts" columns={SOURCE_COLUMNS} rows={r.sources} rowKey={(s) => s.source_id} />
            ) : (
              <EmptyState title="No source contracts">Without a contract no source can be asked about a person.</EmptyState>
            )}
          </Card>
          <Card title={`Eligibility policies (${r.policies.length}) · tier rules ${r.tier_rules}`}>
            {r.policies.length ? (
              <ul className="space-y-1 text-sm">
                {r.policies.map((p) => (
                  <li key={`${p.policy_id}-${p.version}`}>
                    <span className="font-mono text-xs">
                      {p.policy_id} v{p.version}
                    </span>{" "}
                    · {p.country}
                    {p.subdivision ? `-${p.subdivision}` : ""} · {p.profession_code} · {p.service_code}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="No policies">Eligibility cannot be decided without a published policy.</EmptyState>
            )}
          </Card>
          <Card title={`Certifications (${r.certifications.length})`}>
            {r.certifications.length ? (
              <ul className="space-y-1 text-sm">
                {r.certifications.map((c) => (
                  <li key={c.record_id}>
                    {c.scheme} · {c.country} · {c.service_code} · {c.entity} · {c.status} ({c.valid_from} → {c.valid_until ?? "open"})
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="No certifications held">No certified-provider claim can be made until a record with its evidence is added.</EmptyState>
            )}
          </Card>
        </div>
      )}
    </AdminQueryView>
  );
}
