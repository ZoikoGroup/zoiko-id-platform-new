import { describe, expect, it } from "vitest";

import { budgetCheckMessage } from "./shared";

describe("budgetCheckMessage", () => {
  it("counts what was raised", () => {
    expect(budgetCheckMessage({ raised: [{}, {}], status: "EVALUATED" })).toBe("2 new notification(s) raised");
  });
  it("says nothing is new only when the budgets were really evaluated", () => {
    expect(budgetCheckMessage({ raised: [], status: "EVALUATED" })).toBe("Nothing new to report");
  });
  it("does not read as all clear when no price book covers the month", () => {
    expect(budgetCheckMessage({ raised: [], status: "UNPRICED" })).toMatch(/cannot be checked/);
  });
});
