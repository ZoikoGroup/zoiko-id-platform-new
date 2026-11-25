"""Content-as-code compiles into one digest-pinned snapshot; production refuses sample or unsigned content (INV-02, ADR-03)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from assistant_helpers import make_settings
from zid_assistant import content_tool
from zid_assistant.application.snapshot import (
    ContentError,
    build_knowledge,
    compute_digest,
    load_knowledge,
    sign_digest,
    verify_digest,
)
from zid_assistant.settings import AssistantSettings

CONTENT = AssistantSettings().content_dir


@pytest.fixture
def content(tmp_path: Path) -> Path:
    dst = tmp_path / "content"
    shutil.copytree(CONTENT, dst)
    return dst


def test_launch_content_shape() -> None:
    k = build_knowledge(CONTENT, allow_local_http=False)
    assert len(k.intents) == 18 and len(k.answers) == 29
    assert len(k.snapshot.capabilities) == 25
    assert k.snapshot.sample is True
    # nothing is approved by existing (CONTENT-005): every answer is a draft, every capability unreleased
    assert {a.state.value for a in k.answers.values()} == {"DRAFT_NOT_PUBLISHED"}
    assert {c.status.value for c in k.snapshot.capabilities.values()} == {"EVIDENCE_PENDING"}
    assert not k.snapshot.facts, "no commercial fact is approved at launch (P05)"
    assert not any(c.approved for c in k.snapshot.claims.values()), (
        "no sensitive claim is approved at launch (P04)"
    )


def test_digest_is_stable_and_changes_with_content(content: Path) -> None:
    d1 = compute_digest(content)
    assert d1 == compute_digest(content)
    p = content / "p05_commercial.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    assert compute_digest(content) != d1


def test_digest_ignores_line_endings(content: Path) -> None:
    d1 = compute_digest(content)
    p = content / "copy.yaml"
    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert compute_digest(content) == d1


def test_signature_roundtrip_and_tamper_detection(tmp_path: Path, content: Path) -> None:
    priv, pub, sig = tmp_path / "p.pem", tmp_path / "q.pem", tmp_path / "s.sig"
    assert content_tool.main(["keygen", str(priv), str(pub)]) == 0
    assert content_tool.main(["sign", str(priv), str(sig), str(content)]) == 0
    assert content_tool.main(["verify", str(pub), str(sig), str(content)]) == 0
    p = content / "answers.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    assert (
        content_tool.main(["verify", str(pub), str(sig), str(content)]) == 1
    )  # a changed byte breaks the signature
    assert verify_digest("abc", sign_digest("abc", priv.read_bytes()), pub.read_bytes())
    assert not verify_digest("abd", sign_digest("abc", priv.read_bytes()), pub.read_bytes())
    assert not verify_digest("abc", "not-base64!!", pub.read_bytes())


def test_production_refuses_a_sample_snapshot() -> None:
    with pytest.raises(ContentError, match="engineering sample"):
        load_knowledge(make_settings(environment="production"))


def test_production_refuses_an_unsigned_snapshot(content: Path) -> None:
    m = content / "manifest.yaml"
    doc = yaml.safe_load(m.read_text(encoding="utf-8"))
    doc["sample"] = False
    m.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ContentError, match="not signed"):
        load_knowledge(make_settings(environment="production", content_dir=content))


def test_a_signed_non_sample_snapshot_is_accepted_in_production(
    tmp_path: Path, content: Path
) -> None:
    m = content / "manifest.yaml"
    doc = yaml.safe_load(m.read_text(encoding="utf-8"))
    doc["sample"] = False
    m.write_text(yaml.safe_dump(doc), encoding="utf-8")
    priv, pub, sig = tmp_path / "p.pem", tmp_path / "q.pem", tmp_path / "s.sig"
    content_tool.main(["keygen", str(priv), str(pub)])
    content_tool.main(["sign", str(priv), str(sig), str(content)])
    k = load_knowledge(
        make_settings(
            environment="production",
            content_dir=content,
            snapshot_signature_file=sig,
            snapshot_public_key_file=pub,
        )
    )
    assert k.snapshot.signed and not k.snapshot.sample


def test_a_bad_signature_stops_startup(tmp_path: Path, content: Path) -> None:
    priv, pub, sig = tmp_path / "p.pem", tmp_path / "q.pem", tmp_path / "s.sig"
    content_tool.main(["keygen", str(priv), str(pub)])
    sig.write_text("AAAA\n", encoding="utf-8")
    with pytest.raises(ContentError, match="signature"):
        load_knowledge(
            make_settings(
                content_dir=content, snapshot_signature_file=sig, snapshot_public_key_file=pub
            )
        )


def test_cross_reference_errors_are_all_reported(content: Path) -> None:
    p = content / "answers.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["answers"][0]["cta_link_id"] = "NO-SUCH-LINK"
    doc["answers"][1]["source_ids"] = ["PUB-999"]
    doc["answers"][2]["claim_ids"] = ["CL-99"]
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ContentError) as e:
        build_knowledge(content, allow_local_http=False)
    joined = " | ".join(e.value.problems)
    assert "NO-SUCH-LINK" in joined and "PUB-999" in joined and "CL-99" in joined


def test_destinations_must_be_https_with_no_credentials(content: Path) -> None:
    p = content / "p03_destinations.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["destinations"][0]["url"] = "http://example.com/x"
    doc["destinations"][1]["url"] = "https://user:pw@example.com/x"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ContentError) as e:
        build_knowledge(content, allow_local_http=False)
    assert len(e.value.problems) == 2
    # local/test may use an http localhost origin, but never one with credentials
    doc["destinations"][0]["url"] = "http://localhost:3000/x"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ContentError) as e2:
        build_knowledge(content, allow_local_http=True)
    assert len(e2.value.problems) == 1


def test_unknown_keys_and_duplicates_are_rejected(content: Path) -> None:
    p = content / "p01_capabilities.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["capabilities"][0]["surprise"] = True
    doc["capabilities"].append(dict(doc["capabilities"][1]))
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ContentError) as e:
        build_knowledge(content, allow_local_http=False)
    assert any("duplicate" in x for x in e.value.problems) and any(
        "p01" in x for x in e.value.problems
    )


def test_validate_command_succeeds_on_shipped_content() -> None:
    assert content_tool.main(["validate"]) == 0
