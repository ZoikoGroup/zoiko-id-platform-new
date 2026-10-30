"use client";

import type { DowngradeImpact, EffectiveEntitlements, PlanFeature, ReservationUsage } from "@zoiko/api-client";
import { Badge, Banner, Card, DataTable, EmptyState, KeyValues, Select, type Column } from "@zoiko/ui";
import { Layers, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { bySection, featureTone, lowerBands, meterNote, reasonText, summary, upgradeHint } from "./plan-features";
import { BILLING_KEY, count } from "./shared";

export function PlanFeaturesSection() {
  const view = useCellQuery([...BILLING_KEY, "effective"], (api) => api.billing.effective());
  return <QueryView query={view}>{(v) => <Body v={v} />}</QueryView>;
}

function Body({ v }: { v: EffectiveEntitlements }) {
  const s = summary(v);
  return (
    <div className="space-y-4">
      <Banner tone="info" icon={ShieldCheck} title="A plan unlocks capabilities and allowances, nothing else">
        {v.note}
      </Banner>
      <Card
        title={`Band ${v.band ?? "—"} on the ${v.plan.toLowerCase()} plan`}
        description={`Catalogue ${v.catalog_version}. ${v.source === "DEFAULT" ? "The default plan: no signed order yet." : "From a signed order."}`}
      >
        <KeyValues
          columns={4}
          items={[
            ["Included now", `${s.included} of ${s.total}`],
            ["Available with an upgrade or add-on", String(s.upgrade)],
            ["On hold for a region or route", String(s.held)],
            ["Subscription", v.subscription_state.toLowerCase()],
          ]}
        />
      </Card>
      {bySection(v.features).map((g) => (
        <Card key={g.section} title={g.title} description={g.help} padded={false}>
          <FeatureTable items={g.items} />
        </Card>
      ))}
      <DowngradeCard band={v.band} />
      <MetersCard />
    </div>
  );
}

function FeatureTable({ items }: { items: PlanFeature[] }) {
  const columns: Column<PlanFeature>[] = [
    { key: "name", header: "Capability", pinned: true, render: (f) => f.name, sortValue: (f) => f.name },
    {
      key: "state",
      header: "For you",
      render: (f) => (
        <span className="inline-flex flex-col gap-0.5">
          <Badge tone={featureTone(f)} size="xs">
            {f.label ?? "—"}
          </Badge>
          {f.available ? null : <span className="text-xs text-ink-muted">{reasonText(f.reasons)}</span>}
          {upgradeHint(f) ? <span className="text-xs text-ink-muted">{upgradeHint(f)}</span> : null}
        </span>
      ),
    },
    { key: "note", header: "Note", render: (f) => <span className="text-xs text-ink-muted">{f.note}</span> },
  ];
  return <DataTable caption="Capabilities" columns={columns} rows={items} rowKey={(f) => f.feature} />;
}

function DowngradeCard({ band }: { band: string | null }) {
  const options = lowerBands(band);
  const [to, setTo] = useState<string | null>(null);
  const impact = useCellQuery([...BILLING_KEY, "downgrade", to], (api) => api.billing.downgradeImpact(to as string), { enabled: Boolean(to) });
  if (options.length === 0) return null;
  return (
    <Card title="Before you move to a lower band" description="See what would switch off. Nothing is changed or deleted by looking.">
      <Select label="Band to compare" options={options.map((b) => ({ id: b, label: b }))} value={to} onChange={setTo} placeholder="Choose a band" />
      {to ? <QueryView query={impact}>{(d) => <Impact d={d} />}</QueryView> : null}
    </Card>
  );
}

function Impact({ d }: { d: DowngradeImpact }) {
  return (
    <div className="mt-3 space-y-3 text-sm">
      {d.becomes_inactive.length === 0 && d.becomes_add_on.length === 0 ? (
        <EmptyState title="Nothing would switch off" icon={Layers}>Everything you use today stays available at {d.to_band}.</EmptyState>
      ) : (
        <>
          <p>These stop accepting new work from the renewal date:</p>
          <ul className="list-disc pl-5">
            {d.becomes_inactive.map((x) => (
              <li key={x.feature}>{x.name}</li>
            ))}
            {d.becomes_add_on.map((x) => (
              <li key={x.feature}>{x.name} (still available as an add-on)</li>
            ))}
          </ul>
        </>
      )}
      <p className="text-xs text-ink-muted">Kept whatever you choose: {d.preserved.join("; ")}.</p>
    </div>
  );
}

function MetersCard() {
  const usage = useCellQuery([...BILLING_KEY, "usage-summary"], (api) => api.billing.usageSummary());
  return (
    <QueryView query={usage}>
      {(u) => (
        <Card title="Usage this period" description={`Resets ${new Date(u.resets_at).toLocaleDateString()}. ${u.treatment.duplicate} ${u.treatment.incomplete}`}>
          <DataTable
            caption="Usage meters"
            columns={meterColumns}
            rows={u.meters}
            rowKey={(m) => m.meter}
          />
          <p className="mt-2 text-xs text-ink-muted">{u.treatment.unpriced}</p>
        </Card>
      )}
    </QueryView>
  );
}

const meterColumns: Column<ReservationUsage["meters"][number]>[] = [
  { key: "meter", header: "Meter", pinned: true, render: (m) => <span className="font-mono text-xs">{m.meter}</span>, sortValue: (m) => m.meter },
  { key: "committed", header: "Used", render: (m) => count(m.committed), sortValue: (m) => m.committed },
  { key: "reserved", header: "Reserved", render: (m) => count(m.reserved), sortValue: (m) => m.reserved },
  { key: "note", header: "Allowance", render: (m) => <span className="text-xs text-ink-muted">{meterNote(m)}</span> },
];
