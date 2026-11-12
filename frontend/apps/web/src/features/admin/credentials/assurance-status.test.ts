import { describe, expect, it } from "vitest";

import { badge, parseBundle, releaseReads, verifyWords } from "./assurance-status";

describe("assurance status display (ZID-UAT-005)", () => {
  it("shows only a recorded independent PASS as done", () => {
    expect(badge("PASS")).toBe("ACTIVE");
    for (const s of ["NOT_RUN", "BLOCKED", "INCONCLUSIVE", "WAIVED", "anything-else", ""]) expect(badge(s)).toBe("PENDING");
  });

  it("shows a failure or an expired pass as rejected, never as pending-and-fine", () => {
    expect(badge("FAIL")).toBe("REJECTED");
    expect(badge("EXPIRED")).toBe("REJECTED");
  });

  it("never reads a release as go while anything is missing", () => {
    expect(releaseReads({ status: "GO", missing: [] })).toBe("go");
    expect(releaseReads({ status: "GO", missing: ["GOV-01:NOT_RUN"] })).toBe("no go");
    expect(releaseReads({ status: "NO_GO", missing: [] })).toBe("no go");
  });
});

describe("evidence builder input", () => {
  it("accepts a stored manifest id or a full bundle and refuses the rest", () => {
    expect(parseBundle("0b7e1a52-4d3c-4c1e-9a0f-5b6d7e8f9a01")).toEqual({ ok: true, bundle: { manifest_id: "0b7e1a52-4d3c-4c1e-9a0f-5b6d7e8f9a01" } });
    expect(parseBundle('{"manifest":{"a":1},"jws":"x.y.z"}').ok).toBe(true);
    for (const bad of ["", "not json", "[]", '{"manifest":{"a":1}}', '{"jws":"x"}']) expect(parseBundle(bad).ok).toBe(false);
  });

  it("reads only a clean verification as intact and says when releases were locked", () => {
    expect(verifyWords({ valid: true, problems: [], locked_releases: [] })).toMatch(/^Intact/);
    const bad = verifyWords({ valid: false, problems: ["MANIFEST_CHANGED"], locked_releases: ["r1"] });
    expect(bad).toMatch(/^Not intact: manifest changed/);
    expect(bad).toMatch(/1 release\(s\) built on it are now locked/);
  });
});

