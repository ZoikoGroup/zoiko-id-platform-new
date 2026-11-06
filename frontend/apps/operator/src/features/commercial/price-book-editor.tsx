"use client";

import type { PriceBookDetail, PriceBookLine } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Dialog, StatusBadge, TextField } from "@zoiko/ui";
import { CheckCircle2, Copy, Pencil } from "lucide-react";
import { useMemo, useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";

const PLAN_ORDER = ["DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE"];

/** A rate keeps its small decimals (0.015 must not read as 0.02). */
export function formatRate(amount: string, currency: string): string {
  const n = Number(amount);
  if (!Number.isFinite(n)) return amount;
  if (n === 0) return "Free";
  return new Intl.NumberFormat("en-US", { style: "currency", currency, currencyDisplay: "symbol", minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(n);
}

/** SKU rows by plan columns, from the flat lines the cell sends. */
export function matrixOf(lines: PriceBookLine[]): { skus: { sku: string; unit: string | null }[]; plans: string[]; at: (sku: string, plan: string) => PriceBookLine | undefined } {
  const byKey = new Map(lines.map((l) => [`${l.sku}|${l.plan}`, l]));
  const skus = [...new Map(lines.map((l) => [l.sku, l.unit ?? null])).entries()].map(([sku, unit]) => ({ sku, unit })).sort((a, b) => a.sku.localeCompare(b.sku));
  const present = new Set(lines.map((l) => l.plan));
  const plans = [...PLAN_ORDER.filter((p) => present.has(p)), ...[...present].filter((p) => !PLAN_ORDER.includes(p)).sort()];
  return { skus, plans, at: (sku, plan) => byKey.get(`${sku}|${plan}`) };
}

/** Is this a number the cell will take as a rate (non-negative, up to six decimals)? */
export const validRate = (v: string): boolean => /^\d+(\.\d{1,6})?$/.test(v.trim());

/** The price book as a table people can read: every SKU against every plan. A draft is edited in place; an approved book is frozen and says so. */
export function PriceBookEditor({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  const book = useCellQuery([...COMMERCIAL_KEY, "book", id], (api) => api.commercial.book(id));
  return <QueryView query={book}>{(b) => <Editor b={b} onOpen={onOpen} />}</QueryView>;
}

function Editor({ b, onOpen }: { b: PriceBookDetail; onOpen: (id: string) => void }) {
  const draft = b.status === "DRAFT";
  const m = useMemo(() => matrixOf(b.lines), [b.lines]);
  const [editing, setEditing] = useState<{ sku: string; plan: string } | null>(null);
  const [approving, setApproving] = useState(false);
  const [copying, setCopying] = useState(false);
  const readiness = useCellQuery([...COMMERCIAL_KEY, "readiness", b.price_book_id], (api) => api.commercial.bookReadiness(b.price_book_id), { enabled: draft });

  return (
    <Card
      title={`Price book v${b.version}`}
      description={
        draft
          ? "A draft. Click a rate to change it, then approve and publish. Rates apply to new orders only, never retroactively."
          : "Approved and frozen by the database. To change a rate, create an editable copy."
      }
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge state={b.status} kind="Price book" />
          <Button variant="secondary" size="sm" icon={<Copy aria-hidden className="size-4" />} onPress={() => setCopying(true)}>
            Create editable copy
          </Button>
          {draft ? (
            <Button size="sm" icon={<CheckCircle2 aria-hidden className="size-4" />} onPress={() => setApproving(true)}>
              Approve and publish
            </Button>
          ) : null}
        </div>
      }
      padded={false}
    >
      {draft ? (
        <div className="border-b border-border p-4">
          {readiness.data ? (
            <Banner tone={readiness.data.ready ? "ok" : "warn"} title={readiness.data.ready ? `Ready to publish: ${readiness.data.lines} lines, nothing unpriced` : `Not ready: ${readiness.data.missing.length} unpriced SKU and plan pairs`}>
              {readiness.data.missing.length ? (
                <span className="text-xs">
                  {readiness.data.missing.slice(0, 8).map((x) => `${x.plan} ${x.sku}`).join(", ")}
                  {readiness.data.missing.length > 8 ? ` and ${readiness.data.missing.length - 8} more` : ""}. Click a dash in the table to price it.
                </span>
              ) : readiness.data.free.length ? (
                <span className="text-xs">{readiness.data.free.length} line(s) are priced at zero. Check that was meant.</span>
              ) : null}
            </Banner>
          ) : null}
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] text-sm">
          <caption className="sr-only">Rates of price book version {b.version}, by SKU and plan</caption>
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
              <th scope="col" className="px-4 py-2">SKU</th>
              {m.plans.map((p) => (
                <th key={p} scope="col" className="px-4 py-2 text-right">{p}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.skus.map(({ sku, unit }) => (
              <tr key={sku} className="border-b border-border last:border-0">
                <th scope="row" className="px-4 py-2 text-left font-normal">
                  <span className="font-mono text-xs">{sku}</span>
                  {unit ? <span className="block text-xs text-ink-muted">per {unit}</span> : null}
                </th>
                {m.plans.map((p) => {
                  const l = m.at(sku, p);
                  return (
                    <td key={p} className="px-4 py-2 text-right">
                      {draft ? (
                        <button
                          type="button"
                          aria-label={`${l ? "Change" : "Set"} ${sku} on ${p}`}
                          onClick={() => setEditing({ sku, plan: p })}
                          className="group inline-flex w-full flex-col items-end rounded px-2 py-1 hover:bg-hover focus-visible:outline focus-visible:outline-2"
                        >
                          <span className={l ? "tabular" : "text-warn-fg"}>{l ? formatRate(l.unit_rate, b.currency) : "— set"}</span>
                          {l?.included_quantity ? <span className="text-xs text-ink-muted">{l.included_quantity.toLocaleString("en-GB")} included</span> : null}
                          <Pencil aria-hidden className="size-3 text-ink-subtle opacity-0 group-hover:opacity-100" />
                        </button>
                      ) : (
                        <span className={l ? "tabular" : "text-ink-subtle"}>
                          {l ? formatRate(l.unit_rate, b.currency) : "—"}
                          {l?.included_quantity ? <span className="block text-xs text-ink-muted">{l.included_quantity.toLocaleString("en-GB")} included</span> : null}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t border-border px-4 py-3 text-xs text-ink-muted">
        Currency {b.currency}. {b.effective_from ? `Effective from ${new Date(b.effective_from).toLocaleString("en-GB")}.` : "Not yet effective."} <Badge size="xs">{b.lines.length} lines</Badge>
      </p>
      {editing ? <EditLine b={b} sku={editing.sku} plan={editing.plan} onClose={() => setEditing(null)} /> : null}
      {approving ? <ApproveBook b={b} ready={readiness.data?.ready ?? false} onClose={() => setApproving(false)} /> : null}
      {copying ? <CopyBook b={b} onClose={() => setCopying(false)} onOpen={onOpen} /> : null}
    </Card>
  );
}

function EditLine({ b, sku, plan, onClose }: { b: PriceBookDetail; sku: string; plan: string; onClose: () => void }) {
  const line = b.lines.find((l) => l.sku === sku && l.plan === plan);
  // A new pair takes the unit divisor the same SKU already has on another plan, so a per-1,000 rate stays per 1,000.
  const sibling = b.lines.find((l) => l.sku === sku);
  const [rate, setRate] = useState(line?.unit_rate ?? "");
  const [included, setIncluded] = useState(line?.included_quantity != null ? String(line.included_quantity) : "");
  const bad = !validRate(rate) || (included !== "" && !/^\d+$/.test(included));
  const save = useCellMutation(
    (api, v: { rate: string; included: string }) =>
      api.commercial.call("PUT", `/v1/commercial/price-books/${encodeURIComponent(b.price_book_id)}/lines`, {
        sku,
        plan,
        unit_rate: v.rate.trim(),
        unit_divisor: line?.unit_divisor ?? sibling?.unit_divisor ?? 1,
        included_quantity: v.included === "" ? null : Number(v.included),
        minimum_amount: line?.minimum_amount ?? null,
      }),
    { invalidate: [COMMERCIAL_KEY], success: `${plan} ${sku} saved`, onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !save.isPending && onClose()}
      title={`${sku} on ${plan}`}
      description={line ? "Change this rate in the draft." : "This SKU is not priced on this plan yet."}
      footer={
        <>
          <Button variant="secondary" isDisabled={save.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={save.isPending} isDisabled={bad} onPress={() => save.mutate({ rate, included })}>
            Save rate
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label={`Unit rate (${b.currency})`} value={rate} onChange={setRate} inputMode="decimal" errorMessage={rate && !validRate(rate) ? "Enter a number such as 1499 or 0.015." : undefined} description="Enter 0 only if this is meant to be free." />
        <TextField label="Included quantity (optional)" value={included} onChange={setIncluded} inputMode="numeric" errorMessage={included !== "" && !/^\d+$/.test(included) ? "Whole numbers only." : undefined} />
        {line?.minimum_amount ? <p className="text-xs text-ink-muted">The minimum amount of {line.minimum_amount} {b.currency} on this line is kept.</p> : null}
        {save.error ? <ApiErrorView error={save.error} title="The rate was not saved" /> : null}
      </div>
    </Dialog>
  );
}

function ApproveBook({ b, ready, onClose }: { b: PriceBookDetail; ready: boolean; onClose: () => void }) {
  const [when, setWhen] = useState("");
  const approve = useCellMutation(
    (api, v: { when: string }) => api.commercial.call("POST", `/v1/commercial/price-books/${encodeURIComponent(b.price_book_id)}:approve`, v.when ? { effective_from: new Date(v.when).toISOString() } : {}),
    { invalidate: [COMMERCIAL_KEY], success: `Price book v${b.version} approved and published`, onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !approve.isPending && onClose()}
      title={`Approve and publish v${b.version}`}
      description="After this the rates can no longer be edited, and they apply to new orders from the date below."
      footer={
        <>
          <Button variant="secondary" isDisabled={approve.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={approve.isPending} isDisabled={!ready} onPress={() => approve.mutate({ when })}>
            Approve and publish
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {!ready ? <Banner tone="warn">Some SKU and plan pairs are still unpriced. Price them in the table first.</Banner> : null}
        <TextField label="Effective from (optional)" type="datetime-local" value={when} onChange={setWhen} description="Leave empty to start now." />
        <p className="text-sm text-ink-muted">
          {b.lines.length} lines in {b.currency}. Companies already on a plan keep what they signed; this book is used for new orders.
        </p>
        {approve.error ? <ApiErrorView error={approve.error} title="The book was not approved" /> : null}
      </div>
    </Dialog>
  );
}

function CopyBook({ b, onClose, onOpen }: { b: PriceBookDetail; onClose: () => void; onOpen: (id: string) => void }) {
  const [note, setNote] = useState("");
  const copy = useCellMutation(
    (api, v: { note: string }) => api.commercial.call("POST", "/v1/commercial/price-books", { copy_of: b.price_book_id, note: v.note.trim() || null }),
    {
      invalidate: [COMMERCIAL_KEY],
      success: "Editable copy created",
      onSuccess: (r) => {
        const id = (r as { price_book_id?: string }).price_book_id;
        onClose();
        if (id) onOpen(id);
      },
    },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !copy.isPending && onClose()}
      title={`Copy v${b.version} as a new draft`}
      description="The copy starts with the same rates. Change what you need, then approve and publish it."
      footer={
        <>
          <Button variant="secondary" isDisabled={copy.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={copy.isPending} onPress={() => copy.mutate({ note })}>
            Create the draft
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Note (optional)" value={note} onChange={setNote} placeholder="For example: Q1 repricing" />
        {copy.error ? <ApiErrorView error={copy.error} title="The copy was not created" /> : null}
      </div>
    </Dialog>
  );
}
