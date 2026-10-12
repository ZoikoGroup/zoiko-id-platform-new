from fastapi.testclient import TestClient
from zid_platform.main import create_app
from zid_platform.settings import Settings


def test_cell_role_serves_all_cell_modules() -> None:
    with TestClient(create_app(Settings(role="cell", cell_id="uk-1"))) as client:
        data = client.get("/").json()["data"]
    assert data["service"] == "zoiko-id-cell" and data["cell_id"] == "uk-1"
    assert (
        len(data["modules"]) == 27
        and "mandate" in data["modules"]
        and "locator" not in data["modules"]
    )


def test_global_role_serves_only_locator_and_discovery() -> None:
    with TestClient(create_app(Settings(role="global", cell_id="global"))) as client:
        data = client.get("/").json()["data"]
    assert data["service"] == "zoiko-id-global" and data["modules"] == ["locator", "discovery"]
