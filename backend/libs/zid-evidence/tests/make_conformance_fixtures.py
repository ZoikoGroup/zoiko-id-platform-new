"""Generate the shared conformance vectors for the two independent verifiers (Python and TypeScript; CR-170, WF08 E05).

Run from the repository root with the backend environment:
    python backend/libs/zid-evidence/tests/make_conformance_fixtures.py

The vectors are written to sdks/typescript/test/fixtures/evidence-conformance.json. For each case the PYTHON verifier states which
checks pass and fail; the TypeScript test must reach the same answer, and a Python test asserts the file is current. Signatures are
Ed25519 and ES256 with throwaway keys made here: nothing in the file is a secret.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_evidence_bundle import ALG, bundle_for
from zid_crypto import LocalSigner
from zid_evidence import hash_json, verify_bundle
from zid_evidence.jcs import canonicalize

OUT = HERE.parents[3] / "sdks" / "typescript" / "test" / "fixtures" / "evidence-conformance.json"

JCS_VALUES: list[Any] = [
    {},
    [],
    {"b": 1, "a": [1, 2, {"z": None, "y": True}]},
    {"é": 1, "e": 2, "\U0001f600": 3, "ﬁ": 4},
    'line\nbreak "quoted" \\ back \u0001 control \u007f del',
    [
        0,
        -0.0,
        1.5,
        1e21,
        1e-7,
        123456789012345680000.0,
        0.000001,
        4.5e-7,
        9007199254740992,
        -9007199254740992,
    ],
    {"amount": 12.50, "ok": False, "nested": {"k": "v", "list": []}},
]


def _tamper(b: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(b)
    node: Any = out
    *parents, last = path.split(".")
    for p in parents:
        node = node[int(p)] if isinstance(node, list) else node[p]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return out


def build() -> dict[str, Any]:
    signer = LocalSigner("evidence-k1")
    other = LocalSigner("evidence-k1")  # same kid, different key material: a substituted key
    keys = [signer.public_jwk()]
    good = {n: bundle_for(i, signer, n) for n, i in ((1, 0), (2, 1), (5, 2), (8, 7))}
    base = good[5]
    cases: list[tuple[str, dict[str, Any], list[dict[str, Any]] | None, bool]] = [
        (f"valid, tree of {n}", b, keys, False) for n, b in good.items()
    ]
    withheld = copy.deepcopy(base)
    withheld["record"] = None
    cases += [
        ("record withheld: structural only", withheld, keys, False),
        ("keys taken from the bundle itself", base, None, False),
        ("tampered record", _tamper(base, "record.outcome", "DENY"), keys, False),
        ("tampered record id", _tamper(base, "record_id", "r999"), keys, False),
        (
            "tampered ledger entry time",
            _tamper(base, "ledger_entry.committed_at", "2030-01-01T00:00:00.000000Z"),
            keys,
            False,
        ),
        (
            "tampered entry hash",
            _tamper(base, "ledger_entry.entry_hash", "sha-256:" + "00" * 32),
            keys,
            False,
        ),
        (
            "tampered proof path",
            _tamper(base, "inclusion_proof.hashes.0", "sha-256:" + "11" * 32),
            keys,
            False,
        ),
        (
            "short proof path",
            _tamper(base, "inclusion_proof.hashes", base["inclusion_proof"]["hashes"][:-1]),
            keys,
            False,
        ),
        ("wrong leaf index", _tamper(base, "inclusion_proof.leaf_index", 0), keys, False),
        (
            "tampered merkle root",
            _tamper(base, "checkpoint.merkle_root", "sha-256:" + "22" * 32),
            keys,
            False,
        ),
        ("tampered checkpoint range", _tamper(base, "checkpoint.last_sequence", 99), keys, False),
        ("signature from another key", base, [other.public_jwk()], False),
        ("signing key not pinned", base, [LocalSigner("some-other-kid").public_jwk()], False),
        (
            "truncated signature",
            _tamper(base, "checkpoint.signature", base["checkpoint"]["signature"][:-4]),
            keys,
            False,
        ),
        ("unknown format", _tamper(base, "format", "zid-evidence-bundle/2"), keys, False),
        (
            "hash algorithm outside the allowlist",
            _tamper(base, "profile.hash_alg", "md5"),
            keys,
            False,
        ),
        (
            "a timestamp reference nobody can check",
            _tamper(base, "checkpoint.external_timestamp_ref", "AAAA"),
            keys,
            False,
        ),
        ("an anchor was required but none present", base, keys, True),
    ]
    out_cases = []
    for name, bundle, trusted, require_ts in cases:
        from zid_evidence.bundle import TimestampOptions

        report = verify_bundle(
            bundle, trusted_keys=trusted, timestamps=TimestampOptions(required=require_ts)
        )
        out_cases.append(
            {
                "name": name,
                "bundle": bundle,
                "trusted_keys": trusted,
                "require_timestamp": require_ts,
                "expected": {
                    "ok": report.ok,
                    "structural_only": report.structural_only,
                    "keys_source": report.keys_source,
                    "passed": [c.name for c in report.checks if c.ok],
                    "failed": [c.name for c in report.checks if not c.ok],
                },
            }
        )
    return {
        "about": "Shared conformance vectors for the Python and TypeScript evidence verifiers. Generated; do not edit.",
        "hash_alg": ALG,
        "jcs": [
            {
                "value": v,
                "canonical_hex": canonicalize(v).hex(),
                "sha256": str(hash_json(v, alg=ALG)),
            }
            for v in JCS_VALUES
        ],
        "bundles": out_cases,
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("wrote", OUT)
