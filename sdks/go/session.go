package zoiko

import "strings"

// The verification session state machine (ZID-INT-002 section 05.1): a pure mirror of the TypeScript and Python ones.
// The server is authoritative; this only stops a client doing or showing what the contract forbids: nothing is captured before consent, a
// corrupt payload never advances, OCR is not source authenticity, an issuer outage keeps the session SOURCE_PENDING (there is no FAILED
// state), a human decision needs a documented authority, and withdrawing consent closes capture.

var SessionStates = []string{
	"NEW", "CONSENT_REQUIRED", "READY", "COLLECTING", "RETRY_REQUIRED", "EVIDENCE_RECEIVED", "SOURCE_PENDING",
	"REVIEW_REQUIRED", "APPEALED", "COMPLETE", "REJECTED", "EXPIRED", "DECLINED", "CANCELED", "UNAVAILABLE",
}

var next = map[string][]string{
	"NEW":               {"CONSENT_REQUIRED", "UNAVAILABLE"},
	"CONSENT_REQUIRED":  {"READY", "DECLINED"},
	"READY":             {"COLLECTING", "CANCELED"},
	"COLLECTING":        {"EVIDENCE_RECEIVED", "RETRY_REQUIRED", "CANCELED"},
	"RETRY_REQUIRED":    {"COLLECTING", "CANCELED"},
	"EVIDENCE_RECEIVED": {"SOURCE_PENDING", "REVIEW_REQUIRED"},
	"SOURCE_PENDING":    {"COMPLETE", "REVIEW_REQUIRED", "EXPIRED"},
	"REVIEW_REQUIRED":   {"COMPLETE", "APPEALED", "REJECTED"},
	"APPEALED":          {"REVIEW_REQUIRED"},
	"COMPLETE":          {}, "REJECTED": {}, "EXPIRED": {}, "DECLINED": {}, "CANCELED": {}, "UNAVAILABLE": {},
}

var eventFor = map[string]string{
	"CONSENT_REQUIRED": "session.created", "READY": "consent.recorded", "COLLECTING": "capture.started",
	"EVIDENCE_RECEIVED": "document.submitted", "SOURCE_PENDING": "issuer.pending", "REVIEW_REQUIRED": "review.required",
	"COMPLETE": "session.completed", "CANCELED": "session.canceled",
}

// SessionError carries a stable code (TRANSITION_NOT_ALLOWED, AUTHORITY_REQUIRED).
type SessionError struct{ Code, Message string }

func (e *SessionError) Error() string { return e.Message }

// Transition is one allowed move; Event is empty where the contract names none.
type Transition struct{ From, To, Event string }

func CanMove(from, to string) bool {
	for _, s := range next[from] {
		if s == to {
			return true
		}
	}
	return false
}

func IsTerminal(state string) bool { return len(next[state]) == 0 }

// Move goes to `to`. A human decision at REVIEW_REQUIRED (to COMPLETE or REJECTED) must name its authority.
func Move(from, to, decidedBy string) (Transition, error) {
	if !CanMove(from, to) {
		return Transition{}, &SessionError{"TRANSITION_NOT_ALLOWED", from + " cannot move to " + to}
	}
	if from == "REVIEW_REQUIRED" && (to == "COMPLETE" || to == "REJECTED") && strings.TrimSpace(decidedBy) == "" {
		return Transition{}, &SessionError{"AUTHORITY_REQUIRED", "a review decision needs a documented authority"}
	}
	return Transition{from, to, eventFor[to]}, nil
}

// WithdrawConsent closes capture where it is open; a finished session is untouched (and the call refused).
func WithdrawConsent(state string) (Transition, error) {
	switch state {
	case "READY", "COLLECTING", "RETRY_REQUIRED":
		return Move(state, "CANCELED", "")
	case "CONSENT_REQUIRED":
		return Move(state, "DECLINED", "")
	}
	return Transition{}, &SessionError{"TRANSITION_NOT_ALLOWED", "consent cannot be withdrawn in " + state}
}

// AfterCapture: a corrupt payload never advances.
func AfterCapture(payloadIntact bool) string {
	if payloadIntact {
		return "EVIDENCE_RECEIVED"
	}
	return "RETRY_REQUIRED"
}

// AfterSource: an outage is not a failure, the session waits.
func AfterSource(answer string) string {
	switch answer {
	case "CONFIRMED":
		return "COMPLETE"
	case "UNCLEAR":
		return "REVIEW_REQUIRED"
	case "TIMED_OUT":
		return "EXPIRED"
	}
	return "SOURCE_PENDING"
}
