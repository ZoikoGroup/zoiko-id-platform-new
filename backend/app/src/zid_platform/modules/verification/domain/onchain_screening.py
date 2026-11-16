"""On-chain address screening: address shape and the provider result as a claim (ZID-GAP-001 v2.0 DTV-34; CR-101). Pure domain.

Blockchain analytics needs a licensed provider and a risk model the provider must disclose. What is here is the part that is ours: is the address well formed
for its chain, what does the provider say (passed through with its provenance), and what a person should look at.

- shape only: an address is checked for the shape and, where it is cheap and exact, the checksum of its chain (Bitcoin and Tron use base58check; Ethereum
  addresses must be 0x and forty hex digits, and a mixed-case one is NOT checksum-verified here, which the answer says). A well-formed address is not an address
  anyone owns, and nothing here links an address to a person;
- the provider's risk band is passed through and labelled PROVIDER_ASSERTED. This module never invents a risk, never turns one into a verdict, and a provider
  that is unavailable or raises is UNAVAILABLE, never "clean";
- an unavailable answer, or a HIGH or SEVERE band, is REVIEW for a person. No transfer is blocked here;
- the address is hashed with a key (the tenant and the chain inside it) before it is kept anywhere, so a stored result cannot be searched by address across companies.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Protocol

LINE = "onchain_screening"
CHAINS = frozenset({"BITCOIN", "ETHEREUM", "TRON", "SOLANA"})
BANDS = frozenset({"LOW", "MEDIUM", "HIGH", "SEVERE"})
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ETH = re.compile(r"^0x[0-9a-fA-F]{40}$")
BECH32 = re.compile(r"^(bc1|tb1)[02-9ac-hj-np-z]{11,71}$")


class ChainError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _b58check(text: str, versions: tuple[int, ...]) -> bool:
    num = 0
    for ch in text:
        i = B58.find(ch)
        if i < 0:
            return False
        num = num * 58 + i
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big")
    raw = b"\x00" * (len(text) - len(text.lstrip("1"))) + raw
    if len(raw) != 25 or raw[0] not in versions:
        return False
    return hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:]


def check_address(chain: str, address: str) -> tuple[bool, tuple[str, ...]]:
    """(well formed, notes). Never says an address is owned or safe."""
    if chain not in CHAINS:
        raise ChainError("CHAIN_NOT_SUPPORTED", chain)
    text = (address or "").strip()
    if chain == "ETHEREUM":
        if not ETH.match(text):
            return False, ("ADDRESS_SHAPE_INVALID",)
        body = text[2:]
        return True, (
            ()
            if body == body.lower() or body == body.upper()
            else ("MIXED_CASE_CHECKSUM_NOT_VERIFIED",)
        )
    if chain == "BITCOIN":
        if text.lower().startswith(("bc1", "tb1")):
            return (
                (True, ("BECH32_CHECKSUM_NOT_VERIFIED",))
                if BECH32.match(text.lower())
                else (False, ("ADDRESS_SHAPE_INVALID",))
            )
        return (
            (True, ()) if _b58check(text, (0x00, 0x05)) else (False, ("ADDRESS_CHECKSUM_INVALID",))
        )
    if chain == "TRON":
        return (
            (True, ())
            if text.startswith("T") and len(text) == 34 and _b58check(text, (0x41,))
            else (False, ("ADDRESS_CHECKSUM_INVALID",))
        )
    return (
        (True, ("SHAPE_ONLY",))
        if 32 <= len(text) <= 44 and all(c in B58 for c in text)
        else (False, ("ADDRESS_SHAPE_INVALID",))
    )


def address_hash(chain: str, address: str, *, tenant_id: str, pepper: bytes) -> str:
    """A keyed hash with the tenant and the chain in the key. Ethereum addresses are case-folded first: the case is a checksum, not a different address."""
    if not pepper:
        raise ChainError(
            "PEPPER_REQUIRED", "a hash without a secret key can be reversed by guessing"
        )
    text = address.strip().lower() if chain == "ETHEREUM" else address.strip()
    key = hashlib.sha256(pepper + b"|" + tenant_id.encode() + b"|" + chain.encode()).digest()
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderAnswer:
    available: bool
    band: str | None = None  # LOW | MEDIUM | HIGH | SEVERE, as the provider states it
    categories: tuple[str, ...] = ()  # exposure categories the provider names, passed through
    reasons: tuple[str, ...] = ()
    provider: str = ""


class ChainProvider(Protocol):
    """What a blockchain analytics adapter implements. The only place a network call may happen; it fails by returning `available=False`, never by raising."""

    def check(self, chain: str, address: str) -> ProviderAnswer: ...


class NoProvider:
    def check(self, chain: str, address: str) -> ProviderAnswer:
        return ProviderAnswer(False, None, (), ("NO_PROVIDER_CONFIGURED",), "")


@dataclass(frozen=True, slots=True)
class Screening:
    chain: str
    well_formed: bool
    notes: tuple[str, ...]
    provider: ProviderAnswer
    recommendation: str  # REVIEW or NONE; there is no BLOCK
    assurance: str = "PROVIDER_ASSERTED"
    caveats: tuple[str, ...] = field(
        default=(
            "A well-formed address is not an address anyone owns, and nothing here links it to a person.",
            "The risk band is the provider's, passed through. Nothing was blocked.",
        )
    )


def screen(chain: str, address: str, provider: ChainProvider | None = None) -> Screening:
    ok, notes = check_address(chain, address)
    if not ok:
        return Screening(
            chain,
            False,
            notes,
            ProviderAnswer(False, None, (), ("NOT_ASKED_MALFORMED_ADDRESS",), ""),
            "REVIEW",
        )
    try:
        answer = (provider or NoProvider()).check(chain, address.strip())
    except Exception:  # noqa: BLE001 - a provider that fails is unavailable, never clean
        answer = ProviderAnswer(False, None, (), ("PROVIDER_FAILED",), "")
    if answer.available and answer.band not in BANDS:
        answer = ProviderAnswer(False, None, (), ("PROVIDER_BAND_UNKNOWN",), answer.provider)
    review = (not answer.available) or answer.band in {"HIGH", "SEVERE"}
    return Screening(chain, True, notes, answer, "REVIEW" if review else "NONE")
