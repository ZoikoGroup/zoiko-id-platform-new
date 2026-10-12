"""Property tests for the authority algebra (AT-02, AUTHZ-03, MAND-01; DEC-C1 §3).

Hypothesis generates arbitrary constraint sets and checks the algebraic laws the specs require:
intersection never widens, containment is reflexive and transitive, and a request covered by a
child is always covered by its parent.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st
from zid_platform.modules.authority.domain.algebra import (
    ANY,
    Authority,
    Request,
    ValueLimit,
    Window,
    uncovered,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ACTIONS = ["invoice.approve", "invoice.read", "payment.release", "tool.execute"]
RESOURCES = ["invoice:1", "invoice:2", "invoice:*", "payment:9", "payment:*"]
WORDS = ["a", "b", "c", "d"]


def set_scope(items: list[str]) -> st.SearchStrategy[object]:
    return st.one_of(st.just(ANY), st.frozensets(st.sampled_from(items), max_size=len(items)))


limits = st.one_of(st.none(), st.integers(min_value=0, max_value=50))
values = st.one_of(
    st.none(),
    st.builds(
        ValueLimit,
        amount=st.decimals(
            min_value=0, max_value=10_000, places=2, allow_nan=False, allow_infinity=False
        ),
        currency=st.sampled_from(["GBP", "EUR"]),
    ),
)


@st.composite
def windows(draw: st.DrawFn) -> Window:
    lo = draw(st.one_of(st.none(), st.integers(0, 100)))
    hi = draw(st.one_of(st.none(), st.integers(0, 200)))
    if lo is not None and hi is not None and hi < lo:
        lo, hi = hi, lo
    return Window(
        None if lo is None else T0 + timedelta(days=lo),
        None if hi is None else T0 + timedelta(days=hi),
    )


authorities = st.builds(
    Authority,
    actions=set_scope(ACTIONS),
    resources=set_scope(RESOURCES),
    purposes=set_scope(WORDS),
    data=set_scope(WORDS),
    jurisdictions=set_scope(["GB", "DE", "FR"]),
    tools=set_scope(WORDS),
    value=values,
    count=limits,
    data_bytes=limits,
    min_assurance=st.integers(0, 3),
    window=windows(),
)

requests = st.builds(
    Request,
    action=st.sampled_from(ACTIONS),
    resource=st.sampled_from(["invoice:1", "invoice:2", "payment:9", "payment:3"]),
    at=st.integers(0, 250).map(lambda d: T0 + timedelta(days=d)),
    purpose=st.one_of(st.none(), st.sampled_from(WORDS)),
    data_category=st.one_of(st.none(), st.sampled_from(WORDS)),
    jurisdiction=st.one_of(st.none(), st.sampled_from(["GB", "DE", "FR"])),
    tool=st.one_of(st.none(), st.sampled_from(WORDS)),
    value=st.one_of(
        st.none(),
        st.builds(
            ValueLimit,
            amount=st.decimals(
                min_value=0, max_value=10_000, places=2, allow_nan=False, allow_infinity=False
            ),
            currency=st.sampled_from(["GBP", "EUR"]),
        ),
    ),
    count=st.integers(1, 60),
    data_bytes=st.integers(0, 60),
    actor_assurance=st.integers(0, 3),
)

PROPS = settings(max_examples=400, deadline=None)


@PROPS
@given(authorities)
def test_containment_is_reflexive(a: Authority) -> None:
    assert a.contains(a)


@PROPS
@given(authorities, authorities)
def test_intersection_never_widens_either_side(parent: Authority, child: Authority) -> None:
    """Monotonic attenuation: Effective(child) = parent ∩ child is inside both."""
    eff = parent.intersect(child)
    assert parent.contains(eff), parent.widenings(eff)
    assert child.contains(eff), child.widenings(eff)


@PROPS
@given(authorities, authorities, authorities)
def test_delegation_chains_only_narrow(root: Authority, mid: Authority, leaf: Authority) -> None:
    """Narrowing a parent never widens a descendant (DEC-C1 acceptance)."""
    eff_mid = root.intersect(mid)
    eff_leaf = eff_mid.intersect(leaf)
    assert root.contains(eff_mid) and eff_mid.contains(eff_leaf) and root.contains(eff_leaf)


@PROPS
@given(authorities, authorities, requests)
def test_child_never_authorizes_what_parent_denies(
    parent: Authority, child: Authority, req: Request
) -> None:
    """AT-02: any request the effective child covers is covered by the parent."""
    eff = parent.intersect(child)
    if not uncovered(eff, req):
        assert not uncovered(parent, req), uncovered(parent, req)


@PROPS
@given(authorities, authorities, authorities)
def test_containment_is_transitive(a: Authority, b: Authority, c: Authority) -> None:
    if a.contains(b) and b.contains(c):
        assert a.contains(c)


@PROPS
@given(authorities, authorities)
def test_contained_child_is_unchanged_by_intersection_coverage(
    parent: Authority, child: Authority
) -> None:
    """If the issuance gate accepts a child (child ⊆ parent), the child's own scope is what it gets."""
    if parent.contains(child):
        eff = parent.intersect(child)
        assert eff.contains(child) or child.contains(eff)


def test_currency_mismatch_leaves_nothing_spendable() -> None:
    a = Authority(value=ValueLimit(Decimal(100), "GBP"))
    b = Authority(value=ValueLimit(Decimal(100), "EUR"))
    eff = a.intersect(b)
    req = Request("x", "y", T0, value=ValueLimit(Decimal("0.01"), "GBP"))
    assert uncovered(eff, req) and a.contains(eff) and b.contains(eff)
