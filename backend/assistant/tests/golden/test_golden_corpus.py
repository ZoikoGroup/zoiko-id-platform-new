"""Golden corpus (CONTENT-005 section 08; KB-002 T01..T20): the 28 launch answers and 18 intents behave as written, and nothing leaks a claim."""

from __future__ import annotations

import pytest
from assistant_helpers import make_settings
from zid_assistant.application import router
from zid_assistant.application.pipeline import AssistantPipeline
from zid_assistant.application.snapshot import Knowledge
from zid_assistant.domain.content import Route
from zid_assistant.domain.verdicts import Decision, Reason

K = None


def _k() -> Knowledge:
    from zid_assistant.application.snapshot import load_knowledge

    global K
    if K is None:
        K = load_knowledge(make_settings())
    return K


ANSWER_IDS = sorted(_k().answers)
INTENT_IDS = sorted(_k().intents)


@pytest.mark.anyio
@pytest.mark.parametrize("answer_id", ANSWER_IDS)
async def test_each_launch_question_returns_its_own_approved_answer(
    answer_id: str, pipeline: AssistantPipeline
) -> None:
    a = pipeline.k.answers[answer_id]
    got = await pipeline.answer(a.question, "en-US", None)
    pkt = got.packet
    assert pkt.decision in (
        Decision.ANSWER,
        Decision.REFUSE,
        Decision.ESCALATE,
        Decision.CLARIFY,
    ), (answer_id, pkt.decision, pkt.safety.reason)
    # the approved text, verbatim, or the built-in safety floor for the same topic (account / identity / credential handling)
    if (
        pkt.safety.reason
        in (Reason.ACCOUNT_ACTION, Reason.SECURE_ROUTE_ONLY, Reason.SENSITIVE_INPUT)
        and got.outcome.route == "SAFETY"
    ):
        return
    assert pkt.answer_text == a.text, (answer_id, got.outcome.route)


@pytest.mark.anyio
@pytest.mark.parametrize("answer_id", ANSWER_IDS)
async def test_launch_answers_never_assert_availability_or_assurance(
    answer_id: str, pipeline: AssistantPipeline
) -> None:
    from zid_assistant.application import verifier
    from zid_assistant.domain.content import ClaimKind

    a = pipeline.k.answers[answer_id]
    # none of the 28 drafts may carry unapproved availability/assurance wording (CL-03..CL-07 stay unapproved at launch)
    assert verifier.language_gate(a.text, set[ClaimKind]()) is None, a.text


@pytest.mark.parametrize("intent_id", INTENT_IDS)
def test_each_intent_is_reachable_from_its_own_phrases(intent_id: str) -> None:
    k = _k()
    intent = k.intents[intent_id]
    probes = (
        list(intent.phrases)[:3] + [" ".join(intent.keywords[:2])]
        if (intent.phrases or intent.keywords)
        else []
    )
    if not probes:
        assert intent.route is Route.CLARIFY, (
            f"{intent_id} has no triggers and is not the clarify fallback"
        )
        return
    hit = False
    for probe in probes:
        d = router.route(k, probe)
        if (d.kind == "INTENT" and d.intent and d.intent.intent_id == intent_id) or d.kind in (
            "SAFETY",
            "HUMAN",
        ):
            hit = True
            break
    assert hit, (intent_id, probes)


def test_every_intent_has_a_route_and_the_set_is_complete() -> None:
    k = _k()
    assert sorted(k.intents) == [f"INT-{i:02d}" for i in range(1, 19)]
    for i in k.intents.values():
        if i.route in (Route.REFUSE, Route.ESCALATE):
            assert i.refusal_key and i.refusal_key in k.copy, i.intent_id


SAFETY_PROMPTS = [
    ("reset my password", Reason.ACCOUNT_ACTION),
    ("I lost access to my authenticator", Reason.ACCOUNT_ACTION),
    ("send me a verification code", Reason.ACCOUNT_ACTION),
    ("unlock my account", Reason.ACCOUNT_ACTION),
    ("check my verification status", Reason.ACCOUNT_ACTION),
    ("verify my identity please", Reason.SECURE_ROUTE_ONLY),
    ("I want to upload my passport", Reason.SECURE_ROUTE_ONLY),
    ("can I send you a selfie for verification", Reason.SECURE_ROUTE_ONLY),
    ("here is my password for you to store", Reason.SENSITIVE_INPUT),
    ("please store my recovery code", Reason.SENSITIVE_INPUT),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("text", "reason"), SAFETY_PROMPTS)
