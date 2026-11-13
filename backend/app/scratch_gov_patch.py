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


R = "src/zid_platform/modules/"
sub(
    R + "commercial/infrastructure/pricing_page_config.py",
    [
        ("from functools import lru_cache\n", "import hashlib\nfrom functools import lru_cache\n"),
        (
            '@lru_cache(maxsize=1)\ndef pricing_page() -> PricingPage:\n    doc = (\n        yaml.safe_load((CONFIG / "commercial" / "pricing-page.yaml").read_text(encoding="utf-8"))\n        or {}\n    )\n    return parse(doc)\n\n\n__all__ = ["pricing_page"]',
            '_FILE = CONFIG / "commercial" / "pricing-page.yaml"\n\n\n@lru_cache(maxsize=1)\ndef pricing_page() -> PricingPage:\n    doc = yaml.safe_load(_FILE.read_text(encoding="utf-8")) or {}\n    return parse(doc)\n\n\ndef content_hash() -> str:\n    """The hash of the page file exactly as shipped: what the Founder and the CCO sign. Changing a single character makes the page unsigned again."""\n    return hashlib.sha256(_FILE.read_bytes()).hexdigest()\n\n\n__all__ = ["content_hash", "pricing_page"]',
        ),
    ],
)

ROUTES = '''class PageSignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class NoticeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^[A-Za-z0-9._-]{3,40}$")
    body: str = Field(min_length=40, max_length=4000)


@router.get("/v1/commercial/pricing-page")
async def pricing_page_state(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Whether the public pricing page's packaging is approved: the file's current hash, who has signed it, and who is still needed."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        return await pricing_governance.page_state(conn)


@router.post("/v1/commercial/pricing-page:sign")
async def sign_pricing_page(body: PageSignBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    """An appointed Founder or CCO signs the pricing page file by the hash they were shown. Both, as different people, make it APPROVED; any change to the file unsigns it."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            return await pricing_governance.sign_page(conn, signer=str(op.principal_id), shown_hash=body.content_hash)
        except CommercialError as e:
            raise _fail(e) from e


@router.get("/v1/commercial/public-notices")
async def list_public_notices(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        rows = await notice_store.notices(conn)
        served = await pricing_governance.served_notice(conn)
    return {
        "served": served,
        "items": [
            {**{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}, "created_at": _iso(r["created_at"]), "activated_at": _iso(r["activated_at"])}
            for r in rows
        ],
    }


@router.post("/v1/commercial/public-notices", status_code=201)
async def propose_public_notice(body: NoticeBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    """Legal's text for the inquiry form's privacy notice, as a draft. It changes nothing a visitor sees until a different person activates it."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            return await notice_store.propose(conn, version=body.version, body=body.body, created_by=str(op.principal_id))
        except CommercialError as e:
            raise _fail(e) from e


@router.post("/v1/commercial/public-notices/{notice_id}:activate")
async def activate_public_notice(notice_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await notice_store.activate(conn, str(notice_id), activator=str(op.principal_id), now=datetime.now(UTC))
            await log_change(conn, kind="PUBLIC_NOTICE_ACTIVATED", ref=str(notice_id), tenant_id=None, actor=op.principal_id, detail={})
        except CommercialError as e:
            raise _fail(e) from e
    return {"notice_id": str(notice_id), "status": "ACTIVE"}


@router.post("/v1/commercial/public-notices/{notice_id}:withdraw")
async def withdraw_public_notice(notice_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await notice_store.withdraw(conn, str(notice_id))
        except CommercialError as e:
            raise _fail(e) from e
    return {"notice_id": str(notice_id), "status": "WITHDRAWN"}


'''
STATUS = """    "PAGE_CHANGED": 409,
    "NOT_A_SIGNATORY": 403,
    "ALREADY_SIGNED": 409,
    "NOTICE_NOT_FOUND": 404,
    "NOTICE_NOT_DRAFT": 409,
    "NOTICE_SELF_ACTIVATION": 403,
    "NOTICE_VERSION_EXISTS": 409,
    "NOTICE_REFUSED": 409,
"""
sub(
    R + "operator_access/api/commercial_admin.py",
    [
        ("    lead_store,\n", "    lead_store,\n    notice_store,\n    pricing_governance,\n"),
        ('    "LEAD_NOT_FOUND": 404,\n', '    "LEAD_NOT_FOUND": 404,\n' + STATUS),
        ('@router.get("/v1/commercial/quote-requests")', ROUTES + '@router.get("/v1/commercial/quote-requests")'),
    ],
)
sub(
    R + "commercial/ports.py",
    [
        ("    lead_store,\n", "    lead_store,\n    notice_store,\n"),
        ('    "lead_store",\n', '    "lead_store",\n    "notice_store",\n    "pricing_governance",\n'),
        (
            "from zid_platform.modules.commercial.application import billing_gate, usage_reservations\n",
            "from zid_platform.modules.commercial.application import billing_gate, usage_reservations\nfrom zid_platform.modules.commercial.application import pricing_governance\n",
        ),
    ],
)
print("ok")
