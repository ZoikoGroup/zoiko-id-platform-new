"""Merkle tree hashing, inclusion and consistency proofs (RFC 9162 §2.1; EVIDENCE-001 §11).

Leaf hash  = H(0x00 || leaf), node hash = H(0x01 || left || right), the empty tree hashes to H("").
The split point of an n-leaf tree is the largest power of two smaller than n, so trees of every
size are well defined and proofs are RFC 9162 audit paths. The hash function is always named
(`alg`), never implicit. Proof hashes are raw bytes here; the bundle format carries them as
`<alg>:<hex>` digests.
"""

from __future__ import annotations

import hmac
from collections.abc import Sequence

from zid_evidence.digest import HASH_ALGS, DigestError

__all__ = [
    "consistency_proof",
    "inclusion_proof",
    "leaf_hash",
    "node_hash",
    "root",
    "verify_consistency",
    "verify_inclusion",
]


def _h(alg: str, data: bytes) -> bytes:
    if alg not in HASH_ALGS:
        raise DigestError(f"hash algorithm not allowed: {alg!r}")
    return bytes(HASH_ALGS[alg](data).digest())


def leaf_hash(leaf: bytes, *, alg: str) -> bytes:
    return _h(alg, b"\x00" + leaf)


def node_hash(left: bytes, right: bytes, *, alg: str) -> bytes:
    return _h(alg, b"\x01" + left + right)


def _split(n: int) -> int:
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def _mth(hashes: Sequence[bytes], alg: str) -> bytes:
    """Merkle tree hash over already-hashed leaves."""
    n = len(hashes)
    if n == 0:
        return _h(alg, b"")
    if n == 1:
        return hashes[0]
    k = _split(n)
    return node_hash(_mth(hashes[:k], alg), _mth(hashes[k:], alg), alg=alg)


def root(leaves: Sequence[bytes], *, alg: str) -> bytes:
    """MTH(D[n]) of raw leaf inputs."""
    return _mth([leaf_hash(x, alg=alg) for x in leaves], alg)


def _path(m: int, hashes: Sequence[bytes], alg: str) -> list[bytes]:
    n = len(hashes)
    if n <= 1:
        return []
    k = _split(n)
    if m < k:
        return [*_path(m, hashes[:k], alg), _mth(hashes[k:], alg)]
    return [*_path(m - k, hashes[k:], alg), _mth(hashes[:k], alg)]


def inclusion_proof(index: int, leaves: Sequence[bytes], *, alg: str) -> list[bytes]:
    """PATH(index, D[n]): the audit path for leaf `index` (RFC 9162 §2.1.3.1)."""
    if not 0 <= index < len(leaves):
        raise ValueError("leaf index outside the tree")
    return _path(index, [leaf_hash(x, alg=alg) for x in leaves], alg)


def _subproof(m: int, hashes: Sequence[bytes], b: bool, alg: str) -> list[bytes]:
    n = len(hashes)
    if m == n:
        return [] if b else [_mth(hashes, alg)]
    k = _split(n)
    if m <= k:
        return [*_subproof(m, hashes[:k], b, alg), _mth(hashes[k:], alg)]
    return [*_subproof(m - k, hashes[k:], False, alg), _mth(hashes[:k], alg)]


def consistency_proof(first_size: int, leaves: Sequence[bytes], *, alg: str) -> list[bytes]:
    """PROOF(m, D[n]): proves the first `first_size` leaves are a prefix (RFC 9162 §2.1.4.1)."""
    if not 0 <= first_size <= len(leaves):
        raise ValueError("first tree larger than the second")
    if first_size in (0, len(leaves)):
        return []
    return _subproof(first_size, [leaf_hash(x, alg=alg) for x in leaves], True, alg)


def verify_inclusion(
    *,
    leaf_index: int,
    tree_size: int,
    leaf_digest: bytes,
    proof: Sequence[bytes],
    root_hash: bytes,
    alg: str,
) -> bool:
    """RFC 9162 §2.1.3.2. `leaf_digest` is the leaf *hash* (H(0x00 || leaf))."""
    if not 0 <= leaf_index < tree_size:
        return False
    fn, sn, r = leaf_index, tree_size - 1, leaf_digest
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r, alg=alg)
            while not fn & 1 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = node_hash(r, p, alg=alg)
        fn >>= 1
        sn >>= 1
    return sn == 0 and hmac.compare_digest(r, root_hash)


def verify_consistency(
    *,
    first_size: int,
    second_size: int,
    first_root: bytes,
    second_root: bytes,
    proof: Sequence[bytes],
    alg: str,
) -> bool:
    """RFC 9162 §2.1.4.2: the tree of `second_size` leaves extends the one of `first_size`."""
    if not 0 <= first_size <= second_size:
        return False
    if first_size == second_size:
        return not proof and hmac.compare_digest(first_root, second_root)
    if first_size == 0:
        return not proof
    if not proof:
        return False
    path = list(proof)
    if first_size & (first_size - 1) == 0:  # exact power of two: the first root is a subtree
        path.insert(0, first_root)
    fn, sn = first_size - 1, second_size - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = path[0]
    for c in path[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr = node_hash(c, fr, alg=alg)
            sr = node_hash(c, sr, alg=alg)
            while not fn & 1 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            sr = node_hash(sr, c, alg=alg)
        fn >>= 1
        sn >>= 1
    return (
        sn == 0
        and hmac.compare_digest(fr, first_root)
        and hmac.compare_digest(sr, second_root)
    )
