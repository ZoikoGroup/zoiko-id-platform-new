"use client";

import type { RegulatoryReadiness } from "@zoiko/api-client";
import { Badge, Banner, Card, DataTable, EmptyState, PageHeader, StatusBadge, type Column } from "@zoiko/ui";
import { Gauge } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/op";

import { REG, Reasons } from "./common";

type Row = RegulatoryReadiness["countries"][number]["capabilities"][number] & { country: string };

const MODE_TONE: Record<string, "ok" | "warn" | "danger"> = { enforce: "ok", shadow: "warn", off: "danger" };

export function ReadinessPage() {
  const q = useCellQuery(REG.readiness, (api) => api.regulatory.readiness());
  const columns: Column<Row>[] = [
    { key: "country", header: "Country", pinned: true, render: (r) => <span className="font-mono">{r.country}</span>, sortValue: (r) => r.country },
    { key: "capability", header: "Capability", render: (r) => <span className="font-mono text-2xs">{r.capability}</span>, sortValue: (r) => r.capability },
    {
      key: "availability",
      header: "Availability",
      render: (r) => (r.availability === "-" ? "-" : <StatusBadge state={r.availability} kind="Availability" />),
    },
    { key: "provider", header: "Provider", render: (r) => r.provider ?? <span className="text-ink-subtle">none</span> },
    {
      key: "verdict",
      header: "Under enforce",
      render: (r) => (r.would_refuse.length ? <Badge tone="danger">Refused</Badge> : <Badge tone="ok">Allowed</Badge>),
      sortValue: (r) => r.would_refuse.length,
    },
    { key: "why", header: "Why it would be refused", render: (r) => <Reasons reasons={r.would_refuse} none="Nothing missing" /> },
  ];
  return (
    <>
      <PageHeader
        title="Enforce readiness"
        description="What switching each regulatory check from shadow to enforce would refuse today, market by market. This page changes nothing: the switches are set per environment by their owners."
      />
      <QueryView query={q}>
        {(d) => {
          const rows: Row[] = d.countries.flatMap((c) => c.capabilities.map((cap) => ({ ...cap, country: c.country })));
          const ready = rows.filter((r) => !r.would_refuse.length).length;
          return (
            <div className="space-y-5">
              <Card title="Switches">
                <ul className="flex flex-wrap gap-2" aria-label="Switch modes">
                  {Object.entries(d.modes).map(([k, m]) => (
                    <li key={k}>
                      <Badge tone={MODE_TONE[m] ?? "neutral"}>
                        {k.replaceAll("_", " ")}: {m}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </Card>
              {d.empty_configuration.length ? (
                <Banner tone="warn" title="Owner configuration still empty">
                  {d.empty_configuration.join(", ")}. Until these are filled and approved, enforce refuses whatever depends on them.
                </Banner>
              ) : null}
              <Card padded={false} title={`${ready} of ${rows.length} would be allowed under enforce`}>
                <DataTable
                  caption="Enforce readiness"
                  columns={columns}
                  rows={rows}
                  rowKey={(r) => `${r.country}:${r.capability}`}
                  empty={
                    <EmptyState icon={Gauge} title="No countries configured">
                      The country registry lists no regulated capability.
                    </EmptyState>
                  }
                />
              </Card>
            </div>
          );
        }}
      </QueryView>
    </>
  );
}
