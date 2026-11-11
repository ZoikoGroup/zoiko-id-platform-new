/**
 * The verification session state machine (ZID-INT-002 section 05.1), as a pure function with no I/O.
 *
 * The server is authoritative: this machine only stops a client from showing or doing something the contract forbids.
 *  - nothing is captured before consent (`NEW` cannot reach `COLLECTING`);
 *  - a corrupt payload never advances: it ends in `RETRY_REQUIRED`;
 *  - OCR is not source authenticity: evidence goes to `SOURCE_PENDING` or `REVIEW_REQUIRED`, never straight to `COMPLETE`;
 *  - an issuer outage keeps the session `SOURCE_PENDING`: there is no FAILED state at all;
 *  - a human decision (`REVIEW_REQUIRED` to `COMPLETE` or `REJECTED`) needs a documented authority;
 *  - withdrawing consent closes capture.
 */

export const SESSION_STATES = [
  "NEW",
  "CONSENT_REQUIRED",
  "READY",
  "COLLECTING",
  "RETRY_REQUIRED",
  "EVIDENCE_RECEIVED",
  "SOURCE_PENDING",
  "REVIEW_REQUIRED",
  "APPEALED",
  "COMPLETE",
  "REJECTED",
  "EXPIRED",
  "DECLINED",
  "CANCELED",
  "UNAVAILABLE",
] as const;
export type SessionState = (typeof SESSION_STATES)[number];

const NEXT: Record<SessionState, readonly SessionState[]> = {
  NEW: ["CONSENT_REQUIRED", "UNAVAILABLE"],
  CONSENT_REQUIRED: ["READY", "DECLINED"],
  READY: ["COLLECTING", "CANCELED"],
  COLLECTING: ["EVIDENCE_RECEIVED", "RETRY_REQUIRED", "CANCELED"],
  RETRY_REQUIRED: ["COLLECTING", "CANCELED"],
  EVIDENCE_RECEIVED: ["SOURCE_PENDING", "REVIEW_REQUIRED"],
  SOURCE_PENDING: ["COMPLETE", "REVIEW_REQUIRED", "EXPIRED"],
  REVIEW_REQUIRED: ["COMPLETE", "APPEALED", "REJECTED"],
  APPEALED: ["REVIEW_REQUIRED"],
  COMPLETE: [],
  REJECTED: [],
  EXPIRED: [],
  DECLINED: [],
  CANCELED: [],
  UNAVAILABLE: [],
};

export const TERMINAL_STATES: readonly SessionState[] = SESSION_STATES.filter((s) => NEXT[s].length === 0);

/** Events the session reports to the host page (section 05). */
export type SessionEvent =
  | "session.created"
  | "consent.recorded"
  | "capture.started"
  | "document.submitted"
  | "issuer.pending"
  | "review.required"
  | "session.completed"
  | "session.canceled";

const EVENT_FOR: Partial<Record<SessionState, SessionEvent>> = {
  CONSENT_REQUIRED: "session.created",
  READY: "consent.recorded",
  COLLECTING: "capture.started",
  EVIDENCE_RECEIVED: "document.submitted",
  SOURCE_PENDING: "issuer.pending",
  REVIEW_REQUIRED: "review.required",
  COMPLETE: "session.completed",
  CANCELED: "session.canceled",
};

export class SessionError extends Error {
  constructor(
    readonly code: "TRANSITION_NOT_ALLOWED" | "AUTHORITY_REQUIRED",
    message: string,
  ) {
    super(message);
    this.name = "SessionError";
  }
}

export type Transition = { from: SessionState; to: SessionState; event: SessionEvent | null };

export function canMove(from: SessionState, to: SessionState): boolean {
  return NEXT[from].includes(to);
}

export function isTerminal(state: SessionState): boolean {
  return NEXT[state].length === 0;
}

/**
 * Move to `to`. A human decision at `REVIEW_REQUIRED` (to `COMPLETE` or `REJECTED`) must name the authority that made it;
 * without one it is refused, so a person cannot be silently approved or rejected by the client.
 */
export function move(from: SessionState, to: SessionState, opts: { decidedBy?: string } = {}): Transition {
  if (!canMove(from, to)) {
    throw new SessionError("TRANSITION_NOT_ALLOWED", `${from} cannot move to ${to}`);
  }
  if (from === "REVIEW_REQUIRED" && (to === "COMPLETE" || to === "REJECTED") && !opts.decidedBy?.trim()) {
    throw new SessionError("AUTHORITY_REQUIRED", "a review decision needs a documented authority");
  }
  return { from, to, event: EVENT_FOR[to] ?? null };
}

/** Consent withdrawn: capture closes where it is open; a finished session is untouched. */
export function withdrawConsent(state: SessionState): Transition {
  if (state === "READY" || state === "COLLECTING" || state === "RETRY_REQUIRED") return move(state, "CANCELED");
  if (state === "CONSENT_REQUIRED") return move(state, "DECLINED");
  throw new SessionError("TRANSITION_NOT_ALLOWED", `consent cannot be withdrawn in ${state}`);
}

/** Where captured evidence goes: a corrupt payload retries; good evidence waits for the source or a person, never straight to done. */
export function afterCapture(payloadIntact: boolean): SessionState {
  return payloadIntact ? "EVIDENCE_RECEIVED" : "RETRY_REQUIRED";
}

/** What the source answered. An outage is not a failure: the session waits (or goes to a person once it has waited long enough). */
export function afterSource(answer: "CONFIRMED" | "UNAVAILABLE" | "UNCLEAR" | "TIMED_OUT"): SessionState {
  switch (answer) {
    case "CONFIRMED":
      return "COMPLETE";
    case "UNCLEAR":
      return "REVIEW_REQUIRED";
    case "TIMED_OUT":
      return "EXPIRED";
    default:
      return "SOURCE_PENDING"; // UNAVAILABLE: pending, never FAILED
  }
}
