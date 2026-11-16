"use client";

import type { AgentItem, AgentStatus } from "@zoiko/api-client";
import { Badge, Button, Card, CopyId, DataTable, EmptyState, Select, StatusBadge, type Column } from "@zoiko/ui";
import { Bot, Plus } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { AgentDetailPanel } from "./agent-detail";
import { RegisterApiClientDialog } from "./api-agent";
import { RegisterAgentDialog } from "./register-agent-dialog";
import { AGENTS_KEY } from "./shared";
import { UserText } from "@/lib/user-text";

const STATUS_OPTIONS = [
  { id: "", label: "All statuses" },
  ...(["DRAFT", "PENDING_APPROVAL", "ACTIVE", "SUSPENDED", "REVOKED", "RETIRED"] as AgentStatus[]).map((s) => ({
    id: s,
    label: s.replace("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()),
  })),
];

export function AgentsSection() {
  const [status, setStatus] = useState("");
  const agents = useCellQuery([...AGENTS_KEY, status], (api) => api.agents.list((status || undefined) as AgentStatus | undefined));
  const [creating, setCreating] = useState(false);
  const [apiClient, setApiClient] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const columns: Column<AgentItem>[] = [
    {
      key: "name",
      header: "Agent",
      pinned: true,
      render: (a) => (
        <span className="inline-flex flex-col">
          <span className="font-medium"><UserText>{a.agent_name}</UserText></span>
          <span className="font-mono text-2xs text-ink-subtle">{a.agent_principal_id.slice(0, 8)}…</span>
        </span>
      ),
      sortValue: (a) => a.agent_name,
    },
    { key: "status", header: "Status", render: (a) => <StatusBadge state={a.status} kind="Agent status" />, sortValue: (a) => a.status },
    {
      key: "blueprint",
      header: "Blueprint",
      render: (a) => <span className="font-mono text-xs">{a.blueprint_id}@{a.blueprint_version}</span>,
    },
    { key: "tools", header: "Tools", render: (a) => <span className="tabular">{a.allowed_tools.length}</span>, sortValue: (a) => a.allowed_tools.length },
    {
      key: "kind",
      header: "Kind",
      render: (a) => (a.parent_agent_id ? <Badge size="xs" tone="info">Sub-agent</Badge> : <Badge size="xs">Agent</Badge>),
    },
    { key: "sponsor", header: "Sponsor", render: (a) => (a.sponsor_principal_id ? <CopyId value={a.sponsor_principal_id} label="sponsor" /> : "—"), defaultHidden: true },
    { key: "rev", header: "Security revision", render: (a) => <span className="tabular">{a.security_revision}</span>, defaultHidden: true },
  ];

  return (
    <div className="space-y-4">
      <Card
        padded={false}
        title="Agents"
        description="Each agent has its own identity, credentials, audit trail and kill switch, even when one person sponsors several."
        actions={
          <span className="flex items-end gap-3">
            <Select label="Status" options={STATUS_OPTIONS} value={status} onChange={setStatus} />
            <Button size="sm" variant="secondary" onPress={() => setApiClient(true)}>
              Register API client
            </Button>
            <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
              Register agent
            </Button>
          </span>
        }
      >
        <QueryView query={agents}>
          {(data) => (
            <DataTable
              caption="Agents"
              columns={columns}
              rows={data.items}
              rowKey={(a) => a.agent_principal_id}
              onRowAction={(a) => setSelected(a.agent_principal_id)}
              selectedKey={selected}
              empty={
                <EmptyState
                  icon={Bot}
                  title="No agents registered"
                  action={
                    <Button size="sm" onPress={() => setCreating(true)}>
                      Register agent
                    </Button>
                  }
                >
                  Register an agent after you have a blueprint release and the tools it may use.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>
      {selected ? <AgentDetailPanel agentId={selected} onClose={() => setSelected(null)} /> : null}
      <RegisterApiClientDialog isOpen={apiClient} onClose={() => setApiClient(false)} />
      <RegisterAgentDialog isOpen={creating} onClose={() => setCreating(false)} onCreated={(id) => setSelected(id)} />
    </div>
  );
}
