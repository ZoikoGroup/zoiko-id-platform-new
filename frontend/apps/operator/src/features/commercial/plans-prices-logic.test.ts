import type { PriceBookLine, PriceBookSummary } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { OFFER_DAYS, PRICE_ITEMS, approvedBook, bigChange, changeSentence, itemById, lineOf, offerProblem, offerSentence, parseAmount, priceText, readyToSell, reductionPercent, setupChecklist } from "./plans-prices-logic";

const fee = itemById("ZID-BASE")!;
const rate = itemById("ZID-AUTH")!;
const book = (over: Partial<PriceBookSummary> = {}): PriceBookSummary => ({ price_book_id: "b1", version: 2, currency: "USD", status: "APPROVED", effective_from: null, approved_at: null, note: null, ...over });
const line = (sku: string, plan: string, unit_rate: string): PriceBookLine => ({ sku, plan, unit_rate, unit_divisor: 1, included_quantity: null });

describe("an amount typed by a person", () => {
  it("accepts plain numbers, a dollar sign and thousands commas", () => {
    expect(parseAmount("349", fee)).toEqual({ ok: true, value: "349" });
    expect(parseAmount(" $1,499.50 ", fee)).toEqual({ ok: true, value: "1499.50" });
    expect(parseAmount("0.015", rate)).toEqual({ ok: true, value: "0.015" });
  });
  it("asks for an amount and refuses what is not a number, zero and silly sizes", () => {
    for (const bad of ["", "   ", "abc", "1e3", "-5", "12.", ".5", "1.2.3", "0", "0.00", "1000000.01"]) expect(parseAmount(bad, fee).ok, bad).toBe(false);
  });
  it("keeps a monthly fee to two decimals but lets a usage rate keep small decimals", () => {
    expect(parseAmount("10.999", fee).ok).toBe(false);
    expect(parseAmount("0.0155", rate).ok).toBe(true);
    expect(parseAmount("0.1234567", rate).ok).toBe(false);
  });
});

describe("words for a price", () => {
  it("shows a fee in cents and a small rate without rounding it away", () => {
    expect(priceText("299", "USD")).toBe("$299.00");
    expect(priceText("0.015", "USD")).toBe("$0.015");
    expect(priceText("0.0155", "USD")).toBe("$0.0155");
  });
  it("says what will happen, from and to", () => {
    expect(changeSentence(fee, "LAUNCH", "299", "349", "USD")).toBe("Launch: monthly plan fee changes from $299.00 per month to $349.00 per month.");
    expect(changeSentence(fee, "SCALE", null, "4999", "USD")).toBe("Scale: monthly plan fee will be $4,999.00 per month.");
  });
});

describe("a slip is noticed before it is saved", () => {
  it("flags more than double and less than half, and nothing in between or without a current price", () => {
    expect(bigChange("299", "2990")).toBe("much-higher");
    expect(bigChange("299", "598")).toBe("much-higher");
    expect(bigChange("299", "100")).toBe("much-lower");
    expect(bigChange("299", "349")).toBeNull();
    expect(bigChange(null, "349")).toBeNull();
    expect(bigChange("0", "5")).toBeNull();
  });
});

describe("what can be priced", () => {
  it("lists the monthly fee first, with Enterprise only on the fee, and every item names its unit", () => {
    expect(PRICE_ITEMS[0]!.id).toBe("ZID-BASE");
    expect(PRICE_ITEMS[0]!.plans).toContain("ENTERPRISE");
    for (const i of PRICE_ITEMS.slice(1)) expect(i.plans).not.toContain("ENTERPRISE");
    for (const i of PRICE_ITEMS) expect(i.per.length).toBeGreaterThan(3);
    expect(new Set(PRICE_ITEMS.map((i) => i.id)).size).toBe(PRICE_ITEMS.length);
  });
  it("finds a line by item and plan", () => {
    const lines = [line("ZID-BASE", "LAUNCH", "299"), line("ZID-AUTH", "LAUNCH", "0.4")];
    expect(lineOf(lines, "ZID-AUTH", "LAUNCH")?.unit_rate).toBe("0.4");
    expect(lineOf(lines, "ZID-AUTH", "SCALE")).toBeUndefined();
  });
  it("takes the newest approved book, not a draft", () => {
    const books = [book({ version: 4, status: "DRAFT" }), book({ price_book_id: "b3", version: 3 }), book({ price_book_id: "b1", version: 1, status: "SUPERSEDED" })];
    expect(approvedBook(books)?.price_book_id).toBe("b3");
    expect(approvedBook([book({ status: "DRAFT" })])).toBeUndefined();
  });
});

