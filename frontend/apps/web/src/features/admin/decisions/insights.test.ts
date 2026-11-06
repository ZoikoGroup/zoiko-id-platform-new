import { describe, expect, it } from "vitest";

import { controlLine, percent } from "./insights";

describe("insights wording", () => {
  it("shows rates plainly", () => {
    expect(percent(null)).toBe("n/a");
    expect(percent(1)).toBe("100%");
    expect(percent(0.4286)).toBe("42.9%");
  });
  it("says what a control's counts mean", () => {
    expect(controlLine({ control: "x", label: "x", passed: 1, of: 2, rate: 0.5, met: false })).toBe("1 of 2 (50.0%)");
    expect(controlLine({ control: "x", label: "x", passed: 0, of: 0, rate: null, met: null })).toMatch(/No decisions/);
  });
});
