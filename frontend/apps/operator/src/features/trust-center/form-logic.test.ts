import { describe, expect, it } from "vitest";

import { evidenceReady, figureReady, splitList } from "./form-logic";

const ev = { digest: "a".repeat(64), scheme: "SOC 2", issuer: "Auditor LLP", legal_entity: "Zoiko Ltd", holder: "zoiko", products: "verify_api, hosted_flow", period_start: "2026-01-01", period_end: "2026-12-31" };
const fig = { service: "verify_api", region: "uk-1", window_label: "2026-10", numerator: "9990", denominator: "10000", source_ref: "telemetry:2026-10" };

describe("trust center form rules", () => {
  it("splits lists on commas and lines and drops blanks", () => {
    expect(splitList("a, b,,\n c ")).toEqual(["a", "b", "c"]);
  });
  it("needs a real digest, a covered product and a period that does not end before it starts", () => {
    expect(evidenceReady(ev)).toBe(true);
    expect(evidenceReady({ ...ev, digest: "xyz" })).toBe(false);
    expect(evidenceReady({ ...ev, products: " , " })).toBe(false);
    expect(evidenceReady({ ...ev, period_end: "2025-12-31" })).toBe(false);
  });
  it("accepts a figure only when good does not exceed eligible and eligible is above zero", () => {
    expect(figureReady(fig)).toBe(true);
    expect(figureReady({ ...fig, numerator: "10001" })).toBe(false);
    expect(figureReady({ ...fig, denominator: "0" })).toBe(false);
    expect(figureReady({ ...fig, window_label: "October" })).toBe(false);
    expect(figureReady({ ...fig, numerator: "" })).toBe(false);
  });
});
