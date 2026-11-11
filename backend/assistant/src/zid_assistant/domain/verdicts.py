"""Server-owned outcomes. The client and the model never decide these (ZI-CHAT-KB-002 section 06; ADR-04)."""

from __future__ import annotations

from enum import StrEnum


class Decision(StrEnum):
    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    ABSTAIN = "ABSTAIN"  # no verified evidence: say so, never guess
    REFUSE = "REFUSE"  # out of scope or unsafe (secrets, identity actions, account access)
    ESCALATE = "ESCALATE"  # point to a human or an official channel (a link, never an action)


class Reason(StrEnum):
    OK = "OK"
    DISABLED = "ASSISTANT_DISABLED"
    KILL_SWITCH = "KILL_SWITCH"
    NO_SNAPSHOT = "NO_SNAPSHOT"
    NO_SOURCE = "NO_ELIGIBLE_SOURCE"
    CAPABILITY_NOT_RELEASED = "CAPABILITY_NOT_RELEASED"
    CLAIM_NOT_APPROVED = "CLAIM_NOT_APPROVED"
    CONTRADICTION = "CONTRADICTION"
    SECURE_ROUTE_ONLY = "SECURE_ROUTE_ONLY"
    SENSITIVE_INPUT = "SENSITIVE_INPUT"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_OUTPUT_REJECTED = "MODEL_OUTPUT_REJECTED"
    SOURCE_WITHDRAWN = "SOURCE_WITHDRAWN"
    AMBIGUOUS = "AMBIGUOUS"
    ACCOUNT_ACTION = "ACCOUNT_ACTION"
    HUMAN_REQUESTED = "HUMAN_REQUESTED"
