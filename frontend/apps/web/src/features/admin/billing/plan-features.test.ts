import type { EffectiveEntitlements, PlanFeature, UsageMeter } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { bySection, featureTone, isCompliance, lowerBands, meterNote, reasonText, summary, upgradeHint } from "./plan-features";

const f = (over: Partial<PlanFeature>): PlanFeature => ({
  feature: "advanced.scim-automated-provisioning",
  section: "advanced",
  name: "SCIM automated provisioning",
  band_state: "UPGRADE",
  label: "Available with upgrade",
  available: false,
  reasons: ["NOT_IN_BAND"],
  upgrade_band: "C4",
  note: "",
  ...over,
});

describe("what a customer is told when a feature is not included", () => {
  it("names the band that would include it", () => {
    expect(upgradeHint(f({}))).toContain("C4");
    expect(reasonText(["NOT_IN_BAND"])).toBe("Not part of your plan.");
  });

  it("never offers an upgrade as a way past a region or a compliance hold", () => {
    for (const reason of ["NOT_AVAILABLE_IN_REGION", "COVERAGE_UNKNOWN", "PROVIDER_ROUTE_OUT_OF_COVERAGE"]) {
      const held = f({ reasons: [reason], upgrade_band: "C4" });
      expect(isCompliance(held.reasons)).toBe(true);
      expect(upgradeHint(held)).toBeNull();
      expect(featureTone(held)).toBe("warn");
    }
    expect(reasonText(["NOT_AVAILABLE_IN_REGION"])).toBe("Not available in your region.");
  });

  it("points an add-on at an order, not at a plan change", () => {
    const addOn = f({ band_state: "ADD_ON", reasons: ["ADD_ON_REQUIRED"], upgrade_band: null });
    expect(upgradeHint(addOn)).toContain("add-on");
    expect(upgradeHint(f({ available: true, reasons: [], band_state: "INCLUDED" }))).toBeNull();
  });

  it("says something safe for a reason it does not know", () => {
    expect(reasonText(["SOMETHING_NEW"])).toBe("Not available.");
  });
});

describe("tones and grouping", () => {
  it("shows included, add-on, held and not-included differently", () => {
    expect(featureTone(f({ available: true, reasons: [], band_state: "INCLUDED" }))).toBe("ok");
    expect(featureTone(f({ available: true, reasons: [], band_state: "ADD_ON" }))).toBe("info");
    expect(featureTone(f({}))).toBe("neutral");
  });

  it("groups the baseline first and drops empty groups", () => {
    const groups = bySection([f({ section: "advanced" }), f({ section: "baseline", available: true, reasons: [] })]);
    expect(groups.map((g) => g.section)).toEqual(["baseline", "advanced"]);
    expect(groups[0]!.title).toBe("Always included");
  });

  it("counts included, upgrade and held features separately", () => {
    const v = {
      features: [f({ available: true, reasons: [] }), f({}), f({ reasons: ["NOT_AVAILABLE_IN_REGION"] })],
    } as unknown as EffectiveEntitlements;
    expect(summary(v)).toEqual({ included: 1, upgrade: 1, held: 1, total: 3 });
  });
});

describe("downgrade choices and meters", () => {
  it("offers only lower bands for a downgrade", () => {
    expect(lowerBands("C3")).toEqual(["C1", "C2"]);
    expect(lowerBands("C1")).toEqual([]);
    expect(lowerBands(null)).toEqual([]);
  });

  it("says plainly when a limit has no number or a meter is not priced", () => {
    const m: UsageMeter = { meter: "api.request", counts: "", committed: 0, reserved: 0, released: 0, limit: null, remaining: null, quota_defined: true, priced: false };
    expect(meterNote(m)).toBe("Recorded, not priced and not capped.");
    expect(meterNote({ ...m, limit: 100, remaining: 40 })).toBe("40 of 100 left this period.");
    expect(meterNote({ ...m, quota_defined: false })).toContain("commercial decision");
  });
});
