"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { PlanRequest, WaitingPlanOffer } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { Tag } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";
import { useDirectMode } from "./mode";
import { OFFER_DAYS, approvedBook, lineOf, offerProblem, offerSentence, parseAmount, planLabel, priceText, PRICE_ITEMS, reductionPercent } from "./plans-prices-logic";

export type PlanRequestRow = PlanRequest & { tenant_id: string; company: string | null };

/** The plan's list price in the approved price book, or null when there is none. */
function useListPrice(plan: string): { list: string | null; currency: string } {
  const books = useCellQuery([...COMMERCIAL_KEY, "books"], (api) => api.commercial.books());
  const live = approvedBook(books.data?.items ?? []);
  const book = useCellQuery([...COMMERCIAL_KEY, "book", live?.price_book_id ?? "none"], (api) => api.commercial.book(live!.price_book_id), { enabled: Boolean(live) });
  const line = book.data ? lineOf(book.data.lines, "ZID-BASE", plan) : undefined;
  return { list: line ? line.unit_rate : null, currency: live?.currency ?? "USD" };
}

/** Answer a company's request with the monthly price it will pay. The price is typed by hand. It moves no plan and charges nothing: the company buys at it, pays on Stripe, and the plan
 * switches on only after that payment is confirmed. A price below the list price is a reduction and is held to the discount authority by the cell. */
export function OfferDialog({ request, onClose }: { request: PlanRequestRow; onClose: () => void }) {
  const direct = useDirectMode();
  const qc = useQueryClient();
  const { list, currency } = useListPrice(request.plan);
  const [amount, setAmount] = useState("");
  const [days, setDays] = useState("14");
  const [note, setNote] = useState("");
  const company = request.company ?? "This company";
  const problem = amount.trim() === "" ? null : offerProblem(amount, list);
  const parsed = parseAmount(amount, PRICE_ITEMS[0]!);
  const ready = list !== null && parsed.ok && !problem;
  const save = useCellMutation((api) => api.commercial.offerPlanPrice(request.request_id, { amount: parsed.ok ? parsed.value : "", valid_days: Number(days), note }), {
    invalidate: [COMMERCIAL_KEY],
    success: "Price sent to the company",
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: COMMERCIAL_KEY });
      onClose();
    },
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !save.isPending && onClose()}
      title={`Make an offer: ${planLabel(request.plan)} for ${company}`}
      description="The price is for this company only. They then buy the plan at that price and pay it on Stripe. Nothing is switched on until the payment is confirmed."
      footer={
        <>
          <Button variant="secondary" isDisabled={save.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={save.isPending} isDisabled={!ready} icon={<Tag aria-hidden className="size-4" />} onPress={() => save.mutate(undefined)}>
            Send the price
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <dl className="grid gap-3 rounded-lg border border-border bg-surface-sunken p-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-ink-subtle">List price</dt>
            <dd className="font-medium text-ink">{list === null ? "None yet" : `${priceText(list, currency)} per month`}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-subtle">They asked to pay</dt>
            <dd className="font-medium text-ink">{request.desired_amount ? `${priceText(request.desired_amount, currency)} per month` : "Did not say"}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-subtle">Their note</dt>
            <dd className="text-ink">{request.note ?? "—"}</dd>
          </div>
        </dl>
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label={`Price for ${company} (${currency} per month)`}
            value={amount}
            onChange={setAmount}
            inputMode="decimal"
            placeholder={list ?? "for example 4500"}
            description="Type the amount yourself. Up to two decimals. Never above the list price."
            errorMessage={problem ?? undefined}
          />
          <Select label="This price can be used for" options={OFFER_DAYS} value={days} onChange={setDays} description="After that the company pays the list price again." />
        </div>
        <TextField label="Note to the company (optional)" value={note} onChange={setNote} multiline rows={3} />
        {ready && parsed.ok && list !== null ? (
          <Banner tone="info" title="What will happen">
            {offerSentence(company, request.plan, parsed.value, list, currency, days)}{" "}
            {Number(reductionPercent(list, parsed.value)) === 0
              ? "The list price needs no approval."
              : direct
                ? "You approve this reduction yourself, so the company can buy at it straight away."
                : "A different operator with the right role must approve this reduction before the company can buy at it."}
          </Banner>
        ) : null}
        {save.isError ? <ApiErrorView error={save.error} title="The price was not sent" onRetry={() => save.reset()} /> : null}
      </div>
    </Dialog>
  );
}

