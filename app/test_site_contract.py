import re
from pathlib import Path

from fastapi.testclient import TestClient

from main import app


STATIC_DIR = Path(__file__).parent / "static"
TEST_PASSWORD = "site-suite-password"


def test_dashboard_shell_and_static_assets_are_served_with_no_cache_headers():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"

    html = response.text
    assert "<title>Stock Momentum AI</title>" in html
    assert 'id="loginScreen"' in html
    assert 'id="dashboard"' in html
    assert "PostgreSQL Momentum Candidates" in html
    assert "Put/Call" in html
    assert "Pullback Watch" in html
    assert "Biotech Catalysts" in html
    assert "Future Event Tracker" in html

    asset_paths = re.findall(r'(?:href|src)="([^"]*/static/[^"]+)"', html)
    assert sorted(asset_paths) == [
        "/static/app.js?v=202608231220",
        "/static/styles.css?v=202608231220",
    ]
    for asset_path in asset_paths:
        asset_response = client.get(asset_path)
        assert asset_response.status_code == 200
        assert asset_response.headers["cache-control"] == "no-store, max-age=0"
        assert asset_response.text.strip()


def test_dashboard_markup_satisfies_javascript_dom_contract():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    referenced_ids = set(re.findall(r'document\.querySelector\("#([A-Za-z0-9_-]+)"\)', javascript))
    declared_ids = set(re.findall(r'id="([^"]+)"', html))

    assert referenced_ids
    assert referenced_ids <= declared_ids

    tab_targets = set(re.findall(r'data-tab="([^"]+)"', html))
    assert tab_targets
    assert tab_targets <= declared_ids

    sort_keys = set(re.findall(r'data-sort-key="([^"]+)"', html))
    assert sort_keys == {"symbol", "price", "rsi", "score", "growth", "volume", "calls", "options"}


def test_auth_flow_protects_dashboard_data_apis(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("APP_SESSION_SECRET", "site-suite-secret")
    client = TestClient(app)

    assert client.get("/auth/status").json() == {"authenticated": False}

    blocked_response = client.get("/universe")
    assert blocked_response.status_code == 401
    assert blocked_response.json()["detail"] == "Login required"

    bad_login_response = client.post("/auth/login", json={"password": "wrong"})
    assert bad_login_response.status_code == 401

    login_response = client.post("/auth/login", json={"password": TEST_PASSWORD})
    assert login_response.status_code == 200
    assert login_response.json() == {"authenticated": True}
    assert "momentum_session=" in login_response.headers["set-cookie"]
    assert client.get("/auth/status").json() == {"authenticated": True}

    universe_response = client.get("/universe")
    assert universe_response.status_code == 200
    assert universe_response.json()["symbols"]

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {"authenticated": False}
    assert client.get("/universe").status_code == 401


def test_health_endpoint_exposes_site_runtime_status():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json().keys() == {
        "status",
        "marketdata_configured",
        "provider",
        "options_provider",
        "openai_configured",
    }
    assert response.json()["status"] == "ok"
    assert response.json()["provider"] == "fmp"
