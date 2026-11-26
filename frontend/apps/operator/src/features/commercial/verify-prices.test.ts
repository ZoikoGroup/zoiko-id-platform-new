import { describe, expect, it } from "vitest";

import { earliestStart, priceProblem } from "./verify-prices";

const NOW = new Date(Date.UTC(2026, 10, 25));
const ok = { meter: "VERIFY_CHECK_COMPLETE", currency: "USD", rate: "1.25", start: "2026-12-01" };

describe("proposing a Verify API price (operator)", () => {
  it("starts no earlier than the first day of next month, across a year end", () => {
    expect(earliestStart(NOW)).toBe("2026-12-01");
    expect(earliestStart(new Date(Date.UTC(2026, 11, 31)))).toBe("2027-01-01");
  });
  it("accepts a proper proposal", () => {
    expect(priceProblem(ok, NOW)).toBeNull();
    expect(priceProblem({ ...ok, rate: "0" }, NOW)).toBeNull();
  });
  it("names what is wrong", () => {
    expect(priceProblem({ ...ok, meter: "" }, NOW)).toContain("what is being priced");
    expect(priceProblem({ ...ok, currency: "" }, NOW)).toContain("currency");
    for (const rate of ["", "abc", "-1", "1.1234567", "1,5"]) expect(priceProblem({ ...ok, rate }, NOW)).toContain("number");
    expect(priceProblem({ ...ok, rate: "100000.5" }, NOW)).toContain("at most");
    expect(priceProblem({ ...ok, start: "2026-12-15" }, NOW)).toContain("first day");
    expect(priceProblem({ ...ok, start: "2026-11-01" }, NOW)).toContain("cannot start before 2026-12-01");
    expect(priceProblem({ ...ok, start: "2025-01-01" }, NOW)).toContain("cannot start before");
  });
});
