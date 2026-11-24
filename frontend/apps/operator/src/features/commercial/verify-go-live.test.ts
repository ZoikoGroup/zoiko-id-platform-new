import { describe, expect, it } from "vitest";

import { allConfirmed, CONFIRMATIONS } from "./verify-go-live";

const ALL = { company_verified: true, terms_signed: true, privacy_reviewed: true, billing_ready: true, provider_approved: true };

describe("go-live confirmations", () => {
  it("lists exactly the five items the cell requires", () => {
    expect(CONFIRMATIONS.map((c) => c.id).sort()).toEqual(Object.keys(ALL).sort());
  });
  it("is confirmed only when every item is ticked", () => {
    expect(allConfirmed(ALL)).toBe(true);
    for (const k of Object.keys(ALL) as (keyof typeof ALL)[]) expect(allConfirmed({ ...ALL, [k]: false })).toBe(false);
  });
});
