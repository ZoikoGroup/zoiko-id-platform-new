import { describe, expect, it } from "vitest";

import { approvalRefusalText, approveLabel, approvedVersusApplied, caseNotice, holdText } from "./approvals";

describe("the words an approver sees", () => {
  it("labels the button by tier and by how many approvals the case already has", () => {
    expect(approveLabel({ tier: "A1", approvals_received: 0, approvals_required: 1 })).toBe("Approve request");
    expect(approveLabel({ tier: "A2", approvals_received: 0, approvals_required: 2 })).toBe("Approve — 1 of 2");
    expect(approveLabel({ tier: "A2", approvals_received: 1, approvals_required: 2 })).toBe("Approve — 2 of 2");
    expect(approveLabel({ tier: "A2", approvals_received: 2, approvals_required: 2 })).toBe("Approve — 2 of 2");
  });

  it("says a first critical approval changed nothing", () => {
    const text = caseNotice({ state: "PARTIALLY_APPROVED", hold: null, approvals_received: 1, approvals_required: 2 });
    expect(text).toBe("1 of 2 independent approvals received. No changes have taken effect.");
    expect(caseNotice({ state: "APPROVED_READY", hold: null, approvals_received: 2, approvals_required: 2 })).toMatch(/until it is applied/);
  });

  it("explains a disabled Approve in the records' words, not a code", () => {
    expect(approvalRefusalText("VOTER_IS_REQUESTER")).toMatch(/submitted or changed this request/);
    expect(approvalRefusalText("VOTER_NO_AUTHORITY")).toMatch(/^Independent approver required/);
    expect(approvalRefusalText("VOTER_ALREADY_VOTED")).toMatch(/One person counts once/);
    expect(approvalRefusalText("SOMETHING_NEW")).toBe("This could not be done.");
    expect(approvalRefusalText(undefined)).toBe("This could not be done.");
  });

  it("holds a case with no independent approver in a stated state and never offers to waive it", () => {
    const held = caseNotice({ state: "PENDING_APPROVAL", hold: "BLOCKED_INSUFFICIENT_APPROVERS", approvals_received: 0, approvals_required: 2 });
    expect(held).toBe(holdText("BLOCKED_INSUFFICIENT_APPROVERS"));
    expect(held).toMatch(/nothing is waived/i);
    expect(holdText("PENDING_INDEPENDENT_REVIEW")).toMatch(/one independent approver/);
    for (const text of [held, holdText("PENDING_INDEPENDENT_REVIEW")]) expect(text.toLowerCase()).not.toMatch(/override|skip|bypass/);
  });

  it("keeps Approved and Applied as two moments", () => {
    expect(approvedVersusApplied({ approved_at: "2026-11-01T10:00:00Z", applied_at: null })).toEqual({ approved: "2026-11-01T10:00:00Z", applied: null });
  });
});
