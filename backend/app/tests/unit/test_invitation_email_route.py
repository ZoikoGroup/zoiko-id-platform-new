"""The invitation email (ZID-EM-004) points at the /join page, and the token it carries has the shape that page expects."""

from __future__ import annotations

import re

from zid_platform.modules.communication.application.config import DEFAULT_ROUTES
from zid_platform.modules.communication.domain.catalogue import FieldKind, field_kind
from zid_platform.modules.communication.infrastructure.catalogue_file import load as load_catalogue


def test_the_invitation_template_is_in_the_catalogue_with_its_one_link_field() -> None:
    template = next(t for t in load_catalogue() if t.id == "ZID-EM-004")
    assert template.event == "tenant.member.invited"
    assert template.action is not None and template.action.url_field == "single_use_invitation_url"
    assert field_kind("single_use_invitation_url") is FieldKind.URL_TOKEN  # one token, carried as ?t=


def test_the_link_is_routed_to_the_join_page_so_the_template_can_be_sent() -> None:
    assert DEFAULT_ROUTES["single_use_invitation_url"] == "/join"  # a template whose link has no route is never sendable


def test_a_workspace_name_has_no_underscore_so_the_token_splits_in_one_place() -> None:
    slug_rule = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")  # tenant_slug: lower-case letters, digits and inner hyphens
    token = re.compile(r"^[A-Za-z0-9_\-]{16,512}$")  # what a URL token may be
    for slug in ("acme", "tesco-plc", "bp-technology-ventures-limited-a1b2c3d4"):
        assert slug_rule.match(slug) and "_" not in slug
        assert token.match(f"{slug}_{'T' * 43}")  # a workspace and a long ticket fit in one token
