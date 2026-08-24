import os
import re

import httpx
import pytest


LOCAL_SITE_URL = os.getenv("LOCAL_SITE_URL", "http://127.0.0.1:8001/")

pytestmark = pytest.mark.live_site


def _require_running_site(client: httpx.Client) -> httpx.Response:
    try:
        response = client.get("/health")
    except httpx.RequestError as error:
        pytest.skip(f"Local site is not reachable at {LOCAL_SITE_URL}: {error}")
    return response


def test_local_site_serves_dashboard_shell_and_assets():
    with httpx.Client(base_url=LOCAL_SITE_URL, timeout=3.0, follow_redirects=True) as client:
        health_response = _require_running_site(client)
        assert health_response.status_code == 200
        health = health_response.json()
        assert health["status"] == "ok"
        assert health["provider"] == "fmp"

        page_response = client.get("/")
        assert page_response.status_code == 200
        assert page_response.headers["cache-control"] == "no-store, max-age=0"

        html = page_response.text
        assert "<title>Stock Momentum AI</title>" in html
        assert 'id="loginScreen"' in html
        assert 'id="dashboard"' in html
        assert "PostgreSQL Momentum Candidates" in html
        assert "Biotech Catalysts" in html

        asset_paths = re.findall(r'(?:href|src)="([^"]*/static/[^"]+)"', html)
        assert asset_paths
        for asset_path in asset_paths:
            asset_response = client.get(asset_path)
            assert asset_response.status_code == 200
            assert asset_response.headers["cache-control"] == "no-store, max-age=0"

        auth_response = client.get("/auth/status")
        assert auth_response.status_code == 200
        assert isinstance(auth_response.json()["authenticated"], bool)
