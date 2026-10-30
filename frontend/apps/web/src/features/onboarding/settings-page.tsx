"use client";

import type {
  ConfigurationRevision,
  TenantMember,
  TenantTransition,
  TenantView,
} from "@zoiko/api-client";
import {
  Banner,
  Button,
  Card,
  Code,
  CopyId,
  DataTable,
  DateTime,
  KeyValues,
  PageHeader,
  Select,
  StatusBadge,
  Tabs,
  type Column,
} from "@zoiko/ui";
import { Crown, PauseCircle, Settings, ShieldAlert } from "lucide-react";
import { useState } from "react";

import {
  ApiErrorView,
  QueryView,
  useCellMutation,
  useCellQuery,
} from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { AuthenticationSettings } from "../settings/authentication";
import { BrandingSettingsPanel } from "../settings/branding";
import { CryptographySettings } from ". ./settings/cryptography";
import { NotificationSettingsPanel } from "../settings/notifications";
import { ApprovalStep, TENANT_KEY, standing, useTenant } from "./shared";

/** The step-up action the server classifies as S2 (ZID-ENG-DEC-009): adding or removing an owner. */
const OWNER_TRANSFER = "initiate-confirm-transfer-of-organization-ownership";

const SUSPEND_REASONS = [
  { id: "SECURITY_INCIDENT", label: "Security incident" },
  { id: "COMMERCIAL_HOLD", label: "Commercial hold" },
  { id: "LEGAL_HOLD", label: "Legal hold" },
  { id: "OWNER_REQUEST", label: "Owner request" },
];
const OFFBOARD_REASONS = [
  { id: "OWNER_REQUEST", label: "Owner request" },
  { id: "CONTRACT_ENDED", label: "Contract ended" },
  { id: "MIGRATED_AWAY", label: "Moved to another platform" },
];

function Details({ tenant }: { tenant: TenantView }) {
  return (
    <Card title="Company">
      <KeyValues
        columns={2}
        items={[
          ["Name", tenant.display_name],
          [
            "Workspace",
            <span key="w" className="font-mono">
              {tenant.tenant_slug}
            </span>,
          ],
          [
            "Stage",
            <StatusBadge key="s" state={tenant.status} kind="Tenant" />,
          ],
          ["Deployment mode", tenant.deployment_mode],
          [
            "Home cell",
            <span key="c" className="font-mono">
              {tenant.home_cell_id}
            </span>,
          ],
          ["Configuration revision", tenant.configuration_revision],
          [
            "Tenant ID",
            <CopyId key="t" value={tenant.tenant_id} label="tenant" />,
          ],
          [
            "Organization",
            tenant.organization_id ? (
              <CopyId
                key="o"
                value={tenant.organization_id}
                label="organization"
              />
            ) : (
              "Not verified"
            ),
          ],
          ["Created", <DateTime key="d" value={tenant.created_at} />],
        ]}
      />
    </Card>
  );
}

/** Owners (OVERLAY-001 §4): adding or removing one is a protected action with a second
 * administrator's approval; the last owner can never be removed. */
