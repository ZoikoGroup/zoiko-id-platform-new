"use client";

import { Banner, PageHeader, Tabs } from "@zoiko/ui";
import Link from "next/link";

import { AgentsSection } from "./agents";
import { BlueprintsSection } from "./blueprints";
import { ToolsSection } from "./tools";

export function AgentsPage() {
  return (
    <>
      <PageHeader
        title="Agents & tools"
        description="AI agents, the blueprints they are built from, and the tools they may use. An agent is an identity with accountable people behind it, not a user account."
      />
      <Banner tone="info" title="Identity, not authority">
        An active agent can act only through grants and mandates, and a tool that shows up in a server&apos;s tool list is not executable until it is
        active here. Runtime bindings are managed under{" "}
        <Link className="font-medium underline" href="/admin/workloads">
          Workload identity
        </Link>
        .
      </Banner>
      <div className="mt-4">
        <Tabs
          label="Agents and tools"
          tabs={[
            { id: "agents", label: "Agents", content: <AgentsSection /> },
            { id: "blueprints", label: "Blueprints", content: <BlueprintsSection /> },
            { id: "tools", label: "Tools", content: <ToolsSection /> },
          ]}
        />
      </div>
    </>
  );
}
