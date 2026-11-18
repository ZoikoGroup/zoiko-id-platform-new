import { describe, expect, it } from "vitest";

import { anchorFingerprint, derOf, fingerprintsMatch, problemText, STATE, stepsFor, toBase64 } from "./logic";

const der = Uint8Array.from([0x30, 0x03, 0x02, 0x01, 0x05]);
const pem = (bytes: Uint8Array) => new TextEncoder().encode(`-----BEGIN CERTIFICATE-----\n${toBase64(bytes).replace(/(.{4})/g, "$1\n")}\n-----END CERTIFICATE-----\n`);

describe("what a person may do with a package", () => {
  it("offers validate and reject for a staged package, to anyone", () => {
    expect(stepsFor({ state: "STAGED", staged_by: "a" }, "a").steps).toEqual(["validate", "reject"]);
    expect(stepsFor({ state: "STAGED", staged_by: "a" }, "b").steps).toEqual(["validate", "reject"]);
  });

  it("never offers the stager approve or activate, and says why", () => {
    const own = stepsFor({ state: "VALIDATED", staged_by: "a" }, "a");
    expect(own.steps).toEqual(["reject"]);
    expect(own.blockedBecauseYouStaged).toBe(true);
    expect(stepsFor({ state: "APPROVED", staged_by: "a" }, "a")).toEqual({ steps: [], blockedBecauseYouStaged: true });
  });

  it("offers a different person approve, then activate", () => {
    expect(stepsFor({ state: "VALIDATED", staged_by: "a" }, "b")).toEqual({ steps: ["approve", "reject"], blockedBecauseYouStaged: false });
    expect(stepsFor({ state: "APPROVED", staged_by: "a" }, "b")).toEqual({ steps: ["activate"], blockedBecauseYouStaged: false });
  });

  it("offers nothing to an unknown caller for a step that needs a second person, and nothing for a finished package", () => {
    expect(stepsFor({ state: "VALIDATED", staged_by: "a" }, undefined).steps).toEqual(["approve", "reject"]);
    for (const state of ["REJECTED", "ACTIVE", "SUPERSEDED", "ROLLED_BACK", "RETIRED"] as const) {
      expect(stepsFor({ state, staged_by: "a" }, "b").steps).toEqual([]);
    }
  });
});

describe("the anchor fingerprint the person must type back", () => {
  it("is the SHA-256 of the DER bytes, the same for a PEM file as for the DER file", async () => {
    const fromDer = await anchorFingerprint(der);
    const fromPem = await anchorFingerprint(pem(der));
    expect(fromDer).toMatch(/^[0-9a-f]{64}$/);
    expect(fromPem).toBe(fromDer);
    expect(Array.from(derOf(pem(der)))).toEqual(Array.from(der));
  });

  it("matches what was typed whatever the case, spaces or colons, and nothing shorter", async () => {
    const fp = await anchorFingerprint(der);
    expect(fingerprintsMatch(fp.toUpperCase(), fp)).toBe(true);
    expect(fingerprintsMatch(fp.match(/.{2}/g)!.join(":"), fp)).toBe(true);
    expect(fingerprintsMatch(` ${fp} `, fp)).toBe(true);
    expect(fingerprintsMatch(fp.slice(0, 63), fp)).toBe(false);
    expect(fingerprintsMatch("", fp)).toBe(false);
    expect(fingerprintsMatch("0".repeat(64), fp)).toBe(false);
  });
});

describe("the words", () => {
  it("says only an active package is trusted and gives every state a meaning", () => {
    for (const s of Object.values(STATE)) expect(s.meaning.length).toBeGreaterThan(10);
    expect(STATE.ACTIVE.tone).toBe("ok");
    expect(STATE.REJECTED.meaning).toContain("never");
  });

  it("explains a refusal and shows an unknown code as itself", () => {
    expect(problemText("self_approval")).toContain("someone else");
    expect(problemText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});
