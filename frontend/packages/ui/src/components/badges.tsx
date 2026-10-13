/** Canonical-state badges (ZID-UX-001 §4, §36): text + icon + accessible label, never color only.
 *
 * Kept deliberately separate: cryptographic validity, verification, authentication assurance,
 * authority and risk are different facts, so "green" never collapses them into "authorized". */
import {
  AlertOctagon,
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDashed,
  Clock,
  FlaskConical,
  GitCommitVertical,
  HelpCircle,
  Info,
  Loader,
  PauseCircle,
  Receipt,
  ShieldAlert,
  ShieldCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../utils/cn";

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral" | "brand";

const toneClass: Record<Tone, string> = {
  ok: "bg-ok-bg text-ok-fg ring-ok-fg/20",
  warn: "bg-warn-bg text-warn-fg ring-warn-fg/25",
  danger: "bg-danger-bg text-danger-fg ring-danger-fg/20",
  info: "bg-info-bg text-info-fg ring-info-fg/20",
  neutral: "bg-neutral-bg text-neutral-fg ring-neutral-fg/15",
  brand: "bg-brand-50 text-brand-700 ring-brand-600/20",
};

export function Badge({
  tone = "neutral",
  icon: Icon,
  children,
  label,
  className,
  size = "sm",
}: {
  tone?: Tone;
  icon?: LucideIcon;
  children: ReactNode;
  /** Full accessible text when the visible text is abbreviated. */
  label?: string;
  className?: string;
  size?: "xs" | "sm";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md font-medium ring-1 ring-inset",
        size === "xs" ? "px-1.5 py-px text-2xs" : "px-2 py-0.5 text-xs",
        toneClass[tone],
        className,
      )}
    >
      {Icon ? <Icon aria-hidden className={size === "xs" ? "size-3" : "size-3.5"} /> : null}
      {label ? (
        <>
          <span aria-hidden>{children}</span>
          <span className="sr-only">{label}</span>
        </>
      ) : (
        children
      )}
    </span>
  );
}

type StateDef = { tone: Tone; icon: LucideIcon };

/** Canonical states across domains. Unknown values render neutral with a "?" icon, never green. */
const STATES: Record<string, StateDef> = {
  ACTIVE: { tone: "ok", icon: CheckCircle2 },
  APPROVED: { tone: "ok", icon: CheckCircle2 },
  VERIFIED: { tone: "ok", icon: CheckCircle2 },
  VALID: { tone: "ok", icon: CheckCircle2 },
  PASS: { tone: "ok", icon: CheckCircle2 },
  READY: { tone: "ok", icon: CheckCircle2 },
  COMPLETED: { tone: "ok", icon: CheckCircle2 },
  COMMITTED: { tone: "ok", icon: CheckCircle2 },
  ENABLED: { tone: "ok", icon: CheckCircle2 },
  TRUSTED: { tone: "ok", icon: ShieldCheck },
  HEALTHY: { tone: "ok", icon: CheckCircle2 },
  NORMAL: { tone: "ok", icon: CheckCircle2 },
  ALLOW: { tone: "ok", icon: CheckCircle2 },
  PERMIT: { tone: "ok", icon: CheckCircle2 },
  GRANTED: { tone: "ok", icon: CheckCircle2 },
  RESOLVED: { tone: "ok", icon: CheckCircle2 },
  PENDING: { tone: "info", icon: Clock },
  REQUESTED: { tone: "info", icon: Clock },
  SCHEDULED: { tone: "info", icon: Clock },
  RESERVED: { tone: "info", icon: Clock },
  IN_PROGRESS: { tone: "info", icon: Loader },
  RUNNING: { tone: "info", icon: Loader },
  VALIDATING: { tone: "info", icon: Loader },
  REVIEW: { tone: "info", icon: Clock },
  NEEDS_REVIEW: { tone: "info", icon: Clock },
  PENDING_REPROOF: { tone: "info", icon: Clock },
  PENDING_APPROVAL: { tone: "info", icon: Clock },
  ACKNOWLEDGED: { tone: "info", icon: Info },
  OPEN: { tone: "info", icon: Info },
  DRAFT: { tone: "neutral", icon: CircleDashed },
  COOLING_OFF: { tone: "warn", icon: Clock },
  PAUSED: { tone: "warn", icon: PauseCircle },
  SUSPENDED: { tone: "warn", icon: PauseCircle },
  DEGRADED: { tone: "warn", icon: AlertTriangle },
  STEP_UP_REQUIRED: { tone: "warn", icon: AlertTriangle },
  REAUTH_REQUIRED: { tone: "warn", icon: AlertTriangle },
  REPROOF_REQUIRED: { tone: "warn", icon: AlertTriangle },
  REPROOF_DUE: { tone: "warn", icon: AlertTriangle },
  ELEVATED_RISK: { tone: "warn", icon: AlertTriangle },
  RESTRICTED: { tone: "warn", icon: ShieldAlert },
  QUARANTINED: { tone: "warn", icon: ShieldAlert },
  STALE: { tone: "warn", icon: AlertTriangle },
  CONFLICTED: { tone: "warn", icon: AlertOctagon },
  EXECUTION_UNKNOWN: { tone: "warn", icon: HelpCircle },
  EXPIRING: { tone: "warn", icon: Clock },
  EXPIRED: { tone: "neutral", icon: Clock },
  RETIRED: { tone: "neutral", icon: Ban },
  DISABLED: { tone: "neutral", icon: Ban },
  RELEASED: { tone: "neutral", icon: Ban },
  CANCELLED: { tone: "neutral", icon: Ban },
  CLOSED: { tone: "neutral", icon: Ban },
  REVOKED: { tone: "danger", icon: XCircle },
  REJECTED: { tone: "danger", icon: XCircle },
  DENIED: { tone: "danger", icon: XCircle },
  DENY: { tone: "danger", icon: XCircle },
  FAILED: { tone: "danger", icon: XCircle },
  FAIL: { tone: "danger", icon: XCircle },
  INVALID: { tone: "danger", icon: XCircle },
  UNTRUSTED: { tone: "danger", icon: ShieldAlert },
  COMPROMISED: { tone: "danger", icon: ShieldAlert },
  OUTAGE: { tone: "danger", icon: AlertOctagon },
  UNKNOWN: { tone: "neutral", icon: HelpCircle },
};

