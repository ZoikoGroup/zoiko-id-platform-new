"use client";

import type { FederationIntegration } from "@zoiko/api-client";
import { ActionMenu, Badge, Button, Card, CopyId, DataTable, DateTime, EmptyState, RevisionChip, StatusBadge, humanize, type Column } from "@zoiko/ui";
import { Ban, KeyRound, Link2, LogIn, Plus, ToggleRight } from "lucide-react";
import { useState } from "react";

import { useCellMutation, useCellQuery } from "@/lib/cell";

import { IdpInitiatedDialog, LoginUrlDialog, RotateDialog } from "./integration-actions";
import { CreateIntegrationDialog, INTEGRATIONS_KEY } from "./integration-form";
import { AdminQueryView, R3ActionDialog } from "./shared";

type Open = { kind: "rotate" | "idp" | "login" | "disable"; row: FederationIntegration } | { kind: "create" } | null;

export function IntegrationsSection() {
  const integrations = useCellQuery(INTEGRATIONS_KEY, (api) => api.federation.integrations());
  const [open, setOpen] = useState<Open>(null);
  const close = () => setOpen(null);

  const disable = useCellMutation((api, id: string) => api.federation.disable(id), {
    invalidate: [INTEGRATIONS_KEY],
    success: (r) => `${r.display_name} quarantined`,
  });

  const columns: Column<FederationIntegration>[] = [
    {
      key: "name",
      header: "Provider",
      pinned: true,
      sortValue: (r) => r.display_name,
      render: (r) => (
        <div className="min-w-0">
          <div className="font-medium text-ink">{r.display_name}</div>
          <CopyId value={r.connection_id} label="connection" />
        </div>
      ),
    },
    { key: "protocol", header: "Protocol", sortValue: (r) => r.protocol, render: (r) => <Badge tone="neutral">{r.protocol}</Badge> },
    {
      key: "issuer",
      header: "Issuer",
      sortValue: (r) => r.issuer,
      render: (r) => (
        <span className="block max-w-64 truncate font-mono text-xs" title={r.issuer}>
          {r.issuer}
        </span>
      ),
    },
    { key: "status", header: "Status", sortValue: (r) => r.status, render: (r) => <StatusBadge state={r.status} kind="Integration" /> },
    { key: "revision", header: "Config", sortValue: (r) => r.config_revision, render: (r) => <RevisionChip kind="rev" value={r.config_revision} /> },
    {
      key: "expires",
      header: "Metadata expires",
      sortValue: (r) => r.metadata_expires_at ?? "",
      render: (r) => <DateTime value={r.metadata_expires_at} />,
    },
    {
      key: "idp",
      header: "IdP-initiated",
      render: (r) =>
        r.protocol !== "SAML" ? (
          <span className="text-ink-subtle">—</span>
        ) : r.allow_idp_initiated ? (
          <Badge tone="warn" label={`IdP-initiated SSO on, ${r.idp_initiated_targets.length} targets`}>
            On · {r.idp_initiated_targets.length}
          </Badge>
        ) : (
          <span className="text-xs text-ink-muted">Off</span>
        ),
    },
    { key: "link", header: "Linking", defaultHidden: true, render: (r) => <span className="text-xs">{humanize(r.link_policy)}</span> },
    {
      key: "encrypted",
      header: "Encrypted assertions",
      defaultHidden: true,
      render: (r) => (r.protocol === "SAML" ? (r.require_encrypted_assertions ? "Required" : "Optional") : "—"),
    },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (r) => (
        <ActionMenu
          label={`Actions for ${r.display_name}`}
          onAction={(id) => setOpen({ kind: id as "rotate" | "idp" | "login" | "disable", row: r })}
          items={[
            { id: "login", label: "Copy sign-in link", icon: <LogIn aria-hidden className="size-4" /> },
            { id: "rotate", label: "Rotate trust material", icon: <KeyRound aria-hidden className="size-4" />, disabled: r.status !== "ACTIVE" },
            ...(r.protocol === "SAML"
              ? [
                  {
                    id: "idp",
                    label: r.allow_idp_initiated ? "Switch off IdP-initiated SSO" : "Switch on IdP-initiated SSO",
                    icon: <ToggleRight aria-hidden className="size-4" />,
                    disabled: r.status !== "ACTIVE",
                  },
                ]
              : []),
            {
              id: "disable",
              label: "Disable",
              icon: <Ban aria-hidden className="size-4" />,
              danger: true,
              disabled: r.status !== "ACTIVE",
              separatorBefore: true,
            },
          ]}
        />
      ),
    },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Integrations"
        description="External OIDC and SAML providers users can sign in with. Every change here is R3."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setOpen({ kind: "create" })}>
            Add provider
          </Button>
        }
      >
        <AdminQueryView query={integrations}>
          {(data) => (
            <DataTable
              caption="Identity provider integrations"
              columns={columns}
              rows={data.items}
              rowKey={(r) => r.connection_id}
              empty={
                <EmptyState icon={Link2} title="No identity providers">
                  Add an OIDC or SAML provider to offer enterprise sign-in.
                </EmptyState>
              }
            />
          )}
        </AdminQueryView>
      </Card>

      {open?.kind === "create" ? <CreateIntegrationDialog onClose={close} /> : null}
      {open?.kind === "rotate" ? <RotateDialog integration={open.row} onClose={close} /> : null}
      {open?.kind === "idp" ? <IdpInitiatedDialog integration={open.row} onClose={close} /> : null}
      {open?.kind === "login" ? <LoginUrlDialog integration={open.row} onClose={close} /> : null}
      {open?.kind === "disable" ? (
        <R3ActionDialog
          isOpen
          onClose={close}
          title={`Disable ${open.row.display_name}`}
          intent={
            <p>
              The integration is quarantined at once: new sign-ins through this provider are refused and pending sign-in attempts fail. Existing
              sessions are not ended. There is no re-enable action, and because an issuer is unique per tenant the same provider cannot simply be
              added again.
            </p>
          }
          scope={[
            ["Provider", open.row.display_name],
            ["Protocol", open.row.protocol],
            ["Issuer", <span key="i" className="break-all font-mono text-xs">{open.row.issuer}</span>],
            ["Connection", <CopyId key="c" value={open.row.connection_id} label="connection" />],
          ]}
          reversibility="irreversible"
          dependents={[{ label: "Users who sign in only through this provider", effect: "Cannot sign in" }]}
          commitLabel="Disable integration"
          run={async () => {
            const r = await disable.mutateAsync(open.row.connection_id);
            return { outcome: r.status, objectId: r.connection_id, revision: r.config_revision };
          }}
        />
      ) : null}
    </>
  );
}
