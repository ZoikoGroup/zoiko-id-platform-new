"""W3C VC Data Model 2.0 (VC-JOSE-COSE) verification and Bitstring Status List.

Revision ID: credential_0003
Revises: credential_0002
Create Date: 2026-10-13

ZID-VC-001 §9, §13. A configuration may now accept VC_JWT (vc+jwt credentials enveloped in a
holder-signed vp+jwt) and check status through a Bitstring Status List; each format keeps the
status mechanism it actually has (SD-JWT VC / mdoc: Token Status List; VC_JWT: Bitstring).
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0003"
down_revision = "credential_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE cred.credential_configuration
            DROP CONSTRAINT credential_configuration_format_check,
            DROP CONSTRAINT credential_configuration_status_method_check,
            ADD CONSTRAINT credential_configuration_format_check
                CHECK (format IN ('SD_JWT_VC', 'MDOC', 'VC_JWT')),
            ADD CONSTRAINT credential_configuration_status_method_check
                CHECK (status_method IN ('TOKEN_STATUS_LIST', 'BITSTRING_STATUS_LIST', 'NONE')),
            ADD CONSTRAINT credential_configuration_status_fits_format
                CHECK (status_method = 'NONE'
                       OR (format = 'VC_JWT') = (status_method = 'BITSTRING_STATUS_LIST'));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE cred.credential_configuration
            DROP CONSTRAINT credential_configuration_status_fits_format,
            DROP CONSTRAINT credential_configuration_status_method_check,
            DROP CONSTRAINT credential_configuration_format_check,
            ADD CONSTRAINT credential_configuration_format_check
                CHECK (format IN ('SD_JWT_VC', 'MDOC')),
            ADD CONSTRAINT credential_configuration_status_method_check
                CHECK (status_method IN ('TOKEN_STATUS_LIST', 'NONE'));
    """)
