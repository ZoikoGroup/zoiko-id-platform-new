from zid_context import RequestContext, current_context, new_id, reset_context, set_context, uuid7


def test_uuid7_is_version_7_and_time_ordered() -> None:
    a, b = uuid7(), uuid7()
    assert a.version == 7 and a.variant == "specified in RFC 4122"
    assert a.int >> 80 <= b.int >> 80  # 48-bit timestamp prefix never decreases


def test_new_id_prefix() -> None:
    assert new_id("req").startswith("req_")


def test_context_is_scoped() -> None:
    ctx = RequestContext(request_id="req_1", correlation_id="cor_1", service="s", cell_id="c")
    token = set_context(ctx)
    assert current_context() is ctx and ctx.tenant_id is None
    reset_context(token)
    assert current_context() is None
