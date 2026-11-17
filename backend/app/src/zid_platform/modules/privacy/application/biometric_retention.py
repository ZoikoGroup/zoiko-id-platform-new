"""Which biometric asset classes a tenant has not given a retention period for (ZID-DTV-WF-04 B05, BIO-08; CR-108).

Read inside the caller's tenant transaction: retention policies are tenant data behind row-level security. A class counts as covered by an ACTIVE
policy whose object type is the class id and whose purpose is this purpose or none (every purpose).
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.application.control import CONFIG, biometric_classes
from zid_platform.modules.privacy.domain import biometric_assets as ba
from zid_platform.modules.privacy.domain import biometric_use_cases as uc


async def missing_biometric_retention(
    conn: AsyncConnection,
    method: str,
    purpose: str,
    *,
    classes: tuple[ba.AssetClass, ...] | None = None,
) -> tuple[str, ...]:
    classes = classes if classes is not None else biometric_classes()
    needed = ba.required(classes, method)
    if not needed:
        return ()
    rows = (
        await conn.execute(
            text(
                "SELECT DISTINCT object_type FROM privacy.retention_policy WHERE status = 'ACTIVE' AND object_type = ANY(:t) "
                "AND (purpose_code IS NULL OR purpose_code = :p)"
            ),
            {"t": list(needed), "p": purpose},
        )
    ).all()
    return ba.missing(classes, method, (r.object_type for r in rows))


@lru_cache(maxsize=1)
def use_cases() -> tuple[uc.UseCase, ...]:
    """config/privacy/biometric-use-cases.yaml (CR-110, BIO-19)."""
    return uc.parse(
        yaml.safe_load((CONFIG / "biometric-use-cases.yaml").read_text(encoding="utf-8")) or {}
    )


def use_case_problems(purpose: str, *, today: date) -> list[str]:
    """Why a Zoiko product's purpose may not use a biometric method now (not certified, or not yet effective); empty otherwise."""
    return uc.problems(use_cases(), purpose, today=today)
