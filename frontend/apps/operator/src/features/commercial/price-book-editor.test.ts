import type { PriceBookLine } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { formatRate, matrixOf, validRate } from "./price-book-editor";

const line = (sku: string, plan: string, rate: string, extra: Partial<PriceBookLine> = {}): PriceBookLine => ({ sku, plan, unit_rate: rate, unit_divisor: 1, included_quantity: null, ...extra });

describe("price book matrix", () => {
  it("lays SKUs out against plans in the usual plan order", () => {
    const m = matrixOf([line("ZID-HUM", "SCALE", "1"), line("ZID-BASE", "LAUNCH", "99"), line("ZID-BASE", "BUSINESS", "1499"), line("ZID-HUM", "LAUNCH", "2")]);
    expect(m.plans).toEqual(["LAUNCH", "BUSINESS", "SCALE"]);
    expect(m.skus.map((s) => s.sku)).toEqual(["ZID-BASE", "ZID-HUM"]);
    expect(m.at("ZID-BASE", "BUSINESS")?.unit_rate).toBe("1499");
    expect(m.at("ZID-BASE", "SCALE")).toBeUndefined(); // an unpriced pair stays empty, never a made-up zero
  });
  it("keeps a plan the usual order does not know, after the known ones", () => {
    expect(matrixOf([line("S", "ZZ", "1"), line("S", "LAUNCH", "1")]).plans).toEqual(["LAUNCH", "ZZ"]);
  });
});

describe("rate formatting and checking", () => {
  it("keeps small decimals and calls zero free", () => {
    expect(formatRate("0.015", "USD")).toBe("$0.015");
    expect(formatRate("1499", "USD")).toBe("$1,499.00");
    expect(formatRate("0", "USD")).toBe("Free");
  });
  it("accepts a non-negative number of up to six decimals only", () => {
    for (const ok of ["0", "1499", "0.015", " 12.5 ", "0.000001"]) expect(validRate(ok)).toBe(true);
    for (const bad of ["", "-1", "1,5", "abc", "1.1234567", "1e3", "."]) expect(validRate(bad)).toBe(false);
  });
});
