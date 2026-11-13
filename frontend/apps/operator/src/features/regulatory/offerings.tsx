"use client";

import type { ServiceOffering } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, PageHeader, TextField, type Column } from "@zoiko/ui";
import { LayoutGrid } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/op";

import { Facts, Mono, REG, Reasons, upper } from "./common";

const OFFERINGS_KEY = [...REG.readiness, "offerings"];

function available(o: ServiceOffering): string[] {
  return o.countries.filter((c) => c.status === "AVAILABLE").map((c) => c.country);
}

function OfferingDialog({ offering, onClose }: { offering: ServiceOffering | null; onClose: () => void }) {
  return (
    <Dialog isOpen={offering !== null} onOpenChange={(open) => !open && onClose()} title={offering?.title ?? ""} size="xl">
      {offering ? (
        <div className="space-y-4">
          <p className="text-sm">{offering.wording}</p>
          <Facts
            items={[
              ["Gated on", offering.capabilities.length ? offering.capabilities.join(", ") : "not country-gated"],
              ["Biometric", offering.biometric ? "yes" : "no"],
              ["Proved by", offering.cases.join(", ")],
              ["Built in", <Mono key="c">{offering.code.join(", ")}</Mono>],
            ]}
          />
          <table className="w-full text-sm">
            <caption className="sr-only">Coverage by country</caption>
            <thead>
              <tr className="text-left text-ink-muted">
                <th>Country</th>
                <th>Status</th>
                <th>What is missing</th>
              </tr>
            </thead>
            <tbody>
              {offering.countries.map((c) => (
                <tr key={c.country} className="border-t border-border align-top">
                  <td>
                    <Mono>{c.country}</Mono>
                  </td>
                  <td>{c.status === "AVAILABLE" ? <Badge tone="ok">Available</Badge> : <Badge tone="danger">Not available</Badge>}</td>
                  <td>
                    <Reasons reasons={c.reasons} none="Nothing" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Dialog>
  );
}

export function OfferingsPage() {
  const [country, setCountry] = useState("");
  const asked = upper(country);
  const q = useCellQuery([...OFFERINGS_KEY, asked], (api) => api.regulatory.offerings(asked.length === 2 ? asked : undefined));
  const [openId, setOpenId] = useState<string | null>(null);
  const open = q.data?.items.find((o) => o.offering_id === openId) ?? null;
  const columns: Column<ServiceOffering>[] = [
    { key: "title", header: "Offering", pinned: true, render: (o) => o.title, sortValue: (o) => o.title },
    {
      key: "available",
      header: "Available in",
      render: (o) => {
        const where = available(o);
        return where.length ? <span className="font-mono text-xs">{where.join(", ")}</span> : <Badge tone="danger">Nowhere yet</Badge>;
      },
      sortValue: (o) => available(o).length,
    },
    {
      key: "blocked",
      header: "Blocked in",
      render: (o) => `${o.countries.length - available(o).length} of ${o.countries.length}`,
    },
    { key: "cases", header: "Tests", render: (o) => o.cases.length },
    {
      key: "open",
      header: "Actions",
      align: "end",
      render: (o) => (
        <Button size="xs" variant="secondary" onPress={() => setOpenId(o.offering_id)} aria-label={`Why: ${o.title}`}>
          Why
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title="Service offerings"
        description="The 12 Zoiko iD offerings. An offering is published in a country only when provider rights, country rules, privacy controls and independently accepted tests all hold there. The public catalog (GET /v1/offerings) shows only what is available."
      />
      <div className="space-y-5">
        <Banner tone="info">Design specifications are not production certification: nothing here is a certification claim.</Banner>
        <TextField label="Country" value={country} onChange={setCountry} placeholder="All countries" className="max-w-40" />
        <Card padded={false} title="Offerings">
          <QueryView query={q}>
            {(d) => (
              <DataTable
                caption="Service offerings"
                columns={columns}
                rows={d.items}
                rowKey={(o) => o.offering_id}
                empty={
                  <EmptyState icon={LayoutGrid} title="No offerings">
                    The offerings catalog is empty.
                  </EmptyState>
                }
              />
            )}
          </QueryView>
        </Card>
      </div>
      <OfferingDialog offering={open} onClose={() => setOpenId(null)} />
    </>
  );
}
