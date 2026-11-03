import type { OrgClaimStatus } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import {
  APPEAL_LABELS,
  CHECKS,
  CHECK_TONE,
  CHECK_WORDS,
  MANDATE_KINDS,
  MAX_PROOF_BYTES,
  RELATIONSHIPS,
  PROOF_KINDS,
  appealKinds,
  appealMessage,
  attemptId,
  buildScreening,
  claimKey,
  emptyOwner,
  mandateProblem,
  nextStep,
  outcomeCopy,
  ownerRowErrors,
  pctValid,
  proofKey,
  proofMessage,
  proofProblem,
  sizeLabel,
  statementProblem,
  tidy,
} from "./org-claim";

const base: OrgClaimStatus = {
  claim_id: "c1",
  jurisdiction: "GB",
  registration_number: "00123456",
  state: "DRAFT",
  outcome: "PROVISIONAL",
  relationship: null,
  submitted_at: null,
  closed_at: null,
  checks: { company: "COMPLETED", person_assurance: "PENDING", authority: "PENDING", risk: "PENDING" },
  company: { status: "VERIFIED", review_reason: null, reasons: [], expires_at: null },
  screening: null,
};

describe("the four checks stay separate", () => {
  it("lists company, identity, authority and risk, each with its own help", () => {
    expect(CHECKS.map((c) => c.key)).toEqual(["company", "person_assurance", "authority", "risk"]);
    for (const c of CHECKS) expect(c.help.length).toBeGreaterThan(10);
  });
});

describe("outcome wording", () => {
  it("never calls a provisional claim verified or promises rights from it", () => {
    const p = outcomeCopy("PROVISIONAL");
    expect(p.title).toBe("Provisional");
    expect(p.body).toMatch(/not a verified organization/);
    expect(p.body).toMatch(/no owner or approver rights/);
  });
  it("names every outcome and promises no completion time", () => {
    for (const o of ["VERIFIED", "PROVISIONAL", "NEEDS_EVIDENCE", "IN_REVIEW", "RESTRICTED", "DECLINED", "STALE", "WITHDRAWN", "ABANDONED"] as const) {
      const c = outcomeCopy(o);
      expect(c.title).toBeTruthy();
      expect(c.body).not.toMatch(/\b\d+\s*(hours?|days?|minutes?)\b/i);
    }
  });
});

describe("holdings and owners", () => {
  it("accepts only percentages from 0 to 100", () => {
    for (const ok of ["0", "25", "12.5", "100", "33.3333"]) expect(pctValid(ok)).toBe(true);
    for (const bad of ["", "-1", "101", "abc", "1e2", "12.56789", "1,5"]) expect(pctValid(bad)).toBe(false);
  });
  it("needs a name and some stated interest, and a plausible birth year", () => {
    expect(ownerRowErrors(emptyOwner()).length).toBeGreaterThan(0);
    expect(ownerRowErrors({ ...emptyOwner(), name: "Olivia Owner", sharesPct: "60" })).toEqual([]);
    expect(ownerRowErrors({ ...emptyOwner(), name: "Olivia Owner", boardControl: true })).toEqual([]);
    expect(ownerRowErrors({ ...emptyOwner(), name: "Olivia Owner", sharesPct: "60", birthYear: "85" })).toEqual(["Enter a four-digit birth year."]);
    expect(ownerRowErrors({ ...emptyOwner(), name: "Olivia Owner", sharesPct: "160" })).toEqual(["Shares must be between 0 and 100."]);
  });
  it("tidies names and builds the request from what was typed", () => {
    expect(tidy("  Olivia   Owner ")).toBe("Olivia Owner");
    const body = buildScreening("  Alice  Smith", [
      { name: "Olivia Owner", birthYear: "1980", sharesPct: "60", votesPct: "", boardControl: false },
      { name: "Bo Board", birthYear: "", sharesPct: "", votesPct: "", boardControl: true },
    ]);
    expect(body.representative).toMatchObject({ ref: "rep", name: "Alice Smith", kind: "PERSON" });
    expect(body.owners?.map((o) => [o.ref, o.name, o.birth])).toEqual([
      ["owner1", "Olivia Owner", "1980"],
      ["owner2", "Bo Board", null],
    ]);
    expect(body.holdings).toEqual([
      { owner: "owner1", shares_pct: "60", votes_pct: null, controls: [] },
      { owner: "owner2", shares_pct: null, votes_pct: null, controls: ["APPOINT_BOARD_MAJORITY"] },
    ]);
  });
});

