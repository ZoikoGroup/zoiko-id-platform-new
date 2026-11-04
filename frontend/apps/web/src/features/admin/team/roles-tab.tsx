"use client";

import type { BillingBundle, RoleMatrix, RoleMode } from "@zoiko/api-client";
import { Badge, Banner, Card, DataTable, EmptyState, type Column } from "@zoiko/ui";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { BUNDLE_COPY, MODE_LABEL, MODE_SHORT, NEVER_CONFERRED, actionsFor, myCounts, roleCopy, summarise, type RoleSummary } from "./roles-logic";

const MODE_TONE: Record<RoleMode, "ok" | "info" | "neutral" | "warn"> = { X: "ok", A: "warn", S: "info", R: "neutral", T: "neutral", "-": "neutral" };

/** C00 Roles and permissions: every role with what the published policy lets it do, and the billing bundles an Owner can give. Read from the
 * server, so nothing is claimed here that the policy does not say. It is a catalogue for navigation; the server decides each request. */
export function RolesTab() {
  const matrix = useCellQuery(["tenant", "role-matrix"], (api) => api.tenant.roleMatrix());
  const mine = useCellQuery(["tenant", "my-permissions"], (api) => api.tenant.myPermissions());
  const access = useCellQuery(["billing", "access"], (api) => api.billing.access());
  return (
    <div className="space-y-4">
      <Banner tone="info" title="What this shows">
        The published role policy. It tells you what a role is meant to be able to do; each request is still decided by the server at the moment it is made, with the person&apos;s
        live roles, approvals and step-up. {NEVER_CONFERRED}
      </Banner>
      {mine.data ? <YourAccess roles={mine.data.roles} counts={myCounts(mine.data)} /> : null}
      <QueryView query={matrix}>{(m) => <Gallery m={m} />}</QueryView>
      {access.data ? <Bundles bundles={access.data.bundles} labels={new Map(access.data.catalogue.map((c) => [c.key, c.label]))} /> : null}
    </div>
  );
}

function YourAccess({ roles, counts }: { roles: string[]; counts: Record<RoleMode, number> }) {
  return (
    <Card title="Your access" description="From your live roles in this organization.">
      <p className="text-sm">
        {roles.length ? roles.map((r) => roleCopy(r).label).join(", ") : "You hold no active role here."}
      </p>
      <p className="mt-2 text-sm text-ink-muted">
        Policy lets you do {counts.X}, approve {counts.A}, prepare {counts.S}, and read {counts.R} things, if every other condition holds.
      </p>
    </Card>
  );
}

function Gallery({ m }: { m: RoleMatrix }) {
  const rows = summarise(m);
  const [role, setRole] = useState<string | null>(null);
  const columns: Column<RoleSummary>[] = [
    { key: "role", header: "Role", pinned: true, render: (r) => <span className="font-medium">{roleCopy(r.role).label}</span>, sortValue: (r) => r.role },
    { key: "help", header: "In short", render: (r) => <span className="text-sm text-ink-muted">{roleCopy(r.role).help}</span> },
    { key: "x", header: "Do", render: (r) => r.counts.X, sortValue: (r) => r.counts.X },
    { key: "a", header: "Approve", render: (r) => r.counts.A, sortValue: (r) => r.counts.A },
    { key: "s", header: "Prepare", render: (r) => r.counts.S, sortValue: (r) => r.counts.S },
    { key: "r", header: "Read", render: (r) => r.counts.R, sortValue: (r) => r.counts.R },
  ];
  return (
    <>
      <Card
        title="Roles"
        description={`Policy version ${m.version}. Select a role to see exactly what it can do. Counts are of the policy's own actions.`}
        padded={false}
      >
        <DataTable<RoleSummary>
          caption="Roles and what each can do"
          columns={columns}
          rows={rows}
          rowKey={(r) => r.role}
          onRowAction={(r) => setRole(r.role)}
          empty={<EmptyState title="No roles published" icon={ShieldCheck}>The role policy has no roles.</EmptyState>}
        />
      </Card>
      {role ? <RoleDetail m={m} role={role} /> : null}
    </>
  );
}

function RoleDetail({ m, role }: { m: RoleMatrix; role: string }) {
  const groups = actionsFor(m, role);
  return (
    <Card title={`${roleCopy(role).label}: what the policy allows`} description={roleCopy(role).help}>
      {groups.length === 0 ? (
        <p className="text-sm text-ink-muted">This role has no rights in the published policy.</p>
      ) : (
        <div className="space-y-5">
          {groups.map((g) => (
            <section key={g.matrix} aria-label={g.title}>
              <h3 className="mb-2 text-sm font-semibold text-ink">{g.title}</h3>
              <ul className="divide-y divide-border rounded-md border border-border">
                {g.rows.map((r) => (
                  <li key={r.action} className="flex flex-wrap items-start gap-3 px-3 py-2 text-sm">
                    <Badge tone={MODE_TONE[r.mode]}>
                      <span title={MODE_LABEL[r.mode]}>{MODE_SHORT[r.mode]}</span>
                    </Badge>
                    <span className="min-w-48 flex-1">
                      <span className="font-medium">{r.feature}</span>
                      {r.rule ? <span className="block text-xs text-ink-subtle">{r.rule}</span> : null}
                    </span>
                    <span className="sr-only">{MODE_LABEL[r.mode]}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </Card>
  );
}

function Bundles({ bundles, labels }: { bundles: Record<BillingBundle, string[]>; labels: Map<string, string> }) {
  return (
    <Card title="Billing access bundles" description="Billing is not part of any role. An Owner gives one of these to a person, with a reason and an optional end date, and can take it away at once.">
      <div className="grid gap-3 md:grid-cols-3">
        {(Object.keys(BUNDLE_COPY) as BillingBundle[]).map((b) => (
          <section key={b} aria-label={BUNDLE_COPY[b].label} className="rounded-md border border-border p-3">
            <h3 className="text-sm font-semibold text-ink">{BUNDLE_COPY[b].label}</h3>
            <p className="mt-1 text-xs text-ink-muted">{BUNDLE_COPY[b].help}</p>
            <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs">
              {(bundles[b] ?? []).map((k) => (
                <li key={k}>{labels.get(k) ?? k}</li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Card>
  );
}
