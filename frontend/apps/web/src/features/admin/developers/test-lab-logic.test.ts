import { describe, expect, it } from "vitest";

import { sessionState } from "./test-lab-logic";

describe("test lab states", () => {
  it("names every session state in plain words", () => {
    for (const s of ["created", "ready", "opened", "capture_started", "submitted", "processing", "action_required", "manual_review", "completed", "inconclusive", "expired", "canceled"]) {
      expect(sessionState(s).label).not.toBe(s);
    }
  });
  it("shows an unknown state as it is", () => {
    expect(sessionState("brand_new")).toEqual({ label: "brand_new", tone: "neutral" });
  });
});
