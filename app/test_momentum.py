import asyncio

from fastapi.testclient import TestClient

from discover import _live_discovery_universe
from main import app
from marketdata import MarketDataCache, MarketDataClient, demo_candles
from momentum import score_symbol
from options_activity import analyze_options_activity


def test_score_symbol_returns_rankable_signal():
    benchmark = demo_candles("QQQ", 260)
    signal = score_symbol("NVDA", demo_candles("NVDA", 260), benchmark)

    assert signal.symbol == "NVDA"
    assert 0 <= signal.score <= 100
    assert signal.grade in {"A", "B", "C", "D", "F"}
    assert signal.setup in {"breakout", "pullback", "extended", "base", "weak"}
    assert signal.factors.return_3m is not None
    assert signal.notes


def test_demo_scan_endpoint_ranks_results():
    client = TestClient(app)

    response = client.get("/scan", params={"symbols": "NVDA,MSFT,AAPL", "demo": "true"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "demo"
    assert payload["benchmark"] == "QQQ"
    assert payload["count"] == 3
    scores = [item["score"] for item in payload["results"]]
    assert scores == sorted(scores, reverse=True)


def test_dashboard_served_at_root():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Stock Momentum AI" in response.text
    assert "2-Week Non-Earnings Movers" in response.text
    assert "Demo" not in response.text


def test_demo_discover_endpoint_returns_movers():
    client = TestClient(app)

    response = client.get("/discover", params={"demo": "true", "growth_min": "0.20", "limit": "10"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "demo"
    assert payload["count"] > 0
    assert payload["count"] <= 10
    assert payload["results"][0]["return_2w"] >= 0.20
    assert payload["results"][0]["likely_earnings_related"] is False
    assert payload["results"][0]["momentum_score"] >= 0
    assert payload["results"][0]["setup"]
    assert payload["results"][0]["call_activity"] in {"unusually elevated", "elevated", "normal", "unavailable"}
    assert payload["results"][0]["put_call_ratio"] is not None
    assert payload["results"][0]["put_call_source"] == "demo"
    assert payload["results"][0]["options_confirmation_score"] is not None
    assert payload["results"][0]["options_signal"] != "Options unavailable"
    assert payload["results"][0]["preferred_contract"] is not None


def test_live_discovery_universe_uses_sector_universe_not_sample(monkeypatch):
    monkeypatch.delenv("DISCOVERY_UNIVERSE", raising=False)

    universe = _live_discovery_universe()

    assert "PLTR" in universe
    assert "QQQ" not in universe
    assert len(universe) > 50


def test_live_scan_without_api_key_returns_503(monkeypatch):
    monkeypatch.delenv("MARKETDATA_API_KEY", raising=False)
    client = TestClient(app)

    response = client.get("/scan", params={"symbols": "NVDA", "demo": "false"})

    assert response.status_code == 503
    assert "MARKETDATA_API_KEY" in response.json()["detail"]


def test_marketdata_cache_round_trips_payload(tmp_path):
    cache = MarketDataCache(tmp_path)

    cache.set("stocks:candles:AAPL", {"s": "ok", "c": [100.0]})

    assert cache.get("stocks:candles:AAPL", ttl_seconds=None) == {"s": "ok", "c": [100.0]}


def test_bulk_candles_does_not_use_provider_cached_mode(monkeypatch):
    captured = {}
    client = MarketDataClient(api_key="test-token")

    async def fake_get(url, params, *, cache_ttl_seconds=300):
        captured["params"] = params
        captured["cache_ttl_seconds"] = cache_ttl_seconds
        return {
            "s": "ok",
            "symbol": ["AAPL"],
            "t": [1],
            "o": [10],
            "h": [11],
            "l": [9],
            "c": [10.5],
            "v": [1000],
        }

    monkeypatch.setattr(client, "_get", fake_get)

    candles = asyncio.run(client.bulk_candles(["AAPL"]))

    assert candles["AAPL"].close == 10.5
    assert captured["params"] == {"symbols": "AAPL"}
    assert captured["cache_ttl_seconds"] == 300


def test_historical_bulk_candles_are_cached_without_provider_cached_mode(monkeypatch):
    captured = {}
    client = MarketDataClient(api_key="test-token")

    async def fake_get(url, params, *, cache_ttl_seconds=300):
        captured["params"] = params
        captured["cache_ttl_seconds"] = cache_ttl_seconds
        return {
            "s": "ok",
            "symbol": ["AAPL"],
            "t": [1],
            "o": [10],
            "h": [11],
            "l": [9],
            "c": [10.5],
            "v": [1000],
        }

    monkeypatch.setattr(client, "_get", fake_get)

    asyncio.run(client.bulk_candles(["AAPL"], date="2026-08-14"))

    assert captured["params"] == {"symbols": "AAPL", "date": "2026-08-14"}
    assert captured["cache_ttl_seconds"] is None


def test_option_chain_accepts_column_only_payload(monkeypatch):
    client = MarketDataClient(api_key="test-token")

    async def fake_get(url, params, *, cache_ttl_seconds=300):
        return {
            "optionSymbol": ["AAPL270115C00100000"],
            "symbol": ["AAPL270115C00100000"],
            "expiration": ["2027-01-15"],
            "strike": [100],
            "side": ["call"],
            "bid": [12.0],
            "ask": [12.4],
            "mid": [12.2],
            "delta": [0.7],
            "openInterest": [1000],
            "volume": [250],
        }

    monkeypatch.setattr(client, "_get", fake_get)

    rows = asyncio.run(client.option_chain("AAPL"))

    assert rows[0]["side"] == "call"
    assert rows[0]["volume"] == 250


def test_options_confirmation_score_rewards_bullish_call_activity():
    activity = analyze_options_activity(
        [
            {
                "optionSymbol": "AAPL260828C00300000",
                "expiration": "2026-08-28",
                "strike": 300,
                "side": "call",
                "volume": 5000,
                "openInterest": 10000,
                "delta": 0.65,
                "bid": 5.0,
                "ask": 5.4,
                "mid": 5.2,
            },
            {
                "optionSymbol": "AAPL260828P00290000",
                "expiration": "2026-08-28",
                "strike": 290,
                "side": "put",
                "volume": 1500,
                "openInterest": 9000,
                "delta": -0.35,
            },
        ]
    )

    assert activity["put_call_ratio"] == 0.3
    assert activity["options_confirmation_score"] >= 80
    assert activity["options_signal"] == "Confirmed bullish momentum"


def test_options_confirmation_score_flags_speculative_call_activity():
    activity = analyze_options_activity(
        [
            {
                "optionSymbol": "XYZ260828C00100000",
                "expiration": "2026-08-28",
                "strike": 100,
                "side": "call",
                "volume": 20000,
                "openInterest": 500,
                "delta": 0.95,
            },
            {
                "optionSymbol": "XYZ260828P00090000",
                "expiration": "2026-08-28",
                "strike": 90,
                "side": "put",
                "volume": 4000,
                "openInterest": 800,
                "delta": -0.2,
            },
        ]
    )

    assert activity["call_activity"] == "unusually elevated"
    assert activity["options_confirmation_score"] < 80
    assert activity["options_signal"] == "Speculative call activity"
