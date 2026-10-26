"""A throttled or unreachable key service gives a retryable 503, never a result without its signature (OPS-17)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.testclient import TestClient
from zid_crypto import CryptoError
from zid_platform.main import create_app
from zid_platform.settings import Settings


def client_with(code: str) -> TestClient:
    app = create_app(
        Settings(database_url="postgresql+asyncpg://x:y@localhost:1/none", environment="test")
    )
    router = APIRouter()

    @router.get("/_probe/crypto")
    async def probe() -> dict[str, str]:
        raise CryptoError(code)

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_a_throttled_key_service_is_a_retryable_503_with_retry_after() -> None:
    r = client_with("CRYPTO_THROTTLED").get("/_probe/crypto")
    assert r.status_code == 503
    assert r.headers["retry-after"] == "2"
    err = r.json()["error"]
    assert (
        err["code"] == "CRYPTO_UNAVAILABLE"
        and err["retryable"] is True
        and err["retry_after_ms"] == 2000
    )
    assert "Nothing was changed" in err["message"]


def test_an_unreachable_key_service_degrades_the_same_way() -> None:
    assert client_with("CRYPTO_MODULE_UNAVAILABLE").get("/_probe/crypto").status_code == 503


def test_a_refused_operation_is_not_dressed_up_as_retryable() -> None:
    r = client_with("CRYPTO_ACCESS_DENIED").get("/_probe/crypto")
    assert r.status_code == 500 and r.json()["error"]["retryable"] is False
