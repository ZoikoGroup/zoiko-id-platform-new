import { ApiError } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import {
  TIER_COPY,
  clock,
  isActionBound,
  refusalText,
  secondsLeft,
  stepUpNeed,
  whyRequired,
} from "./step-up";

const refused = (
  details: { field?: string; issue: string }[],
  code = "STEP_UP_REQUIRED",
) =>
  new ApiError({
    status: 403,
    code,
    message: "A fresh strong authentication is required.",
    details,
  });

describe("telling a command's own proof from a session step-up", () => {
  it("reads the tier, action and reason the server attaches", () => {
    const e = refused([
      { field: "tier", issue: "S2" },
      {
        field: "action",
        issue: "initiate-confirm-transfer-of-organization-ownership",
      },
      { field: "reason", issue: "STEP_UP_PROOF_CONSUMED" },
    ]);
    expect(stepUpNeed(e)).toEqual({
      tier: "S2",
      action: "initiate-confirm-transfer-of-organization-ownership",
      reason: "STEP_UP_PROOF_CONSUMED",
    });
    expect(isActionBound(e)).toBe(true);
  });

  it("does not mistake a plain session step-up, another error or a non-error for one", () => {
    expect(isActionBound(refused([]))).toBe(false);
    expect(
      isActionBound(
        refused([
          { field: "tier", issue: "S0" },
          { field: "action", issue: "x" },
        ]),
      ),
    ).toBe(false);
    expect(
      isActionBound(
        refused(
          [
            { field: "tier", issue: "S2" },
            { field: "action", issue: "x" },
          ],
          "FORBIDDEN",
        ),
      ),
    ).toBe(false);
    expect(stepUpNeed(new Error("boom"))).toBeNull();
    expect(stepUpNeed(null)).toBeNull();
  });
});

describe("the countdown", () => {
  it("rounds up, never goes below zero and survives a bad date", () => {
    const now = Date.parse("2026-11-02T12:00:00Z");
    expect(secondsLeft("2026-11-02T12:04:59.200Z", now)).toBe(300);
    expect(secondsLeft("2026-11-02T12:00:30Z", now)).toBe(30);
    expect(secondsLeft("2026-11-02T11:59:00Z", now)).toBe(0);
    expect(secondsLeft("not a date", now)).toBe(0);
    expect(clock(300)).toBe("5:00");
    expect(clock(65)).toBe("1:05");
    expect(clock(0)).toBe("0:00");
  });
});

describe("what the person is told", () => {
  it("says a critical change is approved once for exactly what is shown, and a sensitive one is recent", () => {
    expect(TIER_COPY.S2.body).toMatch(/once/);
    expect(TIER_COPY.S2.body).toMatch(/details shown/);
    expect(TIER_COPY.S1.body).toMatch(/ten minutes/);
    expect(whyRequired("S2", "Verify both principals.")).toMatch(
      /exactly what you see\. Verify both principals\.$/,
    );
    expect(whyRequired("S1", undefined)).toBe(
      "Sensitive changes need a recent passkey check.",
    );
  });

  it("never suggests email or a text message can stand in, and explains each refusal plainly", () => {
    const none = refusalText("NO_STRONG_AUTHENTICATOR");
    expect(none).toMatch(/account recovery/i);
    expect(none).toMatch(/email and text messages cannot/i);
    expect(refusalText("STEP_UP_PAYLOAD_MISMATCH")).toMatch(/details changed/i);
    expect(refusalText("STEP_UP_PROOF_CONSUMED")).toMatch(/already used/i);
    expect(refusalText("STEP_UP_HUMAN_REQUIRED")).toMatch(/agent/i);
    expect(refusalText("SOMETHING_NEW")).toBe(
      "The confirmation could not be completed.",
    );
  });
});
