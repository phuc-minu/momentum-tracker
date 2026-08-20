import json
from datetime import UTC, datetime
from typing import Any

from momentum import score_symbol
from schemas import Candle, MomentumSignal

MIN_AVERAGE_DAILY_VOLUME = 3_000_000


def candle_to_bar(symbol: str, candle: Candle) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "bar_date": datetime.fromtimestamp(candle.timestamp, UTC).date().isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def candles_to_bars(symbol: str, candles: list[Candle]) -> list[dict[str, Any]]:
    return [candle_to_bar(symbol, candle) for candle in candles]


def score_market_candidates(
    candle_map: dict[str, list[Candle]],
    benchmark_candles: list[Candle],
    *,
    top_n: int = 50,
    benchmark_symbol: str = "",
) -> list[MomentumSignal]:
    benchmark_symbol = benchmark_symbol.strip().upper()
    scores: list[MomentumSignal] = []
    for symbol, candles in candle_map.items():
        if benchmark_symbol and symbol == benchmark_symbol:
            continue
        if not _meets_min_average_volume(candles):
            continue
        try:
            scores.append(score_symbol(symbol, candles, benchmark_candles))
        except ValueError:
            continue
    scores.sort(key=lambda item: item.score, reverse=True)
    return scores[:top_n]


def _meets_min_average_volume(candles: list[Candle], days: int = 20) -> bool:
    if len(candles) < days:
        return False
    average = sum(candle.volume for candle in candles[-days:]) / days
    return average > MIN_AVERAGE_DAILY_VOLUME


def momentum_signal_to_db(signal: MomentumSignal) -> dict[str, Any]:
    return {
        "symbol": signal.symbol,
        "score": signal.score,
        "grade": signal.grade,
        "setup": signal.setup,
        "last_close": signal.last_close,
        "factors": json.dumps(signal.factors.model_dump(mode="json"), separators=(",", ":")),
        "notes": json.dumps(signal.notes, separators=(",", ":")),
    }


def phase2_symbol(symbol: str, *, name: str | None = None, exchange: str | None = None, sector: str | None = None) -> dict[str, Any]:
    return {
        "symbol": symbol.strip().upper(),
        "name": name,
        "exchange": exchange,
        "sector": sector or "Unclassified",
    }
