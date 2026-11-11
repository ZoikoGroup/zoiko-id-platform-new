import { describe, expect, it } from "vitest";

import {
  SESSION_STATES,
  SessionError,
  afterCapture,
  afterSource,
  canMove,
  isTerminal,
  move,
  withdrawConsent,
  type SessionState,
} from "../src/session.js";

describe("verification session state machine (ZID-INT-002 section 05.1)", () => {
  it("follows the documented happy path and reports the documented events", () => {
    const path: SessionState[] = ["NEW", "CONSENT_REQUIRED", "READY", "COLLECTING", "EVIDENCE_RECEIVED", "SOURCE_PENDING", "COMPLETE"];
    const events = path.slice(1).map((to, i) => move(path[i]!, to).event);
    expect(events).toEqual(["session.created", "consent.recorded", "capture.started", "document.submitted", "issuer.pending", "session.completed"]);
  });

  it("allows no capture before consent", () => {
    expect(canMove("NEW", "COLLECTING")).toBe(false);
    expect(canMove("CONSENT_REQUIRED", "COLLECTING")).toBe(false);
    expect(() => move("NEW", "COLLECTING")).toThrow(SessionError);
  });

  it("never lets evidence complete a session directly: OCR is not source authenticity", () => {
    expect(canMove("EVIDENCE_RECEIVED", "COMPLETE")).toBe(false);
    expect(canMove("COLLECTING", "COMPLETE")).toBe(false);
  });

  it("sends a corrupt payload back to retry and good evidence on to the source", () => {
    expect(afterCapture(false)).toBe("RETRY_REQUIRED");
    expect(afterCapture(true)).toBe("EVIDENCE_RECEIVED");
  });

  it("treats an issuer outage as pending, never as a failure", () => {
    expect(afterSource("UNAVAILABLE")).toBe("SOURCE_PENDING");
    expect(SESSION_STATES.some((s) => /FAIL/.test(s))).toBe(false);
    expect(afterSource("CONFIRMED")).toBe("COMPLETE");
    expect(afterSource("UNCLEAR")).toBe("REVIEW_REQUIRED");
    expect(afterSource("TIMED_OUT")).toBe("EXPIRED");
  });

  it("needs a documented authority for a human decision", () => {
    expect(() => move("REVIEW_REQUIRED", "COMPLETE")).toThrow(/authority/);
    expect(() => move("REVIEW_REQUIRED", "REJECTED", { decidedBy: "  " })).toThrow(SessionError);
    expect(move("REVIEW_REQUIRED", "REJECTED", { decidedBy: "reviewer-7" }).to).toBe("REJECTED");
    expect(move("REVIEW_REQUIRED", "APPEALED").to).toBe("APPEALED"); // appeal needs no authority
  });

  it("closes capture when consent is withdrawn, and leaves a finished session alone", () => {
    expect(withdrawConsent("COLLECTING").to).toBe("CANCELED");
    expect(withdrawConsent("READY").event).toBe("session.canceled");
    expect(withdrawConsent("CONSENT_REQUIRED").to).toBe("DECLINED");
    expect(() => withdrawConsent("COMPLETE")).toThrow(SessionError);
  });

  it("has terminal states that go nowhere and no state that is stuck without a way out", () => {
    for (const s of ["COMPLETE", "REJECTED", "EXPIRED", "DECLINED", "CANCELED", "UNAVAILABLE"] as const) {
      expect(isTerminal(s)).toBe(true);
    }
    // every non-terminal state can reach a terminal state
    const reach = (s: SessionState, seen = new Set<SessionState>()): boolean => {
      if (isTerminal(s)) return true;
      if (seen.has(s)) return false;
      seen.add(s);
      return SESSION_STATES.some((t) => canMove(s, t) && reach(t, seen));
    };
    for (const s of SESSION_STATES) expect(reach(s)).toBe(true);
  });
});
