"""Extract registries from spec text exports. Writes JSON to an output dir for review."""
import json
import re
import sys
from pathlib import Path

TXT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

def doc_id(lines):
    for l in lines[:40]:
        m = re.search(r"\b(ZID-[A-Z0-9-]+-\d{3})\b", l)
        if m:
            return m.group(1)
    return "ZID-ARCH-002" if any("Back-End Target Architecture" in l for l in lines[:5]) else "?"

HEAD = re.compile(r"^\d{1,2}\. [A-Z]")
CODE = re.compile(r"^[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+$")
EVENT = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+){1,4}(?: / [a-z0-9_.]+)*$")

def sections(lines, title_re):
    out = []
    for i, l in enumerate(lines):
        if HEAD.match(l) and re.search(title_re, l):
            j = i + 1
            while j < len(lines) and not HEAD.match(lines[j]):
                j += 1
            out.append((l, lines[i + 1:j]))
    return out

errors, events, reqs, tests = [], [], [], []
for f in sorted(TXT.glob("*.txt")):
    lines = [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    did = doc_id(lines)
    for title, body in sections(lines, r"Canonical .*Codes|Error Code Families|Operational States & Codes"):
        for k, l in enumerate(body[:-1]):
            if CODE.match(l) and not CODE.match(body[k + 1]):
                errors.append({"code": l, "meaning": body[k + 1], "source": did, "section": title})
    for title, body in sections(lines, r"Mandatory .*Events|Event Catalogue|Interfaces & Events|DomainEvent"):
        for k, l in enumerate(body[:-1]):
            if EVENT.match(l):
                parts = [x.strip() for x in l.split(" / ")]
                base = parts[0]
                for frag in parts:
                    name = frag
                    if "." not in frag:  # e.g. "release.rollback_started / completed"
                        last = base.split(".")[-1]
                        stem = last.rsplit("_", 1)[0] + "_" if "_" in last else ""
                        name = base[: base.rfind(".") + 1] + stem + frag
                    events.append({"event": name, "meaning": body[k + 1], "source": did, "section": title})
    # requirements (REQ spec): ID, text, priority, verification
    if did == "ZID-REQ-001":
        for k, l in enumerate(lines[:-3]):
            if re.fullmatch(r"(?:N?FR-[A-Z]+|REG)-\d{3}", l):
                reqs.append({"id": l, "text": lines[k + 1], "priority": lines[k + 2],
                             "verification": lines[k + 3]})
    # acceptance tests: PREFIX-NN on its own line, followed by name + criterion
    for title, body in sections(lines, r"Acceptance( & [A-Za-z ]+)? Tests|Acceptance Tests|Production-Readiness Acceptance|Certification Gates"):
        for k, l in enumerate(body[:-2]):
            if re.fullmatch(r"[A-Z]{2,10}(?:-AT)?-\d{2,3}", l):
                tests.append({"id": l, "name": body[k + 1], "criterion": body[k + 2], "source": did})

fam = []
for f in sorted(TXT.glob("*API_and_Interface*.txt")):
    L = [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    for title, body in sections(L, r"Error Code Families"):
        for k, l in enumerate(body[:-2]):
            if re.fullmatch(r"[A-Z]+_\*", l):
                fam.append({"family": l, "use": body[k + 1], "http": body[k + 2]})
json.dump(fam, open(OUT / "families.json", "w"), indent=1)
print("families", len(fam))
json.dump(errors, open(OUT / "errors.json", "w"), indent=1)
json.dump(events, open(OUT / "events.json", "w"), indent=1)
json.dump(reqs, open(OUT / "requirements.json", "w"), indent=1)
json.dump(tests, open(OUT / "tests.json", "w"), indent=1)
codes = {}
for e in errors:
    codes.setdefault(e["code"], set()).add(e["source"])
print("errors", len(errors), "unique", len(codes))
print("events", len(events), "unique", len({e["event"] for e in events}))
print("requirements", len(reqs), "priorities", sorted({r["priority"] for r in reqs}))
print("tests", len(tests), "unique", len({t["id"] for t in tests}))
