import asyncio
import hashlib
import os
from datetime import UTC, datetime
from datetime import timedelta

from marketdata import MarketDataClient, MarketDataError, demo_candles
from options_activity import analyze_options_activity, demo_options_activity
from schemas import Candle, DiscoveryCandidate, NewsItem
from symbol_source import fetch_fmp_screener_symbols
from universe import discovery_universe

EARNINGS_TERMS = ("earnings", "eps", "quarter", "q1", "q2", "q3", "q4", "guidance", "revenue", "profit")
CATALYST_TERMS = {
    "ai": "AI or automation catalyst",
    "contract": "contract or partnership catalyst",
    "deal": "deal or partnership catalyst",
    "fda": "regulatory catalyst",
    "approval": "regulatory catalyst",
    "upgrade": "analyst catalyst",
    "acquisition": "M&A catalyst",
    "merger": "M&A catalyst",
    "bitcoin": "crypto-linked catalyst",
    "crypto": "crypto-linked catalyst",
    "oil": "commodity catalyst",
}
MIN_AVERAGE_DAILY_VOLUME = 3_000_000


async def discover_movers(
    *,
    growth_min: float = 0.20,
    period_days: int = 10,
    limit: int = 10,
    exclude_earnings: bool = True,
    demo: bool = False,
) -> list[DiscoveryCandidate]:
    if demo:
        universe = discovery_universe()
        candidates = [
            await _build_candidate(
                symbol,
                sector,
                demo_discovery_candles(symbol, max(period_days + 2, 40), period_days),
                None,
                options_activity=demo_options_activity(symbol),
                period_days=period_days,
                demo=True,
            )
            for symbol, sector in universe.items()
        ]
    else:
        client = MarketDataClient(timeout_seconds=30.0)
        if not client.api_key:
            raise MarketDataError("FMP_API_KEY is required for live market data")
        universe = _live_discovery_universe()
        candidates = await _discover_live_candidates(
            client,
            universe,
            period_days=period_days,
            growth_min=growth_min,
            limit=limit,
            exclude_earnings=exclude_earnings,
        )

    if demo:
        candidates = [candidate for candidate in candidates if candidate]
        candidates = _apply_relative_strength_percentiles(candidates)

        filtered = [
            candidate
            for candidate in candidates
            if candidate.return_2w >= growth_min
            and (not exclude_earnings or not candidate.likely_earnings_related)
        ]
        _sort_discovery_candidates(filtered)
        return filtered[:limit]

    return candidates


async def _discover_live_candidates(
    client: MarketDataClient,
    universe: dict[str, str],
    *,
    period_days: int,
    growth_min: float,
    limit: int,
    exclude_earnings: bool,
) -> list[DiscoveryCandidate]:
    price_candidates = await _build_bulk_price_candidates(client, universe, period_days)

    price_candidates = [candidate for candidate in price_candidates if candidate]
    price_candidates = _apply_relative_strength_percentiles(price_candidates)

    preliminary = [
        candidate
        for candidate in price_candidates
        if candidate.return_2w >= growth_min
    ]
    _sort_discovery_candidates(preliminary)

    enriched: list[DiscoveryCandidate] = []
    batch_size = max(limit * 2, 12)
    for index in range(0, len(preliminary), batch_size):
        batch = preliminary[index : index + batch_size]
        enriched_batch = await _gather_limited(
            (
                _enrich_live_candidate(client, candidate, period_days)
                for candidate in batch
            ),
            limit=_live_request_concurrency(),
        )
        enriched.extend(candidate for candidate in enriched_batch if candidate)
        filtered = [
            candidate
            for candidate in enriched
            if not exclude_earnings or not candidate.likely_earnings_related
        ]
        if len(filtered) >= limit:
            _sort_discovery_candidates(filtered)
            return filtered[:limit]

    filtered = [
        candidate
        for candidate in enriched
        if not exclude_earnings or not candidate.likely_earnings_related
    ]
    _sort_discovery_candidates(filtered)
    return filtered[:limit]


def _sort_discovery_candidates(candidates: list[DiscoveryCandidate]) -> None:
    candidates.sort(
        key=lambda item: (
            item.momentum_score,
            item.options_confirmation_score if item.options_confirmation_score is not None else -1,
        ),
        reverse=True,
    )


