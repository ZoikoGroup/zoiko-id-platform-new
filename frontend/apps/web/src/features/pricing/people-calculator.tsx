"use client";

import { useEffect, useState } from "react";

export type PeoplePlan = { code: string; name: string; custom: boolean; rate_per_verification: string | null; monthly_minimum: string | null };
export type PeopleEstimate = { plan: string; currency: string; verifications: number; rate_per_verification: string; usage: string; monthly_minimum: string; total: string; minimum_applied: boolean; table_version: string; table_status: string; note: string };
type Get = (query: string) => Promise<{ ok: boolean; data?: { estimate?: PeopleEstimate } }>;

const defaultGet: Get = async (q) => {
  const res = await fetch(`/api/pricing/people?${q}`);
  return { ok: res.ok, data: res.ok ? await res.json() : undefined };
};

const usd = (v: string, currency: string) => new Intl.NumberFormat("en-US", { style: "currency", currency }).format(Number(v));

/** Verify People estimate (the pricing page, version 5.0). The cell does the arithmetic; this only shows it, with the table's status. A plan priced by agreement offers no number. */
export function PeopleCalculator({ plans, currency, status, get = defaultGet }: { plans: PeoplePlan[]; currency: string; status: string; get?: Get }) {
  const first = plans.find((p) => !p.custom)?.code ?? plans[0]?.code ?? "";
  const [plan, setPlan] = useState(first);
  const [volume, setVolume] = useState("600");
  const [got, setGot] = useState<{ key: string; est: PeopleEstimate | null } | null>(null);
  const chosen = plans.find((p) => p.code === plan);
  const n = Number(volume);
  const askable = chosen !== undefined && !chosen.custom && Number.isInteger(n) && n >= 0 && n <= 1_000_000_000;
  const key = `${plan}:${n}`;

  useEffect(() => {
    if (!askable) return;
    let live = true;
    void get(`plan=${encodeURIComponent(plan)}&verifications=${n}`).then((r) => {
      if (live) setGot({ key, est: r.ok && r.data?.estimate ? r.data.estimate : null });
    });
    return () => {
      live = false;
    };
  }, [plan, n, key, askable, get]);

  const est = askable && got?.key === key ? got.est : null;
  const problem = chosen?.custom ? `${chosen.name} is priced by agreement. Talk to sales for a quote.` : !askable ? "Enter a whole number of verifications." : got?.key === key && got.est === null ? "The estimate is not available right now." : null;

  return (
    <section aria-labelledby="people-calc" className="space-y-3 rounded-lg border border-line p-4">
      <h2 id="people-calc" className="text-lg font-semibold text-ink">Verify People: estimate your month</h2>
      <p className="text-sm text-ink-muted">Prices per completed verification. Each plan has a monthly minimum: you pay the larger of the minimum and your usage.</p>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="pp-plan" className="block text-sm font-medium text-ink">Plan</label>
          <select id="pp-plan" value={plan} onChange={(e) => setPlan(e.target.value)} className="mt-1 rounded-md border border-line bg-surface p-2 text-ink">
            {plans.map((p) => <option key={p.code} value={p.code}>{p.name}{p.custom ? " (custom pricing)" : ` (${usd(p.rate_per_verification ?? "0", currency)} each, ${usd(p.monthly_minimum ?? "0", currency)} minimum)`}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="pp-vol" className="block text-sm font-medium text-ink">Verifications per month</label>
          <input id="pp-vol" inputMode="numeric" value={volume} onChange={(e) => setVolume(e.target.value.replace(/[^\d]/g, ""))} className="mt-1 w-32 rounded-md border border-line bg-surface p-2 text-ink" />
        </div>
      </div>
      <div role="status" aria-live="polite" className="text-sm">
        {est ? (
          <p className="text-ink">
            Estimated monthly total <strong>{usd(est.total, est.currency)}</strong>
            <span className="text-ink-muted"> ({est.verifications.toLocaleString("en-US")} x {usd(est.rate_per_verification, est.currency)}{est.minimum_applied ? `, the ${usd(est.monthly_minimum, est.currency)} minimum applies` : ""})</span>
          </p>
        ) : (
          <p className="text-ink-muted">{problem}</p>
        )}
      </div>
      <p className="text-xs text-ink-muted">{status === "PROPOSED" ? "These are the current list prices and may change. " : ""}An indicative estimate: excludes tax and optional services such as human review and AML screening, and is not an invoice.</p>
    </section>
  );
}
