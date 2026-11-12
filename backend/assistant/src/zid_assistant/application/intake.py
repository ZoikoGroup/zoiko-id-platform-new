"""Intake service: the separate, consented contact flow (INT-010; ADR-09; INV-05). Disabled until OD-009..015 are decided.

Flow: config gate (flags, country policy, signed notice) -> validate (schema, DLP) -> idempotency -> ONE transaction that stores the
encrypted fields and an outbox event -> 202 with an opaque reference. The reference is returned only after durable acceptance and
promises nothing about delivery, response time or an agent. The transcript is never an input to this service.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass

from cryptography.fernet import Fernet

from zid_assistant.domain import intake as dom
from zid_assistant.infrastructure.flags import Flags
from zid_assistant.infrastructure.store import IntakeRow, OutboxRow, Store
from zid_assistant.settings import AssistantSettings

ADAPTER_REV = "adapter-v1"
_B32 = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


@dataclass(frozen=True, slots=True)
class Accepted:
    reference: str
    status: str = "ACCEPTED"


def new_reference() -> str:
    return "ZR-" + "".join(secrets.choice(_B32) for _ in range(8))


class IntakeCrypto:
    """Field-level encryption for the only table that holds personal data. The deployed key comes from a file (the broker, DEC-F5);
    a local run without a key file generates an ephemeral one, so local intake is readable only until the process exits."""

    def __init__(self, settings: AssistantSettings) -> None:
        if settings.intake_encryption_key_file is not None:
            key = settings.intake_encryption_key_file.read_text(encoding="utf-8").strip().encode()
        elif settings.is_production_like:
            raise RuntimeError("an intake encryption key file is required outside local/test")
        else:
            key = Fernet.generate_key()
        self._f = Fernet(key)

    def seal(self, data: dict[str, str]) -> bytes:
        return self._f.encrypt(json.dumps(data, sort_keys=True).encode())

    def open(self, blob: bytes) -> dict[str, str]:
        return dict(json.loads(self._f.decrypt(blob)))


class IntakeService:
    def __init__(
        self,
        store: Store,
        settings: AssistantSettings,
        crypto: IntakeCrypto,
        notices: dict[str, tuple[str, str]],
    ) -> None:
        self._store = store
        self._s = settings
        self._crypto = crypto
        # purpose -> (notice_id, notice_text): supplied by the signed privacy schedule (OD-009/011). Empty = intake unavailable.
        self._notices = notices

    # ------------------------------------------------------------------ config
    def available(self, purpose: str, flags: Flags, country: str | None = None) -> bool:
        on = flags.sales_on if purpose == "SALES" else flags.support_on
        return bool(
            on
            and purpose in self._notices
            and self._countries()
            and (country is None or country in self._countries())
        )

    def _countries(self) -> set[str]:
        return set(getattr(self._s, "intake_countries", []) or [])

    def config(self, flags: Flags) -> dict[str, object]:
        def block(purpose: str) -> dict[str, object]:
            if not self.available(purpose, flags):
                return {"available": False, "form_revision": None}
            nid, ntext = self._notices[purpose]
            return {
                "available": True,
                "form_revision": dom.form_revision(purpose),
                "notice_id": nid,
                "notice_text": ntext,
                "countries": sorted(self._countries()),
                "fields": [
                    {
                        "name": f.name,
                        "type": f.type,
                        "required": f.required,
                        "max_length": f.max_length,
                        "options": list(f.options),
                    }
                    for f in dom.specs(purpose)
                ],
                "inquiry_types": list(dom.SALES_INQUIRY_TYPES) if purpose == "SALES" else [],
                "topics": list(dom.SUPPORT_TOPICS) if purpose == "SUPPORT" else [],
            }

        return {
            "sales": block("SALES"),
            "support": block("SUPPORT"),
            "marketing": {"available": flags.marketing_on and bool(self._countries())},
        }

    # ------------------------------------------------------------------ submit
    async def submit(
        self, purpose: str, body: dict[str, object], idem_key: str, flags: Flags
    ) -> Accepted:
        country = dom.validate_country(body.get("country_code"))
        if not self.available(purpose, flags, country):
            raise dom.IntakeError("REGION_OR_POLICY_BLOCK")
        nid, _ntext = self._notices[purpose]
        if (
            body.get("form_revision") != dom.form_revision(purpose)
            or body.get("notice_accepted") is not True
        ):
            raise dom.IntakeError(
                "INVALID_REQUEST", "notice_accepted"
            )  # consent is explicit and tied to the live notice revision
        if set(body) - {
            "form_revision",
            "purpose",
            "country_code",
            "locale",
            "notice_accepted",
            "fields",
        }:
            raise dom.IntakeError("INVALID_REQUEST")
        fields = dom.validate_fields(purpose, body.get("fields"))
        digest = hashlib.sha256(
            json.dumps({"p": purpose, "c": country, "f": fields}, sort_keys=True).encode()
        ).hexdigest()

        existing = await self._store.intake_by_key(idem_key)
        if existing is not None:
            if existing.payload_digest != digest or existing.purpose != purpose:
                raise dom.IntakeError("IDEMPOTENCY_CONFLICT")
            return Accepted(
                existing.reference
            )  # same key, same payload: same reference, nothing duplicated

        request_id = str(uuid.uuid4())
        row = IntakeRow(
            request_id,
            purpose,
            country,
            dom.form_revision(purpose),
            nid,
            idem_key,
            digest,
            self._crypto.seal(fields),
            new_reference(),
        )
        await self._store.create_intake(
            row,
            OutboxRow(str(uuid.uuid4()), request_id, f"{purpose.lower()}-receiver", ADAPTER_REV),
        )
        await self._store.audit("INTAKE_ACCEPTED", purpose, ADAPTER_REV, None, "ACCEPTED")
        return Accepted(row.reference)

    async def record_marketing(self, body: dict[str, object], flags: Flags) -> None:
        country = dom.validate_country(body.get("country_code"))
        if (
            not flags.marketing_on
            or country not in self._countries()
            or body.get("channel") != "EMAIL"
            or not isinstance(body.get("election"), bool)
        ):
            raise dom.IntakeError("REGION_OR_POLICY_BLOCK")
        notice = body.get("notice_id")
        if not isinstance(notice, str) or notice not in {n for n, _ in self._notices.values()}:
            raise dom.IntakeError("INVALID_REQUEST", "notice_id")
        await self._store.record_marketing("EMAIL", country, notice, bool(body["election"]))


def new_idempotency_digest(b: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(b).digest()).decode()
