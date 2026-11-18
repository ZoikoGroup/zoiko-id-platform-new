"""Runtime configuration of the email estate (Communication Estate §07 release decision register).

Everything the copybook says "must be resolved before any production send" is a setting here, and the
default of each is the safe one: email is off, no template is enabled, and a template whose links have no
configured route is never sent. Enabling is a deliberate act by the people the copybook names as approvers
(Security + Brand for the sender domain, Legal + Privacy for notices, CTO + Product for launch scope).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

from zid_platform.modules.communication.domain.catalogue import Template
from zid_platform.modules.communication.domain.policy import Limits
from zid_platform.modules.communication.domain.render import LinkPolicy

# Console pages that exist today. A link field with no entry here (support centre, status centre,
# incident centre, preference centre, wallet, consent centre, public pages) has no safe destination yet, so
# the templates that use it stay unsendable until an operator configures a route for it.
DEFAULT_ROUTES: Mapping[str, str] = {
    "secure_console_url": "/overview",
    "trusted_signin_url": "/sign-in",
    "trusted_sessions_url": "/account/sessions",
    "trusted_security_url": "/account",
    "trusted_account_status_url": "/account",
    "trusted_recovery_status_url": "/account/recovery",
    "trusted_connections_url": "/account/connected-apps",
    "trusted_proofing_url": "/account/verification",
    "trusted_privacy_url": "/account/privacy",
    "trusted_billing_url": "/admin/billing",
    "trusted_mandates_url": "/admin/mandates",
    "trusted_approval_url": "/admin/authority",
    "trusted_audit_url": "/admin/evidence",
    "trusted_developer_url": "/admin/developers",
    "trusted_support_url": "/help",
    # the sign-up verification page (WF-013A): the link carries only its single-use token, `?t=`
    "single_use_verification_url": "/sign-up/verify",
    # the emailed way back when a member cannot sign in: the page redeems `?t=` and runs the one-passkey enrollment
    "single_use_signin_url": "/recover/email",
    # a changed security email (ZID-EM-017, CR-114): the page confirms `?t=` for the signed-in person
    "single_use_email_change_url": "/account/email-change",
}

MARKETING_LISTS: Mapping[str, str] = {"ZID-EM-142": "product_updates"}
LEASE_SECONDS = 300  # a PENDING row older than this belongs to a worker that died mid-send
MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class EmailConfig:
    enabled: bool
    enabled_templates: frozenset[str] | None  # None = every template that is otherwise sendable
    links: LinkPolicy
    support_url: str
    sender_display_name: str
    limits: Limits
    retry_delays_s: tuple[float, ...] = (0.0, 3.0, 15.0)
    legal_entity: str | None = None
    mailing_address: str | None = None
    unsubscribe_base: str | None = None

    def allows(self, template: Template) -> bool:
        """Master switch, the per-template switch, and a destination for every link in the copy."""
        if not self.enabled:
            return False
        if self.enabled_templates is not None and template.id not in self.enabled_templates:
            return False
        return all(self.links.routed(f) for f in template.url_fields)

    @classmethod
    def from_settings(cls, s: Any) -> EmailConfig:
        base = (s.email_public_base_url or s.public_base_url or "").rstrip("/")
        routes = {**DEFAULT_ROUTES, **dict(s.email_routes)}
        links = LinkPolicy(
            base,
            routes,
            allow_localhost_http=s.environment in ("local", "test")
            or base.startswith("http://localhost"),
        )
        enabled = frozenset(s.email_enabled_templates)
        return cls(
            enabled=bool(s.email_enabled),
            enabled_templates=None if "*" in enabled else enabled,
            links=links,
            support_url=links.url_for("trusted_support_url", None),
            sender_display_name=parseaddr(s.email_from)[0] or "Zoiko iD",
            limits=Limits(s.email_flood_per_template_hour, s.email_flood_per_recipient_hour),
            legal_entity=s.email_legal_entity,
            mailing_address=s.email_mailing_address,
            unsubscribe_base=base,
        )


def sendable(config: EmailConfig, templates: Collection[Template]) -> list[str]:
    return sorted(t.id for t in templates if config.allows(t))
