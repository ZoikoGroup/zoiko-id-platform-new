"use client";

import { ApiError, type OnboardingGate, type TenantStatus, type TenantView } from "@zoiko/api-client";
import { Banner, Button, Code, StatusBadge, humanize } from "@zoiko/ui";
import { CheckCircle2, Circle, CircleAlert, CircleDashed, CircleX } from "lucide-react";

import { GuidedApproval } from "@/features/approvals/guided-approval";
import { ApiErrorView, useCellQuery } from "@/lib/cell";

export const TENANT_KEY = ["tenant", "me"];
export const GATES_KEY = ["onboarding", "gates"];
export const PLANS_KEY = ["onboarding", "plans"];
export const DOMAINS_KEY = ["tenant", "domains"];

/** The tenant lifecycle in order (OVERLAY-001 §2). BLOCKED / SUSPENDED / OFFBOARDING / CLOSED are
 * off the happy path and shown as a state, not a step. */
export const LIFECYCLE: { status: TenantStatus; label: string; meaning: string }[] = [
  { status: "DRAFT", label: "Registered", meaning: "The company workspace exists; nothing is trusted yet." },
  { status: "ORGANIZATION_VERIFYING", label: "Company verification", meaning: "The company and your authority to act for it are being established." },
  { status: "CONFIGURING", label: "Configuring", meaning: "Residency, domains, identity providers and administrators are being set up." },
  { status: "INTEGRATING", label: "Integrating", meaning: "Applications, connectors and enforcement points are being connected and tested." },
  { status: "SHADOW", label: "Shadow", meaning: "Zoiko iD evaluates decisions next to your current system but does not enforce them." },
  { status: "PILOT", label: "Pilot", meaning: "Decisions are enforced for an approved, scoped pilot only." },
  { status: "ACTIVE", label: "Live", meaning: "Production enforcement under the approved configuration." },
];

export function lifecycleIndex(status: TenantStatus | string | undefined): number {
  return LIFECYCLE.findIndex((s) => s.status === status);
}

/** The signed-in person's tenant, or null when they belong to none (or may not read it). */
export function useTenant() {
  return useCellQuery<TenantView | null>(TENANT_KEY, async (api) => {
    try {
      return await api.tenant.me();
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return null;
      throw e;
    }
  });
}

export type Standing = {
  isOwner: boolean;
  isAdmin: boolean; // OWNER or ADMIN, ACTIVE
  pendingOwner: boolean; // the bootstrap OWNER before the organization gate
  invited: boolean;
};

export function standing(t: TenantView | null | undefined): Standing {
  const roles = t?.my_roles ?? [];
  const active = (r: string) => roles.some((x) => x.role === r && x.status === "ACTIVE");
  return {
    isOwner: active("OWNER"),
    isAdmin: active("OWNER") || active("ADMIN"),
    pendingOwner: roles.some((x) => x.role === "OWNER" && x.status === "PENDING_VERIFICATION"),
    invited: roles.some((x) => x.status === "INVITED"),
  };
}

/* ------------------------------------------------------------------------------- gates */

const GATE_NAMES: Record<string, { label: string; href?: string }> = {
  "organization.root": { label: "Company verified against the official register", href: "/admin/organization" },
  "tenant.owner": { label: "An active owner is in place", href: "/admin/team" },
  "residency.placement": { label: "Residency profile and home cell set", href: "/admin/residency" },
  "tenant.domain": { label: "At least one company domain verified", href: "/admin/domains" },
};

export function gateLabel(id: string): string {
  return GATE_NAMES[id]?.label ?? humanize(id.replace(/\./g, "_"));
}

const GATE_ICON = { PASS: CheckCircle2, WARN: CircleAlert, FAIL: CircleX, PENDING: CircleDashed } as const;
const GATE_TONE = { PASS: "text-ok-fg", WARN: "text-warn-fg", FAIL: "text-danger-fg", PENDING: "text-ink-subtle" } as const;

