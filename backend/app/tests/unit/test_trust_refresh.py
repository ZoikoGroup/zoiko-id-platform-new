"""The operator tool that fills `config/trust/` from the EU lists: nothing is written unless the list of lists verifies against the signer the
operator pinned, and each national list is verified against the signers the VERIFIED list of lists names. A forged list is never written."""

from __future__ import annotations

from pathlib import Path

from pdf_signing import NOW, list_of_lists_xml, make_cert, pki, rsa_key, trusted_list_xml
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.infrastructure import trust_refresh as rf

LOTL = "https://lotl.example.test/eu-lotl.xml"


def setup():
    lotl_signer = make_cert("EU LOTL Signer", rsa_key())
    de_signer, fr_signer = (
        make_cert("DE TL Signer", rsa_key()),
        make_cert("FR TL Signer", rsa_key()),
    )
    de_pki, fr_pki = pki(), pki()
    files = {
        LOTL: list_of_lists_xml(
            [
                ("DE", "https://de.example.test/tl.xml", de_signer),
                ("FR", "https://fr.example.test/tl.xml", fr_signer),
                (
                    "PL",
                    "http://insecure.example.test/tl.xml",
                    de_signer,
                ),  # not https: never followed
            ],
            sign_with=lotl_signer,
        ),
        "https://de.example.test/tl.xml": trusted_list_xml(de_pki.root, sign_with=de_signer),
        # France's list is signed by someone who is NOT the signer the list of lists names for France
        "https://fr.example.test/tl.xml": trusted_list_xml(
            fr_pki.root, sign_with=make_cert("Forger", rsa_key())
        ),
    }

    def fetch(url: str) -> bytes:
        if url not in files:
            raise ValueError("unreachable")
        return files[url]

    return lotl_signer, de_pki, fr_pki, files, fetch


def test_only_lists_that_verify_are_written_and_a_forged_one_is_reported(tmp_path: Path) -> None:
    lotl_signer, _, _, _, fetch = setup()
    r = rf.refresh(tmp_path, lotl_signer.cert, fetch=fetch, lotl_url=LOTL, now=NOW)
    assert r.lotl_verified and r.written == ["DE"]
    assert r.failed == {"FR": "LIST_SIGNATURE_NOT_VERIFIED"}, (
        "the forged list is not written, and says why"
    )
    assert not (tmp_path / "trusted-lists" / "FR.xml").exists()
    assert not (tmp_path / "trusted-lists" / "PL.xml").exists(), "an http pointer is never fetched"
    cfg = tr.load_trust(tmp_path)
    assert [t.name for t in cfg.trusted_lists] == ["DE.xml"] and cfg.trusted_lists[0].authenticated
    assert cfg.anchors == (), "anchors are written only when asked for"


def test_anchors_are_the_granted_qualified_cas_and_only_on_request(tmp_path: Path) -> None:
    lotl_signer, de_pki, _, _, fetch = setup()
    r = rf.refresh(
        tmp_path, lotl_signer.cert, fetch=fetch, lotl_url=LOTL, anchors_from_lists=True, now=NOW
    )
    assert r.anchors == 1
    cfg = tr.load_trust(tmp_path)
    assert cfg.anchors == (de_pki.root.cert,)


def test_a_list_of_lists_not_signed_by_the_pinned_signer_writes_nothing(tmp_path: Path) -> None:
    _, _, _, _, fetch = setup()
    stranger = make_cert("Someone Else", rsa_key())
    r = rf.refresh(tmp_path, stranger.cert, fetch=fetch, lotl_url=LOTL, now=NOW)
    assert r.lotl_verified is False and "LOTL" in r.failed
    assert not (tmp_path / "trusted-lists").exists() and not any(tmp_path.iterdir())


def test_an_unreachable_list_of_lists_is_reported_and_one_bad_list_does_not_stop_the_others(
    tmp_path: Path,
) -> None:
    lotl_signer, _, _, _files, fetch = setup()

    def flaky(url: str) -> bytes:
        if url.startswith("https://de."):
            raise OSError("down")
        return fetch(url)

    r = rf.refresh(tmp_path, lotl_signer.cert, fetch=flaky, lotl_url=LOTL, now=NOW)
    assert r.failed["DE"].startswith("download failed") and "FR" in r.failed and r.lotl_verified
    gone = rf.refresh(
        tmp_path / "x",
        lotl_signer.cert,
        fetch=lambda u: (_ for _ in ()).throw(OSError("no network")),
        lotl_url=LOTL,
    )
    assert gone.lotl_verified is False and gone.failed["LOTL"].startswith("download failed")


def test_pointers_from_an_unauthenticated_list_of_lists_are_never_followed() -> None:
    lotl_signer, _, _, files, _ = setup()
    assert tr.list_pointers(files[LOTL], []) == ()
    assert len(tr.list_pointers(files[LOTL], [lotl_signer.cert])) == 2, (
        "the http pointer is dropped"
    )


def test_the_downloader_refuses_anything_but_https() -> None:
    import pytest

    with pytest.raises(ValueError):
        rf.https_fetch("http://example.test/tl.xml")
    with pytest.raises(ValueError):
        rf.https_fetch("file:///etc/passwd")
