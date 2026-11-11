package com.zoiko.id;

import java.util.List;
import java.util.Map;

/**
 * The verification session state machine (ZID-INT-002 section 05.1): a pure mirror of the TypeScript, Python and Go ones. The server is
 * authoritative; this only stops a client doing or showing what the contract forbids: nothing is captured before consent, a corrupt payload
 * never advances, an issuer outage keeps the session SOURCE_PENDING (there is no FAILED state), a human decision needs a documented
 * authority, and withdrawing consent closes capture.
 */
public final class Session {
    private Session() {}

    public static final List<String> STATES = List.of("NEW", "CONSENT_REQUIRED", "READY", "COLLECTING", "RETRY_REQUIRED",
            "EVIDENCE_RECEIVED", "SOURCE_PENDING", "REVIEW_REQUIRED", "APPEALED", "COMPLETE", "REJECTED", "EXPIRED", "DECLINED",
            "CANCELED", "UNAVAILABLE");

    private static final Map<String, List<String>> NEXT = Map.ofEntries(
            Map.entry("NEW", List.of("CONSENT_REQUIRED", "UNAVAILABLE")),
            Map.entry("CONSENT_REQUIRED", List.of("READY", "DECLINED")),
            Map.entry("READY", List.of("COLLECTING", "CANCELED")),
            Map.entry("COLLECTING", List.of("EVIDENCE_RECEIVED", "RETRY_REQUIRED", "CANCELED")),
            Map.entry("RETRY_REQUIRED", List.of("COLLECTING", "CANCELED")),
            Map.entry("EVIDENCE_RECEIVED", List.of("SOURCE_PENDING", "REVIEW_REQUIRED")),
            Map.entry("SOURCE_PENDING", List.of("COMPLETE", "REVIEW_REQUIRED", "EXPIRED")),
            Map.entry("REVIEW_REQUIRED", List.of("COMPLETE", "APPEALED", "REJECTED")),
            Map.entry("APPEALED", List.of("REVIEW_REQUIRED")),
            Map.entry("COMPLETE", List.of()), Map.entry("REJECTED", List.of()), Map.entry("EXPIRED", List.of()),
            Map.entry("DECLINED", List.of()), Map.entry("CANCELED", List.of()), Map.entry("UNAVAILABLE", List.of()));

    private static final Map<String, String> EVENT_FOR = Map.of("CONSENT_REQUIRED", "session.created", "READY", "consent.recorded",
            "COLLECTING", "capture.started", "EVIDENCE_RECEIVED", "document.submitted", "SOURCE_PENDING", "issuer.pending",
            "REVIEW_REQUIRED", "review.required", "COMPLETE", "session.completed", "CANCELED", "session.canceled");

    /** A stable code: TRANSITION_NOT_ALLOWED or AUTHORITY_REQUIRED. */
    public static final class SessionError extends RuntimeException {
        private final String code;

        public SessionError(String code, String message) {
            super(message);
            this.code = code;
        }

        public String code() { return code; }
    }

    /** One allowed move; {@code event} is null where the contract names none. */
    public record Transition(String from, String to, String event) {}

    public static boolean canMove(String from, String to) {
        return NEXT.getOrDefault(from, List.of()).contains(to);
    }

    public static boolean isTerminal(String state) {
        return NEXT.getOrDefault(state, List.of()).isEmpty();
    }

    /** A human decision at REVIEW_REQUIRED (to COMPLETE or REJECTED) must name its authority. */
    public static Transition move(String from, String to, String decidedBy) {
        if (!canMove(from, to)) throw new SessionError("TRANSITION_NOT_ALLOWED", from + " cannot move to " + to);
        if (from.equals("REVIEW_REQUIRED") && (to.equals("COMPLETE") || to.equals("REJECTED")) && (decidedBy == null || decidedBy.isBlank())) {
            throw new SessionError("AUTHORITY_REQUIRED", "a review decision needs a documented authority");
        }
        return new Transition(from, to, EVENT_FOR.get(to));
    }

    /** Capture closes where it is open; a finished session is untouched (and the call refused). */
    public static Transition withdrawConsent(String state) {
        return switch (state) {
            case "READY", "COLLECTING", "RETRY_REQUIRED" -> move(state, "CANCELED", null);
            case "CONSENT_REQUIRED" -> move(state, "DECLINED", null);
            default -> throw new SessionError("TRANSITION_NOT_ALLOWED", "consent cannot be withdrawn in " + state);
        };
    }

    public static String afterCapture(boolean payloadIntact) {
        return payloadIntact ? "EVIDENCE_RECEIVED" : "RETRY_REQUIRED";
    }

    /** An outage is not a failure: the session waits. */
    public static String afterSource(String answer) {
        return switch (answer) {
            case "CONFIRMED" -> "COMPLETE";
            case "UNCLEAR" -> "REVIEW_REQUIRED";
            case "TIMED_OUT" -> "EXPIRED";
            default -> "SOURCE_PENDING";
        };
    }
}
