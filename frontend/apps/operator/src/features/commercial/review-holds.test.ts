import { describe, expect, it } from "vitest";

import { holdReady, releaseReady } from "./review-holds";

describe("Review holds", () => {
  it("needs a capital-letter reason code to hold", () => {
    expect(holdReady("REGULATOR_REQUEST")).toBe(true);
    expect(holdReady("because")).toBe(false);
    expect(holdReady("")).toBe(false);
  });
  it("needs a note to release", () => {
    expect(releaseReady("  okay ")).toBe(true);
    expect(releaseReady(" a ")).toBe(false);
  });
});
