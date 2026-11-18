import { describe, expect, it } from "vitest";

import { canReview, checkWords, claimText, DECISION, explainReason, explainReason as explain, keptLine, problemText, reasonProblem as whyProblem, RECAPTURE_TEXT, REJECT_TEXT, SIGNAL_LABEL, claimLine as claimSentence, GRADE_TEXT, waitingLine } from "./logic";

describe("decisions", () => {
  it("never names an assurance level and says missing evidence is not a finding", () => {
    for (const d of Object.values(DECISION)) expect(d.meaning).not.toMatch(/IAL|AAL|eIDAS|assurance/i);
    expect(DECISION.INDETERMINATE.meaning).toMatch(/not a finding/);
    expect(Object.keys(DECISION)).toEqual(["APPROVED", "REVIEW_REQUIRED", "REJECTED", "INDETERMINATE", "ABANDONED"]);
  });
});

describe("reasons and claims", () => {
  it("says a missing trust root means the chip is read but not verified", () => {
    expect(explainReason("CHIP_TRUST_ROOT_MISSING")).toMatch(/cannot be called verified/);
    expect(explainReason("PRESENTATION_ATTACK_DETECTED")).toMatch(/does not cancel it/);
  });
  it("words the biometric switch and leaves unknown codes visible", () => {
    expect(explainReason("BIOMETRICS_OFF:NO_IMPACT_ASSESSMENT")).toBe("Biometric steps are off for this market: no impact assessment.");
    expect(explainReason("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(claimText("CHIP_NOT_CLONED")).toBe("The chip is not a copy");
  });
  it("labels all six signals", () => {
    expect(Object.keys(SIGNAL_LABEL)).toHaveLength(6);
  });
  it("words each refusal", () => {
    expect(problemText("REVIEWER_IS_THE_APPLICANT")).toMatch(/own case/);
  });
});

describe("review", () => {
  it("offers a signature only on an open alternative-route case waiting for a person", () => {
    const base = { state: "OPEN", route: "PASSPORT_OPTICAL_REVIEW", reviewers: 0, decision: "REVIEW_REQUIRED" as const };
    expect(canReview(base)).toBe(true);
    expect(canReview({ ...base, route: "CHIP" })).toBe(false);
    expect(canReview({ ...base, state: "CLOSED" })).toBe(false);
    expect(canReview({ ...base, decision: "INDETERMINATE" })).toBe(false);
  });
});

describe("what a reviewer sees of a photograph and may do about it", () => {
  it("puts a reviewer's recapture and rejection in words, and an unknown code through unchanged", () => {
    expect(explain("RECAPTURE_REQUESTED:GLARE_OR_SHADOW")).toMatch(/new photograph.*glare or a shadow/);
    expect(explain("REVIEWER_REJECTED:DOCUMENT_EXPIRED")).toMatch(/rejected.*expired/);
    expect(explain("REVIEWER_REJECTED:SOMETHING_NEW")).toMatch(/something_new/);
    expect(Object.keys(REJECT_TEXT).sort()).toEqual(["DETAILS_INCONSISTENT", "DOCUMENT_EXPIRED", "DOCUMENT_MANIPULATION_SUSPECTED", "NOT_A_SUPPORTED_DOCUMENT"]);
    expect(Object.keys(RECAPTURE_TEXT).sort()).toEqual(["GLARE_OR_SHADOW", "PAGE_CUT_OFF", "PHOTO_UNREADABLE", "WRONG_PAGE"]);
  });
  it("words a measured check without calling a good score proof", () => {
    expect(checkWords("SHARPNESS", "PASS")).toMatchObject({ tone: "ok", text: "fine" });
    expect(checkWords("ELA_ANOMALY", "FAIL").text).toMatch(/worth a look/);
    expect(checkWords("GLARE", "INCONCLUSIVE").tone).toBe("info");
    expect(checkWords("MRZ_CHECK_DIGITS", "PASS").label).toMatch(/check digits/);
  });
  it("needs a real reason before the photograph is opened", () => {
    expect(whyProblem("short")).toMatch(/at least 10/);
    expect(whyProblem("x".repeat(501))).toMatch(/under 500/);
    expect(whyProblem("Checking the photo page against the details")).toBeNull();
  });
  it("says how long a photograph is kept, or that it is gone", () => {
    expect(keptLine({ has_photo: false })).toMatch(/no photograph/);
    expect(keptLine({ has_photo: true, expired: true })).toMatch(/ended/);
    expect(keptLine({ has_photo: true, expires_at: "2026-12-01T00:00:00Z" })).toMatch(/Kept encrypted until/);
  });
  it("explains the new errors a reviewer can meet", () => {
    expect(problemText("GRANT_INVALID")).toMatch(/Ask again with a reason/);
    expect(problemText("REASON_REQUIRED")).toMatch(/10 to 500/);
  });
});

describe("what an approval counts for, and what is waiting", () => {
  it("says an approved case with no claim is relied on by nothing, and never promises more than the grade", () => {
    expect(claimSentence(null)).toMatch(/nothing else on the platform will rely/);
    expect(claimSentence({ grade: "REVIEWED_NO_CHIP", valid_until: "2027-01-01T00:00:00Z", status: "VALID" })).toMatch(/reviewed by people, no chip read/);
    expect(claimSentence({ grade: "CHIP_VERIFIED", valid_until: "2027-01-01T00:00:00Z", status: "REVOKED" })).toMatch(/withdrawn/);
    expect(GRADE_TEXT.REVIEWED_NO_CHIP).not.toMatch(/verified/i);
  });
  it("words the wait and the deadline", () => {
    expect(waitingLine({ waiting_hours: 0, due_at: null, overdue: false })).toBe("just arrived");
    expect(waitingLine({ waiting_hours: 5, due_at: null, overdue: false })).toBe("waiting 5 hours");
    expect(waitingLine({ waiting_hours: 72, due_at: null, overdue: false })).toBe("waiting 3 days");
    expect(waitingLine({ waiting_hours: 30, due_at: "2026-11-18T10:00:00Z", overdue: true })).toMatch(/overdue/);
    expect(waitingLine({ waiting_hours: 2, due_at: "2026-11-19T10:00:00Z", overdue: false })).toMatch(/due/);
  });
});
