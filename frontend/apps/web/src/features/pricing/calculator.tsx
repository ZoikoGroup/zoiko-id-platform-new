"use client";

import type { BillEstimate, CatalogPlan } from "@zoiko/api-client";
import { Banner, Button, Card, Select, TextField } from "@zoiko/ui";
import { useState, type FormEvent } from "react";

import { meterLabel, money } from "@/features/admin/billing/shared";

import { DISCLAIMER } from "./copy";

type Field = { key: "mah" | "auth_events" | "policy_decisions" | "m2m_grants" | "sso_connections" | "scim_connections"; label: string };

const FIELDS: Field[] = [
  { key: "mah", label: "People who sign in each month" },
  { key: "auth_events", label: "Sign-ins per month" },
  { key: "policy_decisions", label: "Access decisions per month" },
  { key: "m2m_grants", label: "Machine grants per month" },
  { key: "sso_connections", label: "Enterprise SSO connections" },
  { key: "scim_connections", label: "Directory (SCIM) connections" },
];

/** Keyboard-operable estimator. It posts to this app's own route, which asks the cell to run the same rating
 * function an invoice uses. Nothing here prices anything locally. */
export function Calculator({ plans }: { plans: CatalogPlan[] }) {
  const [plan, setPlan] = useState(plans.find((p) => p.plan === "BUSINESS")?.plan ?? plans[0]?.plan ?? "");
  const [values, setValues] = useState<Record<Field["key"], string>>({
    mah: "0",
    auth_events: "0",
    policy_decisions: "0",
    m2m_grants: "0",
    sso_connections: "0",
    scim_connections: "0",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BillEstimate | null>(null);

  const invalid = FIELDS.find((f) => !/^\d{1,12}$/.test(values[f.key].trim()));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (invalid || !plan) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/pricing/estimate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ plan, ...Object.fromEntries(FIELDS.map((f) => [f.key, Number(values[f.key])])) }),
      });
      const payload = (await res.json()) as BillEstimate & { error?: { message?: string } };
      if (!res.ok) throw new Error(payload.error?.message ?? "The estimate could not be produced.");
      setResult(payload);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : "The estimate could not be produced.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Estimate your monthly cost" description="Enter your expected monthly volumes. This is an estimate, not a quotation.">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-describedby="calc-note">
        <Select label="Plan" options={plans.map((p) => ({ id: p.plan, label: p.label }))} value={plan} onChange={setPlan} />
        {FIELDS.map((f) => (
          <TextField
            key={f.key}
            label={f.label}
            value={values[f.key]}
            onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
            inputMode="numeric"
            errorMessage={!/^\d{1,12}$/.test(values[f.key].trim()) ? "Enter a whole number, 0 or more." : undefined}
          />
        ))}
        <div className="flex items-end">
          <Button type="submit" isDisabled={busy || Boolean(invalid)}>
            {busy ? "Estimating…" : "Estimate"}
          </Button>
        </div>
      </form>
      <p id="calc-note" className="mt-3 text-xs text-ink-muted">
        {DISCLAIMER}
      </p>
      <div aria-live="polite" className="mt-4">
        {error ? <Banner tone="danger">{error}</Banner> : null}
        {result?.status === "UNPRICED" ? <Banner tone="info">Prices have not been published yet, so there is nothing to estimate.</Banner> : null}
        {result?.status === "ESTIMATE" ? <Result r={result} /> : null}
      </div>
    </Card>
  );
}

function Result({ r }: { r: BillEstimate }) {
  return (
    <div className="space-y-3">
      <p className="text-lg font-semibold">
        {r.fee_is_floor ? "From " : ""}
        {money(r.monthly_estimate, r.currency)} <span className="text-sm font-normal text-ink-muted">per month, before tax and third-party checks</span>
      </p>
      <table className="w-full text-sm">
        <caption className="sr-only">How the estimate is made up</caption>
        <tbody>
          {(r.lines ?? []).map((ln, i) => (
            <tr key={i} className="border-b border-border last:border-0">
              <th scope="row" className="py-2 pr-3 text-left font-medium">
                {ln.kind === "FEE" ? "Platform fee" : meterLabel(ln.meter)}
              </th>
              <td className="py-2 pr-3 font-mono text-xs text-ink-muted">{ln.formula}</td>
              <td className="tabular py-2 text-right">{money(ln.net, r.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.warnings?.length ? <Banner tone="info">{r.warnings.join(" ")}</Banner> : null}
      <ul className="list-disc space-y-1 pl-5 text-xs text-ink-muted">
        {r.assumptions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
      <p className="text-xs text-ink-subtle">Price book version {r.price_book_version}.</p>
    </div>
  );
}
