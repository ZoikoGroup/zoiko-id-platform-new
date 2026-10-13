"use client";

/** Loading, empty, error, degraded and notification states (ZID-UX-001 §20, §24). */
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  CloudOff,
  FilterX,
  GitMerge,
  Hourglass,
  Info,
  Inbox,
  Lock,
  ShieldOff,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { cn } from "../utils/cn";
import { Button } from "./button";

const bannerTone = {
  info: { cls: "bg-info-bg border-info-fg/20", fg: "text-info-fg", icon: Info },
  ok: { cls: "bg-ok-bg border-ok-fg/20", fg: "text-ok-fg", icon: CheckCircle2 },
  warn: { cls: "bg-warn-bg border-warn-fg/25", fg: "text-warn-fg", icon: AlertTriangle },
  danger: { cls: "bg-danger-bg border-danger-fg/20", fg: "text-danger-fg", icon: XCircle },
} as const;

export type BannerTone = keyof typeof bannerTone;

/** Operational/security banner with an action. `onDismiss` only where dismissal policy allows (§20). */
export function Banner({
  tone = "info",
  title,
  children,
  action,
  onDismiss,
  icon,
  className,
  live,
}: {
  tone?: BannerTone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  onDismiss?: () => void;
  icon?: LucideIcon;
  className?: string;
  /** Announce politely/assertively; defaults to none for static page notices. */
  live?: "polite" | "assertive";
}) {
  const t = bannerTone[tone];
  const Icon = icon ?? t.icon;
  return (
    <div
      role={live === "assertive" ? "alert" : live === "polite" ? "status" : undefined}
      className={cn("flex items-start gap-3 rounded-lg border px-4 py-3 text-sm", t.cls, className)}
    >
      <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", t.fg)} />
      <div className="min-w-0 flex-1">
        {title ? <div className={cn("font-semibold", t.fg)}>{title}</div> : null}
        {children ? <div className={cn(title && "mt-0.5", "text-ink-muted")}>{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
      {onDismiss ? (
        <button type="button" onClick={onDismiss} aria-label="Dismiss" className="shrink-0 rounded p-0.5 text-ink-subtle hover:bg-surface/60 hover:text-ink">
          <X aria-hidden className="size-4" />
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
  icon: Icon = Inbox,
  compact = false,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
  compact?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 text-center", compact ? "py-8" : "py-14")}>
      <span className="flex size-11 items-center justify-center rounded-full bg-sunken">
        <Icon aria-hidden className="size-5 text-ink-subtle" />
      </span>
      <h3 className="mt-3 text-sm font-semibold text-ink">{title}</h3>
      {children ? <p className="mt-1 max-w-md text-sm text-ink-muted">{children}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/** §24 Empty: say *why* nothing shows - none exist, filters hide them, or the viewer lacks scope. */
export function ListEmpty({
  reason,
  noun,
  action,
  onClearFilters,
}: {
  reason: "none" | "filtered" | "scope";
  noun: string;
  action?: ReactNode;
  onClearFilters?: () => void;
}) {
  if (reason === "filtered")
    return (
      <EmptyState
        icon={FilterX}
        title={`No ${noun} match these filters`}
        action={onClearFilters ? <Button variant="secondary" size="sm" onPress={onClearFilters}>Clear filters</Button> : undefined}
      >
        Matching objects may exist outside the current filters.
      </EmptyState>
    );
  if (reason === "scope")
    return (
      <EmptyState icon={Lock} title={`No ${noun} in your scope`}>
        Your role may not include these objects. An administrator can review your access.
      </EmptyState>
    );
  return (
    <EmptyState title={`No ${noun} yet`} action={action}>
      Nothing has been created in this tenant and environment.
    </EmptyState>
  );
}

/** §24 Unauthorized / not found: generic, no protected-object existence, correlation id only. */
export function AccessUnavailable({ correlationId }: { correlationId?: string }) {
  return (
    <EmptyState icon={ShieldOff} title="This page isn't available">
      It may not exist, or your current role may not include it.
      {correlationId ? (
        <span className="mt-2 block text-xs">
          Support reference: <span className="font-mono">{correlationId}</span>
        </span>
      ) : null}
    </EmptyState>
  );
}

/** §24 Stale: last known state, its age and whether actions are blocked pending refresh. */
export function StaleNotice({
  age,
  blocksActions,
  onRefresh,
}: {
  age: ReactNode;
  blocksActions?: boolean;
  onRefresh?: () => void;
}) {
  return (
    <Banner
      tone="warn"
      icon={Clock}
      title="Showing last known state"
      action={onRefresh ? <Button size="xs" variant="secondary" onPress={onRefresh}>Refresh</Button> : undefined}
    >
      Last updated {age}.{" "}
      {blocksActions ? "Changes are blocked until the state is refreshed." : "Values may have changed since."}
    </Banner>
  );
}

/** §24 Partial data: explicitly name the missing source; never treat missing as zero. */
export function PartialDataNotice({ missing }: { missing: string[] }) {
  return (
    <Banner tone="warn" icon={CloudOff} title="Some sources did not respond">
      Not included: {missing.join(", ")}. Totals below exclude these sources and are not zero.
    </Banner>
  );
}

/** §24 Degraded dependency: capability impact, not vendor internals. */
export function DegradedNotice({ capability, impact, action }: { capability: string; impact: string; action?: ReactNode }) {
  return (
    <Banner tone="warn" icon={CloudOff} title={`${capability} is degraded`} action={action}>
      {impact}
    </Banner>
  );
}

/** §24 Conflict / UX-06: expected vs current version, refresh or reapply - never overwrite. */
export function ConflictNotice({
  expected,
  current,
  onRefresh,
  onReapply,
}: {
  expected: string | number;
  current: string | number;
  onRefresh: () => void;
  onReapply?: () => void;
}) {
  return (
    <Banner
      tone="warn"
      icon={GitMerge}
      live="assertive"
      title="This object changed while you were editing"
      action={
        <div className="flex gap-2">
          <Button size="xs" variant="secondary" onPress={onRefresh}>
            Load latest
          </Button>
          {onReapply ? (
            <Button size="xs" onPress={onReapply}>
              Reapply my changes
            </Button>
          ) : null}
        </div>
      }
    >
      You edited version <span className="font-mono">{expected}</span>; the current version is{" "}
      <span className="font-mono">{current}</span>. Nothing was saved.
    </Banner>
  );
}

/** §24 Rate limited: explain retry-after; the caller keeps the draft. */
export function RateLimitedNotice({ retryAfterSeconds }: { retryAfterSeconds: number }) {
  return (
    <Banner tone="info" icon={Hourglass} live="polite" title="Too many requests">
      Try again in {retryAfterSeconds} seconds. Your unsaved changes are kept.
    </Banner>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-sunken", className)} />;
}

export function LoadingRows({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

/** Any thrown error, shown with its canonical code and a support correlation id - never a stack. */
export function ErrorState({
  error,
  title = "Something went wrong",
  onRetry,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}) {
  const e = error as { code?: string; message?: string; correlationId?: string; details?: string[] } | null;
  return (
    <Banner
      tone="danger"
      title={title}
      live="assertive"
      action={onRetry ? <Button size="xs" variant="secondary" onPress={onRetry}>Try again</Button> : undefined}
    >
      <span className="block">{e?.message ?? "The request failed."}</span>
      {e?.code ? (
        <span className="mt-1 block font-mono text-xs">
          {e.code}
          {e.details && e.details.length ? ` · ${e.details.join(", ")}` : ""}
        </span>
      ) : null}
      {e?.correlationId ? (
        <span className="mt-1 block text-xs">
          Support reference: <span className="font-mono">{e.correlationId}</span>
        </span>
      ) : null}
    </Banner>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Toasts: confirmation for low-risk inline saves (§26). Announced via a polite live region.
 * ---------------------------------------------------------------------------------------------- */

type Toast = { id: number; tone: BannerTone; title: string; body?: string };
type ToastApi = { notify: (t: Omit<Toast, "id">) => void };

const ToastContext = createContext<ToastApi>({ notify: () => undefined });

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const notify = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((ts) => [...ts.slice(-2), { ...t, id }]);
    setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 6000);
  }, []);
  const api = useMemo(() => ({ notify }), [notify]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div aria-live="polite" role="region" aria-label="Notifications" className="pointer-events-none fixed bottom-4 end-4 z-[60] flex w-80 flex-col gap-2">
        {toasts.map((t) => {
          const tone = bannerTone[t.tone];
          const Icon = tone.icon;
          return (
            <div
              key={t.id}
              className="pointer-events-auto flex animate-slide-in-end items-start gap-3 rounded-lg border border-border bg-raised p-3 text-sm shadow-pop"
            >
              <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", tone.fg)} />
              <div className="min-w-0 flex-1">
                <div className="font-medium text-ink">{t.title}</div>
                {t.body ? <div className="mt-0.5 text-ink-muted">{t.body}</div> : null}
              </div>
              <button
                type="button"
                aria-label="Dismiss notification"
                className="rounded p-0.5 text-ink-subtle hover:text-ink"
                onClick={() => setToasts((ts) => ts.filter((x) => x.id !== t.id))}
              >
                <X aria-hidden className="size-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

/** Progress meter with text value (never bar-only). */
export function Meter({
  value,
  max = 100,
  label,
  tone = "brand",
  valueText,
}: {
  value: number;
  max?: number;
  label: string;
  tone?: "brand" | "ok" | "warn" | "danger";
  valueText?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const bar = { brand: "bg-brand-600", ok: "bg-ok-solid", warn: "bg-warn-solid", danger: "bg-danger-solid" }[tone];
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="font-medium text-ink-muted">{label}</span>
        <span className="tabular font-semibold text-ink">{valueText ?? `${Math.round(pct)}%`}</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuetext={valueText}
        className="h-2 overflow-hidden rounded-full bg-sunken"
      >
        <div className={cn("h-full rounded-full", bar)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
