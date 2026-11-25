import { describe, expect, it } from "vitest";

import { checklistHeadline, errorGuidance, formatDuration, scopeChangeState, scopeWords, sdkAction, sdkProblem, sdkStateWords, suiteHeadline, verdictWords } from "./developer-logic";

describe("the first-verification checklist", () => {
  it("is ready only when the partner confirmed a signed webhook", () => {
    expect(checklistHeadline({ ready: true, next_step: null, next_action: null }).tone).toBe("ok");
    const open = checklistHeadline({ ready: false, next_step: "ENDPOINT", next_action: "Register an HTTPS endpoint." });
    expect(open).toEqual({ tone: "info", text: "Register an HTTPS endpoint." });
  });
  it("shows no time for an unfinished install and plain words for a finished one", () => {
    expect(formatDuration(null)).toBe("Not finished yet");
    expect(formatDuration(45)).toBe("45 seconds");
    expect(formatDuration(60)).toBe("1 minute");
    expect(formatDuration(540)).toBe("9 minutes");
    expect(formatDuration(3900)).toBe("1 hour 5 min");
  });
});

describe("the SDK register", () => {
  it("offers only the button a package has earned and explains every problem", () => {
    expect(sdkAction({ action: "VIEW_SOURCE" })).toBe("View the source");
    expect(sdkAction({ action: "DOWNLOAD" })).toBe("Download");
    expect(sdkStateWords({ state: "EXPERIMENTAL", published: false })).toContain("not for production use");
    expect(sdkStateWords({ state: "BETA", published: false })).toBe("Beta (not yet published)");
    expect(sdkProblem("GA_TEST_RUN_IS_STALE")).toContain("90 days");
    expect(sdkProblem("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});

describe("error guidance", () => {
  it("says when it is safe to retry and when the platform does not raise an error yet", () => {
    expect(errorGuidance({ caller_behavior: "Wait.", retryable: true, raised_today: true })).toBe("Wait. (safe to retry after waiting)");
    expect(errorGuidance({ caller_behavior: "Stop.", retryable: false, raised_today: true })).toBe("Stop.");
    expect(errorGuidance({ caller_behavior: "Stop.", retryable: false, raised_today: false })).toContain("does not raise this one yet");
  });
});

describe("scope changes", () => {
  const base = { needs_approval: true, approved_by: [] as string[], pending: false, proposed_by: "a" };
  it("is not in force until someone else approves it, and the proposer cannot", () => {
    expect(scopeChangeState(base, "a")).toEqual({ state: "Waiting for a second person. It is not in force yet.", canApprove: false });
    expect(scopeChangeState(base, "b").canApprove).toBe(true);
    expect(scopeChangeState({ ...base, approved_by: ["a"] }, "b").canApprove).toBe(true); // a self-approval row does not count
    expect(scopeChangeState({ ...base, approved_by: ["b"] }, "c")).toEqual({ state: "In force. A second person approved it.", canApprove: false });
    expect(scopeChangeState({ ...base, needs_approval: false }, "b").state).toBe("In force.");
    expect(scopeChangeState({ ...base, pending: true }, "b").state).toContain("staged");
  });
  it("describes scopes in words and falls back to the id", () => {
    expect(scopeWords("verifications:create")).toBe("Start verification sessions");
    expect(scopeWords("future:scope")).toBe("future:scope");
  });
});

describe("health and the test run", () => {
  it("never calls an untried endpoint healthy and never calls a pass a certification", () => {
    expect(verdictWords("UNKNOWN")).toBe("Nothing delivered yet");
    expect(verdictWords("FAILING")).toBe("Failing");
    expect(suiteHeadline({ status: "NOT_RUN" })).toBe("No scored test run yet.");
    expect(suiteHeadline({ status: "PASSED", personas_passed: 11, personas_total: 11 })).toContain("not a certification");
    expect(suiteHeadline({ status: "NOT_CURRENT" })).toContain("more than 90 days");
  });
});