async def _build_bulk_price_candidates(
    client: MarketDataClient,
    universe: dict[str, str],
    period_days: int,
) -> list[DiscoveryCandidate]:
    symbols = list(universe)
    latest = await client.bulk_candles(symbols)
    if not latest:
        return []

    latest_timestamp = max(candle.timestamp for candle in latest.values())
    start = {}
    start_date = _previous_trading_date(latest_timestamp, period_days)
    for offset in range(5):
        candidate_date = _previous_calendar_date(start_date, offset)
        start = await client.bulk_candles(symbols, date=candidate_date)
        if start:
            break

    if not start:
        return []

    candidates: list[DiscoveryCandidate] = []
    for symbol, sector in universe.items():
        start_candle = start.get(symbol)
        last_candle = latest.get(symbol)
        candidate = _build_candidate_from_candle_pair(symbol, sector, start_candle, last_candle)
        if candidate:
            candidates.append(candidate)
    return candidates


def _build_candidate_from_candle_pair(
    symbol: str,
    sector: str,
    start: Candle | None,
    last: Candle | None,
) -> DiscoveryCandidate | None:
    if start is None or last is None or start.close <= 0:
        return None

    return_2w = last.close / start.close - 1
    price_signal = "new 20-day high" if last.close >= start.close else "momentum continuation"
    setup = _setup_label(return_2w, price_signal, None)
    momentum_score = _momentum_score(return_2w, price_signal, None, None)

    return DiscoveryCandidate(
        symbol=symbol,
        sector=sector,
        momentum_score=momentum_score,
        setup=setup,
        price_signal=price_signal,
        relative_strength_percentile=0,
        volume_ratio_20d=None,
        return_2w=round(return_2w, 4),
        start_close=round(start.close, 2),
        last_close=round(last.close, 2),
        put_call_ratio=None,
        put_call_source="unavailable",
        call_volume=None,
        call_open_interest=None,
        call_activity_ratio=None,
        call_activity="unavailable",
        options_confirmation_score=None,
        options_signal="Options unavailable",
        preferred_contract=None,
        earnings_near_move=False,
        earnings_checked=False,
        likely_earnings_related=False,
        catalyst="price momentum; catalyst not identified",
        headlines=[],
    )


def _previous_trading_date(timestamp: int, trading_days: int) -> str:
    value = datetime.fromtimestamp(timestamp, UTC).date()
    remaining = trading_days
    while remaining:
        value -= timedelta(days=1)
        if value.weekday() < 5:
            remaining -= 1
    return value.isoformat()


def _previous_calendar_date(date_value: str, days: int) -> str:
    value = datetime.fromisoformat(date_value).date()
    return (value - timedelta(days=days)).isoformat()


async def _gather_limited(coroutines, *, limit: int):
    semaphore = asyncio.Semaphore(limit)

    async def run(coroutine):
        async with semaphore:
            return await coroutine

    return await asyncio.gather(*(run(coroutine) for coroutine in coroutines))


def _live_discovery_universe() -> dict[str, str]:
    source = os.getenv("DISCOVERY_SYMBOL_SOURCE", "fmp").strip().lower()
    if source == "static":
        return discovery_universe()
    if source != "fmp":
        raise MarketDataError(f"Unsupported DISCOVERY_SYMBOL_SOURCE: {source}")
    try:
        limit = int(os.getenv("DISCOVERY_SYMBOL_LIMIT", os.getenv("PHASE2_SYMBOL_LIMIT", "500")))
    except ValueError:
        limit = 500
    try:
        min_volume = int(os.getenv("DISCOVERY_MIN_AVERAGE_VOLUME", os.getenv("PHASE2_MIN_AVERAGE_VOLUME", "3000000")))
    except ValueError:
        min_volume = MIN_AVERAGE_DAILY_VOLUME
    try:
        symbols = fetch_fmp_screener_symbols(limit=limit, min_average_volume=min_volume)
    except RuntimeError as error:
        raise MarketDataError(str(error)) from error
    if not symbols:
        raise MarketDataError("FMP stock screener returned no discovery symbols")
    return {
        item["symbol"]: item.get("sector") or "Unclassified"
        for item in symbols
    }


def _live_request_concurrency() -> int:
    raw = os.getenv("FMP_CONCURRENCY") or os.getenv("MARKETDATA_CONCURRENCY", "2")
    try:
        return max(1, min(int(raw), 5))
    except ValueError:
        return 2


async def _build_live_price_candidate(
    client: MarketDataClient,
    symbol: str,
    sector: str,
    period_days: int,
) -> DiscoveryCandidate | None:
    try:
        candles = await client.candles(symbol, countback=max(period_days + 5, 260))
    except MarketDataError:
        return None
    if not candles:
        return None
    return await _build_candidate(
        symbol,
        sector,
        candles,
        [],
        earnings=None,
        options_activity=None,
        period_days=period_days,
    )


