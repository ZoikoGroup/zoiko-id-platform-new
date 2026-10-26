"""Compatibility gate for the published contracts (ZID-API-001 §3, ZID-EVENTS-001 §3, ZID-SVC-001 §24).

    python3 tools/scripts/contract_compat.py OLD_DIR NEW_DIR [--accepted FILE]

OLD_DIR and NEW_DIR each hold a `contracts/` tree (the base branch's and this change's). The gate refuses a
change that breaks a consumer inside a minor release:

OpenAPI (every `openapi/**/*.json`)
  - an operation or a success status code is removed;
  - a parameter or request property is added as required, removed, retyped, or loses an enum value
    (requests use `extra=forbid`, so a removed property is a break for senders that still use it);
  - a response property is removed, retyped, or turns from required into optional;
  - a response enum gains a value (unsafe enum expansion: an old client may not know it), unless the schema is
    marked `x-open-enum: true`, which tells clients to treat unknown values as unknown.
AsyncAPI (`asyncapi/channels/*.json`)
  - an event is removed, its CloudEvents type (and so its major version) changes, or it moves lane (a priority
    event moving to the domain lane loses its delivery guarantee);
  - the CloudEvents envelope loses a property, retypes one, or gains a required one.

A deliberate break is allowed only when `--accepted` (default `contracts/baseline/accepted-contract-changes.json`)
lists that exact finding with a change id and a named approver, which keeps the decision in review. Standard
library only; it reads files and never a network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SUCCESS = ("200", "201", "202", "204")
METHODS = ("get", "put", "post", "delete", "patch")


def _resolve(doc: dict[str, Any], node: Any, seen: tuple[str, ...] = ()) -> Any:
    """Follow local `$ref`s (components only); a cycle stops at the reference itself."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return {}
        seen = (*seen, ref)
        target: Any = doc
        for part in ref[2:].split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        node = target
    return node


def _types(node: dict[str, Any]) -> frozenset[str]:
    t = node.get("type")
    if t is None and "anyOf" in node:
        return frozenset(
            str(x.get("type", "any")) for x in node["anyOf"] if isinstance(x, dict) and "type" in x
        ) or frozenset({"any"})
    if isinstance(t, list):
        return frozenset(str(x) for x in t)
    return frozenset({str(t)}) if t is not None else frozenset({"any"})


def schema_breaks(
    old_doc: dict[str, Any],
    new_doc: dict[str, Any],
    old: Any,
    new: Any,
    where: str,
    *,
    direction: str,
    depth: int = 0,
) -> list[str]:
    """Findings for one schema pair. `direction` is "request" (we receive) or "response" (we send)."""
    old, new = _resolve(old_doc, old), _resolve(new_doc, new)
    if depth > 8 or not isinstance(old, dict) or not isinstance(new, dict):
        return []
    out: list[str] = []
    if _types(old) != _types(new) and "any" not in _types(old) | _types(new):
        narrowed = direction == "request" and _types(new) >= _types(old)
        if (
            not narrowed
        ):  # a request that accepts more types is compatible; anything else is a break
            out.append(f"TYPE_CHANGED {where}: {sorted(_types(old))} -> {sorted(_types(new))}")
    old_enum, new_enum = old.get("enum"), new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed, added = (
            set(map(str, old_enum)) - set(map(str, new_enum)),
            set(map(str, new_enum)) - set(map(str, old_enum)),
        )
        if direction == "request" and removed:
            out.append(f"ENUM_VALUE_REMOVED {where}: {sorted(removed)}")
        if direction == "response" and added and not new.get("x-open-enum"):
            out.append(f"UNSAFE_ENUM_EXPANSION {where}: +{sorted(added)}")
    old_props, new_props = old.get("properties") or {}, new.get("properties") or {}
    if isinstance(old_props, dict) and isinstance(new_props, dict):
        old_req, new_req = set(old.get("required") or ()), set(new.get("required") or ())
        for name in sorted(old_props):
            if name not in new_props:
                out.append(f"PROPERTY_REMOVED {where}.{name}")
                continue
            out += schema_breaks(
                old_doc,
                new_doc,
                old_props[name],
                new_props[name],
                f"{where}.{name}",
                direction=direction,
                depth=depth + 1,
            )
        if direction == "request":
            for name in sorted((new_req - old_req) & set(new_props)):
                out.append(f"REQUIRED_PROPERTY_ADDED {where}.{name}")
        else:
            for name in sorted((old_req - new_req) & set(old_props)):
                out.append(f"REQUIRED_PROPERTY_BECAME_OPTIONAL {where}.{name}")
    if "items" in old and "items" in new:
        out += schema_breaks(
            old_doc,
            new_doc,
            old["items"],
            new["items"],
            f"{where}[]",
            direction=direction,
            depth=depth + 1,
        )
    return out


def _json_schema(doc: dict[str, Any], container: Any) -> Any:
    container = _resolve(doc, container)
    content = container.get("content") if isinstance(container, dict) else None
    if isinstance(content, dict):
        for media in ("application/json", "application/cloudevents+json"):
            if media in content:
                return content[media].get("schema", {})
    return {}


