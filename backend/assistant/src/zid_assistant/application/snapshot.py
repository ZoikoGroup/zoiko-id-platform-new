"""Compile content-as-code into one immutable, digest-pinned, optionally signed snapshot (ADR-03; CSR-008 section 02).

`load_knowledge(settings)` is the only way the service obtains knowledge. In a production-like environment it REFUSES to start
with a sample or unsigned snapshot (INV-02): there is no code path that serves unsigned content as approved.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import ValidationError

from zid_assistant.domain.content import (
    Answer,
    Capability,
    ClaimClass,
    CommercialFact,
    CopyItem,
    Destination,
    Intent,
    Manifest,
    Source,
    StarterPrompt,
)
from zid_assistant.domain.eligibility import Snapshot
from zid_assistant.settings import AssistantSettings

FILES = (
    "manifest.yaml",
    "intents.yaml",
    "answers.yaml",
    "p01_capabilities.yaml",
    "p02_sources.yaml",
    "p03_destinations.yaml",
    "p04_claims.yaml",
    "p05_commercial.yaml",
    "copy.yaml",
)
_LOCAL_HTTP = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/|$)")


class ContentError(Exception):
    """The content failed validation. Carries every problem found, so one run shows them all."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "; ".join(problems[:10])
            + (f" (+{len(problems) - 10} more)" if len(problems) > 10 else "")
        )
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Knowledge:
    snapshot: Snapshot
    manifest: Manifest
    intents: dict[str, Intent]
    answers: dict[str, Answer]
    copy: dict[str, CopyItem]
    starters: tuple[StarterPrompt, ...]

    def copy_text(self, key: str, default: str = "") -> str:
        item = self.copy.get(key)
        return item.text if item else default


def _read(path: Path) -> Any:
    if not path.exists():
        raise ContentError([f"missing content file: {path.name}"])
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def compute_digest(content_dir: Path) -> str:
    """sha256 over every content file in a fixed order, with line endings normalized (stable across OSes)."""
    h = hashlib.sha256()
    for name in FILES:
        p = content_dir / name
        data = p.read_bytes().replace(b"\r\n", b"\n") if p.exists() else b""
        h.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def _models[T](
    model: type[T], rows: list[dict[str, Any]], where: str, problems: list[str]
) -> list[T]:
    out: list[T] = []
    for i, row in enumerate(rows or []):
        try:
            out.append(model(**row))
        except (ValidationError, TypeError) as e:
            problems.append(f"{where}[{i}]: {str(e).splitlines()[0][:160]}")
    return out


def _unique[T](items: list[T], key: str, where: str, problems: list[str]) -> dict[str, T]:
    out: dict[str, T] = {}
    for it in items:
        k = getattr(it, key)
        if k in out:
            problems.append(f"{where}: duplicate id {k}")
        out[k] = it
    return out


def _url_ok(url: str, allow_local_http: bool) -> bool:
    parts = urlsplit(url)
    if parts.username or parts.password:
        return False
    if parts.scheme == "https" and parts.hostname:
        return True
    return bool(allow_local_http and _LOCAL_HTTP.match(url))


