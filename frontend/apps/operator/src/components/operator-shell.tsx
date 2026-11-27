"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell, StatusBadge, StatusBarItems, type Command, type EnvironmentKind, type NavGroup } from "@zoiko/ui";
import {
  Activity,
  ClipboardCheck,
  Flag,
  FlaskConical,
  Globe2,
  KeyRound,
  LifeBuoy,
  ListChecks,
  Megaphone,
  Receipt,
  ScanFace,
  Server,
  ShieldQuestion,
  Siren,
  Stamp,
  TriangleAlert,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { useDirectMode } from "@/features/commercial/mode";
import { useRuntime, type Subject } from "@/lib/runtime";

/** The menu, by what an operator does for this product. Pages for features the product has turned off or never used are not listed: biometric method approval, the face lab, enforce readiness, service offerings,
 * regulatory suppliers, holds, issuers, trust lists, conformance, WF-05 check methods, regulated claims, screening providers and screening release gates. Their pages still exist and open by their own address;
 * only the menu entry is gone. Add one back here if a deployment starts using it. */
const NAV: NavGroup[] = [
  {
    label: "Identity verification",
    items: [{ id: "identity", label: "Identity verification", href: "/identity-verification", icon: ScanFace }],
  },
  {
    label: "Operator plane",
    items: [
      { id: "requests", label: "My access", href: "/requests", icon: KeyRound },
      { id: "approvals", label: "Approvals", href: "/approvals", icon: Stamp },
      { id: "break-glass", label: "Break-glass", href: "/break-glass", icon: Siren },
      { id: "reviews", label: "Post-reviews", href: "/reviews", icon: ClipboardCheck },
      { id: "signals", label: "Signals", href: "/signals", icon: Flag },
      { id: "case-reviews", label: "Case reviews", href: "/case-reviews", icon: ClipboardCheck },
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
    label: "Countries and screening",
    items: [
      { id: "reg-countries", label: "Country features", href: "/regulatory/countries", icon: Globe2 },
      { id: "screening-cases", label: "Screening cases", href: "/screening/cases", icon: ClipboardCheck },
      { id: "screening-sources", label: "Sanctions sources", href: "/screening/sources", icon: ShieldQuestion },
    ],
  },
  {
    label: "Trust Center",
    items: [
      { id: "trust-claims", label: "Public claims", href: "/trust-center/claims", icon: Megaphone },
      { id: "trust-ops", label: "Trust operations", href: "/trust-center/operations", icon: ListChecks },
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

/** One operator account manages everything that does not touch customer data, with no access request and no second approver (direct mode). The pages that exist to
 * request, approve and review access are then not shown: nobody is there to ask. A governed deployment keeps them. */
function navFor(direct: boolean): NavGroup[] {
  return direct ? NAV.filter((g) => g.label !== "Operator plane") : NAV;
}

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
  const direct = useDirectMode();
  const nav = navFor(direct);
  const commands = direct ? COMMANDS.filter((c) => nav.some((g) => g.items.some((i) => i.id === c.id))) : COMMANDS;
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
      homeHref={direct ? "/identity-verification" : "/requests"}
      nav={nav}
      currentPath={pathname}
      environment={environment}
      user={{ name: direct ? "Operator" : `Operator ${shortId(subject.sub)}`, email: subject.sub, roles: direct ? ["Single operator"] : subject.acr ? [subject.acr] : undefined }}
      commands={commands}
      onNavigate={(href) => router.push(href)}
      onSignOut={() => void signOut()}
      statusBar={
        <StatusBarItems
          items={[
            { label: "Cell", value: <StatusBadge state={healthState} kind="Cell API" size="xs" /> },
            ...(direct ? [{ label: "Mode", value: <span>Single operator</span> }] : []),
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
