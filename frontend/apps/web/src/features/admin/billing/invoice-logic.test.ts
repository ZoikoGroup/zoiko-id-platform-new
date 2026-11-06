import type { Invoice } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { filterInvoices, invoiceState, isPayable, NO_FILTER } from "./invoice-logic";

const NOW = new Date("2026-11-06T00:00:00Z");
const inv = (o: Partial<Invoice>): Invoice => ({
  invoice_id: "i", number: "ZID-1", status: "ISSUED", period: "2026-10", currency: "USD", subtotal: "100", tax_total: "0", gross: "100", tax_status: "NOT_COMPUTED",
  issued_at: "2026-10-31T00:00:00Z", due_at: "2026-11-30T00:00:00Z", document_digest: null, credited: "0", paid: "0", outstanding: "100", lines: [], ...o,
});

describe("invoice state", () => {
  it("names the states the wireframe lists", () => {
    expect(invoiceState(inv({}), NOW)).toBe("OPEN");
    expect(invoiceState(inv({ paid: "40", outstanding: "60" }), NOW)).toBe("PARTIAL");
    expect(invoiceState(inv({ due_at: "2026-11-01T00:00:00Z" }), NOW)).toBe("OVERDUE");
    expect(invoiceState(inv({ paid: "100", outstanding: "0" }), NOW)).toBe("PAID");
    expect(invoiceState(inv({ credited: "100", outstanding: "0" }), NOW)).toBe("CREDITED");
    expect(invoiceState(inv({ status: "VOID" }), NOW)).toBe("VOID");
    expect(invoiceState(inv({ status: "DRAFT" }), NOW)).toBe("DRAFT");
  });
  it("never calls an invoice paid when the balance is unknown", () => {
    expect(invoiceState(inv({ outstanding: null }), NOW)).toBe("PENDING_VERIFICATION");
    expect(invoiceState(inv({ status: "PENDING_REVIEW" }), NOW)).toBe("PENDING_VERIFICATION");
  });
  it("offers payment only for an issued balance", () => {
    expect(isPayable(inv({}), NOW)).toBe(true);
    expect(isPayable(inv({ paid: "100", outstanding: "0" }), NOW)).toBe(false);
    expect(isPayable(inv({ status: "VOID" }), NOW)).toBe(false);
    expect(isPayable(inv({ outstanding: null }), NOW)).toBe(false);
  });
});

describe("invoice filters", () => {
  const items = [inv({ invoice_id: "a", number: "ZID-001" }), inv({ invoice_id: "b", number: "ZID-002", currency: "EUR", issued_at: "2026-09-30T00:00:00Z", paid: "100", outstanding: "0" })];
  it("searches by number, and narrows by state, currency and date", () => {
    expect(filterInvoices(items, { ...NO_FILTER, text: "002" }, NOW).map((i) => i.invoice_id)).toEqual(["b"]);
    expect(filterInvoices(items, { ...NO_FILTER, state: "PAID" }, NOW).map((i) => i.invoice_id)).toEqual(["b"]);
    expect(filterInvoices(items, { ...NO_FILTER, currency: "USD" }, NOW).map((i) => i.invoice_id)).toEqual(["a"]);
    expect(filterInvoices(items, { ...NO_FILTER, from: "2026-10-01" }, NOW).map((i) => i.invoice_id)).toEqual(["a"]);
    expect(filterInvoices(items, { ...NO_FILTER, to: "2026-09-30" }, NOW).map((i) => i.invoice_id)).toEqual(["b"]);
    expect(filterInvoices(items, NO_FILTER, NOW)).toHaveLength(2);
  });
});