function Owners({ tenantId }: { tenantId: string }) {
  const members = useCellQuery(["tenant", "members"], (api) =>
    api.tenant.members(),
  );
  const [target, setTarget] = useState<{
    kind: "add" | "remove";
    member: TenantMember;
  } | null>(null);
  const { proveAction } = useRuntime();
  const resource = `tenant:${tenantId}`;
  const add = useCellMutation(
    async (api, v: { p: string; a: string }) =>
      api.tenant.addOwner(
        v.p,
        v.a,
        await proveAction({
          action: OWNER_TRANSFER,
          resource,
          change: { operation: "ADD_OWNER", principal_id: v.p },
        }),
      ),
    {
      invalidate: [["tenant", "members"], TENANT_KEY],
      success: "Owner added",
      onSuccess: () => setTarget(null),
    },
  );
  const remove = useCellMutation(
    async (api, v: { p: string; a: string }) =>
      api.tenant.removeOwner(
        v.p,
        v.a,
        await proveAction({
          action: OWNER_TRANSFER,
          resource,
          change: { operation: "REMOVE_OWNER", principal_id: v.p },
        }),
      ),
    {
      invalidate: [["tenant", "members"], TENANT_KEY],
      success: "Owner removed",
      onSuccess: () => setTarget(null),
    },
  );
  const rows = (members.data?.items ?? []).filter((m) => m.status === "ACTIVE");
  const owners = rows.filter((m) => m.role === "OWNER");
  const columns: Column<TenantMember>[] = [
    {
      key: "p",
      header: "Person",
      render: (m) => <CopyId value={m.principal_id} label="principal" />,
    },
    {
      key: "role",
      header: "Role",
      render: (m) => <StatusBadge state={m.role} kind="Role" size="xs" />,
    },
    {
      key: "since",
      header: "Since",
      render: (m) => <DateTime value={m.activated_at} />,
    },
    {
      key: "a",
      header: "",
      align: "end",
      render: (m) =>
        m.role === "OWNER" ? (
          <Button
            size="sm"
            variant="ghost"
            isDisabled={owners.length < 2}
            onPress={() => setTarget({ kind: "remove", member: m })}
          >
            Remove owner
          </Button>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            onPress={() => setTarget({ kind: "add", member: m })}
          >
            Make owner
          </Button>
        ),
    },
  ];
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">
        Owners govern the workspace itself: administrators, lifecycle, residency
        and support access. Being an owner is a workspace role, not legal
        signing authority for the company.
      </p>
      <Card padded={false}>
        <QueryView query={members}>
          {() => (
            <DataTable
              caption="Active members"
              columns={columns}
              rows={rows}
              rowKey={(m) => m.assignment_id}
            />
          )}
        </QueryView>
      </Card>
      {owners.length < 2 ? (
        <p className="text-xs text-ink-muted">
          The last owner cannot be removed. Add another owner first.
        </p>
      ) : null}
      {target ? (
        <ApprovalStep
          action="tenant.owner.change"
          resource={`tenant:${tenantId}`}
          change={`${target.kind === "add" ? "ADD_OWNER" : "REMOVE_OWNER"}:${target.member.principal_id}`}
          label={target.kind === "add" ? "Make owner" : "Remove owner"}
          onRun={(id) =>
            (target.kind === "add" ? add : remove).mutate({
              p: target.member.principal_id,
              a: id,
            })
          }
          pending={add.isPending || remove.isPending}
          error={add.error ?? remove.error}
        />
      ) : null}
    </div>
  );
}

function Lifecycle({ tenant }: { tenant: TenantView }) {
  const [suspendReason, setSuspendReason] = useState("SECURITY_INCIDENT");
  const [offReason, setOffReason] = useState("OWNER_REQUEST");
  const [offboarding, setOffboarding] = useState(false);
  const suspend = useCellMutation((api) => api.tenant.suspend(suspendReason), {
    invalidate: [TENANT_KEY],
    success: "Workspace suspended",
  });
  const resume = useCellMutation((api, id: string) => api.tenant.resume(id), {
    invalidate: [TENANT_KEY],
    success: "Workspace resumed",
  });
  const offboard = useCellMutation(
    (api, id: string) => api.tenant.offboard(id, offReason),
    { invalidate: [TENANT_KEY], success: "Offboarding started" },
  );
  const resource = `tenant:${tenant.tenant_id}`;
  const closed = tenant.status === "OFFBOARDING" || tenant.status === "CLOSED";
  return (
    <div className="space-y-4">
      {tenant.status === "SUSPENDED" ? (
        <Card
          title="Resume the workspace"
          description="Restores sign-in and enforcement. Needs a second administrator."
        >
          <ApprovalStep
            action="tenant.lifecycle.manage"
            resource={resource}
            change="RESUME"
            label="Resume"
            onRun={(id) => resume.mutate(id)}
            pending={resume.isPending}
            error={resume.error}
          />
        </Card>
      ) : !closed ? (
        <Card
          title="Suspend the workspace"
          description="Immediately disables production trust: sessions end and protected actions are refused. Owners can still resume it."
        >
          <div className="flex flex-wrap items-end gap-3">
            <Select
              label="Reason"
              value={suspendReason}
              onChange={setSuspendReason}
              options={SUSPEND_REASONS}
              className="min-w-56"
            />
            <Button
              variant="danger"
              icon={<PauseCircle aria-hidden className="size-4" />}
              pending={suspend.isPending}
              onPress={() => suspend.mutate(undefined)}
            >
              Suspend
            </Button>
          </div>
          {suspend.error ? (
            <div className="mt-3">
              <ApiErrorView error={suspend.error} />
            </div>
          ) : null}
        </Card>
      ) : null}
      {!closed ? (
        <Card
          title="Offboard the company"
          description="Drains integrations, revokes every session, then runs export, deletion and retention. This cannot be undone."
        >
          {offboarding ? (
            <div className="space-y-3">
              <Select
                label="Reason"
                value={offReason}
                onChange={setOffReason}
                options={OFFBOARD_REASONS}
                className="max-w-sm"
              />
              <ApprovalStep
                action="tenant.lifecycle.manage"
                resource={resource}
                change="OFFBOARD"
                label="Offboard company"
                onRun={(id) => offboard.mutate(id)}
                pending={offboard.isPending}
                error={offboard.error}
              />
            </div>
          ) : (
            <Button
              variant="danger"
              icon={<ShieldAlert aria-hidden className="size-4" />}
              onPress={() => setOffboarding(true)}
            >
              Offboard…
            </Button>
          )}
        </Card>
      ) : (
        <Banner tone="warn" title={`Workspace ${tenant.status.toLowerCase()}`}>
          Offboarding drains integrations and revokes access; only retained
          evidence remains once it is closed.
        </Banner>
      )}
    </div>
  );
}

