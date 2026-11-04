"""Attach a piece of evidence to the go-live board: a signed PDF, a supplier sandbox run, a device-test report.

    python tools/scripts/record_evidence.py REF PATH --by "Name, role" --rows CL-003,G0,legal_assessment:GB [--note "..."]

It copies nothing and approves nothing. It records, in deploy/release/evidence-manifest.json, that the file at PATH (inside this repository) existed with this SHA-256, who attached it, when,
and which rows it is offered for. The reference REF is then what an owner writes into the register (ratification.json) or the country registry as the recorded reference; the board shows it
as ATTACHED, and as CHANGED if the file is ever edited afterwards. A row is closed by its owners recording the reference, never by this tool.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy" / "release" / "evidence-manifest.json"


def record(
    ref: str, path: pathlib.Path, by: str, rows: list[str], note: str = ""
) -> dict[str, object]:
    target = path.resolve()
    if ROOT not in target.parents or not target.is_file():
        raise SystemExit(f"{path} is not a file inside the repository")
    if not ref.strip() or not by.strip() or not rows:
        raise SystemExit("a reference, who attached it and at least one row are required")
    book = (
        json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"evidence": []}
    )
    if any(e["ref"] == ref for e in book["evidence"]):
        raise SystemExit(f"{ref} is already in the manifest; use a new reference for a new version")
    entry = {
        "ref": ref,
        "path": target.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "attached_by": by,
        "attached_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "rows": rows,
        "note": note,
    }
    book["evidence"].append(entry)
    MANIFEST.write_text(json.dumps(book, indent=2) + "\n", encoding="utf-8")
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ref")
    ap.add_argument("path", type=pathlib.Path)
    ap.add_argument("--by", required=True)
    ap.add_argument(
        "--rows",
        required=True,
        help="comma-separated: CL-003, G0, E03, NG-04, legal_assessment:GB ...",
    )
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    e = record(a.ref, a.path, a.by, [r.strip() for r in a.rows.split(",") if r.strip()], a.note)
    print(
        f"attached {e['ref']} sha256 {e['sha256'][:16]}...; now write the reference into the register or the country registry"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
