"""Build the email template catalogue from the Communication Estate copybook (ZID-EM-001..160).

    pdftotext -layout Zoiko_iD_Email_Communication_Estate.pdf estate.txt
    python tools/scripts/build_email_catalogue.py estate.txt

Writes backend/app/src/zid_platform/modules/communication/templates/catalogue.json. The copybook is the
source of the words; this script only structures them (id, event, recipient, class, subject, preheader,
paragraphs, the one action, the engineering control). It fails loudly on anything it cannot place, so the
catalogue can never silently drop or garble a template. Re-running it on a revised copybook is how copy
changes enter the repo, as a reviewable diff.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = (
    Path(__file__).resolve().parents[2]
    / "backend/app/src/zid_platform/modules/communication/templates/catalogue.json"
)
HEADER = re.compile(r"^(ZID-EM-\d{3})\s+(.+?)\s*$")
CHROME = (
    re.compile(r"^\s*ZOIKO iD / COMMUNICATION ESTATE / 2026\s*$"),
    re.compile(r"^\s*INTERNAL\s.*ENGINEERING COPY BASELINE\s*\|\s*PAGE\s+\d+\s*$"),
)
SECTION = re.compile(r"^\d{2}\s+[A-Z][^|]*$")
COUNT_LINE = re.compile(r"^\d+ complete email templates")
SENTENCE_END = (".", "!", "?", ":", "{{one_time_code}}")  # the code stands alone on its own line


def clean(lines: list[str]) -> list[str]:
    out = []
    for raw in lines:
        line = raw.replace("\f", "").rstrip()
        if any(p.match(line) for p in CHROME):
            continue
        out.append(line.strip())
    return out


def join_wrapped(parts: list[str]) -> list[str]:
    """Join hard-wrapped lines into paragraphs: a line continues the previous one unless that one ended a sentence."""
    paragraphs: list[str] = []
    for line in parts:
        if not line:
            continue
        greeting_done = paragraphs and paragraphs[-1].startswith("Hello ") and len(paragraphs) == 1
        if paragraphs and not greeting_done and not paragraphs[-1].endswith(SENTENCE_END):
            paragraphs[-1] += " " + line
        else:
            paragraphs.append(line)
    return paragraphs


def parse(text: str) -> list[dict[str, object]]:
    lines = clean(text.splitlines())
    blocks: list[tuple[str, str, list[str]]] = []
    for line in lines:
        m = HEADER.match(line)
        if m:
            blocks.append((m.group(1), m.group(2), []))
            continue
        if blocks:
            blocks[-1][2].append(line)

    templates = []
    for tid, title, body in blocks:
        # stop at the first thing that is not part of the template (section headers, the release register)
        trimmed: list[str] = []
        for line in body:
            if SECTION.match(line) or COUNT_LINE.match(line) or line.startswith("07 / Release"):
                break
            trimmed.append(line)
        joined = "\n".join(trimmed)

        meta = re.search(r"^EVENT\s+(\S+)\s*\|\s*TO\s+(.+?)\s*\|\s*CLASS\s+(.+?)\s*$", joined, re.M)
        if not meta:
            raise SystemExit(f"{tid}: no EVENT/TO/CLASS line")

        def field(name: str, stop: tuple[str, ...]) -> str:
            m = re.search(rf"^{name}\s+(.*?)(?=^(?:{'|'.join(stop)})\b|\Z)", joined, re.M | re.S)
            if not m:
                raise SystemExit(f"{tid}: no {name}")
            return " ".join(m.group(1).split())

        subject = field("SUBJECT", ("PREHEADER",))
        preheader = field("PREHEADER", ("Hello",))

        after = joined.split("PREHEADER", 1)[1]
        after = after.split("\n", 1)[1] if "\n" in after else ""
        # the first line(s) of the preheader may wrap onto following lines; the greeting starts the body
        body_start = after.find("Hello")
        if body_start == -1:
            raise SystemExit(f"{tid}: no greeting")
        main = after[body_start:]

        control = None
        mc = re.search(r"^ENGINEERING CONTROL\s+(.*)$", main, re.M | re.S)
        if mc:
            control = " ".join(mc.group(1).split())
            main = main[: mc.start()]
        main = re.sub(r"^Thank you,.*$", "", main, flags=re.M)

        action = None
        ma = re.search(r"^ACTION\s+(.+?)\s*\u2192\s*(\{\{\w+\}\})\s*$", main, re.M)
        if ma:
            action = {"label": ma.group(1).strip(), "url_field": ma.group(2)[2:-2]}
            main = main[: ma.start()] + main[ma.end() :]
        elif re.search(r"^ACTION", main, re.M):
            raise SystemExit(f"{tid}: unparsed ACTION line")

        paragraphs = join_wrapped(main.splitlines())
        if not paragraphs or not paragraphs[0].startswith("Hello"):
            raise SystemExit(f"{tid}: body does not start with the greeting")
        greeting, *rest = paragraphs
        if greeting.rstrip(",") != "Hello {{recipient_display_name}}":
            raise SystemExit(f"{tid}: unexpected greeting {greeting!r}")

        templates.append(
            {
                "id": tid,
                "title": title,
                "event": meta.group(1),
                "to": meta.group(2).strip(),
                "class_label": meta.group(3).strip(),
                "subject": subject,
                "preheader": preheader,
                "paragraphs": rest,
                "action": action,
                "control": control,
            }
        )
    return templates


def main() -> None:
    src = Path(sys.argv[1]).read_text(encoding="utf-8")
    templates = parse(src)
    ids = [str(t["id"]) for t in templates]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate template ids")
    expected = {f"ZID-EM-{i:03d}" for i in range(1, 161)}
    if set(ids) != expected:
        raise SystemExit(f"missing {sorted(expected - set(ids))} extra {sorted(set(ids) - expected)}")
    templates.sort(key=lambda t: str(t["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "ZID-EM copybook v2.0 (23 Oct 2026)", "templates": templates}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(templates)} templates to {OUT}")


if __name__ == "__main__":
    main()
