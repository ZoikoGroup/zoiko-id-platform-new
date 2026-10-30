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
_SCRIPTS = {**_CYRILLIC, **_GREEK, **_ARABIC}
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


def transliterate(value: str) -> tuple[str, bool]:
    """The text with Cyrillic, Greek and Arabic letters written in Latin, and whether any letter changed script."""
    out, changed = [], False
    for ch in _plain(value).casefold():
        mapped = _SCRIPTS.get(ch)
        if mapped is None:
            out.append(ch)
        else:
            out.append(mapped)
            changed = True
    return "".join(out), changed


def fold(value: str) -> str:
    """Case, accents and punctuation removed, other scripts written in Latin; word order kept."""
    latin, _ = transliterate(value)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", latin)).strip()


def _canon(token: str) -> str:
    for old, new in _VARIANTS:
        token = token.replace(old, new)
    token = re.sub(r"(.)\1+", r"\1", token)  # petroff -> petrof
    return token[:-1] + "f" if token.endswith("v") and len(token) > 3 else token  # -ov, -off, -of


def tokens(value: str) -> tuple[str, ...]:
    return tuple(sorted(_canon(t) for t in fold(value).split()))


def _skeleton(ts: tuple[str, ...]) -> str:
    return "".join(c for c in " ".join(ts) if c not in "aeiouy")


def name_score(a: str, b: str) -> float:
    """0..1 on the folded, word-order-free names. When either name came from another script, a consonant skeleton is also
    compared (vowels are what transliterations disagree on), at a small discount. Deterministic and explainable."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    best = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    if transliterate(a)[1] or transliterate(b)[1]:
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
    mine = {normalize_identifier(i) for i in party.identifiers if len(normalize_identifier(i)) >= 5}
    theirs = {
        normalize_identifier(i) for i in entry.identifiers if len(normalize_identifier(i)) >= 5
    }
    id_hit = bool(mine & theirs)
    names = (entry.primary_name, *entry.aliases)
    best_name, matched = max(((name_score(party.name, n), n) for n in names), key=lambda x: x[0])
    score = best_name
    if id_hit:
        reasons.append("IDENTIFIER_MATCH")
        score = 1.0
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
    if not id_hit and best_name < policy.threshold:
        return None
    if score < policy.threshold and not id_hit:
        return None
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
