"""Biometric release attestation (ZID-DTV-WF-04 QA-041, B11; CR-113).

An approved step up of a biometric method scope is a release decision (`decision_hash`, CR-110). This binds it to the exact build that was running
when it was approved and to the capture SDK the clients use, so the signed evidence names the provider or engine, the model version, the purpose,
the country AND the build:

    ZID_RELEASE_BUILD             the running build (an image digest or a commit); "unrecorded" when the deployment does not say
    ZID_BIOMETRIC_CAPTURE_SDK     the capture SDK version the clients use; "unrecorded" when the deployment does not say
    ZID_BIOMETRIC_RELEASE_BINDING report (default): a live scope approved for another build or SDK is reported on the eligibility answer
                                  (`release_drift`); enforce: it is also refused (RELEASE_BUILD_CHANGED, RELEASE_SDK_CHANGED, RELEASE_NOT_ATTESTED)
                                  until someone approves it again for the running build.

The attestation row is the privacy module's and append-only (migration privacy_0011): a new build is a new approval, never an edit.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import biometric_methods as bm

UNRECORDED = "unrecorded"
LIVE = (bm.State.PILOT_GATED, bm.State.PRODUCTION_ELIGIBLE)


@dataclass(frozen=True, slots=True)
class Release:
    build_ref: str
    sdk_version: str


@dataclass(frozen=True, slots=True)
class Attestation:
    scope_id: str
    version: int
    decision_hash: str
    build_ref: str
    sdk_version: str
    attested_by: str
    attested_at: datetime
    attestation_hash: str

    def view(self) -> dict[str, Any]:
        return {
            "scope_ref": f"{self.scope_id}:v{self.version}",
            "decision_hash": self.decision_hash,
            "build_ref": self.build_ref,
            "sdk_version": self.sdk_version,
            "attested_at": self.attested_at.isoformat(),
            "attestation_hash": self.attestation_hash,
        }


def _clean(value: str | None, limit: int) -> str:
    v = (value or "").strip()
    return v[:limit] if v else UNRECORDED


def running(env: Mapping[str, str] | None = None) -> Release:
    e = os.environ if env is None else env
    return Release(
        _clean(e.get("ZID_RELEASE_BUILD"), 200), _clean(e.get("ZID_BIOMETRIC_CAPTURE_SDK"), 100)
    )


def binding_mode(env: Mapping[str, str] | None = None) -> str:
    e = os.environ if env is None else env
    return (
        "enforce"
        if (e.get("ZID_BIOMETRIC_RELEASE_BINDING") or "").strip().lower() == "enforce"
        else "report"
    )


def attestation_hash(
    *, scope_id: str, version: int, decision_hash: str, release: Release, by: str
) -> str:
    body = {
        "scope_id": scope_id,
        "version": version,
        "decision_hash": decision_hash,
        "build_ref": release.build_ref,
        "sdk_version": release.sdk_version,
        "attested_by": by,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def drift(state: bm.State, attestation: Attestation | None, release: Release) -> tuple[str, ...]:
    """Pure: why a LIVE scope's release decision does not cover what is running now. Nothing for a scope that is not live. A build or SDK the
    deployment does not record cannot be compared and is not called a change (it is reported by `running`)."""
    if state not in LIVE:
        return ()
    if attestation is None:
        return ("RELEASE_NOT_ATTESTED",)
    out = []
    if (
        UNRECORDED not in (release.build_ref, attestation.build_ref)
        and release.build_ref != attestation.build_ref
    ):
        out.append("RELEASE_BUILD_CHANGED")
    if (
        UNRECORDED not in (release.sdk_version, attestation.sdk_version)
        and release.sdk_version != attestation.sdk_version
    ):
        out.append("RELEASE_SDK_CHANGED")
    return tuple(out)


async def attest(
    conn: AsyncConnection,
    record: bm.Record,
    *,
    by: str,
    now: datetime,
    release: Release | None = None,
) -> Attestation:
    """Written in the approval's own transaction. Only an approved step up carries a decision hash, so only it is attested."""
    if not record.decision_hash:
        raise bm.MethodError("METHOD_RELEASE_NOT_SIGNED", "only an approved step up is attested")
    rel = release or running()
    a = Attestation(
        scope_id=record.scope_id,
        version=record.version,
        decision_hash=record.decision_hash,
        build_ref=rel.build_ref,
        sdk_version=rel.sdk_version,
        attested_by=by,
        attested_at=now,
        attestation_hash=attestation_hash(
            scope_id=record.scope_id,
            version=record.version,
            decision_hash=record.decision_hash,
            release=rel,
            by=by,
        ),
    )
    from zid_platform.modules.privacy.ports import record_release_attestation

    await record_release_attestation(
        conn,
        scope_id=a.scope_id,
        version=a.version,
        decision_hash=a.decision_hash,
        build_ref=a.build_ref,
        sdk_version=a.sdk_version,
        attested_by=a.attested_by,
        attested_at=a.attested_at,
        attestation_hash=a.attestation_hash,
    )
    return a


async def attestation_for(conn: AsyncConnection, scope_id: str, version: int) -> Attestation | None:
    """The attestation the version in force rests on: its own, or that of the approval it narrowed or paused from (kept by the privacy module)."""
    from zid_platform.modules.privacy.ports import release_attestation_for

    r = await release_attestation_for(conn, scope_id, version)
    return Attestation(**r) if r is not None else None


__all__ = [
    "UNRECORDED",
    "Attestation",
    "Release",
    "attest",
    "attestation_for",
    "attestation_hash",
    "binding_mode",
    "drift",
    "running",
]
