import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from fastapi.testclient import TestClient

from discover import _build_candidate, _live_discovery_universe, _sort_discovery_candidates
from main import app
from fmp import FmpCache, FmpClient, FmpError, demo_candles
from momentum import _score, adjusted_momentum_score, score_symbol
from options_activity import analyze_options_activity
from phase2_refresh import (
    _apply_stored_candle_fallback,
    _calendar_day_return,
    _fetch_symbol_candles_with_retry,
    _unresolved_candles_log,
    _earnings_metadata_for_symbol,
    _filter_symbols_by_average_volume,
    _filter_symbols_by_price_movement,
    _load_symbols,
    _momentum_signal_to_db_with_metadata,
    _option_activity_to_db,
    _select_top_price_mover_symbols,
    _score_options,
    _screener_warning_logs,
)
from phase2_db import fetch_latest_refresh_run, fetch_phase2_candidates, fetch_recent_daily_bars, insert_refresh_log, prune_scores_for_run, upsert_momentum_scores
from phase2_pipeline import score_market_candidates
from schemas import Candle, DiscoveryCandidate, MomentumFactors
from symbol_source import configured_phase2_symbols, load_phase2_symbols

TEST_PASSWORD = "test-password"


def login(client: TestClient, password: str = TEST_PASSWORD):
    return client.post("/auth/login", json={"password": password})


def test_score_symbol_returns_rankable_signal():
    benchmark = demo_candles("BENCH", 260)
    signal = score_symbol("NVDA", demo_candles("NVDA", 260), benchmark)

    assert signal.symbol == "NVDA"
    assert 0 <= signal.score <= 100
    assert signal.grade in {"A", "B", "C", "D", "F"}
    assert signal.setup in {"breakout", "pullback", "extended", "base", "weak"}
    assert signal.factors.return_1m is not None
    assert signal.factors.return_3m is None
    assert signal.factors.return_6m is None
    assert signal.factors.return_12m is None
    assert signal.factors.distance_to_200d_sma is None
    assert signal.factors.rsi_14 is not None
    assert 0 <= signal.factors.rsi_14 <= 100
    assert signal.factors.current_volume_ratio_20d is not None
    assert signal.notes


def test_adjusted_momentum_score_uses_rsi_and_call_activity():
    base = 80

    assert adjusted_momentum_score(base, rsi_14=82) == 74
    assert adjusted_momentum_score(base, call_activity="elevated") == 83
    assert adjusted_momentum_score(base, rsi_14=82, call_activity="unusually elevated") == 80


def test_adjusted_momentum_score_rewards_current_volume_above_average():
    base = 80

    assert adjusted_momentum_score(base, current_volume_ratio_20d=1.5) == 84
    assert adjusted_momentum_score(base, current_volume_ratio_20d=1.0) == 80
    assert adjusted_momentum_score(base, current_volume_ratio_20d=0.5) == 76


def test_momentum_score_uses_only_one_month_return_horizon():
    factors = MomentumFactors(
        return_1m=0.10,
        return_3m=0,
        return_6m=0,
        return_12m=0,
        benchmark_return_3m=0,
        relative_strength_3m=0,
        distance_to_20d_high=None,
        distance_to_50d_sma=None,
        distance_to_200d_sma=None,
        volume_ratio_20d_vs_60d=None,
        current_volume_ratio_20d=None,
        rsi_14=None,
    )
    longer_horizon_move = factors.model_copy(
        update={
            "return_3m": 2.0,
            "return_6m": -0.9,
            "return_12m": 4.0,
            "relative_strength_3m": 1.5,
        }
    )

    assert _score(factors) == 62
    assert _score(longer_horizon_move) == 62
    assert _score(factors.model_copy(update={"return_1m": 0.20})) == 72


def test_score_symbol_compares_current_volume_to_prior_20_days():
    benchmark = demo_candles("BENCH", 260)
    candles = _with_volume(demo_candles("VOLUME", 260), 4_000_000)
    candles[-1] = candles[-1].model_copy(update={"volume": 8_000_000})

    signal = score_symbol("VOLUME", candles, benchmark)

    assert signal.factors.current_volume_ratio_20d == 2.0
    assert "Current volume is 2.00x the prior 20d average." in signal.notes


