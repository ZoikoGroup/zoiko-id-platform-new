import { describe, expect, it } from "vitest";

import { ACTION_LABEL, appealProblem, BINDING, exportFileName, exportProblem, explainReason, GRADE, mandateProblem, needsSecondSignatory, nextSteps, problemText } from "./logic";

const ME = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";
const base = { person: OTHER, role: "DIRECTOR", source: "BOARD_MINUTE", powers: ["SIGN_AGREEMENT"], evidence: "board-minute-1", from: "2026-11-01T09:00", to: "2027-01-01T09:00" };

describe("grades", () => {
  it("never calls a grade a verified company", () => {
    for (const g of Object.values(GRADE)) expect(g.meaning.toLowerCase()).not.toContain("verified company");
    expect(Object.keys(GRADE)).toEqual(["K0", "K1", "K2", "K3", "K4"]);
  });
});

describe("reasons", () => {
  it("explains each code and says what to do", () => {
    expect(explainReason("REGISTRY_NO_EXACT_MATCH").next).toMatch(/does not mean the company does not exist/);
    expect(explainReason("REGISTRY_STATUS_NOT_ESTABLISHED").next).toMatch(/never read as a problem/);
    expect(explainReason("SOMETHING_NEW")).toEqual({ text: "SOMETHING_NEW", next: "" });
  });
  it("lists distinct next steps in order and skips empty ones", () => {
    expect(nextSteps(["OWNERSHIP_INCOMPLETE", "OWNERSHIP_INCOMPLETE", "SOMETHING_NEW", "PERSONS_NOT_VERIFIED"])).toEqual([
      explainReason("OWNERSHIP_INCOMPLETE").next,
      explainReason("PERSONS_NOT_VERIFIED").next,
    ]);
    expect(nextSteps([])).toEqual([]);
  });
});

describe("mandate form", () => {
  it("accepts a good one", () => expect(mandateProblem(base, ME)).toBeNull());
  it("refuses what the platform refuses", () => {
    expect(mandateProblem({ ...base, person: "bob" }, ME)).toMatch(/identifier/);
    expect(mandateProblem({ ...base, person: ME.toUpperCase() }, ME)).toMatch(/own mandate/);
    expect(mandateProblem({ ...base, powers: [] }, ME)).toMatch(/at least one act/);
    expect(mandateProblem({ ...base, evidence: "  " }, ME)).toMatch(/document/);
    expect(mandateProblem({ ...base, from: "" }, ME)).toMatch(/starts/);
    expect(mandateProblem({ ...base, to: "2026-10-01T09:00" }, ME)).toMatch(/end after/);
    expect(mandateProblem({ ...base, to: "" }, ME)).toBeNull(); // open-ended is allowed
  });
  it("knows which acts always need a second signatory", () => {
    expect(needsSecondSignatory(["SIGN_AGREEMENT"])).toBe(false);
    expect(needsSecondSignatory(["SIGN_AGREEMENT", "AUTHORIZE_PAYMENT"])).toBe(true);
    for (const act of ["AUTHORIZE_PAYMENT", "TRANSFER_ACCOUNT_OWNERSHIP", "DELEGATE_AUTHORITY"]) expect(needsSecondSignatory([act])).toBe(true);
  });
  it("labels every binding act", () => {
    for (const a of BINDING) expect(ACTION_LABEL[a], a).toBeTruthy();
  });
});

describe("appeal and export", () => {
  it("needs a real reason and a purpose", () => {
    expect(appealProblem("no")).toMatch(/20 characters/);
    expect(appealProblem("The extract we sent is current and shows the same office.")).toBeNull();
    expect(exportProblem("x")).toMatch(/what the export is for/);
    expect(exportProblem("Annual due diligence review")).toBeNull();
    expect(exportProblem("y".repeat(201))).not.toBeNull();
  });
  it("makes a safe file name", () => {
    expect(exportFileName({ export_id: "abc-123" })).toBe("company-verification-abc-123.json");
    expect(exportFileName({ export_id: "../../etc/passwd" })).toBe("company-verification-etcpasswd.json");
    expect(exportFileName({})).toBe("company-verification-export.json");
  });
  it("words refusals and shows unknown codes as themselves", () => {
    expect(problemText("decider_is_a_party")).toMatch(/party/);
    expect(problemText("MANDATE_SELF_RECORDED")).toMatch(/own mandate/);
    expect(problemText("WHO_KNOWS")).toBe("WHO_KNOWS");
  });
});
