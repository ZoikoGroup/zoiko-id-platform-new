import { describe, expect, it } from "vitest";

import { UNKNOWN_REGION, oneEntryPerRegion, regionName, regionOf } from "./region";

const uk = { cell_id: "uk-1", jurisdiction: "GB", display_name: "United Kingdom (London)" };
const local = { cell_id: "local-1", jurisdiction: "GB", display_name: "United Kingdom (London)" };
const eu = { cell_id: "eu-1", jurisdiction: "DE", display_name: "Germany (Frankfurt)" };

describe("the region a person reads", () => {
  it("is the place name, and never the cell id", () => {
    expect(regionName(uk)).toBe("United Kingdom (London)");
    expect(regionName(local)).toBe("United Kingdom (London)"); // the local test cell reads like the region it stands for
    expect(regionName(uk)).not.toContain("uk-1");
  });

  it("falls back to the country's own name when a cell has no display name, and never to the id", () => {
    expect(regionName({ cell_id: "xx-9", jurisdiction: "FR" })).toBe("France");
    expect(regionName({ cell_id: "xx-9", jurisdiction: "gb", display_name: "  " })).toBe("United Kingdom");
    expect(regionName({ cell_id: "xx-9", jurisdiction: null })).toBe(UNKNOWN_REGION);
    expect(regionName({ cell_id: "xx-9", jurisdiction: "not-a-code" })).toBe(UNKNOWN_REGION);
    expect(regionName({ cell_id: "xx-9", jurisdiction: null })).not.toContain("xx-9");
    expect(regionName(undefined)).toBe(UNKNOWN_REGION);
    expect(regionName(null)).toBe(UNKNOWN_REGION);
  });

  it("finds a company's region from its home cell", () => {
    expect(regionOf([uk, eu], "eu-1")).toBe("Germany (Frankfurt)");
    expect(regionOf([uk, eu], "gone-1")).toBe(UNKNOWN_REGION); // an unknown cell is never printed
    expect(regionOf([uk, eu], undefined)).toBe(UNKNOWN_REGION);
  });

  it("offers one choice per region, keeping the company's own cell", () => {
    expect(oneEntryPerRegion([uk, local, eu]).map((c) => c.cell_id)).toEqual(["uk-1", "eu-1"]); // "uk-1" and "local-1" are one United Kingdom
    expect(oneEntryPerRegion([uk, local, eu], "local-1").map((c) => c.cell_id)).toEqual(["local-1", "eu-1"]); // what is saved is still the company's own cell
    expect(oneEntryPerRegion([])).toEqual([]);
  });
});
