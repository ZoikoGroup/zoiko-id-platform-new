import { describe, expect, it } from "vitest";

import { adverseMediaSummary, ageText, shortHash, signalSummary, velocitySummary, windowText } from "./signals-format";

describe("signal wording", () => {
  it("says a velocity detail in plain words", () => {
    expect(velocitySummary({ dimension: "DEVICE", action: "SIGNUP", count: 5, threshold: 3, window_seconds: 3600 })).toBe("5 sign-ups from one device in 1 hour (limit 3)");
    expect(velocitySummary({ dimension: "CONTACT", action: "CLAIM", count: 9, threshold: 4, window_seconds: 172_800 })).toBe("9 claims from one contact detail in 2 days (limit 4)");
  });

  it("copes with a detail that is missing fields", () => {
    expect(velocitySummary({})).toBe("Several actions from one source in an unknown period");
  });

  it("says an adverse-media detail in plain words", () => {
    expect(adverseMediaSummary({ provider: "acme", hit_count: 2, severity: "HIGH" })).toBe("Adverse media: 2 possible matches, severity high (from acme)");
    expect(adverseMediaSummary({ hit_count: 1 })).toBe("Adverse media: 1 possible match");
    expect(adverseMediaSummary({})).toBe("Adverse media: no matches");
  });

  it("picks the wording by source", () => {
    expect(signalSummary({ source: "ADVERSE_MEDIA", detail: { hit_count: 1 } })).toMatch(/^Adverse media/);
    expect(signalSummary({ source: "VELOCITY", detail: { count: 2 } })).toMatch(/^2 actions/);
  });

  it("formats windows, hashes and age", () => {
    expect(windowText(90)).toBe("90 seconds");
    expect(windowText(120)).toBe("2 minutes");
    expect(shortHash("ab".repeat(32)).length).toBeLessThan(20);
    expect(shortHash("short")).toBe("short");
    const now = Date.parse("2026-01-02T00:00:00Z");
    expect(ageText("2026-01-01T23:59:30Z", now)).toBe("just now");
    expect(ageText("2026-01-01T22:00:00Z", now)).toBe("2 h");
    expect(ageText("2025-12-30T00:00:00Z", now)).toBe("3 d");
    expect(ageText("nonsense", now)).toBe("unknown");
  });
});
