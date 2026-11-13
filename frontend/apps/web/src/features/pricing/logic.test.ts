import type { PublicAvailability, PublicPricingPage } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import {
  COUNTRY_CODES,
  EMPTY_CONTEXT,
  EMPTY_LEAD,
  GROUPS,
  availabilityQuery,
  buildLead,
  cellFor,
  contextFromLink,
  contextLinkBody,
  estimateBody,
  rateText,
  countryOptions,
  leadErrors,
  marketNamed,
  missingFor,
  recommendedTier,
  scopeFromUseCases,
  stateByFamily,
  withPrerequisites,
  type LeadDraft,
} from "./logic";

const page = {
  families: [
    { family: "IDV", name: "Identity", unit: "check", sales_qualifier: "", regulated: false, depends_on: [] },
    { family: "DOC", name: "Documents", unit: "doc", sales_qualifier: "", regulated: false, depends_on: [] },
    { family: "SIGN", name: "Signatures", unit: "envelope", sales_qualifier: "", regulated: true, depends_on: ["IDV"] },
  ],
  use_cases: [
    { code: "A", label: "A", families: ["SIGN", "DOC"] },
    { code: "B", label: "B", families: ["IDV"] },
  ],
} as unknown as PublicPricingPage;

describe("the market context", () => {
  it("sends only what was named, so the server reports the rest as not evaluated", () => {
    expect(availabilityQuery(EMPTY_CONTEXT)).toBe("");
    expect(availabilityQuery({ subject: "DE", payer: "", issuer: "", region: " eu-central " })).toBe("subject_country=DE&operating_region=eu-central");
    expect(availabilityQuery({ subject: "germany", payer: "FR", issuer: "SE", region: "" })).toBe("payer_country=FR&issuer_country=SE");
    expect(marketNamed(EMPTY_CONTEXT)).toBe(false);
    expect(marketNamed({ ...EMPTY_CONTEXT, region: "eu-central" })).toBe(true);
  });
  it("names countries in words and never offers a code twice", () => {
    expect(new Set(COUNTRY_CODES).size).toBe(COUNTRY_CODES.length);
    expect(countryOptions().find((o) => o.id === "DE")?.label).toMatch(/Germany/);
  });
});

describe("what a matrix cell says", () => {
  it("starts from the plan's packaging when no market is named", () => {
    expect(cellFor("included", undefined)).toBe("INCLUDED");
    expect(cellFor("optional", "MARKET_REQUIRED")).toBe("OPTIONAL");
    expect(cellFor("quote", undefined)).toBe("QUOTE_ONLY");
  });
  it("lets the market only make it more cautious, never more generous", () => {
    expect(cellFor("included", "AVAILABLE")).toBe("INCLUDED");
    expect(cellFor("quote", "AVAILABLE")).toBe("QUOTE_ONLY");
    expect(cellFor("included", "SALES_ASSISTED")).toBe("QUOTE_ONLY");
    expect(cellFor("optional", "RESTRICTED")).toBe("NOT_IN_MARKET");
    expect(cellFor("included", "NOT_AVAILABLE")).toBe("NOT_IN_MARKET");
  });
  it("groups all sixteen families exactly once", () => {
    const all = GROUPS.flatMap((g) => g.families);
    expect(all).toHaveLength(16);
    expect(new Set(all).size).toBe(16);
  });
  it("reads the state per family from the availability answer", () => {
    const a = { cards: [{ family: "IDV", state: "AVAILABLE" }, { family: "SIGN", state: "RESTRICTED" }] } as unknown as PublicAvailability;
    expect(stateByFamily(a)).toEqual({ IDV: "AVAILABLE", SIGN: "RESTRICTED" });
    expect(stateByFamily(undefined)).toEqual({});
  });
});

describe("use cases", () => {
  it("preload the union of their services and name a missing prerequisite", () => {
    expect(scopeFromUseCases(page, ["A"])).toEqual({ families: ["DOC", "SIGN"], missing: [{ family: "SIGN", needs: "IDV" }] });
    expect(scopeFromUseCases(page, ["A", "B"]).missing).toEqual([]);
    expect(scopeFromUseCases(page, [])).toEqual({ families: [], missing: [] });
  });
  it("can add the prerequisites so no impossible combination is sent", () => {
    expect(withPrerequisites(page, ["SIGN"])).toEqual(["IDV", "SIGN"]);
    expect(withPrerequisites(page, ["DOC"])).toEqual(["DOC"]);
  });
});

const good: LeadDraft = { ...EMPTY_LEAD, name: "Ana Silva", email: "Ana@Example.com", company: "Acme Bank", country: "pt", volume: "FROM_10K_TO_100K", entities: "12" };

