"use client";

import type { EntitlementView } from "@zoiko/api-client";
import { Badge, Card, DataTable, EmptyState, KeyValues, type Column } from "@zoiko/ui";
import { Layers } from "lucide-react";

import { useTenant } from "@/features/onboarding/shared";
import { QueryView, useCellQuery } from "@/lib/cell";

import { OrdersCard, PlansCard } from "./orders-plans";
import { BILLING_KEY, count } from "./shared";

export function SubscriptionSection() {
  const tenant = useTenant();
  const id = tenant.data?.tenant_id;
  const view = useCellQuery([...BILLING_KEY, "entitlements", id], (api) => api.billing.entitlements(id as string), { enabled: Boolean(id) });
  return (
    <QueryView query={tenant}>
      {(t) =>
        t === null ? (
          <EmptyState title="No company workspace" icon={Layers}>Subscriptions belong to a company workspace.</EmptyState>
        ) : (
          <QueryView query={view}>{(e) => <Body e={e} />}</QueryView>
        )
      }
    </QueryView>
  );
}

type Cap = EntitlementView["capabilities"][number];

function Body({ e }: { e: EntitlementView }) {
  const columns: Column<Cap>[] = [
    { key: "cap", header: "Capability", pinned: true, render: (c) => <span className="font-mono text-xs">{c.capability}</span>, sortValue: (c) => c.capability },
    {
      key: "mode",
      header: "Included as",
      render: (c) => (
        <span className="inline-flex items-center gap-2">
          {c.mode}
          {c.tier ? <Badge size="xs">{c.tier}</Badge> : null}
          {c.granted_by_exception ? (
            <Badge size="xs" tone="info">
              Granted by exception
            </Badge>
          ) : null}
        </span>
      ),
    },
    {
      key: "released",
      header: "Availability",
      render: (c) => (c.released ? <Badge tone="ok">Available</Badge> : <Badge tone="neutral">Not released</Badge>),
    },
    { key: "limit", header: "Limit", render: (c) => (c.limit === null ? "—" : count(c.limit)), sortValue: (c) => c.limit ?? -1 },
    { key: "used", header: "In use", render: (c) => (c.used === null ? "—" : count(c.used)), sortValue: (c) => c.used ?? -1 },
  ];
  const l = e.limits;
  return (
    <div className="space-y-4">
      <Card title={e.plan_label} description={`Plan ${e.plan}, ${e.source === "DEFAULT" ? "the default plan (no signed order)" : "from a signed order"}.`}>
        <KeyValues
          columns={4}
          items={[
            ["State", e.state],
            ["Effective from", e.effective_from ? new Date(e.effective_from).toLocaleDateString("en-GB") : "—"],
            ["Support", l.support_level],
            ["Production environments", count(l.production_environments)],
            ["People included", count(l.mah_included)],
            ["Sign-ins included", count(l.auth_events_included)],
            ["Access decisions included", count(l.policy_decisions_included)],
            ["Machine grants included", count(l.m2m_grants_included)],
            ["Enterprise SSO connections", count(l.sso_included)],
          ]}
        />
        <p className="mt-3 text-xs text-ink-muted">{e.note}</p>
      </Card>
      <Card title="Capabilities" padded={false}>
        <DataTable<Cap>
          caption="Capabilities on your plan"
          columns={columns}
          rows={e.capabilities}
          rowKey={(c) => c.capability}
          empty={<EmptyState title="No capabilities listed" icon={Layers} />}
        />
      </Card>
      <OrdersCard />
      <PlansCard current={e.plan} />
    </div>
  );
}
