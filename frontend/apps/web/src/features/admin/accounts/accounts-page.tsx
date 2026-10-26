"use client";

import type { PrincipalItem, PrincipalStatus, PrincipalType } from "@zoiko/api-client";
import { Card, CopyId, DataTable, DateTime, FilterBar, ListEmpty, PageHeader, StatusBadge, humanize, type Column, type FilterDef } from "@zoiko/ui";
import { useMemo, useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { PrincipalDetail } from "./principal-detail";

const LIMIT = 500;

const TYPES: PrincipalType[] = ["HUMAN", "ORGANIZATION", "ORG_UNIT", "DEVICE", "APPLICATION", "SERVICE", "WORKLOAD", "AI_AGENT"];
const STATUSES: PrincipalStatus[] = ["PENDING", "ACTIVE", "SUSPENDED", "DISABLED", "SUPERSEDED", "ERASED"];

const FILTERS: FilterDef[] = [
  { id: "type", label: "Type", options: TYPES.map((t) => ({ id: t, label: humanize(t) })) },
  { id: "status", label: "Status", options: STATUSES.map((s) => ({ id: s, label: humanize(s) })) },
];

const columns: Column<PrincipalItem>[] = [
  {
    key: "principal",
    header: "Principal",
    pinned: true,
    render: (p) => <span className="font-mono text-xs">{p.principal_id}</span>,
    sortValue: (p) => p.principal_id,
  },
  { key: "type", header: "Type", render: (p) => humanize(p.principal_type), sortValue: (p) => p.principal_type },
  { key: "status", header: "Status", render: (p) => <StatusBadge state={p.status} kind="Principal" />, sortValue: (p) => p.status },
  { key: "revision", header: "Security revision", render: (p) => <span className="tabular">{p.security_revision}</span>, sortValue: (p) => p.security_revision },
  { key: "cell", header: "Home cell", render: (p) => <span className="font-mono text-xs">{p.home_cell}</span>, defaultHidden: true },
  { key: "created", header: "Created", render: (p) => <DateTime value={p.created_at} />, sortValue: (p) => p.created_at },
  { key: "copy", header: "Copy ID", render: (p) => <CopyId value={p.principal_id} label="principal ID" />, defaultHidden: true },
];

export function AccountsPage() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<PrincipalItem | null>(null);

  const type = (filters.type || undefined) as PrincipalType | undefined;
  const status = (filters.status || undefined) as PrincipalStatus | undefined;
  const principals = useCellQuery(["admin", "principals", type ?? "", status ?? ""], (api) =>
    api.principals.list({ principal_type: type, status, limit: LIMIT }),
  );

  const term = search.trim().toLowerCase();
  const rows = useMemo(
    () => (principals.data?.items ?? []).filter((p) => !term || p.principal_id.toLowerCase().includes(term)),
    [principals.data, term],
  );
  const filtered = Boolean(type || status || term);

  return (
    <>
      <PageHeader
        title="People & principals"
        description="Find a principal to see the authority it holds, its credentials and identity assurance, end its sessions, or run assisted or lockbox recovery."
      />
      <div className="space-y-5">
        <Card padded={false}>
          <div className="border-b border-border p-4">
            <FilterBar
              search={search}
              onSearch={setSearch}
              searchLabel="Filter by principal ID"
              filters={FILTERS}
              values={filters}
              onChange={setFilters}
            />
          </div>
          <QueryView query={principals}>
            {(data) => (
              <DataTable
                caption="Principals"
                columns={columns}
                rows={rows}
                rowKey={(p) => p.principal_id}
                onRowAction={setSelected}
                selectedKey={selected?.principal_id ?? null}
                dense
                footer={
                  data.items.length >= LIMIT ? (
                    <p className="text-xs text-ink-subtle">Showing the first {LIMIT} principals. Narrow the filters to find others.</p>
                  ) : undefined
                }
                empty={
                  <ListEmpty
                    reason={filtered ? "filtered" : "none"}
                    noun="principals"
                    onClearFilters={() => {
                      setFilters({});
                      setSearch("");
                    }}
                  />
                }
              />
            )}
          </QueryView>
        </Card>

        {selected ? (
          <PrincipalDetail key={selected.principal_id} principal={selected} onClose={() => setSelected(null)} />
        ) : (
          <p className="text-sm text-ink-muted">Select a principal to manage its sessions and recovery.</p>
        )}
      </div>
    </>
  );
}