async def _enrich_live_candidate(
    client: MarketDataClient,
    candidate: DiscoveryCandidate,
    period_days: int,
) -> DiscoveryCandidate | None:
    try:
        candles = await client.candles(candidate.symbol, countback=max(period_days + 5, 260))
    except MarketDataError:
        return None

    earnings, news = await asyncio.gather(
        _safe_earnings(client, candidate.symbol),
        _safe_news(client, candidate.symbol),
    )
    option_rows = await _safe_option_chain(client, candidate.symbol) if _enrich_options_enabled() else []
    return await _build_candidate(
        candidate.symbol,
        candidate.sector,
        candles,
        news,
        earnings=earnings,
        options_activity=analyze_options_activity(option_rows),
        period_days=period_days,
    )


def _enrich_options_enabled() -> bool:
    return os.getenv("DISCOVERY_OPTIONS", "").strip().lower() in {"1", "true", "yes", "on"}


async def _build_candidate(
    symbol: str,
    sector: str,
    candles: list[Candle],
    news: list[NewsItem] | None,
    *,
    earnings: list[dict] | None = None,
    options_activity: dict | None = None,
    period_days: int = 10,
    demo: bool = False,
) -> DiscoveryCandidate | None:
    if len(candles) <= period_days:
        return None

    start = candles[-period_days - 1]
    last = candles[-1]
    if start.close <= 0:
        return None
    if not _meets_min_average_volume(candles):
        return None

    return_2w = last.close / start.close - 1
    volume_ratio = _volume_ratio(candles)
    price_signal = _price_signal(candles)
    setup = _setup_label(return_2w, price_signal, volume_ratio)
    momentum_score = _momentum_score(return_2w, price_signal, volume_ratio, options_activity)
    headlines = news if news is not None else _demo_news(symbol)
    earnings_near_move = _earnings_near_move(earnings or [], start.timestamp, last.timestamp)
    headline_related = _headlines_look_earnings_related(headlines)
    likely_earnings_related = earnings_near_move or headline_related
    catalyst = _classify_catalyst(headlines, likely_earnings_related, demo)

    return DiscoveryCandidate(
        symbol=symbol,
        sector=sector,
        momentum_score=momentum_score,
        setup=setup,
        price_signal=price_signal,
        relative_strength_percentile=0,
        volume_ratio_20d=volume_ratio,
        return_2w=round(return_2w, 4),
        start_close=round(start.close, 2),
        last_close=round(last.close, 2),
        put_call_ratio=(options_activity or {}).get("put_call_ratio"),
        put_call_source=(options_activity or {}).get("put_call_source", "unavailable"),
        call_volume=(options_activity or {}).get("call_volume"),
        call_open_interest=(options_activity or {}).get("call_open_interest"),
        call_activity_ratio=(options_activity or {}).get("call_activity_ratio"),
        call_activity=(options_activity or {}).get("call_activity", "unavailable"),
        options_confirmation_score=(options_activity or {}).get("options_confirmation_score"),
        options_signal=(options_activity or {}).get("options_signal", "Options unavailable"),
        preferred_contract=(options_activity or {}).get("preferred_contract"),
        earnings_near_move=earnings_near_move,
        earnings_checked=earnings is not None,
        likely_earnings_related=likely_earnings_related,
        catalyst=catalyst,
        headlines=headlines[:3],
    )


async def _safe_earnings(client: MarketDataClient, symbol: str) -> list[dict] | None:
    try:
        return await client.earnings(symbol, countback=4)
    except Exception:
        return None


async def _safe_news(client: MarketDataClient, symbol: str) -> list[NewsItem]:
    try:
        return await client.news(symbol, countback=5)
    except Exception:
        return []


async def _safe_option_chain(client: MarketDataClient, symbol: str) -> list[dict]:
    try:
        return await client.option_chain(symbol)
    except Exception:
        return []


