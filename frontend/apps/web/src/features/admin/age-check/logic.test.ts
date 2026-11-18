import { describe, expect, it } from "vitest";

import { EMPTY, formProblem, METHOD, reasonWords, RESULT, THRESHOLDS } from "./logic";

const TODAY = new Date("2026-11-18T12:00:00Z");

describe("the age check form", () => {
  it("offers only the ages a policy defines", () => {
    expect(THRESHOLDS).toEqual([13, 16, 18, 21, 25, 65]);
    expect(formProblem({ threshold: 17, birthDate: "2000-01-01" }, TODAY)).toMatch(/listed ages/);
    expect(formProblem({ ...EMPTY, birthDate: "2000-01-01" }, TODAY)).toBeNull();
  });
  it("refuses a birth date that is not a real, past, sensible date", () => {
    for (const bad of ["", "01/02/2000", "2000-02-30", "2027-01-01", "1890-01-01", "2000-13-01"]) {
      expect(formProblem({ ...EMPTY, birthDate: bad }, TODAY)).not.toBeNull();
    }
    expect(formProblem({ ...EMPTY, birthDate: "2026-11-18" }, TODAY)).toBeNull(); // born today
  });
});

describe("what the answer means", () => {
  it("never reads an undetermined answer as a pass or a refusal", () => {
    expect(RESULT.UNDETERMINED.tone).toBe("warn");
    expect(RESULT.UNDETERMINED.meaning).toMatch(/neither a pass nor a refusal/);
    expect(RESULT.AT_LEAST.tone).toBe("ok");
  });
  it("says plainly that a typed birth date is not verified", () => {
    expect(METHOD.SELF_DECLARED).toMatch(/typed/);
    expect(reasonWords("SELF_DECLARED_NOT_VERIFIED")).toMatch(/never show/);
    expect(reasonWords("SOMETHING_NEW")).toBe("something new");
  });
});
