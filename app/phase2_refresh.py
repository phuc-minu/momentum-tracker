import argparse
import asyncio
import json
import os
from typing import Any

from marketdata import MarketDataClient, MarketDataError, demo_candles
from options_activity import analyze_options_activity, demo_options_activity
from phase2_db import (
    connect,
    create_refresh_run,
    finish_refresh_run,
    init_db,
    upsert_daily_bars,
    upsert_momentum_scores,
    upsert_option_scores,
    upsert_symbols,
)
from phase2_pipeline import MIN_AVERAGE_DAILY_VOLUME, candles_to_bars, momentum_signal_to_db, score_market_candidates
from phase2_pipeline import phase2_symbol
from symbol_source import load_phase2_symbols
from universe import configured_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh precomputed Phase 2 momentum candidates.")
    parser.add_argument("--demo", action="store_true", help="Use generated candles instead of FMP.")
    parser.add_argument("--source", default=os.getenv("PHASE2_SYMBOL_SOURCE", "configured"), choices=["configured", "listed", "nasdaq", "fmp"])
    parser.add_argument("--symbol-limit", type=int, default=int(os.getenv("PHASE2_SYMBOL_LIMIT", "500")))
    parser.add_argument("--top", type=int, default=int(os.getenv("PHASE2_TOP_N", "50")))
    parser.add_argument("--countback", type=int, default=int(os.getenv("PHASE2_COUNTBACK", "260")))
    parser.add_argument("--benchmark", default=os.getenv("PHASE2_BENCHMARK", ""))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("PHASE2_FMP_CONCURRENCY") or os.getenv("PHASE2_MARKETDATA_CONCURRENCY", "2")))
    parser.add_argument("--options-concurrency", type=int, default=int(os.getenv("PHASE2_OPTIONS_CONCURRENCY", "2")))
    parser.add_argument("--min-average-volume", type=int, default=int(os.getenv("PHASE2_MIN_AVERAGE_VOLUME", str(MIN_AVERAGE_DAILY_VOLUME))))
    parser.add_argument("--volume-prefilter-days", type=int, default=int(os.getenv("PHASE2_VOLUME_PREFILTER_DAYS", "20")))
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    symbols = _load_symbols(args)
    if not symbols:
        raise SystemExit("No symbols configured. Set PHASE2_SYMBOLS or use --source listed.")
    if not args.demo and args.min_average_volume > 0:
        symbols = await _filter_symbols_by_average_volume(
            symbols,
            min_average_volume=args.min_average_volume,
            days=args.volume_prefilter_days,
            concurrency=args.concurrency,
        )
    benchmark = args.benchmark.strip().upper()
    if benchmark and benchmark not in {item["symbol"] for item in symbols}:
        symbols.append(phase2_symbol(benchmark, name=benchmark, exchange="benchmark", sector="Benchmark"))

    with connect() as connection:
        init_db(connection)
        upsert_symbols(connection, symbols)
        run_id = create_refresh_run(connection, symbols_requested=len(symbols))

        try:
            candle_map = await _fetch_candles(
                symbols=[item["symbol"] for item in symbols],
                benchmark=benchmark,
                countback=args.countback,
                demo=args.demo,
                concurrency=args.concurrency,
            )
            bars = [
                bar
                for symbol, candles in candle_map.items()
                for bar in candles_to_bars(symbol, candles)
            ]
            upsert_daily_bars(connection, bars)

            benchmark_candles = candle_map.get(benchmark) if benchmark else []
            if benchmark and not benchmark_candles:
                benchmark_candles = demo_candles(benchmark, args.countback)
            scores = score_market_candidates(candle_map, benchmark_candles or [], top_n=args.top, benchmark_symbol=benchmark)
            upsert_momentum_scores(
                connection,
                run_id,
                [momentum_signal_to_db(score) for score in scores],
            )
            option_scores = await _score_options(
                symbols=[score.symbol for score in scores],
                run_id=run_id,
                demo=args.demo,
                concurrency=args.options_concurrency,
            )
            upsert_option_scores(connection, run_id, option_scores)
            finish_refresh_run(connection, run_id, status="succeeded", symbols_scored=len(scores))
        except Exception as error:
            connection.rollback()
            finish_refresh_run(connection, run_id, status="failed", symbols_scored=0, error=str(error))
            raise


