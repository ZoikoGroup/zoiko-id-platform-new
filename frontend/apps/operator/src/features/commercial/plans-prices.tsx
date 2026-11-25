"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { PriceBookDetail, PriceBookSummary } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, Select, TextField } from "@zoiko/ui";
import { CheckCircle2, Circle } from "lucide-react";
import { useMemo, useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";
import { useDirectMode } from "./mode";
import {
  PRICE_ITEMS,
  PLAN_LABEL,
  SELF_SERVE_PLANS,
  approvedBook,
  bigChange,
  changeSentence,
  itemById,
  lineOf,
  parseAmount,
  planLabel,
  priceText,
  readyToSell,
  setupChecklist,
  type PriceItemId,
} from "./plans-prices-logic";

/** The simple way to see and change what customers pay. Pick what to change and the plan from the dropdowns, type the amount, save. The page explains each step in
 * plain words; the cell still validates and applies the change, and the older price book screens stay under "Price books (advanced)". */
export function PlansAndPrices() {
  const books = useCellQuery([...COMMERCIAL_KEY, "books"], (api) => api.commercial.books());
  const live = approvedBook(books.data?.items ?? []);
  const book = useCellQuery([...COMMERCIAL_KEY, "book", live?.price_book_id ?? "none"], (api) => api.commercial.book(live!.price_book_id), { enabled: Boolean(live) });
  return (
    <div className="space-y-4">
      <HowItWorks />
      <QueryView query={books}>
        {() => (
          <>
            <Checklist live={live} book={book.data} />
            <WhatYouCharge live={live} book={book.data} />
            <ChangePrice live={live} book={book.data} />
          </>
        )}
      </QueryView>
    </div>
  );
}

function HowItWorks() {
  return (
    <Card title="How pricing works" description="Three things to know before you change a price.">
      <ol className="list-decimal space-y-1.5 pl-5 text-sm text-ink">
        <li>
          <span className="font-medium">One price for every region.</span> A plan costs the same wherever the company is.
        </li>
        <li>
          <span className="font-medium">New purchases only.</span> A company that already has a plan keeps the price it signed. Your change applies to anyone who buys or is
          set up after you save.
        </li>
        <li>
          <span className="font-medium">Nothing is lost.</span> Every change is saved as a new version of the price list with your name on it. The old versions are kept.
        </li>
      </ol>
    </Card>
  );
}

function Checklist({ live, book }: { live: PriceBookSummary | undefined; book: PriceBookDetail | undefined }) {
  const checks = useMemo(() => setupChecklist(live, book?.lines ?? []), [live, book]);
  const ready = readyToSell(checks);
  return (
    <Card title="Is it ready for customers?" description="Customers can buy Launch, Business and Scale online when every line below is a tick.">
      <ul className="space-y-2">
        {checks.map((c) => (
          <li key={c.id} className="flex items-start gap-2 text-sm">
            {c.ok ? <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-ok-fg" /> : <Circle aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-subtle" />}
            <span>
              <span className="font-medium text-ink">{c.label}.</span> <span className="text-ink-muted">{c.detail}</span>
              <span className="sr-only">{c.ok ? " Done." : " Not done yet."}</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-3" role="status">
        {ready ? (
          <Badge tone="ok">Ready: customers can buy a plan online</Badge>
        ) : (
          <Badge tone="warn">Not ready yet: finish the lines without a tick</Badge>
        )}
      </div>
    </Card>
  );
}

function WhatYouCharge({ live, book }: { live: PriceBookSummary | undefined; book: PriceBookDetail | undefined }) {
  if (!live || !book) {
    return (
      <Banner tone="warn" title="There are no published prices yet">
        Approve a price book first (Price books, advanced). After that you can change each price here.
      </Banner>
    );
  }
  const plans = ["DEVELOPER", ...SELF_SERVE_PLANS, "ENTERPRISE"];
  const base = PRICE_ITEMS[0]!;
  const usage = PRICE_ITEMS.slice(1).filter((i) => book.lines.some((l) => l.sku === i.id));
  return (
    <Card title="What customers pay today" description={`Price list version ${live.version}, in ${live.currency}. The same in every region.`} padded={false}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Monthly plan fee by plan</caption>
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
              <th scope="col" className="px-4 py-2">
                Plan
              </th>
              <th scope="col" className="px-4 py-2">
                Monthly fee
              </th>
            </tr>
          </thead>
          <tbody>
            {plans.map((p) => {
              const fee = lineOf(book.lines, base.id, p);
              return (
                <tr key={p} className="border-b border-border last:border-0">
                  <th scope="row" className="px-4 py-2 text-left font-medium">
                    {planLabel(p)}
                  </th>
                  <td className="px-4 py-2 tabular-nums">
                    {fee ? (Number(fee.unit_rate) === 0 ? "Free" : `${p === "ENTERPRISE" ? "From " : ""}${priceText(fee.unit_rate, live.currency)} per month`) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {usage.length ? (
        <details className="border-t border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-brand-700">Show what customers pay above what their plan includes</summary>
          <div className="overflow-x-auto px-4 pb-4">
            <table className="w-full text-sm">
              <caption className="sr-only">Usage rates above the included amounts, by plan</caption>
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
                  <th scope="col" className="py-2">
                    Item
                  </th>
                  {SELF_SERVE_PLANS.map((p) => (
                    <th key={p} scope="col" className="py-2 text-right">
                      {PLAN_LABEL[p]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {usage.map((i) => (
                  <tr key={i.id} className="border-b border-border last:border-0">
                    <th scope="row" className="py-2 text-left font-normal">
                      {i.label} <span className="text-ink-subtle">({i.per})</span>
                    </th>
                    {SELF_SERVE_PLANS.map((p) => {
                      const l = lineOf(book.lines, i.id, p);
                      return (
                        <td key={p} className="py-2 text-right tabular-nums">
                          {l ? priceText(l.unit_rate, live.currency) : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </Card>
  );
}

function ChangePrice({ live, book }: { live: PriceBookSummary | undefined; book: PriceBookDetail | undefined }) {
  const direct = useDirectMode();
  const qc = useQueryClient();
  const [itemId, setItemId] = useState<PriceItemId>("ZID-BASE");
  const [plan, setPlan] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const item = itemById(itemId)!;
  const currency = live?.currency ?? "USD";
  const planOptions = item.plans
    .filter((p) => !book || lineOf(book.lines, item.id, p))
    .map((p) => ({ id: p, label: planLabel(p) }));
  const current = plan && book ? (lineOf(book.lines, item.id, plan)?.unit_rate ?? null) : null;
  const parsed = amount.trim() === "" ? null : parseAmount(amount, item);
  const valid = plan !== null && parsed?.ok === true;
  const swing = valid && parsed?.ok ? bigChange(current, parsed.value) : null;
  const same = valid && parsed?.ok && current !== null && Number(current) === Number(parsed.value);

  const save = useCellMutation(
    (api, v: { plan: string; item: string; amount: string }) => api.commercial.call("POST", "/v1/commercial/plan-prices", v),
    {
      onSuccess: (r, v) => {
        void qc.invalidateQueries({ queryKey: COMMERCIAL_KEY });
        const waiting = r.awaiting_approval === true;
        setDone(
          waiting
            ? `${changeSentence(itemById(v.item)!, v.plan, current, v.amount, currency)} It is saved as a draft and goes live when a second operator approves it.`
            : `${changeSentence(itemById(v.item)!, v.plan, current, v.amount, currency)} It is live now for new purchases.`,
        );
        setAmount("");
        setUnderstood(false);
      },
    },
  );

  if (!live || !book) return null;
  return (
    <Card title="Change a price" description="Choose what to change, choose the plan, then type the new price.">
      <form
        className="space-y-4"
        aria-label="Change a price"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid && parsed?.ok && plan) save.mutate({ plan, item: item.id, amount: parsed.value });
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="1. What do you want to change?"
            options={PRICE_ITEMS.filter((i) => book.lines.some((l) => l.sku === i.id)).map((i) => ({ id: i.id, label: i.label }))}
            value={itemId}
            onChange={(v) => {
              setItemId(v as PriceItemId);
              setPlan(null);
              setDone(null);
            }}
            description={item.help}
          />
          <Select
            label="2. Which plan?"
            options={planOptions}
            value={plan}
            onChange={(v) => {
              setPlan(v);
              setDone(null);
            }}
            description="Developer is always free, so it is not in the list."
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label={`3. New price (${currency}, ${item.per})`}
            value={amount}
            onChange={(v) => {
              setAmount(v);
              setDone(null);
            }}
            inputMode="decimal"
            placeholder={item.id === "ZID-BASE" ? "for example 349" : "for example 0.015"}
            description={item.decimals === 2 ? "Type the amount yourself. Up to two decimals." : "Type the amount yourself. Small decimals such as 0.015 are kept exactly."}
            errorMessage={parsed && !parsed.ok ? parsed.error : undefined}
          />
          <div className="rounded-md border border-border bg-surface-sunken p-3 text-sm" aria-live="polite">
            <p className="text-xs uppercase text-ink-subtle">Today</p>
            <p className="mt-1 font-medium text-ink">
              {plan === null ? "Choose a plan to see its current price." : current === null ? "No price set yet." : `${priceText(current, currency)} ${item.per}`}
            </p>
          </div>
        </div>

        {valid && parsed?.ok && plan ? (
          <div className="space-y-3" aria-live="polite">
            <Banner tone="info" title="What will happen">
              {changeSentence(item, plan, current, parsed.value, currency)} This is the same in every region and applies to new purchases only. Companies that already have the plan keep
              the price they signed.{" "}
              {direct ? "You approve it yourself, so it goes live as soon as you save." : "A second operator must approve it before it goes live."}
            </Banner>
            {same ? <Banner tone="warn" title="This is the same as today's price">Nothing would change.</Banner> : null}
            {swing === "much-higher" ? (
              <Banner tone="warn" title="This is more than double today's price">
                Check the amount for an extra digit before you save.
              </Banner>
            ) : null}
            {swing === "much-lower" ? (
              <Banner tone="warn" title="This is less than half of today's price">
                Check the amount for a missing digit before you save.
              </Banner>
            ) : null}
            <Checkbox isSelected={understood} onChange={setUnderstood}>
              I checked the amount and I understand it applies to new purchases.
            </Checkbox>
          </div>
        ) : null}

        <div className="flex items-center gap-3">
          <Button type="submit" isDisabled={!valid || !understood || Boolean(same) || save.isPending}>
            {save.isPending ? "Saving…" : "Save price"}
          </Button>
          {!valid ? <span className="text-xs text-ink-subtle">Choose a plan and type a valid amount to continue.</span> : null}
        </div>
        {done ? (
          <Banner tone="ok" title="Price saved">
            {done}
          </Banner>
        ) : null}
        {save.isError ? <ApiErrorView error={save.error} title="The price was not saved" onRetry={() => save.reset()} /> : null}
      </form>
    </Card>
  );
}