def build_knowledge(
    content_dir: Path, *, allow_local_http: bool, signed: bool = False
) -> Knowledge:
    """Parse and cross-validate the content. Raises `ContentError` with every problem found."""
    problems: list[str] = []
    manifest_raw = _read(content_dir / "manifest.yaml")
    try:
        manifest = Manifest(**manifest_raw)
    except (ValidationError, TypeError) as e:
        raise ContentError([f"manifest.yaml: {e}"]) from e

    intents = _unique(
        _models(
            Intent, _read(content_dir / "intents.yaml").get("intents", []), "intents", problems
        ),
        "intent_id",
        "intents",
        problems,
    )
    answers = _unique(
        _models(
            Answer, _read(content_dir / "answers.yaml").get("answers", []), "answers", problems
        ),
        "answer_id",
        "answers",
        problems,
    )
    caps = _unique(
        _models(
            Capability,
            _read(content_dir / "p01_capabilities.yaml").get("capabilities", []),
            "p01",
            problems,
        ),
        "capability_id",
        "p01",
        problems,
    )
    sources = _unique(
        _models(
            Source, _read(content_dir / "p02_sources.yaml").get("sources", []), "p02", problems
        ),
        "source_id",
        "p02",
        problems,
    )
    dests = _unique(
        _models(
            Destination,
            _read(content_dir / "p03_destinations.yaml").get("destinations", []),
            "p03",
            problems,
        ),
        "link_id",
        "p03",
        problems,
    )
    claims = _unique(
        _models(
            ClaimClass, _read(content_dir / "p04_claims.yaml").get("claims", []), "p04", problems
        ),
        "claim_id",
        "p04",
        problems,
    )
    facts = _unique(
        _models(
            CommercialFact,
            _read(content_dir / "p05_commercial.yaml").get("facts", []),
            "p05",
            problems,
        ),
        "fact_id",
        "p05",
        problems,
    )
    copy_raw = _read(content_dir / "copy.yaml")
    copy = _unique(
        _models(CopyItem, copy_raw.get("copy", []), "copy", problems), "key", "copy", problems
    )
    starters = tuple(_models(StarterPrompt, copy_raw.get("starters", []), "starters", problems))

    for d in dests.values():
        if not _url_ok(d.url, allow_local_http):
            problems.append(
                f"p03 {d.link_id}: url must be https (or localhost http in local/test), no credentials"
            )
    for a in answers.values():
        if a.intent_id not in intents:
            problems.append(f"answer {a.answer_id}: unknown intent {a.intent_id}")
        if a.cta_link_id and a.cta_link_id not in dests:
            problems.append(f"answer {a.answer_id}: unknown link {a.cta_link_id}")
        problems += [
            f"answer {a.answer_id}: unknown source {s}" for s in a.source_ids if s not in sources
        ]
        problems += [
            f"answer {a.answer_id}: unknown claim {c}" for c in a.claim_ids if c not in claims
        ]
        problems += [
            f"answer {a.answer_id}: unknown capability {c}"
            for c in a.capability_ids
            if c not in caps
        ]
    for i in intents.values():
        if i.cta_link_id and i.cta_link_id not in dests:
            problems.append(f"intent {i.intent_id}: unknown link {i.cta_link_id}")
        if i.refusal_key and i.refusal_key not in copy:
            problems.append(f"intent {i.intent_id}: unknown copy key {i.refusal_key}")
    for s in sources.values():
        problems += [
            f"source {s.source_id}: unknown claim {c}"
            for c in s.claim_ids
            if c.lstrip("!") not in claims
        ]
        problems += [
            f"source {s.source_id}: unknown capability {c}"
            for c in s.eligible_capability_ids
            if c not in caps
        ]
    for c in copy.values():
        if c.link_id and c.link_id not in dests:
            problems.append(f"copy {c.key}: unknown link {c.link_id}")
    if problems:
        raise ContentError(problems)

    snapshot = Snapshot(
        snapshot_id=manifest.snapshot_id,
        digest=compute_digest(content_dir),
        sample=manifest.sample,
        signed=signed,
        capabilities=caps,
        sources=sources,
        destinations=dests,
        claims=claims,
        facts=facts,
    )
    return Knowledge(snapshot, manifest, intents, answers, copy, starters)


# ---------------------------------------------------------------------------------------------- signing
def sign_digest(digest: str, private_key_pem: bytes) -> str:
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("the publisher key must be Ed25519")
    return base64.b64encode(key.sign(digest.encode())).decode()


def verify_digest(digest: str, signature_b64: str, public_key_pem: bytes) -> bool:
    pub = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(pub, Ed25519PublicKey):
        return False
    try:
        pub.verify(base64.b64decode(signature_b64), digest.encode())
    except (InvalidSignature, ValueError):
        return False
    return True


def load_knowledge(settings: AssistantSettings) -> Knowledge:
    """Load the content for this process, enforcing the production rules (INV-02)."""
    allow_http = not settings.is_production_like
    k = build_knowledge(settings.content_dir, allow_local_http=allow_http)
    signed = False
    if settings.snapshot_signature_file and settings.snapshot_public_key_file:
        sig = settings.snapshot_signature_file.read_text(encoding="utf-8").strip()
        signed = verify_digest(
            k.snapshot.digest, sig, settings.snapshot_public_key_file.read_bytes()
        )
        if not signed:
            raise ContentError(["snapshot signature does not verify against the publisher key"])
    if settings.is_production_like:
        if k.snapshot.sample:
            raise ContentError(
                ["refusing to start: the snapshot is an engineering sample (manifest.sample: true)"]
            )
        if not signed:
            raise ContentError(["refusing to start: the snapshot is not signed by the publisher"])
    elif k.snapshot.sample and not settings.allow_sample_snapshot:
        raise ContentError(
            ["sample snapshots are not allowed here (ZID_ASSISTANT_ALLOW_SAMPLE_SNAPSHOT=false)"]
        )
    return Knowledge(
        replace(k.snapshot, signed=signed), k.manifest, k.intents, k.answers, k.copy, k.starters
    )
