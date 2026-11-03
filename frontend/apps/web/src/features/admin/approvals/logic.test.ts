import type { ApprovalCase } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import {
  VOTE_STEP_UP_ACTION,
  actionWords,
  canCancel,
  canExecute,
  canVote,
  emptyReviewerForm,
  executionKey,
  parseConflicts,
  payloadRows,
  reviewerFormErrors,
  stateTone,
  toAppointment,
  voteDraft,
} from "./logic";

const base = { case_id: "c1", request_hash: "h".repeat(64), tier: "A2", state: "PENDING_APPROVAL" } as unknown as ApprovalCase;
const U = "0f8fad5b-d9cb-469f-a165-70867728950e";

describe("the vote's step-up draft", () => {
  it("binds the proof to this case, this hash and this vote, critical for A2", () => {
    expect(voteDraft(base, "APPROVE")).toEqual({
      action: VOTE_STEP_UP_ACTION,
      resource: "approval-case:c1",
      change: { case_id: "c1", request_hash: "h".repeat(64), vote: "APPROVE" },
      critical: true,
    });
    expect(voteDraft({ ...base, tier: "A1" }, "REJECT").critical).toBe(false);
    expect(voteDraft(base, "REJECT").change.vote).toBe("REJECT");
  });
});

describe("what a person may do", () => {
  it("lets only an eligible person vote on an open case, and only the requester cancel", () => {
    const you = (may_vote: boolean, is_requester = false) => ({ ...base, you: { may_vote, reasons: [], is_requester } }) as ApprovalCase;
    expect(canVote(you(true))).toBe(true);
    expect(canVote(you(false))).toBe(false);
    expect(canVote({ ...you(true), state: "APPROVED_READY" })).toBe(false);
    expect(canCancel(you(false, true))).toBe(true);
    expect(canCancel(you(true, false))).toBe(false);
    expect(canCancel({ ...you(false, true), state: "EXECUTED" })).toBe(false);
  });

  it("runs only an approved or failed case", () => {
    expect(canExecute({ state: "APPROVED_READY" })).toBe(true);
    expect(canExecute({ state: "FAILED" })).toBe(true);
    for (const s of ["PENDING_APPROVAL", "PARTIALLY_APPROVED", "EXECUTED", "EXPIRED", "REJECTED"] as const) expect(canExecute({ state: s })).toBe(false);
  });

  it("keys each run separately and stably", () => {
    expect(executionKey("c1", "a")).toBe("exec-c1-a");
    expect(executionKey("c1", "b")).not.toBe(executionKey("c1", "a"));
    expect(executionKey("x".repeat(200), "a").length).toBe(120);
  });

  it("colours states by outcome", () => {
    expect(stateTone("EXECUTED")).toBe("ok");
    expect(stateTone("FAILED")).toBe("danger");
    expect(stateTone("PARTIALLY_APPROVED")).toBe("warn");
  });
});

describe("the request as the approver reads it", () => {
  it("turns the payload into sorted label and value rows", () => {
    expect(payloadRows({ role: "ADMIN", principal_id: "p" })).toEqual([
      { label: "principal id", value: "p" },
      { label: "role", value: "ADMIN" },
    ]);
    expect(actionWords("ownership-transfer-or-replacement")).toBe("Add an Owner");
    expect(actionWords("some-new-action")).toBe("some new action");
  });
});

describe("appointing a reviewer", () => {
  it("asks for every piece of evidence the record names", () => {
    expect(Object.keys(reviewerFormErrors(emptyReviewerForm)).sort()).toEqual(["basis", "declaration", "principal", "relationship"]);
    expect(reviewerFormErrors({ principal: U, relationship: "TRUSTEE", basis: "Deed", declaration: "None", conflicts: "" })).toEqual({});
  });

  it("reads declared conflicts as account ids and flags anything else", () => {
    expect(parseConflicts(`${U}, ${U}\nnot-an-id`)).toEqual({ ids: [U], invalid: ["not-an-id"] });
    expect(reviewerFormErrors({ principal: U, relationship: "TRUSTEE", basis: "b", declaration: "d", conflicts: "oops" }).conflicts).toBeTruthy();
  });

  it("builds the request body", () => {
    expect(toAppointment({ principal: ` ${U} `, relationship: "CO_DIRECTOR", basis: " b ", declaration: " d ", conflicts: U })).toEqual({
      principal_id: U,
      relationship: "CO_DIRECTOR",
      authority_basis: "b",
      independence_declaration: "d",
      declared_conflicts: [U],
    });
  });
});
