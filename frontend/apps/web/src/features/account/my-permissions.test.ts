import { describe, expect, it } from "vitest";

import { actionLabel, expiryText, limitText, whatItAllows } from "./my-permissions";

const NOW = new Date("2026-11-23T10:00:00Z");

describe("the words for a permission", () => {
  it("shows an action in plain words, never as its id", () => {
    expect(actionLabel("payment.release")).toBe("Release a payment");
    expect(actionLabel("invoice.approve")).toBe("Approve an invoice");
    expect(actionLabel("purchase.create")).toBe("Create a purchase");
  });

  it("shows an action it has no words for as it is, rather than hiding it", () => {
    expect(actionLabel("some.new.action")).toBe("some.new.action");
  });

  it("lists what a permission allows, and says so when it allows everything the company allows", () => {
    expect(whatItAllows({ actions: ["invoice.approve", "payment.release"] })).toEqual(["Approve an invoice", "Release a payment"]);
    expect(whatItAllows({ actions: null })).toEqual(["Everything the company allows"]);
    expect(whatItAllows({ actions: [] })).toEqual([]);
  });
});

describe("how long it lasts", () => {
  it("counts the days", () => {
    expect(expiryText("2026-11-30T10:00:00Z", NOW)).toBe("Expires in 7 days");
    expect(expiryText("2026-11-25T10:00:01Z", NOW)).toBe("Expires in 2 days");
    expect(expiryText("2026-11-24T11:00:00Z", NOW)).toBe("Expires tomorrow");
    expect(expiryText("2026-11-23T18:00:00Z", NOW)).toBe("Expires today");
  });
  it("says Expired once it has ended, and handles no end date or nonsense", () => {
    expect(expiryText("2026-11-23T10:00:00Z", NOW)).toBe("Expired");
    expect(expiryText("2026-01-01T00:00:00Z", NOW)).toBe("Expired");
    expect(expiryText(null, NOW)).toBe("No end date");
    expect(expiryText("not a date", NOW)).toBe("No end date");
  });
});

describe("the money limit", () => {
  it("is shown when there is one, and not when there is none", () => {
    expect(limitText({ value_limit: { amount: "500.00", currency: "USD" } })).toBe("Up to 500.00 USD");
    expect(limitText({ value_limit: null })).toBeNull();
  });
});