function History() {
  const transitions = useCellQuery(["tenant", "transitions"], (api) =>
    api.tenant.transitions(),
  );
  const revisions = useCellQuery(["tenant", "revisions"], (api) =>
    api.tenant.configurationRevisions(),
  );
  const tCols: Column<TenantTransition>[] = [
    {
      key: "at",
      header: "When",
      render: (t) => <DateTime value={t.occurred_at} />,
      sortValue: (t) => t.occurred_at,
    },
    {
      key: "move",
      header: "Change",
      render: (t) => (
        <span>
          {(t.from_status ?? "—").toLowerCase()} → {t.to_status.toLowerCase()}
        </span>
      ),
    },
    {
      key: "reason",
      header: "Reason",
      render: (t) => <Code>{t.reason_code}</Code>,
    },
    {
      key: "by",
      header: "By",
      render: (t) =>
        t.actor_principal_id ? (
          <CopyId value={t.actor_principal_id} label="principal" />
        ) : (
          t.actor_type.toLowerCase()
        ),
    },
  ];
  const rCols: Column<ConfigurationRevision>[] = [
    {
      key: "rev",
      header: "Revision",
      render: (r) => r.revision,
      sortValue: (r) => r.revision,
    },
    {
      key: "kind",
      header: "Change",
      render: (r) => <Code>{r.change_kind}</Code>,
    },
    {
      key: "at",
      header: "When",
      render: (r) => <DateTime value={r.created_at} />,
    },
    {
      key: "by",
      header: "By",
      render: (r) =>
        r.actor_principal_id ? (
          <CopyId value={r.actor_principal_id} label="principal" />
        ) : (
          "system"
        ),
    },
  ];
  return (
    <div className="space-y-6">
      <Card title="Lifecycle" padded={false}>
        <QueryView query={transitions}>
          {(r) => (
            <DataTable
              caption="Lifecycle transitions"
              columns={tCols}
              rows={r.items}
              rowKey={(t) => t.transition_id}
            />
          )}
        </QueryView>
      </Card>
      <Card
        title="Configuration revisions"
        description="Every configuration change appends a revision; only a commitment to its content is kept here."
        padded={false}
      >
        <QueryView query={revisions}>
          {(r) => (
            <DataTable
              caption="Configuration revisions"
              columns={rCols}
              rows={r.items}
              rowKey={(x) => String(x.revision)}
            />
          )}
        </QueryView>
      </Card>
    </div>
  );
}

/** Tenant settings (UX-001 §19; OVERLAY-001 §2-4): company details, owners, lifecycle and history. */
export function SettingsPage() {
  const tenant = useTenant();
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Settings}
        title="Company settings"
        description="Your company workspace, its owners, lifecycle and change history."
      />
      <QueryView query={tenant}>
        {(t) => {
          if (!t)
            return (
              <Banner tone="warn" title="No company workspace">
                You are not part of a company workspace.
              </Banner>
            );
          const me = standing(t);
          return (
            <Tabs
              label="Company settings"
              tabs={[
                {
                  id: "details",
                  label: "Details",
                  content: <Details tenant={t} />,
                },
                ...(me.isOwner
                  ? [
                      {
                        id: "owners",
                        label: (
                          <span className="flex items-center gap-1.5">
                            <Crown aria-hidden className="size-3.5" />
                            Owners
                          </span>
                        ),
                        content: <Owners tenantId={t.tenant_id} />,
                      },
                      {
                        id: "lifecycle",
                        label: "Lifecycle",
                        content: <Lifecycle tenant={t} />,
                      },
                    ]
                  : []),
                ...(me.isAdmin
                  ? [
                      {
                        id: "authentication",
                        label: "Authentication",
                        content: <AuthenticationSettings />,
                      },
                      {
                        id: "notifications",
                        label: "Notifications",
                        content: <NotificationSettingsPanel />,
                      },
                      {
                        id: "branding",
                        label: "Branding",
                        content: <BrandingSettingsPanel />,
                      },
                      {
                        id: "cryptography",
                        label: "Cryptography",
                        content: <CryptographySettings />,
                      },
                      { id: "history", label: "History", content: <History /> },
                    ]
                  : []),
              ]}
            />
          );
        }}
      </QueryView>
    </div>
  );
}
