import type { CapabilityDependencies, OfferCard } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import {
  EMPTY_DRAFT,
  buildBody,
  canAdvance,
  coverageGroups,
  dependents,
  expiryNote,
  marketErrors,
  missingDependencies,
  nextStep,
  parseVolume,
  stateTone,
  toggleFamily,
  volumeErrors,
  type Draft,
} from "./solution-builder";

const deps: CapabilityDependencies = {
  version: "t",
  families: [
    { family: "IDV", depends_on: [], needs_provider: true, regulated: false },
    { family: "SIGN", depends_on: ["IDV"], needs_provider: true, regulated: true },
    { family: "API", depends_on: [], needs_provider: false, regulated: false },
  ],
};

const card = (family: string, state: OfferCard["state"]): OfferCard => ({
  family, name: family, unit: "u", sales_qualifier: "q", regulated: false, depends_on: [], sellable: state === "AVAILABLE", next_action: "REQUEST_QUOTE",
  causes: [], refusals: [], state, reasons: [], market: null,
});

const draft = (over: Partial<Draft> = {}): Draft => ({ ...EMPTY_DRAFT, purpose: "ONBOARDING", families: ["API"], subjectCountry: "DE", ...over });

describe("selection", () => {
  it("keeps the selection sorted and toggles", () => {
    expect(toggleFamily(["SIGN"], "IDV")).toEqual(["IDV", "SIGN"]);
    expect(toggleFamily(["IDV", "SIGN"], "IDV")).toEqual(["SIGN"]);
  });
  it("names a signature without a verified identity and blocks going further", () => {
    expect(missingDependencies(["SIGN"], deps)).toEqual([{ family: "SIGN", needs: "IDV" }]);
    expect(missingDependencies(["SIGN", "IDV"], deps)).toEqual([]);
    expect(canAdvance("services", draft({ families: ["SIGN"] }), deps).ok).toBe(false);
    expect(canAdvance("services", draft({ families: ["SIGN", "IDV"] }), deps).ok).toBe(true);
    expect(canAdvance("services", draft({ families: [] }), deps).ok).toBe(false);
  });
  it("tells the person what depends on a service before they remove it", () => {
    expect(dependents(["IDV", "SIGN"], "IDV", deps)).toEqual(["SIGN"]);
    expect(dependents(["IDV"], "IDV", deps)).toEqual([]);
  });
});

describe("volumes and markets", () => {
  it("accepts whole non-negative numbers, treats blank as not stated and refuses the rest", () => {
    expect(parseVolume("5,000")).toEqual({ ok: true, value: 5000 });
    expect(parseVolume("  ")).toEqual({ ok: true, value: undefined });
    for (const bad of ["-1", "1.5", "abc", "1e3", "99999999999"]) expect(parseVolume(bad).ok).toBe(false);
  });
  it("reports a bad figure only for a selected service", () => {
    expect(volumeErrors(draft({ volumes: { API: { expected_units: "x" } } }))).toEqual({ "API.expected_units": expect.any(String) });
    expect(volumeErrors(draft({ volumes: { IDV: { expected_units: "x" } } }))).toEqual({});
  });
  it("wants two-letter country codes and a sensible region", () => {
    expect(Object.keys(marketErrors(draft({ subjectCountry: "Germany" })))).toEqual(["subjectCountry"]);
    expect(marketErrors(draft({ subjectCountry: "de", region: "eu-central" }))).toEqual({});
    expect(Object.keys(marketErrors(draft({ region: "x" })))).toEqual(["region"]);
  });
  it("needs a purpose, a service and a named country before coverage, and none of them needs a price", () => {
    expect(canAdvance("purpose", draft({ purpose: null }), deps).ok).toBe(false);
    expect(canAdvance("coverage", draft({ subjectCountry: "", payerCountry: "" }), deps).ok).toBe(false);
    expect(canAdvance("coverage", draft({ subjectCountry: "", payerCountry: "fr" }), deps).ok).toBe(true);
    expect(canAdvance("review", draft(), deps).ok).toBe(true);
  });
});

describe("the request body", () => {
  it("is built the way the server hashes it: sorted, upper-case countries, only stated volumes", () => {
    const body = buildBody(draft({ families: ["SIGN", "IDV"], subjectCountry: " de ", payerCountry: "", region: " eu-central ", volumes: { IDV: { expected_units: "5,000", manual_reviews: "" }, API: { expected_units: "9" } } }));
    expect(body).toEqual({
      purpose: "ONBOARDING", families: ["IDV", "SIGN"], subject_country: "DE", payer_country: null, operating_region: "eu-central",
      volumes: { IDV: { expected_units: 5000 } },
    });
  });
  it("is null until it can be sent, and never carries a price", () => {
    expect(buildBody(draft({ purpose: null }))).toBeNull();
    expect(buildBody(draft({ families: [] }))).toBeNull();
    expect(buildBody(draft({ volumes: { API: { expected_units: "-4" } } }))).toBeNull();
    expect(buildBody(draft({ subjectCountry: "Germany" }))).toBeNull();
    expect(JSON.stringify(buildBody(draft()))).not.toMatch(/price|amount|rate/i);
  });
});

describe("what is shown", () => {
  it("groups coverage into confirmed, assisted, pending and unavailable, and hides empty groups", () => {
    const groups = coverageGroups([card("A", "AVAILABLE"), card("B", "CONTACT_SALES"), card("C", "RESEARCH_ONLY"), card("D", "NOT_ELIGIBLE"), card("E", "MARKET_REQUIRED")]);
    expect(groups.map((g) => [g.id, g.cards.map((c) => c.family)])).toEqual([["confirmed", ["A"]], ["assisted", ["B", "E"]], ["pending", ["C"]], ["unavailable", ["D"]]]);
    expect(coverageGroups([card("A", "AVAILABLE")]).map((g) => g.id)).toEqual(["confirmed"]);
  });
  it("tones every state and never calls a conversation a refusal", () => {
    expect(stateTone("AVAILABLE")).toBe("ok");
    expect(stateTone("NOT_ELIGIBLE")).toBe("danger");
    expect(stateTone("CONTACT_SALES")).toBe("info");
  });
  it("only ever offers a quote as the way forward, even when everything is available", () => {
    expect(nextStep([card("A", "AVAILABLE")])).toMatch(/signed quote/);
    expect(nextStep([card("A", "AVAILABLE"), card("B", "CONTACT_SALES")])).toMatch(/deal desk/);
    expect(nextStep([])).toMatch(/Choose services/);
  });
  it("says how long a draft has left and refuses an expired one", () => {
    const now = new Date("2026-11-13T00:00:00Z");
    expect(expiryNote("2026-12-13T00:00:00Z", now)).toBe("This draft expires in 30 days.");
    expect(expiryNote("2026-11-14T00:00:00Z", now)).toBe("This draft expires tomorrow.");
    expect(expiryNote("2026-11-12T00:00:00Z", now)).toMatch(/expired/);
  });
});
