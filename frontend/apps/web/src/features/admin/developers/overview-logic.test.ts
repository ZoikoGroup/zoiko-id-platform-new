import { describe, expect, it } from "vitest";

import { attention, isWaiting, percent, successRate } from "./overview-logic";

describe("success rate", () => {
  it("is the share of decided verifications that ended verified", () => {
    expect(successRate({ verified: 3, not_verified: 1, inconclusive: 0 })).toBe(0.75);
    expect(percent(0.75)).toBe("75%");
  });
  it("is empty, not zero, when nothing has ended", () => {
    expect(successRate({ verified: 0, not_verified: 0, inconclusive: 0 })).toBeNull();
    expect(percent(null)).toBe("-");
  });
});

describe("what is waiting", () => {
  it("counts a verification nobody has finished, and no finished one", () => {
    expect(isWaiting("ready")).toBe(true);
    expect(isWaiting("capture_started")).toBe(true);
    expect(isWaiting("completed")).toBe(false);
    expect(isWaiting("inconclusive")).toBe(false);
  });
});

describe("what needs attention", () => {
  const base = { productionOpen: true, activeWebhooks: 1, waiting: 0, inconclusive: 0, verified: 2 };
  it("says everything works when nothing is wrong", () => {
    expect(attention(base).map((n) => n.tone)).toEqual(["ok"]);
  });
  it("warns when no webhook is active and points to where to fix it", () => {
    const n = attention({ ...base, activeWebhooks: 0 });
    expect(n[0]?.tone).toBe("warn");
    expect(n[0]?.go?.tab).toBe("endpoints");
  });
  it("says production is closed, and says nothing is fine when it is", () => {
    expect(attention({ ...base, productionOpen: false })[0]?.go?.tab).toBe("verify-api");
    expect(attention(base).some((n) => n.tone === "ok")).toBe(true);
  });
  it("counts the verifications that ended without an answer", () => {
    expect(attention({ ...base, inconclusive: 2 }).some((n) => n.text.startsWith("2 verifications ended without an answer"))).toBe(true);
  });
});
