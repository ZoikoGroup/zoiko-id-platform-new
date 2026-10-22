"use client";

import type { IssuedCredential, IssuedCredentialStatus } from "@zoiko/api-client";
import { ActionMenu, Card, CopyId, DataTable, DateTime, EmptyState, ListEmpty, Select, StatusBadge, humanize, type Column } from "@zoiko/ui";
import { Ban, PauseCircle, PlayCircle, Wallet } from "lucide-react";
import { useState } from "react";

import { useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminQueryView, R3ActionDialog } from "../identity-providers/shared";

export const ISSUED_KEY = ["admin", "credentials", "issued"];

const STATUSES: IssuedCredentialStatus[] = ["VALID", "SUSPENDED", "REVOKED", "EXPIRED", "UNKNOWN"];
type Change = "suspend" | "reinstate" | "revoke";

const CHANGE_COPY: Record<Change, { title: string; intent: string; commit: string; reversibility: "reversible" | "irreversible" }> = {
  suspend: {
    title: "Suspend credential",
    intent:
      "The credential's status list entry is set to suspended. Verifiers checking status refuse it until it is reinstated. The holder keeps the credential in their wallet.",
    commit: "Suspend credential",
    reversibility: "reversible",
  },
  reinstate: {
    title: "Reinstate credential",
    intent: "The credential's status returns to valid, and verifiers accept it again.",
    commit: "Reinstate credential",
    reversibility: "reversible",
  },
  revoke: {
    title: "Revoke credential",
    intent: "The credential is permanently revoked in its status list. Verifiers checking status refuse it from now on; it cannot be reinstated.",
    commit: "Revoke credential",
    reversibility: "irreversible",
  },
};

export function IssuedSection() {
  const [status, setStatus] = useState<IssuedCredentialStatus | "ALL">("ALL");
  const issued = useCellQuery([...ISSUED_KEY, status], (api) => api.credentials.list(status === "ALL" ? { limit: 500 } : { status, limit: 500 }));
  const [target, setTarget] = useState<{ change: Change; row: IssuedCredential } | null>(null);

  const change = useCellMutation(
    (api, v: { change: Change; id: string }) =>
      v.change === "suspend" ? api.credentials.suspend(v.id) : v.change === "reinstate" ? api.credentials.reinstate(v.id) : api.credentials.revoke(v.id),
    {
      invalidate: [ISSUED_KEY],
      success: (r) => `Credential is now ${humanize(r.status).toLowerCase()}`,
    },
  );

  const columns: Column<IssuedCredential>[] = [
    {
      key: "credential",
      header: "Credential",
      pinned: true,
      sortValue: (c) => c.credential_type,
      render: (c) => (
        <div className="min-w-0">
          <div className="max-w-64 truncate font-mono text-xs font-medium text-ink" title={c.credential_type}>
            {c.credential_type}
          </div>
          <CopyId value={c.credential_id} label="credential" />
        </div>
      ),
    },
    { key: "format", header: "Format", sortValue: (c) => c.format, render: (c) => <span className="font-mono text-xs">{c.format}</span> },
    { key: "status", header: "Status", sortValue: (c) => c.status, render: (c) => <StatusBadge state={c.status} kind="Credential status" /> },
    {
      key: "subject",
      header: "Subject",
      render: (c) => (c.subject_principal_id ? <CopyId value={c.subject_principal_id} label="subject principal" /> : <span className="text-ink-subtle">—</span>),
    },
    { key: "issued", header: "Issued", sortValue: (c) => c.issued_at, render: (c) => <DateTime value={c.issued_at} /> },
    { key: "expires", header: "Expires", sortValue: (c) => c.expires_at ?? "", render: (c) => <DateTime value={c.expires_at} /> },
    {
      key: "issuer",
      header: "Issuer",
      defaultHidden: true,
      render: (c) => (
        <span className="block max-w-64 truncate font-mono text-xs" title={c.issuer_identifier}>
          {c.issuer_identifier}
        </span>
      ),
    },
    { key: "config", header: "Configuration", defaultHidden: true, render: (c) => <CopyId value={c.configuration_id} label="configuration" /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (c) => {
        const items = [
          ...(c.status === "VALID" ? [{ id: "suspend", label: "Suspend", icon: <PauseCircle aria-hidden className="size-4" /> }] : []),
          ...(c.status === "SUSPENDED" ? [{ id: "reinstate", label: "Reinstate", icon: <PlayCircle aria-hidden className="size-4" /> }] : []),
          ...(c.status === "VALID" || c.status === "SUSPENDED"
            ? [{ id: "revoke", label: "Revoke", icon: <Ban aria-hidden className="size-4" />, danger: true, separatorBefore: true }]
            : []),
        ];
        return items.length ? (
          <ActionMenu label={`Actions for credential ${c.credential_id}`} items={items} onAction={(id) => setTarget({ change: id as Change, row: c })} />
        ) : null;
      },
    },
  ];

  const copy = target ? CHANGE_COPY[target.change] : null;

  return (
    <>
      <Card
        padded={false}
        title="Issued credentials"
        description="Credentials this tenant has issued to wallets. Status changes publish to the status list verifiers check, and are R3."
        actions={
          <Select
            label="Status"
            hideLabel
            className="w-44"
            value={status}
            onChange={(v) => setStatus(v as IssuedCredentialStatus | "ALL")}
            options={[{ id: "ALL", label: "All statuses" }, ...STATUSES.map((s) => ({ id: s, label: humanize(s) }))]}
          />
        }
      >
        <AdminQueryView query={issued}>
          {(data) => (
            <DataTable
              caption="Issued credentials"
              columns={columns}
              rows={data.items}
              rowKey={(c) => c.credential_id}
              empty={
                status === "ALL" ? (
                  <EmptyState icon={Wallet} title="No credentials issued">
                    Credentials appear here once a wallet obtains one from an active configuration.
                  </EmptyState>
                ) : (
                  <ListEmpty reason="filtered" noun="credentials" onClearFilters={() => setStatus("ALL")} />
                )
              }
            />
          )}
        </AdminQueryView>
      </Card>

      {target && copy ? (
        <R3ActionDialog
          isOpen
          onClose={() => setTarget(null)}
          title={copy.title}
          intent={<p>{copy.intent}</p>}
          scope={[
            ["Credential", <CopyId key="c" value={target.row.credential_id} label="credential" />],
            ["Type", <span key="t" className="break-all font-mono text-xs">{target.row.credential_type}</span>],
            ["Current status", <StatusBadge key="s" state={target.row.status} kind="Credential status" />],
            [
              "Subject",
              target.row.subject_principal_id ? <CopyId key="p" value={target.row.subject_principal_id} label="subject principal" /> : "—",
            ],
          ]}
          reversibility={copy.reversibility}
          commitLabel={copy.commit}
          run={async () => {
            const r = await change.mutateAsync({ change: target.change, id: target.row.credential_id });
            return { outcome: r.status, objectId: r.credential_id };
          }}
        />
      ) : null}
    </>
  );
}
