"use client";

import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  ChevronLeft,
  ChevronRight,
  Columns3,
  Copy,
  Search,
  X,
  type LucideIcon,
} from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";
import {
  Dialog as AriaDialog,
  DialogTrigger,
  Input,
  Link as AriaLink,
  Popover,
  SearchField,
  Button as AriaButton,
} from "react-aria-components";

import { cn } from "../utils/cn";
import { Checkbox } from "./forms";
import { EmptyState, LoadingRows } from "./feedback";
import { Button } from "./button";
import { useLocale } from "./locale";

/* ------------------------------------------------------------------------------------------------
 * DataTable (§4 "Data table", §23 "Tables", §25)
 * ---------------------------------------------------------------------------------------------- */

export type Column<T> = {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  /** Value used for client-side sorting; omit to make the column unsortable. */
  sortValue?: (row: T) => string | number | null | undefined;
  className?: string;
  /** Hidden by default but available in the column chooser. */
  defaultHidden?: boolean;
  /** Cannot be hidden (identity column). */
  pinned?: boolean;
  align?: "start" | "end";
};

export type BulkAction<T> = {
  id: string;
  label: string;
  icon?: ReactNode;
  /** Only semantically safe, reversible actions belong here (§25, §36). */
  onAction: (rows: T[]) => void;
};

