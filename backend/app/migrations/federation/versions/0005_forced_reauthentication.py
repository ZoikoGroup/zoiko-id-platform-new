"""Forced re-authentication at the identity provider (ZID-ENG-DEC-009 T07).

Revision ID: federation_0005
Revises: federation_0004
Create Date: 2026-11-04

A login transaction can say the person must authenticate again at the identity provider (OIDC `max_age=0` and `prompt=login`; SAML `ForceAuthn`). The
callback then refuses a response whose authentication time is older than the transaction, so a cached IdP session cannot satisfy it.
"""

from zid_platform.migrations import execute_script as _run

revision = "federation_0005"
down_revision = "federation_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE authn.federation_login ADD COLUMN force_reauth boolean NOT NULL DEFAULT false;
    """)
