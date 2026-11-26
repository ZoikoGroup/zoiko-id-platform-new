import { describe, expect, it } from "vitest";

import { costHeading, hasAny, money, monthLabel, reconciliationText, recentMonths } from "./usage-logic";

describe("verify api usage card", () => {
  it("lists recent months across a year boundary, newest first", () => {
    expect(recentMonths(new Date(Date.UTC(2026, 1, 15)), 4)).toEqual(["2026-02", "2026-01", "2025-12", "2025-11"]);
  });
  it("names a month in words", () => {
    expect(monthLabel("2026-11")).toBe("November 2026");
  });
  it("says when the records match, and what differs when they do not", () => {
    expect(reconciliationText({ sessions_started: { sessions: 3, ledger: 3 }, checks_completed: { sessions: 1, ledger: 1 }, reconciled: true }).ok).toBe(true);
    const bad = reconciliationText({ sessions_started: { sessions: 4, ledger: 2 }, checks_completed: { sessions: 2, ledger: 2 }, reconciled: false });
    expect(bad.ok).toBe(false);
    expect(bad.text).toContain("4 sessions started but 2 recorded");
    expect(bad.text).not.toContain("checks finished");
  });
  it("knows an empty month", () => {
    const zero = { sessions_started: 0, verified: 0, not_verified: 0, inconclusive: 0, review_required: 0, canceled: 0, expired: 0 };
    expect(hasAny(zero)).toBe(false);
    expect(hasAny({ ...zero, expired: 1 })).toBe(true);
  });
});

describe("indicative cost wording", () => {
  it("shows money as the cell sent it, with the currency, and does no arithmetic", () => {
    expect(money("4.46", "USD")).toBe("USD 4.46");
    expect(money("0.00", "GBP")).toBe("GBP 0.00");
  });
  it("says when a currency's total is incomplete", () => {
    expect(costHeading({ currency: "USD", complete: true })).toBe("Indicative cost in USD");
    expect(costHeading({ currency: "JPY", complete: false })).toBe("Indicative cost in JPY (incomplete)");
  });
});
