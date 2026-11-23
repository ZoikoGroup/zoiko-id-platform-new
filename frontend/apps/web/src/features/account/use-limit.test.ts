/** Using a limit: who may, and what is refused before anything is sent. */
import { describe, expect, it } from "vitest";

import { canUseLimit, checkAmount, leftAfter, transactionContext } from "./use-limit";

const limits = { perTransaction: "500", remaining: "1200", currency: "GBP" };

describe("who may use a limit", () => {
  it("is the person it was given to, while it is on", () => {
    expect(canUseLimit({ status: "ACTIVE", i_am: ["executor"] })).toBe(true);
    expect(canUseLimit({ status: "ACTIVE", i_am: ["executor", "creator"] })).toBe(true);
  });
  it("is not someone who only set it up, nor a limit that is paused or waiting", () => {
    expect(canUseLimit({ status: "ACTIVE", i_am: ["creator"] })).toBe(false);
    expect(canUseLimit({ status: "PAUSED", i_am: ["executor"] })).toBe(false);
    expect(canUseLimit({ status: "PENDING_APPROVAL", i_am: ["executor"] })).toBe(false);
  });
});

describe("the amount", () => {
  it("accepts one inside both limits", () => {
    expect(checkAmount(" 250.50 ", limits)).toEqual({ ok: true, amount: "250.50" });
  });
  it("refuses what is not an amount, or is nothing", () => {
    for (const bad of ["", "abc", "-5", "1,000", "12.345", "0", "0.00"]) expect(checkAmount(bad, limits).ok).toBe(false);
  });
  it("refuses more than one payment allows, and says the figure", () => {
    expect(checkAmount("500.01", limits)).toEqual({ ok: false, reason: "One payment can be at most 500 GBP." });
    expect(checkAmount("500", limits).ok).toBe(true);
  });
  it("refuses more than is left", () => {
    expect(checkAmount("400", { ...limits, remaining: "300" })).toEqual({ ok: false, reason: "You only have 300 GBP left." });
  });
  it("has no ceiling to compare with when the limit sets none", () => {
    expect(checkAmount("999999", { perTransaction: null, remaining: null, currency: null }).ok).toBe(true);
  });
});

describe("the rest", () => {
  it("shows what stays after the amount", () => {
    expect(leftAfter("1200", "250.5")).toBe("949.50");
    expect(leftAfter(null, "10")).toBeNull();
  });
  it("ties the reference to the limit", () => {
    expect(transactionContext(" INV-7 ", "m-1")).toBe("use|m-1|INV-7");
  });
});
