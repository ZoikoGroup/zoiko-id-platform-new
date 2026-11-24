"""Approved reply routes: the response style and flow of the Zoiko iD website assistant, carried into this service.

An ordered table of (pattern, decision, text, links). The first pattern that matches the visitor's message wins and its fixed, reviewed
text is the reply; nothing here calls a model. Order matters: prompt extraction and account safety first, then boundaries
(certifications, comparisons, SDK availability, pricing-like questions), then grounded topic templates. A template only restates what
the approved notes say, in qualified wording ("Zoiko iD's architecture includes ...; availability depends on the approved release"), and
never starts with "Yes" or "No" except where the answer is a plain fact about the assistant itself.

This layer sits AFTER the built-in safety rules and the human-request handoff in `router.py` (those can never be switched off by content)
and BEFORE the content-defined intents. Anything that matches nothing here falls through to the intents, approved answers, retrieval and
the model, unchanged.

`links` are platform destination ids (p03); the first is the one recommended action. `sources` are snapshot source ids shown as the
reply's citations when the snapshot has them and they are eligible. Every template text is checked against the verifier's language gate
in the unit tests, so a wording slip can never assert availability or assurance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from zid_assistant.domain.sensitive import normalize
from zid_assistant.domain.verdicts import Decision, Reason


@dataclass(frozen=True, slots=True)
class ApprovedRoute:
    name: str
    pattern: re.Pattern[str]
    decision: Decision
    text: str
    links: tuple[str, ...]
    reason: Reason = Reason.OK
    sources: tuple[str, ...] = ()


def _r(
    name: str,
    pattern: str,
    decision: Decision,
    text: str,
    links: tuple[str, ...],
    *,
    reason: Reason | None = None,
    sources: tuple[str, ...] = (),
) -> ApprovedRoute:
    default = {
        Decision.ANSWER: Reason.OK,
        Decision.CLARIFY: Reason.OK,
        Decision.ABSTAIN: Reason.NO_SOURCE,
        Decision.REFUSE: Reason.OUT_OF_SCOPE,
        Decision.ESCALATE: Reason.OK,
    }[decision]
    return ApprovedRoute(
        name,
        re.compile(pattern, re.IGNORECASE | re.DOTALL),
        decision,
        text,
        links,
        reason or default,
        sources,
    )


A, C, B, F, E = (
    Decision.ANSWER,
    Decision.CLARIFY,
    Decision.ABSTAIN,
    Decision.REFUSE,
    Decision.ESCALATE,
)

ROUTES: tuple[ApprovedRoute, ...] = (
    # prompt extraction / jailbreak / role-play: a neutral, in-scope reply, never the prompt
    _r(
        "prompt_extraction",
        r"\b(?:system|internal|hidden|your|initial) (?:prompt|instructions)\b|repeat everything above"
        r"|ignore (?:all |your |the |previous )?(?:previous |prior )?instructions|\byou are now\b|\bpretend (?:to be|you)\b"
        r"|\bjailbreak\b|\bdan\b",
        F,
        "I can help with public Zoiko iD product information and documentation. What identity topic would you like to explore?",
        ("OVERVIEW",),
    ),
    # ZoikoSuite: never say it does or does not integrate
    _r(
        "zoikosuite",
        r"\bzoikosuite\b",
        A,
        "ZoikoSuite is a separate product ecosystem. Zoiko iD is independent identity infrastructure that can underpin it.",
        ("OVERVIEW",),
        sources=("PUB-001",),
    ),
    # tenant records / risk scores
    _r(
        "tenant_records",
        r"\btenant\b|\brisk scores?\b",
        F,
        "I cannot access customer tenant records, risk scores or private identity events from this public chat. Please use the authenticated product workspace or your organization's authorized support channel.",
        ("HELP-SUPPORT",),
    ),
    # pricing, plans, SLA and uptime: never a number or a guarantee; labelled "cannot confirm", routed to pricing and sales
    _r(
        "pricing_sla",
        r"\b(?:pricing|prices?|costs?|how much|sla|slas|uptime|free (?:trial|tier|plan)|subscriptions?|discounts?|quotes?)\b",
        B,
        "I cannot confirm current plans, pricing, uptime or other commercial terms without an approved public source. For a tailored evaluation, use the published sales contact route.",
        ("PRICING", "TALK-TO-SALES"),
        reason=Reason.NO_SOURCE,
    ),
    # suspended / locked account: no lookup, no existence disclosure
    _r(
        "account_suspended",
        r"\baccount\b[^.?!]*\b(?:suspended|locked|blocked|disabled|banned)\b",
        E,
        "I cannot view accounts or see why one is affected. Please use your organization's official sign-in or account-recovery flow, or the Help & support page, and do not share account details here.",
        ("HELP-SUPPORT",),
        reason=Reason.ACCOUNT_ACTION,
    ),
    # passkeys availability today
    _r(
        "passkeys_availability",
        r"(?=.*\bpasskeys?\b)(?=.*\b(?:today|now|enable|available|turn on|live)\b)",
        B,
        "Passkeys can provide phishing-resistant sign-in when implemented correctly. I can explain the approach, but I can confirm Zoiko iD availability only where published release documentation says it is supported.",
        ("AUTH-GUIDE", "DEVELOPER-DOCS", "TALK-TO-SALES"),
        reason=Reason.CAPABILITY_NOT_RELEASED,
    ),
    # is it an identity provider: restate the notes only
    _r(
        "identity_provider",
        r"\bidentity provider\b|\bidp\b",
        A,
        "Zoiko iD is identity infrastructure for people, organizations, devices, workloads and AI agents, bringing authentication, verifiable credentials, consent, delegated authority, risk-aware access decisions and auditable evidence into one trust layer.",
        ("OVERVIEW", "AUTH-GUIDE"),
        sources=("PUB-001",),
    ),
    # certifications / compliance standards
    _r(
        "certifications",
        r"\b(?:iso(?: ?27001)?|soc ?[123]|gdpr|hipaa|pci(?:[- ]dss)?|fedramp|certif\w*|complian\w*|audit report)\b",
        B,
        "I can share verified public assurance information, but I cannot confirm a certification or audit report unless it appears in current Zoiko iD trust documentation. Please see the Trust Center or the security contact.",
        ("TRUST-CENTER", "SECURITY-OVERVIEW", "ASSURANCE-STANDARDS"),
        reason=Reason.CLAIM_NOT_APPROVED,
    ),
    # security / encryption / technology details and "is it safe": never invented
    _r(
        "security_details",
        r"\bencrypt\w*|\bis (?:it|zoiko id) (?:secure|safe|trustworthy)\b|\bhow (?:secure|safe)\b|\btech(?:nology)? stack\b|\bbuilt (?:with|on)\b|\btls\b|\baes\b|\bpros and cons\b|\badvantages?\b|\bdisadvantages?\b",
        B,
        "I cannot confirm security or technology details such as encryption, or assess how secure the platform is. Please see the Trust Center or the Security pages.",
        ("TRUST-CENTER", "SECURITY-OVERVIEW", "ASSURANCE-STANDARDS"),
        reason=Reason.CLAIM_NOT_APPROVED,
    ),
    # country / region availability: never confirmed here
    _r(
        "region_availability",
        r"(?=.*\b(?:countr(?:y|ies)|regions?|india|united states|usa|u\.s\.|united kingdom|uk|europe|eu|germany|france|ireland|canada|australia|singapore|japan|brazil|uae|worldwide|globally)\b)"
        r"(?=.*\b(?:support|supported|available|availability|operate|offer|launch\w*|work in|use it in|sign up|register|registration|open in)\b)",
        B,
        "I cannot confirm availability for a specific country or region. Please check the documentation or contact sales.",
        ("DEVELOPER-DOCS", "TRUST-CENTER", "TALK-TO-SALES"),
        reason=Reason.CAPABILITY_NOT_RELEASED,
    ),
    # revoking consent: the notes say consent is revocable, never how
    _r(
        "consent_revocation",
        r"\brevok\w*\b|\bwithdraw\w*\b[^.?!]*\bconsents?\b",
        A,
        "Zoiko iD's consent and access model includes user-controlled, revocable consent. I cannot describe revocation steps here; please see the privacy notice or the Help & support page.",
        ("AUTHORIZATION-GUIDE", "PRIVACY-NOTICE", "HELP-SUPPORT"),
        sources=("PUB-006",),
    ),
    # are you an AI: disclose AI use and the public boundary
    _r(
        "are_you_ai",
        r"\bare you (?:an? )?(?:ai|bot|robot|human|real person|chatgpt)\b"
        r"|\bam i (?:talking|speaking|chatting) (?:to|with) (?:an? )?(?:ai|bot|human|person)\b",
        A,
        "Yes, I am an AI assistant. I explain Zoiko iD concepts and point to public pages; I cannot see or change any account.",
        ("OVERVIEW",),
    ),
    # trusted data sharing: restate the notes only, no mechanics
    _r(
        "data_sharing",
        r"\bdata sharing\b|\bshare (?:my |our )?(?:data|credentials?)\b",
        A,
        "Zoiko iD's notes describe trusted data sharing as part of verifiable credentials, using standards-based credentials, alongside user-controlled, revocable consent. I cannot describe how it works internally.",
        ("IDENTITY-GUIDES", "AUTHORIZATION-GUIDE"),
        sources=("PUB-018", "PUB-006"),
    ),
    # MFA / passkey support questions: qualified, notes-only wording
    _r(
        "mfa_passkeys",
        r"\b(?:mfa|2fa|multi[- ]?factor|two[- ]factor|passwordless|passkeys?)\b",
        A,
        "Zoiko iD's architecture includes passwordless, MFA and phishing-resistant sign-in patterns (passkeys/FIDO2); availability depends on the approved release.",
        ("AUTH-GUIDE", "DEVELOPER-DOCS"),
        sources=("PUB-004",),
    ),
    # FIDO2 / passkeys definitions: restate the notes only
    _r(
        "fido2",
        r"\bfido2?\b",
        A,
        "Zoiko iD's architecture includes passkeys/FIDO2 as phishing-resistant sign-in patterns; availability depends on the approved release.",
        ("AUTH-GUIDE",),
        sources=("PUB-004",),
    ),
    # account actions: reset / unlock / recovery / codes
    _r(
        "account_actions",
        r"\b(?:reset|change|recover|unlock|restore)\b[^.?!]*\b(?:password|account)\b|\bsend me a (?:code|link)\b|\blost (?:my )?access\b",
        E,
        "I cannot reset passwords, unlock or view accounts, or send verification codes through this public assistant. Please use your organization's official sign-in or account-recovery flow, or the Help & support page.",
        ("HELP-SUPPORT", "CONTACT"),
        reason=Reason.ACCOUNT_ACTION,
    ),
    # privacy rights / data deletion
    _r(
        "data_deletion",
        r"\b(?:delete|erase|remove)\b[^.?!]*\b(?:my )?(?:personal )?data\b|\bdata deletion\b",
        E,
        "Privacy requests are handled through the official privacy process, not in this chat. Please see the privacy notice, and do not share identity details here.",
        ("PRIVACY-NOTICE", "HELP-SUPPORT"),
    ),
    _r(
        "privacy_notice",
        r"\bprivacy (?:notice|policy)\b",
        A,
        "The privacy notice has its own page.",
        ("PRIVACY-NOTICE",),
    ),
    _r(
        "where_is_security_info",
        r"(?=.*\b(?:security|trust)\b)(?=.*\b(?:where|see|find|read|view)\b)(?=.*\b(?:information|info|page|center|centre|details)\b)",
        A,
        "Security, trust and compliance information is on the Trust Center and Security pages.",
        ("TRUST-CENTER", "SECURITY-OVERVIEW", "ASSURANCE-STANDARDS"),
    ),
    _r(
        "consent_recorded",
        r"\b(?:record|store|log|keep|retain)\w*\b[^.?!]*\bconsents?\b|\bconsents?\b[^.?!]*\b(?:recorded|stored|logged|retained)\b",
        B,
        "I cannot confirm what is recorded or stored about consent. Please refer to the Trust Center or the privacy notice for details.",
        ("TRUST-CENTER", "PRIVACY-NOTICE", "AUTHORIZATION-GUIDE"),
    ),
    _r(
        "what_is_machine_identity",
        r"\bwhat is (?:machine|agent|ai[- ]agent|device|workload) identity\b",
        A,
        "Machine and AI-agent identity is part of the Zoiko iD platform.",
        ("MACHINE-IDENTITY", "OVERVIEW"),
        sources=("PUB-008",),
    ),
    _r(
        "sso_support",
        r"(?=.*\b(?:saml|oidc|sso|single sign[- ]on)\b)(?=.*\b(?:support|offer|have|do you|does)\b)"
        r"(?!.*\b(?:vs|versus|difference|differ|compare|price|plan|cost)\b)",
        B,
        "Zoiko iD's architecture includes OIDC and SAML SSO for workforce and customer apps. I cannot confirm availability of a specific setup; please check the documentation or contact sales.",
        ("AUTH-GUIDE", "DEVELOPER-DOCS", "TALK-TO-SALES"),
        reason=Reason.CAPABILITY_NOT_RELEASED,
    ),
    # suspected compromise or a found vulnerability -> published security contact
    _r(
        "security_incident",
        r"\b(?:i|we|my|our)\b[^.?!]*\b(?:hacked|compromised|breach(?:ed)?|stolen|vulnerabilit(?:y|ies))\b",
        E,
        "If you suspect a compromise or have found a vulnerability, please use the published security contact. This assistant cannot investigate accounts or collect any details.",
        ("SECURITY-CONTACT", "HELP-SUPPORT", "SECURITY-OVERVIEW"),
    ),
    # identity proofing / document upload
    _r(
        "identity_proofing",
        r"\bverify (?:my|me)\b[^.?!]*\b(?:id|identity)\b|\bupload\b[^.?!]*\b(?:id|passport|licen[cs]e|selfie|document)",
        F,
        "For your security, please do not upload identity documents or biometric information into this chat. Use the official verification process of your organization, or the Help & support page.",
        ("HELP-SUPPORT", "CONTACT"),
        reason=Reason.SECURE_ROUTE_ONLY,
    ),
    # account / tenant access
    _r(
        "account_access",
        r"\b(?:see|view|access|look up|check|open)\b[^.?!]*\b(?:my|our) (?:account|tenant|profile)\b",
        F,
        "I have no access to any account, tenant or personal data, and I cannot look anything up. Please use the official sign-in page of your application, or the Help & support page.",
        ("HELP-SUPPORT",),
        reason=Reason.ACCOUNT_ACTION,
    ),
    # creating or handling keys, tokens, credentials
    _r(
        "key_handling",
        r"\b(?:create|generate|issue|give|share|paste|send)\b[^.?!]*\b(?:api[- ]?keys?|access tokens?|tokens?|secrets?|credentials?)\b",
        F,
        "I cannot create, accept or manage keys, tokens or credentials in this chat. For how developers work with Zoiko iD, please see the documentation.",
        ("DEVELOPER-DOCS", "MACHINE-IDENTITY"),
        reason=Reason.SENSITIVE_INPUT,
    ),
    # OAuth / OIDC / SAML concepts
    _r(
        "oauth_concepts",
        r"(?=.*\b(?:oauth|oidc|openid|saml)\b)(?=.*(?:\bvs\b|\bversus\b|\bdifference\b|\bdiffer\b|\bcompare\b))",
        A,
        "OAuth 2.0 is a framework for delegated API access, and OpenID Connect adds an identity layer for authentication. The right flow depends on your application, so please see the documentation for published options.",
        ("AUTH-GUIDE", "DEVELOPER-DOCS", "INTEGRATIONS"),
        sources=("PUB-005",),
    ),
    # comparisons with other products
    _r(
        "comparisons",
        r"\b(?:compare|comparison|versus|vs\.?|better than|alternative to|okta|auth0|ping identity|entra|azure ad|keycloak|cognito|onelogin)\b",
        B,
        "I cannot assess or compare Zoiko iD with other products. I can explain Zoiko iD's own concepts; the platform overview or sales can help further.",
        ("OVERVIEW", "TALK-TO-SALES"),
    ),
    # specific SDK / language / connector availability
    _r(
        "sdk_availability",
        r"(?=.*\b(?:sdk|library|libraries|scim|python|java|node(?:\.js)?|golang|ruby|php|swift|kotlin)\b)"
        r"(?=.*\b(?:is there|have|support|offer|available|do you|does)\b)(?!.*\b(?:how do i|how to|how can i)\b)",
        B,
        "I cannot confirm that a specific SDK, language or connector is available. Please check the documentation or contact sales.",
        ("DEVELOPER-DOCS", "INTEGRATIONS", "TALK-TO-SALES"),
        reason=Reason.CAPABILITY_NOT_RELEASED,
    ),
    # too short to answer: one clarification
    _r(
        "too_short",
        r"^\s*(?:tell me more|more|does it work|how does it work|what about it|why|and|ok(?:ay)?)\s*[?.!]*\s*$",
        C,
        "Happy to help. Which topic would you like to explore: authentication and passkeys, verifiable credentials, consent and delegated authority, or APIs and integrations?",
        ("OVERVIEW",),
    ),
    # grounded topic templates: only the approved notes' wording
    _r(
        "agent_authority",
        r"\bagents?\b[^.?!]*\b(?:permission|authori[sz]|act on|behalf)",
        A,
        "Zoiko iD's architecture includes delegated authority, including mandates for AI agents, as part of its consent and access model. Machine and AI-agent identity is part of the platform.",
        ("AUTHORIZATION-GUIDE", "MACHINE-IDENTITY", "DEVELOPER-DOCS"),
        sources=("PUB-009", "PUB-007"),
    ),
    _r(
        "what_is_delegated_authority",
        r"\bwhat is delegated authority\b",
        A,
        "Delegated authority is part of Zoiko iD's consent and access model, including mandates for AI agents.",
        ("AUTHORIZATION-GUIDE",),
        sources=("PUB-007",),
    ),
    _r(
        "fine_grained_consent",
        r"\bfine[- ]grained consent\b",
        A,
        "Zoiko iD's consent and access model includes user-controlled data sharing and fine-grained, revocable consent.",
        ("AUTHORIZATION-GUIDE",),
        sources=("PUB-006",),
    ),
    _r(
        "consent_delegation_how",
        r"(?=.*\bconsent\b)(?=.*\bdelegat)(?=.*\b(?:how|work)\b)",
        A,
        "Zoiko iD's consent and access model includes user-controlled data sharing, fine-grained and revocable consent, and delegated authority including mandates for AI agents. I cannot describe internals beyond that.",
        ("AUTHORIZATION-GUIDE", "OVERVIEW"),
        sources=("PUB-006", "PUB-007"),
    ),
    _r(
        "credential_standards",
        r"\bstandards?\b[^.?!]*\bcredentials?\b|\bcredentials?\b[^.?!]*\bstandards?\b",
        B,
        "Zoiko iD describes its credentials as standards-based. I cannot confirm specific named standards or formats; please check the documentation or contact sales.",
        ("IDENTITY-GUIDES", "DEVELOPER-DOCS", "TALK-TO-SALES"),
    ),
    _r(
        "what_are_verifiable_credentials",
        r"\bwhat (?:are|is) (?:the )?verifiable credentials?\b",
        A,
        "Verifiable credentials in Zoiko iD cover digital identity, KYC-style checks and trusted data sharing using standards-based credentials.",
        ("IDENTITY-GUIDES",),
        sources=("PUB-018",),
    ),
    _r(
        "authentication_passkeys_how",
        r"(?=.*\bauthentication\b)(?=.*\bpasskeys?\b)(?=.*\bhow\b)",
        A,
        "Zoiko iD's architecture includes passwordless, MFA and phishing-resistant sign-in patterns (passkeys/FIDO2), with OIDC and SAML SSO for workforce and customer apps.",
        ("AUTH-GUIDE",),
        sources=("PUB-004", "PUB-005"),
    ),
    _r(
        "api_integration",
        r"\bintegrat\w*\b[^.?!]*\b(?:api|sdk)",
        A,
        "Developers start from the documentation and quickstarts. Zoiko iD's APIs and integrations area covers standards, SDKs, webhooks and enterprise integrations; availability of a specific item is not confirmed here.",
        ("INTEGRATIONS", "DEVELOPER-DOCS", "QUICKSTARTS"),
        sources=("PUB-011",),
    ),
    _r(
        "quickstarts_where",
        r"\bquickstarts?\b[^.?!]*\b(?:where|find|link)|\b(?:where|find)\b[^.?!]*\bquickstarts?\b",
        A,
        "The quickstarts have their own page, and the documentation is the companion for developers.",
        ("QUICKSTARTS", "DEVELOPER-DOCS"),
    ),
    _r(
        "getting_started",
        r"\b(?:set (?:it )?up|getting started|get started|onboard\w*)\b",
        A,
        "Start with the platform overview, then the quickstarts and the documentation.",
        ("OVERVIEW", "QUICKSTARTS", "DEVELOPER-DOCS"),
    ),
)


def match(text: str) -> ApprovedRoute | None:
    """The first approved route whose pattern matches the (normalised) message, or None."""
    t = normalize(text).lower()
    for route in ROUTES:
        if route.pattern.search(t):
            return route
    return None