def _load_symbols(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.demo and args.source == "configured" and not os.getenv("PHASE2_SYMBOLS"):
        return [
            phase2_symbol(symbol, name=symbol, exchange="demo")
            for symbol in configured_universe()
            if not args.benchmark.strip().upper() or symbol != args.benchmark.strip().upper()
        ][: args.symbol_limit]
    return load_phase2_symbols(source=args.source, limit=args.symbol_limit)


async def _filter_symbols_by_average_volume(
    symbols: list[dict[str, Any]],
    *,
    min_average_volume: int,
    days: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    client = MarketDataClient(timeout_seconds=30.0)
    if not client.api_key:
        raise MarketDataError("FMP_API_KEY is required for Phase 2 live refresh")

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def passes_volume_filter(item: dict[str, Any]) -> dict[str, Any] | None:
        symbol = item["symbol"]
        async with semaphore:
            try:
                candles = await client.candles(symbol, countback=days)
            except MarketDataError:
                return None
        if len(candles) < days:
            return None
        average_volume = sum(candle.volume for candle in candles[-days:]) / days
        return item if average_volume > min_average_volume else None

    rows = await asyncio.gather(*(passes_volume_filter(item) for item in symbols))
    return [item for item in rows if item]


async def _fetch_candles(
    *,
    symbols: list[str],
    benchmark: str,
    countback: int,
    demo: bool,
    concurrency: int,
) -> dict[str, list]:
    unique_symbols = list(dict.fromkeys([symbol for symbol in [benchmark, *symbols] if symbol]))
    if demo:
        return {symbol: demo_candles(symbol, countback) for symbol in unique_symbols}

    client = MarketDataClient(timeout_seconds=30.0)
    if not client.api_key:
        raise MarketDataError("FMP_API_KEY is required for Phase 2 live refresh")

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def fetch(symbol: str):
        async with semaphore:
            try:
                return symbol, await client.candles(symbol, countback=countback)
            except MarketDataError:
                return symbol, []

    rows = await asyncio.gather(*(fetch(symbol) for symbol in unique_symbols))
    return {symbol: candles for symbol, candles in rows if candles}


async def _score_options(
    *,
    symbols: list[str],
    run_id: int,
    demo: bool,
    concurrency: int,
) -> list[dict[str, Any]]:
    if demo:
        return [_option_activity_to_db(symbol, demo_options_activity(symbol)) for symbol in symbols]

    client = MarketDataClient(timeout_seconds=30.0)
    if not client.api_key:
        raise MarketDataError("FMP_API_KEY is required for Phase 2 live options refresh")

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def score(symbol: str) -> dict[str, Any]:
        async with semaphore:
            try:
                rows = await client.option_chain(symbol)
            except MarketDataError:
                rows = []
        return _option_activity_to_db(symbol, analyze_options_activity(rows))

    return await asyncio.gather(*(score(symbol) for symbol in symbols))


def _option_activity_to_db(symbol: str, activity: dict[str, Any]) -> dict[str, Any]:
    preferred_contract = activity.get("preferred_contract")
    return {
        "symbol": symbol,
        "put_call_ratio": activity.get("put_call_ratio"),
        "put_call_source": activity.get("put_call_source", "unavailable"),
        "call_volume": activity.get("call_volume"),
        "call_open_interest": activity.get("call_open_interest"),
        "call_activity_ratio": activity.get("call_activity_ratio"),
        "call_activity": activity.get("call_activity", "unavailable"),
        "options_confirmation_score": activity.get("options_confirmation_score"),
        "options_signal": activity.get("options_signal", "Options unavailable"),
        "preferred_contract": json.dumps(
            preferred_contract.model_dump(mode="json") if hasattr(preferred_contract, "model_dump") else preferred_contract,
            separators=(",", ":"),
        )
        if preferred_contract is not None
        else None,
    }


if __name__ == "__main__":
    asyncio.run(main())
