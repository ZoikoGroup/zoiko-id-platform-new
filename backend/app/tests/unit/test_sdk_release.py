"""The SDK release signing tool (ZID-CP-WF10 v2.0, WF-10.06; CR-173): a tampered package, manifest or signature is refused by name. Uses a throwaway key made here; nothing is a secret."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

REPO = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("sdk_release", REPO / "tools" / "scripts" / "sdk_release.py")
assert spec and spec.loader
rel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rel)  # type: ignore[union-attr]


def keypair() -> tuple[bytes, bytes]:
    k = ed25519.Ed25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, pub


@pytest.fixture()
def package(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1"\ndependencies = ["cryptography>=42"]\n', encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_bytes(b"x")
    return root


def signed(package: Path) -> tuple[dict[str, Any], bytes]:
    priv, pub = keypair()
    return rel.sign(rel.build_manifest(package, "zoiko-x", "1.0.0"), priv), pub


def test_a_clean_release_verifies_and_the_manifest_lists_files_and_declared_dependencies_only(package: Path) -> None:
    release, pub = signed(package)
    assert rel.verify(release, pub, package) == []
    paths = [f["path"] for f in release["manifest"]["files"]]
    assert paths == ["README.md", "pyproject.toml", "src/a.py"] and not any("pycache" in p for p in paths)
    assert release["manifest"]["declared_dependencies"] == [{"ecosystem": "pypi", "declared": "cryptography>=42"}] and "not a vulnerability scan" in release["manifest"]["note"]
    assert release["algorithm"] == "Ed25519" and release["manifest_digest"].startswith("sha-256:")


def test_every_kind_of_tampering_is_refused_by_name(package: Path) -> None:
    release, pub = signed(package)
    (package / "src" / "a.py").write_text("print('evil')\n", encoding="utf-8")
    assert rel.verify(release, pub, package) == ["FILE_CHANGED:src/a.py"]  # a changed file
    (package / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (package / "extra.py").write_text("x\n", encoding="utf-8")
    assert rel.verify(release, pub, package) == ["FILE_ADDED:extra.py"]  # a file nobody signed
    (package / "extra.py").unlink()
    (package / "README.md").unlink()
    assert rel.verify(release, pub, package) == ["FILE_MISSING:README.md"]
    (package / "README.md").write_text("hello\n", encoding="utf-8")

    altered = json.loads(json.dumps(release))
    altered["manifest"]["files"][0]["sha256"] = "sha-256:" + "0" * 64
    assert {"MANIFEST_DIGEST_DIFFERS", "SIGNATURE_DOES_NOT_VERIFY"} <= set(rel.verify(altered, pub, package))  # an edited manifest
    forged = {**release, "signature": release["signature"][:-4] + "AAAA"}
    assert "SIGNATURE_DOES_NOT_VERIFY" in rel.verify(forged, pub, package)
    _, other_pub = keypair()
    assert "SIGNED_BY_ANOTHER_KEY" in rel.verify(release, other_pub, package)  # a release signed by someone else's key
    assert rel.verify({"format": "nope"}, pub, package) == ["NOT_A_RELEASE"]


def test_only_an_ed25519_key_signs_and_an_empty_package_has_no_release(package: Path, tmp_path: Path) -> None:
    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = ec_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with pytest.raises(ValueError, match="Ed25519"):
        rel.sign(rel.build_manifest(package, "x", "1"), pem)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no files"):
        rel.build_manifest(empty, "x", "1")


def test_the_command_line_round_trips_and_exits_non_zero_when_a_file_changed(package: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    priv, pub = keypair()
    (tmp_path / "k.pem").write_bytes(priv)
    (tmp_path / "p.pem").write_bytes(pub)
    m, r = tmp_path / "m.json", tmp_path / "r.json"
    assert rel.main(["manifest", str(package), "--name", "zoiko-x", "--version", "1.0.0", "--out", str(m)]) == 0
    assert rel.main(["sign", str(m), "--key", str(tmp_path / "k.pem"), "--out", str(r)]) == 0
    assert rel.main(["verify", str(r), str(package), "--key", str(tmp_path / "p.pem")]) == 0 and "OK" in capsys.readouterr().out
    (package / "README.md").write_text("changed\n", encoding="utf-8")
    assert rel.main(["verify", str(r), str(package), "--key", str(tmp_path / "p.pem")]) == 1 and "FILE_CHANGED:README.md" in capsys.readouterr().out
