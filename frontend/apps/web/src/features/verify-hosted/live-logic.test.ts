import { describe, expect, it } from "vitest";

import { attemptsText, captureProblem, DECLINED_TEXT, EXHAUSTED_TEXT, finishedLive, isLive, nextAfter, noticeLines, RETRY_GUIDANCE, retryText } from "./live-logic";

const file = (name: string) => new File(["x"], name);

describe("the live check on the hosted page", () => {
  it("knows a live view from a sandbox one", () => {
    expect(isLive({ live: true })).toBe(true);
    expect(isLive({ live: false })).toBe(false);
    expect(isLive({})).toBe(false);
  });

  it("gives a retake reason in plain words and never shows an unknown code", () => {
    for (const r of Object.keys(RETRY_GUIDANCE)) expect(retryText(r)).toBe(RETRY_GUIDANCE[r]);
    expect(retryText("SOMETHING_NEW_AND_INTERNAL")).toBe("That did not work. Please take both photographs again.");
    expect(retryText(null)).toBe("That did not work. Please take both photographs again.");
    expect(retryText("toString")).toBe("That did not work. Please take both photographs again."); // an object key is not a reason
  });

  it("counts tries", () => {
    expect(attemptsText(2)).toBe("2 tries left.");
    expect(attemptsText(1)).toBe("1 try left.");
    expect(attemptsText(0)).toBe("No tries left.");
    expect(attemptsText(-3)).toBe("No tries left.");
  });

  it("never claims more than the check does, and never shows the person a result or a score", () => {
    const all = [...noticeLines("Acme Bank"), DECLINED_TEXT, finishedLive("Acme Bank"), EXHAUSTED_TEXT("Acme Bank"), ...Object.values(RETRY_GUIDANCE)].join(" ").toLowerCase();
    for (const banned of ["approved", "verified", "passed", "failed", "fraud", "score", "threshold", "similarity", "biometric", "authentic", "kyc"]) {
      expect(all, banned).not.toContain(banned);
    }
    // it must say what the check is not, in the notice, before anything is photographed
    expect(noticeLines("Acme Bank").join(" ")).toContain("does not test whether a live person");
    expect(noticeLines("Acme Bank").join(" ")).toContain("does not check that the document is genuine");
    expect(noticeLines("Acme Bank")[0]).toContain("Acme Bank");
  });

  it("needs both photographs before anything is sent", () => {
    expect(captureProblem({ document: null, selfie: null })).toContain("document");
    expect(captureProblem({ document: file("d"), selfie: null })).toContain("face");
    expect(captureProblem({ document: file("d"), selfie: file("s") })).toBeNull();
  });

  it("goes to a retake with the reason and the tries left, or to the end", () => {
    expect(nextAfter({ done: false, retry: true, reason: "FACE_NOT_FOUND_OR_UNCLEAR", attempts_left: 2 })).toEqual({ kind: "retry", text: RETRY_GUIDANCE.FACE_NOT_FOUND_OR_UNCLEAR, left: 2 });
    expect(nextAfter({ done: false, attempts_left: -1 })).toMatchObject({ kind: "retry", left: 0 });
    expect(nextAfter({ done: true })).toEqual({ kind: "done", exhausted: false });
    expect(nextAfter({ done: true, closed: true })).toEqual({ kind: "done", exhausted: true });
  });
});
