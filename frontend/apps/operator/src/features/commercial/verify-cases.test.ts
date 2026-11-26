import { describe, expect, it } from "vitest";

import { KIND_TEXT, noteReady } from "./verify-cases";

describe("Verify API cases (operator)", () => {
  it("describes every kind of case in plain words", () => {
    for (const k of ["DISPUTE", "APPEAL", "ERASURE"]) expect(KIND_TEXT[k]).toBeTruthy();
    expect(KIND_TEXT.ERASURE).toContain("removed");
  });
  it("needs a reason the company can read before any decision", () => {
    expect(noteReady("")).toBe(false);
    expect(noteReady("  ab ")).toBe(false);
    expect(noteReady("Done")).toBe(true);
  });
});
