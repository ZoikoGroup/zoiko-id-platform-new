import { describe, expect, it } from "vitest";

import { assertionText, CHECK_LABEL, checkMeaning, headline, RESULT_LABEL } from "./proofing";

const decisions = ["verified_for_scope", "review_required", "more_evidence", "indeterminate", "rejected", "pending"] as const;

describe("the result headline", () => {
  it("reads as verified only for the platform's own decision, and always says for what", () => {
    const verified = headline({ decision: "verified_for_scope", scope: "account_opening", indeterminate: false });
    expect(verified.title).toBe("Verified for account opening");
    expect(verified.tone).toBe("ok");
    expect(verified.body).toContain("this purpose only");
    for (const d of decisions.filter((x) => x !== "verified_for_scope")) {
      const h = headline({ decision: d, scope: "account_opening", indeterminate: d === "indeterminate" });
      expect(h.title.toLowerCase()).not.toContain("verified for");
      expect(h.tone).not.toBe("ok");
    }
  });

  it("never describes an indeterminate result as a decision about the person", () => {
    const h = headline({ decision: "indeterminate", scope: "onboarding", indeterminate: true });
    expect(h.body).toContain("not a decision about you");
  });

  it("names every decision in WF01's words", () => {
    expect(headline({ decision: "review_required", scope: "x", indeterminate: false }).title).toBe("Under review");
    expect(headline({ decision: "more_evidence", scope: "x", indeterminate: false }).title).toBe("More evidence required");
    expect(headline({ decision: "rejected", scope: "x", indeterminate: false }).title).toBe("Not verified");
    expect(headline({ decision: "pending", scope: "x", indeterminate: false }).title).toBe("In progress");
  });
});

describe("what each check means", () => {
  const types = Object.keys(CHECK_LABEL) as (keyof typeof CHECK_LABEL)[];
  const results = Object.keys(RESULT_LABEL) as (keyof typeof RESULT_LABEL)[];

  it("has words for every check and result", () => {
    for (const t of types) for (const r of results) expect(checkMeaning(t, r).length).toBeGreaterThan(10);
  });

  it("says a passing chip shows the document is genuine and not that the person is its holder", () => {
    expect(checkMeaning("chip_crypto", "pass")).toContain("not that you are its holder");
    expect(checkMeaning("document_validity", "pass")).toContain("does not show you own it");
  });

  it("calls an unjudged chip not a failure, and an unavailable face match a missing tie, never a mismatch", () => {
    expect(checkMeaning("chip_crypto", "indeterminate")).toContain("not a failure");
    expect(checkMeaning("face_match", "indeterminate")).not.toContain("did not match");
  });

  it("says a check that was not asked for was not asked for", () => {
    for (const t of types) expect(checkMeaning(t, "not_requested")).toContain("did not ask");
  });
});

describe("what the platform holds true", () => {
  it("has plain words for the assertions and shows an unknown code as itself", () => {
    expect(assertionText("IDENTITY_VERIFIED_FOR_SCOPE")).toBe("Identity verified for this purpose");
    expect(assertionText("DOCUMENT_CHIP_CRYPTO_VALID")).toContain("signature was checked");
    expect(assertionText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});
