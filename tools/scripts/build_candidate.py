"""Assemble the release-gate candidate record from what the pipeline just verified (see release_gate.py).

    build_candidate.py IMAGE@sha256:... PREPROD_DIGEST   # reads sig.json, prov.json, sbom-att.json, findings.json

Each input file exists only if its verification command succeeded (the pipeline runs with `set -e`), so an
absent or unparsable file leaves that section empty and the gate refuses. Standard library only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load(name: str) -> Any:
    try:
        return json.loads(Path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build(
    image: str, preprod_digest: str, sig: Any, prov: Any, sbom: Any, scan: Any
) -> dict[str, Any]:
    digest = image.split("@", 1)[1]
    candidate: dict[str, Any] = {"image": image, "preprod_digest": preprod_digest, "findings": []}

    if isinstance(sig, list) and sig:
        opt = sig[0].get("optional", {})
        candidate["signature"] = {
            "verified": True,
            "identity": opt.get("Subject"),
            "issuer": opt.get("Issuer"),
        }

    def _result(att: Any) -> dict[str, Any] | None:
        if isinstance(att, list) and att and "verificationResult" in att[0]:
            return att[0]["verificationResult"]
        return None

    p = _result(prov)
    if p:
        cert = p.get("signature", {}).get("certificate", {})
        subjects = p.get("statement", {}).get("subject", [])
        candidate["provenance"] = {
            "verified": True,
            "builder_id": cert.get("buildSignerURI"),
            "source_repo": cert.get("sourceRepositoryURI"),
            "source_ref": cert.get("sourceRepositoryRef"),
            "subject_digest": f"sha256:{subjects[0]['digest']['sha256']}" if subjects else None,
            "slsa_level": 3
            if "/.github/workflows/build-image.yml@" in (cert.get("buildSignerURI") or "")
            else 2,
        }
    s = _result(sbom)
    if s:
        subjects = s.get("statement", {}).get("subject", [])
        components = s.get("statement", {}).get("predicate", {}).get("components") or []
        candidate["sbom"] = {
            "format": "CycloneDX",
            "subject_digest": f"sha256:{subjects[0]['digest']['sha256']}"
            if subjects
            else digest + "-unbound",
            "components": [
                f"{c['name']}@{c.get('version', '')}" for c in components if c.get("name")
            ],
        }
    for result in (scan or {}).get("Results", []):
        for v in result.get("Vulnerabilities") or []:
            candidate["findings"].append({"id": v["VulnerabilityID"], "severity": v["Severity"]})
    return candidate


def main() -> int:
    image, preprod = sys.argv[1], sys.argv[2]
    out = build(
        image,
        preprod,
        _load("sig.json"),
        _load("prov.json"),
        _load("sbom-att.json"),
        _load("findings.json"),
    )
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