/** Prices made for one company that still wait: for a second operator's approval, or for the company to buy at them. */
export function PlanOffersView() {
  const direct = useDirectMode();
  const list = useCellQuery([...COMMERCIAL_KEY, "plan-offers"], (api) => api.commercial.planOffers());
  const approve = useCellMutation((api, id: string) => api.commercial.approvePlanOffer(id), { invalidate: [COMMERCIAL_KEY], success: "Price approved: the company can buy at it" });
  const withdraw = useCellMutation((api, id: string) => api.commercial.withdrawPlanOffer(id), { invalidate: [COMMERCIAL_KEY], success: "Price withdrawn" });
  const columns: Column<WaitingPlanOffer>[] = [
    {
      key: "c",
      header: "Company",
      pinned: true,
      render: (o) => (
        <span className="flex flex-col">
          <span className="font-medium">{o.company ?? "Unnamed company"}</span>
          <span className="font-mono text-2xs text-ink-subtle">{o.tenant_id}</span>
        </span>
      ),
      sortValue: (o) => o.company ?? o.tenant_id,
    },
    { key: "p", header: "Plan", render: (o) => <Badge tone="info">{planLabel(o.plan)}</Badge> },
    {
      key: "a",
      header: "Their price",
      render: (o) =>
        o.offer ? (
          <span className="tabular-nums">
            {priceText(o.offer.amount, o.offer.currency)}
            <span className="ml-1 text-xs text-ink-subtle">
              (list {priceText(o.offer.list_amount, o.offer.currency)}, {o.offer.reduction_percent}% below)
            </span>
          </span>
        ) : (
          "—"
        ),
    },
    {
      key: "s",
      header: "State",
      render: (o) =>
        o.offer?.state === "PENDING_APPROVAL" ? (
          <Badge tone="warn">Waiting for approval</Badge>
        ) : (
          <Badge tone="ok">{`Open until ${o.offer ? new Date(o.offer.expires_at).toLocaleDateString("en-GB") : ""}`}</Badge>
        ),
    },
    {
      key: "x",
      header: "Action",
      align: "end",
      render: (o) => (
        <span className="flex justify-end gap-2">
          {o.offer?.state === "PENDING_APPROVAL" && !direct ? (
            <Button size="xs" isDisabled={approve.isPending} onPress={() => approve.mutate(o.request_id)}>
              Approve
            </Button>
          ) : null}
          <Button size="xs" variant="secondary" isDisabled={withdraw.isPending} onPress={() => withdraw.mutate(o.request_id)}>
            Withdraw
          </Button>
        </span>
      ),
    },
  ];
  return (
    <QueryView query={list}>
      {(d) => (
        <Card title="Prices made for one company" description="A price below the list price waits here for a second operator when you are not in single-operator mode. Once it is open, the company can buy the plan at it until it ends." padded={false}>
          <DataTable<WaitingPlanOffer>
            caption="Prices waiting for approval or for the company to buy"
            columns={columns}
            rows={d.items}
            rowKey={(o) => o.request_id}
            empty={<EmptyState title="No prices waiting" icon={Tag} compact>A price you make for a company appears here until it is used or ends.</EmptyState>}
          />
          {approve.isError ? <div className="p-4"><ApiErrorView error={approve.error} title="The price was not approved" onRetry={() => approve.reset()} /></div> : null}
          {withdraw.isError ? <div className="p-4"><ApiErrorView error={withdraw.error} title="The price was not withdrawn" onRetry={() => withdraw.reset()} /></div> : null}
        </Card>
      )}
    </QueryView>
  );
}
