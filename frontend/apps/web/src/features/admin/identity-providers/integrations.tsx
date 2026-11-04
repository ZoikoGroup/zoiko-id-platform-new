"use client";

import type { FederationIntegration } from "@zoiko/api-client";
import { ActionMenu, Badge, Button, Card, CopyId, DataTable, DateTime, EmptyState, RevisionChip, StatusBadge, humanize, type Column } from "@zoiko/ui";
import { Ban, KeyRound, Link2, LogIn, Pencil, Plus, Power, ToggleRight } from "lucide-react";
import { useState } from "react";

import { useCellMutation, useCellQuery } from "@/lib/cell";

import { EditIntegrationDialog, IdpInitiatedDialog, LoginUrlDialog, RotateDialog } from "./integration-actions";
import { CreateIntegrationDialog, INTEGRATIONS_KEY } from "./integration-form";
import { AdminQueryView, R3ActionDialog } from "./shared";

type RowAction = "rotate" | "idp" | "login" | "disable" | "enable" | "edit";
type Open = { kind: RowAction; row: FederationIntegration } | { kind: "create" } | null;

export function IntegrationsSection() {
  const integrations = useCellQuery(INTEGRATIONS_KEY, (api) => api.federation.integrations());
  const [open, setOpen] = useState<Open>(null);
  const close = () => setOpen(null);

  const disable = useCellMutation((api, id: string) => api.federation.disable(id), {
    invalidate: [INTEGRATIONS_KEY],
    success: (r) => `${r.display_name} quarantined`,
  });
  const enable = useCellMutation((api, id: string) => api.federation.enable(id), {
    invalidate: [INTEGRATIONS_KEY],
    success: (r) => `${r.display_name} enabled`,
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
      key: "client",
      header: "Client / entity",
      defaultHidden: true,
      render: (r) => <span className="font-mono text-xs">{r.protocol === "OIDC" ? (r.client_id ?? "—") : (r.sp_entity_id ?? "—")}</span>,
    },
    {
      key: "endpoint",
      header: "Sign-in address",
      defaultHidden: true,
      render: (r) => {
        const v = r.protocol === "OIDC" ? (r.authorization_endpoint ?? r.federation_entity_id) : r.sso_url;
        return v ? <span className="block max-w-64 truncate font-mono text-xs" title={v}>{v}</span> : <span className="text-ink-subtle">—</span>;
      },
    },
    {
      key: "trust",
      header: "Pinned trust",
      defaultHidden: true,
      render: (r) =>
        r.federation_entity_id
          ? "Trust anchor"
          : r.protocol === "SAML"
            ? `${r.pinned_certificates ?? 0} certificate${r.pinned_certificates === 1 ? "" : "s"}`
            : `${r.pinned_keys ?? 0} key${r.pinned_keys === 1 ? "" : "s"}`,
    },
    {
      key: "auth",
      header: "Client authentication",
      defaultHidden: true,
      render: (r) => (r.protocol === "OIDC" && r.client_auth ? humanize(r.client_auth) : "—"),
    },
    { key: "scopes", header: "Scopes", defaultHidden: true, render: (r) => <span className="text-xs">{r.scopes?.join(", ") || "—"}</span> },
    { key: "algs", header: "Algorithms", defaultHidden: true, render: (r) => <span className="text-xs">{r.algorithms.join(", ")}</span> },
    { key: "amr", header: "Trusts auth methods", defaultHidden: true, render: (r) => (r.trust_amr ? "Yes" : "No") },
    {
      key: "acr",
      header: "Assurance levels",
      defaultHidden: true,
      render: (r) => <span className="font-mono text-xs">{Object.entries(r.acr_levels).map(([k, v]) => `${k}=${v}`).join(", ") || "—"}</span>,
    },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (r) => (
        <ActionMenu
          label={`Actions for ${r.display_name}`}
          onAction={(id) => setOpen({ kind: id as RowAction, row: r })}
          items={[
            { id: "login", label: "Copy sign-in link", icon: <LogIn aria-hidden className="size-4" /> },
            { id: "edit", label: "Edit settings", icon: <Pencil aria-hidden className="size-4" />, disabled: r.status !== "ACTIVE" },
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
            r.status === "ACTIVE"
              ? { id: "disable", label: "Disable", icon: <Ban aria-hidden className="size-4" />, danger: true, separatorBefore: true }
              : { id: "enable", label: "Enable", icon: <Power aria-hidden className="size-4" />, separatorBefore: true },
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
      {open?.kind === "edit" ? <EditIntegrationDialog integration={open.row} onClose={close} /> : null}
      {open?.kind === "enable" ? (
        <R3ActionDialog
          isOpen
          onClose={close}
          title={`Enable ${open.row.display_name}`}
          intent={
            <p>
              Sign-in through this provider is accepted again from the moment this commits. Check that its keys or certificates are still current
              (Rotate) and that the metadata has not expired.
            </p>
          }
          scope={[
            ["Provider", open.row.display_name],
            ["Protocol", open.row.protocol],
            ["Issuer", <span key="i" className="break-all font-mono text-xs">{open.row.issuer}</span>],
            ["Connection", <CopyId key="c" value={open.row.connection_id} label="connection" />],
          ]}
          reversibility="reversible"
          commitLabel="Enable integration"
          run={async () => {
            const r = await enable.mutateAsync(open.row.connection_id);
            return { outcome: r.status, objectId: r.connection_id, revision: r.config_revision };
          }}
        />
      ) : null}
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
              sessions are not ended. You can enable it again later; until then it cannot be edited or rotated.
            </p>
          }
          scope={[
            ["Provider", open.row.display_name],
            ["Protocol", open.row.protocol],
            ["Issuer", <span key="i" className="break-all font-mono text-xs">{open.row.issuer}</span>],
            ["Connection", <CopyId key="c" value={open.row.connection_id} label="connection" />],
          ]}
          reversibility="reversible-with-effort"
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
