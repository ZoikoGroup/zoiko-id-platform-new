import { describe, expect, it } from "vitest";

import { canVerify, challengeText, checklistDone, clock, findingsComplete, iHold, listQuery, myPart, revealReady, secondsLeft, visibleCases } from "./review-logic";

const lease = (reviewer: string) => ({ reviewer, expires_at: "2026-11-25T10:15:00Z" });

describe("Review logic", () => {
  const c = { required_checks: ["face_match"], findings: {} as Record<string, "pass" | "fail" | "inconclusive"> };
  it("offers Verified only when every required check is confirmed", () => {
    expect(canVerify(c)).toBe(false);
    expect(canVerify({ ...c, findings: { face_match: "inconclusive" } })).toBe(false);
    expect(canVerify({ ...c, findings: { face_match: "pass" } })).toBe(true);
    expect(findingsComplete({ ...c, findings: { face_match: "fail" } })).toBe(true);
  });
  it("says who may act, by who is signed in", () => {
    expect(myPart({ status: "QUEUED", lease: null }, "a")).toBe("claim");
    expect(myPart({ status: "ESCALATED", lease: null }, "a")).toBe("claim");
    expect(myPart({ status: "AWAITING_APPLICANT", lease: null }, "a")).toBe("applicant");
    expect(myPart({ status: "AWAITING_SECOND_REVIEW", lease: null }, "a")).toBe("second");
    expect(myPart({ status: "INVESTIGATING", lease: lease("a") }, "a")).toBe("work");
    expect(myPart({ status: "INVESTIGATING", lease: lease("b") }, "a")).toBe("watch");
    expect(myPart({ status: "INVESTIGATING", lease: lease("a") }, null)).toBe("watch");
    expect(myPart({ status: "APPROVED_FINAL", lease: null }, "a")).toBe("done");
  });
  it("tells a case I hold from one someone else holds", () => {
    expect(iHold({ lease: lease("a") }, "a")).toBe(true);
    expect(iHold({ lease: lease("b") }, "a")).toBe(false);
    expect(iHold({ lease: null }, "a")).toBe(false);
  });
  it("needs a real reason to reveal", () => {
    expect(revealReady("no")).toBe(false);
    expect(revealReady("Need the request context")).toBe(true);
  });
  it("counts a reveal down to zero and shows it as a clock", () => {
    const end = "2026-11-25T10:05:00Z";
    expect(secondsLeft(end, Date.parse("2026-11-25T10:00:00Z"))).toBe(300);
    expect(secondsLeft(end, Date.parse("2026-11-25T10:09:00Z"))).toBe(0);
    expect(clock(300)).toBe("5:00");
    expect(clock(59)).toBe("0:59");
  });
  it("lets the second reviewer approve only with every point of the checklist ticked", () => {
    const list = ["evidence_reviewed", "reason_matches_evidence"];
    expect(checklistDone(list, {})).toBe(false);
    expect(checklistDone(list, { evidence_reviewed: true })).toBe(false);
    expect(checklistDone(list, { evidence_reviewed: true, reason_matches_evidence: false })).toBe(false);
    expect(checklistDone(list, { evidence_reviewed: true, reason_matches_evidence: true })).toBe(true);
    expect(checklistDone([], {})).toBe(false);
    expect(challengeText("evidence_reviewed")).toContain("evidence");
    expect(challengeText("something_new")).toBe("something new");
  });
  it("filters by state and queue at the cell and by 'mine' on the screen", () => {
    expect(listQuery({ status: "", queue: "", mine: false })).toEqual({});
    expect(listQuery({ status: "QUEUED", queue: "SPECIALIST", mine: true })).toEqual({ status: "QUEUED", queue: "SPECIALIST" });
    const rows = [{ case_id: "1", lease: lease("a") }, { case_id: "2", lease: lease("b") }, { case_id: "3", lease: null }] as never[];
    expect(visibleCases(rows, { status: "", queue: "", mine: false }, "a")).toHaveLength(3);
    expect(visibleCases(rows, { status: "", queue: "", mine: true }, "a")).toHaveLength(1);
  });
});
