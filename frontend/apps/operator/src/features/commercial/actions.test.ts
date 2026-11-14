import { describe, expect, it } from "vitest";

import { ACTIONS, buildRequest } from "./actions";

const get = (id: string) => ACTIONS.find((a) => a.id === id)!;

describe("operator commercial actions", () => {
  it("declares a field for every placeholder in every path, and no two actions share an id", () => {
    expect(new Set(ACTIONS.map((a) => a.id)).size).toBe(ACTIONS.length);
    for (const a of ACTIONS) {
      for (const [, name] of a.path.matchAll(/\{(\w+)\}/g)) {
        expect(a.fields.some((f) => f.key === name && f.in === "path"), `${a.id} lacks path field ${name}`).toBe(true);
      }
    }
  });

  it("names the capability every action needs", () => {
    for (const a of ACTIONS) expect(a.capability, a.id).toMatch(/^[a-z]+\.[a-z.]+$/);
  });

  it("asks for a confirmation sentence on the hard-to-undo steps", () => {
    for (const id of ["book-approve", "order-activate", "invoice-issue", "credit-note", "window-freeze", "billing-state"]) {
      expect(get(id).confirm, id).toBeTruthy();
    }
  });

  it("puts path values in the path, the query value in the query and the rest in the body", () => {
    const r = buildRequest(get("adjustment-decide"), { tenant_id: "t-1", adjustment_id: "a-1", approve: true });
    expect(r.path).toBe("/v1/commercial/tenants/t-1/adjustments/a-1:decide?approve=true");
    expect(r.body).toEqual({});
    const line = buildRequest(get("book-line"), { price_book_id: "b-1", sku: "ZID-HUM", plan: "BUSINESS", unit_rate: "0.015", unit_divisor: "1" });
    expect(line.path).toBe("/v1/commercial/price-books/b-1/lines");
    expect(line.body).toEqual({ sku: "ZID-HUM", plan: "BUSINESS", unit_rate: "0.015", unit_divisor: 1 });
  });

  it("keeps money as a decimal string, never a float", () => {
    const r = buildRequest(get("credit-note"), { tenant_id: "t", invoice_id: "i", amount: "10.10", reason_code: "SERVICE_CREDIT" });
    expect(r.body.amount).toBe("10.10");
  });

  it("refuses a missing required field, a bad number and bad JSON with a readable message", () => {
    expect(() => buildRequest(get("plan-set"), { tenant_id: "t" })).toThrow(/Plan is required/);
    expect(() => buildRequest(get("quote-create"), { account_id: "a", price_book_id: "b", region: "uk-1", term_months: "x", lines: "[]" })).toThrow(
      /whole number/,
    );
    expect(() =>
      buildRequest(get("quote-create"), { account_id: "a", price_book_id: "b", region: "uk-1", term_months: "12", lines: "{oops" }),
    ).toThrow(/not valid JSON/);
  });

  it("encodes path values so one cannot escape its segment", () => {
    const r = buildRequest(get("order-activate"), { order_id: "../x?y" });
    expect(r.path).toBe("/v1/commercial/orders/..%2Fx%3Fy:activate");
  });
});

describe("the pricing-page actions", () => {
  it("builds a rate line with exact decimals and whole units, and a minimum that names its model", () => {
    const r = buildRequest(get("rate-card-line"), { card_id: "c-1", family: "API", country: "DE", period: "MONTHLY", currency: "EUR", charge_unit: "completed check", included_units: "100", unit_amount: "0.50", minimum_amount: "49.00", minimum_model: "FLOOR", source_ref: "sched-1" });
    expect(r.path).toBe("/v1/commercial/family-rate-cards/c-1/lines");
    expect(r.body).toMatchObject({ included_units: 100, unit_amount: "0.50", minimum_amount: "49.00", minimum_model: "FLOOR" });
  });
  it("sends the issuer countries of a market row as a list", () => {
    const f = get("matrix-row").fields.find((x) => x.key === "document_issuer_countries");
    expect(f?.kind).toBe("json");
  });
  it("asks for a confirmation sentence before a rate card is signed, activated or shown publicly, and before the page or a notice is signed or activated", () => {
    for (const id of ["rate-card-approve", "rate-card-activate", "page-sign", "notice-activate"]) expect(get(id).confirm, id).toBeTruthy();
  });
  it("lets only the three finance roles sign a rate card", () => {
    expect(get("rate-card-approve").fields.find((x) => x.key === "role")?.options?.map((o) => o.id)).toEqual(["CFO", "CCO", "FOUNDER"]);
  });
});
