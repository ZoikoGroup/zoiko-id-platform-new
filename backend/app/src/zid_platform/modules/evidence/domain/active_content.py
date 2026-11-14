"""Active content in a PDF, found before anything else reads the file (conformance T41; CR-092). Pure domain, no I/O.

A PDF a person or a validator opens must not be able to run anything. These names appear only in PDFs that can: script, launch actions, actions on
open, embedded files, rich media, XFA forms and form submission. A PDF that carries one is QUARANTINED: it is refused before the scanner and before the
document validator, nothing of it is kept, and the refusal is recorded (hash, size and the kinds found, never the content).

Two things are looked at, because a polyglot hides in either: the file may start with something else and carry the PDF header later, and a name may be
written with `#xx` escapes (`/J#61vaScript` is `/JavaScript`). Both are normalised before the names are looked for.
"""

from __future__ import annotations

import re

# audit-only name (an audit record, not an event on the bus; the catalogue declares only emitted events)
QUARANTINED = "evidence.document.quarantined"

_NAMES = {
    b"JavaScript": "JAVASCRIPT",
    b"JS": "JAVASCRIPT",
    b"Launch": "LAUNCH_ACTION",
    b"OpenAction": "ACTION_ON_OPEN",
    b"AA": "ACTION_ON_OPEN",
    b"EmbeddedFile": "EMBEDDED_FILE",
    b"RichMedia": "RICH_MEDIA",
    b"XFA": "XFA_FORM",
    b"SubmitForm": "FORM_SUBMISSION",
    b"ImportData": "FORM_SUBMISSION",
}
_ACTIVE = re.compile(
    rb"/("
    + b"|".join(sorted(_NAMES, key=lambda n: len(bytes(n)), reverse=True))
    + rb")(?![A-Za-z0-9])"
)
_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
_HEADER_WINDOW = 1024  # a reader accepts the header anywhere in the first kilobyte


def looks_like_pdf(content: bytes) -> bool:
    return b"%PDF-" in content[:_HEADER_WINDOW]


def active_content(content: bytes) -> tuple[str, ...]:
    """The kinds of active content a PDF carries (sorted, no duplicates), or () for a file that is not a PDF or carries none."""
    if not looks_like_pdf(content):
        return ()
    plain = _ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), content)
    return tuple(sorted({_NAMES[m.group(1)] for m in _ACTIVE.finditer(plain)}))
