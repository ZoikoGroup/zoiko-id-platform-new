import { describe, expect, it } from "vitest";

import { finishedText, purposeText, safeReturn, safeSupport, themeClass, validToken } from "./logic";

describe("hosted verification link", () => {
  it("accepts only the shape of a launch token", () => {
    expect(validToken("zvl_" + "a".repeat(43))).toBe(true);
    for (const bad of ["", "zvl_short", "abc_" + "a".repeat(43), "zvl_" + "a/b".repeat(10), "zvl_" + "a".repeat(200)]) {
      expect(validToken(bad)).toBe(false);
    }
  });
  it("names the purpose in plain words, with a safe fallback", () => {
    expect(purposeText("customer_onboarding")).toContain("account");
    expect(purposeText("something_new")).toBe("to confirm who you are");
  });
  it("returns only to https addresses", () => {
    expect(safeReturn("https://app.example.com/done")).toBe("https://app.example.com/done");
    for (const bad of ["http://app.example.com", "javascript:alert(1)", "nonsense", null, undefined, ""]) {
      expect(safeReturn(bad as string)).toBeNull();
    }
  });
  it("says something different when no answer was given", () => {
    expect(finishedText("expired")).not.toBe(finishedText("approved"));
  });
  it("maps only approved themes to a colour and falls back for anything else", () => {
    expect(themeClass("green").bar).toBe("bg-emerald-600");
    for (const odd of [undefined, "", "red", "#ff0000", "constructor", "__proto__"]) expect(themeClass(odd)).toEqual(themeClass("blue"));
  });
  it("offers a help link only when it is a plain https address", () => {
    expect(safeSupport("https://acme.example/help")).toBe("https://acme.example/help");
    for (const bad of ["http://acme.example", "javascript:alert(1)", "", null, undefined]) expect(safeSupport(bad)).toBeNull();
  });
});
