"""Sanctions list model (ZID-ED-04 section 05). Pure domain, no I/O.

Each list keeps its own identity: its id, the version that was loaded, the hash of the file and when it was fetched. A
list is never merged into another and never renamed, so a decision can say exactly which list at which version cleared
or flagged a party. The UK Sanctions List replaced the OFSI Consolidated List on 28 January 2026; there is no OFSI list id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ListId(StrEnum):
    OFAC_SDN = "OFAC_SDN"
    OFAC_NON_SDN = (
        "OFAC_NON_SDN"  # the consolidated (non-SDN) lists: same file layout as the SDN files
    )
    UK_SANCTIONS = "UK_SANCTIONS"
    EU_CONSOLIDATED = "EU_CONSOLIDATED"
    UN_SC = "UN_SC"
    LOCAL = "LOCAL"  # a country's own list, converted by its owner to the normalized local format
    PEP = "PEP"  # politically exposed persons (CR-068): a risk list, not a sanctions list; a match only ever routes to a person


class PartyKind(StrEnum):
    PERSON = "PERSON"
    ENTITY = "ENTITY"
    OTHER = "OTHER"  # vessel, aircraft: never matched to a person or a company by name alone


@dataclass(frozen=True, slots=True)
class Entry:
    """One designated party, normalized from a source list. Source-native references are kept."""

    list_id: ListId
    source_ref: (
        str  # the list's own reference (OFAC ent_num, UK Unique ID, UN DATAID, EU logicalId)
    )
    kind: PartyKind
    primary_name: str
    aliases: tuple[str, ...] = ()
    birth: tuple[str, ...] = ()  # "YYYY" or "YYYY-MM-DD"
    countries: tuple[str, ...] = ()  # as the source gives them (a name or an ISO code), casefolded
    identifiers: tuple[str, ...] = ()  # normalized document and registration numbers
    programs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A loaded list: its version and content hash are what a decision cites."""

    list_id: ListId
    version: str  # the list's own publication reference or date, else the content hash prefix
    content_hash: str
    fetched_at: datetime
    entries: tuple[Entry, ...]

    @property
    def size(self) -> int:
        return len(self.entries)