def _apply_relative_strength_percentiles(candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
    if not candidates:
        return candidates
    ordered = sorted(candidates, key=lambda item: item.return_2w)
    denominator = max(len(ordered) - 1, 1)
    percentiles = {
        item.symbol: round((index / denominator) * 100, 1)
        for index, item in enumerate(ordered)
    }
    for candidate in candidates:
        candidate.relative_strength_percentile = percentiles[candidate.symbol]
        if candidate.relative_strength_percentile >= 95 and candidate.momentum_score < 94:
            candidate.momentum_score = 94
    return candidates


def _earnings_near_move(earnings: list[dict], start_timestamp: int, end_timestamp: int) -> bool:
    window_start = datetime.fromtimestamp(start_timestamp, UTC).date()
    window_end = datetime.fromtimestamp(end_timestamp, UTC).date()
    for row in earnings:
        raw_date = row.get("date") or row.get("reportDate") or row.get("updated")
        if raw_date is None:
            continue
        try:
            if isinstance(raw_date, (int, float)):
                report_date = datetime.fromtimestamp(raw_date, UTC).date()
            else:
                report_date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if window_start <= report_date <= window_end:
            return True
    return False


def _headlines_look_earnings_related(headlines: list[NewsItem]) -> bool:
    text = " ".join(item.headline.lower() for item in headlines)
    return any(term in text for term in EARNINGS_TERMS)


def _classify_catalyst(headlines: list[NewsItem], earnings_related: bool, demo: bool) -> str:
    if earnings_related:
        return "likely earnings-related"
    text = " ".join(item.headline.lower() for item in headlines)
    for term, label in CATALYST_TERMS.items():
        if term in text:
            return label
    return "price momentum; catalyst not identified" if not demo else "demo non-earnings catalyst"


def _volume_ratio(candles: list[Candle]) -> float | None:
    if len(candles) < 20:
        return None
    average = sum(c.volume for c in candles[-20:]) / 20
    if average <= 0:
        return None
    return round(candles[-1].volume / average, 2)


def _meets_min_average_volume(candles: list[Candle], days: int = 20) -> bool:
    if len(candles) < days:
        return False
    average = sum(c.volume for c in candles[-days:]) / days
    return average > MIN_AVERAGE_DAILY_VOLUME


def _price_signal(candles: list[Candle]) -> str:
    last_close = candles[-1].close
    highs = [c.high for c in candles]
    if len(highs) >= 50 and last_close >= max(highs[-50:]) * 0.995:
        return "new 50-day high"
    if len(highs) >= 20 and last_close >= max(highs[-20:]) * 0.995:
        return "new 20-day high"
    return "momentum continuation"


def _setup_label(return_2w: float, price_signal: str, volume_ratio: float | None) -> str:
    if "50-day high" in price_signal and (volume_ratio or 0) >= 1.5:
        return "Early breakout"
    if "high" in price_signal:
        return "Breakout watch"
    if return_2w >= 0.20:
        return "Momentum surge"
    return "Momentum watch"


def _momentum_score(
    return_2w: float,
    price_signal: str,
    volume_ratio: float | None,
    options_activity: dict | None,
) -> float:
    score = 55 + min(return_2w * 120, 28)
    if "50-day high" in price_signal:
        score += 8
    elif "20-day high" in price_signal:
        score += 5
    if volume_ratio is not None:
        score += min(max((volume_ratio - 1) * 8, 0), 8)
    if (options_activity or {}).get("call_activity") == "unusually elevated":
        score += 6
    elif (options_activity or {}).get("call_activity") == "elevated":
        score += 3
    return round(min(score, 99), 1)


def _demo_news(symbol: str) -> list[NewsItem]:
    seed = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)
    catalysts = [
        "announces AI platform expansion",
        "wins multi-year enterprise contract",
        "rises after analyst upgrade",
        "jumps on sector rotation",
        "advances on product launch momentum",
    ]
    return [
        NewsItem(
            headline=f"{symbol} {catalysts[seed % len(catalysts)]}",
            source="demo",
            publication_date=None,
        )
    ]


def demo_discovery_candles(symbol: str, countback: int, period_days: int) -> list[Candle]:
    candles = demo_candles(symbol, countback)
    seed = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)
    boost = 1 + 0.035 + (seed % 32) / 1000
    if seed % 3 != 0:
        return candles

    boosted: list[Candle] = []
    for index, candle in enumerate(candles):
        if index <= len(candles) - period_days - 1:
            boosted.append(candle)
            continue
        position = index - (len(candles) - period_days)
        multiplier = boost ** max(position, 0)
        boosted.append(
            Candle(
                timestamp=candle.timestamp,
                open=round(candle.open * multiplier, 2),
                high=round(candle.high * multiplier, 2),
                low=round(candle.low * multiplier, 2),
                close=round(candle.close * multiplier, 2),
                volume=candle.volume,
            )
        )
    return boosted
