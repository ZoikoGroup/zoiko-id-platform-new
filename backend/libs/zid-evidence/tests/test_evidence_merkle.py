"""RFC 9162 Merkle trees: inclusion and consistency proofs at every small size (EVID-07)."""

from __future__ import annotations

import hashlib

import pytest
from zid_evidence import merkle

ALG = "sha-256"


def leaves(n: int) -> list[bytes]:
    return [f"leaf-{i}".encode() for i in range(n)]


def test_rfc9162_hashing_rules() -> None:
    sha = hashlib.sha256
    assert merkle.root([], alg=ALG) == sha(b"").digest()
    assert merkle.root([b"a"], alg=ALG) == sha(b"\x00a").digest()
    la, lb, lc = (sha(b"\x00" + x).digest() for x in (b"a", b"b", b"c"))
    ab = sha(b"\x01" + la + lb).digest()
    assert merkle.root([b"a", b"b"], alg=ALG) == ab
    # 3 leaves split at the largest power of two below n (2): ((a,b),c)
    assert merkle.root([b"a", b"b", b"c"], alg=ALG) == sha(b"\x01" + ab + lc).digest()


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 31, 33])
def test_every_inclusion_proof_verifies_and_only_for_its_leaf(n: int) -> None:
    data = leaves(n)
    root = merkle.root(data, alg=ALG)
    for i in range(n):
        proof = merkle.inclusion_proof(i, data, alg=ALG)
        leaf = merkle.leaf_hash(data[i], alg=ALG)
        assert merkle.verify_inclusion(
            leaf_index=i, tree_size=n, leaf_digest=leaf, proof=proof, root_hash=root, alg=ALG
        )
        other = merkle.leaf_hash(b"forged", alg=ALG)
        assert not merkle.verify_inclusion(
            leaf_index=i, tree_size=n, leaf_digest=other, proof=proof, root_hash=root, alg=ALG
        )
        if n > 1:
            assert not merkle.verify_inclusion(
                leaf_index=(i + 1) % n,
                tree_size=n,
                leaf_digest=leaf,
                proof=proof,
                root_hash=root,
                alg=ALG,
            )
            bad = [bytes(32)] + list(proof[1:])
            assert not merkle.verify_inclusion(
                leaf_index=i, tree_size=n, leaf_digest=leaf, proof=bad, root_hash=root, alg=ALG
            )
            assert not merkle.verify_inclusion(
                leaf_index=i,
                tree_size=n,
                leaf_digest=leaf,
                proof=proof[:-1],
                root_hash=root,
                alg=ALG,
            )
    with pytest.raises(ValueError):
        merkle.inclusion_proof(n, data, alg=ALG)
    leaf0 = merkle.leaf_hash(data[0], alg=ALG)
    assert not merkle.verify_inclusion(
        leaf_index=n, tree_size=n, leaf_digest=leaf0, proof=[], root_hash=root, alg=ALG
    )


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 20])
def test_every_consistency_proof_verifies(n: int) -> None:
    data = leaves(n)
    second = merkle.root(data, alg=ALG)
    for m in range(n + 1):
        first = merkle.root(data[:m], alg=ALG)
        proof = merkle.consistency_proof(m, data, alg=ALG)
        assert merkle.verify_consistency(
            first_size=m,
            second_size=n,
            first_root=first,
            second_root=second,
            proof=proof,
            alg=ALG,
        ), (m, n)
        if 0 < m < n:
            # a rewritten history (different first tree) does not verify
            forged = merkle.root([b"x", *data[1:m]], alg=ALG)
            assert not merkle.verify_consistency(
                first_size=m,
                second_size=n,
                first_root=forged,
                second_root=second,
                proof=proof,
                alg=ALG,
            )
            tampered = [bytes(32), *proof[1:]]
            assert not merkle.verify_consistency(
                first_size=m,
                second_size=n,
                first_root=first,
                second_root=second,
                proof=tampered,
                alg=ALG,
            )


def test_consistency_edge_cases() -> None:
    data = leaves(4)
    r = merkle.root(data, alg=ALG)
    assert merkle.verify_consistency(
        first_size=4, second_size=4, first_root=r, second_root=r, proof=[], alg=ALG
    )
    assert not merkle.verify_consistency(
        first_size=4, second_size=4, first_root=bytes(32), second_root=r, proof=[], alg=ALG
    )
    assert not merkle.verify_consistency(
        first_size=5, second_size=4, first_root=r, second_root=r, proof=[], alg=ALG
    )
    with pytest.raises(ValueError):
        merkle.consistency_proof(5, data, alg=ALG)


def test_other_allowed_hashes_and_unknown_hash() -> None:
    data = leaves(5)
    for alg in ("sha-384", "sha-512"):
        root = merkle.root(data, alg=alg)
        proof = merkle.inclusion_proof(3, data, alg=alg)
        assert merkle.verify_inclusion(
            leaf_index=3,
            tree_size=5,
            leaf_digest=merkle.leaf_hash(data[3], alg=alg),
            proof=proof,
            root_hash=root,
            alg=alg,
        )
    with pytest.raises(ValueError):
        merkle.root(data, alg="sha-1")
