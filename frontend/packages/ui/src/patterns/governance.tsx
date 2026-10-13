"use client";

/** Governance display patterns from ZID-UX-001 §4: diff viewer, timeline, approval card, authority
 * graph (with its mandatory table equivalent), mandate budget, wizard, one-time secret, chart. */
import {
  AlertTriangle,
  BarChart3,
  Check,
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  Link2,
  Minus,
  Plus,
  Table2,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { EvidenceChip, RiskBadge, StatusBadge } from "../components/badges";
import { Button } from "../components/button";
import { DateTime, Money } from "../components/data";
import { Banner } from "../components/feedback";
import { Checkbox } from "../components/forms";
import { cn } from "../utils/cn";

/* ------------------------------------------------------------------------------------------------
 * Diff viewer (§4): structured before/after for policy, mappings and sensitive configuration.
 * ---------------------------------------------------------------------------------------------- */

export type FieldChange = { path: string; before?: ReactNode; after?: ReactNode; kind: "added" | "removed" | "changed" };

export function StructuredDiff({ changes, caption = "Changes" }: { changes: FieldChange[]; caption?: string }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-canvas text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
          <tr>
            <th scope="col" className="px-3 py-2">Field</th>
            <th scope="col" className="px-3 py-2">Before</th>
            <th scope="col" className="px-3 py-2">After</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {changes.map((c) => (
            <tr key={c.path}>
              <th scope="row" className="px-3 py-2 font-mono text-xs font-normal text-ink">
                <span className="me-1.5 inline-flex">
                  {c.kind === "added" ? (
                    <Plus aria-label="added" className="size-3.5 text-ok-fg" />
                  ) : c.kind === "removed" ? (
                    <Minus aria-label="removed" className="size-3.5 text-danger-fg" />
                  ) : (
                    <span aria-label="changed" className="font-bold text-warn-fg">~</span>
                  )}
                </span>
                {c.path}
              </th>
              <td className={cn("px-3 py-2 font-mono text-xs", c.kind !== "added" && "bg-danger-bg/60 text-danger-fg")}>
                {c.kind === "added" ? <span className="text-ink-subtle">—</span> : <del className="no-underline">{c.before}</del>}
              </td>
              <td className={cn("px-3 py-2 font-mono text-xs", c.kind !== "removed" && "bg-ok-bg/60 text-ok-fg")}>
                {c.kind === "removed" ? <span className="text-ink-subtle">—</span> : <ins className="no-underline">{c.after}</ins>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type DiffLine = { kind: "same" | "add" | "del"; text: string; a?: number; b?: number };

/** Line diff (LCS) for policy source. Small inputs only; large diffs are server-rendered. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--) dp[i]![j] = a[i] === b[j] ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!);
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      out.push({ kind: "same", text: a[i]!, a: i + 1, b: j + 1 });
      i++;
      j++;
    } else if (dp[i + 1]![j]! >= dp[i]![j + 1]!) {
      out.push({ kind: "del", text: a[i]!, a: i + 1 });
      i++;
    } else {
      out.push({ kind: "add", text: b[j]!, b: j + 1 });
      j++;
    }
  }
  while (i < m) out.push({ kind: "del", text: a[i]!, a: ++i });
  while (j < n) out.push({ kind: "add", text: b[j]!, b: ++j });
  return out;
}

export function SourceDiff({ before, after, label = "Policy diff" }: { before: string; after: string; label?: string }) {
  const lines = useMemo(() => lineDiff(before, after), [before, after]);
  const added = lines.filter((l) => l.kind === "add").length;
  const removed = lines.filter((l) => l.kind === "del").length;
  return (
    <figure className="overflow-hidden rounded-lg border border-border">
      <figcaption className="flex items-center justify-between border-b border-border bg-canvas px-3 py-2 text-xs text-ink-muted">
        <span className="font-medium">{label}</span>
        <span>
          <span className="text-ok-fg">+{added}</span> <span className="text-danger-fg">−{removed}</span>
        </span>
      </figcaption>
      <pre tabIndex={0} className="max-h-96 overflow-auto bg-surface font-mono text-xs leading-5 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500">
        {lines.map((l, k) => (
          <div
            key={k}
            className={cn("flex", l.kind === "add" && "bg-ok-bg text-ok-fg", l.kind === "del" && "bg-danger-bg text-danger-fg")}
          >
            <span aria-hidden className="w-9 shrink-0 select-none pe-2 text-end text-ink-subtle">{l.a ?? ""}</span>
            <span aria-hidden className="w-9 shrink-0 select-none pe-2 text-end text-ink-subtle">{l.b ?? ""}</span>
            <span className="w-4 shrink-0 select-none">
              {l.kind === "add" ? <span aria-label="added line">+</span> : l.kind === "del" ? <span aria-label="removed line">−</span> : " "}
            </span>
            <span className="whitespace-pre pe-3">{l.text || " "}</span>
          </div>
        ))}
      </pre>
    </figure>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Timeline (§4): chronological state changes with source, actor and evidence link.
 * ---------------------------------------------------------------------------------------------- */

export type TimelineEvent = {
  id: string;
  at: string;
  title: ReactNode;
  state?: string;
  actor?: ReactNode;
  source?: string;
  evidence?: ReactNode;
  icon?: LucideIcon;
};

export function Timeline({ events, label = "Activity" }: { events: TimelineEvent[]; label?: string }) {
  return (
    <ol aria-label={label} className="relative space-y-5 border-s border-border ps-6">
      {events.map((e) => {
        const Icon = e.icon ?? CheckCircle2;
        return (
          <li key={e.id} className="relative">
            <span className="absolute -start-[2.05rem] top-0 flex size-6 items-center justify-center rounded-full border border-border bg-surface text-ink-muted">
              <Icon aria-hidden className="size-3.5" />
            </span>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-ink">{e.title}</span>
              {e.state ? <StatusBadge state={e.state} size="xs" /> : null}
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-muted">
              <DateTime value={e.at} />
              {e.actor ? <span>by {e.actor}</span> : null}
              {e.source ? <span>via {e.source}</span> : null}
              {e.evidence ? <span>{e.evidence}</span> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Approval card (§4): requested action, actor, scope, expiry, risk, evidence, approving role.
 * ---------------------------------------------------------------------------------------------- */

export function ApprovalCard({
  action,
  requestedBy,
  scope,
  expiresAt,
  risk,
  evidenceClass,
  approvingRole,
  progress,
  state,
  actions,
}: {
  action: ReactNode;
  requestedBy: ReactNode;
  scope: ReactNode;
  expiresAt: string;
  risk: "R0" | "R1" | "R2" | "R3";
  evidenceClass: string;
  approvingRole: string;
  progress?: { approved: number; required: number };
  state: string;
  actions?: ReactNode;
}) {
  return (
    <article className="rounded-card border border-border bg-surface p-4 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink">{action}</h3>
          <p className="mt-0.5 text-xs text-ink-muted">Requested by {requestedBy}</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <StatusBadge state={state} kind="Approval" />
          <RiskBadge risk={risk} compact />
          <EvidenceChip evidenceClass={evidenceClass} />
        </div>
      </div>
      <dl className="mt-3 grid gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Scope</dt>
          <dd className="text-ink">{scope}</dd>
        </div>
        <div>
          <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Approving role</dt>
          <dd className="text-ink">{approvingRole}</dd>
        </div>
        <div>
          <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Expires</dt>
          <dd className="text-ink">
            <DateTime value={expiresAt} />
          </dd>
        </div>
      </dl>
      {progress ? (
        <p className="mt-3 text-xs text-ink-muted">
          Approvals <span className="tabular font-semibold text-ink">{progress.approved}/{progress.required}</span>
        </p>
      ) : null}
      {actions ? <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">{actions}</div> : null}
    </article>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Authority path (§4 "Graph view", §7.1): one subject/action path; every edge shows source type,
 * scope, validity and whether it carries authority. A table equivalent is always available.
 * ---------------------------------------------------------------------------------------------- */

export type AuthorityNode = {
  id: string;
  kind: string;
  label: string;
  state: string;
  /** Edge from the previous node into this one. */
  edge?: { source: string; scope: string; validUntil?: string | null; authorityBearing: boolean };
};

export function AuthorityPath({ nodes, label = "Authority path" }: { nodes: AuthorityNode[]; label?: string }) {
  const [view, setView] = useState<"graph" | "table">("graph");
  const notActive = nodes.filter((n) => !["ACTIVE", "VALID", "VERIFIED"].includes(n.state.toUpperCase()));
  return (
    <figure className="rounded-lg border border-border bg-surface">
      <figcaption className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <span className="text-sm font-semibold text-ink">{label}</span>
        <div role="group" aria-label="View as" className="inline-flex rounded-control border border-border p-0.5">
          {(
            [
              ["graph", "Path", BarChart3],
              ["table", "Table", Table2],
            ] as const
          ).map(([id, text, Icon]) => (
            <button
              key={id}
              type="button"
              aria-pressed={view === id}
              onClick={() => setView(id)}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-ink-muted aria-pressed:bg-sunken aria-pressed:text-ink"
            >
              <Icon aria-hidden className="size-3.5" />
              {text}
            </button>
          ))}
        </div>
      </figcaption>
      {notActive.length ? (
        <div className="px-4 pt-3">
          <Banner tone="warn" title="Not active authority">
            {notActive.map((n) => `${n.label} is ${n.state.toLowerCase()}`).join("; ")}. A stale or conflicted link cannot support R2/R3
            actions.
          </Banner>
        </div>
      ) : null}
      {view === "graph" ? (
        <ol className="flex flex-col gap-0 overflow-x-auto p-4 lg:flex-row lg:items-stretch">
          {nodes.map((n, i) => {
            const inactive = !["ACTIVE", "VALID", "VERIFIED"].includes(n.state.toUpperCase());
            return (
              <li key={n.id} className="flex flex-col items-stretch lg:flex-row lg:items-center">
                {i > 0 && n.edge ? (
                  <div className="flex items-center gap-2 py-1 ps-5 lg:flex-col lg:px-2 lg:py-0">
                    <span
                      aria-hidden
                      className={cn(
                        "h-6 w-px lg:h-px lg:w-16",
                        n.edge.authorityBearing ? "bg-brand-600" : "border-s border-dashed border-ink-subtle lg:border-s-0 lg:border-t",
                      )}
                    />
                    <span className="text-2xs leading-tight text-ink-muted lg:max-w-28 lg:text-center">
                      <span className="font-semibold">{n.edge.source}</span>
                      <br />
                      {n.edge.scope}
                      <br />
                      {n.edge.authorityBearing ? "authority-bearing" : (
                        <span className="inline-flex items-center gap-0.5">
                          <Link2 aria-hidden className="size-3" /> relationship only
                        </span>
                      )}
                    </span>
                  </div>
                ) : null}
                <div
                  className={cn(
                    "min-w-40 rounded-lg border px-3 py-2",
                    inactive ? "border-dashed border-warn-fg/60 bg-warn-bg" : "border-border bg-canvas",
                  )}
                >
                  <div className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">{n.kind}</div>
                  <div className="text-sm font-medium text-ink">{n.label}</div>
                  <div className="mt-1">
                    <StatusBadge state={n.state} size="xs" kind={n.kind} />
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="overflow-x-auto p-4">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">{label}, as a table</caption>
            <thead className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
              <tr>
                <th scope="col" className="py-2 pe-3">Step</th>
                <th scope="col" className="py-2 pe-3">Object</th>
                <th scope="col" className="py-2 pe-3">State</th>
                <th scope="col" className="py-2 pe-3">Derived via</th>
                <th scope="col" className="py-2 pe-3">Scope</th>
                <th scope="col" className="py-2 pe-3">Valid until</th>
                <th scope="col" className="py-2">Carries authority</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {nodes.map((n, i) => (
                <tr key={n.id}>
                  <td className="py-2 pe-3 tabular">{i + 1}</td>
                  <th scope="row" className="py-2 pe-3 font-medium text-ink">
                    {n.kind}: {n.label}
                  </th>
                  <td className="py-2 pe-3"><StatusBadge state={n.state} size="xs" /></td>
                  <td className="py-2 pe-3">{n.edge?.source ?? "—"}</td>
                  <td className="py-2 pe-3">{n.edge?.scope ?? "—"}</td>
                  <td className="py-2 pe-3">{n.edge?.validUntil ? <DateTime value={n.edge.validUntil} /> : n.edge ? "Until revoked" : "—"}</td>
                  <td className="py-2">{n.edge ? (n.edge.authorityBearing ? "Yes" : "No, relationship only") : "Root"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </figure>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Mandate budget (§12, UX-08): available = limit − committed − reserved, exactly as the ledger says.
 * ---------------------------------------------------------------------------------------------- */

export function BudgetMeter({
  label,
  limit,
  committed,
  reserved,
  currency,
}: {
  label: string;
  limit: number;
  committed: number;
  reserved: number;
  /** ISO currency for value budgets (minor units); omit for count budgets. */
  currency?: string;
}) {
  const available = limit - committed - reserved;
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / limit) * 100))}%`;
  const fmt = (v: number) => (currency ? <Money minor={v} currency={currency} /> : <span className="tabular">{v.toLocaleString("en-US")}</span>);
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-ink">{label}</span>
        <span className="text-xs text-ink-muted">Limit {fmt(limit)}</span>
      </div>
      <div aria-hidden className="flex h-3 overflow-hidden rounded-full bg-sunken">
        <div className="h-full bg-brand-600" style={{ width: pct(committed) }} />
        <div className="env-hatch h-full bg-brand-200 text-brand-500/40" style={{ width: pct(reserved) }} />
      </div>
      <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
        <div>
          <dt className="flex items-center gap-1.5 text-xs text-ink-muted">
            <span aria-hidden className="size-2.5 rounded-sm bg-brand-600" /> Committed
          </dt>
          <dd className="font-semibold text-ink">{fmt(committed)}</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1.5 text-xs text-ink-muted">
            <span aria-hidden className="env-hatch size-2.5 rounded-sm bg-brand-200 text-brand-500/40" /> Reserved
          </dt>
          <dd className="font-semibold text-ink">{fmt(reserved)}</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1.5 text-xs text-ink-muted">
            <span aria-hidden className="size-2.5 rounded-sm border border-border-strong bg-sunken" /> Available
          </dt>
          <dd className={cn("font-semibold", available <= 0 ? "text-danger-fg" : "text-ink")}>{fmt(available)}</dd>
        </div>
      </dl>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Wizard (§12.1 grant/mandate creation, §15 integration setup).
 * ---------------------------------------------------------------------------------------------- */

export type WizardStep = { id: string; title: string; description?: string; content: ReactNode; isValid?: boolean };

export function Wizard({
  steps,
  current,
  onStepChange,
  onFinish,
  finishLabel,
  finishPending,
}: {
  steps: WizardStep[];
  current: number;
  onStepChange: (i: number) => void;
  onFinish: () => void;
  finishLabel: string;
  finishPending?: boolean;
}) {
  const step = steps[current]!;
  const last = current === steps.length - 1;
  return (
    <div className="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      <nav aria-label="Steps">
        <ol className="space-y-1">
          {steps.map((s, i) => {
            const state = i < current ? "done" : i === current ? "current" : "todo";
            return (
              <li key={s.id}>
                <button
                  type="button"
                  disabled={i > current}
                  aria-current={state === "current" ? "step" : undefined}
                  onClick={() => onStepChange(i)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-start text-sm",
                    state === "current" ? "bg-brand-50 font-semibold text-brand-700" : "text-ink-muted hover:bg-hover disabled:hover:bg-transparent",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs",
                      state === "done" && "border-ok-fg bg-ok-bg text-ok-fg",
                      state === "current" && "border-brand-600 bg-brand-600 text-on-brand",
                      state === "todo" && "border-border-strong",
                    )}
                  >
                    {state === "done" ? <Check aria-label="complete" className="size-3.5" /> : i + 1}
                  </span>
                  {s.title}
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
      <section aria-labelledby={`wiz-${step.id}`} className="min-w-0 rounded-card border border-border bg-surface shadow-card">
        <div className="border-b border-border px-5 py-4">
          <p className="text-xs font-medium text-ink-subtle">
            Step {current + 1} of {steps.length}
          </p>
          <h2 id={`wiz-${step.id}`} className="text-base font-semibold text-ink">
            {step.title}
          </h2>
          {step.description ? <p className="mt-0.5 text-sm text-ink-muted">{step.description}</p> : null}
        </div>
        <div className="p-5">{step.content}</div>
        <div className="flex justify-between gap-2 border-t border-border px-5 py-3">
          <Button variant="ghost" isDisabled={current === 0} onPress={() => onStepChange(current - 1)}>
            Back
          </Button>
          {last ? (
            <Button isDisabled={step.isValid === false} pending={finishPending} onPress={onFinish}>
              {finishLabel}
            </Button>
          ) : (
            <Button isDisabled={step.isValid === false} onPress={() => onStepChange(current + 1)}>
              Continue
            </Button>
          )}
        </div>
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * One-time secret (§18.1, UX-16): shown once, explicit copy acknowledgement, never re-rendered.
 * ---------------------------------------------------------------------------------------------- */

export function OneTimeSecret({
  label,
  secret,
  onCopy,
  onAcknowledge,
}: {
  label: string;
  secret: string;
  /** Lets the app log the presentation event (§29 Clipboard). */
  onCopy?: () => void;
  onAcknowledge: () => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [ack, setAck] = useState(false);
  return (
    <div className="space-y-4">
      <Banner tone="warn" icon={AlertTriangle} title="Shown once">
        This value cannot be displayed again. Store it in your secret manager now. Prefer private_key_jwt or workload identity for
        production integrations.
      </Banner>
      <div>
        <div className="mb-1.5 text-sm font-medium text-ink">{label}</div>
        <div className="flex items-stretch gap-2">
          <code
            aria-label={revealed ? label : `${label} (hidden)`}
            className="min-w-0 flex-1 overflow-x-auto rounded-control border border-border-strong bg-canvas px-3 py-2 font-mono text-sm text-ink"
          >
            {revealed ? secret : "•".repeat(Math.min(secret.length, 40))}
          </code>
          <Button variant="secondary" size="md" onPress={() => setRevealed((r) => !r)} icon={revealed ? <EyeOff aria-hidden className="size-4" /> : <Eye aria-hidden className="size-4" />}>
            {revealed ? "Hide" : "Reveal"}
          </Button>
          <Button
            variant="secondary"
            onPress={() => {
              void navigator.clipboard?.writeText(secret).then(() => {
                setCopied(true);
                onCopy?.();
              });
            }}
            icon={copied ? <Check aria-hidden className="size-4 text-ok-fg" /> : <Copy aria-hidden className="size-4" />}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>
      <Checkbox isSelected={ack} onChange={setAck}>
        I have stored this secret. I understand it will not be shown again.
      </Checkbox>
      <div className="flex justify-end">
        <Button isDisabled={!ack} onPress={onAcknowledge}>
          Done
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Stacked bar chart with a tabular equivalent and text description (§5.1, §36).
 * ---------------------------------------------------------------------------------------------- */

export type ChartSeries = { key: string; label: string; className: string };

export function StackedBarChart({
  title,
  description,
  categories,
  series,
  data,
}: {
  title: string;
  /** Plain-language summary for assistive tech and as the chart caption. */
  description: string;
  categories: string[];
  series: ChartSeries[];
  /** data[seriesKey][categoryIndex] */
  data: Record<string, number[]>;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const totals = categories.map((_, i) => series.reduce((s, se) => s + (data[se.key]?.[i] ?? 0), 0));
  const max = Math.max(1, ...totals);
  return (
    <figure>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <figcaption>
          <span className="block text-sm font-semibold text-ink">{title}</span>
          <span className="block text-xs text-ink-muted">{description}</span>
        </figcaption>
        <div role="group" aria-label="View as" className="inline-flex rounded-control border border-border p-0.5">
          {(["chart", "table"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className="rounded-md px-2 py-1 text-xs font-medium capitalize text-ink-muted aria-pressed:bg-sunken aria-pressed:text-ink"
            >
              {v}
            </button>
          ))}
        </div>
      </div>
      {view === "chart" ? (
        <div aria-hidden>
          <div className="flex h-40 items-end gap-1">
            {categories.map((c, i) => (
              <div key={c} className="flex h-full min-w-0 flex-1 flex-col justify-end" title={`${c}: ${totals[i]}`}>
                <div className="flex flex-col-reverse overflow-hidden rounded-t-sm" style={{ height: `${(totals[i]! / max) * 100}%` }}>
                  {series.map((s) => (
                    <div key={s.key} className={s.className} style={{ flexGrow: data[s.key]?.[i] ?? 0 }} />
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-1 flex gap-1 text-2xs text-ink-subtle">
            {categories.map((c, i) => (
              <span key={c} className="min-w-0 flex-1 truncate text-center">
                {i % Math.ceil(categories.length / 8) === 0 ? c : ""}
              </span>
            ))}
          </div>
          <ul className="mt-3 flex flex-wrap gap-3 text-xs text-ink-muted">
            {series.map((s) => (
              <li key={s.key} className="flex items-center gap-1.5">
                <span className={cn("size-2.5 rounded-sm", s.className)} />
                {s.label}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className={view === "table" ? "max-h-64 overflow-auto" : "sr-only"}>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">{title}</caption>
          <thead className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
            <tr>
              <th scope="col" className="py-1.5 pe-3">Period</th>
              {series.map((s) => (
                <th key={s.key} scope="col" className="py-1.5 pe-3 text-end">{s.label}</th>
              ))}
              <th scope="col" className="py-1.5 text-end">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {categories.map((c, i) => (
              <tr key={c}>
                <th scope="row" className="py-1.5 pe-3 font-normal">{c}</th>
                {series.map((s) => (
                  <td key={s.key} className="tabular py-1.5 pe-3 text-end">{(data[s.key]?.[i] ?? 0).toLocaleString("en-US")}</td>
                ))}
                <td className="tabular py-1.5 text-end font-medium">{totals[i]!.toLocaleString("en-US")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
