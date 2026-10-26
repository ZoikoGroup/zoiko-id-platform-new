import type { Catalog, CatalogPlan, CatalogPriceLine } from "@zoiko/api-client";
import type { Metadata } from "next";

import { count, money } from "@/features/admin/billing/shared";
import { Calculator } from "@/features/pricing/calculator";
import { ADD_ONS, DISCLAIMER, ENTERPRISE_NOTE, GLOSSARY, HERO, PLAN_ORDER, SKU_LABEL, SUBHERO } from "@/features/pricing/copy";
import { server } from "@/server/bff";

export const metadata: Metadata = { title: "Plans & pricing" };
export const dynamic = "force-dynamic";

async function loadCatalog(): Promise<Catalog | null> {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/catalog`, { cache: "no-store", headers: { accept: "application/json" } });
    return res.ok ? ((await res.json()) as Catalog) : null;
  } catch {
    return null;
  }
}

const order = (p: CatalogPlan) => PLAN_ORDER.indexOf(p.plan as (typeof PLAN_ORDER)[number]);

function linesOf(catalog: Catalog, plan: string): CatalogPriceLine[] {
  return (catalog.pricing?.lines ?? []).filter((l) => l.plan === plan);
}

function fee(catalog: Catalog, plan: CatalogPlan): string {
  const base = linesOf(catalog, plan.plan).find((l) => l.sku === "ZID-BASE");
  if (!base) return "Contact sales";
  const text = money(base.unit_rate, catalog.pricing?.currency);
  return plan.platform_fee_is_floor ? `From ${text}` : text;
}

const included = (n: number | null) => (n === null ? "Negotiated" : count(n));

export default async function Page() {
  const catalog = await loadCatalog();
  const plans = (catalog?.plans ?? []).slice().sort((a, b) => order(a) - order(b));
  const priced = catalog?.status === "PRICED";

  return (
    <div className="space-y-10">
      <section className="space-y-3">
        <h1 className="text-3xl font-bold tracking-tight text-ink">{HERO}</h1>
        <p className="max-w-2xl text-lg text-ink-muted">{SUBHERO}</p>
      </section>

      {!catalog ? (
        <p role="status" className="rounded-control border border-border p-4 text-sm">
          Plans could not be loaded right now. Please try again shortly or contact sales.
        </p>
      ) : (
        <>
          {!priced ? (
            <p role="status" className="rounded-control border border-border p-4 text-sm">
              Prices have not been published yet. The plans below show what each one includes.
            </p>
          ) : null}

          <section aria-labelledby="plans-h" className="space-y-3">
            <h2 id="plans-h" className="text-xl font-semibold">
              Plans
            </h2>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-3xl text-sm">
                <caption className="sr-only">Plans and what each includes per month</caption>
                <thead>
                  <tr className="border-b border-border-strong text-left">
                    <th scope="col" className="py-2 pr-3">
                      <span className="sr-only">Feature</span>
                    </th>
                    {plans.map((p) => (
                      <th key={p.plan} scope="col" className="px-3 py-2 font-semibold">
                        {p.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <Row label="Platform fee per month" cells={plans.map((p) => (priced ? fee(catalog, p) : "—"))} />
                  <Row label="People included" cells={plans.map((p) => included(p.limits.mah_included))} />
                  <Row label="Sign-ins included" cells={plans.map((p) => included(p.limits.auth_events_included))} />
                  <Row label="Access decisions included" cells={plans.map((p) => included(p.limits.policy_decisions_included))} />
                  <Row label="Machine grants included" cells={plans.map((p) => included(p.limits.m2m_grants_included))} />
                  <Row label="Enterprise SSO connections included" cells={plans.map((p) => included(p.limits.sso_included))} />
                  <Row label="Production environments" cells={plans.map((p) => included(p.limits.production_environments))} />
                  <Row label="Support" cells={plans.map((p) => p.limits.support_level)} />
                </tbody>
              </table>
            </div>
            <div className="space-y-2 md:hidden">
              {plans.map((p) => (
                <details key={p.plan} className="rounded-control border border-border p-3">
                  <summary className="cursor-pointer font-semibold">
                    {p.label} <span className="font-normal text-ink-muted">{priced ? fee(catalog, p) : ""}</span>
                  </summary>
                  <dl className="mt-3 space-y-1 text-sm">
                    <Item k="People included" v={included(p.limits.mah_included)} />
                    <Item k="Sign-ins included" v={included(p.limits.auth_events_included)} />
                    <Item k="Access decisions included" v={included(p.limits.policy_decisions_included)} />
                    <Item k="Machine grants included" v={included(p.limits.m2m_grants_included)} />
                    <Item k="Enterprise SSO connections" v={included(p.limits.sso_included)} />
                    <Item k="Production environments" v={included(p.limits.production_environments)} />
                    <Item k="Support" v={p.limits.support_level} />
                  </dl>
                </details>
              ))}
            </div>
            <p className="text-xs text-ink-muted">{ENTERPRISE_NOTE}</p>
          </section>

          {priced ? (
            <section aria-labelledby="meters-h" className="space-y-3">
              <h2 id="meters-h" className="text-xl font-semibold">
                What you pay above what a plan includes
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full min-w-xl text-sm">
                  <caption className="sr-only">Rates above the included amounts, by plan</caption>
                  <thead>
                    <tr className="border-b border-border-strong text-left">
                      <th scope="col" className="py-2 pr-3">
                        Meter
                      </th>
                      {plans.filter((p) => linesOf(catalog, p.plan).length > 1).map((p) => (
                        <th key={p.plan} scope="col" className="px-3 py-2 font-semibold">
                          {p.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.keys(SKU_LABEL)
                      .filter((sku) => sku !== "ZID-BASE")
                      .map((sku) => (
                        <Row
                          key={sku}
                          label={SKU_LABEL[sku]!}
                          cells={plans
                            .filter((p) => linesOf(catalog, p.plan).length > 1)
                            .map((p) => {
                              const l = linesOf(catalog, p.plan).find((x) => x.sku === sku);
                              if (!l) return "—";
                              const per = l.unit_divisor > 1 ? ` per ${count(l.unit_divisor)}` : "";
                              return `${money(l.unit_rate, catalog.pricing?.currency)}${per}`;
                            })}
                        />
                      ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}

          <section aria-labelledby="glossary-h" className="space-y-3">
            <h2 id="glossary-h" className="text-xl font-semibold">
              What we count
            </h2>
            <dl className="grid gap-4 md:grid-cols-2">
              {GLOSSARY.map((g) => (
                <div key={g.term}>
                  <dt className="font-medium">{g.term}</dt>
                  <dd className="text-sm text-ink-muted">{g.meaning}</dd>
                </div>
              ))}
            </dl>
            <p className="text-sm text-ink-muted">{ADD_ONS}</p>
          </section>

          {priced && plans.length > 0 ? <Calculator plans={plans} /> : null}
        </>
      )}

      <p className="text-xs text-ink-muted">{DISCLAIMER}</p>
    </div>
  );
}

function Row({ label, cells }: { label: string; cells: string[] }) {
  return (
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="py-2 pr-3 text-left font-medium">
        {label}
      </th>
      {cells.map((c, i) => (
        <td key={i} className="tabular px-3 py-2">
          {c}
        </td>
      ))}
    </tr>
  );
}

function Item({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-ink-muted">{k}</dt>
      <dd className="tabular">{v}</dd>
    </div>
  );
}
