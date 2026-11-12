"""The answer pipeline (ZI-CHAT-ENG-003 section 06; ADR-04). Deterministic first, constrained retrieval second, model last.

order: route (safety, human, intent) -> approved answer entry | retrieval -> (optional) model draft -> server verification -> packet.
Every branch that cannot be proven falls to ABSTAIN / REFUSE / ESCALATE (fail closed). The model can only ever shorten the path
to an answer that the verifier then re-checks; it can never widen what is allowed.
"""

from __future__ import annotations

import copy
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from zid_assistant import POLICY_VERSION
from zid_assistant.application import router, verifier
from zid_assistant.application.retrieval import Hit, SpanIndex
from zid_assistant.application.snapshot import Knowledge
from zid_assistant.domain.content import Answer, ClaimKind, ContentState, Route
from zid_assistant.domain.eligibility import (
    Context,
    Snapshot,
    Verdict,
    claim_eligible,
    link_usable,
    source_eligible,
)
from zid_assistant.domain.packet import (
    Action,
    AnswerPacket,
    CapabilityRef,
    Citation,
    ClaimRef,
    Safety,
)
from zid_assistant.domain.verdicts import Decision, Reason
from zid_assistant.infrastructure.model.base import (
    DraftRequest,
    EvidenceItem,
    ModelRefused,
    ModelUnavailable,
)
from zid_assistant.infrastructure.model.gateway import ModelGateway
from zid_assistant.infrastructure.revocations import Revocations, apply
from zid_assistant.settings import AssistantSettings

# Safety copy lives in code as the floor: a content file can improve the wording, never remove the refusal (ADR-01).
_DEFAULT_COPY = {
    "refuse_account_access": "I can't view accounts, send codes, reset access or change anything. This public assistant has no access to any account. Please use your organization's official sign-in or account-recovery flow.",
    "refuse_identity_proofing": "Please don't share identity documents, photos or personal verification details in this chat. Identity verification only happens in a separate, official, authenticated process, never here.",
    "refuse_credentials": "Please don't share passwords, codes, keys or tokens here. I can't store or use them, and nothing you type in this chat is kept. If a secret was exposed, rotate it through its official channel.",
    "sensitive_input_blocked": "That message looks like it contains a secret or personal identity data, so it was not processed or stored. Please remove it and ask your question again in general terms.",
    "abstain_no_source": "I can't confirm that from the public information I'm allowed to use. You can ask differently, or use one of the official links below.",
    "abstain_capability_unconfirmed": "I can explain the general concept, but I can't confirm whether Zoiko iD offers it today without current, published release information.",
    "out_of_scope": "I can help with questions about Zoiko iD and identity concepts such as sign-in, credentials, consent, authorization, machine and AI-agent identity, and integrations.",
    "escalate_sales": "I can't take a request myself in this chat, but our sales team can help.",
    "escalate_support": "I can't open support requests in this chat, but our support team can help with public product questions.",
    "model_unavailable": "I can't answer that right now. The official links below are always available.",
    "greeting": "Hello! I'm the Zoiko iD assistant. Ask me about identity, passkeys, credentials, consent or integrations, or pick a topic below.",
    "thanks": "You're welcome! Ask me anything else about Zoiko iD whenever you like.",
    "clarify_scope": "Could you tell me a little more about what you'd like to know?",
    "sample_label": "Engineering sample content, not an approved publication.",
}
_SENT = re.compile(r"(?<=[.!?])\s+")
_STATIC_HELP_LINKS = ("OVERVIEW", "DEVELOPER-DOCS", "HELP-SUPPORT", "CONTACT")


@dataclass(frozen=True, slots=True)
class Servable:
    ok: bool
    reason: Reason
    claims: list[ClaimRef]
    caps: list[CapabilityRef]
    sample: bool
    refuse: bool = False


@dataclass(frozen=True, slots=True)
class Outcome:
    """Metadata for logs and metrics. Never contains visitor text or model text (ADR-12)."""

    intent_id: str | None
    decision: Decision
    reason: Reason
    route: str
    source_ids: tuple[str, ...] = ()
    ai_attempted: bool = False
    ai_used: bool = False
    seconds: float = 0.0
    extras: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Answered:
    packet: AnswerPacket
    outcome: Outcome


