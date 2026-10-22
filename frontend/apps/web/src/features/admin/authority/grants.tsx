"use client";

import type { AuthorityGrant, GrantStatus } from "@zoiko/api-client";
import {
  Badge,
  Button,
  Card,
  CopyId,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  FilterBar,
  ListEmpty,
  StatusBadge,
  humanize,
  type Column,
} from "@zoiko/ui";
import { Gavel, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { GrantDetail } from "./grant-detail";
import { IssueGrantDialog } from "./issue-grant";
import { ScopeList, UUID_RE } from "./shared";

export const GRANTS_KEY = ["authority", "grants"];

const STATUSES: GrantStatus[] = [
  "ACTIVE",
  "PENDING_APPROVAL",
  "REVALIDATION_PENDING",
  "SUSPENDED",
  "DRAFT",
  "REVOKED",
  "EXPIRED",
  "SUPERSEDED",
];

export function isRevocable(g: AuthorityGrant) {
  return !["REVOKED", "EXPIRED", "SUPERSEDED"].includes(g.status);
}

export function GrantsPanel() {
  const [filters, setFilters] = useState<Record<string, string>>({ status: "ACTIVE" });
  const [grantee, setGrantee] = useState("");
  const granteeId = UUID_RE.test(grantee.trim()) ? grantee.trim() : undefined;
  const q = { status: (filters.status || undefined) as GrantStatus | undefined, grantee_principal_id: granteeId };
  const grants = useCellQuery([...GRANTS_KEY, q], (api) => api.authority.grants(q));

  const [selected, setSelected] = useState<AuthorityGrant | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<AuthorityGrant | null>(null);
  const [issueOpen, setIssueOpen] = useState(false);

  const revoke = useCellMutation((api, id: string) => api.authority.revokeGrant(id), {
    invalidate: [GRANTS_KEY],
    success: "Grant revoked",
    onSuccess: (r) => {
      setRevokeTarget(null);
      setSelected((s) => (s && s.authority_grant_id === r.authority_grant_id ? { ...s, status: r.status } : s));
    },
  });

  const closeRevoke = () => {
    setRevokeTarget(null);
    revoke.reset();
  };

  const columns: Column<AuthorityGrant>[] = [
    { key: "id", header: "Grant", pinned: true, render: (g) => <CopyId value={g.authority_grant_id} label="grant" /> },
    { key: "status", header: "Status", render: (g) => <StatusBadge state={g.status} kind="Grant" />, sortValue: (g) => g.status },
    { key: "type", header: "Type", render: (g) => <span className="text-sm">{humanize(g.grant_type)}</span>, sortValue: (g) => g.grant_type },
    { key: "grantee", header: "Grantee", render: (g) => <CopyId value={g.grantee_principal_id} label="grantee" /> },
    { key: "represented", header: "On behalf of", render: (g) => <CopyId value={g.represented_principal_id} label="represented principal" />, defaultHidden: true },
    { key: "grantor", header: "Grantor", render: (g) => <CopyId value={g.grantor_principal_id} label="grantor" />, defaultHidden: true },
    { key: "actions", header: "Actions", render: (g) => <ScopeList values={g.actions?.slice(0, 3)} any="Any action" /> },
    {
      key: "value",
      header: "Value limit",
      render: (g) => (g.value_limit ? <span className="tabular font-mono text-xs">{`${g.value_limit.amount} ${g.value_limit.currency}`}</span> : <span className="text-ink-subtle">—</span>),
    },
    {
      key: "delegation",
      header: "Delegation",
      render: (g) => (g.delegation.allowed ? <Badge tone="info">{`Depth ≤ ${g.delegation.max_depth}`}</Badge> : <span className="text-ink-subtle">No</span>),
      defaultHidden: true,
    },
    { key: "until", header: "Valid until", render: (g) => <DateTime value={g.valid_to} />, sortValue: (g) => g.valid_to ?? "" },
    {
      key: "row-actions",
      header: "Row actions",
      align: "end",
      render: (g) => (
        <div className="flex justify-end gap-1.5">
          <Button size="xs" variant="secondary" onPress={() => setSelected(g)}>
            Details
          </Button>
          {isRevocable(g) ? (
            <Button size="xs" variant="danger-outline" onPress={() => setRevokeTarget(g)}>
              Revoke
            </Button>
          ) : null}
        </div>
      ),
    },
  ];

  const filtered = Boolean(filters.status || granteeId);

  return (
    <>
      <Card
        title="Authority grants"
        description="Who may act, for whom, on what, and until when. Each grant is a link in an authority chain."
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setIssueOpen(true)}>
            Issue grant
          </Button>
        }
        padded={false}
      >
        <div className="border-b border-border px-5 py-3">
          <FilterBar
            filters={[{ id: "status", label: "Status", options: STATUSES.map((s) => ({ id: s, label: humanize(s) })) }]}
            values={filters}
            onChange={setFilters}
            search={grantee}
            onSearch={setGrantee}
            searchLabel="Grantee principal ID"
          />
          {grantee.trim() && !granteeId ? (
            <p className="mt-1.5 text-xs text-ink-subtle">Enter a full principal UUID to filter by grantee.</p>
          ) : null}
        </div>
        <QueryView query={grants}>
          {(data) => (
            <DataTable
              caption="Authority grants"
              columns={columns}
              rows={data.items}
              rowKey={(g) => g.authority_grant_id}
              onRowAction={setSelected}
              selectedKey={selected?.authority_grant_id ?? null}
              empty={
                filtered ? (
                  <ListEmpty
                    reason="filtered"
                    noun="grants"
                    onClearFilters={() => {
                      setFilters({});
                      setGrantee("");
                    }}
                  />
                ) : (
                  <EmptyState icon={Gavel} title="No authority grants yet">
                    Issue a grant to give a principal authority to act on behalf of another.
                  </EmptyState>
                )
              }
            />
          )}
        </QueryView>
      </Card>

      {selected ? (
        <GrantDetail grant={selected} onClose={() => setSelected(null)} onRevoke={isRevocable(selected) ? () => setRevokeTarget(selected) : undefined} />
      ) : null}

      {issueOpen ? (
        <IssueGrantDialog
          onClose={() => setIssueOpen(false)}
          onIssued={(g) => {
            setIssueOpen(false);
            setSelected(g);
          }}
        />
      ) : null}

      <Dialog
        isOpen={revokeTarget !== null}
        onOpenChange={(o) => {
          if (!o && !revoke.isPending) closeRevoke();
        }}
        title="Revoke this grant?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={revoke.isPending} onPress={closeRevoke}>
              Cancel
            </Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => revokeTarget && revoke.mutate(revokeTarget.authority_grant_id)}>
              Revoke grant
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          {revokeTarget ? (
            <p>
              Grant <CopyId value={revokeTarget.authority_grant_id} label="grant" /> stops conferring authority now. Every grant and mandate derived from it
              loses its authority chain, so delegated agents stop being able to act under it. This cannot be undone; issue a new grant instead.
            </p>
          ) : null}
          {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
