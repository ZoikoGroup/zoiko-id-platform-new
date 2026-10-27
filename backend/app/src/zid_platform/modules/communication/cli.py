"""Operator command line for the email estate.

    python -m zid_platform.modules.communication.cli --env-file C:\\Users\\me\\.zoiko\\email.env coverage
    python -m zid_platform.modules.communication.cli render ZID-EM-004
    python -m zid_platform.modules.communication.cli --env-file ... send-test ZID-EM-023 --to me@example.com

`send-test` is the only way to send without a platform event, and it sends to exactly the address you type, with
obvious sample values and a "[test]" subject prefix: it exists to prove the transport (login, TLS, delivery,
headers) and to look at a template in a real inbox. It touches no database and writes no message-log row.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from zid_platform.modules.communication.application.config import EmailConfig
from zid_platform.modules.communication.domain import catalogue as cat
from zid_platform.modules.communication.domain.render import Money, RenderContext, render
from zid_platform.modules.communication.infrastructure.catalogue_file import load as load_catalogue
from zid_platform.modules.communication.ports import build_sender, coverage
from zid_platform.settings import Settings


def load_env_file(path: str) -> None:
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def sample(name: str) -> object:
    kind = cat.field_kind(name)
    if kind is cat.FieldKind.DATETIME:
        return datetime.now(UTC)
    if kind is cat.FieldKind.MONEY:
        return Money(Decimal("1499.00"), "USD")
    if kind is cat.FieldKind.CODE:
        return "123456"
    if kind is cat.FieldKind.URL_TOKEN:
        return "sample-token-not-valid-0000000000"
    if kind is cat.FieldKind.URL_ROUTE:
        return None
    if kind is cat.FieldKind.REFERENCE:
        return "SAMPLE-0001"
    return f"[sample {name.replace('_', ' ')}]"


def context(cfg: EmailConfig) -> RenderContext:
    return RenderContext(
        sender_display_name=cfg.sender_display_name,
        links=cfg.links,
        support_url=cfg.support_url,
        marketing_basis="you asked to receive Zoiko iD product updates",
        preferences_url=cfg.links.base_url + "/preferences",
        unsubscribe_url=cfg.links.base_url + "/email/unsubscribe?sample",
        legal_entity=cfg.legal_entity or "Zoiko Tech Inc.",
        mailing_address=cfg.mailing_address or "[mailing address]",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="communication")
    ap.add_argument("--env-file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage")
    r = sub.add_parser("render")
    r.add_argument("template")
    t = sub.add_parser("send-test")
    t.add_argument("template")
    t.add_argument("--to", required=True)
    args = ap.parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)
    settings = Settings()
    cfg = EmailConfig.from_settings(settings)
    catalogue = load_catalogue()

    if args.cmd == "coverage":
        rows = coverage()
        wired = [x for x in rows if x["wired"]]
        sendable = [x for x in wired if cfg.allows(catalogue.get(str(x["template"])))]
        print(
            f"{len(rows)} templates, {len(wired)} have an event this platform emits, {len(sendable)} of those sendable now"
        )
        for row in wired:
            template = catalogue.get(str(row["template"]))
            state = "sendable" if cfg.allows(template) else "blocked (switch or link route)"
            print(f"  {row['template']}  {row['event']:<45} {state}")
        return 0

    template = catalogue.get(args.template)
    # a test render must not depend on which links are routed: route every link field to its default or a stub
    routes = {
        **dict(cfg.links.routes),
        **{f: f"/{f}" for f in template.url_fields if not cfg.links.routed(f)},
    }
    test_cfg = replace(
        cfg, links=type(cfg.links)(cfg.links.base_url, routes, cfg.links.allow_localhost_http)
    )
    rendered = render(template, {f: sample(f) for f in template.fields}, context(test_cfg))
    if args.cmd == "render":
        print(
            f"{rendered.template_id} ({rendered.message_class.value}, {rendered.template_version})"
        )
        print(f"Subject: {rendered.subject}\nPreheader: {rendered.preheader}\n\n{rendered.text}")
        return 0

    sender = build_sender(settings)
    if sender is None:
        print("email is not enabled (ZID_EMAIL_ENABLED)", file=sys.stderr)
        return 2
    rendered = replace(rendered, subject=f"[test] {rendered.subject}")
    result = asyncio.run(sender.send(to=args.to, rendered=rendered, message_id="cli-test"))
    print(f"{result.kind}" + (f" ({result.code})" if result.code else ""))
    return 0 if result.sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
