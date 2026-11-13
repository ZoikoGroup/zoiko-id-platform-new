"use client";

import type { PublicAvailability, PublicPricingPage, PublicState } from "@zoiko/api-client";
import { Badge, Banner, Button, ButtonLink, Card, Checkbox, Select } from "@zoiko/ui";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { Estimator } from "./estimator";
import { LeadForm } from "./lead-form";
import {
  CELL_HELP,
  CELL_LABEL,
  EMPTY_CONTEXT,
  GROUPS,
  MARKET_HELP,
  MARKET_LABEL,
  availabilityQuery,
  contextLinkBody,
  cellFor,
  countryOptions,
  marketNamed,
  missingFor,
  rateText,
  recommendedTier,
  scopeFromUseCases,
  stateByFamily,
  withPrerequisites,
  type CellState,
  type Context,
} from "./logic";

const COUNTRIES = countryOptions();
const CELL_TONE: Record<CellState, "ok" | "info" | "neutral" | "warn"> = { INCLUDED: "ok", OPTIONAL: "info", QUOTE_ONLY: "neutral", NOT_IN_MARKET: "warn" };
const STATE_TONE: Record<PublicState, "ok" | "info" | "neutral" | "warn" | "danger"> = { AVAILABLE: "ok", SALES_ASSISTED: "info", RESTRICTED: "danger", NOT_AVAILABLE: "warn", MARKET_REQUIRED: "neutral" };

/** The interactive part of the pricing page (ZID-UX-PRICE-004 WF-P01 to P07): the market, the three plans, the use cases, the sixteen-service comparison, the coverage answer and the
 * inquiry. The server decides every answer; nothing here prices anything or infers a market from the browser. */
