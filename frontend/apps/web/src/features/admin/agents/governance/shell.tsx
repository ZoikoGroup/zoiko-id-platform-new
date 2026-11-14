"use client";

import { Badge, KeyValues, PageHeader, StatusBadge } from "@zoiko/ui";
import { Bot } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { useCellQuery } from "@/lib/cell";

export type GovernanceView = "overview" | "approvals" | "review" | "permissions" | "limits" | "monitoring" | "daara";

const FLEET: { id: GovernanceView; label: string; href: string }[] = [
  { id: "overview", label: "Overview", href: "/admin/agents/governance" },
  { id: "approvals", label: "Approvals", href: "/admin/agents/governance/approvals" },
  { id: "review", label: "Review queue", href: "/admin/agents/review" },
];
const PER_AGENT: { id: GovernanceView; label: string; path: string }[] = [
  { id: "permissions", label: "Permissions", path: "permissions" },
  { id: "limits", label: "Usage & spending", path: "limits" },
  { id: "monitoring", label: "Monitoring", path: "monitoring" },
  { id: "daara", label: "Access readiness agent", path: "daara" },
];

/** One adaptive workspace of five pages (section 12): the same navigation everywhere, the current page marked for assistive technology. */
export function GovernanceNav({ current, agentId }: { current: GovernanceView; agentId?: string }) {
  const items = [
    ...FLEET.map((f) => ({ ...f })),
    ...(agentId ? PER_AGENT.map((p) => ({ id: p.id, label: p.label, href: `/admin/agents/governance/${agentId}/${p.path}` })) : []),
  ];
  return (
    <nav aria-label="Agent governance" className="mb-5">
      <ul className="flex flex-wrap gap-1 border-b border-border">
        {items.map((i) => (
          <li key={i.id}>
            <Link
              href={i.href}
              aria-current={i.id === current ? "page" : undefined}
              className={`-mb-px inline-flex min-h-11 items-center border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
                i.id === current ? "border-brand-600 text-brand-700" : "border-transparent text-ink-muted hover:text-ink"
              }`}
            >
              {i.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** The agent this page is about: name, state, sponsor and region come from the server, never from the page. */
export function AgentGovernanceHeader({ agentId, title, description, actions, view }: { agentId: string; title: string; description: ReactNode; actions?: ReactNode; view: GovernanceView }) {
  const agent = useCellQuery(["admin", "agents", agentId], (a) => a.agents.get(agentId));
  const a = agent.data;
  return (
    <>
      <PageHeader
        icon={Bot}
        title={a ? `${a.agent_name}: ${title}` : title}
        description={description}
        actions={actions}
        breadcrumbs={[
          { label: "Agents & tools", href: "/admin/agents" },
          { label: "Governance", href: "/admin/agents/governance" },
          { label: a?.agent_name ?? "Agent" },
        ]}
        meta={
          a ? (
            <span className="flex flex-wrap items-center gap-2">
              <StatusBadge state={a.status} kind="Agent status" />
              <Badge size="xs">{a.blueprint_id}@{a.blueprint_version}</Badge>
            </span>
          ) : undefined
        }
      />
      {a ? (
        <div className="mb-4">
          <KeyValues
            columns={4}
            items={[
              ["Agent ID", <span key="id" className="font-mono text-xs">{a.agent_principal_id}</span>],
              ["Sponsor", a.sponsor_principal_id ? <span key="s" className="font-mono text-xs">{a.sponsor_principal_id}</span> : "None: it cannot run"],
              ["Tools it may ever use", a.allowed_tools.length ? a.allowed_tools.join(", ") : "None"],
              ["Security revision", String(a.security_revision)],
            ]}
          />
        </div>
      ) : null}
      <GovernanceNav current={view} agentId={agentId} />
    </>
  );
}
