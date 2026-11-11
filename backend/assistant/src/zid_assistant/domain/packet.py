"""The validated answer packet the widget renders (ZI-CHAT-KB-002 section 06; ZI-CHAT-ENG-003 section 04).

The model never mints citations, claim ids, capability states or destinations: the server fills every one of these from the
signed snapshot. The client shows `decision` and the approved action types, nothing else is trusted.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .verdicts import Decision, Reason


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimRef(_Out):
    claim_id: str
    support: list[str] = Field(default_factory=list)  # "SOURCE-ID#span" pairs


class Citation(_Out):
    source_id: str
    revision: str
    title: str
    span_id: str | None = None
    link_id: str | None = None


class CapabilityRef(_Out):
    capability_id: str
    public_state: str


class Action(_Out):
    type: str  # OPEN_APPROVED_LINK | START_HANDOFF_SALES | START_HANDOFF_SUPPORT
    link_id: str | None = None
    label: str | None = None


class Safety(_Out):
    reason: Reason = Reason.OK
    sensitive_input_blocked: bool = False
    sample_content: bool = False  # engineering sample, not approved publication
    ai_assisted: bool = False


class AnswerPacket(_Out):
    response_id: str
    policy_version: str
    snapshot_id: str
    decision: Decision
    intent_id: str | None = None
    answer_text: str
    claims: list[ClaimRef] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    capabilities: list[CapabilityRef] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    safety: Safety = Field(default_factory=Safety)
    session_expires_at: datetime | None = None