/** Machine-readable onboarding requirements and their results; each links to where it is fixed. */
export function GateList({ gates, empty }: { gates: OnboardingGate[]; empty?: string }) {
  if (!gates.length) return <p className="text-sm text-ink-muted">{empty ?? "No gates apply at this stage."}</p>;
  return (
    <ul className="divide-y divide-border rounded-card border border-border">
      {gates.map((g) => {
        const Icon = GATE_ICON[g.status] ?? Circle;
        const href = GATE_NAMES[g.gate_id]?.href;
        return (
          <li key={`${g.stage}:${g.gate_id}`} className="flex items-start gap-3 px-4 py-3">
            <Icon aria-hidden className={`mt-0.5 size-5 shrink-0 ${GATE_TONE[g.status] ?? ""}`} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-ink">{gateLabel(g.gate_id)}</span>
                <StatusBadge state={g.status} kind="Gate" size="xs" />
                {!g.blocking ? <span className="text-xs text-ink-muted">advisory</span> : null}
              </div>
              {g.detail || g.code ? (
                <p className="mt-0.5 text-xs text-ink-muted">
                  {g.detail || humanize(g.code ?? "")}
                  {g.code && g.detail ? (
                    <>
                      {" "}
                      <Code>{g.code}</Code>
                    </>
                  ) : null}
                </p>
              ) : null}
            </div>
            {href && g.status !== "PASS" ? (
              <a href={href} className="shrink-0 text-sm font-medium text-brand-700 hover:underline">
                Fix
              </a>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function blockingCount(gates: OnboardingGate[]): number {
  return gates.filter((g) => g.blocking && g.status !== "PASS").length;
}

/* ------------------------------------------------- company details entered at registration */

export {
  carried,
  carryQuery,
  companyNumberValid,
  leiValid,
  normalizeCompanyNumber,
  type CarriedOrganization,
} from "./carry";

/* ------------------------------------------------- second-administrator approval (four eyes) */

/**
 * A step that needs a second administrator (OVERLAY-001 §4; AUTH approvals). The person asks with one button and the
 * page follows the answer; once another administrator approves, the step's own button appears. No request identifier
 * or context string is typed or pasted (see GuidedApproval).
 */
export function ApprovalStep({
  action,
  resource,
  change,
  label,
  summary,
  onRun,
  pending,
  error,
}: {
  action: string;
  resource: string;
  change: string;
  label: string;
  /** What is being approved, in words, as the approver will read it. Defaults to the label and the target. */
  summary?: string;
  onRun: (approvalRequestId: string) => void;
  pending: boolean;
  error: unknown;
}) {
  // Owner approval (CR-036): the company's owner needs no second administrator, so the step is one button. The cell
  // decides who may; everyone else sees the usual request-and-wait flow below.
  const mode = useCellQuery(["onboarding", "mode"], (api) => api.onboarding.mode());
  if (mode.data?.self_approve) {
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" pending={pending} onPress={() => onRun("")}>
            {label}
          </Button>
          <span className="text-xs text-ink-muted">You approve this as the company owner. No second administrator is needed.</span>
        </div>
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <GuidedApproval
        action={action}
        resource={resource}
        change={change}
        label={label}
        summary={summary ?? `${label} (${action.replaceAll(".", " ")}${change ? `: ${change}` : ""})`}
      >
        {(id) => (
          <Button size="sm" pending={pending} onPress={() => onRun(id)}>
            {label}
          </Button>
        )}
      </GuidedApproval>
      {error ? <ApiErrorView error={error} /> : null}
    </div>
  );
}

export function DevelopmentNotice() {
  return (
    <Banner tone="warn" icon={CircleAlert} title="Development verification">
      This workspace was verified with a synthetic, self-labelled development record (local/test only). In production the company is checked
      against the official register and your identity is matched to one of its officers.
    </Banner>
  );
}
