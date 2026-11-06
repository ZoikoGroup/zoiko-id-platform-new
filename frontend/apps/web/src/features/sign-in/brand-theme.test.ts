import { describe, expect, it } from "vitest";

import { parseBrand, safeLogo, themeVars } from "./brand-theme";

describe("brand theme", () => {
  it("themes only with a plain hex colour", () => {
    expect(themeVars("#1a73e8")["--color-brand-600"]).toBe("#1a73e8");
    expect(themeVars("#1a73e8")["--color-brand-700"]).toBe("#1662c5");
    expect(themeVars("red; background:url(x)")).toEqual({});
    expect(themeVars("")).toEqual({});
  });
  it("accepts only inline raster images as a logo", () => {
    expect(safeLogo("data:image/png;base64,iVBORw0KGgo=")).not.toBeNull();
    expect(safeLogo("data:image/svg+xml;base64,PHN2Zz4=")).toBeNull();
    expect(safeLogo("https://evil.example/x.png")).toBeNull();
    expect(safeLogo('data:image/png;base64,AAAA"onerror="x')).toBeNull();
  });
  it("keeps only what has the right shape", () => {
    expect(parseBrand(null).accent).toBe("blue");
    expect(parseBrand({ welcome_text: 5, accent_hex: "#112233" })).toMatchObject({ welcome_text: "", accent_hex: "#112233" });
  });
});
