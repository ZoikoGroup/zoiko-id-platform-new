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


def test_keygen_makes_a_working_ed25519_pair_and_never_overwrites_a_release_key(tmp_path: Path, package: Path) -> None:
    priv, pub = tmp_path / "k.pem", tmp_path / "k.pub.pem"
    key_id = rel.generate_keypair(priv, pub)
    assert key_id.startswith("sha-256:") and priv.read_bytes().startswith(b"-----BEGIN " + b"PRIVATE KEY-----") and pub.read_bytes().startswith(b"-----BEGIN PUBLIC KEY-----")
    release = rel.sign(rel.build_manifest(package, "zoiko-x", "1.0.0"), priv.read_bytes())
    assert release["key_id"] == key_id and rel.verify(release, pub.read_bytes(), package) == []  # what the new pair signs, the new public key verifies
    with pytest.raises(FileExistsError):  # replacing a release key by accident would orphan every earlier signature
        rel.generate_keypair(priv, tmp_path / "other.pub.pem")
    with pytest.raises(FileExistsError):
        rel.generate_keypair(tmp_path / "other.pem", pub)
    assert not (tmp_path / "other.pem").exists()  # a refused run leaves nothing behind


def test_the_declared_version_is_read_from_each_kind_of_package(tmp_path: Path) -> None:
    py = tmp_path / "py"
    py.mkdir()
    (py / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "2.3.4"\n', encoding="utf-8")
    js = tmp_path / "js"
    js.mkdir()
    (js / "package.json").write_text('{"name": "x", "version": "1.2.3"}', encoding="utf-8")
    net = tmp_path / "net"
    net.mkdir()
    (net / "X.csproj").write_text("<Project><PropertyGroup><Version>0.9.1</Version></PropertyGroup></Project>", encoding="utf-8")
    assert (rel.declared_version(py), rel.declared_version(js), rel.declared_version(net)) == ("2.3.4", "1.2.3", "0.9.1")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        rel.declared_version(empty)


def test_the_release_workflow_is_manual_signs_before_it_publishes_and_keeps_secrets_in_one_approved_environment() -> None:
    import yaml

    wf = yaml.safe_load((REPO / ".github" / "workflows" / "sdk-release.yml").read_text(encoding="utf-8"))
    triggers = wf.get("on") or wf.get(True)
    assert set(triggers) == {"workflow_dispatch"}  # no push, no pull request: a fork or a stray commit cannot start a release
    jobs = wf["jobs"]
    assert jobs["sign"]["needs"] == "build" and jobs["publish"]["needs"] == "sign"  # publishing needs a signature, signing needs a clean build
    assert jobs["publish"]["if"] == "inputs.publish" and jobs["build"]["if"] == "vars.SDK_RELEASE_ENABLED == 'true'"
    assert jobs["sign"]["environment"] == "sdk-release" and jobs["publish"]["environment"] == "sdk-release"  # the approval gate
    text = (REPO / ".github" / "workflows" / "sdk-release.yml").read_text(encoding="utf-8")
    assert "secrets." not in str(jobs["build"])  # the build job, which runs the package's own tests and scripts, never sees a key or a token
    assert text.count("secrets.SDK_SIGNING_KEY") == 1 and "id-token: write" not in str(jobs["build"]) + str(jobs["sign"])
    sign_steps = " ".join(str(s.get("run", "")) for s in jobs["sign"]["steps"])
    assert "shred -u" in sign_steps and "sdk-signing-public.pem" in sign_steps  # the key file is removed and the result is checked with the public half
    publish_steps = " ".join(str(s.get("run", "")) for s in jobs["publish"]["steps"])
    assert publish_steps.index("sdk_release.py verify") < publish_steps.index("uv publish")  # the files are re-checked immediately before they leave
    assert "--provenance" in publish_steps
