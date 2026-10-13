"use client";

import { ChevronRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Breadcrumb, Breadcrumbs as AriaBreadcrumbs, Link as AriaLink } from "react-aria-components";

import { cn } from "../utils/cn";

export type Crumb = { label: string; href?: string };

/** Breadcrumbs follow the object hierarchy, not internal routes (§3.1). */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <AriaBreadcrumbs className="flex flex-wrap items-center gap-1 text-sm text-ink-subtle">
      {items.map((c, i) => (
        <Breadcrumb key={`${c.label}-${i}`} className="flex items-center gap-1">
          {c.href && i < items.length - 1 ? (
            <AriaLink href={c.href} className="rounded-sm outline-none hover:text-ink hover:underline focus-visible:ring-2 focus-visible:ring-brand-500">
              {c.label}
            </AriaLink>
          ) : (
            <span aria-current={i === items.length - 1 ? "page" : undefined} className="text-ink-muted">
              {c.label}
            </span>
          )}
          {i < items.length - 1 ? <ChevronRight aria-hidden className="size-3.5 rtl:rotate-180" /> : null}
        </Breadcrumb>
      ))}
    </AriaBreadcrumbs>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  breadcrumbs,
  meta,
  icon: Icon,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  breadcrumbs?: Crumb[];
  /** Status/assurance/revision badges shown under the title (object headers). */
  meta?: ReactNode;
  icon?: LucideIcon;
}) {
  return (
    <header className="mb-6">
      {breadcrumbs?.length ? (
        <div className="mb-2">
          <Breadcrumbs items={breadcrumbs} />
        </div>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          {Icon ? (
            <span className="mt-0.5 flex size-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface text-ink-muted shadow-card">
              <Icon aria-hidden className="size-5" />
            </span>
          ) : null}
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-2xl">{title}</h1>
            {description ? <p className="mt-1 max-w-3xl text-sm text-ink-muted">{description}</p> : null}
            {meta ? <div className="mt-2 flex flex-wrap items-center gap-1.5">{meta}</div> : null}
          </div>
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  className,
  padded = true,
  footer,
  as: As = "section",
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  padded?: boolean;
  footer?: ReactNode;
  as?: "section" | "div" | "article";
}) {
  return (
    <As className={cn("min-w-0 rounded-card border border-border bg-surface shadow-card", className)}>
      {title || actions ? (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-3.5">
          <div className="min-w-0">
            {title ? <h2 className="text-sm font-semibold text-ink">{title}</h2> : null}
            {description ? <p className="mt-0.5 text-sm text-ink-muted">{description}</p> : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className={padded ? "p-5" : undefined}>{children}</div>
      {footer ? <div className="border-t border-border px-5 py-3">{footer}</div> : null}
    </As>
  );
}

/** A label/value grid for object facts. */
export function KeyValues({
  items,
  columns = 2,
}: {
  items: [ReactNode, ReactNode][];
  columns?: 1 | 2 | 3 | 4;
}) {
  return (
    <dl
      className={cn(
        "grid gap-x-6 gap-y-4",
        columns === 1 && "grid-cols-1",
        columns === 2 && "sm:grid-cols-2",
        columns === 3 && "sm:grid-cols-2 lg:grid-cols-3",
        columns === 4 && "sm:grid-cols-2 lg:grid-cols-4",
      )}
    >
      {items.map(([k, v], i) => (
        <div key={i} className="min-w-0">
          <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">{k}</dt>
          <dd className="mt-1 break-words text-sm text-ink">{v ?? <span className="text-ink-subtle">—</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Summary number that drills into a filtered canonical list (§5.1). */
export function MetricCard({
  label,
  value,
  href,
  icon: Icon,
  lines,
  tone,
}: {
  label: string;
  value: ReactNode;
  href?: string;
  icon?: LucideIcon;
  /** Secondary facts, each optionally drillable. */
  lines?: { label: ReactNode; href?: string; tone?: "warn" | "danger" | "ok" }[];
  tone?: "warn" | "danger";
}) {
  const head = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-ink-muted">{label}</span>
        {Icon ? <Icon aria-hidden className="size-4 text-ink-subtle" /> : null}
      </div>
      <div
        className={cn(
          "tabular mt-2 text-2xl font-semibold tracking-tight",
          tone === "danger" ? "text-danger-fg" : tone === "warn" ? "text-warn-fg" : "text-ink",
        )}
      >
        {value}
      </div>
    </>
  );
  return (
    <div className="flex min-w-0 flex-col rounded-card border border-border bg-surface p-4 shadow-card">
      {href ? (
        <AriaLink href={href} className="-m-1 rounded-md p-1 outline-none hover:bg-hover/60 focus-visible:ring-2 focus-visible:ring-brand-500">
          {head}
        </AriaLink>
      ) : (
        head
      )}
      {lines?.length ? (
        <ul className="mt-3 space-y-1 border-t border-border pt-3 text-sm">
          {lines.map((l, i) => {
            const cls = cn(
              "flex items-center gap-1.5",
              l.tone === "danger" ? "text-danger-fg" : l.tone === "warn" ? "text-warn-fg" : "text-ink-muted",
            );
            return (
              <li key={i}>
                {l.href ? (
                  <AriaLink href={l.href} className={cn(cls, "rounded-sm outline-none hover:underline focus-visible:ring-2 focus-visible:ring-brand-500")}>
                    {l.label}
                  </AriaLink>
                ) : (
                  <span className={cls}>{l.label}</span>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

/** Kept for simple facts without drill-down. */
export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-card border border-border bg-surface p-4 shadow-card">
      <div className="text-sm text-ink-muted">{label}</div>
      <div className="tabular mt-1 text-2xl font-semibold text-ink">{value}</div>
      {hint ? <div className="mt-1 text-xs text-ink-subtle">{hint}</div> : null}
    </div>
  );
}

/** Horizontal summary strip: "Needs review 12 | Reproof due 38 | ..." - each item drills (§8). */
export function SummaryStrip({
  items,
  label,
}: {
  items: { label: string; value: ReactNode; href?: string; tone?: "warn" | "danger" | "ok" }[];
  label: string;
}) {
  return (
    <ul aria-label={label} className="grid grid-cols-2 gap-px overflow-hidden rounded-card border border-border bg-border shadow-card sm:grid-cols-3 lg:flex">
      {items.map((it) => {
        const inner = (
          <>
            <span className="text-xs font-medium text-ink-muted">{it.label}</span>
            <span
              className={cn(
                "tabular text-lg font-semibold",
                it.tone === "danger" ? "text-danger-fg" : it.tone === "warn" ? "text-warn-fg" : it.tone === "ok" ? "text-ok-fg" : "text-ink",
              )}
            >
              {it.value}
            </span>
          </>
        );
        return (
          <li key={it.label} className="min-w-0 flex-1 bg-surface">
            {it.href ? (
              <AriaLink href={it.href} className="flex h-full flex-col gap-0.5 px-4 py-3 outline-none hover:bg-hover/60 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500">
                {inner}
              </AriaLink>
            ) : (
              <div className="flex flex-col gap-0.5 px-4 py-3">{inner}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Link-based section navigation (object tabs that are real URLs, e.g. /people/:id/authority). */
export function SectionNav({
  items,
  label,
}: {
  items: { label: string; href: string; current?: boolean; count?: number }[];
  label: string;
}) {
  return (
    <nav aria-label={label} className="mb-5 overflow-x-auto border-b border-border">
      <ul className="flex min-w-max gap-1">
        {items.map((it) => (
          <li key={it.href}>
            <AriaLink
              href={it.href}
              aria-current={it.current ? "page" : undefined}
              className="-mb-px flex items-center gap-1.5 border-b-2 border-transparent px-3 py-2 text-sm font-medium text-ink-muted outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 current:border-brand-600 current:text-brand-700"
            >
              {it.label}
              {it.count !== undefined ? (
                <span className="tabular rounded-full bg-sunken px-1.5 text-2xs text-ink-muted">{it.count}</span>
              ) : null}
            </AriaLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function Grid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("grid gap-4", className)}>{children}</div>;
}

/** Main + context rail layout (§3.1 context rail; §32 three-column at ≥1440). */
export function WithRail({ children, rail }: { children: ReactNode; rail: ReactNode }) {
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="min-w-0 space-y-5">{children}</div>
      <aside aria-label="Context" className="min-w-0 space-y-4">
        {rail}
      </aside>
    </div>
  );
}

export function Divider() {
  return <hr className="my-4 border-border" />;
}
