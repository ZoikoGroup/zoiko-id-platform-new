from fastapi.testclient import TestClient
from zid_platform.main import create_app
from zid_platform.settings import Settings


def test_cell_role_serves_all_cell_modules() -> None:
    with TestClient(create_app(Settings(role="cell", cell_id="uk-1"))) as client:
        data = client.get("/").json()["data"]
    assert data["service"] == "zoiko-id-cell" and data["cell_id"] == "uk-1"
    assert (
        len(data["modules"]) == 32
        and "mandate" in data["modules"]
        and "locator" not in data["modules"]
    )


def test_global_role_serves_only_locator_and_discovery() -> None:
    with TestClient(create_app(Settings(role="global", cell_id="global"))) as client:
        data = client.get("/").json()["data"]
    assert data["service"] == "zoiko-id-global" and data["modules"] == ["locator", "discovery"]


def test_a_cell_can_also_serve_the_global_plane_on_its_own_port() -> None:
    """One process, one port: the cell modules plus locator + discovery, and the global schema is required too."""
    app = create_app(Settings(role="cell", cell_id="uk-1", serve_global_plane=True))
    with TestClient(app) as client:
        data = client.get("/").json()["data"]
    assert data["service"] == "zoiko-id-cell"
    assert "mandate" in data["modules"] and {"locator", "discovery"} <= set(data["modules"])
    assert "global_directory" in data["owned_schemas"]
    paths = set(app.openapi()["paths"])
    assert {"/v1/discovery", "/.well-known/zoiko-discovery-keys"} <= paths
    assert len(paths) > 100  # and the cell's own routes


def test_the_global_plane_is_not_served_by_a_cell_unless_asked() -> None:
    app = create_app(Settings(role="cell", cell_id="uk-1"))
    assert "/v1/discovery" not in app.openapi()["paths"]