def breaking_openapi(old: dict[str, Any], new: dict[str, Any], file: str = "") -> list[str]:
    out: list[str] = []
    tag = f"{file}: " if file else ""
    for path, item in sorted((old.get("paths") or {}).items()):
        new_item = (new.get("paths") or {}).get(path) or {}
        for method in METHODS:
            if method not in item:
                continue
            op_name = f"{method.upper()} {path}"
            if method not in new_item:
                out.append(f"{tag}REMOVED_OPERATION {op_name}")
                continue
            old_op, new_op = item[method], new_item[method]
            old_params = {
                (p["in"], p["name"]): p
                for p in map(lambda x: _resolve(old, x), old_op.get("parameters", []))
            }
            new_params = {
                (p["in"], p["name"]): p
                for p in map(lambda x: _resolve(new, x), new_op.get("parameters", []))
            }
            for key, p in sorted(new_params.items()):
                if key not in old_params and p.get("required"):
                    out.append(f"{tag}REQUIRED_PARAMETER_ADDED {op_name} {key[0]}:{key[1]}")
                elif (
                    key in old_params and p.get("required") and not old_params[key].get("required")
                ):
                    out.append(f"{tag}PARAMETER_BECAME_REQUIRED {op_name} {key[0]}:{key[1]}")
            for key, p in sorted(old_params.items()):
                if key in new_params:
                    out += [
                        f"{tag}{b} ({op_name} parameter {key[1]})"
                        for b in schema_breaks(
                            old,
                            new,
                            p.get("schema", {}),
                            new_params[key].get("schema", {}),
                            key[1],
                            direction="request",
                        )
                    ]
            out += [
                f"{tag}{b} ({op_name} request)"
                for b in schema_breaks(
                    old,
                    new,
                    _json_schema(old, old_op.get("requestBody", {})),
                    _json_schema(new, new_op.get("requestBody", {})),
                    "body",
                    direction="request",
                )
            ]
            if new_op.get("requestBody", {}).get("required") and not old_op.get(
                "requestBody", {}
            ).get("required"):
                if old_op.get("requestBody") is not None:
                    out.append(f"{tag}REQUEST_BODY_BECAME_REQUIRED {op_name}")
            for code in SUCCESS:
                if code in (old_op.get("responses") or {}):
                    if code not in (new_op.get("responses") or {}):
                        out.append(f"{tag}SUCCESS_RESPONSE_REMOVED {op_name} {code}")
                        continue
                    out += [
                        f"{tag}{b} ({op_name} {code})"
                        for b in schema_breaks(
                            old,
                            new,
                            _json_schema(old, old_op["responses"][code]),
                            _json_schema(new, new_op["responses"][code]),
                            "response",
                            direction="response",
                        )
                    ]
    return out


def breaking_asyncapi(old: dict[str, Any], new: dict[str, Any], file: str = "") -> list[str]:
    out: list[str] = []
    tag = f"{file}: " if file else ""
    old_msgs = (old.get("components") or {}).get("messages") or {}
    new_msgs = (new.get("components") or {}).get("messages") or {}

    def lanes(doc: dict[str, Any]) -> dict[str, str]:
        found: dict[str, str] = {}
        for channel, body in (doc.get("channels") or {}).items():
            for mid in body.get("messages") or {}:
                found[mid] = str(channel).rsplit(".", 1)[-1]
        return found

    old_lanes, new_lanes = lanes(old), lanes(new)
    for mid, message in sorted(old_msgs.items()):
        if mid not in new_msgs:
            out.append(f"{tag}EVENT_REMOVED {message.get('title', mid)}")
            continue
        if new_msgs[mid].get("name") != message.get("name"):
            out.append(
                f"{tag}EVENT_TYPE_CHANGED {message.get('title', mid)}: {message.get('name')} -> {new_msgs[mid].get('name')}"
            )
        if old_lanes.get(mid) != new_lanes.get(mid):
            out.append(
                f"{tag}EVENT_LANE_CHANGED {message.get('title', mid)}: {old_lanes.get(mid)} -> {new_lanes.get(mid)}"
            )
    old_env = (old.get("components") or {}).get("schemas", {}).get("CloudEvent", {})
    new_env = (new.get("components") or {}).get("schemas", {}).get("CloudEvent", {})
    out += [
        f"{tag}{b} (CloudEvents envelope)"
        for b in schema_breaks(old, new, old_env, new_env, "envelope", direction="response")
    ]
    for name in sorted(set(new_env.get("required") or ()) - set(old_env.get("required") or ())):
        out.append(f"{tag}ENVELOPE_REQUIRED_FIELD_ADDED {name}")
    return out


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def compare_dirs(old_dir: Path, new_dir: Path) -> list[str]:
    out: list[str] = []
    for old_file in sorted((old_dir / "openapi").rglob("*.json")):
        rel = old_file.relative_to(old_dir)
        new_file = new_dir / rel
        if not new_file.exists():
            out.append(f"{rel.as_posix()}: REMOVED_CONTRACT_FILE")
            continue
        out += breaking_openapi(_load(old_file), _load(new_file), rel.as_posix())
    for old_file in sorted((old_dir / "asyncapi" / "channels").glob("*.json")):
        rel = old_file.relative_to(old_dir)
        new_file = new_dir / rel
        if not new_file.exists():
            out.append(f"{rel.as_posix()}: REMOVED_CONTRACT_FILE")
            continue
        out += breaking_asyncapi(_load(old_file), _load(new_file), rel.as_posix())
    return out


def unaccepted(findings: list[str], accepted: list[dict[str, Any]]) -> list[str]:
    """Findings not covered by an accepted change: it must name the finding, a change id and an approver."""
    ok = {
        str(a["finding"])
        for a in accepted
        if a.get("finding") and a.get("change_id") and a.get("approved_by")
    }
    return [f for f in findings if f not in ok]


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument(
        "--accepted", default=str(root / "contracts/baseline/accepted-contract-changes.json")
    )
    args = ap.parse_args(argv)
    accepted_file = Path(args.accepted)
    accepted = (
        json.loads(accepted_file.read_text(encoding="utf-8")) if accepted_file.exists() else []
    )
    findings = unaccepted(compare_dirs(Path(args.old), Path(args.new)), accepted)
    for f in findings:
        print(f"BREAKING {f}")
    print(f"contract compatibility: {len(findings)} breaking change(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