describe("keys and next steps", () => {
  it("makes a key per company and attempt, within the server's limit", () => {
    expect(claimKey("GB", "00123456", "a1")).toBe("claim-GB-00123456-a1");
    expect(claimKey("DE", "HRB 12345".repeat(30), "a1").length).toBeLessThanOrEqual(120);
  });
  it("gives each opening of the page its own attempt id", () => {
    expect(attemptId()).toMatch(/^[0-9a-z-]{6,8}$/);
    expect(attemptId()).not.toBe(attemptId());
  });
  it("asks for the authority details only once the company is found, then waits, then is done", () => {
    expect(nextStep(base)).toBe("authority");
    expect(nextStep({ ...base, checks: { ...base.checks, company: "PENDING" } })).toBe("wait");
    const declared = { ...base, screening: { status: "REVIEW_REQUIRED", next_action: "x", expires_at: null, tasks: [] } };
    expect(nextStep(declared)).toBe("submit"); // whom to screen is said, and it has not been sent
    expect(nextStep({ ...declared, state: "SUBMITTED", submitted_at: "2026-11-03T10:00:00Z" })).toBe("wait");
    expect(nextStep({ ...base, outcome: "VERIFIED" })).toBe("done");
  });
});

describe("supporting documents", () => {
  const f = (over: Partial<{ name: string; size: number; type: string }> = {}) => ({ name: "certificate.pdf", size: 1024, type: "application/pdf", ...over });

  it("flags what the browser can see before anything is sent", () => {
    expect(proofProblem(f())).toBeNull();
    expect(proofProblem(f({ type: "image/png" }))).toBeNull();
    expect(proofProblem(f({ size: 0 }))).toMatch(/empty/i);
    expect(proofProblem(f({ size: MAX_PROOF_BYTES + 1 }))).toMatch(/10 MB/);
    expect(proofProblem(f({ type: "application/x-msdownload", name: "a.pdf" }))).toMatch(/PDF, PNG or JPEG/);
    expect(proofProblem(f({ type: "" }))).toBeNull(); // an unknown type is left for the server, which reads the bytes
  });

  it("says every server refusal in plain words and never shows a raw code", () => {
    for (const code of ["PROOF_TOO_LARGE", "PROOF_EMPTY", "PROOF_TYPE_NOT_ALLOWED", "PROOF_TYPE_MISMATCH", "PROOF_ACTIVE_CONTENT", "PROOF_MALFORMED", "PROOF_INFECTED", "PROOF_SCAN_UNAVAILABLE", "PROOF_RESIDENCY_DENIED", "PROOF_LIMIT_REACHED"]) {
      expect(proofMessage(code)).not.toMatch(/PROOF_/);
      expect(proofMessage(code).length).toBeGreaterThan(10);
    }
    expect(proofMessage("SOMETHING_NEW")).toBe("That document could not be added.");
    expect(proofMessage(undefined)).toBe("That document could not be added.");
  });

  it("keeps one key per file choice and names every kind", () => {
    const a = proofKey("0190abcd-1111", f(), "x1");
    expect(a).toBe(proofKey("0190abcd-1111", f(), "x1"));
    expect(a).not.toBe(proofKey("0190abcd-1111", f(), "x2"));
    expect(a).not.toBe(proofKey("0190abcd-1111", f({ size: 2048 }), "x1"));
    expect(a.length).toBeLessThanOrEqual(120);
    expect(PROOF_KINDS.map((k) => k.id)).toContain("OTHER");
    expect(PROOF_KINDS.every((k) => k.label.length > 3)).toBe(true);
  });

  it("writes sizes people can read", () => {
    expect(sizeLabel(10)).toBe("1 KB");
    expect(sizeLabel(2048)).toBe("2 KB");
    expect(sizeLabel(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});

describe("appeals and corrections", () => {
  it("offers the right request for each state and nothing when the checks are complete or unavailable", () => {
    expect(appealKinds("REJECTED")).toEqual(["APPEAL", "CORRECTION"]);
    expect(appealKinds("GATHERING")).toEqual(["CORRECTION"]);
    expect(appealKinds("REVIEW_REQUIRED")).toEqual(["CORRECTION"]);
    for (const s of ["VERIFIED", "UNAVAILABLE", "EXPIRED", "SUSPENDED", undefined]) expect(appealKinds(s)).toEqual([]);
    expect(Object.keys(APPEAL_LABELS).sort()).toEqual(["APPEAL", "CORRECTION"]);
  });

  it("asks for at least a sentence and no more than the limit before sending", () => {
    expect(statementProblem("")).toMatch(/at least a sentence/i);
    expect(statementProblem("too short")).toMatch(/at least a sentence/i);
    expect(statementProblem("x".repeat(19))).not.toBeNull();
    expect(statementProblem("x".repeat(20))).toBeNull();
    expect(statementProblem("   " + "x".repeat(19) + "   ")).not.toBeNull(); // spaces do not count
    expect(statementProblem("x".repeat(2000))).toBeNull();
    expect(statementProblem("x".repeat(2001))).toMatch(/under 2000/);
  });

  it("says every server refusal in plain words and never shows a raw code", () => {
    for (const code of ["STATEMENT_INVALID", "APPEAL_NOT_AVAILABLE", "APPEAL_ALREADY_OPEN", "AMEND_NOT_AVAILABLE", "SCREENING_RESIDENCY_DENIED"]) {
      expect(appealMessage(code)).not.toMatch(/[A-Z]{4,}_[A-Z]/);
      expect(appealMessage(code).length).toBeGreaterThan(15);
    }
    expect(appealMessage("SOMETHING_NEW")).toBe("That request could not be sent.");
    expect(appealMessage(undefined)).toBe("That request could not be sent.");
  });
});

describe("states a claim can be in", () => {
  it("says out of date and restricted in their own words, never as verified or merely pending", () => {
    expect(CHECK_WORDS.STALE).toBe("Out of date");
    expect(CHECK_WORDS.RESTRICTED).toBe("Restricted");
    expect(CHECK_TONE.STALE).toBe("warn");
    expect(CHECK_TONE.RESTRICTED).toBe("danger");
    expect(outcomeCopy("STALE").title).toBe("Out of date");
    expect(outcomeCopy("RESTRICTED").body).toMatch(/paused/);
  });
  it("tells a withdrawn or closed claim apart, with a way to start again and nothing promised about the past", () => {
    for (const o of ["WITHDRAWN", "ABANDONED"] as const) {
      expect(outcomeCopy(o).body).toMatch(/Nothing was deleted/);
      expect(outcomeCopy(o).body).toMatch(/start a new claim/);
    }
  });
  it("offers submitting once whom to screen is said, and nothing once a claim is closed or verified", () => {
    const declared = { ...base, screening: { status: "SCREENING", next_action: "", expires_at: null, tasks: [] } };
    expect(nextStep(base)).toBe("authority");
    expect(nextStep(declared)).toBe("submit");
    expect(nextStep({ ...declared, state: "SUBMITTED", submitted_at: "2026-11-03T10:00:00Z" })).toBe("wait");
    expect(nextStep({ ...declared, state: "WITHDRAWN", outcome: "WITHDRAWN", closed_at: "2026-11-03T10:00:00Z" })).toBe("done");
    expect(nextStep({ ...declared, outcome: "VERIFIED" })).toBe("done");
  });
});

describe("how the person acts for the company", () => {
  const today = new Date("2026-11-03T12:00:00");
  it("offers the three relationships of the design, and only an officer needs no document", () => {
    expect(RELATIONSHIPS.map((r) => r.id)).toEqual(["DIRECTOR_OR_OFFICER", "DELEGATED_EMPLOYEE", "EXTERNAL_REPRESENTATIVE"]);
    expect(MANDATE_KINDS).toEqual(["LETTER_OF_AUTHORITY", "BOARD_RESOLUTION", "SIGNING_MATRIX"]);
    expect(PROOF_KINDS.some((k) => k.id === "SIGNING_MATRIX")).toBe(true);
    expect(mandateProblem("DIRECTOR_OR_OFFICER", "", "", today)).toBeNull();
  });
  it("asks a delegate for the document and a future end date before sending", () => {
    expect(mandateProblem("DELEGATED_EMPLOYEE", "", "2027-01-01", today)).toMatch(/Choose the document/);
    expect(mandateProblem("EXTERNAL_REPRESENTATIVE", "p1", "", today)).toMatch(/Enter the date/);
    expect(mandateProblem("DELEGATED_EMPLOYEE", "p1", "2026-11-02", today)).toMatch(/future/);
    expect(mandateProblem("DELEGATED_EMPLOYEE", "p1", "2027-01-01", today)).toBeNull();
  });
  it("sends the mandate only for a delegate, and never the officer's", () => {
    const owners = [{ ...emptyOwner(), name: "Olivia Owner", sharesPct: "60" }];
    const officer = buildScreening("Alice Smith", owners, { relationship: "DIRECTOR_OR_OFFICER", proofId: "ignored", expires: "2027-01-01" });
    expect(officer.relationship).toBe("DIRECTOR_OR_OFFICER");
    expect(officer.mandate_proof_id).toBeUndefined();
    const delegate = buildScreening("Alice Smith", owners, { relationship: "DELEGATED_EMPLOYEE", proofId: "p1", expires: "2027-01-01" });
    expect(delegate.mandate_proof_id).toBe("p1");
    expect(delegate.mandate_expires_at).toMatch(/^2027-01-01T/);
    expect(buildScreening("Alice Smith", owners).relationship).toBe("DIRECTOR_OR_OFFICER"); // the default is the register
  });
});