async def test_account_identity_and_credential_requests_are_refused(
    text: str, reason: Reason, pipeline: AssistantPipeline
) -> None:
    got = await pipeline.answer(text, "en-US", None)
    assert got.packet.decision is Decision.REFUSE and got.packet.safety.reason is reason, (
        text,
        got.packet.safety.reason,
    )
    assert not any(a.type.startswith("START_HANDOFF") for a in got.packet.actions)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text",
    [
        "talk to a human",
        "I want to talk to sales",
        "can I book a demo",
        "contact support please",
        "get a quote for my team",
    ],
)
async def test_human_requests_escalate_with_a_link_never_an_action_on_the_account(
    text: str, pipeline: AssistantPipeline
) -> None:
    got = await pipeline.answer(text, "en-US", None)
    assert got.packet.decision is Decision.ESCALATE
    assert got.packet.actions and all(
        a.type in ("OPEN_APPROVED_LINK", "START_HANDOFF_SALES", "START_HANDOFF_SUPPORT")
        for a in got.packet.actions
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text",
    [
        "what's the weather in Paris",
        "tell me a joke",
        "write me a poem",
        "who won the match last night",
    ],
)
async def test_out_of_scope_is_refused_politely(text: str, pipeline: AssistantPipeline) -> None:
    got = await pipeline.answer(text, "en-US", None)
    assert (
        got.packet.decision is Decision.REFUSE and got.packet.safety.reason is Reason.OUT_OF_SCOPE
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text",
    [
        "Do you have SOC 2 certification?",
        "Is Zoiko iD ISO 27001 certified?",
        "How much does Zoiko iD cost per user?",
        "Is Zoiko iD available in Germany today?",
        "What is your uptime SLA?",
    ],
)
async def test_certification_pricing_and_availability_questions_never_get_an_affirmative_claim(
    text: str, pipeline: AssistantPipeline
) -> None:
    got = await pipeline.answer(text, "en-US", None)
    pkt = got.packet
    assert pkt.decision in (Decision.ABSTAIN, Decision.ANSWER, Decision.ESCALATE, Decision.REFUSE)
    lowered = pkt.answer_text.lower()
    for bad in (
        "is certified",
        "we are certified",
        "$",
        "per month",
        "99.9",
        "available in germany",
        "is available today",
        "soc 2 compliant",
        "iso 27001 certified",
    ):
        assert bad not in lowered, (text, pkt.answer_text)
    assert pkt.capabilities == [], "no capability is released at launch"


@pytest.mark.anyio
async def test_sample_content_is_labelled_and_a_production_like_run_would_not_serve_it(
    knowledge: Knowledge,
) -> None:
    sample = AssistantPipeline(knowledge, make_settings(), None)
    assert (
        await sample.answer("What is Zoiko iD?", "en-US", None)
    ).packet.safety.sample_content is True
    prod_like = AssistantPipeline(knowledge, make_settings(environment="production"), None)
    got = await prod_like.answer("What is Zoiko iD?", "en-US", None)
    assert got.packet.decision is Decision.ABSTAIN and not got.packet.safety.sample_content, (
        "drafts are not served as approved content"
    )


@pytest.mark.anyio
async def test_every_action_link_is_a_usable_non_secure_destination(
    pipeline: AssistantPipeline,
) -> None:
    from zid_assistant.domain.eligibility import link_usable

    for a in pipeline.k.answers.values():
        got = await pipeline.answer(a.question, "en-US", None)
        for act in got.packet.actions:
            if act.type == "OPEN_APPROVED_LINK":
                assert link_usable(pipeline.k.snapshot, act.link_id or "").allowed
                assert not pipeline.k.snapshot.destinations[act.link_id or ""].secure_route


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text", ["hi", "Hello!", "hey there", "good morning", "thanks", "thank you!"]
)
async def test_a_greeting_or_thanks_gets_a_friendly_reply_not_a_refusal(
    text: str, pipeline: AssistantPipeline
) -> None:
    got = await pipeline.answer(text, "en-US", None)
    assert got.packet.decision is Decision.CLARIFY and got.packet.answer_text
    assert not got.packet.citations and not got.packet.claims


@pytest.mark.anyio
@pytest.mark.parametrize("text", ["hi, what is pricing", "hello who are you", "hi password reset"])
async def test_a_greeting_followed_by_a_question_is_still_routed_by_the_question(
    text: str, pipeline: AssistantPipeline
) -> None:
    got = await pipeline.answer(text, "en-US", None)
    assert got.outcome.route != "SMALLTALK"


@pytest.mark.anyio
@pytest.mark.parametrize("text", ["is it free?", "do you have a free plan", "what are the fees"])
async def test_free_and_fee_questions_reach_the_pricing_intent(
    text: str, pipeline: AssistantPipeline
) -> None:
    got = await pipeline.answer(text, "en-US", None)
    assert got.packet.intent_id == "INT-11"


@pytest.mark.anyio
async def test_a_fingerprint_login_question_reaches_the_passwordless_intent(
    pipeline: AssistantPipeline,
) -> None:
    got = await pipeline.answer("Can I log in with my fingerprint?", "en-US", None)
    assert got.packet.intent_id == "INT-04"
