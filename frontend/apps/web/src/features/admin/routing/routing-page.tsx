"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { ClaimsCostSection, CoverageSection, ProvidersSection, ReleasesSection, SimulationSection, TraceSection } from "./routing-sections";

export function RoutingPage() {
  return (
    <>
      <PageHeader
        title="Coverage & routing"
        description="Which countries, documents and methods are really available, which providers may serve them, and why a route was chosen. Only approved, current evidence counts. A chosen provider is never a verified identity."
      />
      <Tabs
        label="Coverage and routing"
        tabs={[
          { id: "coverage", label: "Coverage", content: <CoverageSection /> },
          { id: "providers", label: "Providers & data paths", content: <ProvidersSection /> },
          { id: "simulation", label: "Simulation", content: <SimulationSection /> },
          { id: "trace", label: "Trace & recovery", content: <TraceSection /> },
          { id: "releases", label: "Releases", content: <ReleasesSection /> },
          { id: "claims", label: "Claims & cost", content: <ClaimsCostSection /> },
        ]}
      />
    </>
  );
}
