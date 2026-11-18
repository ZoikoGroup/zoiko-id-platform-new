import { describe, expect, it } from "vitest";

import { canCapture, LIVENESS_MS, MAX_LIVENESS_BYTES, MAX_PROBE_BYTES, pickRecorderType, problemOf, toBase64, toFace } from "./selfie";

describe("canCapture", () => {
  it("opens the camera only for an engine approved for the person's country", () => {
    expect(canCapture({ mode: "APPROVED_FOR_SCOPE", countries: ["GB", "DE"] }, "gb")).toEqual({ allowed: true });
    expect(canCapture({ mode: "APPROVED_FOR_SCOPE", countries: ["GB"] }, "FR").allowed).toBe(false);
    expect(canCapture({ mode: "APPROVED_FOR_SCOPE", countries: ["GB"] }, null).allowed).toBe(false);
  });
  it("never opens it while the engine is off, lab only, or unknown", () => {
    for (const mode of ["DISABLED", "LAB_ONLY"] as const) {
      const g = canCapture({ mode, countries: ["GB"] }, "GB");
      expect(g.allowed).toBe(false);
      expect(!g.allowed && g.reason).toMatch(/Nothing about your face is collected|not needed/);
    }
    expect(canCapture(undefined, "GB").allowed).toBe(false);
  });
});

describe("problemOf", () => {
  it("maps browser media errors", () => {
    expect(problemOf({ name: "NotAllowedError" })).toBe("PERMISSION_DENIED");
    expect(problemOf({ name: "NotFoundError" })).toBe("NO_CAMERA");
    expect(problemOf({ name: "NotReadableError" })).toBe("CAMERA_BUSY");
    expect(problemOf(new Error("x"))).toBe("UNKNOWN");
    expect(problemOf(null)).toBe("UNKNOWN");
  });
});

describe("toFace", () => {
  it("returns what finish takes", () => {
    const r = toFace(new Uint8Array([1, 2, 3]), new Uint8Array([4, 5]));
    expect(r).toEqual({ ok: true, face: { probe_base64: "AQID", liveness_base64: "BAU=" } });
  });
  it("names empty and oversized captures", () => {
    expect(toFace(new Uint8Array(0), new Uint8Array(1))).toEqual({ ok: false, problem: "EMPTY" });
    expect(toFace(new Uint8Array(MAX_PROBE_BYTES + 1), new Uint8Array(1))).toEqual({ ok: false, problem: "TOO_LARGE" });
    expect(toFace(new Uint8Array(1), new Uint8Array(MAX_LIVENESS_BYTES + 1))).toEqual({ ok: false, problem: "TOO_LARGE" });
    expect(toFace(new Uint8Array(MAX_PROBE_BYTES), new Uint8Array(MAX_LIVENESS_BYTES)).ok).toBe(true);
  });
  it("encodes large buffers without overflowing the call stack", () => {
    const big = new Uint8Array(1_000_000).fill(7);
    expect(atob(toBase64(big)).length).toBe(1_000_000);
  });
});

describe("recorder and timing", () => {
  it("picks the first supported recording type, or none", () => {
    expect(pickRecorderType((t) => t === "video/webm")).toBe("video/webm");
    expect(pickRecorderType(() => false)).toBeNull();
    expect(LIVENESS_MS).toBeLessThanOrEqual(5000);
  });
});
