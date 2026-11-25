import { describe, expect, it } from "vitest";
import { canonicalize, verifyBundle, type Jwk } from "../src/evidence-verify";
import fixtures from "./fixtures/evidence-conformance.json";

/** Two independent verifiers must agree: the Python one wrote the expected answers, this one has to reach them. */

const hex = (b: Uint8Array): string => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");

describe("canonical JSON agrees with the Python implementation", () => {
  for (const [i, v] of fixtures.jcs.entries()) {
    it(`vector ${i}`, async () => {
      const bytes = canonicalize(v.value);
      expect(hex(bytes)).toBe(v.canonical_hex);
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes as BufferSource));
      expect(`sha-256:${hex(digest)}`).toBe(v.sha256);
    });
  }
  it("refuses what Python refuses", () => {
    expect(() => canonicalize({ a: Number.NaN })).toThrow();
    expect(() => canonicalize("\ud800")).toThrow();
  });
});

describe("bundle verification agrees on every check", () => {
  for (const c of fixtures.bundles) {
    it(c.name, async () => {
      const r = await verifyBundle(c.bundle, { trustedKeys: (c.trusted_keys ?? undefined) as Jwk[] | undefined, requireTimestamp: c.require_timestamp });
      expect(r.checks.filter((x) => x.ok).map((x) => x.name)).toEqual(c.expected.passed);
      expect(r.checks.filter((x) => !x.ok).map((x) => x.name)).toEqual(c.expected.failed);
      expect(r.ok).toBe(c.expected.ok);
      expect(r.structuralOnly).toBe(c.expected.structural_only);
      expect(r.keysSource).toBe(c.expected.keys_source);
    });
  }
  it("never throws on garbage", async () => {
    for (const junk of [null, 5, "x", [], {}, { format: "zid-evidence-bundle/1" }]) {
      const r = await verifyBundle(junk, { trustedKeys: [] });
      expect(r.ok).toBe(false);
    }
  });
  it("covers a real mix of passing and failing cases", () => {
    const ok = fixtures.bundles.filter((c) => c.expected.ok).length;
    expect(ok).toBeGreaterThanOrEqual(5);
    expect(fixtures.bundles.length - ok).toBeGreaterThanOrEqual(12);
  });
});
