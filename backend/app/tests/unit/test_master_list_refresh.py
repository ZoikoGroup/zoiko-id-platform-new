"""Scheduled CSCA master-list fetch (WF01, CR-114): it only stages, skips what it already holds, refuses private addresses, and ships with no source."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from zid_platform.modules.evidence.application import master_list_refresh as r
from zid_platform.modules.evidence.domain.trust_store import TrustStoreError

NOW = datetime(2026, 11, 18, tzinfo=UTC)
TENANT = "11111111-1111-1111-1111-111111111111"
BODY = b"master-list-bytes"


def source(**kw: Any) -> r.Source:
    base = {
        "id": "gb",
        "url": "https://publisher.example/ml",
        "tenant_id": TENANT,
        "anchors": (b"pem",),
        "anchor_sha256": ("ab" * 32,),
        "note": None,
    }
    return r.Source(**{**base, **kw})


class Fake:
    def __init__(self, body: bytes | None) -> None:
        self.body = body

    async def get(self, url: str) -> bytes | None:
        return self.body


@asynccontextmanager
async def in_tenant(conn: Any, tenant: str):
    conn.scopes.append(tenant)
    yield conn


class Conn:
    def __init__(self) -> None:
        self.scopes: list[str] = []


@pytest.fixture
def staged(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []

    async def listing(conn: Any) -> list[dict[str, Any]]:
        return held

    async def stage(conn: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        held.append({"package_sha256": hashlib.sha256(kw["master_list"]).hexdigest()})
        return {}

    monkeypatch.setattr(r.store, "listing", listing)
    monkeypatch.setattr(r.trust_store, "stage", stage)
    return calls


def run(sources: list[r.Source], body: bytes | None, conn: Conn | None = None) -> dict[str, int]:
    return asyncio.run(
        r.refresh_pass(
            conn or Conn(), NOW, sources=sources, fetcher=Fake(body), in_tenant=in_tenant
        )
    )  # type: ignore[arg-type]


def test_a_new_list_is_staged_under_the_system_actor_in_the_sources_tenant(
    staged: list[dict[str, Any]],
) -> None:
    conn = Conn()
    assert run([source()], BODY, conn) == {
        "staged": 1,
        "unchanged": 0,
        "unavailable": 0,
        "refused": 0,
    }
    call = staged[0]
    assert (
        call["actor"] == r.ACTOR == "system:master-list-refresh"
        and call["tenant_id"] == TENANT
        and conn.scopes == [TENANT]
    )
    assert call["source_uri"] == "https://publisher.example/ml" and call[
        "confirmed_anchor_sha256"
    ] == ("ab" * 32,)


def test_the_same_list_is_not_staged_twice_and_a_changed_one_is(
    staged: list[dict[str, Any]],
) -> None:
    conn = Conn()
    run([source()], BODY, conn)
    assert run([source()], BODY, conn)["unchanged"] == 1 and len(staged) == 1
    assert run([source()], BODY + b"!", conn)["staged"] == 1 and len(staged) == 2


def test_an_unavailable_source_stages_nothing(staged: list[dict[str, Any]]) -> None:
    assert run([source()], None)["unavailable"] == 1 and staged == []


def test_a_refused_staging_is_counted_and_does_not_stop_the_other_sources(
    monkeypatch: pytest.MonkeyPatch, staged: list[dict[str, Any]]
) -> None:
    async def refuse(conn: Any, **kw: Any) -> dict[str, Any]:
        if kw["source_uri"].endswith("/bad"):
            raise TrustStoreError("ANCHOR_FINGERPRINT_MISMATCH")
        staged.append(kw)
        return {}

    monkeypatch.setattr(r.trust_store, "stage", refuse)
    counts = run(
        [
            source(id="bad", url="https://publisher.example/bad"),
            source(id="ok", url="https://publisher.example/ok"),
        ],
        BODY,
    )
    assert counts["refused"] == 1 and counts["staged"] == 1


def test_this_module_never_validates_approves_or_activates() -> None:
    text = Path(r.__file__).read_text(encoding="utf8")
    for forbidden in (
        "trust_store.validate",
        "trust_store.approve",
        "trust_store.activate",
        "trust_store.rollback",
    ):
        assert forbidden not in text


def test_the_shipped_config_has_no_source_and_the_loader_returns_none() -> None:
    assert r.load() == []


def write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "pki").mkdir()
    (tmp_path / "pki" / "a.pem").write_bytes(b"pem")
    (tmp_path / r.FILE).write_text(body, encoding="utf8")
    return tmp_path


GOOD = "sources:\n  - {id: gb, url: 'https://publisher.example/ml', tenant_id: t1, anchor_files: [pki/a.pem], anchor_sha256: ['AB']}\n"


def test_a_good_source_loads_with_its_anchor_bytes_and_lowercase_fingerprints(
    tmp_path: Path,
) -> None:
    [s] = r.load(write(tmp_path, GOOD))
    assert s.id == "gb" and s.anchors == (b"pem",) and s.anchor_sha256 == ("ab",)


@pytest.mark.parametrize(
    "body",
    [
        GOOD.replace("https://publisher.example/ml", "http://127.0.0.1/ml"),
        GOOD.replace("https://publisher.example/ml", "http://10.0.0.5/ml"),
        GOOD.replace("https://publisher.example/ml", "file:///etc/passwd"),
        GOOD.replace(", anchor_sha256: ['AB']", ""),
        GOOD.replace("anchor_files: [pki/a.pem], ", ""),
        GOOD.replace("tenant_id: t1, ", ""),
        GOOD.replace("}\n", ", surprise: 1}\n"),
        GOOD + GOOD.split("\n", 1)[1],
    ],
)
def test_a_bad_source_refuses_the_whole_file(tmp_path: Path, body: str) -> None:
    with pytest.raises(r.ConfigError):
        r.load(write(tmp_path, body))
