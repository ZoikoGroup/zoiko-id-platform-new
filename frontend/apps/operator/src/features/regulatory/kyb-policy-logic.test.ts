import { describe, expect, it } from "vitest";

import { groupProblems, isDigest, parsePack, problemText, rollbackProblem, rolesStillNeeded, SIGN_OFF_ROLES, shortHash } from "./kyb-policy-logic";

describe("problemText", () => {
  it("explains fixed and patterned codes", () => {
    expect(problemText("UBO_THRESHOLD_NOT_SIGNED")).toMatch(/Compliance owner/);
    expect(problemText("SIGN_OFF_LEGAL_MISSING")).toBe("No legal sign-off yet.");
    expect(problemText("SOURCE_COMPANIES_HOUSE_CREDENTIAL_MISSING")).toBe("The source “companies house” has no credential in the key store.");
    expect(problemText("SOURCE_INPI_RNE_LEGAL_BASIS_MISSING")).toMatch(/lawful basis/);
    expect(problemText("RETENTION_RAW_DOCUMENTS_MISSING")).toBe("No retention period for raw documents.");
    expect(problemText("RECHECK_K2_ABOVE_CEILING_NOT_RATIFIED")).toMatch(/not ratified/);
    expect(problemText("RECHECK_K1_MISSING")).toBe("No recheck period for grade K1.");
  });
  it("shows an unknown code as itself", () => expect(problemText("SOMETHING_NEW")).toBe("SOMETHING_NEW"));
});

describe("sign-offs", () => {
  it("separates sign-off gaps from the rest", () => {
    expect(groupProblems(["SIGN_OFF_LEGAL_MISSING", "DELETION_JOB_MISSING", "SIGN_OFF_PRIVACY_MISSING"])).toEqual({ signOffs: ["SIGN_OFF_LEGAL_MISSING", "SIGN_OFF_PRIVACY_MISSING"], other: ["DELETION_JOB_MISSING"] });
  });
  it("lists the roles still needed", () => {
    expect(rolesStillNeeded([])).toEqual([...SIGN_OFF_ROLES]);
    expect(rolesStillNeeded(["LEGAL", "SECURITY"])).toEqual(["COMPLIANCE", "PRIVACY"]);
    expect(rolesStillNeeded([...SIGN_OFF_ROLES])).toEqual([]);
  });
});

describe("inputs", () => {
  it("recognises a digest", () => {
    expect(isDigest(`sha256:${"a".repeat(64)}`)).toBe(true);
    for (const bad of ["", "sha256:abc", `sha256:${"A".repeat(64)}`, `md5:${"a".repeat(64)}`]) expect(isDigest(bad), bad).toBe(false);
    expect(shortHash(`sha256:${"ab12".repeat(16)}`)).toBe("ab12ab12ab12");
  });
  it("reads a pack or says why not", () => {
    expect(parsePack('{"jurisdiction":"GB"}')).toEqual({ ok: true, pack: { jurisdiction: "GB" } });
    expect(parsePack("not json")).toEqual({ ok: false, reason: "That is not valid JSON." });
    expect(parsePack("[1]").ok).toBe(false);
    expect(parsePack("null").ok).toBe(false);
  });
  it("needs a reason to roll back", () => {
    expect(rollbackProblem("no")).toMatch(/ten characters/);
    expect(rollbackProblem("The lower threshold was not agreed.")).toBeNull();
  });
});
