"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell, Banner, StatusBarItems, StatusBadge, type AlertItem, type Command, type EnvironmentKind, type NavGroup } from "@zoiko/ui";
import {
  AppWindow,
  BadgeCheck,
  ClipboardCheck,
  ListChecks,
  Building2,
  Cable,
  Fingerprint,
  Gauge,
  Globe,
  Gavel,
  Globe2,
  LayoutDashboard,
  LifeBuoy,
  Link2,
  MonitorSmartphone,
  RadioTower,
  Rocket,
  Scale,
  ScanFace,
  ScrollText,
  ServerCog,
  Settings,
  ShieldCheck,
  ShieldOff,
  UserLock,
  UserPlus,
  Users,
  Wallet,
  Mail,
  Receipt,
  Webhook,
  BellRing,
  Bot,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { PrivacySignal } from "@/components/privacy-signal";
import { ConsoleGuide } from "@/components/console-guide";
import { ageOf } from "@/features/alerts/shared";
import { RegionText } from "@/features/admin/residency/region-text";
import { standing, useTenant } from "@/features/onboarding/shared";
import { useCellQuery } from "@/lib/cell";
import { visibleNav } from "@/lib/nav-access";
import { useRuntime, type Subject } from "@/lib/runtime";

/** Navigation mirrors the cell's real API surface. What a person is shown follows their role (`visibleNav`: a Member
 * sees no administration, UX-02); the cell still decides every call by authority grants, assurance and approvals,
 * so a hidden page is a courtesy and never the protection (UX-03). */
const NAV: NavGroup[] = [
  {
    label: "Company",
    items: [
      { id: "overview", label: "Overview", href: "/overview", icon: LayoutDashboard },
      { id: "alerts", label: "Alerts & tasks", href: "/alerts", icon: BellRing },
      { id: "onboarding", label: "Onboarding", href: "/admin/onboarding", icon: Rocket },
      { id: "organization", label: "Company verification", href: "/admin/organization", icon: Building2 },
      { id: "domains", label: "Domains", href: "/admin/domains", icon: Globe },
      { id: "applications", label: "Applications", href: "/admin/applications", icon: AppWindow },
      { id: "integrations", label: "Integrations", href: "/admin/integrations", icon: Cable },
      { id: "team", label: "Team & invitations", href: "/admin/team", icon: UserPlus },
      { id: "setup", label: "Organization setup", href: "/organizations/current/setup", icon: ListChecks },
      { id: "approvals", label: "Approvals", href: "/organizations/current/approvals", icon: ClipboardCheck },
      { id: "billing", label: "Billing", href: "/admin/billing", icon: Receipt },
      { id: "contacts", label: "Email contacts", href: "/admin/contacts", icon: Mail },
      { id: "checks", label: "Verification checks", href: "/admin/checks", icon: ListChecks },
      { id: "settings", label: "Company settings", href: "/admin/settings", icon: Settings },
    ],
  },
  {
    label: "My account",
    items: [
      { id: "account", label: "My account", href: "/account", icon: LayoutDashboard },
      { id: "sessions", label: "Sessions", href: "/account/sessions", icon: MonitorSmartphone },
      { id: "passkeys", label: "Passkeys", href: "/account/passkeys", icon: Fingerprint },
      { id: "my-limits", label: "My limits and permissions", href: "/account/limits", icon: Gauge },
      { id: "connected-apps", label: "Connected apps", href: "/account/connected-apps", icon: Link2 },
      { id: "my-verification", label: "Identity verification", href: "/account/verification", icon: ScanFace },
      { id: "recovery-codes", label: "Recovery codes", href: "/account/recovery", icon: LifeBuoy },
      { id: "my-idv", label: "Passport identity check", href: "/account/identity-proofing", icon: ScanFace },
      { id: "my-privacy", label: "Privacy", href: "/account/privacy", icon: ShieldOff },
    ],
  },
  {
    label: "Administration",
    items: [
      { id: "idp", label: "Identity providers", href: "/admin/identity-providers", icon: Building2 },
      { id: "verification", label: "Verification", href: "/admin/verification", icon: BadgeCheck },
      { id: "credentials", label: "Credentials & trust", href: "/admin/credentials", icon: Wallet },
      { id: "authority", label: "Authority & approvals", href: "/admin/authority", icon: Gavel },
      { id: "mandates", label: "Mandates", href: "/admin/mandates", icon: Scale },
      { id: "decisions", label: "Access decisions", href: "/admin/decisions", icon: ShieldCheck },
      { id: "signals", label: "Security signals", href: "/admin/signals", icon: RadioTower },
      { id: "provisioning", label: "Provisioning", href: "/admin/provisioning", icon: Users },
      { id: "evidence", label: "Evidence & audit", href: "/admin/evidence", icon: ScrollText },
      { id: "operator-access", label: "Operator access", href: "/admin/operator-access", icon: UserLock },
      { id: "residency", label: "Residency", href: "/admin/residency", icon: Globe2 },
      { id: "agents", label: "Agents & tools", href: "/admin/agents", icon: Bot },
      { id: "workloads", label: "Workload identity", href: "/admin/workloads", icon: ServerCog },
      { id: "company-verification", label: "Company verification", href: "/admin/company-verification", icon: ClipboardCheck },
      { id: "developers", label: "Developers", href: "/admin/developers", icon: Webhook },
    ],
  },
];

/** The command palette lists the same pages the person is shown, so it never offers a page the navigation hides. */
function commandsFor(groups: NavGroup[]): Command[] {
  return groups.flatMap((g) => g.items.map((i) => ({ id: i.id, label: i.label, group: g.label ?? "Pages", href: i.href, icon: i.icon })));
}

function shortId(id: string) {
  return id.length > 14 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

export function ConsoleShell({
  subject,
  environment,
  children,
}: {
  subject: Subject;
  environment: EnvironmentKind;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { signOut, api } = useRuntime();
  const company = useTenant();
  const tenant = company.data;
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => (await (await fetch("/api/health", { cache: "no-store" })).json()) as { status: string },
    refetchInterval: 60_000,
  });
  // Open alerts feed the bell, the nav badge and the persistent banner for critical ones (UX §20). A
  // failed read leaves the shell without alerts rather than breaking it.
  const openAlerts = useCellQuery(["alerts", "shell"], (api) => api.alerts.list({}), { refetchInterval: 60_000 });
  const alertItems: AlertItem[] = (openAlerts.data?.items ?? []).slice(0, 8).map((a) => ({
    id: a.alert_id,
    title: a.title,
    href: a.href || "/alerts",
    severity: a.severity,
    age: ageOf(a.last_seen_at),
  }));
  const criticalOpen = (openAlerts.data?.items ?? []).filter((a) => a.severity === "CRITICAL" && a.state !== "RESOLVED");
  const openCount = openAlerts.data?.items.length ?? 0;
  const me = standing(tenant);
  // A person the Owner gave billing access (Billing Admin, Billing Viewer, Invoice reader) is not an administrator but needs the Billing page.
  const billing = useCellQuery(["billing", "access", "shell"], (api) => api.billing.access(), { enabled: Boolean(tenant) && !me.isAdmin });
  const billingAccess = !me.isAdmin && (billing.data?.permissions.length ?? 0) > 0;
  // While the company loads the full navigation is drawn, so the sidebar does not reflow when the role arrives (a
  // layout shift, UX-30); a person who is not an administrator then loses the administration links, which only ever
  // led to pages the cell refuses them. Once it is known, the navigation follows the role (UX-02).
  // What the person's roles may do (the role matrix). Approvers, Operators and Viewers get the pages their role is for; a plain Member gets none.
  const permissions = useCellQuery(["tenant", "permissions", "shell"], (api) => api.tenant.myPermissions(), { enabled: Boolean(tenant) && !me.isAdmin });
  const roleKnown = !company.isLoading;
  const nav: NavGroup[] = (roleKnown ? visibleNav(NAV, { isAdmin: me.isAdmin, pendingOwner: me.pendingOwner && !me.personal, billingAccess, rights: permissions.data?.rights }) : NAV).map((g) => ({
    ...g,
    items: g.items.map((i) =>
      i.id === "alerts" && openCount > 0 ? { ...i, badge: openCount, badgeTone: criticalOpen.length ? ("danger" as const) : ("warn" as const) } : i,
    ),
  }));
  const status = health.data?.status;
  const healthState = status === "ready" || status === "ok" ? "HEALTHY" : status === "degraded" ? "DEGRADED" : status ? "OUTAGE" : "UNKNOWN";

  return (
    <AppShell
      surface="Identity & trust"
      homeHref="/overview"
      nav={nav}
      alerts={alertItems}
      alertsHref="/alerts"
      helpHref="/help"
      banners={
        criticalOpen.length ? (
          <Banner tone="danger" title={`${criticalOpen.length} critical alert${criticalOpen.length === 1 ? "" : "s"} need attention`} live="polite">
            {criticalOpen[0]?.title}. <a className="font-medium underline" href="/alerts">Open alerts &amp; tasks</a>
          </Banner>
        ) : null
      }
      currentPath={pathname}
      // The tenant must always be visible (UX §3.1). The ID token does not carry it, so prefer the company record.
      tenant={
        tenant
          ? { id: tenant.tenant_id, name: tenant.display_name }
          : subject.tenantId
            ? { id: subject.tenantId, name: shortId(subject.tenantId) }
            : undefined
      }
      environment={environment}
      user={{ name: `Principal ${shortId(subject.sub)}`, email: subject.sub, roles: me.roleLabel ? [me.roleLabel] : subject.acr ? [subject.acr] : undefined }}
      commands={commandsFor(nav)}
      search={async (query, signal) => {
        // Authorization-aware and tenant-scoped on the server (UX-01): whatever comes back is allowed.
        const found = await api.search.query(query, signal);
        return found.items.map((h) => ({ id: `${h.type}:${h.id}`, label: h.label, group: h.group, href: h.href, hint: h.hint }));
      }}
      onNavigate={(href) => router.push(href)}
      onSignOut={() => void signOut()}
      statusBar={
        <StatusBarItems
          items={[
            { label: "Cell", value: <StatusBadge state={healthState} kind="Cell API" size="xs" /> },
            { label: "Company", value: tenant ? <StatusBadge state={tenant.status} kind="Company stage" size="xs" /> : <span className="font-mono">{subject.tenantId ?? "—"}</span> },
            ...(tenant ? [{ label: "Region", value: <RegionText cellId={tenant.home_cell_id} /> }] : []),
            {
              label: "Signed in",
              value: subject.authTime ? new Date(subject.authTime * 1000).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—",
            },
          ]}
        />
      }
    >
      {children}
      <ConsoleGuide />
      <PrivacySignal />
      <span className="sr-only" aria-live="polite">
        {healthState === "OUTAGE" ? "The cell API is unreachable." : ""}
      </span>
    </AppShell>
  );
}
