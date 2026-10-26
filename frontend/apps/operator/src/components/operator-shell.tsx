"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell, StatusBadge, StatusBarItems, type Command, type EnvironmentKind, type NavGroup } from "@zoiko/ui";
import { Activity, ClipboardCheck, Receipt, FlaskConical, KeyRound, LifeBuoy, Server, Siren, Stamp, TriangleAlert } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { useRuntime, type Subject } from "@/lib/runtime";

const NAV: NavGroup[] = [
  {
    label: "Operator plane",
    items: [
      { id: "requests", label: "My access", href: "/requests", icon: KeyRound },
      { id: "approvals", label: "Approvals", href: "/approvals", icon: Stamp },
      { id: "break-glass", label: "Break-glass", href: "/break-glass", icon: Siren },
      { id: "reviews", label: "Post-reviews", href: "/reviews", icon: ClipboardCheck },
    ],
  },
  {
    label: "Operations",
    items: [
      { id: "incidents", label: "Incidents", href: "/incidents", icon: TriangleAlert },
      { id: "services", label: "Services & SLOs", href: "/services", icon: Activity },
    ],
  },
  {
    label: "Commercial",
    items: [{ id: "commercial", label: "Commercial", href: "/commercial", icon: Receipt }],
  },
  {
    label: "Resilience",
    items: [
      { id: "cells", label: "Cells", href: "/cells", icon: Server },
      { id: "recovery", label: "Recovery operations", href: "/recovery", icon: LifeBuoy },
      { id: "exercises", label: "DR exercises", href: "/exercises", icon: FlaskConical },
    ],
  },
];

const COMMANDS: Command[] = NAV.flatMap((g) =>
  g.items.map((i) => ({ id: i.id, label: i.label, group: g.label ?? "Pages", href: i.href, icon: i.icon })),
);

function shortId(id: string) {
  return id.length > 14 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

export function OperatorShell({ subject, environment, children }: { subject: Subject; environment: EnvironmentKind; children: ReactNode }) {
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
      surface="Operator Console"
      homeHref="/requests"
      nav={NAV}
      currentPath={pathname}
      environment={environment}
      user={{ name: `Operator ${shortId(subject.sub)}`, email: subject.sub, roles: subject.acr ? [subject.acr] : undefined }}
      commands={COMMANDS}
      onNavigate={(href) => router.push(href)}
      onSignOut={() => void signOut()}
      statusBar={
        <StatusBarItems
          items={[
            { label: "Cell", value: <StatusBadge state={healthState} kind="Cell API" size="xs" /> },
            { label: "Operator tenant", value: <span className="font-mono">{subject.tenantId ?? "—"}</span> },
            {
              label: "Signed in",
              value: subject.authTime ? new Date(subject.authTime * 1000).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—",
            },
          ]}
        />
      }
    >
      {children}
    </AppShell>
  );
}
