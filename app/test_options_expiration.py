import asyncio
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from fmp import FmpClient, FmpError, summarize_expiration_volume
from main import app


def summarize(rows, target=date(2026, 12, 16)):
    return summarize_expiration_volume(rows, target, {"expiration": None, "total_volume": None})


def test_nearest_expiration_excludes_other_dates_and_sums_both_sides():
    result = summarize([
        {"expiration": "2026-12-18", "side": "call", "volume": 20},
        {"expiration": "2026-12-18", "side": "call", "volume": 30},
        {"expiration": "2026-12-18", "side": "put", "volume": 10},
        {"expiration": "2026-12-11", "side": "put", "volume": 999},
    ])
    assert result == {"expiration": "2026-12-18", "call_volume": 50, "put_volume": 10, "total_volume": 60}


def test_missing_volume_is_not_reported_as_zero():
    result = summarize([
        {"expiration": "2026-12-18", "side": "call", "volume": None},
        {"expiration": "2026-12-18", "side": "put", "volume": 0},
    ])
    assert result["call_volume"] is None
    assert result["put_volume"] == 0
    assert result["total_volume"] is None


def test_unrelated_expirations_and_institutional_periods_are_unavailable():
    assert summarize([
        {"expiration": "2026-10-16", "side": "call", "volume": 999},
        {"expiration": "2026Q4", "side": "put", "volume": 999},
    ])["expiration"] is None


def test_options_volume_requires_login():
    assert TestClient(app).get("/options/three-month-volume?symbols=AAPL").status_code == 401


def test_calendar_month_target_clamps_day_and_ignores_holdings(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 30, tzinfo=UTC)

    monkeypatch.setattr("fmp.datetime", FixedDateTime)
    monkeypatch.delenv("FMP_OPTIONS_CHAIN_PATH", raising=False)
    monkeypatch.setenv("FMP_POSITIONS_SUMMARY_ENABLED", "true")
    result = asyncio.run(FmpClient(api_key="test").three_month_options_volume("AAPL"))
    assert result["target_expiration"] == "2027-02-28"
    assert result["total_volume"] is None


def test_endpoint_preserves_results_when_one_symbol_fails(monkeypatch):
    async def volume(self, symbol):
        if symbol == "MSFT":
            raise FmpError("Provider unavailable")
        return {"symbol": symbol, "expiration": "2026-12-18", "total_volume": 100}

    monkeypatch.setattr(FmpClient, "three_month_options_volume", volume)
    monkeypatch.setenv("APP_PASSWORD", "test-options")
    client = TestClient(app)
    client.post("/auth/login", json={"password": "test-options"})
    response = client.get("/options/three-month-volume?symbols=AAPL,MSFT")
    assert response.status_code == 200
    assert response.json()["results"][0]["total_volume"] == 100
    assert response.json()["results"][1]["total_volume"] is None
