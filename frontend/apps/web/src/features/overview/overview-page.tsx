"use client";

import type { TenantView } from "@zoiko/api-client";
import { Banner, ButtonLink, Card, PageHeader, StatusBadge, SummaryStrip } from "@zoiko/ui";
import { Building2, CheckCircle2, Circle, KeyRound, LayoutDashboard, PartyPopper, Rocket, UserPlus } from "lucide-react";

import { PendingInvitation } from "@/features/account/invitation";
import { ApprovalsWaiting } from "@/features/approvals/guided-approval";
import { DevelopmentNotice, GATES_KEY, GateList, LIFECYCLE, blockingCount, lifecycleIndex, standing, useTenant } from "@/features/onboarding/shared";
import { QueryView, useCellQuery } from "@/lib/cell";

/** The lifecycle as a horizontal stepper (OVERLAY-001 §2): where the company is, what comes next. */
function Lifecycle({ tenant }: { tenant: TenantView }) {
  const at = lifecycleIndex(tenant.status);
  const off = at < 0; // BLOCKED, SUSPENDED, OFFBOARDING, CLOSED
  return (
    <Card title="Where your company is" description={off ? undefined : LIFECYCLE[at]?.meaning}>
      {off ? (
        <Banner tone="warn" title={`Workspace ${tenant.status.toLowerCase()}`}>
          The workspace is off the normal onboarding path. An owner can see why under Team &amp; invitations and the audit log.
        </Banner>
      ) : (
        <ol className="flex flex-wrap items-center gap-2" aria-label="Onboarding lifecycle">
          {LIFECYCLE.map((s, i) => {
            const done = i < at;
            const current = i === at;
            return (
              <li key={s.status} className="flex items-center gap-2">
                <span
                  aria-current={current ? "step" : undefined}
                  className={
                    current
                      ? "flex items-center gap-1.5 rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold text-brand-700"
                      : done
                        ? "flex items-center gap-1.5 rounded-full bg-ok-bg px-3 py-1 text-xs font-medium text-ok-fg"
                        : "flex items-center gap-1.5 rounded-full bg-hover px-3 py-1 text-xs text-ink-subtle"
                  }
                >
                  {done ? <CheckCircle2 aria-hidden className="size-3.5" /> : <Circle aria-hidden className="size-3.5" />}
                  {s.label}
                </span>
                {i < LIFECYCLE.length - 1 ? <span aria-hidden className="h-px w-3 bg-border" /> : null}
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

function AdminOverview({ tenant }: { tenant: TenantView }) {
  const gates = useCellQuery(GATES_KEY, (api) => api.onboarding.gates());
  const members = useCellQuery(["tenant", "members"], (api) => api.tenant.members());
  const items = members.data?.items ?? [];
  const joined = items.filter((m) => m.progress === "ACTIVE").length;
  const waiting = items.filter((m) => m.progress !== "ACTIVE" && m.progress !== "REVOKED").length;
  const blocking = blockingCount(gates.data?.gates ?? []);
  const at = lifecycleIndex(tenant.status);

  return (
    <div className="space-y-6">
      <SummaryStrip
        label="Company summary"
        items={[
          { label: "Stage", value: <StatusBadge state={tenant.status} kind="Tenant" size="xs" />, href: "/admin/onboarding" },
          { label: "Blocking gates", value: blocking, href: "/admin/onboarding", tone: blocking ? "warn" : "ok" },
          { label: "People joined", value: joined, href: "/admin/team" },
          { label: "Invitations open", value: waiting, href: "/admin/team", tone: waiting ? "warn" : undefined },
          { label: "Home cell", value: <span className="font-mono">{tenant.home_cell_id}</span>, href: "/admin/residency" },
        ]}
      />
      <Lifecycle tenant={tenant} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card
          title="Action required"
          description={blocking ? "These gates block the next stage." : "Nothing blocks the next stage."}
          actions={
            tenant.status !== "ACTIVE" ? (
              <ButtonLink href="/admin/onboarding" size="sm" icon={<Rocket aria-hidden className="size-4" />}>
                Continue onboarding
              </ButtonLink>
            ) : null
          }
        >
          <QueryView query={gates}>
            {(g) => <GateList gates={g.gates} empty={at >= lifecycleIndex("INTEGRATING") ? "Stage gates are checked in Onboarding → Validate." : undefined} />}
          </QueryView>
        </Card>
        <Card
          title="Your team"
          description="People invited to the workspace and how far they are with their passkey."
          actions={
            <ButtonLink href="/admin/team" size="sm" variant="secondary" icon={<UserPlus aria-hidden className="size-4" />}>
              Invite people
            </ButtonLink>
          }
        >
          <QueryView query={members}>
            {(m) =>
              m.items.length ? (
                <ul className="divide-y divide-border">
                  {m.items.slice(0, 6).map((x) => (
                    <li key={x.assignment_id} className="flex items-center justify-between py-2 text-sm">
                      <span className="font-mono text-xs text-ink-muted">{x.principal_id.slice(0, 8)}…</span>
                      <span className="text-ink">{x.role.toLowerCase()}</span>
                      <StatusBadge state={x.progress} kind="Progress" size="xs" />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-ink-muted">Only you so far. Invite colleagues so each creates their own passkey.</p>
              )
            }
          </QueryView>
        </Card>
      </div>
    </div>
  );
}

/** A founder with one passkey and no recovery codes can lock the whole company out by losing one
 * device (AUTH-001 recovery): until there is a second passkey or recovery codes, say so. */
function RecoveryPrompt() {
  const passkeys = useCellQuery(["authn", "authenticators"], (api) => api.me.authenticators());
  const codes = useCellQuery(["authn", "recovery-status"], (api) => api.me.recoveryCodeStatus());
  const active = (passkeys.data?.items ?? []).filter((a) => a.status === "ACTIVE").length;
  if (!passkeys.data || !codes.data || active >= 2 || codes.data.codes_remaining > 0) return null;
  return (
    <Banner tone="warn" icon={KeyRound} title="Protect your access">
      You have one passkey and no recovery codes. If you lose this device, nobody can get back into the account. Add a second passkey or save
      recovery codes now.
      <span className="mt-2 flex flex-wrap gap-3">
        <a href="/account/recovery" className="font-medium text-brand-700 hover:underline">
          Save recovery codes
        </a>
        <a href="/account/passkeys" className="font-medium text-brand-700 hover:underline">
          Add a passkey
        </a>
      </span>
    </Banner>
  );
}

/** Landing page (UX-001 §5, §31): exception-led - what the company's state is and what to do next. */
export function OverviewPage({ welcome, carry }: { welcome: boolean; carry: string }) {
  const tenant = useTenant();
  return (
    <div className="space-y-6">
      <QueryView query={tenant}>
        {(t) => {
          if (!t) {
            return (
              <>
                <PageHeader icon={LayoutDashboard} title="Overview" description="Your Zoiko iD account." />
                <Banner tone="info" title="No company workspace">
                  You are signed in, but not part of a company workspace. Manage your passkeys and sessions under{" "}
                  <a href="/account" className="font-medium text-brand-700 hover:underline">
                    My account
                  </a>
                  .
                </Banner>
              </>
            );
          }
          const me = standing(t);
          return (
            <>
              <PageHeader icon={LayoutDashboard} title={t.display_name} description={`Workspace ${t.tenant_slug}`} />
              <PendingInvitation autoJoin={welcome} />
              <RecoveryPrompt />
              {welcome && carry.includes("org=dev") ? <DevelopmentNotice /> : null}
              {welcome && me.pendingOwner && !me.personal ? (
                <Banner tone="ok" icon={PartyPopper} title="Your passkey is set up">
                  Next, verify your organization so you become its owner.
                </Banner>
              ) : null}
              {me.personal ? <PersonalWorkspace /> : null}
              {me.pendingOwner && !me.personal ? (
                <Card title="Verify your organization" description="Your account is active. The organization is not established until it is verified against its official register.">
                  <div className="space-y-3">
                    <p className="text-sm text-ink-muted">
                      Find the organization in its register, show how you act for it and complete your identity check. Each check is tracked on its own, and you
                      become the owner only when all of them are complete.
                    </p>
                    <ButtonLink href={`/admin/organization${carry ? `?${carry}` : ""}`} icon={<Building2 aria-hidden className="size-4" />}>
                      Start verification
                    </ButtonLink>
                  </div>
                </Card>
              ) : null}
              {me.isAdmin ? <ApprovalsWaiting compact /> : null}
              {me.isAdmin ? <AdminOverview tenant={t} /> : null}
              {!me.isAdmin && (!me.pendingOwner || me.personal) && !me.invited ? (
                <>
                  <Lifecycle tenant={t} />
                  <Card title="Your account">
                    <p className="text-sm text-ink-muted">
                      Manage your passkeys, sessions and privacy under{" "}
                      <a href="/account" className="font-medium text-brand-700 hover:underline">
                        My account
                      </a>
                      .
                    </p>
                  </Card>
                </>
              ) : null}
            </>
          );
        }}
      </QueryView>
    </div>
  );
}

/** WF-13A S5: a personal workspace. The account is active; identity is not verified and nothing here asks for it. An organization can be added later, from the claim flow. */
function PersonalWorkspace() {
  return (
    <Card title="Your personal workspace" description="Your account is active. It is not identity verified, and nothing here requires it.">
      <div className="space-y-3">
        <ul className="space-y-1 text-sm text-ink-muted">
          <li>Manage your passkeys, sessions and privacy under My account.</li>
          <li>Verify your identity only when something asks for it (a verified-person badge, or to claim an organization).</li>
          <li>If you are invited to an organization, the invitation appears here for you to accept or decline.</li>
        </ul>
        <div className="flex flex-wrap gap-2">
          <ButtonLink href="/account" variant="secondary" icon={<KeyRound aria-hidden className="size-4" />}>
            My account
          </ButtonLink>
          <ButtonLink href="/account/verification" variant="secondary" icon={<CheckCircle2 aria-hidden className="size-4" />}>
            Verify my identity
          </ButtonLink>
          <ButtonLink href="/admin/organization" variant="ghost" icon={<Building2 aria-hidden className="size-4" />}>
            Add an organization
          </ButtonLink>
        </div>
      </div>
    </Card>
  );
}
