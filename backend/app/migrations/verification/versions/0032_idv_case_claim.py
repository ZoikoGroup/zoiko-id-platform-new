"""The identity claim an approved proofing case leaves for the rest of the platform (ZID-DTV-WF-03; CR-125).

Revision ID: verification_0032
Revises: verification_0031
Create Date: 2026-11-18

idv_case_claim: when a case is APPROVED under a market whose rules set a validity period, one claim is recorded for its subject: IDENTITY_VERIFIED_FOR_SCOPE, with the GRADE it was earned at
(CHIP_VERIFIED, or REVIEWED_NO_CHIP for a route that people decide), the market and the pinned rules, and the date it stops counting. Screening and company verification read it beside the verification-session
assertions. A market that sets no validity period leaves no claim. A rights request revokes it. Row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0032"
down_revision = "verification_0031"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.idv_case_claim (
            claim_id             uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            case_id              uuid NOT NULL UNIQUE REFERENCES verify.idv_case (case_id),
            subject_principal_id uuid NOT NULL,
            claim_type           text NOT NULL CHECK (claim_type = 'IDENTITY_VERIFIED_FOR_SCOPE'),
            grade                text NOT NULL CHECK (grade IN ('CHIP_VERIFIED', 'REVIEWED_NO_CHIP')),
            market               text NOT NULL,
            policy_version       text NOT NULL,
            policy_hash          text NOT NULL,
            performed_at         timestamptz NOT NULL,
            valid_until          timestamptz NOT NULL,
            status               text NOT NULL DEFAULT 'VALID' CHECK (status IN ('VALID', 'REVOKED')),
            revoked_at           timestamptz,
            revoke_reason        text,
            CHECK (valid_until > performed_at),
            CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL))
        );
        CREATE INDEX idv_case_claim_subject ON verify.idv_case_claim (subject_principal_id, status, valid_until);
        ALTER TABLE verify.idv_case_claim ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.idv_case_claim FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.idv_case_claim USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.idv_case_claim;")
