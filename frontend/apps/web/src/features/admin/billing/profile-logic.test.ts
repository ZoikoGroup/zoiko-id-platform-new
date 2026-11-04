import type { BillingProfile } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { formFromProfile, profileBody, profileChanged, profileFieldLabel, validateProfile } from "./profile-logic";

const empty: BillingProfile = {
  version: 0,
  payer: { legal_name: "Nexa Ltd", country: "GB", currency: "GBP", change_note: "" },
  address: null,
  tax_ids: [],
  contacts: null,
  contact_verification: "UNVERIFIED",
  updated_at: null,
};

describe("billing profile form", () => {
  it("starts from the payer's country and asks for the required fields", () => {
    const f = formFromProfile(empty);
    expect(f.country).toBe("GB");
    const e = validateProfile(f);
    expect(Object.keys(e).sort()).toEqual(["city", "invoice_email", "line1"]);
  });

  it("accepts a complete form and trims what it sends", () => {
    const f = { ...formFromProfile(empty), line1: " 1 High St ", city: "London", invoice_email: " ap@nexa.com ", tax_ids: [{ type: "VAT" as const, value: " GB123456789 " }] };
    expect(validateProfile(f)).toEqual({});
    const body = profileBody(f, 3);
    expect(body.expected_version).toBe(3);
    expect(body.address.line1).toBe("1 High St");
    expect(body.contacts.invoice_email).toBe("ap@nexa.com");
    expect(body.tax_ids).toEqual([{ type: "VAT", value: "GB123456789" }]);
  });

  it("refuses a bad tax number, a bad email, a long list and a lowercase country", () => {
    const base = { ...formFromProfile(empty), line1: "a", city: "b", invoice_email: "x@y.com" };
    expect(validateProfile({ ...base, tax_ids: [{ type: "VAT", value: "ab" }] }).tax_ids).toBeTruthy();
    expect(validateProfile({ ...base, tax_ids: Array.from({ length: 6 }, () => ({ type: "VAT" as const, value: "GB1234" })) }).tax_ids).toMatch(/At most/);
    expect(validateProfile({ ...base, notices_email: "nope" }).notices_email).toBeTruthy();
    expect(validateProfile({ ...base, country: "gb1" }).country).toBeTruthy();
  });

  it("only counts a real difference as a change", () => {
    const a = formFromProfile(empty);
    expect(profileChanged(a, { ...a, city: " " })).toBe(false);
    expect(profileChanged(a, { ...a, city: "Leeds" })).toBe(true);
    expect(profileChanged(a, { ...a, tax_ids: [{ type: "EIN", value: "12-3456789" }] })).toBe(true);
  });

  it("words changed fields and passes unknown ones through", () => {
    expect(profileFieldLabel("tax_ids")).toBe("Tax identifiers");
    expect(profileFieldLabel("zzz")).toBe("zzz");
  });
});
