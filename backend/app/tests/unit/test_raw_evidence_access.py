"""Raw supporting evidence stays out of reach (ZID-VERIFY-001 VERIFY-22; ZID-EVIDENCE-001 §14, §21).

Raw documents and biometric material are encrypted objects in the evidence vault. Reading one is an R3, E3
action (`evidence.object.read`). Today no API route reads raw objects and the verification module never
stores or reads them (providers keep raw material; Zoiko keeps normalized results and commitments), so a
support user or engineer has no path to them. This test keeps it that way: a route that starts reading raw
objects must call the protected-action gate for exactly that action.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml
import zid_platform

SRC = Path(zid_platform.__file__).parent
CATALOG = SRC.parents[3] / "contracts/action-catalog/actions.yaml"
READERS = {"read_object", "store_object"}


def _calls(path: Path) -> list[tuple[str, ast.AST]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""), n)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    ]


def test_the_raw_evidence_read_action_is_the_highest_class_in_the_catalogue() -> None:
    """VERIFY-22: reading a raw object is R3 with E3 evidence, so it can never be a casual support action."""
    action = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["actions"]["evidence.object.read"]
    assert action["min_risk_class"] == "R3" and action["min_evidence_class"] == "E3"


def test_no_api_route_reads_raw_evidence_without_the_protected_gate() -> None:
    """VERIFY-22: any API module that reads or stores raw objects must call protected_action for the read action."""
    offenders = []
    for path in sorted((SRC / "modules").glob("*/api/*.py")):
        names = {name for name, _ in _calls(path)}
        if names & READERS and not (
            "protected_action" in names
            and "evidence.object.read" in path.read_text(encoding="utf-8")
        ):
            offenders.append(path.relative_to(SRC).as_posix())
    assert not offenders, f"routes touch raw evidence without evidence.object.read: {offenders}"


def test_the_verification_module_never_stores_or_reads_raw_evidence_objects() -> None:
    """VERIFY-22: normalised results and commitments only; the raw document and biometric stay with the provider.

    ONE exception, pinned (CR-125): on a route that people decide there is no provider and no chip, so the evidence module keeps the document photograph (`evidence.application.review_photo`). The verification
    module may reach it only through that port, only from `idv_media.py`, and still never calls the vault itself or names an evidence object."""
    allowed = {"modules/verification/application/idv_media.py": {"keep", "read"}}
    for path in sorted((SRC / "modules/verification").rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        calls = {name for name, _ in _calls(path)}
        assert not calls & READERS, rel
        assert "evidence_object" not in path.read_text(encoding="utf-8"), path.name
        text = path.read_text(encoding="utf-8")
        if "review_photo" in text:
            assert rel in allowed, f"{rel} reaches the review-photo port: only idv_media.py may"


def test_the_review_photo_port_is_reached_by_one_module_and_opened_only_under_the_raw_read_action() -> (
    None
):
    """The pinned exception has two conditions: only `idv_media.py` imports the port, and the API opens a photograph only after the protected `evidence.object.read` act."""
    users = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "review_photo" in p.read_text(encoding="utf-8")
        and "evidence/application/review_photo.py" not in p.as_posix()
        and "evidence/ports.py" not in p.as_posix()
    ]
    assert users == ["modules/verification/application/idv_media.py"], users
    api = (SRC / "modules/verification/api/idv_cases.py").read_text(encoding="utf-8")
    assert 'action="evidence.object.read"' in api and "verification.idv.view_document" not in api