type Sort = { key: string; dir: "asc" | "desc" } | null;

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  empty,
  loading = false,
  rowHref,
  onRowAction,
  bulkActions,
  bulkNotice = "Bulk actions are limited to safe, reversible changes. Security changes need object-level review.",
  toolbar,
  footer,
  dense = false,
  selectedKey,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Accessible table name. */
  caption: string;
  empty?: ReactNode;
  loading?: boolean;
  /** Makes the first column a link to the object page. */
  rowHref?: (row: T) => string;
  /** Quick-inspect handler (opens a side panel); deep edit navigates (§4 "Side panel"). */
  onRowAction?: (row: T) => void;
  bulkActions?: BulkAction<T>[];
  bulkNotice?: string;
  toolbar?: ReactNode;
  footer?: ReactNode;
  dense?: boolean;
  selectedKey?: string | null;
}) {
  const [sort, setSort] = useState<Sort>(null);
  const [hidden, setHidden] = useState<Set<string>>(
    () => new Set(columns.filter((c) => c.defaultHidden).map((c) => c.key)),
  );
  const [selection, setSelection] = useState<Set<string>>(new Set());

  const visible = columns.filter((c) => !hidden.has(c.key));
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const get = col.sortValue;
    return [...rows].sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      const r = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return sort.dir === "asc" ? r : -r;
    });
  }, [rows, sort, columns]);

  const selectable = Boolean(bulkActions?.length);
  const allSelected = selectable && rows.length > 0 && rows.every((r) => selection.has(rowKey(r)));
  const selectedRows = rows.filter((r) => selection.has(rowKey(r)));

  const cellPad = dense ? "px-3 py-1.5" : "px-4 py-2.5";

  return (
    <div className="min-w-0">
      {(toolbar || columns.length > 3) && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <div className="min-w-0 flex-1">{toolbar}</div>
          {columns.length > 3 ? (
            <ColumnChooser
              columns={columns}
              hidden={hidden}
              onToggle={(key) =>
                setHidden((h) => {
                  const n = new Set(h);
                  if (n.has(key)) n.delete(key);
                  else n.add(key);
                  return n;
                })
              }
            />
          ) : null}
        </div>
      )}

      {selectable && selectedRows.length > 0 ? (
        <div
          role="region"
          aria-label="Bulk actions"
          className="flex flex-wrap items-center gap-3 border-b border-border bg-brand-50 px-4 py-2 text-sm"
        >
          <span className="font-medium text-brand-700">{selectedRows.length} selected</span>
          {bulkActions!.map((a) => (
            <Button key={a.id} size="xs" variant="secondary" icon={a.icon} onPress={() => a.onAction(selectedRows)}>
              {a.label}
            </Button>
          ))}
          <Button size="xs" variant="ghost" onPress={() => setSelection(new Set())}>
            Clear
          </Button>
          <span className="basis-full text-xs text-ink-muted md:basis-auto">{bulkNotice}</span>
        </div>
      ) : null}

      {loading ? (
        <div className="p-4">
          <LoadingRows rows={5} />
        </div>
      ) : rows.length === 0 ? (
        empty ?? <EmptyState title="No results">No objects match the current view.</EmptyState>
      ) : (
        // A scrollable region must be reachable and named for keyboard and screen-reader users (WCAG 2.1.1).
        <div
          role="region"
          aria-label={caption}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
          className="overflow-x-auto outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
        >
          <table className="w-full border-collapse text-left text-sm">
            <caption className="sr-only">{caption}</caption>
            <thead className="hidden bg-canvas/60 md:table-header-group">
              <tr className="border-b border-border">
                {selectable ? (
                  <th scope="col" className={cn(cellPad, "w-10")}>
                    <Checkbox
                      aria-label={allSelected ? "Deselect all rows" : "Select all rows"}
                      isSelected={allSelected}
                      isIndeterminate={!allSelected && selectedRows.length > 0}
                      onChange={(v) => setSelection(v ? new Set(rows.map(rowKey)) : new Set())}
                    />
                  </th>
                ) : null}
                {visible.map((c) => {
                  const active = sort?.key === c.key;
                  return (
                    <th
                      key={c.key}
                      scope="col"
                      aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                      className={cn(
                        cellPad,
                        "text-2xs font-semibold uppercase tracking-wider text-ink-subtle",
                        c.align === "end" && "text-end",
                        c.className,
                      )}
                    >
                      {c.sortValue ? (
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 rounded uppercase hover:text-ink"
                          onClick={() =>
                            setSort((s) =>
                              s?.key !== c.key ? { key: c.key, dir: "asc" } : s.dir === "asc" ? { key: c.key, dir: "desc" } : null,
                            )
                          }
                        >
                          {c.header}
                          {active ? (
                            sort!.dir === "asc" ? (
                              <ArrowUp aria-hidden className="size-3" />
                            ) : (
                              <ArrowDown aria-hidden className="size-3" />
                            )
                          ) : (
                            <ArrowUpDown aria-hidden className="size-3 opacity-40" />
                          )}
                        </button>
                      ) : (
                        c.header
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const key = rowKey(row);
                const isSel = selection.has(key);
                return (
                  <tr
                    key={key}
                    aria-selected={selectable ? isSel : undefined}
                    data-current={selectedKey === key || undefined}
                    className={cn(
                      "block border-b border-border py-2 last:border-0 md:table-row md:py-0",
                      "hover:bg-hover/60 data-[current]:bg-brand-50/70",
                      isSel && "bg-brand-50/50",
                      onRowAction && "cursor-pointer",
                    )}
                    onClick={
                      onRowAction
                        ? (e) => {
                            const t = e.target as HTMLElement;
                            if (t.closest("a,button,input,[role=checkbox]")) return;
                            onRowAction(row);
                          }
                        : undefined
                    }
                  >
                    {selectable ? (
                      <td className={cn(cellPad, "block md:table-cell")}>
                        <Checkbox
                          aria-label={`Select row ${key}`}
                          isSelected={isSel}
                          onChange={(v) =>
                            setSelection((s) => {
                              const n = new Set(s);
                              if (v) n.add(key);
                              else n.delete(key);
                              return n;
                            })
                          }
                        />
                      </td>
                    ) : null}
                    {visible.map((c, i) => (
                      <td
                        key={c.key}
                        className={cn(
                          "flex gap-3 px-4 py-1 md:table-cell md:align-middle",
                          dense ? "md:px-3 md:py-1.5" : "md:px-4 md:py-3",
                          c.align === "end" && "md:text-end",
                          c.className,
                        )}
                      >
                        <span className="w-32 shrink-0 text-2xs font-semibold uppercase tracking-wide text-ink-subtle md:hidden">
                          {c.header}
                        </span>
                        <span className="min-w-0">
                          {i === 0 && rowHref ? (
                            <AriaLink
                              href={rowHref(row)}
                              className="rounded-sm font-medium text-ink outline-none hover:text-brand-600 hover:underline focus-visible:ring-2 focus-visible:ring-brand-500"
                            >
                              {c.render(row)}
                            </AriaLink>
                          ) : i === 0 && onRowAction ? (
                            <button
                              type="button"
                              className="rounded-sm text-start font-medium text-ink hover:text-brand-600 hover:underline"
                              onClick={() => onRowAction(row)}
                            >
                              {c.render(row)}
                            </button>
                          ) : (
                            c.render(row)
                          )}
                        </span>
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {footer ? <div className="border-t border-border px-4 py-3">{footer}</div> : null}
    </div>
  );
}

function ColumnChooser<T>({
  columns,
  hidden,
  onToggle,
}: {
  columns: Column<T>[];
  hidden: Set<string>;
  onToggle: (key: string) => void;
}) {
  return (
    <DialogTrigger>
      <Button size="sm" variant="ghost" icon={<Columns3 aria-hidden className="size-4" />}>
        Columns
      </Button>
      <Popover placement="bottom end" className="w-60 rounded-lg border border-border bg-raised p-3 shadow-[var(--shadow-pop)]">
        <AriaDialog aria-label="Choose visible columns" className="space-y-2 outline-none">
          <div className="text-2xs font-semibold uppercase tracking-wide text-ink-subtle">Visible columns</div>
          {columns.map((c) => (
            <Checkbox
              key={c.key}
              isSelected={!hidden.has(c.key)}
              isDisabled={c.pinned}
              onChange={() => onToggle(c.key)}
            >
              {c.header}
            </Checkbox>
          ))}
        </AriaDialog>
      </Popover>
    </DialogTrigger>
  );
}

/** Cursor pagination: stable navigation over mutable datasets, no fake row numbers (§25). */
export function CursorPager({
  hasPrevious,
  hasNext,
  onPrevious,
  onNext,
  summary,
}: {
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  summary?: ReactNode;
}) {
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-3 text-sm text-ink-muted">
      <span>{summary}</span>
      <div className="flex gap-2">
        <Button size="sm" variant="secondary" isDisabled={!hasPrevious} onPress={onPrevious} icon={<ChevronLeft aria-hidden className="size-4" />}>
          Previous
        </Button>
        <Button size="sm" variant="secondary" isDisabled={!hasNext} onPress={onNext} iconEnd={<ChevronRight aria-hidden className="size-4" />}>
          Next
        </Button>
      </div>
    </nav>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Filter bar with chips (§4 "Data table", §25)
 * ---------------------------------------------------------------------------------------------- */

export type FilterDef = { id: string; label: string; options: { id: string; label: string }[] };

export function FilterBar({
  search,
  onSearch,
  searchLabel = "Search",
  filters = [],
  values,
  onChange,
  extra,
}: {
  search?: string;
  onSearch?: (v: string) => void;
  searchLabel?: string;
  filters?: FilterDef[];
  values: Record<string, string>;
  onChange: (values: Record<string, string>) => void;
  extra?: ReactNode;
}) {
  const active = filters.filter((f) => values[f.id]);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {onSearch ? (
          <SearchField
            aria-label={searchLabel}
            value={search}
            onChange={onSearch}
            className="group relative w-full sm:w-64"
          >
            <Search aria-hidden className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-subtle" />
            <Input
              placeholder={searchLabel}
              className="h-8 w-full rounded-[var(--radius-control)] border border-border-strong bg-surface ps-8 pe-7 text-sm text-ink outline-none placeholder:text-ink-subtle focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25 [&::-webkit-search-cancel-button]:hidden"
            />
            <AriaButton className="absolute end-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-ink-subtle hover:text-ink group-data-[empty]:hidden">
              <X aria-hidden className="size-3.5" />
              <span className="sr-only">Clear search</span>
            </AriaButton>
          </SearchField>
        ) : null}
        {filters.map((f) => (
          <FilterSelect
            key={f.id}
            def={f}
            value={values[f.id] ?? ""}
            onChange={(v) => onChange({ ...values, [f.id]: v })}
          />
        ))}
        {extra}
      </div>
      {active.length > 0 ? (
        <ul aria-label="Active filters" className="flex flex-wrap items-center gap-1.5">
          {active.map((f) => (
            <li key={f.id}>
              <span className="inline-flex items-center gap-1 rounded-full border border-brand-200 bg-brand-50 py-0.5 ps-2.5 pe-1 text-xs text-brand-700">
                <span className="text-brand-700/70">{f.label}:</span>
                {f.options.find((o) => o.id === values[f.id])?.label ?? values[f.id]}
                <button
                  type="button"
                  aria-label={`Remove filter ${f.label}`}
                  className="rounded-full p-0.5 hover:bg-brand-100"
                  onClick={() => {
                    const n = { ...values };
                    delete n[f.id];
                    onChange(n);
                  }}
                >
                  <X aria-hidden className="size-3" />
                </button>
              </span>
            </li>
          ))}
          <li>
            <button type="button" className="text-xs font-medium text-ink-muted hover:text-ink" onClick={() => onChange({})}>
              Clear all
            </button>
          </li>
        </ul>
      ) : null}
    </div>
  );
}

function FilterSelect({ def, value, onChange }: { def: FilterDef; value: string; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <div className="relative">
      <label htmlFor={id} className="sr-only">
        {def.label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 appearance-none rounded-[var(--radius-control)] border border-border-strong bg-surface ps-2.5 pe-7 text-sm text-ink outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25"
      >
        <option value="">{def.label}: Any</option>
        {def.options.map((o) => (
          <option key={o.id} value={o.id}>
            {def.label}: {o.label}
          </option>
        ))}
      </select>
      <ChevronRight aria-hidden className="pointer-events-none absolute end-2 top-1/2 size-3.5 -translate-y-1/2 rotate-90 text-ink-subtle" />
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Object identity & formatting (§4 "Object link", §23 dates/numbers)
 * ---------------------------------------------------------------------------------------------- */

/** Immutable id with a short display and an explicit copy affordance. */
export function CopyId({ value, short = true, label = "ID" }: { value: string; short?: boolean; label?: string }) {
  const [copied, setCopied] = useState(false);
  const display = short && value.length > 16 ? `${value.slice(0, 10)}…${value.slice(-4)}` : value;
  return (
    <span className="inline-flex items-center gap-1 font-mono text-xs text-ink-muted">
      <span title={value}>{display}</span>
      <button
        type="button"
        aria-label={`Copy ${label} ${value}`}
        className="rounded p-0.5 hover:bg-hover hover:text-ink"
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? <Check aria-hidden className="size-3.5 text-ok-fg" /> : <Copy aria-hidden className="size-3.5" />}
      </button>
      <span role="status" className="sr-only">
        {copied ? "Copied" : ""}
      </span>
    </span>
  );
}

/** Type icon + canonical display name + immutable short ID (§4 "Object link"). */
export function ObjectLink({
  icon: Icon,
  type,
  name,
  id,
  href,
}: {
  icon: LucideIcon;
  type: string;
  name: string;
  id?: string;
  href?: string;
}) {
  const label = (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-sunken text-ink-muted" title={type}>
        <Icon aria-hidden className="size-3.5" />
      </span>
      <span className="sr-only">{type}: </span>
      <span className="truncate font-medium">{name}</span>
    </span>
  );
  return (
    <span className="inline-flex min-w-0 flex-col">
      {href ? (
        <AriaLink
          href={href}
          className="rounded-sm text-ink outline-none hover:text-brand-600 hover:underline focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          {label}
        </AriaLink>
      ) : (
        label
      )}
      {id ? (
        <span className="ps-7.5">
          <CopyId value={id} label={`${type} ID`} />
        </span>
      ) : null}
    </span>
  );
}

/** User-local time; UTC/ISO available on hover, and shown inline where `utc` (§23 evidence detail). */
export function DateTime({
  value,
  utc = false,
  style = "medium",
}: {
  value: string | null | undefined;
  utc?: boolean;
  style?: "short" | "medium" | "date";
}) {
  const locale = useLocale();
  if (!value) return <span className="text-ink-subtle">—</span>;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return <span>{value}</span>;
  const local =
    style === "date"
      ? d.toLocaleDateString(locale, { dateStyle: "medium" })
      : d.toLocaleString(locale, { dateStyle: style === "short" ? "short" : "medium", timeStyle: "short" });
  const iso = d.toISOString();
  return (
    <span className="inline-flex flex-col">
      <time dateTime={iso} title={`${iso} (UTC)`} className="tabular">
        {local}
      </time>
      {utc ? <span className="font-mono text-2xs text-ink-subtle">{iso.replace("T", " ").slice(0, 19)} UTC</span> : null}
    </span>
  );
}


/** "4m ago" with the absolute time on hover. `now` is injectable for deterministic rendering. */
export function RelativeTime({ value, now }: { value: string | null | undefined; now?: number }) {
  const locale = useLocale();
  const rtf = typeof Intl !== "undefined" ? new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" }) : null;
  if (!value) return <span className="text-ink-subtle">—</span>;
  const d = new Date(value);
  const diff = (d.getTime() - (now ?? Date.now())) / 1000;
  const abs = Math.abs(diff);
  const [n, unit]: [number, Intl.RelativeTimeFormatUnit] =
    abs < 60 ? [diff, "second"] : abs < 3600 ? [diff / 60, "minute"] : abs < 86400 ? [diff / 3600, "hour"] : [diff / 86400, "day"];
  return (
    <time dateTime={d.toISOString()} title={`${d.toISOString()} (UTC)`} className="tabular whitespace-nowrap">
      {rtf ? rtf.format(Math.round(n), unit) : d.toISOString()}
    </time>
  );
}

/** Locale display, explicit currency, canonical minor-unit value retained for copy (§23). */
export function Money({ minor, currency, compact = false }: { minor: number; currency: string; compact?: boolean }) {
  const locale = useLocale();
  const major = minor / 100;
  const text = new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    currencyDisplay: "code",
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 1 : 2,
  }).format(major);
  return (
    <data value={`${minor} ${currency}`} className="tabular whitespace-nowrap">
      {text}
    </data>
  );
}

export function NumberValue({ value, compact = false }: { value: number; compact?: boolean }) {
  const locale = useLocale();
  return (
    <data value={value} className="tabular">
      {new Intl.NumberFormat(locale, { notation: compact ? "compact" : "standard" }).format(value)}
    </data>
  );
}

/** Pretty JSON for protocol payloads (never rendered as HTML). */
export function JsonView({ value, className, label = "JSON" }: { value: unknown; className?: string; label?: string }) {
  return (
    <pre
      aria-label={label}
      tabIndex={0}
      className={cn(
        "max-h-96 overflow-auto rounded-lg bg-code-bg p-4 font-mono text-xs leading-relaxed text-code-fg outline-none focus-visible:ring-2 focus-visible:ring-brand-500",
        className,
      )}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

/** Inline code / identifier. */
export function Code({ children }: { children: ReactNode }) {
  return <code className="rounded bg-sunken px-1 py-px font-mono text-[0.85em] text-ink">{children}</code>;
}
