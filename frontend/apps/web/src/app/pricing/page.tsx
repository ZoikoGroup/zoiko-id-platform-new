import type { Catalog, CatalogPlan, CatalogPriceLine, PublicContext, PublicPricingPage } from "@zoiko/api-client";
import { ButtonLink } from "@zoiko/ui";
import type { Metadata } from "next";

import { count, money } from "@/features/admin/billing/shared";
import { Calculator } from "@/features/pricing/calculator";
import { PeopleCalculator, type PeoplePlan } from "@/features/pricing/people-calculator";
import { ADD_ONS, DISCLAIMER, ENTERPRISE_NOTE, FAQ, GLOSSARY, HERO, PLAN_ORDER, PLATFORM_NOTE, SKU_LABEL, SUBHERO } from "@/features/pricing/copy";
import { PricingExplorer } from "@/features/pricing/explorer";
import { contextFromLink } from "@/features/pricing/logic";
import { server } from "@/server/bff";

export const metadata: Metadata = {
  title: "Plans & pricing",
  description: "Verify identity, validate credentials and prove authority, market by market, with prices shown only where an approved rate card is effective.",
  alternates: { canonical: "/pricing" },
};
export const dynamic = "force-dynamic";

async function load<T>(path: string): Promise<T | null> {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}${path}`, { cache: "no-store", headers: { accept: "application/json" } });
    return res.ok ? ((await res.json()) as T) : null;
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

export default async function Page({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  // A shared market link (?ctx=): the cell verifies the signature and expiry. A bad or expired link only means the market is chosen again; it never blocks the page.
  const raw = (await searchParams)?.ctx;
  const token = typeof raw === "string" && /^[A-Za-z0-9._-]{10,600}$/.test(raw) ? raw : null;
  const linked = token ? await load<PublicContext>(`/v1/public/pricing/context-link/${token}`) : null;
  const initialContext = linked ? contextFromLink(linked) : undefined;
  const linkProblem = token !== null && linked === null;
  const [structure, catalog, people] = await Promise.all([load<PublicPricingPage>("/v1/public/pricing/page"), load<Catalog>("/v1/catalog"), load<{ plans: PeoplePlan[]; currency: string; status: string }>("/v1/verify-api/pricing/people")]);
  const plans = (catalog?.plans ?? []).slice().sort((a, b) => order(a) - order(b));
  const priced = catalog?.status === "PRICED";

  return (
    <div className="space-y-14">
      <section className="space-y-4" aria-labelledby="hero-h">
        <h1 id="hero-h" className="text-3xl font-bold tracking-tight text-ink sm:text-4xl">
          {HERO}
        </h1>
        <p className="max-w-3xl text-lg text-ink-muted">{SUBHERO}</p>
        <div className="flex flex-wrap gap-3">
          <ButtonLink variant="primary" href="#plans">
            See plans
          </ButtonLink>
          <ButtonLink variant="secondary" href="#solution">
            Build a solution
          </ButtonLink>
        </div>
      </section>

      {people ? <PeopleCalculator plans={people.plans} currency={people.currency} status={people.status} /> : null}

      {structure ? (
        <>
          <PricingExplorer page={structure} initialContext={initialContext} linkProblem={linkProblem} />
          <section aria-labelledby="disclosure-h" className="space-y-2 rounded-control border border-border p-4 text-sm">
            <h2 id="disclosure-h" className="text-base font-semibold">
              How pricing works here
            </h2>
            <ul className="list-disc space-y-1 pl-5 text-ink-muted">
              <li>{structure.disclosures.price_basis}</li>
              <li>{structure.disclosures.tax}</li>
              <li>{structure.disclosures.commitment}</li>
              <li>{structure.disclosures.no_surprise}</li>
              <li>{structure.disclosures.plan_change}</li>
              <li>{structure.disclosures.exit}</li>
            </ul>
          </section>
        </>
      ) : (
        <p role="status" className="rounded-control border border-border p-4 text-sm">
          The plan comparison could not be loaded right now. Please try again shortly or contact sales.
        </p>
      )}

      <section aria-labelledby="platform-h" className="space-y-6">
        <div className="space-y-2">
          <h2 id="platform-h" className="text-2xl font-semibold">
            Platform plans and usage
          </h2>
          <p className="max-w-3xl text-sm text-ink-muted">{PLATFORM_NOTE}</p>
        </div>

        {!catalog ? (
          <p role="status" className="rounded-control border border-border p-4 text-sm">
            Platform plans could not be loaded right now. Please try again shortly or contact sales.
          </p>
        ) : (
          <>
            {!priced ? (
              <p role="status" className="rounded-control border border-border p-4 text-sm">
                Prices have not been published yet. The plans below show what each one includes.
              </p>
            ) : null}

            <div className="space-y-3">
              <h3 id="plans-h" className="text-lg font-semibold">
                Platform plans
              </h3>
              <div className="hidden overflow-x-auto md:block" role="region" aria-labelledby="plans-h" tabIndex={0}>
                <table className="w-full min-w-3xl text-sm">
                  <caption className="sr-only">Platform plans and what each includes per month</caption>
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
            </div>

            {priced ? (
              <div className="space-y-3">
                <h3 id="meters-h" className="text-lg font-semibold">
                  What you pay above what a plan includes
                </h3>
                <div className="overflow-x-auto" role="region" aria-labelledby="meters-h" tabIndex={0}>
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
              </div>
            ) : null}

            <div className="space-y-3">
              <h3 id="glossary-h" className="text-lg font-semibold">
                What we count
              </h3>
              <dl className="grid gap-4 md:grid-cols-2">
                {GLOSSARY.map((g) => (
                  <div key={g.term}>
                    <dt className="font-medium">{g.term}</dt>
                    <dd className="text-sm text-ink-muted">{g.meaning}</dd>
                  </div>
                ))}
              </dl>
              <p className="text-sm text-ink-muted">{ADD_ONS}</p>
            </div>

            {priced && plans.length > 0 ? <Calculator plans={plans} /> : null}
          </>
        )}
      </section>

      <section aria-labelledby="faq-h" className="space-y-3">
        <h2 id="faq-h" className="text-2xl font-semibold">
          Questions
        </h2>
        <div className="space-y-2">
          {FAQ.map((f) => (
            <details key={f.q} className="rounded-control border border-border p-3">
              <summary className="cursor-pointer font-medium">{f.q}</summary>
              <p className="mt-2 text-sm text-ink-muted">{f.a}</p>
            </details>
          ))}
        </div>
      </section>

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
