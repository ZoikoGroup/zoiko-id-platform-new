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
    const body = buildLead(good, ["KYB", "IDV"]);
    expect(body).toMatchObject({ kind: "ENTERPRISE", work_email: "ana@example.com", country: "PT", capabilities: ["IDV", "KYB"], entities: 12, partner_type: null, website: "" });
    expect(body?.notice_version).toMatch(/notice/);
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
    expect(buildLead({ ...good, needs: "passport 123456789" }, [])).toBeNull();
  });
  it("sends a partner type only for a partner and keeps the hidden field as it is", () => {
    expect(buildLead({ ...good, kind: "PARTNER", partnerType: "RESELLER" }, [])?.partner_type).toBe("RESELLER");
    expect(buildLead({ ...good, kind: "PARTNER" }, [])).toBeNull();
    expect(buildLead({ ...good, website: "bot" }, [])?.website).toBe("bot");
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
