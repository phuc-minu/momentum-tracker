import argparse
import asyncio
import os
from typing import Any

from marketdata import MarketDataClient, MarketDataError, demo_candles
from phase2_db import (
    connect,
    create_refresh_run,
    finish_refresh_run,
    init_db,
    upsert_daily_bars,
    upsert_momentum_scores,
    upsert_symbols,
)
from phase2_pipeline import candles_to_bars, momentum_signal_to_db, score_market_candidates
from symbol_source import load_phase2_symbols
from universe import configured_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh precomputed Phase 2 momentum candidates.")
    parser.add_argument("--demo", action="store_true", help="Use generated candles instead of MarketData.")
    parser.add_argument("--source", default=os.getenv("PHASE2_SYMBOL_SOURCE", "configured"), choices=["configured", "nasdaq"])
    parser.add_argument("--symbol-limit", type=int, default=int(os.getenv("PHASE2_SYMBOL_LIMIT", "500")))
    parser.add_argument("--top", type=int, default=int(os.getenv("PHASE2_TOP_N", "50")))
    parser.add_argument("--countback", type=int, default=int(os.getenv("PHASE2_COUNTBACK", "260")))
    parser.add_argument("--benchmark", default=os.getenv("PHASE2_BENCHMARK", "QQQ"))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("PHASE2_MARKETDATA_CONCURRENCY", "2")))
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    symbols = _load_symbols(args)
    if not symbols:
        raise SystemExit("No symbols configured. Set PHASE2_SYMBOLS or use --source nasdaq.")
    benchmark = args.benchmark.strip().upper()
    if benchmark not in {item["symbol"] for item in symbols}:
        symbols.append(
            {
                "symbol": benchmark,
                "name": benchmark,
                "exchange": "benchmark",
                "sector": "Benchmark",
            }
        )

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

            benchmark_candles = candle_map.get(benchmark) or demo_candles(benchmark, args.countback)
            scores = score_market_candidates(candle_map, benchmark_candles, top_n=args.top)
            upsert_momentum_scores(
                connection,
                run_id,
                [momentum_signal_to_db(score) for score in scores],
            )
            finish_refresh_run(connection, run_id, status="succeeded", symbols_scored=len(scores))
        except Exception as error:
            connection.rollback()
            finish_refresh_run(connection, run_id, status="failed", symbols_scored=0, error=str(error))
            raise


def _load_symbols(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.demo and args.source == "configured" and not os.getenv("PHASE2_SYMBOLS"):
        return [
            {"symbol": symbol, "name": symbol, "exchange": "demo", "sector": "Unclassified"}
            for symbol in configured_universe()
            if symbol != args.benchmark.strip().upper()
        ][: args.symbol_limit]
    return load_phase2_symbols(source=args.source, limit=args.symbol_limit)


async def _fetch_candles(
    *,
    symbols: list[str],
    benchmark: str,
    countback: int,
    demo: bool,
    concurrency: int,
) -> dict[str, list]:
    unique_symbols = list(dict.fromkeys([benchmark, *symbols]))
    if demo:
        return {symbol: demo_candles(symbol, countback) for symbol in unique_symbols}

    client = MarketDataClient(timeout_seconds=30.0)
    if not client.api_key:
        raise MarketDataError("MARKETDATA_API_KEY is required for Phase 2 live refresh")

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def fetch(symbol: str):
        async with semaphore:
            try:
                return symbol, await client.candles(symbol, countback=countback)
            except MarketDataError:
                return symbol, []

    rows = await asyncio.gather(*(fetch(symbol) for symbol in unique_symbols))
    return {symbol: candles for symbol, candles in rows if candles}


if __name__ == "__main__":
    asyncio.run(main())