def test_demo_scan_endpoint_ranks_results(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    client = TestClient(app)
    login(client)

    response = client.get("/scan", params={"symbols": "NVDA,MSFT,AAPL", "demo": "true"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "demo"
    assert payload["benchmark"] == ""
    assert payload["count"] == 3
    scores = [item["score"] for item in payload["results"]]
    assert scores == sorted(scores, reverse=True)


def test_dashboard_served_at_root():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Stock Momentum AI" in response.text
    assert "PostgreSQL Momentum Candidates" in response.text
    assert "Date Added" in response.text
    assert "Price When Added" in response.text
    assert 'data-sort-key="calls"' not in response.text
    assert "Overextended" in response.text
    assert "Pullback Watch" in response.text
    assert "Biotech Catalysts" in response.text
    assert "Future Event Tracker" in response.text
    assert "Demo" not in response.text
    for sort_key in ("symbol", "sector", "price", "rsi", "score", "growth", "volume", "options"):
        assert f'data-sort-key="{sort_key}"' in response.text
    for sort_key in ("rank", "signal", "source"):
        assert f'data-sort-key="{sort_key}"' not in response.text


def test_demo_discover_endpoint_returns_movers(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    client = TestClient(app)
    login(client)

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


def test_discovery_candidate_requires_average_daily_volume_over_3m():
    candles = _with_volume(demo_candles("LOWV", 260), 3_000_000)

    candidate = asyncio.run(
        _build_candidate(
            "LOWV",
            "Technology",
            candles,
            [],
            earnings=[],
            options_activity=None,
        )
    )

    assert candidate is None


def test_discovery_candidate_requires_stock_price_over_10():
    candles = _with_volume(demo_candles("CHEAP", 260), 4_000_000)
    candles[-1] = candles[-1].model_copy(update={"open": 10, "high": 10, "low": 10, "close": 10})

    candidate = asyncio.run(
        _build_candidate(
            "CHEAP",
            "Technology",
            candles,
            [],
            earnings=[],
            options_activity=None,
        )
    )

    assert candidate is None


def test_live_discovery_universe_uses_fmp_screener(monkeypatch):
    def fake_screener(*, limit=500, min_average_volume=None, min_price=None):
        assert limit == 25
        assert min_average_volume == 3_000_000
        assert min_price == 10
        return [
            {"symbol": "MRVL", "sector": "Technology"},
            {"symbol": "IBM", "sector": "Technology"},
        ]

    monkeypatch.setenv("DISCOVERY_SYMBOL_LIMIT", "25")
    monkeypatch.setenv("DISCOVERY_MIN_AVERAGE_VOLUME", "3000000")
    monkeypatch.setenv("DISCOVERY_MIN_PRICE", "10")
    monkeypatch.setattr("discover.fetch_fmp_screener_symbols", fake_screener)

    universe = _live_discovery_universe()

    assert universe == {"MRVL": "Technology", "IBM": "Technology"}


def test_live_discovery_universe_can_use_static_fallback(monkeypatch):
    monkeypatch.setenv("DISCOVERY_SYMBOL_SOURCE", "static")

    universe = _live_discovery_universe()

    assert "PLTR" in universe
    assert len(universe) > 50


def test_discovery_sort_uses_score_then_one_month_return():
    candidates = [
        _candidate("AAA", momentum_score=90, return_1m=0.15, options_confirmation_score=40),
        _candidate("BBB", momentum_score=91, return_1m=0.01, options_confirmation_score=None),
        _candidate("CCC", momentum_score=90, return_1m=0.05, options_confirmation_score=88),
        _candidate("DDD", momentum_score=90, return_1m=0.10, options_confirmation_score=None),
    ]

    _sort_discovery_candidates(candidates)

    assert [candidate.symbol for candidate in candidates] == ["BBB", "AAA", "DDD", "CCC"]


def test_scan_requires_login(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    client = TestClient(app)

    response = client.get("/scan", params={"symbols": "NVDA", "demo": "true"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Login required"


def test_login_accepts_configured_password(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    client = TestClient(app)

    bad_response = login(client, "wrong")
    good_response = login(client, TEST_PASSWORD)
    status_response = client.get("/auth/status")

    assert bad_response.status_code == 401
    assert good_response.status_code == 200
    assert status_response.json()["authenticated"] is True


def test_live_scan_without_api_key_returns_503(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    client = TestClient(app)
    login(client)

    response = client.get("/scan", params={"symbols": "NVDA", "demo": "false"})

    assert response.status_code == 503
    assert "FMP_API_KEY" in response.json()["detail"]


def test_live_scan_skips_symbol_level_fmp_errors(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.api_key = "test-token"

        async def candles(self, symbol, countback=260):
            if symbol == "AVGO":
                raise FmpError("FMP returned HTTP 402")
            return demo_candles(symbol, countback)

    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    monkeypatch.setattr("main.FmpClient", FakeClient)
    client = TestClient(app)
    login(client)

    response = client.get("/scan", params={"symbols": "AAPL,AVGO", "demo": "false"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "live"
    assert payload["count"] == 1
    assert payload["results"][0]["symbol"] == "AAPL"
    assert payload["skipped_symbols"] == ["AVGO"]


def test_phase2_configured_symbols_are_env_driven(monkeypatch):
    monkeypatch.setenv("PHASE2_SYMBOLS", "sndk, nvda, SNDK")

    symbols = configured_phase2_symbols()

    assert [item["symbol"] for item in symbols] == ["SNDK", "NVDA"]


def test_phase2_listed_source_includes_other_exchanges(monkeypatch):
    payloads = {
        "nasdaqlisted": (
            "Symbol|Security Name|Market Category|Test Issue\n"
            "MRVL|Marvell Technology, Inc.|Q|N\n"
            "TEST|Test Issue|Q|Y\n"
            "File Creation Time: 0819202612:00||||\n"
        ),
        "otherlisted": (
            "ACT Symbol|Security Name|Exchange|Test Issue\n"
            "IBM|International Business Machines Corporation|N|N\n"
            "BRK/B|Berkshire Hathaway Inc.|N|N\n"
            "File Creation Time: 0819202612:00||||\n"
        ),
    }

    def fake_download(url):
        return payloads["nasdaqlisted"] if "nasdaqlisted" in url else payloads["otherlisted"]

    monkeypatch.setattr("symbol_source._download_text", fake_download)

    symbols = load_phase2_symbols(source="listed", limit=10)

    assert [item["symbol"] for item in symbols] == ["MRVL", "IBM"]
    assert symbols[0]["exchange"] == "NASDAQ"
    assert symbols[1]["exchange"] == "N"


def test_phase2_fmp_source_uses_screener(monkeypatch):
    captured = {}

    def fake_download(url):
        captured["url"] = url
        return (
            '[{"symbol":"MRVL","companyName":"Marvell Technology, Inc.","exchangeShortName":"NASDAQ","sector":"Technology"},'
            '{"symbol":"BRK.B","companyName":"Berkshire Hathaway Inc.","exchangeShortName":"NYSE","sector":"Financial Services"}]'
        )

    monkeypatch.setenv("FMP_API_KEY", "test-token")
    monkeypatch.setattr("symbol_source._download_text", fake_download)

    symbols = load_phase2_symbols(source="fmp", limit=100)

    assert [item["symbol"] for item in symbols] == ["MRVL", "BRK.B"]
    assert symbols[0]["exchange"] == "NASDAQ"
    assert "avgVolumeMoreThan=3000000" in captured["url"]
    assert "priceMoreThan=10" in captured["url"]
    assert "apikey=test-token" in captured["url"]


def test_phase2_refresh_load_symbols_passes_screener_thresholds(monkeypatch):
    captured = {}

    def fake_load_phase2_symbols(*, source, limit, min_average_volume=None, min_price=None):
        captured.update(
            {
                "source": source,
                "limit": limit,
                "min_average_volume": min_average_volume,
                "min_price": min_price,
            }
        )
        return [{"symbol": "MRVL", "exchange": "NASDAQ"}]

    args = SimpleNamespace(
        demo=False,
        source="fmp",
        benchmark="",
        symbol_limit=75,
        min_average_volume=1_500_000,
        min_price=5.0,
    )
    monkeypatch.setattr("phase2_refresh.load_phase2_symbols", fake_load_phase2_symbols)

    symbols = _load_symbols(args)

    assert symbols == [{"symbol": "MRVL", "exchange": "NASDAQ"}]
    assert captured == {
        "source": "fmp",
        "limit": 75,
        "min_average_volume": 1_500_000,
        "min_price": 5.0,
    }


def test_phase2_warns_when_fmp_screener_returns_fewer_than_50_symbols(monkeypatch):
    monkeypatch.setenv("FMP_SCREENER_COUNTRY", "US")
    args = SimpleNamespace(
        demo=False,
        source="fmp",
        symbol_limit=500,
        min_average_volume=3_000_000,
        min_price=10,
    )

    logs = _screener_warning_logs(args, [{"symbol": f"SYM{index}"} for index in range(44)])

    assert len(logs) == 1
    assert logs[0]["level"] == "warning"
    assert logs[0]["source"] == "fmp.company-screener"
    assert "fewer than 50" in logs[0]["message"]
    assert '"symbols_returned":44' in logs[0]["details"]


def test_phase2_does_not_warn_when_fmp_screener_returns_at_least_50_symbols():
    args = SimpleNamespace(
        demo=False,
        source="fmp",
        symbol_limit=500,
        min_average_volume=3_000_000,
        min_price=10,
    )

    assert _screener_warning_logs(args, [{"symbol": f"SYM{index}"} for index in range(50)]) == []


def test_phase2_prefilter_uses_average_daily_volume(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.api_key = "test-token"

        async def candles(self, symbol, countback=20):
            volume = 4_000_000 if symbol == "HIGH" else 3_000_000
            candles = _with_volume(demo_candles(symbol, countback), volume)
            if symbol == "CHEAP":
                candles = _with_volume(demo_candles(symbol, countback), 4_000_000)
                candles[-1] = candles[-1].model_copy(update={"open": 10, "high": 10, "low": 10, "close": 10})
            return candles

    monkeypatch.setattr("phase2_refresh.FmpClient", FakeClient)

    symbols = asyncio.run(
        _filter_symbols_by_average_volume(
            [{"symbol": "HIGH"}, {"symbol": "EDGE"}, {"symbol": "CHEAP"}],
            min_average_volume=3_000_000,
            min_price=10,
            days=20,
            concurrency=2,
        )
    )

    assert [item["symbol"] for item in symbols] == ["HIGH"]


def test_phase2_prefilter_keeps_top_price_movers(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.api_key = "test-token"

        async def candles(self, symbol, countback=40):
            multipliers = {"MRNA": 1.6, "NVDA": 1.3, "AAPL": 1.1, "LOWV": 2.0}
            candles = _with_calendar_day_move(demo_candles(symbol, countback), 30, multipliers[symbol])
            volume = 1_000_000 if symbol == "LOWV" else 4_000_000
            return _with_volume(candles, volume)

    monkeypatch.setattr("phase2_refresh.FmpClient", FakeClient)

    symbols = asyncio.run(
        _filter_symbols_by_price_movement(
            [{"symbol": "AAPL"}, {"symbol": "MRNA"}, {"symbol": "NVDA"}, {"symbol": "LOWV"}],
            min_average_volume=3_000_000,
            min_price=10,
            volume_days=20,
            movement_days=30,
            limit=2,
            concurrency=2,
        )
    )

    assert [item["symbol"] for item in symbols] == ["MRNA", "NVDA"]


def test_phase2_prefilter_uses_stored_bars_for_provider_misses(monkeypatch):
    monkeypatch.setenv("PHASE2_FMP_RETRY_BACKOFF_SECONDS", "0")
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.api_key = "test-token"

        async def candles(self, symbol, countback=40):
            if symbol == "PURR":
                raise FmpError("temporary provider miss")
            multipliers = {"NVDA": 1.3, "AAPL": 1.1}
            candles = _with_calendar_day_move(demo_candles(symbol, countback), 30, multipliers[symbol])
            return _with_volume(candles, 4_000_000)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_fetch_recent_daily_bars(connection, symbols, *, countback):
        assert symbols == ["PURR"]
        assert countback == 40
        candles = _with_volume(
            _with_calendar_day_move(demo_candles("PURR", countback), 30, 1.9),
            4_000_000,
        )
        return _daily_bar_rows("PURR", candles)

    monkeypatch.setattr("phase2_refresh.FmpClient", FakeClient)
    monkeypatch.setattr("phase2_refresh.connect", lambda: FakeConnection())
    monkeypatch.setattr("phase2_refresh.fetch_recent_daily_bars", fake_fetch_recent_daily_bars)

    symbols = asyncio.run(
        _filter_symbols_by_price_movement(
            [{"symbol": "AAPL"}, {"symbol": "PURR"}, {"symbol": "NVDA"}],
            min_average_volume=3_000_000,
            min_price=10,
            volume_days=20,
            movement_days=30,
            limit=2,
            concurrency=2,
        )
    )

    assert [item["symbol"] for item in symbols] == ["PURR", "NVDA"]


def test_phase2_fetch_recent_daily_bars_reads_latest_rows():
    class FakeCursor:
        query = ""
        params = None

        def execute(self, query, params=None):
            self.query = query
            self.params = params
            return self

        def fetchall(self):
            return [
                {
                    "symbol": "MRNA",
                    "bar_date": "2026-08-21",
                    "open": 140,
                    "high": 150,
                    "low": 130,
                    "close": 145.13,
                    "volume": 86_865_450,
                }
            ]

    class FakeConnection:
        cursor_instance = FakeCursor()

        def cursor(self):
            return self

        def __enter__(self):
            return self.cursor_instance

        def __exit__(self, *args):
            return False

    connection = FakeConnection()

    rows = fetch_recent_daily_bars(connection, ["MRNA"], countback=260)

    assert rows[0]["symbol"] == "MRNA"
    assert "row_number() over (partition by symbol order by bar_date desc)" in connection.cursor_instance.query
    assert connection.cursor_instance.params == (["MRNA"], 260)


def test_phase2_refresh_uses_stored_bars_for_provider_misses(monkeypatch):
    reference = demo_candles("AAPL", 80)
    fallback = demo_candles("MRNA", 80)
    fallback[-1] = fallback[-1].model_copy(update={"timestamp": reference[-1].timestamp})

    def fake_fetch_recent_daily_bars(connection, symbols, *, countback):
        assert symbols == ["MRNA"]
        assert countback == 80
        return _daily_bar_rows("MRNA", fallback)

    monkeypatch.setattr("phase2_refresh.fetch_recent_daily_bars", fake_fetch_recent_daily_bars)
    candle_map = {"AAPL": reference}

    unresolved = _apply_stored_candle_fallback(None, candle_map, ["AAPL", "MRNA"], countback=80)

    assert unresolved == []
    assert candle_map["MRNA"][-1].close == fallback[-1].close
    assert len(candle_map["MRNA"]) == 80


def test_phase2_refresh_rejects_stale_stored_bars(monkeypatch):
    reference = demo_candles("AAPL", 80)
    fallback = demo_candles("MRNA", 80)
    fallback[-1] = fallback[-1].model_copy(update={"timestamp": reference[-1].timestamp - 86_400})

    monkeypatch.setattr(
        "phase2_refresh.fetch_recent_daily_bars",
        lambda connection, symbols, *, countback: _daily_bar_rows("MRNA", fallback),
    )
    candle_map = {"AAPL": reference}

    unresolved = _apply_stored_candle_fallback(None, candle_map, ["AAPL", "MRNA"], countback=80)

    assert unresolved == ["MRNA"]
    assert "MRNA" not in candle_map


def test_phase2_candle_retry_logs_final_failure(caplog):
    calls = []

    class FailingClient:
        async def candles(self, symbol, *, countback):
            calls.append(symbol)
            raise FmpError("FMP rate limit reached.")

    with caplog.at_level("WARNING", logger="phase2_refresh"):
        candles = asyncio.run(
            _fetch_symbol_candles_with_retry(FailingClient(), "MRNA", countback=60, attempts=3, backoff_seconds=0)
        )

    assert candles == []
    assert calls == ["MRNA", "MRNA", "MRNA"]
    assert "FMP candles failed for MRNA after 3 attempts: FMP rate limit reached." in caplog.text


def test_phase2_skips_symbols_without_candles_below_missing_ratio():
    log = _unresolved_candles_log(["MRNA", "GME"], symbols_requested=50, max_missing_ratio=0.5)

    assert log["level"] == "warning"
    assert log["source"] == "fmp.candles"
    assert log["message"].startswith("Skipped 2 of 50 symbols")
    assert json.loads(log["details"])["unresolved_symbols"] == ["MRNA", "GME"]


def test_phase2_fails_when_too_many_symbols_lack_candles():
    with pytest.raises(FmpError, match="No complete candle history available for"):
        _unresolved_candles_log([f"S{i}" for i in range(26)], symbols_requested=50, max_missing_ratio=0.5)


def test_phase2_selects_top_30_day_price_movers_before_scoring():
    candle_map = {
        "MRNA": _with_calendar_day_move(demo_candles("MRNA", 80), 30, 1.6),
        "AAPL": _with_calendar_day_move(demo_candles("AAPL", 80), 30, 1.1),
        "NVDA": _with_calendar_day_move(demo_candles("NVDA", 80), 30, 1.3),
        "LOWV": _with_volume(_with_calendar_day_move(demo_candles("LOWV", 80), 30, 2.0), 1_000_000),
    }

    symbols = _select_top_price_mover_symbols(
        candle_map,
        ["AAPL", "MRNA", "NVDA", "LOWV"],
        limit=2,
        movement_days=30,
        min_price=10,
        min_average_volume=3_000_000,
    )

    assert symbols == ["MRNA", "NVDA"]


def test_phase2_calendar_day_return_uses_bar_at_or_before_target_date():
    candles = _with_calendar_day_move(demo_candles("CAL", 80), 30, 1.25)

    assert round(_calendar_day_return(candles, 30), 4) == 0.25


def test_phase2_scores_market_candidates():
    benchmark = demo_candles("BENCH", 260)
    candle_map = {
        "SNDK": demo_candles("SNDK", 260),
        "NVDA": demo_candles("NVDA", 260),
        "BENCH": benchmark,
    }

    scores = score_market_candidates(candle_map, benchmark, top_n=2, benchmark_symbol="BENCH")

    assert len(scores) == 2
    assert "BENCH" not in {score.symbol for score in scores}
    assert scores[0].score >= scores[1].score


def test_phase2_earnings_metadata_marks_recent_report():
    candles = demo_candles("CRM", 60)
    report_date = datetime.fromtimestamp(candles[-1].timestamp, UTC).date().isoformat()

    metadata = _earnings_metadata_for_symbol([{"date": report_date}], candles)

    assert metadata["earnings_checked"] is True
    assert metadata["earnings_near_move"] is True
    assert metadata["likely_earnings_related"] is True
    assert metadata["earnings_date"] == report_date


def test_phase2_momentum_db_payload_includes_earnings_metadata():
    signal = score_symbol("CRM", demo_candles("CRM", 60), [])

    row = _momentum_signal_to_db_with_metadata(
        signal,
        {
            "earnings_checked": True,
            "earnings_near_move": True,
            "likely_earnings_related": True,
            "earnings_date": "2026-08-26",
        },
    )

    assert '"likely_earnings_related":true' in row["factors"]
    assert "Likely earnings-related move on 2026-08-26." in row["notes"]


def test_phase2_scores_filter_low_average_daily_volume():
    benchmark = demo_candles("BENCH", 260)
    candle_map = {
        "LOWV": _with_volume(demo_candles("LOWV", 260), 1_000_000),
        "CHEAP": _with_last_price(_with_volume(demo_candles("CHEAP", 260), 4_000_000), 10),
        "NVDA": demo_candles("NVDA", 260),
        "BENCH": benchmark,
    }

    scores = score_market_candidates(candle_map, benchmark, top_n=5, benchmark_symbol="BENCH")

    assert "LOWV" not in {score.symbol for score in scores}
    assert "CHEAP" not in {score.symbol for score in scores}
    assert "NVDA" in {score.symbol for score in scores}


def test_phase2_candidates_requires_database_url(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(app)
    login(client)

    response = client.get("/phase2/candidates")

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


def test_phase2_candidates_sort_by_score_then_growth():
    class FakeCursor:
        query = ""
        params = None

        def execute(self, query, params=None):
            self.query = query
            self.params = params
            return self

        def fetchall(self):
            return []

    class FakeConnection:
        cursor_instance = FakeCursor()

        def cursor(self):
            return self

        def __enter__(self):
            return self.cursor_instance

        def __exit__(self, *args):
            return False

    connection = FakeConnection()

    fetch_phase2_candidates(connection, limit=10)

    assert "order by m.score desc, (m.factors->>'return_1m')::numeric desc nulls last" in connection.cursor_instance.query
    assert "and m.last_close > %s" in connection.cursor_instance.query
    assert "m.factors->>'likely_earnings_related'" in connection.cursor_instance.query
    assert connection.cursor_instance.params == (10.0, False, 10)
    assert "o.put_call_source" in connection.cursor_instance.query
    assert "o.call_volume" in connection.cursor_instance.query
    assert "o.call_activity_ratio" in connection.cursor_instance.query


def test_phase2_candidates_can_exclude_earnings_related_rows():
    class FakeCursor:
        query = ""
        params = None

        def execute(self, query, params=None):
            self.query = query
            self.params = params
            return self

        def fetchall(self):
            return []

    class FakeConnection:
        cursor_instance = FakeCursor()

        def cursor(self):
            return self

        def __enter__(self):
            return self.cursor_instance

        def __exit__(self, *args):
            return False

    connection = FakeConnection()

    fetch_phase2_candidates(connection, limit=25, min_price=12.5, exclude_earnings=True)

    assert connection.cursor_instance.params == (12.5, True, 25)


def test_phase2_refresh_prunes_stale_scores():
    class FakeCursor:
        queries = []

        def execute(self, query, params=None):
            self.queries.append((query, params))
            return self

    class FakeConnection:
        cursor_instance = FakeCursor()
        committed = False

        def cursor(self):
            return self

        def __enter__(self):
            return self.cursor_instance

        def __exit__(self, *args):
            return False

        def commit(self):
            self.committed = True

    connection = FakeConnection()

    prune_scores_for_run(connection, 7)

    assert connection.committed is True
    assert "delete from option_scores where run_id is distinct from %s" in connection.cursor_instance.queries[0][0]
    assert "delete from momentum_scores where run_id is distinct from %s" in connection.cursor_instance.queries[1][0]
    assert connection.cursor_instance.queries[0][1] == (7,)
    assert connection.cursor_instance.queries[1][1] == (7,)


def test_first_listed_timestamp_uses_score_timestamp():
    class FakeCursor:
        queries = []

        def executemany(self, query, values):
            self.queries.append((query, values))

        def execute(self, query, params=None):
            self.queries.append((query, params))
            return self

    class FakeConnection:
        cursor_instance = FakeCursor()

        def cursor(self):
            return self

        def __enter__(self):
            return self.cursor_instance

        def __exit__(self, *args):
            return False

        def commit(self):
            return None

    upsert_momentum_scores(FakeConnection(), 3, [{
        "symbol": "AAPL", "score": 90, "grade": "A", "setup": "breakout",
        "last_close": 200, "factors": {}, "notes": [],
    }])

    query = FakeConnection.cursor_instance.queries[1][0]
    assert "set first_listed_at = m.scored_at" in query
    assert "first_listed_at = now()" not in query


def test_phase2_refresh_logs_are_inserted_and_returned_with_latest_run():
    class FakeCursor:
        def __init__(self):
            self.queries = []
            self.fetchone_count = 0

        def execute(self, query, params=None):
            self.queries.append((query, params))
            return self

        def fetchone(self):
            self.fetchone_count += 1
            if self.fetchone_count == 2:
                return {"table_name": "refresh_logs"}
            return {
                "id": 7,
                "started_at": datetime(2026, 8, 26, tzinfo=UTC),
                "finished_at": datetime(2026, 8, 26, 22, 30, tzinfo=UTC),
                "status": "succeeded",
                "symbols_requested": 44,
                "symbols_scored": 40,
                "error": None,
            }

        def fetchall(self):
            return [
                {
                    "id": 11,
                    "run_id": 7,
                    "created_at": datetime(2026, 8, 26, 22, 29, tzinfo=UTC),
                    "level": "warning",
                    "source": "fmp.company-screener",
                    "message": "FMP company-screener returned fewer than 50 usable symbols: 44.",
                    "details": {"symbols_returned": 44},
                }
            ]

    class FakeConnection:
        cursor_instance = FakeCursor()
        committed = False

        def cursor(self):
            return self

        def __enter__(self):
            return self.cursor_instance

        def __exit__(self, *args):
            return False

        def commit(self):
            self.committed = True

    connection = FakeConnection()

    insert_refresh_log(
        connection,
        7,
        level="warning",
        source="fmp.company-screener",
        message="FMP company-screener returned fewer than 50 usable symbols: 44.",
        details='{"symbols_returned":44}',
    )
    latest = fetch_latest_refresh_run(connection)

    assert connection.committed is True
    assert "insert into refresh_logs" in connection.cursor_instance.queries[0][0]
    assert connection.cursor_instance.queries[0][1] == (
        7,
        "warning",
        "fmp.company-screener",
        "FMP company-screener returned fewer than 50 usable symbols: 44.",
        '{"symbols_returned":44}',
    )
    assert latest["logs"][0]["source"] == "fmp.company-screener"
    assert latest["logs"][0]["details"] == {"symbols_returned": 44}


def test_phase2_option_activity_serializes_for_db():
    activity = analyze_options_activity(
        [
            {
                "optionSymbol": "AMD260828C00200000",
                "expiration": "2026-08-28",
                "strike": 200,
                "side": "call",
                "volume": 8000,
                "openInterest": 12000,
                "delta": 0.7,
            },
            {
                "optionSymbol": "AMD260828P00190000",
                "expiration": "2026-08-28",
                "strike": 190,
                "side": "put",
                "volume": 2000,
                "openInterest": 10000,
                "delta": -0.3,
            },
        ]
    )

    row = _option_activity_to_db("AMD", activity)

    assert row["symbol"] == "AMD"
    assert row["put_call_ratio"] == 0.25
    assert row["call_volume"] == 8000
    assert row["call_activity"] in {"elevated", "unusually elevated"}
    assert row["options_confirmation_score"] is not None
    assert "AMD260828C00200000" in row["preferred_contract"]


def _candidate(
    symbol: str,
    *,
    momentum_score: float,
    options_confirmation_score: float | None,
    return_1m: float = 0.1,
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        symbol=symbol,
        sector="Technology",
        momentum_score=momentum_score,
        setup="Momentum watch",
        price_signal="momentum continuation",
        relative_strength_percentile=50,
        return_1m=return_1m,
        return_2w=0.1,
        start_close=100,
        last_close=110,
        options_confirmation_score=options_confirmation_score,
        options_signal="Options unavailable" if options_confirmation_score is None else "Options neutral",
        earnings_near_move=False,
        earnings_checked=True,
        likely_earnings_related=False,
        catalyst="price momentum; catalyst not identified",
    )


def _with_volume(candles: list[Candle], volume: int) -> list[Candle]:
    return [
        Candle(
            timestamp=candle.timestamp,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=volume,
        )
        for candle in candles
    ]


def _with_last_price(candles: list[Candle], price: float) -> list[Candle]:
    updated = list(candles)
    updated[-1] = updated[-1].model_copy(update={"open": price, "high": price, "low": price, "close": price})
    return updated


def _with_calendar_day_move(candles: list[Candle], days: int, multiplier: float) -> list[Candle]:
    updated = list(candles)
    last_date = datetime.fromtimestamp(updated[-1].timestamp, UTC).date()
    target_date = last_date - timedelta(days=days)
    start_index = 0
    for index, candle in enumerate(updated):
        if datetime.fromtimestamp(candle.timestamp, UTC).date() <= target_date:
            start_index = index
        else:
            break
    start_close = updated[start_index].close
    last_close = round(start_close * multiplier, 2)
    updated[-1] = updated[-1].model_copy(
        update={
            "open": last_close,
            "high": last_close,
            "low": last_close,
            "close": last_close,
        }
    )
    return updated


def _daily_bar_rows(symbol: str, candles: list[Candle]) -> list[dict]:
    return [
        {
            "symbol": symbol,
            "bar_date": datetime.fromtimestamp(candle.timestamp, UTC).date(),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for candle in candles
    ]


def test_fmp_cache_round_trips_payload(tmp_path):
    cache = FmpCache(tmp_path)

    cache.set("stocks:candles:AAPL", {"s": "ok", "c": [100.0]})

    assert cache.get("stocks:candles:AAPL", ttl_seconds=None) == {"s": "ok", "c": [100.0]}


def test_bulk_candles_uses_fmp_quote_batches(monkeypatch):
    captured = {}
    client = FmpClient(api_key="test-token")

    async def fake_get(url, params, *, cache_ttl_seconds=300):
        captured["url"] = url
        captured["params"] = params
        captured["cache_ttl_seconds"] = cache_ttl_seconds
        return [{"symbol": "AAPL", "timestamp": 1, "open": 10, "dayHigh": 11, "dayLow": 9, "price": 10.5, "volume": 1000}]

    monkeypatch.setattr(client, "_get", fake_get)

    candles = asyncio.run(client.bulk_candles(["AAPL"]))

    assert candles["AAPL"].close == 10.5
    assert captured["url"].endswith("/quote")
    assert captured["params"] == {"symbol": "AAPL"}
    assert captured["cache_ttl_seconds"] == 300


def test_historical_bulk_candles_uses_fmp_eod_date(monkeypatch):
    captured = {}
    client = FmpClient(api_key="test-token")

    async def fake_get(url, params, *, cache_ttl_seconds=300):
        captured["url"] = url
        captured["params"] = params
        captured["cache_ttl_seconds"] = cache_ttl_seconds
        return [{"date": "2026-08-14", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000}]

    monkeypatch.setattr(client, "_get", fake_get)

    candles = asyncio.run(client.bulk_candles(["AAPL"], date="2026-08-14"))

    assert candles["AAPL"].close == 10.5
    assert captured["url"].endswith("/historical-price-eod/full")
    assert captured["params"] == {"symbol": "AAPL", "from": "2026-08-14", "to": "2026-08-14"}
    assert captured["cache_ttl_seconds"] is None


def test_option_chain_returns_empty_without_options_provider(monkeypatch):
    monkeypatch.delenv("FMP_OPTIONS_CHAIN_PATH", raising=False)
    monkeypatch.setenv("FMP_POSITIONS_SUMMARY_ENABLED", "false")
    client = FmpClient(api_key="test-token")

    assert asyncio.run(client.option_chain("MSFT")) == []
    assert client.options_configured is False


def test_phase2_score_options_returns_unavailable_without_fmp_options(monkeypatch):
    class FakeClient:
        options_configured = False

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("phase2_refresh.FmpClient", FakeClient)

    rows = asyncio.run(_score_options(symbols=["AAPL", "MSFT"], run_id=1, demo=False, concurrency=2))

    assert [row["symbol"] for row in rows] == ["AAPL", "MSFT"]
    assert {row["options_signal"] for row in rows} == {"Options unavailable"}
    assert {row["call_activity"] for row in rows} == {"unavailable"}


def test_fmp_client_requires_fmp_env_vars_for_options(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("FMP_OPTIONS_CHAIN_PATH", raising=False)
    monkeypatch.setenv("FMP_POSITIONS_SUMMARY_ENABLED", "false")
    client = FmpClient()

    assert client.api_key is None
    assert client.options_configured is False
    assert asyncio.run(client.option_chain("AAPL")) == []


def test_option_chain_normalizes_configured_row_provider(monkeypatch):
    monkeypatch.setenv("FMP_OPTIONS_CHAIN_PATH", "options-chain")
    monkeypatch.setenv("FMP_POSITIONS_SUMMARY_ENABLED", "false")
    client = FmpClient(api_key="test-token")

    async def fake_get(url, params, *, cache_ttl_seconds=300):
        assert url.endswith("/options-chain")
        assert params == {"symbol": "AAPL"}
        return {
            "calls": [
                {
                    "contractSymbol": "AAPL260828C00300000",
                    "expirationDate": "2026-08-28",
                    "strikePrice": 300,
                    "bid": 5,
                    "ask": 5.4,
                    "delta": 0.65,
                    "open_interest": 10000,
                    "volume": 5000,
                }
            ],
            "puts": [
                {
                    "contractSymbol": "AAPL260828P00290000",
                    "expirationDate": "2026-08-28",
                    "strikePrice": 290,
                    "volume": 1500,
                    "open_interest": 9000,
                }
            ],
        }

    monkeypatch.setattr(client, "_get", fake_get)

    rows = asyncio.run(client.option_chain("AAPL"))

    assert rows[0]["side"] == "call"
    assert rows[0]["optionSymbol"] == "AAPL260828C00300000"
    assert rows[0]["mid"] == 5.2
    assert rows[1]["side"] == "put"


def test_option_chain_404_with_list_body_raises_fmp_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FMP_OPTIONS_CHAIN_PATH", "options-chain")
    monkeypatch.setenv("FMP_POSITIONS_SUMMARY_ENABLED", "false")
    client = FmpClient(api_key="test-token", cache=FmpCache(tmp_path))

    def handler(request):
        return httpx.Response(404, json=[])

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(FmpError, match="HTTP 404"):
        asyncio.run(client.option_chain("DOCS"))


def test_option_chain_uses_fmp_positions_summary(monkeypatch):
    monkeypatch.delenv("FMP_OPTIONS_CHAIN_PATH", raising=False)
    monkeypatch.setenv("FMP_POSITIONS_SUMMARY_ENABLED", "true")
    monkeypatch.setenv("FMP_POSITIONS_SUMMARY_YEAR", "2026")
    monkeypatch.setenv("FMP_POSITIONS_SUMMARY_QUARTER", "2")
    client = FmpClient(api_key="test-token")

    async def fake_get(url, params, *, cache_ttl_seconds=300):
        assert url.endswith("/institutional-ownership/symbol-positions-summary")
        assert params == {"symbol": "AAPL", "year": 2026, "quarter": 2}
        return [
            {
                "symbol": "AAPL",
                "year": 2026,
                "quarter": 2,
                "totalCalls": 10000,
                "totalPuts": 2500,
                "lastTotalCalls": 8000,
                "lastTotalPuts": 3000,
                "putCallRatio": 0.25,
            }
        ]

    monkeypatch.setattr(client, "_get", fake_get)

    activity = analyze_options_activity(asyncio.run(client.option_chain("AAPL")))

    assert activity["put_call_ratio"] == 0.25
    assert activity["put_call_source"] == "volume"
    assert activity["call_volume"] == 10000


def test_put_call_ratio_is_volume_only():
    activity = analyze_options_activity(
        [
            {"side": "call", "volume": 0, "openInterest": 10000},
            {"side": "put", "volume": 0, "openInterest": 5000},
        ]
    )

    assert activity["put_call_ratio"] is None
    assert activity["put_call_source"] == "unavailable"
    assert activity["call_open_interest"] == 10000


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
    assert activity["preferred_contract"].expiration == "2026-08-28"
    assert activity["options_confirmation_score"] >= 80
    assert activity["options_signal"] == "Confirmed bullish momentum"


def test_put_volume_does_not_affect_options_scoring():
    call = {
        "optionSymbol": "AAPL260828C00300000",
        "expiration": "2026-08-28",
        "strike": 300,
        "side": "call",
        "volume": 5000,
        "openInterest": 10000,
        "delta": 0.65,
    }
    low_put_volume = analyze_options_activity([
        call,
        {"side": "put", "volume": 100, "openInterest": 10000},
    ])
    high_put_volume = analyze_options_activity([
        call,
        {"side": "put", "volume": 15000, "openInterest": 10000},
    ])

    assert low_put_volume["put_call_ratio"] != high_put_volume["put_call_ratio"]
    assert low_put_volume["call_activity"] == high_put_volume["call_activity"]
    assert low_put_volume["options_confirmation_score"] == high_put_volume["options_confirmation_score"]
    assert low_put_volume["options_signal"] == high_put_volume["options_signal"]


def test_preferred_contract_formats_epoch_expiration():
    activity = analyze_options_activity(
        [
            {
                "optionSymbol": "PLTR260828C00170000",
                "expiration": 1787947200,
                "strike": 170,
                "side": "call",
                "volume": 3000,
                "openInterest": 10000,
                "delta": 0.63,
            }
        ]
    )

    assert activity["preferred_contract"].expiration == "2026-08-28"


def test_preferred_contract_prefers_friday_expiration():
    activity = analyze_options_activity(
        [
            {
                "optionSymbol": "MSFT260817C00490000",
                "expiration": "2026-08-17",
                "strike": 490,
                "side": "call",
                "volume": 20000,
                "openInterest": 10000,
                "delta": 0.75,
            },
            {
                "optionSymbol": "MSFT260828C00490000",
                "expiration": "2026-08-28",
                "strike": 490,
                "side": "call",
                "volume": 500,
                "openInterest": 10000,
                "delta": 0.7,
            },
        ]
    )

    assert activity["preferred_contract"].option_symbol == "MSFT260828C00490000"
    assert activity["preferred_contract"].expiration == "2026-08-28"


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
