"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { BillingPayment, PaymentAttempt } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DateTime, KeyValues } from "@zoiko/ui";
import { CreditCard } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { AsOf } from "./as-of";
import { BILLING_KEY, money } from "./shared";

const KEY = [...BILLING_KEY, "payment"];

/** What the company owes now and the card it pays with. The card is entered on Stripe's hosted page, never here: this
 * page only starts that step and, when Stripe sends the person back, asks the cell to verify and save the result. */
export function PaymentSection() {
  const qc = useQueryClient();
  const payment = useCellQuery(KEY, (api) => api.billing.payment());
  const start = useCellMutation((api) => api.billing.startPaymentSetup(), {
    onSuccess: (r) => window.location.assign(r.url),
  });
  const complete = useCellMutation((api, sessionId: string) => api.billing.completePaymentSetup(sessionId), {
    invalidate: [KEY],
    success: "Payment method saved",
  });
  const online = useCellMutation((api, invoiceId: string) => api.billing.payOnline(invoiceId), {
    onSuccess: (r) => window.location.assign(r.url),
  });
  const completeOnline = useCellMutation((api, sessionId: string) => api.billing.completeOnlinePayment(sessionId), {
    invalidate: [KEY, [...BILLING_KEY, "invoices"], [...BILLING_KEY, "state"]],
    success: "Payment received. Thank you.",
  });
  const [paid, setPaid] = useState<{ ok: boolean; text: string } | null>(null);
  const pay = useCellMutation((api, v: { id: string; key: string }) => api.billing.payInvoice(v.id, v.key), {
    invalidate: [KEY, [...BILLING_KEY, "invoices"], [...BILLING_KEY, "state"]],
    onSuccess: (r) => setPaid(r.status === "CAPTURED" ? { ok: true, text: "Payment received. Thank you." } : { ok: false, text: failureText(r.failure_code) }),
  });
  const { session } = useRuntime();
  const ready = session?.authenticated === true;
  const returned = useRef(false);

  // Stripe returns to /admin/billing?payment=saved (a card) or payment=paid (an invoice paid on its page) with
  // &session_id=cs_...: finish the step once, then clean the address.
  // Wait for the page's own session (it carries the request-verification token); a write sent before it loads is refused.
  useEffect(() => {
    if (returned.current || !ready) return;
    const url = new URL(window.location.href);
    const sessionId = url.searchParams.get("session_id");
    const outcome = url.searchParams.get("payment");
    if ((outcome === "saved" || outcome === "paid") && sessionId) {
      returned.current = true;
      const settled = () => {
        url.searchParams.delete("payment");
        url.searchParams.delete("session_id");
        window.history.replaceState(null, "", url.pathname + (url.search || ""));
        void qc.invalidateQueries({ queryKey: KEY });
      };
      if (outcome === "saved") complete.mutate(sessionId, { onSettled: settled });
      else completeOnline.mutate(sessionId, { onSettled: settled });
    }
  }, [complete, completeOnline, qc, ready]);

  const cancelled = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("payment") === "cancelled";

  return (
    <div className="space-y-4">
      {complete.isPending || completeOnline.isPending ? (
        <Banner tone="info" title="Payment verification in progress">
          <span role="status">We are confirming this with the payment provider. Nothing is marked paid or saved until that finishes. Please keep this page open.</span>
        </Banner>
      ) : null}
      {cancelled ? <Banner tone="info" title="No card was saved">You left the card page before finishing. Nothing changed.</Banner> : null}
      {complete.isError ? <ApiErrorView error={complete.error} title="Couldn't save the card" /> : null}
      {pay.isError ? <ApiErrorView error={pay.error} title="Couldn't take the payment" /> : null}
      {online.isError ? <ApiErrorView error={online.error} title="Couldn't open the payment page" onRetry={() => online.reset()} /> : null}
      {completeOnline.isError ? <ApiErrorView error={completeOnline.error} title="Couldn't confirm the payment" /> : null}
      {completeOnline.isSuccess ? <Banner tone="ok" title="Payment received">Your payment was confirmed and your invoice is settled.</Banner> : null}
      {paid ? <Banner tone={paid.ok ? "ok" : "danger"} title={paid.ok ? "Payment received" : "The payment did not go through"}>{paid.text}</Banner> : null}
      <AsOf at={payment.dataUpdatedAt} />
      <QueryView query={payment}>{(p) => <Body p={p} onOnline={(id) => online.mutate(id)} onlineBusy={online.isPending} onRetry={() => void payment.refetch()} refetching={payment.isFetching} onStart={() => start.mutate(undefined as never)} starting={start.isPending} startError={start.error} onPay={(id) => { setPaid(null); pay.mutate({ id, key: newKey() }); }} paying={pay.isPending} />}</QueryView>
    </div>
  );
}

