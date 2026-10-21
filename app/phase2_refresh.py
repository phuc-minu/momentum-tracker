import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime, time, timedelta
from typing import Any

from fmp import FmpClient, FmpError, demo_candles
from momentum import adjusted_momentum_score, grade_for_score
from options_activity import analyze_options_activity, demo_options_activity
from phase2_db import (
    connect,
    create_refresh_run,
    fetch_recent_daily_bars,
    finish_refresh_run,
    insert_refresh_log,
    init_db,
    prune_scores_for_run,
    upsert_daily_bars,
    upsert_momentum_scores,
    upsert_option_scores,
    upsert_symbols,
)
from phase2_pipeline import MIN_AVERAGE_DAILY_VOLUME, MIN_STOCK_PRICE, candles_to_bars, momentum_signal_to_db, score_market_candidates
from phase2_pipeline import phase2_symbol
from schemas import Candle
from symbol_source import load_phase2_symbols
from universe import configured_universe

LOGGER = logging.getLogger(__name__)
MIN_EXPECTED_SCREENER_SYMBOLS = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh precomputed Phase 2 momentum candidates.")
    parser.add_argument("--demo", action="store_true", help="Use generated candles instead of FMP.")
    parser.add_argument("--source", default=os.getenv("PHASE2_SYMBOL_SOURCE", "fmp"), choices=["configured", "listed", "nasdaq", "fmp"])
    parser.add_argument("--symbol-limit", type=int, default=int(os.getenv("PHASE2_SYMBOL_LIMIT", "500")))
    parser.add_argument("--top", type=int, default=int(os.getenv("PHASE2_TOP_N", "50")))
    parser.add_argument("--mover-limit", type=int, default=int(os.getenv("PHASE2_MOVER_LIMIT", "50")))
    parser.add_argument("--price-movement-days", type=int, default=int(os.getenv("PHASE2_PRICE_MOVEMENT_DAYS", "30")))
    parser.add_argument("--countback", type=int, default=int(os.getenv("PHASE2_COUNTBACK", "60")))
    parser.add_argument("--benchmark", default=os.getenv("PHASE2_BENCHMARK", ""))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("PHASE2_FMP_CONCURRENCY", "2")))
    parser.add_argument("--options-concurrency", type=int, default=int(os.getenv("PHASE2_OPTIONS_CONCURRENCY", "2")))
    parser.add_argument("--min-average-volume", type=int, default=int(os.getenv("PHASE2_MIN_AVERAGE_VOLUME", str(MIN_AVERAGE_DAILY_VOLUME))))
    parser.add_argument("--min-price", type=float, default=float(os.getenv("PHASE2_MIN_PRICE", str(MIN_STOCK_PRICE))))
    parser.add_argument("--volume-prefilter-days", type=int, default=int(os.getenv("PHASE2_VOLUME_PREFILTER_DAYS", "20")))
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if not args.demo and args.source != "fmp":
        raise SystemExit("Live Phase 2 refresh must use PHASE2_SYMBOL_SOURCE=fmp")
    symbols = _load_symbols(args)
    pending_logs = _screener_warning_logs(args, symbols)
    if not symbols:
        raise SystemExit("No symbols configured. Set PHASE2_SYMBOLS or use --source listed.")
    if not args.demo and args.min_average_volume > 0:
        symbols = await _filter_symbols_by_price_movement(
            symbols,
            min_average_volume=args.min_average_volume,
            min_price=args.min_price,
            volume_days=args.volume_prefilter_days,
            movement_days=args.price_movement_days,
            limit=args.mover_limit,
            concurrency=args.concurrency,
        )
        if not symbols:
            raise FmpError("No symbols met the 30-day price movement requirements")
    benchmark = args.benchmark.strip().upper()
    if benchmark and benchmark not in {item["symbol"] for item in symbols}:
        symbols.append(phase2_symbol(benchmark, name=benchmark, exchange="benchmark", sector="Benchmark"))

    with connect() as connection:
        init_db(connection)
        upsert_symbols(connection, symbols)
        run_id = create_refresh_run(connection, symbols_requested=len(symbols))
        for log in pending_logs:
            insert_refresh_log(connection, run_id, **log)

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
            unresolved_symbols = _apply_stored_candle_fallback(
                connection,
                candle_map,
                [item["symbol"] for item in symbols],
                countback=args.countback,
            )
            if unresolved_symbols:
                log = _unresolved_candles_log(
                    unresolved_symbols,
                    symbols_requested=len(symbols),
                    max_missing_ratio=float(os.getenv("PHASE2_MAX_MISSING_CANDLE_RATIO", "0.5")),
                )
                insert_refresh_log(connection, run_id, **log)

            benchmark_candles = candle_map.get(benchmark) if benchmark else []
            if benchmark and not benchmark_candles:
                raise FmpError(f"No complete candle history available for benchmark {benchmark}")
            mover_symbols = _select_top_price_mover_symbols(
                candle_map,
                [item["symbol"] for item in symbols],
                limit=args.mover_limit,
                movement_days=args.price_movement_days,
                min_price=args.min_price,
                min_average_volume=args.min_average_volume,
            )
            if not mover_symbols:
                raise FmpError("No symbols met the 30-day price movement requirements")
            scores = score_market_candidates(
                {symbol: candle_map[symbol] for symbol in mover_symbols if symbol in candle_map},
                benchmark_candles or [],
                top_n=args.top,
                benchmark_symbol=benchmark,
                min_price=args.min_price,
            )
            option_scores = await _score_options(
                symbols=[score.symbol for score in scores],
                run_id=run_id,
                demo=args.demo,
                concurrency=args.options_concurrency,
            )
            earnings_metadata = await _score_earnings(
                symbols=[score.symbol for score in scores],
                candle_map=candle_map,
                demo=args.demo,
                concurrency=args.options_concurrency,
            )
            scores = _apply_options_score_adjustments(scores, option_scores)
            upsert_momentum_scores(
                connection,
                run_id,
                [
                    _momentum_signal_to_db_with_metadata(
                        score,
                        earnings_metadata.get(score.symbol, _default_earnings_metadata()),
                    )
                    for score in scores
                ],
            )
            upsert_option_scores(connection, run_id, option_scores)
            prune_scores_for_run(connection, run_id)
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
    return load_phase2_symbols(
        source=args.source,
        limit=args.symbol_limit,
        min_average_volume=args.min_average_volume,
        min_price=args.min_price,
    )


