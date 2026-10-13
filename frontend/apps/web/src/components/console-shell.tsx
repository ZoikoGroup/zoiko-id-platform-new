"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell, StatusBarItems, StatusBadge, type Command, type EnvironmentKind, type NavGroup } from "@zoiko/ui";
import {
  BadgeCheck,
  Building2,
  Fingerprint,
  Gavel,
  KeyRound,
  LayoutDashboard,
  LifeBuoy,
  Link2,
  MonitorSmartphone,
  RadioTower,
  Scale,
  ScanFace,
  ShieldCheck,
  Users,
  Wallet,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { useRuntime, type Subject } from "@/lib/runtime";

/** Navigation mirrors the cell's real API surface. The Administration group is shown to every
 * signed-in user: the cell decides per action (authority grants, assurance, approvals), and each
 * page explains a refusal rather than hiding the area. */
const NAV: NavGroup[] = [
  {
    label: "My account",
    items: [
      { id: "account", label: "Overview", href: "/account", icon: LayoutDashboard },
      { id: "sessions", label: "Sessions", href: "/account/sessions", icon: MonitorSmartphone },
      { id: "passkeys", label: "Passkeys", href: "/account/passkeys", icon: Fingerprint },
      { id: "connected-apps", label: "Connected apps", href: "/account/connected-apps", icon: Link2 },
      { id: "my-verification", label: "Identity verification", href: "/account/verification", icon: ScanFace },
      { id: "recovery-codes", label: "Recovery codes", href: "/account/recovery", icon: LifeBuoy },
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
      { id: "accounts", label: "Sessions & recovery", href: "/admin/accounts", icon: KeyRound },
      { id: "provisioning", label: "Provisioning", href: "/admin/provisioning", icon: Users },
    ],
  },
];

const COMMANDS: Command[] = NAV.flatMap((g) =>
  g.items.map((i) => ({ id: i.id, label: i.label, group: g.label ?? "Pages", href: i.href, icon: i.icon })),
);

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
  const { signOut } = useRuntime();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => (await (await fetch("/api/health", { cache: "no-store" })).json()) as { status: string },
    refetchInterval: 60_000,
  });
  const status = health.data?.status;
  const healthState = status === "ready" || status === "ok" ? "HEALTHY" : status === "degraded" ? "DEGRADED" : status ? "OUTAGE" : "UNKNOWN";

  return (
    <AppShell
      surface="Identity & trust"
      homeHref="/account"
      nav={NAV}
      currentPath={pathname}
      tenant={subject.tenantId ? { id: subject.tenantId, name: shortId(subject.tenantId) } : undefined}
      environment={environment}
      user={{ name: `Principal ${shortId(subject.sub)}`, email: subject.sub, roles: subject.acr ? [subject.acr] : undefined }}
      commands={COMMANDS}
      onNavigate={(href) => router.push(href)}
      onSignOut={() => void signOut()}
      statusBar={
        <StatusBarItems
          items={[
            { label: "Cell", value: <StatusBadge state={healthState} kind="Cell API" size="xs" /> },
            { label: "Tenant", value: <span className="font-mono">{subject.tenantId ?? "—"}</span> },
            {
              label: "Signed in",
              value: subject.authTime ? new Date(subject.authTime * 1000).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—",
            },
          ]}
        />
      }
    >
      {children}
      <span className="sr-only" aria-live="polite">
        {healthState === "OUTAGE" ? "The cell API is unreachable." : ""}
      </span>
    </AppShell>
  );
}
