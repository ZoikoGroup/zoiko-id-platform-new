"use client";

import { PageHeader, SectionNav, TextField } from "@zoiko/ui";
import { useState } from "react";

import { defaultWindow, VIEWS, type ViewSlug, type WindowState } from "./logic";
import {
  AccessView,
  ConversionView,
  DecisionsView,
  DefinitionsView,
  DeliveryView,
  EconomicsView,
  FirstAttemptView,
  IncidentsView,
  OverviewView,
  ProvidersView,
  ReportsView,
  RiskView,
} from "./views";

/** The Command Center (ZID-CPS-WF-02): twelve read-only views over governed aggregates. It decides nothing, charges nothing and opens no
 * evidence: cases, billing, webhooks and routing stay on their own pages under their own access. */
export function CommandCenter({ view }: { view: ViewSlug }) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const [w, setW] = useState<WindowState>(() => defaultWindow(new Date(), tz));
  const windowed = !["incidents", "definitions", "access"].includes(view);
  return (
    <div className="space-y-5">
      <PageHeader title="Command Center" description="Verification operations, conversion and delivery for this organization. Figures come from recorded events; nothing here changes a case." />
      <SectionNav label="Command Center views" items={VIEWS.map((v) => ({ label: v.label, href: `/verify-api/command-center/${v.slug}`, current: v.slug === view }))} />
      {windowed ? (
        <form aria-label="Time window" className="grid gap-3 sm:grid-cols-4" onSubmit={(e) => e.preventDefault()}>
          <TextField label="From" type="date" value={w.from} onChange={(v) => setW({ ...w, from: v })} />
          <TextField label="To" type="date" value={w.to} onChange={(v) => setW({ ...w, to: v })} />
          <TextField label="Timezone" value={w.tz} onChange={(v) => setW({ ...w, tz: v })} description="Days start and end in this timezone." />
          <TextField label="Market" value={w.market} onChange={(v) => setW({ ...w, market: v.toUpperCase().slice(0, 2) })} placeholder="All" description="Two-letter country code." />
        </form>
      ) : null}
      {view === "overview" ? <OverviewView w={w} /> : null}
      {view === "conversion" ? <ConversionView w={w} /> : null}
      {view === "first-attempt" ? <FirstAttemptView w={w} /> : null}
      {view === "decisions" ? <DecisionsView w={w} /> : null}
      {view === "risk" ? <RiskView w={w} /> : null}
      {view === "providers" ? <ProvidersView w={w} /> : null}
      {view === "economics" ? <EconomicsView w={w} /> : null}
      {view === "delivery" ? <DeliveryView w={w} /> : null}
      {view === "incidents" ? <IncidentsView /> : null}
      {view === "reports" ? <ReportsView w={w} /> : null}
      {view === "definitions" ? <DefinitionsView /> : null}
      {view === "access" ? <AccessView /> : null}
    </div>
  );
}
