"""PR-14 / AT-018: no invisible cross-brand or cross-tenant identity graph (ZID-PRI-004 section 15).

No fraud graph exists. This guard keeps it that way until one is designed with authorization: any table whose name says it links people, devices
or identities must be tenant-scoped (so a link can never join two tenants' records), and nothing may be shared across tenants by being marked
unscoped. A future graph has to come through here, where the author must decide on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OWNERSHIP = yaml.safe_load(
    (ROOT.parent / "database" / "table-ownership.yaml").read_text(encoding="utf-8")
)["tables"]
LINKISH = re.compile(
    r"(graph|edge|identity_link|subject_link|device_link|person_link|linked_|_link$|_links$|fingerprint|cluster)"
)
# Tables that match the pattern and are known not to link people: each is a reviewed exception with its reason.
REVIEWED = {}


def test_every_link_like_table_is_tenant_scoped_so_a_link_cannot_cross_tenants() -> None:
    offenders = [
        name
        for name, meta in OWNERSHIP.items()
        if LINKISH.search(name.split(".", 1)[-1])
        and name not in REVIEWED
        and not meta.get("tenant_scoped")
    ]
    assert offenders == [], (
        f"a link or graph table that is not tenant-scoped could join tenants: {offenders}"
    )


def test_no_migration_creates_an_unscoped_table_joining_two_subjects() -> None:
    """A table with two subject-like columns and no tenant column is how a cross-tenant edge would be built."""
    pair = re.compile(
        r"(subject|principal|person|identity|device)_?(a|1|from|src|left)\b", re.IGNORECASE
    )
    other = re.compile(
        r"(subject|principal|person|identity|device)_?(b|2|to|dst|right)\b", re.IGNORECASE
    )
    bad = []
    for path in (ROOT / "migrations").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r"CREATE TABLE\s+[\w.]+\s*\((.*?)\n\s*\);", text, flags=re.DOTALL):
            if pair.search(block) and other.search(block) and "tenant_id" not in block:
                bad.append(path.name)
    assert bad == [], f"an edge-like table with no tenant column: {bad}"


def test_the_guard_would_catch_an_unscoped_graph_table() -> None:
    fake = {"fraud.identity_edge": {"tenant_scoped": False}}
    hits = [
        n
        for n, m in fake.items()
        if LINKISH.search(n.split(".", 1)[-1]) and not m.get("tenant_scoped")
    ]
    assert hits == ["fraud.identity_edge"]
