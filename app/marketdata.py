import hashlib
import math
import os
from datetime import UTC, datetime, timedelta

import httpx

from schemas import Candle, NewsItem


class MarketDataError(RuntimeError):
    pass


class MarketDataClient:
    def __init__(self, api_key: str | None = None, timeout_seconds: float = 20.0):
        self.api_key = api_key or os.getenv("MARKETDATA_API_KEY")
        self.timeout_seconds = timeout_seconds

    async def candles(self, symbol: str, countback: int = 260, resolution: str = "D") -> list[Candle]:
        if not self.api_key:
            raise MarketDataError("MARKETDATA_API_KEY is required for live market data")

        url = f"https://api.marketdata.app/v1/stocks/candles/{resolution}/{symbol}/"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"countback": countback}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise MarketDataError(_http_error_message(error)) from error
            except httpx.TimeoutException as error:
                raise MarketDataError(f"MarketData request timed out for {symbol}") from error
            except httpx.HTTPError as error:
                raise MarketDataError(f"MarketData request failed for {symbol}: {error}") from error
            payload = response.json()

        status = payload.get("s")
        if status == "no_data":
            return []
        if status != "ok":
            message = payload.get("errmsg", f"Unexpected MarketData status: {status}")
            raise MarketDataError(message)

        return _parse_marketdata_candles(payload)

    async def bulk_candles(
        self,
        symbols: list[str],
        *,
        date: str | None = None,
        resolution: str = "D",
    ) -> dict[str, Candle]:
        if not self.api_key:
            raise MarketDataError("MARKETDATA_API_KEY is required for live market data")
        if not symbols:
            return {}

        params = {"symbols": ",".join(symbols)}
        if date:
            params["date"] = date
        payload = await self._get(
            f"https://api.marketdata.app/v1/stocks/bulkcandles/{resolution}/",
            params,
        )
        if payload.get("s") == "no_data":
            return {}
        if payload.get("s") != "ok":
            raise MarketDataError(payload.get("errmsg", f"Unexpected bulk candles status: {payload.get('s')}"))
        return _parse_marketdata_bulk_candles(payload)

    async def earnings(self, symbol: str, countback: int = 2) -> list[dict]:
        if not self.api_key:
            raise MarketDataError("MARKETDATA_API_KEY is required for live earnings data")

        payload = await self._get(f"https://api.marketdata.app/v1/stocks/earnings/{symbol}/", {"countback": countback})
        if payload.get("s") == "no_data":
            return []
        if payload.get("s") != "ok":
            raise MarketDataError(payload.get("errmsg", f"Unexpected earnings status: {payload.get('s')}"))
        return _unpack_columnar_payload(payload)

    async def news(self, symbol: str, countback: int = 5) -> list[NewsItem]:
        if not self.api_key:
            raise MarketDataError("MARKETDATA_API_KEY is required for live news data")

        payload = await self._get(f"https://api.marketdata.app/v1/stocks/news/{symbol}/", {"countback": countback})
        if payload.get("s") == "no_data":
            return []
        if payload.get("s") != "ok":
            raise MarketDataError(payload.get("errmsg", f"Unexpected news status: {payload.get('s')}"))
        rows = _unpack_columnar_payload(payload)
        return [
            NewsItem(
                headline=str(row.get("headline", "")),
                source=row.get("source"),
                publication_date=row.get("publicationDate"),
            )
            for row in rows
            if row.get("headline")
        ]

    async def put_call_ratio(self, symbol: str) -> tuple[float | None, str]:
        if not self.api_key:
            raise MarketDataError("MARKETDATA_API_KEY is required for live options data")

        payload = await self._get(
            f"https://api.marketdata.app/v1/options/chain/{symbol}/",
            {"monthly": "false"},
        )
        if payload.get("s") == "no_data":
            return None, "unavailable"
        if payload.get("s") != "ok":
            raise MarketDataError(payload.get("errmsg", f"Unexpected option chain status: {payload.get('s')}"))

        rows = _unpack_columnar_payload(payload)
        volume_ratio = _put_call_ratio_from_rows(rows, "volume")
        if volume_ratio is not None:
            return volume_ratio, "volume"

        open_interest_ratio = _put_call_ratio_from_rows(rows, "openInterest")
        if open_interest_ratio is not None:
            return open_interest_ratio, "open_interest"
        return None, "unavailable"

    async def option_chain(self, symbol: str) -> list[dict]:
        if not self.api_key:
            raise MarketDataError("MARKETDATA_API_KEY is required for live options data")

        payload = await self._get(
            f"https://api.marketdata.app/v1/options/chain/{symbol}/",
            {"monthly": "false"},
        )
        if payload.get("s") == "no_data":
            return []
        if payload.get("s") != "ok":
            raise MarketDataError(payload.get("errmsg", f"Unexpected option chain status: {payload.get('s')}"))
        return _unpack_columnar_payload(payload)

    async def _get(self, url: str, params: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise MarketDataError(_http_error_message(error)) from error
            except httpx.TimeoutException as error:
                raise MarketDataError("MarketData request timed out") from error
            except httpx.HTTPError as error:
                raise MarketDataError(f"MarketData request failed: {error}") from error
            return response.json()


def _http_error_message(error: httpx.HTTPStatusError) -> str:
    status_code = error.response.status_code
    provider_message = _provider_error_message(error.response)
    rate_limit = _rate_limit_summary(error.response)
    if status_code == 429:
        details = ". ".join(item for item in [provider_message, rate_limit] if item)
        suffix = f" {details}" if details else ""
        return f"MarketData rate limit reached.{suffix}"
    if status_code == 403:
        return "MarketData access is temporarily blocked. The provider allows one active device/IP per token; wait a few minutes before retrying from Azure."
    return f"MarketData returned HTTP {status_code}"


def _provider_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    message = payload.get("errmsg") or payload.get("message")
    return str(message) if message else ""


def _rate_limit_summary(response: httpx.Response) -> str:
    headers = response.headers
    limit = headers.get("x-api-ratelimit-limit")
    remaining = headers.get("x-api-ratelimit-remaining")
    reset = headers.get("x-api-ratelimit-reset")
    consumed = headers.get("x-api-ratelimit-consumed")
    pieces = []
    if remaining is not None:
        pieces.append(f"remaining={remaining}")
    if limit is not None:
        pieces.append(f"limit={limit}")
    if consumed is not None:
        pieces.append(f"consumed={consumed}")
    if reset is not None:
        pieces.append(f"reset={reset}")
    return f"Rate limit headers: {', '.join(pieces)}." if pieces else ""


def _parse_marketdata_candles(payload: dict) -> list[Candle]:
    timestamps = payload.get("t", [])
    opens = payload.get("o", [])
    highs = payload.get("h", [])
    lows = payload.get("l", [])
    closes = payload.get("c", [])
    volumes = payload.get("v", [])

    candles: list[Candle] = []
    for index, timestamp in enumerate(timestamps):
        candles.append(
            Candle(
                timestamp=int(timestamp),
                open=float(opens[index]),
                high=float(highs[index]),
                low=float(lows[index]),
                close=float(closes[index]),
                volume=int(volumes[index]) if index < len(volumes) and volumes[index] is not None else 0,
            )
        )
    return candles


def _parse_marketdata_bulk_candles(payload: dict) -> dict[str, Candle]:
    symbols = payload.get("symbol", [])
    opens = payload.get("o", [])
    highs = payload.get("h", [])
    lows = payload.get("l", [])
    closes = payload.get("c", [])
    volumes = payload.get("v", [])
    timestamps = payload.get("t", [])

    candles: dict[str, Candle] = {}
    for index, symbol in enumerate(symbols):
        if index >= len(closes) or closes[index] is None:
            continue
        candles[str(symbol).upper()] = Candle(
            timestamp=int(timestamps[index]),
            open=float(opens[index]),
            high=float(highs[index]),
            low=float(lows[index]),
            close=float(closes[index]),
            volume=int(volumes[index]) if index < len(volumes) and volumes[index] is not None else 0,
        )
    return candles


def _unpack_columnar_payload(payload: dict) -> list[dict]:
    columns = {
        key: value
        for key, value in payload.items()
        if isinstance(value, list) and key != "s"
    }
    if not columns:
        return []
    row_count = max(len(value) for value in columns.values())
    rows: list[dict] = []
    for index in range(row_count):
        rows.append(
            {
                key: value[index] if index < len(value) else None
                for key, value in columns.items()
            }
        )
    return rows


def _put_call_ratio_from_rows(rows: list[dict], metric: str) -> float | None:
    put_total = 0.0
    call_total = 0.0
    for row in rows:
        value = row.get(metric)
        if value in (None, ""):
            continue
        side = str(row.get("side", "")).lower()
        if side == "put":
            put_total += float(value)
        elif side == "call":
            call_total += float(value)
    if call_total <= 0:
        return None
    return round(put_total / call_total, 2)


def demo_candles(symbol: str, countback: int = 260) -> list[Candle]:
    seed = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)
    drift = 0.00035 + (seed % 80) / 100000
    volatility = 0.012 + (seed % 25) / 5000
    base_price = 35 + (seed % 220)
    base_volume = 8_000_000 + (seed % 40_000_000)
    now = datetime.now(UTC)
    candles: list[Candle] = []
    close = float(base_price)

    for index in range(countback):
        cycle = math.sin((index + seed % 31) / 9.0) * volatility
        pulse = math.cos((index + seed % 17) / 21.0) * volatility * 0.45
        daily_return = drift + cycle + pulse
        previous_close = close
        close = max(1.0, close * (1 + daily_return))
        high = max(previous_close, close) * (1 + volatility * 0.55)
        low = min(previous_close, close) * (1 - volatility * 0.55)
        volume_wave = 1 + math.sin(index / 13.0 + seed % 7) * 0.25
        timestamp = int((now - timedelta(days=countback - index)).timestamp())
        candles.append(
            Candle(
                timestamp=timestamp,
                open=round(previous_close, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=int(base_volume * volume_wave),
            )
        )
    return candles
