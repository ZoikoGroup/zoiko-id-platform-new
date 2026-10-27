"use client";

import type { AuthorityGrant, IssuedCredential, Mandate, PrincipalItem } from "@zoiko/api-client";
import { Badge, Card, CopyId, DataTable, EmptyState, StatusBadge, type Column } from "@zoiko/ui";
import { KeyRound, ScrollText } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/cell";

function scope(list: string[] | null): string {
  if (list === null) return "any";
  if (list.length === 0) return "none";
  return list.length <= 2 ? list.join(", ") : `${list.slice(0, 2).join(", ")} +${list.length - 2}`;
}

/** What this principal may do and on whose behalf (UX-001 §6.1 Authority tab): grants it holds and
 * mandates it executes. Reading authority here never grants any; edits go through the Authority pages. */
export function AuthorityCard({ principal }: { principal: PrincipalItem }) {
  const id = principal.principal_id;
  const grants = useCellQuery(["admin", "principal", id, "grants"], (api) => api.authority.grants({ grantee_principal_id: id, limit: 50 }));
  const mandates = useCellQuery(["admin", "principal", id, "mandates"], (api) => api.mandates.list({ executor_principal_id: id, limit: 50 }));

  const grantColumns: Column<AuthorityGrant>[] = [
    { key: "status", header: "Status", pinned: true, render: (g) => <StatusBadge state={g.status} kind="Grant status" size="xs" /> },
    { key: "type", header: "Type", render: (g) => g.grant_type.replace(/_/g, " ").toLowerCase() },
    { key: "actions", header: "Actions", render: (g) => <span className="font-mono text-xs">{scope(g.actions)}</span> },
    { key: "resources", header: "Resources", render: (g) => <span className="font-mono text-xs">{scope(g.resources)}</span> },
    {
      key: "wide",
      header: "Scope",
      render: (g) =>
        g.actions === null || g.resources === null ? (
          <Badge tone="warn" size="xs">Wildcard</Badge>
        ) : (
          <Badge size="xs">Explicit</Badge>
        ),
    },
    { key: "behalf", header: "On behalf of", render: (g) => <CopyId value={g.represented_principal_id} label="represented principal" /> },
    { key: "until", header: "Valid until", render: (g) => (g.valid_to ? new Date(g.valid_to).toLocaleDateString() : "No end") },
  ];
  const mandateColumns: Column<Mandate>[] = [
    { key: "status", header: "Status", pinned: true, render: (m) => <StatusBadge state={m.status} kind="Mandate status" size="xs" /> },
    { key: "purpose", header: "Purpose", render: (m) => <span className="font-mono text-xs">{m.purpose_id}</span> },
    { key: "actions", header: "Actions", render: (m) => <span className="font-mono text-xs">{scope(m.allowed_actions)}</span> },
    { key: "limit", header: "Value limit", render: (m) => (m.value_limit ? `${m.value_limit} ${m.currency ?? ""}` : "—") },
    { key: "behalf", header: "On behalf of", render: (m) => <CopyId value={m.represented_principal_id} label="represented principal" /> },
  ];

  return (
    <Card
      padded={false}
      title="Authority"
      description="Grants held and mandates executed by this principal, and on whose behalf. Having a grant is not the same as being allowed: every action is still decided against current state."
    >
      <div className="space-y-4 p-4">
        <QueryView query={grants}>
          {(d) => (
            <DataTable
              caption="Authority grants held"
              columns={grantColumns}
              rows={d.items}
              rowKey={(g) => g.authority_grant_id}
              empty={<EmptyState icon={KeyRound} title="No grants">This principal holds no authority grants.</EmptyState>}
            />
          )}
        </QueryView>
        <QueryView query={mandates}>
          {(d) => (
            <DataTable
              caption="Mandates executed"
              columns={mandateColumns}
              rows={d.items}
              rowKey={(m) => m.mandate_id}
              empty={<EmptyState icon={ScrollText} title="No mandates">This principal executes no mandates.</EmptyState>}
            />
          )}
        </QueryView>
      </div>
    </Card>
  );
}

/** Credentials issued to this principal. Cryptographic validity only: whether one authorises an action is
 * decided separately (UX-09). */
export function CredentialsCard({ principal }: { principal: PrincipalItem }) {
  const creds = useCellQuery(["admin", "principal", principal.principal_id, "credentials"], (api) => api.credentials.list({ limit: 200 }));
  const columns: Column<IssuedCredential>[] = [
    { key: "type", header: "Credential", pinned: true, render: (c) => <span className="font-mono text-xs">{c.credential_type}</span> },
    { key: "status", header: "Validity", render: (c) => <StatusBadge state={c.status} kind="Credential cryptographic status" size="xs" /> },
    { key: "issuer", header: "Issuer", render: (c) => <span className="font-mono text-xs">{c.issuer_identifier}</span> },
    { key: "exp", header: "Expires", render: (c) => (c.expires_at ? new Date(c.expires_at).toLocaleDateString() : "No expiry") },
  ];
  return (
    <Card
      padded={false}
      title="Credentials"
      description="Credentials issued to this principal. A valid credential says nothing about authority: that is decided by grants, mandates and policy."
    >
      <QueryView query={creds}>
        {(d) => (
          <DataTable
            caption="Credentials issued to this principal"
            columns={columns}
            rows={d.items.filter((c) => c.subject_principal_id === principal.principal_id)}
            rowKey={(c) => c.credential_id}
            empty={<EmptyState icon={KeyRound} title="No credentials">Nothing has been issued to this principal.</EmptyState>}
          />
        )}
      </QueryView>
    </Card>
  );
}
