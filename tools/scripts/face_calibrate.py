"""Measure the face engine on a folder of photographs and print the threshold for each false-match target (CR-130).

    python tools/scripts/face_calibrate.py PHOTO_FOLDER [--face-url http://127.0.0.1:8091] [--max-different 20000] [--seed 1]

PHOTO_FOLDER holds one sub-folder per person, each with two or more photographs of that person (the layout public sets such as LFW use):

    PHOTO_FOLDER/Alice/1.jpg  Alice/2.jpg   Bob/1.jpg  Bob/2.jpg ...

Every pair of photographs of one person is a `same` pair. `different` pairs are drawn at random across two people, up to --max-different. Each pair goes to the face service's /match and only its
cosine similarity is kept: no photograph leaves the machine, none is written anywhere, and the report holds scores and counts only. Use photographs of people who agreed to this.

The result is a measurement on THIS set. It is not an evaluation of the route (document photographs against live selfies, other populations, presentation attacks)."""

from __future__ import annotations

import argparse
import base64
import itertools
import json
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "backend" / "app" / "src"))

from zid_platform.modules.verification.domain import face_calibration as fc  # noqa: E402

EXT = {".jpg", ".jpeg", ".png"}


def photos_by_person(root: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        files = sorted(f for f in d.iterdir() if f.suffix.lower() in EXT)
        if files:
            out[d.name] = files
    return out


def score(url: str, a: Path, b: Path) -> float | None:
    body = json.dumps({"probe_base64": base64.b64encode(a.read_bytes()).decode(), "reference_base64": base64.b64encode(b.read_bytes()).decode()}).encode()
    req = urllib.request.Request(f"{url}/match", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310  (the owner's own face service)
            return float(json.load(r)["cosine"])
    except urllib.error.HTTPError as e:
        if e.code == 422:
            return None  # no face, or more than one: the pair is skipped, never scored as zero
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--face-url", default="http://127.0.0.1:8091")
    ap.add_argument("--max-different", type=int, default=20000)
    ap.add_argument("--max-same", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    people = photos_by_person(a.folder)
    rng = random.Random(a.seed)
    same_pairs = [pair for files in people.values() for pair in itertools.combinations(files, 2)]
    rng.shuffle(same_pairs)
    same_pairs = same_pairs[: a.max_same]
    names = [n for n, f in people.items() if f]
    diff_pairs: list[tuple[Path, Path]] = []
    while len(diff_pairs) < a.max_different and len(names) >= 2:
        x, y = rng.sample(names, 2)
        diff_pairs.append((rng.choice(people[x]), rng.choice(people[y])))
        if len(diff_pairs) >= len(names) * len(names):
            break
    pairs: list[fc.Pair] = []
    skipped = 0
    for kind, items in ((True, same_pairs), (False, diff_pairs)):
        for i, (p, q) in enumerate(items, 1):
            s = score(a.face_url, p, q)
            if s is None:
                skipped += 1
            else:
                pairs.append(fc.Pair(s, kind))
            if i % 200 == 0:
                print(f"  {'same' if kind else 'different'}: {i}/{len(items)}", file=sys.stderr)
    try:
        result = fc.report(pairs)
    except fc.CalibrationError as e:
        print(f"Cannot measure this set: {e.code}", file=sys.stderr)
        return 2
    result["pairs_skipped_no_single_face"] = skipped
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
