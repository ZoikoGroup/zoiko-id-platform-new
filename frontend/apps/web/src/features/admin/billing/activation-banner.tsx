"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { ActivationWait } from "@zoiko/api-client";
import { Banner, Button, Card } from "@zoiko/ui";
import { CreditCard } from "lucide-react";
import { useEffect, useRef } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { BILLING_KEY, money } from "./shared";

const KEY = [...BILLING_KEY, "activations"];

/** The plan the operator approved, waiting for its first month's fee. The plan does not change until Stripe confirms the payment: coming back from Stripe
 * only asks the cell to check, and "paid" appears only once the cell says the plan is on. Shown on every billing tab so it cannot be missed. */
export function ActivationBanner() {
  const qc = useQueryClient();
  const waiting = useCellQuery(KEY, (api) => api.billing.activations(), { refetchInterval: 15_000 });
  const { session } = useRuntime();
  const ready = session?.authenticated === true;
  const returned = useRef(false);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: KEY });
    void qc.invalidateQueries({ queryKey: BILLING_KEY });
    void qc.invalidateQueries({ queryKey: ["tenant"] });
  };
  const pay = useCellMutation((api, id: string) => api.billing.payActivation(id), { onSuccess: (r) => window.location.assign(r.url) });
  const confirm = useCellMutation((api, v: { id: string; session?: string }) => api.billing.confirmActivation(v.id, v.session), { onSuccess: refresh });

  // Stripe returns to /admin/billing?activation=paid&order=...&session_id=cs_...: ask the cell to check once, then tidy the address.
  useEffect(() => {
    if (returned.current || !ready) return;
    const url = new URL(window.location.href);
    const order = url.searchParams.get("order");
    const sessionId = url.searchParams.get("session_id");
    if (url.searchParams.get("activation") === "paid" && order && sessionId) {
      returned.current = true;
      confirm.mutate(
        { id: order, session: sessionId },
        {
          onSettled: () => {
            for (const k of ["activation", "order", "session_id"]) url.searchParams.delete(k);
            window.history.replaceState(null, "", url.pathname + (url.search || ""));
          },
        },
      );
    }
  }, [ready, confirm]);

  const cancelled = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("activation") === "cancelled";
  const items = waiting.data?.items ?? [];

  return (
    <div className="space-y-3">
      {confirm.isPending ? (
        <Banner tone="info" title="Payment verification in progress">
          <span role="status">We are confirming your payment with Stripe. Your plan changes only once that is done. Please keep this page open.</span>
        </Banner>
      ) : null}
      {confirm.data?.activated ? (
        <Banner tone="ok" title={confirm.data.plan ? `Your ${confirm.data.plan.toLowerCase()} plan is now on` : "Your plan is now on"}>
          Payment confirmed. Your new plan and its allowances are in effect.
        </Banner>
      ) : null}
      {confirm.data && !confirm.data.activated ? (
        <Banner tone="warn" title="Not confirmed yet">
          {confirm.data.reason ?? "Stripe has not confirmed the payment yet."} Nothing has changed on your plan. If you have paid, check again in a moment.
        </Banner>
      ) : null}
      {cancelled ? <Banner tone="info" title="No payment was made">You left the payment page before finishing. Your plan has not changed and you can pay when you are ready.</Banner> : null}
      {confirm.isError ? <ApiErrorView error={confirm.error} title="Couldn't confirm the payment" onRetry={() => confirm.reset()} /> : null}
      {pay.isError ? <ApiErrorView error={pay.error} title="Couldn't open the payment page" onRetry={() => pay.reset()} /> : null}
      {items.map((w) => (
        <WaitCard key={w.order_id} wait={w} onPay={() => pay.mutate(w.order_id)} paying={pay.isPending} onCheck={() => confirm.mutate({ id: w.order_id })} checking={confirm.isPending} />
      ))}
    </div>
  );
}

function WaitCard({ wait, onPay, paying, onCheck, checking }: { wait: ActivationWait; onPay: () => void; paying: boolean; onCheck: () => void; checking: boolean }) {
  const plan = (wait.plan ?? "new").toLowerCase();
  return (
    <Card title={`Pay to switch on your ${plan} plan`} description="Approved by Zoiko. Your plan stays as it is until this is paid.">
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-48 flex-1">
          <p className="text-2xl font-semibold tabular-nums text-ink">{money(wait.amount, wait.currency)}</p>
          <p className="text-xs text-ink-muted">First month's platform fee. It is taken off your first monthly invoice, so you are not billed for it twice.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon={<CreditCard aria-hidden className="size-4" />} pending={paying} onPress={onPay}>
            Pay {money(wait.amount, wait.currency)} on Stripe
          </Button>
          <Button variant="secondary" pending={checking} onPress={onCheck}>
            I have paid: check now
          </Button>
        </div>
      </div>
    </Card>
  );
}