export function PricingExplorer({ page, initialContext, linkProblem = false }: { page: PublicPricingPage; initialContext?: Context | undefined; linkProblem?: boolean }) {
  const [ctx, setCtx] = useState<Context>(initialContext ?? EMPTY_CONTEXT);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [shareError, setShareError] = useState<string | null>(null);
  const [availability, setAvailability] = useState<PublicAvailability | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [useCases, setUseCases] = useState<string[]>([]);
  const [extra, setExtra] = useState<string[]>([]);
  const [kind, setKind] = useState<"ENTERPRISE" | "PARTNER">("ENTERPRISE");

  const names = useMemo(() => Object.fromEntries(page.families.map((f) => [f.family, f.name])), [page]);
  const { families: scopeFamilies } = useMemo(() => scopeFromUseCases(page, useCases), [page, useCases]);
  const scope = useMemo(() => [...new Set([...scopeFamilies, ...extra])].sort(), [scopeFamilies, extra]);
  const missing = useMemo(() => missingFor(page, scope), [page, scope]);
  const recommended = recommendedTier(page, scope);
  const state = stateByFamily(availability);

  async function check(next: Context) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/pricing/availability?${availabilityQuery(next)}`, { cache: "no-store" });
      const payload = (await res.json()) as PublicAvailability & { error?: { message?: string } };
      if (!res.ok) throw new Error(payload.error?.message ?? "Availability could not be checked.");
      setAvailability(payload);
    } catch (err) {
      setAvailability(undefined);
      setError(err instanceof Error ? err.message : "Availability could not be checked.");
    } finally {
      setBusy(false);
    }
  }

  // A shared link only pre-fills the market; the server judges it afresh, so the answer is fetched as if the buyer had pressed Check.
  const started = useRef(false);
  useEffect(() => {
    if (started.current || !initialContext || !marketNamed(initialContext)) return;
    started.current = true;
    void check(initialContext);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function share() {
    setShareError(null);
    try {
      const res = await fetch("/api/pricing/context-link", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(contextLinkBody(ctx)) });
      const payload = (await res.json()) as { token?: string; error?: { message?: string } };
      if (!res.ok || !payload.token) throw new Error(payload.error?.message ?? "A link could not be made.");
      setShareUrl(`${window.location.origin}/pricing?ctx=${payload.token}#market`);
    } catch (err) {
      setShareUrl(null);
      setShareError(err instanceof Error ? err.message : "A link could not be made.");
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void check(ctx);
  };
  // Changing the market invalidates whatever was shown for the old one (P03): the old answer is dropped at once, never kept beside the new choice.
  const change = (patch: Partial<Context>) => {
    setCtx((c) => ({ ...c, ...patch }));
    setAvailability(undefined);
    setShareUrl(null);
  };

  return (
    <div className="space-y-12">
      <section id="market" aria-labelledby="market-h" className="space-y-3">
        <h2 id="market-h" className="text-xl font-semibold">
          Choose your market
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">
          Each service is judged for the country of the person being checked, the country of the paying company, and the data region, separately. Nothing is inferred from your device or location.
        </p>
        <form onSubmit={submit} className="grid gap-4 rounded-control border border-border p-4 sm:grid-cols-2 lg:grid-cols-4" aria-describedby="market-note">
          <Select label="Country of the people checked" options={COUNTRIES} value={ctx.subject || null} onChange={(v) => change({ subject: v })} placeholder="Choose a country" />
          <Select label="Country of the paying company" options={COUNTRIES} value={ctx.payer || null} onChange={(v) => change({ payer: v })} placeholder="Choose a country" />
          <Select label="Country that issued the documents" description="Optional. Used to check which services cover those documents." options={COUNTRIES} value={ctx.issuer || null} onChange={(v) => change({ issuer: v })} placeholder="Not sure" />
          <Select
            label="Data region"
            description={availability ? "Only regions with a released service." : "Check availability first to see the regions."}
            options={(availability?.released_regions ?? []).map((r) => ({ id: r, label: r }))}
            value={ctx.region || null}
            onChange={(v) => {
              const next = { ...ctx, region: v };
              setCtx(next);
              void check(next);
            }}
            isDisabled={!availability || availability.released_regions.length === 0}
            placeholder={availability && availability.released_regions.length === 0 ? "None released yet" : "Choose a region"}
          />
          <div className="flex flex-wrap gap-3 sm:col-span-2 lg:col-span-4">
            <Button type="submit" isDisabled={busy || !marketNamed(ctx)} pending={busy}>
              Check availability
            </Button>
            <Button type="button" variant="secondary" isDisabled={!marketNamed(ctx)} onPress={() => void share()}>
              Copy link to this market
            </Button>
          </div>
        </form>
        <p id="market-note" className="text-xs text-ink-muted">
          Prices, where an approved rate card is effective for your market, are shown with their currency, charge unit and effective date. Where there is none, the page says Quote required and shows no amount.
        </p>
        <div aria-live="polite" className="text-sm">
          {error ? <Banner tone="danger">{error}</Banner> : null}
          {linkProblem ? <Banner tone="warn">That link is not valid or has expired. Choose your market again.</Banner> : null}
          {shareError ? <Banner tone="danger">{shareError}</Banner> : null}
          {shareUrl ? (
            <p role="status" className="break-all text-ink-muted">
              Link to this market, valid for a limited time. It holds only the countries and region, no price or personal detail: <span className="font-mono">{shareUrl}</span>
            </p>
          ) : null}
          {availability ? (
            <p className="text-ink-muted">
              Availability checked against market release version {availability.market_matrix_version ?? "none yet"}.{" "}
              {availability.market_matrix_version === null ? "No market has been released yet, so every service is arranged with Sales." : ""}
            </p>
          ) : null}
        </div>
      </section>

      <section id="plans" aria-labelledby="plans-h" className="space-y-3">
        <h2 id="plans-h" className="text-xl font-semibold">
          Plans
        </h2>
        {page.packaging_status !== "APPROVED" ? (
          <p role="status" className="rounded-control border border-border p-3 text-sm">
            Plan contents are being finalised. Confirm the scope you need with Sales before relying on a comparison below.
          </p>
        ) : null}
        <ul className="grid gap-4 md:grid-cols-3">
          {page.tiers.map((t) => {
            const included = page.families.filter((f) => t.family_state[f.family] === "included");
            const optional = page.families.filter((f) => t.family_state[f.family] === "optional");
            return (
              <li key={t.code}>
                <Card title={t.label} description={t.fit}>
                  <div className="space-y-3 text-sm">
                    {recommended === t.code ? (
                      <Badge tone="brand" size="xs">
                        Recommended for {useCases.map((c) => page.use_cases.find((u) => u.code === c)?.label).filter(Boolean).join(", ")}
                      </Badge>
                    ) : null}
                    <p className="font-medium">Price: Quote required</p>
                    <p className="text-xs text-ink-muted">Rates are per service. Choose your market to see the approved rate for each service, where one exists.</p>
                    <div>
                      <p className="font-medium">Includes</p>
                      <ul className="list-disc pl-5 text-ink-muted">
                        {included.map((f) => (
                          <li key={f.family}>{f.name}</li>
                        ))}
                      </ul>
                    </div>
                    {optional.length ? (
                      <p className="text-ink-muted">
                        <span className="font-medium text-ink">Optional modules:</span> {optional.length}. See the comparison below.
                      </p>
                    ) : null}
                    <ButtonLink variant="primary" href={t.cta_kind === "BUILD_SOLUTION" ? "#inquiry" : t.cta_kind === "CONFIGURE" ? "#solution" : "#market"}>
                      {t.cta_label}
                    </ButtonLink>
                  </div>
                </Card>
              </li>
            );
          })}
        </ul>
        <ul className="grid gap-3 md:grid-cols-2">
          {page.paths.map((p) => (
            <li key={p.code} className="rounded-control border border-border bg-sunken p-3 text-sm">
              <p className="font-semibold">{p.label}</p>
              <p className="mt-1 text-ink-muted">{p.text}</p>
              <p className="mt-2">
                <a className="font-medium text-brand-700 underline" href={p.code === "PARTNER" ? "#inquiry" : "/sign-in"} onClick={() => p.code === "PARTNER" && setKind("PARTNER")}>
                  {p.cta_label}
                </a>
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section id="solution" aria-labelledby="solution-h" className="space-y-3">
        <h2 id="solution-h" className="text-xl font-semibold">
          Build a starting scope
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">
          Choose what you are setting this up for. It preloads the services you are likely to need. It is a starting point for a conversation, not a guaranteed result, and it never switches anything on.
        </p>
        <fieldset className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <legend className="sr-only">Use cases</legend>
          {page.use_cases.map((u) => (
            <Checkbox key={u.code} isSelected={useCases.includes(u.code)} onChange={(on) => setUseCases((s) => (on ? [...s, u.code] : s.filter((c) => c !== u.code)))}>
              {u.label}
            </Checkbox>
          ))}
        </fieldset>
        {scope.length ? (
          <div className="space-y-2 rounded-control border border-border p-3 text-sm" aria-live="polite">
            <p className="font-medium">Your starting scope: {scope.map((f) => names[f] ?? f).join(", ")}.</p>
            {missing.length ? (
              <Banner tone="warn" title="Some services need another one first">
                {missing.map((m) => `${names[m.family]} needs ${names[m.needs]}`).join("; ")}.{" "}
                <Button size="xs" variant="secondary" onPress={() => setExtra((s) => withPrerequisites(page, [...scope, ...s]))}>
                  Add what is missing
                </Button>
              </Banner>
            ) : null}
            <p>
              <a className="font-medium text-brand-700 underline" href="#inquiry">
                Send this scope to Sales
              </a>
            </p>
          </div>
        ) : null}
      </section>

      <section id="compare" aria-labelledby="compare-h" className="space-y-3">
        <h2 id="compare-h" className="text-xl font-semibold">
          Compare all services
        </h2>
        <p className="text-sm text-ink-muted">
          Sixteen services, how each plan packages them, and, once you have chosen a market, whether each is offered there. Base security, privacy and evidence access are not hidden behind add-ons.
        </p>
        <div className="hidden overflow-x-auto md:block" role="region" aria-labelledby="compare-h" tabIndex={0}>
          <table className="w-full min-w-3xl text-sm">
            <caption className="sr-only">The sixteen services by plan, with the market's answer where a market was chosen</caption>
            <thead>
              <tr className="border-b border-border-strong text-left">
                <th scope="col" className="py-2 pr-3">
                  Service
                </th>
                {page.tiers.map((t) => (
                  <th key={t.code} scope="col" className="px-3 py-2 font-semibold">
                    {t.label}
                  </th>
                ))}
                {availability ? (
                  <th scope="col" className="px-3 py-2 font-semibold">
                    In your market
                  </th>
                ) : null}
              </tr>
            </thead>
            {GROUPS.map((g) => (
              <tbody key={g.id}>
                <tr>
                  <th scope="rowgroup" colSpan={page.tiers.length + (availability ? 2 : 1)} className="bg-sunken px-2 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-muted">
                    {g.label}
                  </th>
                </tr>
                {g.families.map((code) => {
                  const f = page.families.find((x) => x.family === code);
                  if (!f) return null;
                  return (
                    <tr key={code} className="border-b border-border last:border-0">
                      <th scope="row" className="py-2 pr-3 text-left font-medium">
                        {f.name}
                        <span className="block text-xs font-normal text-ink-muted">Unit: {f.unit}</span>
                        {scope.includes(code) ? <span className="block text-xs font-normal text-brand-700">In your starting scope</span> : null}
                      </th>
                      {page.tiers.map((t) => {
                        const cell = cellFor(t.family_state[code]!, state[code]);
                        return (
                          <td key={t.code} className="px-3 py-2">
                            <Badge tone={CELL_TONE[cell]} size="xs">
                              {CELL_LABEL[cell]}
                            </Badge>
                            <span className="sr-only">. {CELL_HELP[cell]}</span>
                          </td>
                        );
                      })}
                      {availability ? (
                        <td className="px-3 py-2">
                          <Badge tone={STATE_TONE[state[code] ?? "MARKET_REQUIRED"]} size="xs">
                            {MARKET_LABEL[state[code] ?? "MARKET_REQUIRED"]}
                          </Badge>
                          <RateNote availability={availability} family={code} />
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            ))}
          </table>
        </div>
        <div className="space-y-2 md:hidden">
          {GROUPS.map((g) => (
            <details key={g.id} className="rounded-control border border-border p-3">
              <summary className="cursor-pointer font-semibold">{g.label}</summary>
              <div className="mt-3 space-y-4">
                {g.families.map((code) => {
                  const f = page.families.find((x) => x.family === code);
                  if (!f) return null;
                  return (
                    <div key={code}>
                      <p className="font-medium">{f.name}</p>
                      <p className="text-xs text-ink-muted">Unit: {f.unit}</p>
                      <dl className="mt-1 space-y-1 text-sm">
                        {page.tiers.map((t) => (
                          <div key={t.code} className="flex justify-between gap-3">
                            <dt className="text-ink-muted">{t.label}</dt>
                            <dd>{CELL_LABEL[cellFor(t.family_state[code]!, state[code])]}</dd>
                          </div>
                        ))}
                        {availability ? (
                          <div className="flex justify-between gap-3">
                            <dt className="text-ink-muted">In your market</dt>
                            <dd>
                              {MARKET_LABEL[state[code] ?? "MARKET_REQUIRED"]}
                              <RateNote availability={availability} family={code} />
                            </dd>
                          </div>
                        ) : null}
                      </dl>
                    </div>
                  );
                })}
              </div>
            </details>
          ))}
        </div>
      </section>

      <section id="estimate" aria-labelledby="estimate-h" className="space-y-3">
        <h2 id="estimate-h" className="text-xl font-semibold">
          Estimate your cost
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">An estimate from the approved rate card for your market. It is not a quote and creates no charge.</p>
        <Estimator page={page} ctx={ctx} availability={availability} />
      </section>

      <section id="coverage" aria-labelledby="coverage-h" className="space-y-3">
        <h2 id="coverage-h" className="text-xl font-semibold">
          Where each service is offered
        </h2>
        {availability ? (
          <Coverage availability={availability} />
        ) : (
          <p className="text-sm text-ink-muted">Choose your market above and check availability. Until then no service is shown as available anywhere.</p>
        )}
      </section>

      <section id="inquiry" aria-labelledby="inquiry-h" className="space-y-3">
        <h2 id="inquiry-h" className="sr-only">
          Talk to Sales
        </h2>
        <LeadForm capabilities={scope} names={names} kind={kind} onKind={setKind} notice={page.notice} />
      </section>
    </div>
  );
}

const ORDER: PublicState[] = ["AVAILABLE", "SALES_ASSISTED", "RESTRICTED", "NOT_AVAILABLE"];

function Coverage({ availability }: { availability: PublicAvailability }) {
  return (
    <div className="space-y-4">
      {ORDER.map((s) => {
        const cards = availability.cards.filter((c) => c.state === s);
        if (!cards.length) return null;
        return (
          <section key={s} aria-label={MARKET_LABEL[s]} className="rounded-control border border-border p-3">
            <h3 className="flex flex-wrap items-center gap-2 font-semibold">
              <Badge tone={STATE_TONE[s]} size="xs">
                {MARKET_LABEL[s]}
              </Badge>
              <span className="text-sm font-normal text-ink-muted">{MARKET_HELP[s]}</span>
            </h3>
            <ul className="mt-2 space-y-2 text-sm">
              {cards.map((c) => (
                <li key={c.family}>
                  <span className="font-medium">{c.name}</span>
                  {c.rates.MONTHLY || c.rates.ANNUAL ? (
                    <span className="block text-xs text-ink-muted">
                      {c.rates.MONTHLY ? `Monthly: ${rateText(c.rates.MONTHLY)}. ` : ""}
                      {c.rates.ANNUAL ? `Annual: ${rateText(c.rates.ANNUAL)}.` : ""}
                    </span>
                  ) : null}
                  {c.terms ? <span className="text-ink-muted"> · seller {c.terms.seller_entity} · {c.terms.currency} · data region {c.terms.data_region}</span> : null}
                  {c.reasons.length ? (
                    <ul className="mt-1 list-disc pl-5 text-xs text-ink-muted">
                      {c.reasons.map((r) => (
                        <li key={r.cause}>{r.text}</li>
                      ))}
                    </ul>
                  ) : null}
                  {c.provider_attribution ? <p className="mt-1 text-xs text-ink-muted">{c.provider_attribution}</p> : null}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      <p className="text-xs text-ink-muted">{availability.dimensions.issuer_country.value ? availability.dimensions.issuer_country.note : null}</p>
      {availability.rate_card ? (
        <p className="text-xs text-ink-muted">
          Rates come from approved rate card version {availability.rate_card.version}
          {availability.rate_card.effective_from ? `, effective ${availability.rate_card.effective_from.slice(0, 10)}` : ""}.
        </p>
      ) : null}
    </div>
  );
}

/** The approved rate of one service in the chosen market, or nothing: an unpriced service never shows an amount. */
function RateNote({ availability, family }: { availability: PublicAvailability; family: string }) {
  const rates = availability.cards.find((c) => c.family === family)?.rates;
  if (!rates || (!rates.MONTHLY && !rates.ANNUAL)) return null;
  return <span className="mt-1 block text-xs text-ink-muted">{rates.MONTHLY ? rateText(rates.MONTHLY) : rateText(rates.ANNUAL!)}</span>;
}
