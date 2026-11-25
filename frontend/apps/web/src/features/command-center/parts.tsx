"use client";

import type { VerifyAnalytics as VA } from "@zoiko/api-client";
import { Badge, Banner } from "@zoiko/ui";
import type { ReactNode } from "react";

import { basis, label, rowLabel, shown } from "./logic";

/** One headline figure with its denominator, definition version and kind (Q10, Q11). Withheld and no-data figures say so in words. */
export function Tile({ g, dataLabel, hint }: { g: VA.Governed; dataLabel?: string; hint?: ReactNode }) {
  const l = label(g.suppressed ? "OBSERVED" : g.quality, dataLabel);
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <p className="text-sm font-medium text-ink-muted">{g.name}</p>
      <p className="mt-1 text-2xl font-semibold tabular text-ink">{shown(g.value, g.unit, g.suppressed)}</p>
      {g.p95 !== undefined && g.p95 !== null && !g.suppressed ? <p className="text-xs text-ink-muted">p95 {shown(g.p95, g.unit)}</p> : null}
      <p className="mt-1 text-xs text-ink-muted">{basis(g)}</p>
      <p className="mt-2">
        <Badge tone={l.tone} size="xs">
          {l.text}
        </Badge>
      </p>
      {hint ? <p className="mt-2 text-xs text-ink-muted">{hint}</p> : null}
    </div>
  );
}

export function Tiles({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{children}</div>;
}

/** A breakdown as a table whose rows carry their own text value; the bar is decoration only, so nothing depends on colour or on seeing it
 * (Q41, Q42). Withheld rows stay listed as withheld. */
export function Figures({
  caption,
  rows,
  labelKey,
  labelTitle,
  valueKey = "value",
  unit = "ratio",
  countKey,
}: {
  caption: string;
  rows: VA.Row[];
  labelKey: string;
  labelTitle: string;
  valueKey?: string;
  unit?: string;
  countKey?: string;
}) {
  if (!rows.length) return <p className="text-sm text-ink-muted">{caption}: no data in this window.</p>;
  const max = Math.max(1, ...rows.map((r) => (typeof r[valueKey] === "number" ? (r[valueKey] as number) : 0)));
  return (
    <table className="w-full text-sm">
      <caption className="mb-2 text-left font-medium text-ink">{caption}</caption>
      <thead>
        <tr className="text-left text-ink-muted">
          <th scope="col" className="py-1 pr-3 font-medium">{labelTitle}</th>
          <th scope="col" className="py-1 pr-3 font-medium">Value</th>
          {countKey ? <th scope="col" className="py-1 pr-3 font-medium">Count</th> : null}
          <th scope="col" className="w-1/3 py-1 font-medium"><span className="sr-only">Bar</span></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const v = typeof r[valueKey] === "number" ? (r[valueKey] as number) : null;
          return (
            <tr key={`${String(r[labelKey])}-${i}`} className="border-t border-border">
              <th scope="row" className="py-1.5 pr-3 text-left font-normal">{rowLabel(r, labelKey)}</th>
              <td className="py-1.5 pr-3 tabular">{shown(v, unit, r.suppressed === true)}</td>
              {countKey ? <td className="py-1.5 pr-3 tabular">{r.suppressed ? "Withheld" : String(r[countKey] ?? "-")}</td> : null}
              <td className="py-1.5" aria-hidden="true">
                {v !== null && !r.suppressed ? <div className="h-2 rounded bg-brand-500" style={{ width: `${Math.max(2, (v / (unit === "ratio" ? 1 : max)) * 100)}%` }} /> : null}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Where the figures come from and how fresh they are (07.01 item 04, M16). A source past the policy makes every figure provisional. */
export function Freshness({ quality, sources, scope }: { quality: string; sources: VA.Freshness[]; scope?: VA.Scope }) {
  const l = label(quality, scope?.data_label);
  return (
    <section aria-label="Data freshness" className="rounded-lg border border-border bg-sunken p-3 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <Badge tone={l.tone} size="xs">{l.text}</Badge>
        {scope ? (
          <span className="text-ink-muted">
            {scope.from.slice(0, 10)} to {scope.to.slice(0, 10)} ({scope.timezone}) · {scope.environment}
            {scope.market ? ` · ${scope.market}` : ""} · groups under {scope.min_group} withheld · data as of {scope.watermark_utc ? new Date(scope.watermark_utc).toLocaleString() : "no events yet"}
          </span>
        ) : null}
      </p>
      {sources.some((s) => s.stale) ? (
        <p className="mt-1 text-warn-fg">Some sources have not sent events recently; figures that depend on them are provisional, not live.</p>
      ) : null}
      {sources.length ? (
        <p className="mt-1 text-ink-muted">
          {sources.map((s) => `${s.source}: last event ${Math.round(s.lag_seconds / 60)} min ago, ${s.late} late, ${s.rejected} rejected`).join(" · ")}
        </p>
      ) : null}
    </section>
  );
}

/** A refusal for lack of access (403), as opposed to any other failure. */
export function denied(error: unknown): boolean {
  return (error as { status?: number } | null)?.status === 403;
}

export function Withheld({ error }: { error: unknown }) {
  if (!denied(error)) return null;
  const code = (error as { code?: string } | null)?.code;
  return (
    <Banner tone="info" title="Withheld for your access">
      {code === "ANALYTICS_PERMISSION_REQUIRED"
        ? "This view needs an analytics access bundle. An Owner can give it under Command Center, Access & privacy."
        : "Your account cannot do this here."}
    </Banner>
  );
}
