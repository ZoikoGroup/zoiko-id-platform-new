"use client";

import type { ScreeningProvider } from "@zoiko/api-client";
import { Badge, Banner, Card, DataTable, PageHeader, type Column } from "@zoiko/ui";

import { QueryView, useCellQuery } from "@/lib/op";

import { Mono } from "../regulatory/common";

import { blockedText, providerTone } from "./provider-logic";

const columns: Column<ScreeningProvider>[] = [
  { key: "p", header: "Provider", pinned: true, render: (r) => <Mono>{r.provider_id}</Mono> },
  { key: "s", header: "State", render: (r) => <Badge tone={providerTone(r)}>{r.state.toLowerCase()}</Badge> },
  { key: "c", header: "Covers", render: (r) => `${r.classes.join(", ").replaceAll("_", " ").toLowerCase() || "nothing"} in ${r.markets.join(", ") || "no market"}` },
  { key: "g", header: "Regions", render: (r) => r.regions.join(", ") || "none" },
  { key: "m", header: "Cost", render: (r) => `${r.unit_cost} a check, budget ${r.monthly_budget ?? "none"}, quota ${r.monthly_quota ?? "none"}` },
  { key: "f", header: "Authorised by", render: (r) => r.finance_authorised_by ?? "nobody" },
  { key: "x", header: "Circuit", render: (r) => (r.circuit.open ? <Badge tone="danger">open: not being asked</Badge> : r.circuit.recent_failures ? `${r.circuit.recent_failures} recent failures` : "closed") },
];

export function ScreeningProvidersPage() {
  const q = useCellQuery(["screening", "providers"], (api) => api.regulatory.screeningProviders());
  return (
    <>
      <PageHeader
        title="Screening providers"
        description="Licensed sources for politically exposed persons, associates and adverse media. None is used until it is approved, authorised by Finance, contracted for the market and region, inside its budget, and its adapter is installed. There is no failover to another provider or region: a provider that is down makes its check unavailable, never clear."
        breadcrumbs={[{ label: "Screening" }, { label: "Providers" }]}
      />
      <div className="space-y-4">
        <Banner tone="info" title="Set by a reviewed change, not a click">
          Providers are listed in the versioned provider registry. This view shows the effect of that change and what still stops each provider being used.
        </Banner>
        <QueryView query={q}>
          {(d) =>
            d.items.length ? (
              <>
                <DataTable<ScreeningProvider> caption="Providers" rows={d.items} rowKey={(r) => r.provider_id} empty="" columns={columns} />
                {d.items.map((p) => (
                  <Card key={p.provider_id} title={p.provider_id} description={p.blocked_by.length ? "Cannot be used yet" : "Every written control is in place"}>
                    {p.blocked_by.length ? <ul className="list-disc space-y-1 pl-5 text-sm">{p.blocked_by.map((c) => <li key={c}>{blockedText(c)}</li>)}</ul> : <p className="text-sm text-ink-muted">Legal, Privacy and Finance still decide whether the provider is right; these are only the controls the platform can check.</p>}
                  </Card>
                ))}
              </>
            ) : (
              <p className="text-sm text-ink-muted">No provider is registered. Politically exposed persons, associates and adverse media are not available to anyone.</p>
            )
          }
        </QueryView>
      </div>
    </>
  );
}
