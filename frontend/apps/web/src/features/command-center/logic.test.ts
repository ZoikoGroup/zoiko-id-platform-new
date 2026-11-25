import { describe, expect, it } from "vitest";

import { basis, defaultWindow, label, shown, viewOf, VIEWS } from "./logic";

describe("Command Center figures (WF-02 truthfulness rules)", () => {
  it("has the twelve governed views and falls back to the overview", () => {
    expect(VIEWS).toHaveLength(12);
    expect(viewOf("economics")).toBe("economics");
    expect(viewOf("../admin")).toBe("overview");
  });

  it("never shows a withheld or missing figure as a value", () => {
    expect(shown(0.8891, "ratio")).toBe("88.9%");
    expect(shown(0, "count")).toBe("0");
    expect(shown(null, "ratio")).toBe("No data");
    expect(shown(0.5, "ratio", true)).toBe("Withheld");
    expect(shown(150, "seconds")).toBe("2.5 min");
  });

  it("puts the denominator and definition version under every headline figure (Q10)", () => {
    expect(basis({ numerator: 889, denominator: 1000, definition_version: "1.0", suppressed: false, unit: "ratio" })).toBe("889 of 1000 · definition v1.0");
    expect(basis({ numerator: null, denominator: null, definition_version: "1.0", suppressed: true, unit: "ratio" })).toMatch(/Fewer than the minimum group/);
  });

  it("labels sandbox data as sandbox, never as observed production (Q11)", () => {
    expect(label("OBSERVED", "SANDBOX").text).toMatch(/Sandbox/);
    expect(label("OBSERVED", "OBSERVED").text).toBe("Observed");
    expect(label("PROVISIONAL", "OBSERVED").tone).toBe("warn");
  });

  it("defaults to the last seven days in the person's timezone", () => {
    const w = defaultWindow(new Date("2026-11-25T23:30:00Z"), "Asia/Kolkata");
    expect(w.to).toBe("2026-11-26");
    expect(w.from).toBe("2026-11-20");
  });
});
