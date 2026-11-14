"use client";

import { Banner, Button, Card, PageHeader, Tabs } from "@zoiko/ui";
import { ArrowRight, Check } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useCellQuery } from "@/lib/cell";

import { AgentsSection } from "./agents";
import { setupSteps, type SetupStep } from "./agents-logic";
import { BlueprintsSection } from "./blueprints";
import { BuildsSection } from "./builds";
import { AuthorityNotice } from "./pickers";
import { AGENTS_KEY, BLUEPRINTS_KEY, BUILDS_KEY, TOOLS_KEY } from "./shared";
import { ToolsSection } from "./tools";

type Tab = "agents" | "blueprints" | "builds" | "tools";

/** The order to set an agent up in, with where each step stands. The first one not done is marked as the next thing to do. */
function SetupGuide({ onGo }: { onGo: (tab: Tab) => void }) {
  const tools = useCellQuery(TOOLS_KEY, (a) => a.agents.tools());
  const blueprints = useCellQuery(BLUEPRINTS_KEY, (a) => a.agents.blueprints());
  const agents = useCellQuery([...AGENTS_KEY, ""], (a) => a.agents.list(undefined));
  const bindings = useCellQuery(["admin", "workloads"], (a) => a.workloads.bindings());
  const builds = useCellQuery(BUILDS_KEY, (a) => a.agents.builds());
  if (!tools.data && !blueprints.data && !agents.data) return null;
  const steps = setupSteps({
    tools: tools.data?.items ?? [],
    blueprints: blueprints.data?.items ?? [],
    agents: agents.data?.items ?? [],
    bindings: bindings.data?.items ?? [],
    builds: builds.data?.items ?? [],
    buildsEnforced: builds.data?.enforced ?? false,
  });
  const next = steps.find((s) => !s.done)?.id;

  const go = (s: SetupStep) => {
    const tab = s.tab;
    if (tab === "workloads") {
      return (
        <Link className="inline-flex min-h-8 items-center gap-1 text-sm font-medium text-brand-700 underline" href="/admin/workloads">
          Open <ArrowRight aria-hidden className="size-3.5" />
        </Link>
      );
    }
    return (
      <Button size="xs" variant={s.id === next ? "primary" : "secondary"} onPress={() => onGo(tab)}>
        {s.done ? "Open" : "Do this"}
      </Button>
    );
  };

  return (
    <Card title="Set up an agent" description="In this order. Each step opens the screen where you do it.">
      <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {steps.map((s, i) => (
          <li
            key={s.id}
            aria-current={s.id === next ? "step" : undefined}
            className={`flex flex-col gap-2 rounded-card border p-3 ${s.id === next ? "border-brand-500 bg-brand-50/40" : "border-border"}`}
          >
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${s.done ? "bg-ok-bg text-ok-fg" : "bg-sunken text-ink-muted"}`}
              >
                {s.done ? <Check className="size-3.5" /> : i + 1}
              </span>
              <p className="text-sm font-medium text-ink">
                {s.title}
                <span className="sr-only">{s.done ? " (done)" : s.id === next ? " (next)" : " (to do)"}</span>
              </p>
            </div>
            <p className="min-h-10 text-xs text-ink-muted">{s.detail}</p>
            <div>{go(s)}</div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

export function AgentsPage() {
  const [tab, setTab] = useState<Tab>("agents");
  return (
    <>
      <PageHeader
        title="Agents & tools"
        description="AI agents, the blueprints they are built from, and the tools they may use. An agent is an identity with accountable people behind it, not a user account."
      />
      <div className="space-y-4">
        <AuthorityNotice needs={["machine.tool.manage", "machine.build.register", "machine.build.manage", "machine.blueprint.manage", "machine.agent.manage"]} what="register tools, blueprints and agents" />
        <SetupGuide onGo={setTab} />
        <Banner tone="info" title="Access readiness agent">
          A reference agent that checks mandate metadata and proposes reviews for a person. It cannot change access, and it is off unless your environment turns it on.{" "}
          <Link className="font-medium underline" href="/admin/agents/readiness">
            Open access readiness
          </Link>
          .
        </Banner>
        <Banner tone="info" title="Identity, not authority">
          An active agent can act only through grants and mandates, and a tool that shows up in a server&apos;s tool list is not executable until it is
          active here. Runtime bindings are managed under{" "}
          <Link className="font-medium underline" href="/admin/workloads">
            Workload identity
          </Link>
          .
        </Banner>
      </div>
      <div className="mt-4">
        <Tabs
          label="Agents and tools"
          selectedTab={tab}
          onSelectionChange={(id) => setTab(id as Tab)}
          tabs={[
            { id: "agents", label: "Agents", content: <AgentsSection /> },
            { id: "builds", label: "Builds", content: <BuildsSection /> },
            { id: "blueprints", label: "Blueprints", content: <BlueprintsSection /> },
            { id: "tools", label: "Tools", content: <ToolsSection /> },
          ]}
        />
      </div>
    </>
  );
}