describe("the inquiry form", () => {
  it("accepts a good inquiry and builds exactly what the server expects", () => {
    expect(leadErrors(good)).toEqual({});
    const body = buildLead(good, ["KYB", "IDV"], "n-1");
    expect(body).toMatchObject({ kind: "ENTERPRISE", work_email: "ana@example.com", country: "PT", capabilities: ["IDV", "KYB"], entities: 12, partner_type: null, website: "" });
    expect(body?.notice_version).toBe("n-1");
    expect(JSON.stringify(body)).not.toMatch(/price|amount|tenant|entitlement/i);
  });
  it("tells a person what is wrong, field by field", () => {
    const errs = leadErrors({ ...EMPTY_LEAD, kind: "PARTNER", email: "nope", entities: "0" });
    expect(Object.keys(errs).sort()).toEqual(["company", "country", "email", "entities", "name", "partnerType", "volume"]);
  });
  it("refuses identity numbers, links and over-long text before sending", () => {
    expect(leadErrors({ ...good, needs: "passport 123456789" }).needs).toMatch(/identity, document or account numbers/);
    expect(leadErrors({ ...good, needs: "card 4111 1111 1111 1111" }).needs).toBeTruthy();
    expect(leadErrors({ ...good, referral: "https://evil.example" }).referral).toMatch(/links/);
    expect(leadErrors({ ...good, needs: "x".repeat(501) }).needs).toMatch(/500/);
    expect(buildLead({ ...good, needs: "passport 123456789" }, [], "n-1")).toBeNull();
  });
  it("sends a partner type only for a partner and keeps the hidden field as it is", () => {
    expect(buildLead({ ...good, kind: "PARTNER", partnerType: "RESELLER" }, [], "n-1")?.partner_type).toBe("RESELLER");
    expect(buildLead({ ...good, kind: "PARTNER" }, [], "n-1")).toBeNull();
    expect(buildLead({ ...good, website: "bot" }, [], "n-1")?.website).toBe("bot");
  });
});

describe("a scope's missing prerequisites", () => {
  it("count what the buyer added by hand, not only what the use cases preload", () => {
    expect(missingFor(page, ["SIGN"])).toEqual([{ family: "SIGN", needs: "IDV" }]);
    expect(missingFor(page, ["SIGN", "IDV"])).toEqual([]);
    expect(missingFor(page, [])).toEqual([]);
  });
});

describe("the recommended badge", () => {
  const tiers = {
    tiers: [
      { code: "ESSENTIALS", family_state: { IDV: "included", DOC: "included", KYB: "quote", SIGN: "quote" } },
      { code: "BUSINESS", family_state: { IDV: "included", DOC: "included", KYB: "optional", SIGN: "quote" } },
      { code: "ENTERPRISE", family_state: { IDV: "included", DOC: "included", KYB: "optional", SIGN: "quote" } },
    ],
  } as unknown as PublicPricingPage;
  it("comes only from the lowest plan that covers the chosen services", () => {
    expect(recommendedTier(tiers, ["IDV", "DOC"])).toBe("ESSENTIALS");
    expect(recommendedTier(tiers, ["IDV", "KYB"])).toBe("BUSINESS");
  });
  it("is absent with no scope or when every plan leaves a service to Sales", () => {
    expect(recommendedTier(tiers, [])).toBeNull();
    expect(recommendedTier(tiers, ["IDV", "SIGN"])).toBeNull();
  });
});

describe("rates, the estimate and the shared market", () => {
  const rate = { currency: "EUR", charge_unit: "completed check", included_units: 1000, unit_amount: "0.50", minimum_amount: "49.00", minimum_model: "FLOOR" as const };
  it("states a rate in its own currency and unit, and says whether the minimum is a floor or a fee", () => {
    expect(rateText(rate)).toBe("EUR 0.50 per completed check, first 1,000 included; minimum spend EUR 49.00");
    expect(rateText({ ...rate, minimum_model: "PLATFORM_FEE", included_units: 0 })).toBe("EUR 0.50 per completed check; platform fee EUR 49.00 plus usage");
    expect(rateText({ ...rate, minimum_amount: null, minimum_model: null })).not.toMatch(/minimum|fee/);
  });
  const ctx = { ...EMPTY_CONTEXT, payer: "DE", subject: "FR" };
  it("builds an estimate only from whole volumes and a named payer, leaving blank services out", () => {
    expect(estimateBody(ctx, "MONTHLY", { API: "1000", DOC: "", IDV: "0" })).toEqual({ payer_country: "DE", subject_country: "FR", period: "MONTHLY", volumes: { API: 1000 } });
    expect(estimateBody(ctx, "ANNUAL", { API: "12.5" })).toBeNull();
    expect(estimateBody(ctx, "ANNUAL", { API: "-1" })).toBeNull();
    expect(estimateBody(ctx, "ANNUAL", { API: "2000000000" })).toBeNull();
    expect(estimateBody(EMPTY_CONTEXT, "MONTHLY", { API: "5" })).toBeNull();
    expect(estimateBody(ctx, "MONTHLY", {})).toBeNull();
  });
  it("carries only the market in a shared link and reads it back the same way", () => {
    const body = contextLinkBody({ subject: "FR", payer: "DE", issuer: "", region: "eu-central" });
    expect(body).toEqual({ subject_country: "FR", payer_country: "DE", operating_region: "eu-central" });
    expect(contextFromLink({ subject_country: "FR", payer_country: null, issuer_country: "ES", operating_region: null })).toEqual({ subject: "FR", payer: "", issuer: "ES", region: "" });
  });
});
