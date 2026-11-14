def sub(p, pairs):
    s = open(p, encoding="utf8", newline="").read()
    crlf = "\r\n" in s
    s = s.replace("\r\n", "\n")
    for a, b in pairs:
        assert a in s, (p, a[:70])
        s = s.replace(a, b, 1)
    if crlf:
        s = s.replace("\n", "\r\n")
    open(p, "w", encoding="utf8", newline="").write(s)


R = "src/zid_platform/modules/commercial/"
sub(
    R + "application/market_gate.py",
    [
        ("from zid_errors import ZidError\n", "from zid_errors import ErrorDetail, ZidError\n"),
        (
            '            details=[\n                {"code": r.code, "cause": r.cause.value, "dimension": r.dimension} for r in verdict.refusals\n            ],\n',
            "            details=[\n                ErrorDetail(field=r.dimension or 'market', issue=f'{r.cause.value}:{r.code}')\n                for r in verdict.refusals\n            ],\n",
        ),
    ],
)
sub(
    R + "ports.py",
    [
        ("from zid_platform.modules.commercial.application import billing_gate, usage_reservations\n", "from zid_platform.modules.commercial.application import billing_gate, usage_reservations\nfrom zid_platform.modules.commercial.application import market_gate\n"),
        ('    "market_matrix_store",\n', '    "market_gate",\n    "market_matrix_store",\n'),
    ],
)
sub(
    R + "api/commercial.py",
    [
        (
            "    entitlement_resolver,\n",
            "    entitlement_resolver,\n    market_gate,\n",
        ),
        (
            '@router.get("/v1/commercial/capability-dependencies")',
            '''class MarketGateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: Annotated[str, Field(min_length=2, max_length=8)]
    subject_country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    payer_country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    operating_region: Annotated[str, Field(min_length=2, max_length=40)] | None = None


@router.post("/v1/commercial/market-gate:check")
async def market_gate_check(
    body: MarketGateIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Asked by the platform's own services before a paid operation runs (section 10.1). Judged against the active market matrix like the configuration check; blocking
    only where the deployment or the company enforces commercial limits, otherwise reported (`sellable: false, blocked: false`). Scope `usage:write`, like reservations."""
    _usage_scope(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await market_gate.check(
            conn,
            family=body.family,
            subject_country=body.subject_country,
            payer_country=body.payer_country,
            operating_region=body.operating_region,
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )


@router.get("/v1/commercial/capability-dependencies")''',
        ),
    ],
)
print("ok")
