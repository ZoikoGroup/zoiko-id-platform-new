import { describe, expect, it } from "vitest";

import { baselineBody, regionProblem } from "./baseline-dialog";

describe("company baseline form", () => {
  it("splits regions on spaces and commas and drops repeats", () => {
    const b = baselineBody(null, ["a", "a", "b"], "local-1, local-1  eu-1");
    expect(b.allowed_tools).toEqual(["a", "b"]);
    expect(b.allowed_regions).toEqual(["local-1", "eu-1"]);
  });

  it("keeps what the form does not show: quotas, providers and the schedule switch", () => {
    const b = baselineBody(
      { ai_providers: ["acme"], quotas: { runs_per_agent_day: 5 }, schedules_allowed: true },
      ["a"],
      "local-1",
    );
    expect(b.ai_providers).toEqual(["acme"]);
    expect(b.quotas).toEqual({ runs_per_agent_day: 5 });
    expect(b.schedules_allowed).toBe(true);
  });

  it("sends no quotas when none were set, and schedules off", () => {
    const b = baselineBody({ quotas: {} }, ["a"], "local-1");
    expect(b.quotas).toBeNull();
    expect(b.schedules_allowed).toBe(false);
  });

  it("refuses no region and wildcard regions", () => {
    expect(regionProblem("")).toMatch(/at least one region/);
    expect(regionProblem("eu-*")).toMatch(/Wildcards/);
    expect(regionProblem("local-1")).toBeNull();
  });
});
