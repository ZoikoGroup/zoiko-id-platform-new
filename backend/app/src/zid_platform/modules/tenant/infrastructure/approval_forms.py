"""The forms behind "New request" in the approvals inbox (`backend/config/approvals/request-forms.yaml`; slice L-13).

Loaded and checked against the approval registry, so a form can never offer something the server would refuse and no requestable action can
be left without a way to ask for it: every customer A1 or A2 action that has an executor has a form or a stated reason it is started
elsewhere. A file that breaks that does not start the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from zid_platform.modules.tenant.domain.approvals import Scope, Tier
from zid_platform.modules.tenant.infrastructure import approvals as approvals_store
from zid_platform.modules.tenant.infrastructure.catalog import CONFIG, _yaml

FIELD_KINDS = frozenset({"member", "owner", "text", "textarea", "select"})


class FormsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    label: str
    kind: str
    required: bool
    help: str | None
    max_length: int
    options: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Form:
    action_id: str
    tier: str
    label: str
    summary: str
    fields: tuple[Field, ...]
    target_from: str | None
    target_prefix: str
    target_literal: str | None

    def target_for(self, payload: dict[str, Any]) -> str:
        if self.target_literal is not None:
            return self.target_literal
        assert self.target_from is not None
        return f"{self.target_prefix}{payload[self.target_from]}"


def parse(doc: dict[str, Any], registry: Any) -> tuple[tuple[Form, ...], dict[str, str]]:
    forms: list[Form] = []
    seen: set[str] = set()
    for raw in doc.get("forms") or []:
        action_id = str(raw["action_id"])
        if action_id in seen:
            raise FormsError(f"{action_id}: two forms for one action")
        seen.add(action_id)
        policy = registry.actions.get(action_id)
        if policy is None:
            raise FormsError(f"{action_id}: not an action in the approval registry")
        if (
            policy.scope is not Scope.CUSTOMER
            or policy.tier not in (Tier.A1, Tier.A2)
            or not policy.executor
        ):
            raise FormsError(f"{action_id}: a customer cannot request this action here")
        fields: list[Field] = []
        keys: set[str] = set()
        for f in raw.get("fields") or []:
            kind = str(f["kind"])
            if kind not in FIELD_KINDS:
                raise FormsError(f"{action_id}.{f.get('key')}: unknown field kind {kind}")
            options = tuple((str(o["id"]), str(o["label"])) for o in (f.get("options") or ()))
            if kind == "select" and not options:
                raise FormsError(f"{action_id}.{f['key']}: a select needs options")
            keys.add(str(f["key"]))
            fields.append(
                Field(
                    str(f["key"]), str(f["label"]), kind, bool(f.get("required", False)),
                    str(f["help"]) if f.get("help") else None, int(f.get("max_length", 200)), options,
                )
            )  # fmt: skip
        target = raw.get("target") or {}
        literal = target.get("literal")
        source = target.get("from")
        if (literal is None) == (source is None):
            raise FormsError(
                f"{action_id}: a target is a literal or a field, never both or neither"
            )
        if source is not None and source not in keys:
            raise FormsError(f"{action_id}: the target names a field that is not on the form")
        if not fields:
            raise FormsError(f"{action_id}: a form without fields asks for nothing")
        forms.append(
            Form(
                action_id, policy.tier.value, str(raw["label"]), str(raw["summary"]), tuple(fields),
                source, str(target.get("prefix", "")), None if literal is None else str(literal),
            )
        )  # fmt: skip
    elsewhere = {str(k): str(v) for k, v in (doc.get("handled_elsewhere") or {}).items()}
    for action_id in elsewhere:
        if action_id not in registry.actions:
            raise FormsError(f"{action_id}: handled elsewhere, but not an action in the registry")
    requestable = {
        a
        for a, p in registry.actions.items()
        if p.scope is Scope.CUSTOMER and p.tier in (Tier.A1, Tier.A2) and p.executor
    }
    missing = sorted(requestable - seen - set(elsewhere))
    if missing:
        raise FormsError(
            f"no form, and no reason it is started elsewhere, for: {', '.join(missing)}"
        )
    return tuple(forms), elsewhere


@lru_cache(maxsize=1)
def request_forms() -> tuple[Form, ...]:
    forms, _ = parse(_yaml(CONFIG / "approvals" / "request-forms.yaml"), approvals_store.registry())
    return forms


def view(form: Form) -> dict[str, Any]:
    return {
        "action_id": form.action_id,
        "level": form.tier,
        "label": form.label,
        "summary": form.summary,
        # how the request names what it is about: a field's value, with a prefix in front, or one fixed word
        "target": {"from": form.target_from, "prefix": form.target_prefix, "literal": form.target_literal},
        "fields": [
            {
                "key": f.key, "label": f.label, "kind": f.kind, "required": f.required, "help": f.help,
                "max_length": f.max_length, "options": [{"id": i, "label": label} for i, label in f.options],
            }
            for f in form.fields
        ],
    }  # fmt: skip
