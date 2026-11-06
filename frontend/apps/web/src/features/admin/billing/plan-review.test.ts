import type { CatalogPlan } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { losses, monthlyFee, reviewRows } from "./plan-review";

const plan = (code: string, mah: number | null, caps: string[] = []): CatalogPlan => ({
  plan: code, label: code, platform_fee_is_floor: false,
  limits: { mah_included: mah, auth_events_included: 100, policy_decisions_included: 100, m2m_grants_included: 10, sso_included: 1, production_environments: 1, support_level: "Standard" },
  capabilities: caps.map((capability) => ({ capability, mode: "ON", tier: null })),
});

describe("plan review", () => {
  it("shows more, less and unknown allowances from the catalogue only", () => {
    const rows = reviewRows(plan("A", 100), plan("B", 500));
    expect(rows.find((r) => r.label === "People included")).toMatchObject({ from: "100", to: "500", change: "MORE" });
    expect(reviewRows(plan("A", 500), plan("B", 100)).find((r) => r.label === "People included")?.change).toBe("LESS");
    expect(reviewRows(plan("A", 100), plan("B", null)).find((r) => r.label === "People included")).toMatchObject({ to: "Agreed per order", change: "UNKNOWN" });
  });
  it("lists what a move would take away", () => {
    expect(losses(plan("A", 500, ["sso"]), plan("B", 100))).toEqual(["People included: 500 becomes 100", "Capability sso is not in the new plan"]);
    expect(losses(plan("A", 100), plan("B", 500))).toEqual([]);
    expect(losses(undefined, plan("B", 100))).toEqual([]);
  });
  it("never invents a price", () => {
    expect(monthlyFee(undefined, "A")).toBeNull();
    expect(monthlyFee({ pricing: null }, "A")).toBeNull();
    expect(monthlyFee({ pricing: { version: 1, currency: "USD", effective_from: "x", lines: [{ sku: "ZID-BASE", plan: "A", unit: "m", unit_rate: "49", unit_divisor: 1, released: true }] } }, "A")).toBe("49");
  });
});
