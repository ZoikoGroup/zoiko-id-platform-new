"use client";

import type { PricingPeriod, PublicAvailability, PublicEstimate, PublicPricingPage } from "@zoiko/api-client";
import { Banner, Button, Select, TextField } from "@zoiko/ui";
import { useState, type FormEvent } from "react";

import { estimateBody, type Context } from "./logic";

/** The family estimate (WF-P04). It asks for a payer country and a volume per service, shows the working for every line, and never adds amounts across currencies. A service with no approved
 * rate, or not released in the market, says Quote required and why. Whether monthly or annual is offered is the server's answer: the choice appears only when every priced service has both. */
export function Estimator({ page, ctx, availability }: { page: PublicPricingPage; ctx: Context; availability: PublicAvailability | undefined }) {
  const [volumes, setVolumes] = useState<Record<string, string>>({});
  const [period, setPeriod] = useState<PricingPeriod>("MONTHLY");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PublicEstimate | null>(null);

  const priced = (availability?.cards ?? []).filter((c) => Object.keys(c.rates).length > 0);
  const both = availability?.rate_card?.both_periods === true;
  const effective: PricingPeriod = both ? period : "MONTHLY";

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body = estimateBody(ctx, effective, volumes);
    if (!body) {
      setError("Name the country of the paying company, then enter a whole number of expected units per month for at least one service.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/pricing/family-estimate", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const payload = (await res.json()) as PublicEstimate & { error?: { message?: string } };
      if (!res.ok) throw new Error(payload.error?.message ?? "The estimate could not be made.");
      setResult(payload);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : "The estimate could not be made.");
    } finally {
      setBusy(false);
    }
  }

  if (!availability) return <p className="text-sm text-ink-muted">Choose your market above and check availability to estimate a monthly cost.</p>;
  if (!availability.rate_card) return <p className="text-sm text-ink-muted">No approved rate card is effective yet, so no service can be estimated. Sales will quote each one.</p>;
  if (priced.length === 0) return <p className="text-sm text-ink-muted">No service in your market has an approved rate yet. Sales will quote them.</p>;

  return (
    <div className="space-y-4">
      <form onSubmit={submit} noValidate className="space-y-4 rounded-control border border-border p-4">
        {both ? (
          <Select
            label="Billing period"
            description="Annual is priced on twelve times your monthly volume."
            options={[
              { id: "MONTHLY", label: "Monthly" },
              { id: "ANNUAL", label: "Annual" },
            ]}
            value={period}
            onChange={(v) => {
              setPeriod(v as PricingPeriod);
              setResult(null);
            }}
          />
        ) : (
          <p className="text-sm text-ink-muted">Billed monthly. An annual rate is offered only where one is approved for every priced service.</p>
        )}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {priced.map((c) => (
            <TextField
              key={c.family}
              label={`${c.name}: expected units per month`}
              description={`Unit: ${c.unit}`}
              inputMode="numeric"
              value={volumes[c.family] ?? ""}
              onChange={(v) => {
                setVolumes((s) => ({ ...s, [c.family]: v }));
                setResult(null);
              }}
            />
          ))}
        </div>
        {error ? <Banner tone="danger">{error}</Banner> : null}
        <Button type="submit" isDisabled={busy} pending={busy}>
          Estimate
        </Button>
      </form>
      <div aria-live="polite">{result ? <EstimateResult result={result} names={Object.fromEntries(page.families.map((f) => [f.family, f.name]))} /> : null}</div>
    </div>
  );
}

function EstimateResult({ result, names }: { result: PublicEstimate; names: Record<string, string> }) {
  const totals = Object.entries(result.totals);
  return (
    <div className="space-y-3 rounded-control border border-border p-4 text-sm">
      <p className="font-semibold">Estimate, not a quote. Nothing is charged.</p>
      {result.lines.length === 0 ? <p>No selected service could be priced. See below.</p> : null}
      <ul className="space-y-2">
        {result.lines.map((l) => (
          <li key={l.family} className="border-b border-border pb-2 last:border-0">
            <p className="font-medium">
              {names[l.family] ?? l.name}: {l.currency} {l.amount} {l.period === "ANNUAL" ? "a year" : "a month"}
            </p>
            <p className="text-xs text-ink-muted">{l.formula}</p>
            {l.annual_comparison ? (
              <p className="text-xs">
                Twelve months at the monthly rate: {l.annual_comparison.currency} {l.annual_comparison.twelve_months}. Annual: {l.annual_comparison.annual}. You save {l.annual_comparison.currency} {l.annual_comparison.saving}.
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      {totals.length ? (
        <p className="font-medium">
          Total, by currency:{" "}
          {totals.map(([c, v]) => `${c} ${v}`).join(" and ")}. Tax is not included.
        </p>
      ) : null}
      {result.quote_required.length ? (
        <div>
          <p className="font-medium">Quote required</p>
          <ul className="list-disc pl-5 text-ink-muted">
            {result.quote_required.map((q) => (
              <li key={q.family}>
                {names[q.family] ?? q.name}: {q.reasons[0]?.text ?? "Arranged with Sales."}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <ul className="list-disc pl-5 text-xs text-ink-muted">
        {result.assumptions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
      <p className="text-xs text-ink-muted">
        Rate card version {result.rate_card?.version ?? "none"}. Estimate reference <span className="font-mono">{result.quote_hash.slice(0, 16)}</span>. Offered until {new Date(result.valid_until).toUTCString()}.
      </p>
    </div>
  );
}
