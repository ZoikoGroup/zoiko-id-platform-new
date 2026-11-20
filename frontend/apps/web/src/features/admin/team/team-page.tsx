"use client";

import type { Invitation, InvitableRole, MemberProgress, TenantMember } from "@zoiko/api-client";
import {
  Banner,
  Button,
  ButtonLink,
  Card,
  Checkbox,
  DataTable,
  Dialog,
  EmptyState,
  PageHeader,
  RadioCards,
  RelativeTime,
  StatusBadge,
  SummaryStrip,
  Tabs,
  TextField,
  type Column,
} from "@zoiko/ui";
import { AlertTriangle, Check, CheckCircle2, Circle, Copy, Fingerprint, LogIn, Mail, RefreshCw, UserPlus, Users } from "lucide-react";
import { useMemo, useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { can } from "@/lib/nav-access";

import { DelegationsTab } from "./delegations-tab";
import { OrgStructureTab } from "./org-structure-tab";
import { CustomRolesTab } from "./custom-roles-tab";
import { PersonLabel } from "./person-label";
import { RolesTab } from "./roles-tab";

const MEMBERS_KEY = ["tenant", "members"];
const TENANT_KEY = ["tenant", "me"];

/** The four steps an invited person goes through, in order (OVERLAY-001 §4). */
const STEPS: { id: MemberProgress; label: string; icon: typeof Circle }[] = [
  { id: "INVITED", label: "Link shared", icon: Mail },
  { id: "PASSKEY_CREATED", label: "Passkey created", icon: Fingerprint },
  { id: "SIGNED_IN", label: "Signed in", icon: LogIn },
  { id: "ACTIVE", label: "Joined", icon: CheckCircle2 },
];

function stepIndex(p: MemberProgress): number {
  if (p === "PENDING_VERIFICATION") return 0;
  const i = STEPS.findIndex((s) => s.id === p);
  return i < 0 ? -1 : i;
}

function Progress({ member }: { member: TenantMember }) {
  if (member.progress === "REVOKED") return <StatusBadge state="REVOKED" kind="Invitation" size="xs" />;
  const at = stepIndex(member.progress);
  return (
    <ol className="flex items-center gap-1.5" aria-label={`Progress: ${STEPS[Math.max(at, 0)]?.label ?? member.progress}`}>
      {STEPS.map((s, i) => {
        const done = i <= at;
        const Icon = done ? s.icon : Circle;
        return (
          <li key={s.id} className="flex items-center gap-1.5">
            <span
              title={s.label}
              className={
                done
                  ? "flex size-6 items-center justify-center rounded-full bg-ok-bg text-ok-fg"
                  : "flex size-6 items-center justify-center rounded-full bg-hover text-ink-subtle"
              }
            >
              <Icon aria-hidden className="size-3.5" />
            </span>
            {i < STEPS.length - 1 ? <span aria-hidden className={done && i < at ? "h-px w-4 bg-ok-fg" : "h-px w-4 bg-border"} /> : null}
          </li>
        );
      })}
      <li className="ml-1 text-xs text-ink-muted">{STEPS[Math.max(at, 0)]?.label}</li>
    </ol>
  );
}

/** The link names the workspace too: an invitee's browser has no workspace cookie yet, and without it the ticket is
 * looked up in the wrong company and refused. */
function enrollLink(ticket: string, workspace?: string): string {
  const w = workspace ? `&workspace=${encodeURIComponent(workspace)}` : "";
  return `${window.location.origin}/enroll?ticket=${encodeURIComponent(ticket)}${w}`;
}

/** The single-use passkey setup link: shown once, copied or handed over by email. */
function ShareLink({ invitation, workspace, onDone }: { invitation: Invitation; workspace?: string; onDone: () => void }) {
  const link = useMemo(() => enrollLink(invitation.enrollment_ticket, workspace), [invitation.enrollment_ticket, workspace]);
  const [copied, setCopied] = useState(false);
  const [ack, setAck] = useState(false);
  const expires = new Date(invitation.enrollment_ticket_expires_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
  const mail = `mailto:?subject=${encodeURIComponent("Set up your Zoiko iD passkey")}&body=${encodeURIComponent(
    `You have been invited. Open this link on the device you want to sign in with and create your passkey:\n\n${link}\n\nThe link works once and expires ${expires}.`,
  )}`;
  return (
    <div className="space-y-4">
      <Banner tone="warn" icon={AlertTriangle} title="Shown once" live="polite">
        Anyone with this link can create the passkey for this account. Send it only to the person you invited, over a channel you trust. It works once
        and expires {expires}.
      </Banner>
      <div>
        <div className="mb-1.5 text-sm font-medium text-ink">Passkey setup link</div>
        <div className="flex items-stretch gap-2">
          <code aria-label="Passkey setup link" className="min-w-0 flex-1 overflow-x-auto rounded-control border border-border-strong bg-canvas px-3 py-2 font-mono text-xs text-ink">
            {link}
          </code>
          <Button
            variant="secondary"
            onPress={() => void navigator.clipboard?.writeText(link).then(() => setCopied(true))}
            icon={copied ? <Check aria-hidden className="size-4 text-ok-fg" /> : <Copy aria-hidden className="size-4" />}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>
      <a href={mail} className="inline-flex items-center gap-2 text-sm font-medium text-brand-700 hover:underline">
        <Mail aria-hidden className="size-4" /> Open an email with the link
      </a>
      <p className="text-xs text-ink-muted">
        Watch the person&apos;s progress in the team list: it updates by itself when they create their passkey, sign in and join.
      </p>
      <Checkbox isSelected={ack} onChange={setAck}>
        I have shared the link with the person I invited
      </Checkbox>
      <div className="flex justify-end">
        <Button isDisabled={!ack} onPress={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}

export function TeamPage() {
  const tenant = useCellQuery(TENANT_KEY, (api) => api.tenant.me());
  const members = useCellQuery(MEMBERS_KEY, (api) => api.tenant.members(), { refetchInterval: 5_000 });
  const permissions = useCellQuery(["tenant", "permissions", "team"], (api) => api.tenant.myPermissions());
  // Only people whose role may manage members see the controls; the cell refuses everyone else anyway.
  const canManage = can(permissions.data?.rights, "a.invite-operator-or-viewer", "X");
  const [inviting, setInviting] = useState(false);
  const [role, setRole] = useState<InvitableRole>("MEMBER");
  const [displayName, setDisplayName] = useState("");
  const [renaming, setRenaming] = useState<TenantMember | null>(null);
  const [newName, setNewName] = useState("");
  const [issued, setIssued] = useState<Invitation | null>(null);
  const [revoking, setRevoking] = useState<TenantMember | null>(null);

  const invite = useCellMutation((api, v: { role: InvitableRole; name: string }) => api.tenant.invite(v.role, v.name.trim() || undefined), {
    invalidate: [MEMBERS_KEY],
    onSuccess: (inv) => setIssued(inv),
  });
  const rename = useCellMutation((api, v: { id: string; name: string }) => api.tenant.rename(v.id, v.name.trim() || null), {
    invalidate: [MEMBERS_KEY],
    success: "Name saved",
    onSuccess: () => setRenaming(null),
  });
  const reissue = useCellMutation((api, id: string) => api.tenant.reissue(id), {
    invalidate: [MEMBERS_KEY],
    onSuccess: (inv) => {
      setIssued(inv);
      setInviting(true);
    },
  });
  const revoke = useCellMutation((api, id: string) => api.tenant.revoke(id), {
    invalidate: [MEMBERS_KEY],
    success: "Access removed",
    onSuccess: () => setRevoking(null),
  });

  const closeInvite = () => {
    if (invite.isPending) return;
    setInviting(false);
    setIssued(null);
    setDisplayName("");
    invite.reset();
    reissue.reset();
  };

  const rows = members.data?.items ?? [];
  const pending = rows.filter((m) => m.status === "INVITED").length;
  const joined = rows.filter((m) => m.status === "ACTIVE").length;
  const withPasskey = rows.filter((m) => m.passkeys > 0 && m.status !== "REVOKED").length;

  const columns: Column<TenantMember>[] = [
    {
      key: "who",
      header: "Person",
      pinned: true,
      render: (m) => <PersonLabel person={m.person} fallbackId={m.principal_id} />,
      sortValue: (m) => m.created_at,
    },
    { key: "role", header: "Role", render: (m) => <span className="text-sm font-medium">{m.role}</span>, sortValue: (m) => m.role },
    { key: "progress", header: "Progress", render: (m) => <Progress member={m} />, sortValue: (m) => stepIndex(m.progress) },
    {
      key: "passkey",
      header: "Passkey",
      render: (m) =>
        m.passkeys > 0 ? (
          <span className="text-sm">
            {m.passkeys} · <RelativeTime value={m.passkey_registered_at} />
          </span>
        ) : (
          <span className="text-sm text-ink-subtle">Not yet</span>
        ),
    },
    {
      key: "seen",
      header: "Last sign-in",
      render: (m) => (m.last_sign_in_at ? <RelativeTime value={m.last_sign_in_at} /> : <span className="text-sm text-ink-subtle">Never</span>),
      sortValue: (m) => m.last_sign_in_at ?? "",
    },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (m) => (
        <div className="flex justify-end gap-2">
          {canManage && m.status === "INVITED" && m.passkeys === 0 ? (
            <Button
              size="xs"
              variant="secondary"
              icon={<RefreshCw aria-hidden className="size-3.5" />}
              pending={reissue.isPending && reissue.variables === m.assignment_id}
              onPress={() => reissue.mutate(m.assignment_id)}
            >
              New link
            </Button>
          ) : null}
          {canManage && m.status !== "REVOKED" ? (
            <Button
              size="xs"
              variant="secondary"
              onPress={() => {
                setRenaming(m);
                setNewName(m.person?.name ?? "");
              }}
            >
              Name
            </Button>
          ) : null}
          {canManage && m.role !== "OWNER" && m.status !== "REVOKED" ? (
            <Button size="xs" variant="danger-outline" onPress={() => setRevoking(m)}>
              Remove
            </Button>
          ) : null}
        </div>
      ),
    },
  ];

  const people = (
      <div className="space-y-5">
        {tenant.data ? (
          <SummaryStrip
            label="Team summary"
            items={[
              { label: "Tenant", value: tenant.data.display_name },
              { label: "Status", value: <StatusBadge state={tenant.data.status} kind="Tenant" size="xs" /> },
              { label: "Joined", value: joined },
              { label: "Waiting on invitee", value: pending },
              { label: "With a passkey", value: withPasskey },
            ]}
          />
        ) : null}
        {reissue.error ? <ApiErrorView error={reissue.error} /> : null}
        <Card padded={false}>
          <QueryView query={members}>
            {(data) => (
              <DataTable
                caption="Team members and invitations"
                columns={columns}
                rows={data.items}
                rowKey={(m) => m.assignment_id}
                empty={
                  <EmptyState
                    icon={Users}
                    title="Nobody invited yet"
                    action={
                      <Button size="sm" onPress={() => setInviting(true)}>
                        Invite person
                      </Button>
                    }
                  >
                    Invite a colleague: they receive a link to create their own passkey. Nobody ever shares a passkey.
                  </EmptyState>
                }
              />
            )}
          </QueryView>
        </Card>
        <Card title="How invitations work" description="Passkeys are never shared or copied between people.">
          <ol className="list-decimal space-y-1.5 pl-5 text-sm text-ink-muted">
            <li>You invite a person: the cell creates their account and a single-use passkey setup link.</li>
            <li>You send that link to them. It works once and expires within 24 hours; you can issue a new one until they use it.</li>
            <li>They open it on their own device and create a passkey, which only they hold.</li>
            <li>They sign in with it and join the tenant. Each step shows up here as it happens.</li>
          </ol>
        </Card>
      </div>
  );

  return (
    <>
      <PageHeader
        title="Team & invitations"
        description="Invite people into this tenant. Each person gets a single-use link to create their own passkey; you can follow them through to their first sign-in."
        actions={
          canManage ? (
            <Button icon={<UserPlus aria-hidden className="size-4" />} onPress={() => setInviting(true)}>
              Invite person
            </Button>
          ) : undefined
        }
      />
      <Tabs
        label="Team"
        tabs={[
          { id: "people", label: "People", content: people },
          { id: "roles", label: "Roles and permissions", content: <RolesTab /> },
          { id: "structure", label: "Structure", content: <OrgStructureTab /> },
          { id: "custom-roles", label: "Custom roles", content: <CustomRolesTab /> },
          { id: "delegations", label: "Delegations", content: <DelegationsTab /> },
        ]}
      />

      <Dialog
        isOpen={inviting}
        onOpenChange={(o) => !o && closeInvite()}
        title={issued ? "Share the passkey setup link" : "Invite a person"}
        size="md"
        footer={
          issued ? undefined : (
            <>
              <Button variant="secondary" isDisabled={invite.isPending} onPress={closeInvite}>
                Cancel
              </Button>
              <Button pending={invite.isPending} onPress={() => invite.mutate({ role, name: displayName })}>
                Create invitation
              </Button>
            </>
          )
        }
      >
        {issued ? (
          <ShareLink invitation={issued} workspace={tenant.data?.tenant_slug} onDone={closeInvite} />
        ) : (
          <div className="space-y-4">
            <TextField label="Name (optional)" value={displayName} onChange={setDisplayName} placeholder="Priya Nair" />
            <RadioCards
              label="Role"
              value={role}
              onChange={(v) => setRole(v as InvitableRole)}
              options={[
                { id: "MEMBER", label: "Member", description: "Signs in and uses applications. The default." },
                { id: "OPERATOR", label: "Operator", description: "Runs the verification and credential work assigned to them. No policy or role administration." },
                { id: "VIEWER", label: "Viewer", description: "Reads redacted views only. Cannot change anything." },
                { id: "ADMIN", label: "Administrator", description: "Can also see tenant administration. Authority still comes from grants." },
              ]}
            />
            <p className="text-xs text-ink-muted">An invitation never makes someone an OWNER; owners change only through a second administrator&apos;s approval.</p>
            <div className="space-y-2 rounded-card border border-border bg-sunken/40 p-3">
              <p className="text-sm font-medium text-ink">Need an Approver?</p>
              <p className="text-xs text-ink-muted">
                An Approver votes on requests and approves amounts up to a limit you give them. Because it is a privileged role, it is granted by a request that one independent person approves, and the invitation link
                comes once it is applied. The same goes for an Administrator when the owners require approval.
              </p>
              <ButtonLink href="/organizations/current/approvals?new=privileged-role-grant-or-elevation" size="sm" variant="secondary">
                Request Approver or Administrator rights
              </ButtonLink>
            </div>
            {invite.error ? <ApiErrorView error={invite.error} /> : null}
          </div>
        )}
      </Dialog>

      <Dialog
        isOpen={renaming !== null}
        onOpenChange={(o) => !o && !rename.isPending && setRenaming(null)}
        title={renaming ? `Name for ${renaming.person?.member_code ?? "this person"}` : "Name"}
        size="md"
        footer={
          <>
            <Button variant="secondary" isDisabled={rename.isPending} onPress={() => setRenaming(null)}>
              Cancel
            </Button>
            <Button pending={rename.isPending} onPress={() => renaming && rename.mutate({ id: renaming.principal_id, name: newName })}>
              Save name
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <TextField label="Name" value={newName} onChange={setNewName} placeholder="Priya Nair" />
          <p className="text-xs text-ink-muted">The member number never changes. Leave the name empty to show the number alone.</p>
          {rename.error ? <ApiErrorView error={rename.error} /> : null}
        </div>
      </Dialog>

      <Dialog
        isOpen={revoking !== null}
        onOpenChange={(o) => !o && !revoke.isPending && setRevoking(null)}
        title="Remove this person from the tenant?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={revoke.isPending} onPress={() => setRevoking(null)}>
              Cancel
            </Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => revoking && revoke.mutate(revoking.assignment_id)}>
              Remove
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p>
            Their {revoking?.role.toLowerCase()} role ends now
            {revoking?.status === "INVITED" ? " and the invitation link stops working for this tenant" : ""}.
          </p>
          {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