export function humanize(state: string): string {
  const s = state.replace(/[_.]/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function stateTone(state: string | null | undefined): Tone {
  return STATES[(state ?? "UNKNOWN").toUpperCase()]?.tone ?? "neutral";
}

/** A canonical state, e.g. ACTIVE, REVOKED, COOLING_OFF. Unknown states render neutral. */
export function StatusBadge({
  state,
  kind,
  size,
}: {
  state: string | null | undefined;
  /** What the state is about ("Credential status", "Issuer trust"...), read to assistive tech. */
  kind?: string;
  size?: "xs" | "sm";
}) {
  const value = state ?? "UNKNOWN";
  const def = STATES[value.toUpperCase()] ?? { tone: "neutral" as Tone, icon: HelpCircle };
  return (
    <Badge tone={def.tone} icon={def.icon} size={size} label={`${kind ? `${kind}: ` : ""}${humanize(value)}`}>
      {humanize(value)}
    </Badge>
  );
}

const RISK: Record<string, { tone: Tone; text: string }> = {
  R0: { tone: "neutral", text: "Low" },
  R1: { tone: "info", text: "Moderate" },
  R2: { tone: "warn", text: "High" },
  R3: { tone: "danger", text: "Critical action" },
};

/** Action sensitivity R0–R3 plus a plain-language descriptor. Not incident severity (§5.1). */
export function RiskBadge({ risk, compact = false }: { risk: string | number; compact?: boolean }) {
  const key = typeof risk === "number" ? `R${risk}` : risk.toUpperCase();
  const r = RISK[key] ?? { tone: "neutral" as Tone, text: "Unrated" };
  return (
    <Badge tone={r.tone} icon={ShieldAlert} label={`Action risk ${key}: ${r.text}`}>
      {compact ? key : `${key} · ${r.text}`}
    </Badge>
  );
}

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "INFO";

const SEVERITY: Record<Severity, { tone: Tone; icon: LucideIcon }> = {
  CRITICAL: { tone: "danger", icon: AlertOctagon },
  HIGH: { tone: "warn", icon: AlertTriangle },
  MEDIUM: { tone: "info", icon: Info },
  INFO: { tone: "neutral", icon: Info },
};

/** Operational/incident severity (§20). Deliberately a different shape from RiskBadge. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const s = SEVERITY[severity];
  return (
    <Badge tone={s.tone} icon={s.icon} label={`Severity ${humanize(severity)}`} className="uppercase tracking-wide">
      {severity}
    </Badge>
  );
}

/** Authentication (AAL-Z*), identity (IAL-Z*), authority (AR-*) or workload (WIA-Z*) assurance. */
export function AssuranceBadge({
  kind,
  level,
}: {
  kind: "Authentication" | "Identity" | "Authority" | "Workload";
  level: string | number | null | undefined;
}) {
  const prefix = { Authentication: "AAL-Z", Identity: "IAL-Z", Authority: "AR-", Workload: "WIA-Z" }[kind];
  const text = level === null || level === undefined ? "None" : typeof level === "number" ? `${prefix}${level}` : level;
  return (
    <Badge tone="brand" label={`${kind} assurance ${text}`}>
      <span className="font-normal opacity-75">{kind.slice(0, 4)}.</span> {text}
    </Badge>
  );
}

/** Evidence class E0–E3 plus whether a decision receipt can be retrieved. */
export function EvidenceChip({ evidenceClass, receipt }: { evidenceClass: string; receipt?: boolean }) {
  return (
    <Badge
      tone="neutral"
      icon={Receipt}
      label={`Evidence class ${evidenceClass}${receipt === undefined ? "" : receipt ? ", receipt available" : ", no receipt"}`}
    >
      {evidenceClass}
      {receipt === undefined ? null : receipt ? " · receipt" : " · no receipt"}
    </Badge>
  );
}

/** Security / policy / root / relationship revision, shown where troubleshooting needs it. */
export function RevisionChip({ kind, value }: { kind: string; value: string | number }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-1.5 py-px font-mono text-2xs text-ink-muted"
      aria-label={`${kind} revision ${value}`}
    >
      <GitCommitVertical aria-hidden className="size-3" />
      {kind} {value}
    </span>
  );
}

export type EnvironmentKind = "production" | "sandbox" | "test" | "development";

/** Environment label; production also gets a hatched marker so it survives monochrome (UX-04, UX-20). */
export function EnvironmentBadge({ environment }: { environment: EnvironmentKind }) {
  if (environment === "production") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-env-prod/40 bg-danger-bg px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-env-prod">
        <span aria-hidden className="env-hatch size-2.5 rounded-sm" />
        Production
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-env-sandbox/50 bg-info-bg px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-env-sandbox">
      <FlaskConical aria-hidden className="size-3.5" />
      {environment}
    </span>
  );
}

/** Small "provider-derived" qualifier so third-party scores are never read as Zoiko truth (§8.1). */
export function ProviderDerived({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      {children}
      <span className="text-2xs font-medium uppercase tracking-wide text-ink-subtle">provider-derived</span>
    </span>
  );
}
