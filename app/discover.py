import asyncio
import hashlib
from datetime import UTC, datetime

from marketdata import MarketDataClient, demo_candles
from schemas import Candle, DiscoveryCandidate, NewsItem
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


async def discover_movers(
    *,
    growth_min: float = 0.20,
    period_days: int = 10,
    limit: int = 10,
    exclude_earnings: bool = True,
    demo: bool = False,
) -> list[DiscoveryCandidate]:
    universe = discovery_universe()
    if demo:
        candidates = [
            await _build_candidate(
                symbol,
                sector,
                demo_discovery_candles(symbol, max(period_days + 2, 40), period_days),
                None,
                put_call_ratio=demo_put_call_ratio(symbol),
                put_call_source="demo",
                period_days=period_days,
                demo=True,
            )
            for symbol, sector in universe.items()
        ]
    else:
        client = MarketDataClient(timeout_seconds=30.0)
        candidates = await asyncio.gather(
            *(
                _build_live_candidate(client, symbol, sector, period_days)
                for symbol, sector in universe.items()
            )
        )

    filtered = [
        candidate
        for candidate in candidates
        if candidate
        and candidate.return_2w >= growth_min
        and (not exclude_earnings or not candidate.likely_earnings_related)
    ]
    filtered.sort(key=lambda item: item.return_2w, reverse=True)
    return filtered[:limit]


async def _build_live_candidate(
    client: MarketDataClient,
    symbol: str,
    sector: str,
    period_days: int,
) -> DiscoveryCandidate | None:
    candles = await client.candles(symbol, countback=max(period_days + 5, 30))
    if not candles:
        return None
    earnings, news, options_sentiment = await asyncio.gather(
        _safe_earnings(client, symbol),
        _safe_news(client, symbol),
        _safe_put_call_ratio(client, symbol),
    )
    put_call_ratio, put_call_source = options_sentiment
    return await _build_candidate(
        symbol,
        sector,
        candles,
        news,
        earnings=earnings,
        put_call_ratio=put_call_ratio,
        put_call_source=put_call_source,
        period_days=period_days,
    )


async def _build_candidate(
    symbol: str,
    sector: str,
    candles: list[Candle],
    news: list[NewsItem] | None,
    *,
    earnings: list[dict] | None = None,
    put_call_ratio: float | None = None,
    put_call_source: str = "unavailable",
    period_days: int = 10,
    demo: bool = False,
) -> DiscoveryCandidate | None:
    if len(candles) <= period_days:
        return None

    start = candles[-period_days - 1]
    last = candles[-1]
    if start.close <= 0:
        return None

    return_2w = last.close / start.close - 1
    headlines = news if news is not None else _demo_news(symbol)
    earnings_near_move = _earnings_near_move(earnings or [], start.timestamp, last.timestamp)
    headline_related = _headlines_look_earnings_related(headlines)
    likely_earnings_related = earnings_near_move or headline_related
    catalyst = _classify_catalyst(headlines, likely_earnings_related, demo)

    return DiscoveryCandidate(
        symbol=symbol,
        sector=sector,
        return_2w=round(return_2w, 4),
        start_close=round(start.close, 2),
        last_close=round(last.close, 2),
        put_call_ratio=put_call_ratio,
        put_call_source=put_call_source,
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


async def _safe_put_call_ratio(client: MarketDataClient, symbol: str) -> tuple[float | None, str]:
    try:
        return await client.put_call_ratio(symbol)
    except Exception:
        return None, "unavailable"


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


def demo_put_call_ratio(symbol: str) -> float:
    seed = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)
    return round(0.45 + (seed % 130) / 100, 2)
