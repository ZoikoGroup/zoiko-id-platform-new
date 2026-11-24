import { describe, expect, it } from "vitest";

import { draftProblem, EMPTY_DRAFT, marketsProblem, methodLabel, parseMarkets, purposeLabel, toRequest } from "./go-live-logic";

const OK = { markets: "gb, us\nGB", methods: ["face_match"], purpose: "customer_onboarding", band: "lt_1k", useCase: "Verify new customers of our app.", terms: true, dpa: true };

describe("go live form", () => {
  it("reads countries from commas, spaces and lines, upper-cased and without repeats", () => {
    expect(parseMarkets("gb, us\nGB  fr;de")).toEqual(["GB", "US", "FR", "DE"]);
    expect(parseMarkets("  ")).toEqual([]);
  });
  it("explains a bad country list", () => {
    expect(marketsProblem([])).toContain("at least one");
    expect(marketsProblem(["USA"])).toContain("USA");
    expect(marketsProblem(Array.from({ length: 21 }, (_, i) => String.fromCharCode(65 + (i % 26)) + "A"))).toContain("at most 20");
    expect(marketsProblem(["GB", "US"])).toBeNull();
  });
  it("names the first missing thing, in the order the form is filled", () => {
    expect(draftProblem(EMPTY_DRAFT)).toContain("country");
    expect(draftProblem({ ...OK, methods: [] })).toContain("check");
    expect(draftProblem({ ...OK, purpose: "" })).toContain("use it for");
    expect(draftProblem({ ...OK, band: "" })).toContain("how many");
    expect(draftProblem({ ...OK, useCase: "short" })).toContain("10 to 500");
    expect(draftProblem({ ...OK, terms: false })).toContain("terms");
    expect(draftProblem({ ...OK, dpa: false })).toContain("terms");
    expect(draftProblem(OK)).toBeNull();
  });
  it("builds the request with the versions the person accepted", () => {
    const r = toRequest(OK, { terms: "2026-11", dpa: "2026-11" });
    expect(r).toMatchObject({ markets: ["GB", "US"], terms_version: "2026-11", accept_terms: true, accept_dpa: true, purpose_code: "customer_onboarding" });
  });
  it("labels checks and purposes, with a safe fallback", () => {
    expect(methodLabel("face_match")).toContain("face");
    expect(methodLabel("new_thing")).toBe("new thing");
    expect(purposeLabel("fraud_prevention")).toContain("fraud");
  });
});