class AssistantPipeline:
    def __init__(
        self, k: Knowledge, settings: AssistantSettings, gateway: ModelGateway | None
    ) -> None:
        self.k = k
        self.s = settings
        self.gateway = gateway
        self.index = SpanIndex(k.snapshot)

    def with_revocations(self, rev: Revocations) -> AssistantPipeline:
        """This pipeline with the live revocation list folded in for one request (ADR-11). Shares the index, gateway and settings."""
        if not rev:
            return self
        clone = copy.copy(self)
        clone.k = apply(self.k, rev)
        return clone

    # ------------------------------------------------------------------ helpers
    def _ctx(self, locale: str, snapshot: Snapshot | None = None) -> Context:
        return Context(locale=locale, allow_sample=self._sample_allowed())

    def _sample_allowed(self) -> bool:
        return bool(
            self.s.allow_sample_snapshot
            and not self.s.is_production_like
            and self.k.snapshot.sample
        )

    def _copy(self, key: str | None) -> str:
        if key and key in self.k.copy:
            return self.k.copy[key].text
        return _DEFAULT_COPY.get(key or "", _DEFAULT_COPY["abstain_no_source"])

    def _link_action(self, link_id: str | None, label: str | None = None) -> Action | None:
        if not link_id or not link_usable(self.k.snapshot, link_id).allowed:
            return None
        dest = self.k.snapshot.destinations[link_id]
        if dest.secure_route:
            return (
                None  # secure-route destinations are never opened from chat with chat data (ADR-01)
            )
        return Action(type="OPEN_APPROVED_LINK", link_id=link_id, label=label or dest.label)

    def _static_actions(self) -> list[Action]:
        out = [self._link_action(i) for i in _STATIC_HELP_LINKS]
        return [a for a in out if a is not None][:3]

    def _packet(
        self,
        decision: Decision,
        text: str,
        *,
        intent_id: str | None,
        reason: Reason = Reason.OK,
        actions: list[Action] | None = None,
        claims: list[ClaimRef] | None = None,
        citations: list[Citation] | None = None,
        capabilities: list[CapabilityRef] | None = None,
        sample: bool = False,
        ai: bool = False,
        blocked: bool = False,
        expires: datetime | None = None,
    ) -> AnswerPacket:
        return AnswerPacket(
            response_id=f"rsp_{uuid.uuid4().hex}",
            policy_version=POLICY_VERSION,
            snapshot_id=self.k.snapshot.snapshot_id,
            decision=decision,
            intent_id=intent_id,
            answer_text=text,
            claims=claims or [],
            citations=citations or [],
            capabilities=capabilities or [],
            actions=actions if actions is not None else [],
            safety=Safety(
                reason=reason,
                sensitive_input_blocked=blocked,
                sample_content=sample,
                ai_assisted=ai,
            ),
            session_expires_at=expires,
        )

    def blocked_packet(self, expires: datetime | None) -> AnswerPacket:
        return self._packet(
            Decision.REFUSE,
            self._copy("sensitive_input_blocked"),
            intent_id=None,
            reason=Reason.SENSITIVE_INPUT,
            blocked=True,
            expires=expires,
        )

    # ------------------------------------------------------------------ answer entries
    def _answer_servable(self, a: Answer, ctx: Context) -> Servable:
        """Is this approved answer entry allowed to be shown now?

        Hedged / explanatory entries (the default) are shown when their state allows it and their wording passes the language gate;
        their claim ids only label the topic. An entry that ASSERTS its claims (`asserts: true`) needs every claim class eligible. A
        SECURE_ROUTE class turns the entry into a refusal: its approved text is the safe redirect (CL-08)."""
        snap = self.k.snapshot
        published = a.state is ContentState.PUBLISHED
        is_sample = a.state is ContentState.DRAFT_NOT_PUBLISHED
        if not published and not (is_sample and ctx.allow_sample and snap.sample):
            return Servable(False, Reason.NO_SOURCE, [], [], False)
        if a.locale != ctx.locale:
            return Servable(False, Reason.NO_SOURCE, [], [], False)
        claims: list[ClaimRef] = []
        approved: set[ClaimKind] = set()
        refuse = False
        for cid in a.claim_ids:
            res = claim_eligible(snap, cid, ctx, capability_ids=a.capability_ids)
            cls = snap.claims.get(cid)
            if res.verdict is Verdict.DENY_SECURE_ROUTE:
                refuse = True
            elif res.allowed:
                if cls is not None:
                    approved.add(cls.kind)
            elif a.asserts:
                return Servable(False, res.reason, [], [], is_sample)
            claims.append(
                ClaimRef(
                    claim_id=cid,
                    support=[
                        f"{sid}#{s.span_id}"
                        for sid in a.source_ids
                        if sid in snap.sources
                        for s in snap.sources[sid].spans[:1]
                    ],
                )
            )
        for sid in a.source_ids:
            if not source_eligible(snap, sid, ctx).allowed:
                return Servable(False, Reason.NO_SOURCE, [], [], is_sample)
        if not refuse:
            bad = verifier.language_gate(a.text, approved)
            if bad is not None:
                return Servable(False, bad.reason, [], [], is_sample)
        caps = (
            [CapabilityRef(capability_id=c, public_state="RELEASED") for c in a.capability_ids]
            if a.asserts
            else []
        )
        return Servable(
            True, Reason.SECURE_ROUTE_ONLY if refuse else Reason.OK, claims, caps, is_sample, refuse
        )

    def _citations(
        self, source_ids: tuple[str, ...], hits: list[Hit] | None = None, link_id: str | None = None
    ) -> list[Citation]:
        out: list[Citation] = []
        snap = self.k.snapshot
        spans = {h.source_id: h.span_id for h in hits or []}
        for sid in source_ids:
            src = snap.sources.get(sid)
            if src is None:
                continue
            out.append(
                Citation(
                    source_id=sid,
                    revision=src.revision,
                    title=src.title,
                    span_id=spans.get(sid),
                    link_id=link_id if link_usable(snap, link_id or "").allowed else None,
                )
            )
        return out

    # ------------------------------------------------------------------ the entry point
    async def answer(
        self,
        text: str,
        locale: str,
        expires: datetime | None,
        *,
        handoff_available: dict[str, bool] | None = None,
        ai_on: bool = False,
    ) -> Answered:
        t0 = time.perf_counter()
        ctx = self._ctx(locale)
        decision = router.route(self.k, text)
        handoff = handoff_available or {}

        def done(pkt: AnswerPacket, route: str, **kw: object) -> Answered:
            return Answered(
                pkt,
                Outcome(
                    intent_id=pkt.intent_id,
                    decision=pkt.decision,
                    reason=pkt.safety.reason,
                    route=route,
                    source_ids=tuple(c.source_id for c in pkt.citations),
                    ai_attempted=bool(kw.get("ai_attempted", False)),
                    ai_used=pkt.safety.ai_assisted,
                    seconds=time.perf_counter() - t0,
                ),
            )

        chat = router.smalltalk(text)
        if chat is not None and decision.kind != "SAFETY":
            return done(
                self._packet(
                    Decision.CLARIFY,
                    self._copy(chat),
                    intent_id=None,
                    actions=[],
                    expires=expires,
                ),
                "SMALLTALK",
            )

        if decision.kind == "SAFETY":
            acts = self._safety_actions(decision.reason)
            return done(
                self._packet(
                    Decision.REFUSE,
                    self._copy(decision.copy_key),
                    intent_id=None,
                    reason=decision.reason,
                    actions=acts,
                    expires=expires,
                ),
                "SAFETY",
            )

        if decision.kind == "HUMAN":
            purpose = decision.purpose or "SALES"
            actions: list[Action] = []
            if handoff.get(purpose.lower()):
                actions.append(
                    Action(
                        type=f"START_HANDOFF_{purpose}",
                        label="Talk to Sales" if purpose == "SALES" else "Contact support",
                    )
                )
            else:
                link_id = "TALK-TO-SALES" if purpose == "SALES" else "HELP-SUPPORT"
                la = self._link_action(link_id)
                if la:
                    actions.append(la)
            return done(
                self._packet(
                    Decision.ESCALATE,
                    self._copy(decision.copy_key),
                    intent_id=None,
                    reason=Reason.HUMAN_REQUESTED,
                    actions=actions,
                    expires=expires,
                ),
                "HUMAN",
            )

        intent = decision.intent
        if intent is not None and intent.route in (Route.REFUSE, Route.ESCALATE, Route.CLARIFY):
            dec = {
                Route.REFUSE: Decision.REFUSE,
                Route.ESCALATE: Decision.ESCALATE,
                Route.CLARIFY: Decision.CLARIFY,
            }[intent.route]
            # an approved library entry for this exact question carries the richer, reviewed wording: prefer it, keep the intent's decision
            found = router.best_answer(self.k, text, intent, min_score=3.0)
            if found is not None:
                a, _s = found
                sv = self._answer_servable(a, ctx)
                if sv.ok:
                    link = self._link_action(a.cta_link_id) or self._link_action(intent.cta_link_id)
                    reason = (
                        Reason.SECURE_ROUTE_ONLY
                        if sv.refuse
                        else (Reason.OUT_OF_SCOPE if dec is Decision.REFUSE else Reason.OK)
                    )
                    return done(
                        self._packet(
                            Decision.ANSWER
                            if dec is Decision.CLARIFY
                            else dec,  # a reviewed answer is an answer, not a question
                            a.text,
                            intent_id=intent.intent_id,
                            reason=reason,
                            claims=sv.claims,
                            citations=self._citations(a.source_ids, link_id=a.cta_link_id),
                            capabilities=sv.caps,
                            actions=[link] if link else [],
                            sample=sv.sample,
                            expires=expires,
                        ),
                        intent.route.value,
                    )
            acts = [a for a in [self._link_action(intent.cta_link_id)] if a]
            reason = Reason.OUT_OF_SCOPE if dec is Decision.REFUSE else Reason.OK
            return done(
                self._packet(
                    dec,
                    self._copy(intent.refusal_key),
                    intent_id=intent.intent_id,
                    reason=reason,
                    actions=acts,
                    expires=expires,
                ),
                intent.route.value,
            )

        # approved answer entry first (ADR-04: known FAQ intents are answered deterministically). A DETERMINISTIC intent accepts a
        # loose match; any other intent (or none) needs a strong match to the library question before we prefer it over retrieval.
        det = intent is not None and intent.route is Route.DETERMINISTIC
        found = router.best_answer(
            self.k, text, intent if det else None, min_score=1.0 if det else 3.0
        )
        if found is not None:
            a, _score = found
            aintent = self.k.intents.get(a.intent_id) or intent
            sv = self._answer_servable(a, ctx)
            if sv.ok:
                link = self._link_action(a.cta_link_id) or self._link_action(
                    getattr(aintent, "cta_link_id", None)
                )
                dec = Decision.REFUSE if sv.refuse else Decision.ANSWER
                return done(
                    self._packet(
                        dec,
                        a.text,
                        intent_id=a.intent_id,
                        reason=sv.reason,
                        claims=sv.claims,
                        citations=self._citations(a.source_ids, link_id=a.cta_link_id),
                        capabilities=sv.caps,
                        actions=[link] if link else [],
                        sample=sv.sample,
                        expires=expires,
                    ),
                    "DETERMINISTIC",
                )
            if det:
                return done(self._abstain(a.intent_id, sv.reason, expires), "DETERMINISTIC")

        # constrained retrieval (RAG route, or an unrouted question that the sources can support)
        hits = self.index.search(self.k.snapshot, ctx, text)
        if not hits:
            if intent is None:
                return done(
                    self._packet(
                        Decision.REFUSE,
                        self._copy("out_of_scope"),
                        intent_id=None,
                        reason=Reason.OUT_OF_SCOPE,
                        actions=self._static_actions(),
                        expires=expires,
                    ),
                    "NONE",
                )
            return done(self._abstain(intent.intent_id, Reason.NO_SOURCE, expires), "RAG")
        intent_id = intent.intent_id if intent else None
        evidence = tuple(EvidenceItem(h.source_id, h.span_id, h.text) for h in hits)
        is_sample = any(
            self.k.snapshot.sources[h.source_id].state is not ContentState.PUBLISHED for h in hits
        )

        ai_attempted = False
        if self.gateway is not None and ai_on:
            ai_attempted = True
            try:
                draft = await self.gateway.draft(
                    DraftRequest(
                        text,
                        evidence,
                        self.s.model_max_output_tokens,
                        self.s.model_timeout_seconds,
                        locale,
                    )
                )
                res = verifier.verify(draft.raw_text, evidence, self.k, ctx)
                if isinstance(res, verifier.Verified):
                    cites = [
                        Citation(
                            source_id=e.source_id,
                            revision=self.k.snapshot.sources[e.source_id].revision,
                            title=self.k.snapshot.sources[e.source_id].title,
                            span_id=e.span_id,
                        )
                        for e in res.refs
                    ]
                    claims = [
                        ClaimRef(claim_id=c, support=[e.ref for e in res.refs])
                        for c in res.claim_ids
                    ]
                    return done(
                        self._packet(
                            Decision.ANSWER,
                            res.answer_text,
                            intent_id=intent_id,
                            claims=claims,
                            citations=cites,
                            actions=self._cta_for(intent),
                            sample=is_sample,
                            ai=True,
                            expires=expires,
                        ),
                        "RAG",
                        ai_attempted=True,
                    )
                # a rejected draft never reaches the visitor: fall through to the verified source excerpt
            except (ModelUnavailable, ModelRefused):
                pass
        excerpt = self._excerpt_answer(hits, ctx, intent_id, intent, is_sample, expires)
        if excerpt is not None:
            return done(excerpt, "RAG", ai_attempted=ai_attempted)
        return done(
            self._abstain(
                intent_id,
                Reason.NO_SOURCE if not ai_attempted else Reason.MODEL_UNAVAILABLE,
                expires,
            ),
            "RAG",
            ai_attempted=ai_attempted,
        )

    # ------------------------------------------------------------------ pieces
    def _cta_for(self, intent: object) -> list[Action]:
        link_id = getattr(intent, "cta_link_id", None)
        a = self._link_action(link_id)
        return [a] if a else []

    def _safety_actions(self, reason: Reason) -> list[Action]:
        if reason is Reason.ACCOUNT_ACTION:
            return [a for a in [self._link_action("HELP-SUPPORT")] if a]
        return [
            a for a in [self._link_action("PRIVACY-NOTICE"), self._link_action("HELP-SUPPORT")] if a
        ][:2]

    def _abstain(
        self, intent_id: str | None, reason: Reason, expires: datetime | None
    ) -> AnswerPacket:
        key = (
            "abstain_capability_unconfirmed"
            if reason is Reason.CAPABILITY_NOT_RELEASED
            else "model_unavailable"
            if reason is Reason.MODEL_UNAVAILABLE
            else "abstain_no_source"
        )
        return self._packet(
            Decision.ABSTAIN,
            self._copy(key),
            intent_id=intent_id,
            reason=reason,
            actions=self._static_actions(),
            expires=expires,
        )

    def _excerpt_answer(
        self,
        hits: list[Hit],
        ctx: Context,
        intent_id: str | None,
        intent: object,
        is_sample: bool,
        expires: datetime | None,
    ) -> AnswerPacket | None:
        """Verified, approved source text shown as is (no model). Claims on the source must be eligible, as for any answer."""
        snap = self.k.snapshot
        top = hits[0]
        src = snap.sources[top.source_id]
        cap_ids = tuple(src.eligible_capability_ids)
        claims: list[ClaimRef] = []
        approved: set[ClaimKind] = set()
        for cid in src.claim_ids:
            if cid.startswith("!"):
                continue
            res = claim_eligible(snap, cid, ctx, capability_ids=cap_ids)
            if res.verdict is Verdict.DENY_SECURE_ROUTE:
                return (
                    None  # a source about account operations is never excerpted into a chat answer
                )
            if res.allowed and (cls := snap.claims.get(cid)) is not None:
                approved.add(cls.kind)
            claims.append(ClaimRef(claim_id=cid, support=[f"{src.source_id}#{top.span_id}"]))
        text = " ".join(_SENT.split(top.text.strip())[:2]).strip()
        if not text or verifier.language_gate(text, approved) is not None:
            return None
        return self._packet(
            Decision.ANSWER,
            text,
            intent_id=intent_id,
            claims=claims,
            citations=[
                Citation(
                    source_id=src.source_id,
                    revision=src.revision,
                    title=src.title,
                    span_id=top.span_id,
                )
            ],
            actions=self._cta_for(intent),
            sample=is_sample,
            expires=expires,
        )