function Body({ p, onOnline, onlineBusy, onRetry, refetching, onStart, starting, startError, onPay, paying }: { p: BillingPayment; onOnline: (invoiceId: string) => void; onlineBusy: boolean; onRetry: () => void; refetching: boolean; onStart: () => void; starting: boolean; startError: unknown; onPay: (invoiceId: string) => void; paying: boolean }) {
  const due = p.amount_due.filter((d) => Number(d.amount) > 0);
  const expiry = p.method ? cardExpiry(p.method.exp_month, p.method.exp_year) : null;
  const lastFailed = due.length > 0 && p.payments[0]?.status === "FAILED" ? p.payments[0] : null;
  return (
    <>
      {lastFailed ? (
        <Banner tone="danger" title="Your last payment did not go through">
          {failureAdvice(lastFailed.failure_code)}
          {p.can_manage
            ? needsBank(lastFailed.failure_code)
              ? " Use Pay on Stripe on the invoice below to confirm it with your bank."
              : " Use Pay now on the invoice below, or replace the card."
            : " Ask the company owner to update the card."}
        </Banner>
      ) : null}
      {expiry === "expired" ? (
        <Banner tone="danger" title="Your card has expired">Replace it so your invoices can be paid automatically.</Banner>
      ) : expiry === "soon" ? (
        <Banner tone="warn" title="Your card expires soon">Replace it before the end of the month so automatic payment keeps working.</Banner>
      ) : null}
      <Card title="Amount due now" description="Issued invoices that are not yet paid. Your estimated bill for the current month is under Bill preview.">
        {due.length === 0 ? (
          <p className="text-sm text-ink-muted">Nothing is due right now.</p>
        ) : (
          <>
            <ul className="space-y-1">
              {due.map((d) => (
                <li key={d.currency} className="text-2xl font-semibold tabular-nums text-ink">
                  {money(d.amount, d.currency)}
                </li>
              ))}
            </ul>
            <ul className="mt-3 divide-y divide-line text-sm">
              {p.open_invoices.map((i) => (
                <li key={i.invoice_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span className="font-mono text-xs">{i.number ?? "Invoice"}</span>
                  <span className="text-ink-muted">{i.due_at ? `due ${new Date(i.due_at).toLocaleDateString("en-GB")}` : ""}</span>
                  <span className="tabular-nums">{money(i.outstanding, i.currency)}</span>
                  {p.can_manage ? (
                    <span className="flex gap-2">
                      {p.method && !needsBank(lastFailed?.failure_code ?? null) ? (
                        <Button size="xs" variant="secondary" isDisabled={paying || onlineBusy} onPress={() => onPay(i.invoice_id)}>
                          Pay now
                        </Button>
                      ) : null}
                      <Button
                        size="xs"
                        variant={p.method && !needsBank(lastFailed?.failure_code ?? null) ? "ghost" : "primary"}
                        pending={onlineBusy}
                        isDisabled={paying}
                        onPress={() => onOnline(i.invoice_id)}
                      >
                        Pay on Stripe
                      </Button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>

      <Card title="Payment method" description="Invoices are charged to this card automatically when they fall due. The card number is entered on Stripe's secure page and is never stored or seen by Zoiko iD.">
        {!p.configured ? (
          <Banner tone="warn" title="Card payments are not switched on yet">The platform has no payment provider configured, so a card cannot be saved here yet.</Banner>
        ) : !p.account_linked ? (
          <Banner tone="info" title="Your billing account is not set up yet">Your billing account is created by Zoiko when your plan is agreed. Contact your account manager, then come back to add a card.</Banner>
        ) : (
          <div className="space-y-4">
            {p.method ? (
              <KeyValues
                columns={2}
                items={[
                  ["Card", <span key="c" className="inline-flex items-center gap-2"><CreditCard aria-hidden className="size-4" /> {label(p.method.brand)} ending {p.method.last4}</span>],
                  ["Expires", `${String(p.method.exp_month).padStart(2, "0")}/${p.method.exp_year}`],
                  ["Used for", <Badge key="u" tone="ok">Automatic payment</Badge>],
                ]}
              />
            ) : (
              p.method_error ? (
                <div className="flex flex-wrap items-center gap-3">
                  <p className="text-sm text-ink-muted">We couldn't check your saved card with the payment provider just now. This is usually brief and nothing has changed.</p>
                  <Button size="xs" variant="secondary" pending={refetching} onPress={onRetry}>Check again</Button>
                </div>
              ) : (
                <p className="text-sm text-ink-muted">No card saved yet.</p>
              )
            )}
            {startError ? <ApiErrorView error={startError} title="Couldn't open the card page" /> : null}
            {p.can_manage ? (
              <Button pending={starting} icon={<CreditCard aria-hidden className="size-4" />} onPress={onStart}>
                {p.method ? "Replace card" : "Add a card"}
              </Button>
            ) : (
              <p className="text-sm text-ink-muted">Only the company owner can add or replace the card.</p>
            )}
          </div>
        )}
      </Card>

      <Card title="Payment history" description="The latest attempts to collect your invoices.">
        {p.payments.length === 0 ? (
          <p className="text-sm text-ink-muted">No payments yet.</p>
        ) : (
          <table className="w-full text-sm">
            <caption className="sr-only">Payment attempts</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
                <th scope="col" className="py-2">Date</th>
                <th scope="col" className="py-2">Invoice</th>
                <th scope="col" className="py-2 text-right">Amount</th>
                <th scope="col" className="py-2 text-right">Result</th>
              </tr>
            </thead>
            <tbody>
              {p.payments.map((a, n) => (
                <tr key={`${a.at}-${n}`} className="border-b border-border last:border-0">
                  <td className="py-2"><DateTime value={a.at} /></td>
                  <td className="py-2 font-mono text-xs">{a.invoice_number ?? "—"}</td>
                  <td className="tabular-nums py-2 text-right">{money(a.amount, a.currency)}</td>
                  <td className="py-2 text-right">{resultBadge(a)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function resultBadge(a: PaymentAttempt) {
  return a.status === "CAPTURED" ? <Badge tone="ok">Paid</Badge> : <Badge tone="warn">{failureText(a.failure_code)}</Badge>;
}

/** What a refused payment means, in plain words. Unknown codes are shown as a generic refusal, never as a raw code. */
function failureText(code: string | null): string {
  switch (code) {
    case "NO_PAYMENT_METHOD":
      return "No card saved";
    case "PROVIDER_UNAVAILABLE":
      return "Payment provider unavailable, try again";
    case "INSUFFICIENT_FUNDS":
      return "Insufficient funds";
    case "EXPIRED_CARD":
      return "Card expired";
    case "CARD_DECLINED":
    case "GENERIC_DECLINE":
      return "Card declined";
    case "AUTHENTICATION_REQUIRED":
    case "PAYMENT_REQUIRES_ACTION":
      return "Bank confirmation needed";
    case "INCORRECT_CVC":
    case "INVALID_CVC":
      return "Card security code refused";
    case "PAYMENT_PROCESSING":
      return "Processing";
    default:
      return "Not completed";
  }
}

/** Whether the bank has to confirm the payment with the cardholder, which only Stripe's own page can ask for. */
function needsBank(code: string | null): boolean {
  return code === "AUTHENTICATION_REQUIRED" || code === "PAYMENT_REQUIRES_ACTION";
}

/** What the person can do about a failed payment. Never a raw code. */
function failureAdvice(code: string | null): string {
  switch (code) {
    case "AUTHENTICATION_REQUIRED":
    case "PAYMENT_REQUIRES_ACTION":
      return "Your bank needs you to confirm this payment.";
    case "INSUFFICIENT_FUNDS":
      return "The card did not have enough available funds.";
    case "EXPIRED_CARD":
      return "The card has expired.";
    case "NO_PAYMENT_METHOD":
      return "There is no card saved to pay with.";
    case "PROVIDER_UNAVAILABLE":
      return "We could not reach the payment provider. It will be tried again.";
    case "CARD_DECLINED":
    case "GENERIC_DECLINE":
      return "Your bank declined the card.";
    default:
      return "The payment could not be completed.";
  }
}

/** Whether a card is past its last month or ends within the next 30 days. */
function cardExpiry(month: number, year: number): "expired" | "soon" | null {
  if (!month || !year) return null;
  const end = new Date(year, month, 0, 23, 59, 59); // the last moment of the expiry month
  const now = new Date();
  if (end < now) return "expired";
  return end.getTime() - now.getTime() < 30 * 24 * 3600 * 1000 ? "soon" : null;
}

/** One per click on Pay now: the same key can never charge twice. */
function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k${Date.now()}${Math.random().toString(36).slice(2, 10)}`;
}

function label(brand: string): string {
  return brand ? brand.charAt(0).toUpperCase() + brand.slice(1) : "Card";
}
