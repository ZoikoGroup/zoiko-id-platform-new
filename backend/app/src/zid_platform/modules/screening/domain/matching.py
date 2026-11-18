"""Matching a party against sanctions lists (ZID-ED-04 sections 04 and 06). Pure domain, no I/O.

A fuzzy name hit is an alert, not a finding of guilt. So:
- a name alone, however close, is a POSSIBLE match; an identifier match or a very close name with the same birth year is STRONG;
- date of birth and country corroborate or contradict, and the reasons say so in words a reviewer can read;
- nothing here ever clears, rejects or approves anyone: an alert always goes to a person, and only a party with no alert is
  CLEAR, against the exact list versions that were checked;
- names are compared without case, accents or word order, with Cyrillic, Greek and Arabic written in Latin first; any other script
  is compared as written, so a miss is possible and the reviewer's own checks stay mandatory (the lists also carry original-script
  names as aliases).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from zid_platform.modules.screening.domain.lists import Entry, ListId, PartyKind, Snapshot


class Strength(StrEnum):
    STRONG = "STRONG"
    POSSIBLE = "POSSIBLE"


@dataclass(frozen=True, slots=True)
class Party:
    ref: str  # an opaque id, never the name
    kind: PartyKind
    name: str
    birth: str | None = None  # "YYYY" or "YYYY-MM-DD"
    countries: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()
    expired_identifiers: tuple[
        str, ...
    ] = ()  # an identifier whose document has expired: shown to a reviewer, never taken as a confirmed current identity


@dataclass(frozen=True, slots=True)
class Match:
    party_ref: str
    list_id: ListId
    list_version: str
    source_ref: str
    matched_name: str
    score: float
    strength: Strength
    reasons: tuple[str, ...]
    programs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchPolicy:
    version: str = "p1"
    threshold: float = 0.88  # at or above this a name is an alert; proposed, owners may tune
    strong_name: float = 0.97


# Deterministic transliteration of the scripts that sanctions lists most often carry beside a Latin name. A script that is
# not here (Han, Kana, Hangul, Hebrew, Thai, ...) is compared as written, so a miss is possible: the lists also carry the
# original-script name as an alias, and a reviewer's own checks stay mandatory.
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e", "є": "ye", "ж": "zh", "з": "z",
    "и": "i", "і": "i", "ї": "yi", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}  # fmt: skip
_GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i", "κ": "k", "λ": "l",
    "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f",
    "χ": "ch", "ψ": "ps", "ω": "o",
}  # fmt: skip
_ARABIC = {
    "ا": "a", "أ": "a", "إ": "i", "آ": "a", "ء": "", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh",
    "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "a",
    "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "ة": "h", "و": "w", "ي": "y",
    "ى": "a", "ئ": "", "ؤ": "",
}  # fmt: skip
# Devanagari, written the way sanctions lists romanise it. A consonant carries an inherent "a" that a vowel sign or the virama removes. Deterministic and lossy: a reviewer still checks.
_DEV_CONSONANT = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n", "ट": "t", "ठ": "th",
    "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh",
    "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}  # fmt: skip
_DEV_VOWEL = {
    "अ": "a",
    "आ": "a",
    "इ": "i",
    "ई": "i",
    "उ": "u",
    "ऊ": "u",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
}
_DEV_MATRA = {
    "ा": "a",
    "ि": "i",
    "ी": "i",
    "ु": "u",
    "ू": "u",
    "ृ": "ri",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
}
_DEV_SIGN = {"ं": "n", "ँ": "n", "ः": "h", "़": ""}
_VIRAMA = "्"
_DEVANAGARI = {**_DEV_CONSONANT, **_DEV_VOWEL, **_DEV_MATRA, **_DEV_SIGN, _VIRAMA: ""}


def _devanagari(text: str) -> tuple[str, bool]:
    """Devanagari letters written in Latin, and whether any were found."""
    out: list[str] = []
    found = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _DEV_CONSONANT:
            found = True
            nxt = text[i + 1] if i + 1 < len(text) else ""
            base = _DEV_CONSONANT[ch]
            if nxt == _VIRAMA:
                out.append(base)
                i += 2
                continue
            if nxt in _DEV_MATRA:
                out.append(base + _DEV_MATRA[nxt])
                i += 2
                continue
            out.append(base + "a")
        elif ch in _DEV_VOWEL or ch in _DEV_SIGN or ch in _DEV_MATRA or ch == _VIRAMA:
            found = True
            out.append(_DEVANAGARI.get(ch, ""))
        else:
            out.append(ch)
        i += 1
    return "".join(out), found


_SCRIPTS = {**_CYRILLIC, **_GREEK, **_ARABIC}

# scripts this matcher writes in Latin, and scripts it can only compare as written (a name in them matches only the identical original-script alias)
_HANDLED = {"CYRILLIC": _CYRILLIC, "GREEK": _GREEK, "ARABIC": _ARABIC, "DEVANAGARI": _DEVANAGARI}
_AS_WRITTEN = ("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "HEBREW", "THAI")
# spellings that differ only by transliteration convention are brought to one form, on both sides alike
_VARIANTS = (
    ("ph", "f"),
    ("ck", "k"),
    ("q", "k"),
    ("w", "v"),
    ("ou", "u"),
    ("oo", "u"),
    ("dzh", "j"),
    ("zh", "j"),
)


def _plain(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def transliterate(value: str, *, indic: bool = False) -> tuple[str, bool]:
    """The text with Cyrillic, Greek and Arabic letters written in Latin, and whether any letter changed script."""
    out, changed = [], False
    # the accents are stripped by NFKD, which would also split some Devanagari signs, so that script is converted first from the composed text
    # Devanagari is converted only for sanctions screening (`indic`): there a false alert goes to a reviewer, while identity matching keeps sending Indic names to a person.
    if indic:
        value, changed = _devanagari(unicodedata.normalize("NFC", value))
    for ch in _plain(value).casefold():
        mapped = _SCRIPTS.get(ch)
        if mapped is None:
            out.append(ch)
        else:
            out.append(mapped)
            changed = True
    return "".join(out), changed


def trace(value: str) -> list[dict[str, str]]:
    """What changed script and what could not: one entry per script with the letters it covered, and whether it was written in Latin or compared as written. Kept with a case so a
    reviewer sees that a name was transliterated, how, and which scripts only an identical original-script alias can match."""
    out: dict[str, set[str]] = {}
    for ch in unicodedata.normalize("NFC", value).casefold():
        for script, table in _HANDLED.items():
            if ch in table:
                out.setdefault(script, set()).add(ch)
        name = unicodedata.name(ch, "")
        for as_written in _AS_WRITTEN:
            if name.startswith(as_written) or (as_written == "CJK" and "CJK" in name):
                out.setdefault("AS_WRITTEN:" + as_written, set()).add(ch)
    return [
        {
            "script": script,
            "letters": "".join(sorted(letters)),
            "handling": "AS_WRITTEN" if script.startswith("AS_WRITTEN") else "TRANSLITERATED",
        }
        for script, letters in sorted(out.items())
    ]


def fold(value: str, *, indic: bool = False) -> str:
    """Case, accents and punctuation removed, other scripts written in Latin; word order kept."""
    latin, _ = transliterate(value, indic=indic)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", latin)).strip()


def _canon(token: str) -> str:
    for old, new in _VARIANTS:
        token = token.replace(old, new)
    token = re.sub(r"(.)\1+", r"\1", token)  # petroff -> petrof
    return token[:-1] + "f" if token.endswith("v") and len(token) > 3 else token  # -ov, -off, -of


def tokens(value: str, *, indic: bool = False) -> tuple[str, ...]:
    return tuple(sorted(_canon(t) for t in fold(value, indic=indic).split()))


def _skeleton(ts: tuple[str, ...]) -> str:
    return "".join(c for c in " ".join(ts) if c not in "aeiouy")


def name_score(a: str, b: str, *, indic: bool = False) -> float:
    """0..1 on the folded, word-order-free names. When either name came from another script, a consonant skeleton is also
    compared (vowels are what transliterations disagree on), at a small discount. Deterministic and explainable."""
    ta, tb = tokens(a, indic=indic), tokens(b, indic=indic)
    if not ta or not tb:
        return 0.0
    best = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    if transliterate(a, indic=indic)[1] or transliterate(b, indic=indic)[1]:
        sa, sb = _skeleton(ta), _skeleton(tb)
        if len(sa.replace(" ", "")) >= 3 and len(sb.replace(" ", "")) >= 3:
            best = max(best, 0.95 * SequenceMatcher(None, sa, sb).ratio())
    return round(best, 4)


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _birth_relation(party: str | None, entry_birth: Sequence[str]) -> str | None:
    """'MATCH', 'CONFLICT' or None when either side has no date."""
    if not party or not entry_birth:
        return None
    year = party[:4]
    return "MATCH" if any(b[:4] == year for b in entry_birth) else "CONFLICT"


def _kinds_compatible(a: PartyKind, b: PartyKind) -> bool:
    return a is b and a is not PartyKind.OTHER


def match_entry(
    party: Party, entry: Entry, snapshot: Snapshot, policy: MatchPolicy
) -> Match | None:
    if not _kinds_compatible(party.kind, entry.kind):
        return None
    reasons: list[str] = []
    expired = {
        normalize_identifier(i)
        for i in party.expired_identifiers
        if len(normalize_identifier(i)) >= 5
    }
    mine = {
        normalize_identifier(i)
        for i in party.identifiers
        if len(normalize_identifier(i)) >= 5 and normalize_identifier(i) not in expired
    }
    theirs = {
        normalize_identifier(i) for i in entry.identifiers if len(normalize_identifier(i)) >= 5
    }
    id_hit = bool(mine & theirs)
    expired_hit = bool(expired & theirs) and not id_hit
    names = (entry.primary_name, *entry.aliases)
    best_name, matched = max(
        ((name_score(party.name, n, indic=True), n) for n in names), key=lambda x: x[0]
    )
    score = best_name
    if id_hit:
        reasons.append("IDENTIFIER_MATCH")
        score = 1.0
    elif expired_hit:
        reasons.append(
            "IDENTIFIER_MATCH_EXPIRED_DOCUMENT"
        )  # a candidate for a person to read, not a confirmed current identity (QA-017)
        score = max(score, policy.threshold)
    if best_name >= policy.threshold:
        reasons.append("NAME_EXACT" if best_name >= 0.999 else "NAME_SIMILAR")
    birth = _birth_relation(party.birth, entry.birth)
    if birth == "MATCH":
        reasons.append("BIRTH_YEAR_MATCH")
        score = min(1.0, score + 0.03)
    elif birth == "CONFLICT":
        reasons.append("BIRTH_YEAR_CONFLICT")
        score = max(0.0, score - 0.10)
    if set(map(str.casefold, party.countries)) & set(entry.countries):
        reasons.append("COUNTRY_OVERLAP")
        score = min(1.0, score + 0.02)
    if not id_hit and not expired_hit and best_name < policy.threshold:
        return None
    if score < policy.threshold and not id_hit:
        return None
    if not (party.birth or party.countries or party.identifiers or party.expired_identifiers):
        reasons.append(
            "NO_SECONDARY_IDENTIFIERS"
        )  # lowers certainty; it is never evidence of a false positive (QA-018)
    if trace(party.name) or any(trace(n) for n in (matched,)):
        reasons.append("SCRIPT_TRACE_AVAILABLE")
    strong = id_hit or (best_name >= policy.strong_name and birth == "MATCH")
    return Match(
        party_ref=party.ref,
        list_id=entry.list_id,
        list_version=snapshot.version,
        source_ref=entry.source_ref,
        matched_name=matched,
        score=round(score, 4),
        strength=Strength.STRONG if strong else Strength.POSSIBLE,
        reasons=tuple(reasons),
        programs=entry.programs,
    )


def screen_party(
    party: Party, snapshots: Iterable[Snapshot], policy: MatchPolicy | None = None
) -> list[Match]:
    """Every alert for one party across the lists given, strongest first. An empty result means no alert, nothing more."""
    policy = policy or MatchPolicy()
    out: list[Match] = []
    for snap in snapshots:
        for entry in snap.entries:
            m = match_entry(party, entry, snap, policy)
            if m is not None:
                out.append(m)
    return sorted(
        out, key=lambda m: (m.strength is not Strength.STRONG, -m.score, m.list_id, m.source_ref)
    )