describe("is it ready for customers", () => {
  const all = [line("ZID-BASE", "LAUNCH", "299"), line("ZID-BASE", "BUSINESS", "1499"), line("ZID-BASE", "SCALE", "4999")];
  it("is ready when a book is approved and the three paid plans have a fee", () => {
    const checks = setupChecklist(book(), all);
    expect(checks).toHaveLength(4);
    expect(readyToSell(checks)).toBe(true);
  });
  it("says exactly what is missing, in order", () => {
    const missingOne = setupChecklist(book(), all.slice(0, 2));
    expect(readyToSell(missingOne)).toBe(false);
    expect(missingOne.filter((c) => !c.ok).map((c) => c.id)).toEqual(["SCALE"]);
    expect(missingOne.find((c) => c.id === "SCALE")!.detail).toBe("Set the Scale monthly fee below.");
    expect(setupChecklist(undefined, []).filter((c) => !c.ok)).toHaveLength(4);
  });
  it("does not count a free plan as priced", () => {
    const free = setupChecklist(book(), [line("ZID-BASE", "LAUNCH", "0"), ...all.slice(1)]);
    expect(free.find((c) => c.id === "LAUNCH")!.ok).toBe(false);
  });
});

describe("a price for one company", () => {
  it("measures how far below the list price it is, and says nothing is saved at the list price or above", () => {
    expect(reductionPercent("4999", "4500")).toBe("9.98");
    expect(reductionPercent("1499", "1499")).toBe("0.00");
    expect(reductionPercent("1499", "1600")).toBe("0.00");
    expect(reductionPercent("0", "10")).toBe("0.00");
    expect(reductionPercent("abc", "10")).toBe("0.00");
  });

  it("accepts a typed price up to the list price and refuses anything else, in words", () => {
    expect(offerProblem("4500", "4999")).toBeNull();
    expect(offerProblem("4999", "4999")).toBeNull(); // the list price itself is a valid answer
    expect(offerProblem("$4,500.50", "4999")).toBeNull();
    expect(offerProblem("5000", "4999")).toMatch(/cannot be above the list price/);
    expect(offerProblem("", "4999")).toMatch(/Type the new price/);
    expect(offerProblem("abc", "4999")).toMatch(/numbers only/);
    expect(offerProblem("0", "4999")).toMatch(/more than zero/);
    expect(offerProblem("10.999", "4999")).toMatch(/two decimals/);
    expect(offerProblem("4500", null)).toMatch(/no list price/);
  });

  it("tells the operator what the company will read and what happens", () => {
    const t = offerSentence("Acme", "SCALE", "4500", "4999", "USD", "14");
    expect(t).toContain("Acme can buy the Scale plan for $4,500.00 a month");
    expect(t).toContain("9.98% below the list price of $4,999.00");
    expect(t).toContain("next 14 days");
    expect(t).toContain("plan switches on only after the payment is confirmed");
    expect(offerSentence("Acme", "SCALE", "4999", "4999", "USD", "7")).toContain("which is the list price");
  });

  it("offers a few clear lengths of time", () => {
    expect(OFFER_DAYS.map((d) => d.id)).toEqual(["7", "14", "30", "60"]);
  });
});
