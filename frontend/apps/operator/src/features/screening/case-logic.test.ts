import { describe, expect, it } from "vitest";

import { ACTIONS_FOR, askProblem, codes, EDD_ITEMS, EMPTY_FORM, errorText, needsChecker, proposeProblem, reasonText } from "./case-logic";

const ok = { ...EMPTY_FORM, disposition: "CLEARED_FOR_PURPOSE" as const, action: "RELEASE" as const, rationale: "The date of birth and the country both differ from the subject.", reasons: "conflicting_dob", evidence: "ev_1" };

describe("screening case decisions", () => {
  it("offers only the acts that fit a decision", () => {
    expect(ACTIONS_FOR.CLEARED_FOR_PURPOSE).toEqual(["RELEASE"]);
    expect(ACTIONS_FOR.PROHIBITED).toContain("FREEZE");
    expect(ACTIONS_FOR.HOLD_PENDING_REVIEW).toEqual(["HOLD"]);
  });

  it("knows what needs a second person", () => {
    expect(needsChecker("HOLD_PENDING_REVIEW", "HOLD")).toBe(false);
    expect(needsChecker("EDD_REQUIRED", "EDD")).toBe(false);
    expect(needsChecker("CLEARED_FOR_PURPOSE", "RELEASE")).toBe(true);
    expect(needsChecker("RESTRICTED", "HOLD")).toBe(true);
    expect(needsChecker("PROHIBITED", "BLOCK")).toBe(true);
  });

  it("names what the platform would refuse before it has to", () => {
    expect(proposeProblem(ok)).toBeNull();
    expect(proposeProblem({ ...ok, action: "REJECT" })).toContain("does not fit");
    expect(proposeProblem({ ...ok, rationale: "short" })).toContain("20 characters");
    expect(proposeProblem({ ...ok, reasons: "" })).toContain("reason code");
    expect(proposeProblem({ ...ok, reasons: "no good!" })).toContain("capital letters");
    expect(proposeProblem({ ...ok, evidence: "" })).toContain("evidence");
    expect(proposeProblem({ ...ok, disposition: "PROHIBITED", action: "BLOCK", reviewBy: "2027-01-01T00:00" })).toContain("citation");
    expect(proposeProblem({ ...ok, disposition: "PROHIBITED", action: "BLOCK", citation: "Reg 1" })).toContain("looked at again");
    expect(proposeProblem({ ...ok, disposition: "PROHIBITED", action: "BLOCK", citation: "Reg 1", reviewBy: "2027-01-01T00:00" })).toBeNull();
    expect(proposeProblem({ ...EMPTY_FORM, rationale: "A hold while the identifiers are checked.", reasons: "AWAITING_IDS" })).toBeNull();
  });

  it("turns free text into upper-case codes", () => {
    expect(codes("conflicting_dob, name_common  alias_checked")).toEqual(["CONFLICTING_DOB", "NAME_COMMON", "ALIAS_CHECKED"]);
    expect(codes("")).toEqual([]);
  });

  it("says reasons and refusals in words and an unknown code as itself", () => {
    expect(reasonText("POSSIBLE_MATCH_AWAITS_REVIEWER")).toContain("reviewer");
    expect(reasonText("NOT_SCREENED:PEP_RCA")).toBe("Not screened: pep rca.");
    expect(reasonText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(errorText("CHECKER_IS_THE_MAKER")).toContain("someone else");
    expect(errorText("LEGAL_ROLE_REQUIRED")).toContain("Legal");
    expect(errorText("UNKNOWN")).toBe("UNKNOWN");
  });
});

describe("information requests and the new reason codes", () => {
  it("says what the PEP dates and the article link mean, in words", () => {
    expect(reasonText("PEP_FORMER")).toMatch(/does not by itself lower the risk/);
    expect(reasonText("PEP_NOTE_DATES_MISSING")).toMatch(/never as former/);
    expect(reasonText("ARTICLE_NAME_ONLY")).toMatch(/never a strong match/);
    expect(reasonText("ARTICLE_CONTRADICTED")).toMatch(/contradicts/);
    expect(reasonText("ARTICLE_RETRACTED")).toMatch(/retracted/);
  });
  it("lets a reviewer ask for catalogue items inside the window and nothing else", () => {
    expect(Object.keys(EDD_ITEMS)).toContain("SOURCE_OF_FUNDS");
    expect(askProblem([], 14)).toMatch(/at least one/);
    expect(askProblem(["BANK_PASSWORD"], 14)).toMatch(/from the list/);
    expect(askProblem(["SOURCE_OF_FUNDS"], 2)).toMatch(/3 and 60/);
    expect(askProblem(["SOURCE_OF_FUNDS"], 61)).toMatch(/3 and 60/);
    expect(askProblem(["SOURCE_OF_FUNDS", "SUPPORTING_DOCUMENT"], 14)).toBeNull();
  });
  it("words the errors a reviewer can meet", () => {
    expect(errorText("REVIEWER_ASKED_FOR_IT")).toMatch(/someone else/);
    expect(errorText("NOTHING_TO_REVIEW")).toMatch(/not answered/);
  });
});
