import { describe, expect, it } from "vitest";

import { micros, outcomeText, parseBudget, percentUsed, problemText, standing } from "./logic";

describe("parseBudget", () => {
  it("accepts a plain decimal and an upper-cased currency", () => {
    expect(parseBudget(" 2500.50 ", "gbp")).toEqual({ ok: true, limit: "2500.50", currency: "GBP" });
    expect(parseBudget("0", "EUR")).toEqual({ ok: true, limit: "0", currency: "EUR" });
    expect(parseBudget("1.123456", "USD").ok).toBe(true);
  });
  it("refuses what the platform refuses", () => {
    for (const bad of ["", "-1", "1e3", "1,000", "abc", "1.1234567", "12345678901234", "1.", ".5", "+5"]) expect(parseBudget(bad, "GBP").ok, bad).toBe(false);
    for (const bad of ["", "GB", "GBPX", "12A", "£"]) expect(parseBudget("10", bad).ok, bad).toBe(false);
  });
});

describe("amounts", () => {
  it("compares exactly, with no floating point", () => {
    expect(micros("0.1") + micros("0.2")).toBe(micros("0.3"));
    expect(micros("12")).toBe(12_000_000n);
    expect(percentUsed("50", "200")).toBe(25);
    expect(percentUsed("0.000001", "1000000")).toBe(0);
    expect(percentUsed("300", "200")).toBe(100);
    expect(percentUsed("0", "0")).toBe(100); // a zero limit means none this month
  });
});

describe("standing", () => {
  it("says there is no limit when none is set", () => {
    expect(standing({ budget: null, month_start: "2026-11-01T00:00:00+00:00" }).title).toMatch(/No monthly limit/);
  });
  it("warns near the limit, blocks at it, and is calm below", () => {
    const base = { month_start: "x", budget: { monthly_limit: "100", currency: "GBP", version: 1 } };
    expect(standing({ ...base, spent: "10", remaining: "90", exceeded: false }).tone).toBe("ok");
    expect(standing({ ...base, spent: "85", remaining: "15", exceeded: false }).tone).toBe("warn");
    const hit = standing({ ...base, spent: "100", remaining: "0", exceeded: true });
    expect(hit.tone).toBe("danger");
    expect(hit.text).toMatch(/already started still finish/);
  });
  it("handles loading", () => {
    expect(standing(undefined).tone).toBe("neutral");
  });
});

describe("words", () => {
  it("explains refusals and shows an unknown code as itself", () => {
    expect(problemText("budget_invalid")).toMatch(/three-letter/);
    expect(problemText("SOMETHING_ELSE")).toBe("SOMETHING_ELSE");
  });
  it("writes outcomes in words", () => {
    expect(outcomeText("VERIFIED", null)).toBe("verified");
    expect(outcomeText("FAILED", "DOCUMENT_EXPIRED")).toBe("failed, document expired");
  });
});
