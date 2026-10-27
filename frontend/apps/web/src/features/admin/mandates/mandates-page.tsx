"use client";

import type { Mandate, MandateStatus } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, DataTable, DateTime, EmptyState, FilterBar, ListEmpty, PageHeader, StatusBadge, humanize, type Column } from "@zoiko/ui";
import { Plus, Scale } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { ScopeList, UUID_RE, useMe } from "../authority/shared";
import { CreateMandateDialog } from "./create-mandate";
import { MandateDetail } from "./mandate-detail";
import { MANDATES_KEY, StatusChangeDialog, allowedMoves, budgetSummary, type Move } from "./mandate-status";

const STATUSES: MandateStatus[] = ["PENDING_APPROVAL", "ACTIVE", "PAUSED", "EXHAUSTED", "COMPLETED", "EXPIRED", "REVOKED"];

export function MandatesPage() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [executor, setExecutor] = useState("");
  const executorId = UUID_RE.test(executor.trim()) ? executor.trim() : undefined;
  const q = { status: (filters.status || undefined) as MandateStatus | undefined, executor_principal_id: executorId };
  const mandates = useCellQuery([...MANDATES_KEY, "list", q], (api) => api.mandates.list(q));

  // Creating a mandate hands out company money, so it needs a grant of its own (`mandate.create`) that administration
  // does not include. Offer the button only to someone who holds one; the cell refuses everyone else.
  const me = useMe();
  const myGrants = useCellQuery(["authority", "grants", "can-create-mandate", me], (api) =>
    api.authority.grants({ status: "ACTIVE", grantee_principal_id: me, limit: 200 }),
  );
  // Shown unless we positively know this person holds no such grant: a slow or failed lookup must never hide it.
  const known = Boolean(me) && myGrants.isSuccess;
  const cannotCreate = known && !(myGrants.data?.items ?? []).some((g) => g.actions === null || g.actions.includes("mandate.create"));
  const canCreate = !cannotCreate;

  const [picked, setPicked] = useState<Mandate | null>(null);
  const [creating, setCreating] = useState(false);
  const [move, setMove] = useState<{ mandate: Mandate; move: Move } | null>(null);

  const rows = mandates.data?.items ?? [];
  // Prefer the freshest list row; fall back to the picked object (e.g. just created, or filtered out).
  const selected = rows.find((m) => m.mandate_id === picked?.mandate_id) ?? picked;

  const columns: Column<Mandate>[] = [
    { key: "id", header: "Mandate", pinned: true, render: (m) => <span className="font-mono text-xs" title={m.mandate_id}>{m.mandate_id.slice(0, 8)}…{m.mandate_id.slice(-4)}</span> }, // plain text: the first column is already a row button, and a copy button inside it is invalid HTML,
    { key: "status", header: "Status", render: (m) => <StatusBadge state={m.status} kind="Mandate" />, sortValue: (m) => m.status },
    { key: "executor", header: "Executor", render: (m) => <CopyId value={m.executor_principal_id} label="executor" /> },
    { key: "represented", header: "On behalf of", render: (m) => <CopyId value={m.represented_principal_id} label="represented principal" />, defaultHidden: true },
    { key: "grant", header: "Source grant", render: (m) => <CopyId value={m.authority_grant_id} label="source grant" />, defaultHidden: true },
    { key: "actions", header: "Actions", render: (m) => <ScopeList values={m.allowed_actions.slice(0, 3)} /> },
    { key: "budget", header: "Budget", render: (m) => <span className="tabular font-mono text-xs">{budgetSummary(m)}</span> },
    { key: "until", header: "Valid until", render: (m) => <DateTime value={m.valid_to} />, sortValue: (m) => m.valid_to },
    {
      key: "row-actions",
      header: "Row actions",
      align: "end",
      render: (m) => (
        <div className="flex justify-end gap-1.5">
          <Button size="xs" variant="secondary" onPress={() => setPicked(m)}>
            Details
          </Button>
          {allowedMoves(m.status).map((mv) => (
            <Button key={mv} size="xs" variant={mv === "revoke" ? "danger-outline" : "secondary"} onPress={() => setMove({ mandate: m, move: mv })}>
              {humanize(mv)}
            </Button>
          ))}
        </div>
      ),
    },
  ];

  const filtered = Boolean(filters.status || executorId);

  return (
    <>
      <PageHeader
        icon={Scale}
        title="Mandates"
        description="Bounded, budgeted authority handed from a grant to an executor such as an AI agent or service. Owners and administrators can switch on, pause or revoke any of them; the person who runs or created one can pause it, and its creator can revoke it."
        actions={
          canCreate ? (
            <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
              Create mandate
            </Button>
          ) : undefined
        }
      />
      {cannotCreate ? (
        <Banner tone="info" title="You can review and switch mandates on or off, but not create them">
          Creating a mandate needs a spending grant that includes <strong>Give someone a spending limit</strong>. Ask the owner to issue you one under
          Authority, or ask the person who holds one to create it. You can then activate, pause or revoke it here.
        </Banner>
      ) : null}
      <Card padded={false}>
        <div className="border-b border-border px-5 py-3">
          <FilterBar
            filters={[{ id: "status", label: "Status", options: STATUSES.map((s) => ({ id: s, label: humanize(s) })) }]}
            values={filters}
            onChange={setFilters}
            search={executor}
            onSearch={setExecutor}
            searchLabel="Executor principal ID"
          />
          {executor.trim() && !executorId ? <p className="mt-1.5 text-xs text-ink-subtle">Enter a full principal UUID to filter by executor.</p> : null}
        </div>
        <QueryView query={mandates}>
          {(data) => (
            <DataTable
              caption="Mandates"
              columns={columns}
              rows={data.items}
              rowKey={(m) => m.mandate_id}
              onRowAction={setPicked}
              selectedKey={picked?.mandate_id ?? null}
              empty={
                filtered ? (
                  <ListEmpty
                    reason="filtered"
                    noun="mandates"
                    onClearFilters={() => {
                      setFilters({});
                      setExecutor("");
                    }}
                  />
                ) : (
                  <EmptyState icon={Scale} title="No mandates yet">
                    {canCreate
                      ? "Press Create mandate to hand part of your spending grant to an agent, a service or a colleague, with a budget and an end date."
                      : "Nobody has created a mandate yet. Once someone with a spending grant creates one, it appears here for you to switch on."}
                  </EmptyState>
                )
              }
            />
          )}
        </QueryView>
      </Card>

      {selected ? (
        <MandateDetail mandate={selected} onClose={() => setPicked(null)} onMove={(mv) => setMove({ mandate: selected, move: mv })} />
      ) : null}
      {creating ? (
        <CreateMandateDialog
          onClose={() => setCreating(false)}
          onCreated={(m) => {
            setCreating(false);
            setPicked(m);
          }}
        />
      ) : null}
      {move ? <StatusChangeDialog mandate={move.mandate} move={move.move} onClose={() => setMove(null)} /> : null}
    </>
  );
}
