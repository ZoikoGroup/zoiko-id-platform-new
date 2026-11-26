"use client";

import type { VerifyApiPrice, VerifyApiPriceIn } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, EmptyState, Select, TextField } from "@zoiko/ui";
import { Tag } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";

const KEY = [...COMMERCIAL_KEY, "verify-prices"];

/** The first day of next month, as the earliest a price may start (a price never starts in a month that has begun, so nothing already used is repriced). */
export function earliestStart(now: Date): string {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return d.toISOString().slice(0, 10);
}

/** Why a proposal cannot be sent yet, or null. The cell checks everything again. */
export function priceProblem(d: { meter: string; currency: string; rate: string; start: string }, now: Date): string | null {
  if (!d.meter) return "Choose what is being priced.";
  if (!d.currency) return "Choose a currency.";
  if (!/^\d{1,6}(\.\d{1,6})?$/.test(d.rate.trim())) return "Give the price as a number with up to 6 decimal places, for example 1.25.";
  if (Number(d.rate) > 100000) return "The price is at most 100000.";
  if (!/^\d{4}-\d{2}-01$/.test(d.start.trim())) return "A price starts on the first day of a month, for example 2027-01-01.";
  if (d.start.trim() < earliestStart(now)) return `A price cannot start before ${earliestStart(now)}: nothing already used is repriced.`;
  return null;
}

const STATE: Record<string, { label: string; tone: "info" | "ok" | "danger" }> = {
  PROPOSED: { label: "Waiting for a second person", tone: "info" },
  APPROVED: { label: "Approved", tone: "ok" },
  REJECTED: { label: "Rejected", tone: "danger" },
};

/** What a verification costs a partner (decision D12). One person proposes a price for a usage fact from a later month; a DIFFERENT person approves it. Companies see it as an indicative cost on their usage screen. It is not
 * an invoice and does not touch the commercial price book. */
export function VerifyPricesView() {
  const list = useCellQuery(KEY, (api) => api.commercial.verifyPrices());
  return (
    <div className="space-y-4">
      <Banner tone="info" title="Indicative cost only">
        Approved prices appear on each company's usage screen as an indicative cost with the price named. They are not invoices and include no tax. Invoicing a verification is a separate step in the commercial estate.
      </Banner>
      <Propose currencies={list.data?.currencies ?? []} meters={list.data?.meters ?? []} />
      <Card title="Price list" description="Proposed, approved and rejected prices, newest month first. An approved price is never edited: a change is a new price from a later month.">
        {list.isPending ? (
          <p className="text-sm text-ink-muted">Loading…</p>
        ) : list.error ? (
          <ApiErrorView error={list.error} title="The prices could not be loaded" onRetry={() => void list.refetch()} />
        ) : list.data.items.length === 0 ? (
          <EmptyState title="No prices yet" icon={Tag} compact>
            Until a price is approved, companies see usage counts only.
          </EmptyState>
        ) : (
          <div className="space-y-3">
            {list.data.items.map((p) => (
              <PriceRow key={p.price_id} p={p} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function Propose({ currencies, meters }: { currencies: string[]; meters: { id: string; label: string }[] }) {
  const [draft, setDraft] = useState({ meter: "", currency: "", rate: "", start: earliestStart(new Date()) });
  const problem = priceProblem(draft, new Date());
  const send = useCellMutation<void, VerifyApiPrice>((api) => api.commercial.verifyPricePropose({ meter: draft.meter, currency: draft.currency, unit_rate: draft.rate.trim(), effective_from: draft.start.trim() } satisfies VerifyApiPriceIn), {
    invalidate: [KEY],
    success: "Price proposed. A different person must approve it.",
  });
  return (
    <Card title="Propose a price" description="From the first day of a later month. A different person approves it.">
      <div className="grid gap-3 sm:grid-cols-2">
        <Select label="What is priced" options={meters.map((m) => ({ id: m.id, label: m.label }))} value={draft.meter} onChange={(v) => setDraft({ ...draft, meter: v })} />
        <Select label="Currency" options={currencies.map((c) => ({ id: c, label: c }))} value={draft.currency} onChange={(v) => setDraft({ ...draft, currency: v })} />
        <TextField label="Price per unit" value={draft.rate} onChange={(v) => setDraft({ ...draft, rate: v })} description="A number such as 1.25, with up to 6 decimal places." />
        <TextField label="Starts on" value={draft.start} onChange={(v) => setDraft({ ...draft, start: v })} description="The first day of a month, from next month on." />
      </div>
      {draft.meter || draft.currency || draft.rate ? <p className="mt-2 text-xs text-ink-muted">{problem}</p> : null}
      <div className="mt-3">
        <Button size="sm" isDisabled={problem !== null || send.isPending} pending={send.isPending} onPress={() => send.mutate(undefined)}>
          Propose price
        </Button>
      </div>
      {send.error ? <ApiErrorView error={send.error} title="The price was not proposed" onRetry={() => send.reset()} /> : null}
    </Card>
  );
}

function PriceRow({ p }: { p: VerifyApiPrice }) {
  const [note, setNote] = useState("");
  const opts = { invalidate: [KEY] };
  const approve = useCellMutation<void, VerifyApiPrice>((api) => api.commercial.verifyPriceApprove(p.price_id, note), { ...opts, success: "Approved. Companies see it from its month." });
  const reject = useCellMutation<void, VerifyApiPrice>((api) => api.commercial.verifyPriceReject(p.price_id, note), { ...opts, success: "Rejected" });
  const busy = approve.isPending || reject.isPending;
  const error = approve.error ?? reject.error;
  const s = STATE[p.status] ?? { label: p.status, tone: "info" as const };
  return (
    <section aria-label={`${p.label} ${p.currency} from ${p.effective_from}`} className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{p.label}</span>
        <Badge tone={s.tone}>{s.label}</Badge>
        <span className="font-mono text-sm">
          {p.currency} {p.unit_rate}
        </span>
        <span className="text-xs text-ink-muted">from {p.effective_from}</span>
      </div>
      {p.note ? <p className="text-sm text-ink-muted">{p.note}</p> : null}
      {p.status === "PROPOSED" ? (
        <div className="flex flex-wrap items-end gap-2">
          <TextField label="Note (needed to reject)" value={note} onChange={setNote} />
          <Button size="sm" isDisabled={busy} pending={approve.isPending} onPress={() => approve.mutate(undefined)}>
            Approve
          </Button>
          <Button size="sm" variant="secondary" isDisabled={busy || note.trim().length < 3} onPress={() => reject.mutate(undefined)}>
            Reject
          </Button>
        </div>
      ) : null}
      {error ? <ApiErrorView error={error} title="That was not recorded" onRetry={() => (approve.reset(), reject.reset())} /> : null}
    </section>
  );
}