def _screener_warning_logs(args: argparse.Namespace, symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if args.demo or args.source != "fmp" or len(symbols) >= MIN_EXPECTED_SCREENER_SYMBOLS:
        return []
    message = (
        "FMP company-screener returned fewer than "
        f"{MIN_EXPECTED_SCREENER_SYMBOLS} usable symbols: {len(symbols)}."
    )
    LOGGER.warning(message)
    return [
        {
            "level": "warning",
            "source": "fmp.company-screener",
            "message": message,
            "details": json.dumps(
                {
                    "symbols_returned": len(symbols),
                    "minimum_expected": MIN_EXPECTED_SCREENER_SYMBOLS,
                    "symbol_limit": args.symbol_limit,
                    "min_average_volume": args.min_average_volume,
                    "min_price": args.min_price,
                    "country": os.getenv("FMP_SCREENER_COUNTRY", "US"),
                },
                separators=(",", ":"),
            ),
        }
    ]


async def _filter_symbols_by_average_volume(
    symbols: list[dict[str, Any]],
    *,
    min_average_volume: int,
    min_price: float = MIN_STOCK_PRICE,
    days: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    client = FmpClient(timeout_seconds=30.0)
    if not client.api_key:
        raise FmpError("FMP_API_KEY is required for Phase 2 live refresh")

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def passes_volume_filter(item: dict[str, Any]) -> dict[str, Any] | None:
        symbol = item["symbol"]
        async with semaphore:
            try:
                candles = await client.candles(symbol, countback=days)
            except FmpError:
                return None
        if len(candles) < days:
            return None
        if candles[-1].close <= min_price:
            return None
        average_volume = sum(candle.volume for candle in candles[-days:]) / days
        return item if average_volume > min_average_volume else None

    rows = await asyncio.gather(*(passes_volume_filter(item) for item in symbols))
    return [item for item in rows if item]


async def _filter_symbols_by_price_movement(
    symbols: list[dict[str, Any]],
    *,
    min_average_volume: int,
    min_price: float = MIN_STOCK_PRICE,
    volume_days: int,
    movement_days: int,
    limit: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    client = FmpClient(timeout_seconds=30.0)
    if not client.api_key:
        raise FmpError("FMP_API_KEY is required for Phase 2 live refresh")

    countback = max(volume_days, movement_days + 10, 40)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def fetch(item: dict[str, Any]) -> tuple[dict[str, Any], list[Candle]]:
        symbol = item["symbol"]
        async with semaphore:
            candles = await _fetch_symbol_candles_with_retry(client, symbol, countback=countback)
        return item, candles

    rows = await asyncio.gather(*(fetch(item) for item in symbols))
    candle_map = {item["symbol"]: candles for item, candles in rows if candles}
    _apply_prefilter_stored_candle_fallback(candle_map, [item["symbol"] for item in symbols], countback=countback)

    def price_movement(item: dict[str, Any]) -> tuple[dict[str, Any], float] | None:
        symbol = item["symbol"]
        candles = candle_map.get(symbol, [])
        if not candles:
            return None
        if candles[-1].close <= min_price:
            return None
        if not _meets_average_volume(candles, min_average_volume, days=volume_days):
            return None
        return_value = _calendar_day_return(candles, movement_days)
        if return_value is None:
            return None
        return item, return_value

    movers = [row for row in (price_movement(item) for item in symbols) if row is not None]
    movers.sort(key=lambda row: (-row[1], row[0]["symbol"]))
    return [item for item, _return_value in movers[:limit]]


def _apply_prefilter_stored_candle_fallback(
    candle_map: dict[str, list[Candle]],
    symbols: list[str],
    *,
    countback: int,
) -> None:
    reference_date = _latest_candle_date(candle_map)
    stale_or_missing = []
    for symbol in symbols:
        candles = candle_map.get(symbol)
        if not candles:
            stale_or_missing.append(symbol)
            continue
        if reference_date is not None and _candle_date(candles[-1]) < reference_date:
            stale_or_missing.append(symbol)

    if not stale_or_missing:
        return

    try:
        with connect() as connection:
            stored_rows = fetch_recent_daily_bars(connection, stale_or_missing, countback=countback)
    except Exception as error:
        LOGGER.warning("Stored prefilter candle fallback unavailable: %s", error)
        return

    stored_map = _stored_rows_to_candle_map(stored_rows)
    replaced = 0
    for symbol in stale_or_missing:
        stored_candles = stored_map.get(symbol, [])
        if len(stored_candles) < countback:
            continue
        if reference_date is not None and _candle_date(stored_candles[-1]) < reference_date:
            continue
        candle_map[symbol] = stored_candles[-countback:]
        replaced += 1

    if replaced:
        LOGGER.info("Used stored prefilter candle fallback for %s symbols", replaced)


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

    client = FmpClient(timeout_seconds=30.0)
    if not client.api_key:
        raise FmpError("FMP_API_KEY is required for Phase 2 live refresh")

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def fetch(symbol: str):
        async with semaphore:
            return symbol, await _fetch_symbol_candles_with_retry(client, symbol, countback=countback)

    rows = await asyncio.gather(*(fetch(symbol) for symbol in unique_symbols))
    return {symbol: candles for symbol, candles in rows if candles}


async def _fetch_symbol_candles_with_retry(
    client: FmpClient,
    symbol: str,
    *,
    countback: int,
    attempts: int | None = None,
    backoff_seconds: float | None = None,
) -> list:
    total_attempts = max(1, attempts if attempts is not None else int(os.getenv("PHASE2_FMP_RETRY_ATTEMPTS", "3")))
    backoff = backoff_seconds if backoff_seconds is not None else float(os.getenv("PHASE2_FMP_RETRY_BACKOFF_SECONDS", "5"))
    for attempt in range(total_attempts):
        try:
            return await client.candles(symbol, countback=countback)
        except FmpError as error:
            if attempt == total_attempts - 1:
                LOGGER.warning("FMP candles failed for %s after %s attempts: %s", symbol, total_attempts, error)
                return []
            await asyncio.sleep(backoff * (attempt + 1))
    return []


def _apply_stored_candle_fallback(
    connection,
    candle_map: dict[str, list],
    symbols: list[str],
    *,
    countback: int,
) -> list[str]:
    missing_symbols = [symbol for symbol in symbols if symbol and symbol not in candle_map]
    if not missing_symbols:
        return []

    stored_rows = fetch_recent_daily_bars(connection, missing_symbols, countback=countback)
    stored_map = _stored_rows_to_candle_map(stored_rows)
    reference_date = _latest_candle_date(candle_map)
    unresolved_symbols = []
    for symbol in missing_symbols:
        candles = stored_map.get(symbol, [])
        if len(candles) < countback:
            unresolved_symbols.append(symbol)
            continue
        if reference_date is not None and _candle_date(candles[-1]) < reference_date:
            unresolved_symbols.append(symbol)
            continue
        candle_map[symbol] = candles[-countback:]
    return unresolved_symbols


def _unresolved_candles_log(
    unresolved_symbols: list[str],
    *,
    symbols_requested: int,
    max_missing_ratio: float,
) -> dict[str, Any]:
    summary = ", ".join(unresolved_symbols[:20]) + (
        f" and {len(unresolved_symbols) - 20} more" if len(unresolved_symbols) > 20 else ""
    )
    if len(unresolved_symbols) > symbols_requested * max_missing_ratio:
        raise FmpError(f"No complete candle history available for {summary}")
    message = f"Skipped {len(unresolved_symbols)} of {symbols_requested} symbols without complete candle history: {summary}"
    LOGGER.warning(message)
    return {
        "level": "warning",
        "source": "fmp.candles",
        "message": message,
        "details": json.dumps(
            {
                "unresolved_symbols": unresolved_symbols,
                "symbols_requested": symbols_requested,
                "max_missing_ratio": max_missing_ratio,
            },
            separators=(",", ":"),
        ),
    }


def _select_top_price_mover_symbols(
    candle_map: dict[str, list],
    symbols: list[str],
    *,
    limit: int,
    movement_days: int,
    min_price: float,
    min_average_volume: int,
) -> list[str]:
    movers = []
    for symbol in symbols:
        candles = candle_map.get(symbol, [])
        if not candles:
            continue
        if candles[-1].close <= min_price:
            continue
        if not _meets_average_volume(candles, min_average_volume):
            continue
        return_value = _calendar_day_return(candles, movement_days)
        if return_value is None:
            continue
        movers.append((symbol, return_value))
    movers.sort(key=lambda item: (-item[1], item[0]))
    return [symbol for symbol, _return_value in movers[:limit]]


def _calendar_day_return(candles: list[Candle], days: int) -> float | None:
    if not candles or candles[-1].close <= 0:
        return None
    target_date = _candle_date(candles[-1]) - timedelta(days=max(1, days))
    start = None
    for candle in candles:
        if _candle_date(candle) <= target_date:
            start = candle
        else:
            break
    if start is None or start.close <= 0:
        return None
    return candles[-1].close / start.close - 1


def _meets_average_volume(candles: list[Candle], min_average_volume: int, days: int = 20) -> bool:
    if min_average_volume <= 0:
        return True
    if len(candles) < days:
        return False
    average_volume = sum(candle.volume for candle in candles[-days:]) / days
    return average_volume > min_average_volume


def _stored_rows_to_candle_map(rows: list[dict[str, Any]]) -> dict[str, list[Candle]]:
    candle_map: dict[str, list[Candle]] = {}
    for row in rows:
        candle = _stored_row_to_candle(row)
        if candle is None:
            continue
        candle_map.setdefault(str(row["symbol"]).upper(), []).append(candle)
    return candle_map


def _stored_row_to_candle(row: dict[str, Any]) -> Candle | None:
    bar_date = row.get("bar_date")
    if not bar_date:
        return None
    if isinstance(bar_date, str):
        date_value = datetime.fromisoformat(bar_date).date()
    elif isinstance(bar_date, datetime):
        date_value = bar_date.date()
    else:
        date_value = bar_date
    timestamp = int(datetime.combine(date_value, time.min, tzinfo=UTC).timestamp())
    try:
        return Candle(
            timestamp=timestamp,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row.get("volume") or 0),
        )
    except (TypeError, ValueError):
        return None


def _latest_candle_date(candle_map: dict[str, list]):
    dates = [_candle_date(candles[-1]) for candles in candle_map.values() if candles]
    return max(dates) if dates else None


def _candle_date(candle: Candle):
    return datetime.fromtimestamp(candle.timestamp, UTC).date()


async def _score_options(
    *,
    symbols: list[str],
    run_id: int,
    demo: bool,
    concurrency: int,
) -> list[dict[str, Any]]:
    if demo:
        return [_option_activity_to_db(symbol, demo_options_activity(symbol)) for symbol in symbols]

    client = FmpClient(timeout_seconds=30.0)
    if not client.options_configured:
        return [_option_activity_to_db(symbol, analyze_options_activity([])) for symbol in symbols]

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def score(symbol: str) -> dict[str, Any]:
        async with semaphore:
            try:
                rows = await client.option_chain(symbol)
            except FmpError:
                rows = []
        return _option_activity_to_db(symbol, analyze_options_activity(rows))

    return await asyncio.gather(*(score(symbol) for symbol in symbols))


def _apply_options_score_adjustments(scores, option_scores: list[dict[str, Any]]):
    option_score_by_symbol = {row["symbol"]: row for row in option_scores}
    adjusted = []
    for score in scores:
        options = option_score_by_symbol.get(score.symbol, {})
        adjusted_score = adjusted_momentum_score(
            score.score,
            call_activity=options.get("call_activity"),
        )
        adjusted.append(score.model_copy(update={"score": adjusted_score, "grade": grade_for_score(adjusted_score)}))
    adjusted.sort(key=lambda item: (item.score, item.factors.return_1m if item.factors.return_1m is not None else -999), reverse=True)
    return adjusted


async def _score_earnings(
    *,
    symbols: list[str],
    candle_map: dict[str, list[Candle]],
    demo: bool,
    concurrency: int,
) -> dict[str, dict[str, Any]]:
    if demo:
        return {symbol: _default_earnings_metadata(checked=True) for symbol in symbols}

    client = FmpClient(timeout_seconds=30.0)
    if not client.api_key:
        return {symbol: _default_earnings_metadata() for symbol in symbols}

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))

    async def score(symbol: str) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            try:
                earnings = await client.earnings(symbol, countback=4)
            except FmpError:
                return symbol, _default_earnings_metadata()
        return symbol, _earnings_metadata_for_symbol(earnings, candle_map.get(symbol, []))

    rows = await asyncio.gather(*(score(symbol) for symbol in symbols))
    return dict(rows)


