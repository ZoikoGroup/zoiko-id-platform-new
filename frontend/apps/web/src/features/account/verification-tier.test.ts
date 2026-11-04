import type { VerificationClaimView } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { ALTERNATIVE_COPY, CANNOT_OPTIONS, checkCopy, revocationText, toggle } from "./verification-tier";

const claim = (state: string, revokedAt: string | null = null): VerificationClaimView => ({
  claim: "legal_name",
  method: "DOCUMENT_AND_LIVENESS",
  source: "veriff",
  policy: { level: "IAL-Z2", profile: "uk-onboarding", version: 3 },
  scope: ["onboarding"],
  issued_at: "2026-11-01T00:00:00Z",
  valid_until: "2027-11-01T00:00:00Z",
  revocation: { state, revoked_at: revokedAt, assertion_id: "a" },
});

describe("verification tier wording", () => {
  it("names each check plainly and passes an unknown one through", () => {
    expect(checkCopy("GOVERNMENT_ID").label).toBe("A photo ID");
    expect(checkCopy("EMAIL").label).toBe("Confirm your email address");
    expect(checkCopy("SOME_NEW_CHECK").label).toBe("some new check");
  });

  it("keeps withdrawn, expired and in-force claims distinct", () => {
    expect(revocationText(claim("VALID"))).toMatch(/In force/);
    expect(revocationText(claim("EXPIRED"))).toBe("Expired");
    expect(revocationText(claim("REVOKED", "2026-11-03T10:00:00Z"))).toBe("Withdrawn on 2026-11-03");
    expect(revocationText(claim("STALE"))).toBe("stale");
  });

  it("toggles what the person cannot do, once each", () => {
    expect(toggle([], "NO_CAMERA")).toEqual(["NO_CAMERA"]);
    expect(toggle(["NO_CAMERA", "NO_PHONE"], "NO_CAMERA")).toEqual(["NO_PHONE"]);
  });

  it("has words for every alternative and every thing a person may say they cannot do", () => {
    expect(CANNOT_OPTIONS.map((o) => o.id)).toEqual(["NO_CAMERA", "NO_PHONE", "NO_STANDARD_ID"]);
    expect(Object.keys(ALTERNATIVE_COPY).sort()).toEqual(["ASSISTED_PROOFING", "EQUIVALENT_CREDENTIAL"]);
  });
});