def _default_earnings_metadata(*, checked: bool = False) -> dict[str, Any]:
    return {
        "earnings_checked": checked,
        "earnings_near_move": False,
        "likely_earnings_related": False,
        "earnings_date": None,
    }


def _earnings_metadata_for_symbol(earnings: list[dict], candles: list[Candle]) -> dict[str, Any]:
    metadata = _default_earnings_metadata(checked=True)
    if len(candles) < 2:
        return metadata

    window_end = _candle_date(candles[-1])
    window_start = window_end - timedelta(days=30)
    for row in earnings:
        report_date = _earnings_report_date(row)
        if report_date is None:
            continue
        if window_start <= report_date <= window_end:
            metadata["earnings_near_move"] = True
            metadata["likely_earnings_related"] = True
            metadata["earnings_date"] = report_date.isoformat()
            break
    return metadata


def _earnings_report_date(row: dict):
    raw_date = row.get("date") or row.get("reportDate") or row.get("updated")
    if raw_date is None:
        return None
    try:
        if isinstance(raw_date, (int, float)):
            return datetime.fromtimestamp(raw_date, UTC).date()
        return datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _momentum_signal_to_db_with_metadata(signal, earnings_metadata: dict[str, Any]) -> dict[str, Any]:
    row = momentum_signal_to_db(signal)
    notes = list(signal.notes)
    if earnings_metadata.get("likely_earnings_related"):
        date_text = earnings_metadata.get("earnings_date")
        suffix = f" on {date_text}" if date_text else ""
        notes.append(f"Likely earnings-related move{suffix}.")
    row["notes"] = json.dumps(notes, separators=(",", ":"))

    factors = signal.factors.model_dump(mode="json")
    factors.update(earnings_metadata)
    row["factors"] = json.dumps(factors, separators=(",", ":"))
    return row


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
