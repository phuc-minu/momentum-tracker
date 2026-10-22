import asyncio
import hashlib
import json
import math
import os
import calendar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from schemas import Candle, NewsItem


class FmpError(RuntimeError):
    pass


class FmpCache:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or Path(os.getenv("FMP_CACHE_DIR", Path(__file__).parent / ".cache" / "fmp"))
        self.enabled = os.getenv("FMP_CACHE_DISABLED", "").strip().lower() not in {"1", "true", "yes", "on"}

    def get(self, key: str, ttl_seconds: int | None) -> Any | None:
        if not self.enabled:
            return None

        path = self._path(key)
        try:
            with path.open("r", encoding="utf-8") as file:
                cached = json.load(file)
        except (OSError, ValueError):
            return None

        if ttl_seconds is not None:
            fetched_at = float(cached.get("fetched_at", 0))
            if datetime.now(UTC).timestamp() - fetched_at > ttl_seconds:
                return None

        return cached.get("payload")

    def set(self, key: str, payload: Any) -> None:
        if not self.enabled:
            return

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(key)
            temp_path = path.with_suffix(".tmp")
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(
                    {
                        "version": 1,
                        "fetched_at": datetime.now(UTC).timestamp(),
                        "payload": payload,
                    },
                    file,
                    separators=(",", ":"),
                )
            temp_path.replace(path)
        except OSError:
            return

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"


class FmpClient:
    def __init__(self, api_key: str | None = None, timeout_seconds: float = 20.0, cache: FmpCache | None = None):
        self.api_key = api_key or os.getenv("FMP_API_KEY")
        self.base_url = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com/stable/").rstrip("/") + "/"
        self.fmp_options_chain_path = os.getenv("FMP_OPTIONS_CHAIN_PATH", "").strip()
        self.timeout_seconds = timeout_seconds
        self.cache = cache or FmpCache()

    @property
    def options_configured(self) -> bool:
        return bool(self.api_key and (self.fmp_options_chain_path or _fmp_positions_summary_enabled()))

    async def candles(self, symbol: str, countback: int = 60, resolution: str = "D") -> list[Candle]:
        if not self.api_key:
            raise FmpError("FMP_API_KEY is required for live FMP data")

        payload = await self._get(
            self._url("historical-price-eod/full"),
            {"symbol": symbol, "from": _from_date_for_countback(countback)},
            cache_ttl_seconds=_cache_ttl("FMP_CANDLE_CACHE_SECONDS", 900),
        )
        return _parse_fmp_historical_payload(payload)[-countback:]

    async def bulk_candles(
        self,
        symbols: list[str],
        *,
        date: str | None = None,
        resolution: str = "D",
    ) -> dict[str, Candle]:
        if not self.api_key:
            raise FmpError("FMP_API_KEY is required for live FMP data")
        if not symbols:
            return {}

        if not date:
            return await self._latest_quotes_as_candles(symbols)

        return await self._historical_date_as_candles(symbols, date)

    async def earnings(self, symbol: str, countback: int = 2) -> list[dict]:
        if not self.api_key:
            raise FmpError("FMP_API_KEY is required for live earnings data")

        payload = await self._get(
            self._url("earnings"),
            {"symbol": symbol, "limit": countback},
            cache_ttl_seconds=_cache_ttl("FMP_REFERENCE_CACHE_SECONDS", 3600),
        )
        return _as_rows(payload)[:countback]

    async def news(self, symbol: str, countback: int = 5) -> list[NewsItem]:
        if not self.api_key:
            raise FmpError("FMP_API_KEY is required for live news data")

        payload = await self._get(
            self._url("news/stock"),
            {"symbols": symbol, "limit": countback},
            cache_ttl_seconds=_cache_ttl("FMP_REFERENCE_CACHE_SECONDS", 3600),
        )
        rows = _as_rows(payload)
        return [
            NewsItem(
                headline=str(row.get("title") or row.get("headline") or ""),
                source=row.get("publisher") or row.get("site") or row.get("source"),
                publication_date=row.get("publishedDate") or row.get("date") or row.get("publicationDate"),
            )
            for row in rows
            if row.get("title") or row.get("headline")
        ]

    async def put_call_ratio(self, symbol: str) -> tuple[float | None, str]:
        rows = await self.option_chain(symbol)
        volume_ratio = _put_call_ratio_from_rows(rows, "volume")
        if volume_ratio is not None:
            return volume_ratio, "volume"
        return None, "unavailable"

    async def option_chain(self, symbol: str) -> list[dict]:
        if self.api_key and _fmp_positions_summary_enabled():
            payload = await self._get(
                self._url("institutional-ownership/symbol-positions-summary"),
                _fmp_positions_summary_params(symbol),
                cache_ttl_seconds=_cache_ttl("FMP_REFERENCE_CACHE_SECONDS", 3600),
            )
            rows = _fmp_positions_summary_to_option_rows(payload)
            if rows:
                return rows

        if self.fmp_options_chain_path:
            if not self.api_key:
                raise FmpError("FMP_API_KEY is required for FMP options data")
            payload = await self._get(
                self._url(self.fmp_options_chain_path.format(symbol=symbol)),
                {"symbol": symbol},
                cache_ttl_seconds=_cache_ttl("FMP_LATEST_CACHE_SECONDS", 300),
            )
            return _normalize_option_rows(payload)

        return []

    async def three_month_options_volume(self, symbol: str) -> dict:
        today = datetime.now(UTC).date()
        month_index = today.year * 12 + today.month - 1 + 3
        year, month = divmod(month_index, 12)
        target = today.replace(year=year, month=month + 1, day=min(today.day, calendar.monthrange(year, month + 1)[1]))
        empty = {"symbol": symbol, "target_expiration": target.isoformat(), "expiration": None,
                 "call_volume": None, "put_volume": None, "total_volume": None}
        # Institutional positions are holdings, not contract trading volume.
        if not self.api_key or not self.fmp_options_chain_path:
            return empty
        payload = await self._get(
            self._url(self.fmp_options_chain_path.format(symbol=symbol)),
            {"symbol": symbol},
            cache_ttl_seconds=_cache_ttl("FMP_LATEST_CACHE_SECONDS", 300),
        )
        return summarize_expiration_volume(_normalize_option_rows(payload), target, empty)

    async def _get(self, url: str, params: dict, *, cache_ttl_seconds: int | None = 300) -> Any:
        request_params = {**params, "apikey": self.api_key}
        cache_key = _cache_key(url, request_params)
        cached_payload = self.cache.get(cache_key, cache_ttl_seconds)
        if cached_payload is not None:
            return cached_payload

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(url, params=request_params)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise FmpError(_http_error_message(error)) from error
            except httpx.TimeoutException as error:
                raise FmpError("FMP request timed out") from error
            except httpx.HTTPError as error:
                raise FmpError(f"FMP request failed: {error}") from error
            if response.status_code == 204:
                payload = {"s": "no_data"}
            else:
                payload = response.json()

        if isinstance(payload, (dict, list)):
            self.cache.set(cache_key, payload)
        return payload

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path.lstrip('/')}"

    async def _latest_quotes_as_candles(self, symbols: list[str]) -> dict[str, Candle]:
        # The stable quote endpoint accepts one symbol and returns [] for a comma list;
        # batch-quote is not available on every plan.
        semaphore = asyncio.Semaphore(_quote_concurrency())

        async def fetch(symbol: str) -> Any:
            async with semaphore:
                return await self._get(
                    self._url("quote"),
                    {"symbol": symbol},
                    cache_ttl_seconds=_cache_ttl("FMP_LATEST_CACHE_SECONDS", 300),
                )

        candles = {}
        for payload in await asyncio.gather(*(fetch(symbol) for symbol in symbols)):
            for row in _as_rows(payload):
                candle = _fmp_quote_to_candle(row)
                if candle:
                    candles[str(row.get("symbol", "")).upper()] = candle
        return candles

    async def _historical_date_as_candles(self, symbols: list[str], date: str) -> dict[str, Candle]:
        candles = {}
        for symbol in symbols:
            payload = await self._get(
                self._url("historical-price-eod/full"),
                {"symbol": symbol, "from": date, "to": date},
                cache_ttl_seconds=None,
            )
            rows = _parse_fmp_historical_payload(payload)
            if rows:
                candles[symbol.upper()] = rows[-1]
        return candles


def summarize_expiration_volume(rows: list[dict], target, empty: dict) -> dict:
    expirations = {}
    for row in rows:
        value = row.get("expiration")
        try:
            if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
                expiration = datetime.fromtimestamp(float(value), UTC).date()
            else:
                expiration = datetime.fromisoformat(str(value)[:10]).date()
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        if abs((expiration - target).days) <= 15:
            expirations.setdefault(expiration, []).append(row)
    if not expirations:
        return empty
    expiration = min(expirations, key=lambda day: (abs((day - target).days), day))
    totals = {}
    for side in ("call", "put"):
        contracts = [row for row in expirations[expiration] if row.get("side") == side]
        volumes = [_as_float(row.get("volume")) for row in contracts]
        totals[side] = sum(volumes) if volumes and all(v is not None and math.isfinite(v) and v >= 0 for v in volumes) else None
    return {**empty, "expiration": expiration.isoformat(), "call_volume": totals["call"],
            "put_volume": totals["put"], "total_volume": totals["call"] + totals["put"]
            if all(v is not None for v in totals.values()) else None}


def _cache_key(url: str, params: dict) -> str:
    normalized_params = {
        str(key): str(value)
        for key, value in sorted(params.items())
    }
    return json.dumps({"url": url, "params": normalized_params}, sort_keys=True, separators=(",", ":"))


def _cache_ttl(name: str, default_seconds: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default_seconds
    try:
        return max(0, int(raw))
    except ValueError:
        return default_seconds


def _fmp_positions_summary_enabled() -> bool:
    raw = os.getenv("FMP_POSITIONS_SUMMARY_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _fmp_positions_summary_params(symbol: str) -> dict[str, int | str]:
    quarter, year = _fmp_positions_summary_period()
    return {"symbol": symbol, "year": year, "quarter": quarter}


def _fmp_positions_summary_period() -> tuple[int, int]:
    env_year = os.getenv("FMP_POSITIONS_SUMMARY_YEAR")
    env_quarter = os.getenv("FMP_POSITIONS_SUMMARY_QUARTER")
    if env_year and env_quarter:
        try:
            return max(1, min(4, int(env_quarter))), int(env_year)
        except ValueError:
            pass

    today = datetime.now(UTC).date()
    current_quarter = ((today.month - 1) // 3) + 1
    if current_quarter == 1:
        return 4, today.year - 1
    return current_quarter - 1, today.year


def _from_date_for_countback(countback: int) -> str:
    calendar_days = max(countback * 2, countback + 30)
    return (datetime.now(UTC).date() - timedelta(days=calendar_days)).isoformat()


def _quote_concurrency() -> int:
    try:
        return max(1, min(int(os.getenv("FMP_CONCURRENCY", "2")), 5))
    except ValueError:
        return 2


def _as_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("historical", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if payload.get("symbol"):
            return [payload]
    return []


def _unpack_columnar_payload(payload: dict) -> list[dict]:
    if "optionSymbol" not in payload and "underlying" not in payload:
        return []
    columns = {
        key: value
        for key, value in payload.items()
        if isinstance(value, list) and key not in {"s", "calls", "puts", "data", "results", "options", "chain"}
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


def _normalize_option_rows(payload: Any) -> list[dict]:
    rows: list[dict] = []
    for row in _flatten_option_payload(payload):
        normalized = _normalize_option_row(row)
        if normalized:
            rows.append(normalized)
    return rows


def _fmp_positions_summary_to_option_rows(payload: Any) -> list[dict]:
    rows = _as_rows(payload)
    if not rows:
        return []
    row = rows[0]
    total_calls = _as_float(row.get("totalCalls") or row.get("total_calls"))
    total_puts = _as_float(row.get("totalPuts") or row.get("total_puts"))
    put_call_ratio = _as_float(row.get("putCallRatio") or row.get("put_call_ratio"))
    if total_calls is None and total_puts is not None and put_call_ratio and put_call_ratio > 0:
        total_calls = total_puts / put_call_ratio
    if total_puts is None and total_calls is not None and put_call_ratio is not None:
        total_puts = total_calls * put_call_ratio
    if not total_calls and not total_puts:
        return []

    expiration = f"{row.get('year', '')}Q{row.get('quarter', '')}".strip("Q")
    symbol = row.get("symbol")
    return [
        {
            "optionSymbol": f"{symbol or ''}:institutional:calls",
            "symbol": symbol,
            "expiration": expiration or None,
            "side": "call",
            "volume": total_calls,
            "openInterest": _as_float(row.get("lastTotalCalls") or row.get("last_total_calls")),
            "source": "institutional",
        },
        {
            "optionSymbol": f"{symbol or ''}:institutional:puts",
            "symbol": symbol,
            "expiration": expiration or None,
            "side": "put",
            "volume": total_puts,
            "openInterest": _as_float(row.get("lastTotalPuts") or row.get("last_total_puts")),
            "source": "institutional",
        },
    ]


def _flatten_option_payload(payload: Any, side: str | None = None) -> list[dict]:
    if isinstance(payload, list):
        rows: list[dict] = []
        for item in payload:
            rows.extend(_flatten_option_payload(item, side))
        return rows
    if not isinstance(payload, dict):
        return []

    columnar_rows = _unpack_columnar_payload(payload)
    if columnar_rows:
        return columnar_rows

    rows = []
    for key in ("calls", "call"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(_flatten_option_payload(value, "call"))
    for key in ("puts", "put"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(_flatten_option_payload(value, "put"))

    for key in ("data", "results", "options", "chain"):
        value = payload.get(key)
        if isinstance(value, (list, dict)):
            rows.extend(_flatten_option_payload(value, side))

    if rows:
        return rows
    if any(key in payload for key in ("optionSymbol", "contractSymbol", "strike", "strikePrice", "volume", "openInterest")):
        return [{**payload, "_side": side}]
    return []


def _normalize_option_row(row: dict) -> dict | None:
    side = _normalize_option_side(row)
    if not side:
        return None
    bid = _as_float(row.get("bid"))
    ask = _as_float(row.get("ask"))
    mid = _as_float(row.get("mid") or row.get("mark"))
    if mid is None and bid is not None and ask is not None:
        mid = round((bid + ask) / 2, 4)
    option_symbol = row.get("optionSymbol") or row.get("contractSymbol") or row.get("symbol")
    return {
        "optionSymbol": option_symbol,
        "symbol": row.get("underlyingSymbol") or row.get("underlying") or row.get("ticker"),
        "expiration": row.get("expiration") or row.get("expirationDate") or row.get("expiry") or row.get("date"),
        "strike": _as_float(row.get("strike") or row.get("strikePrice")),
        "side": side,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "delta": _as_float(row.get("delta")),
        "openInterest": _as_float(row.get("openInterest") or row.get("open_interest")),
        "volume": _as_float(row.get("volume")),
    }


def _normalize_option_side(row: dict) -> str | None:
    raw_side = row.get("_side") or row.get("side") or row.get("type") or row.get("optionType")
    if raw_side:
        value = str(raw_side).lower()
        if value in {"call", "calls", "c"}:
            return "call"
        if value in {"put", "puts", "p"}:
            return "put"

    option_symbol = str(row.get("optionSymbol") or row.get("contractSymbol") or "")
    if len(option_symbol) >= 9:
        marker = option_symbol[-9]
        if marker == "C":
            return "call"
        if marker == "P":
            return "put"
    return None


def _parse_fmp_historical_payload(payload: Any) -> list[Candle]:
    rows = _as_rows(payload)
    candles = [_fmp_historical_row_to_candle(row) for row in rows]
    return sorted((candle for candle in candles if candle), key=lambda candle: candle.timestamp)


def _fmp_historical_row_to_candle(row: dict) -> Candle | None:
    date_value = row.get("date")
    if not date_value:
        return None
    try:
        timestamp = int(datetime.fromisoformat(str(date_value)[:10]).replace(tzinfo=UTC).timestamp())
        return Candle(
            timestamp=timestamp,
            open=float(row.get("open")),
            high=float(row.get("high")),
            low=float(row.get("low")),
            close=float(row.get("adjClose") or row.get("close")),
            volume=int(float(row.get("volume") or 0)),
        )
    except (TypeError, ValueError):
        return None


def _fmp_quote_to_candle(row: dict) -> Candle | None:
    price = row.get("price") or row.get("close")
    if price is None:
        return None
    timestamp = row.get("timestamp")
    try:
        return Candle(
            timestamp=int(timestamp) if timestamp else int(datetime.now(UTC).timestamp()),
            open=float(row.get("open") or price),
            high=float(row.get("dayHigh") or row.get("high") or price),
            low=float(row.get("dayLow") or row.get("low") or price),
            close=float(price),
            volume=int(float(row.get("volume") or 0)),
        )
    except (TypeError, ValueError):
        return None


def _http_error_message(error: httpx.HTTPStatusError, *, provider: str = "FMP") -> str:
    status_code = error.response.status_code
    provider_message = _provider_error_message(error.response)
    rate_limit = _rate_limit_summary(error.response)
    if status_code == 429:
        details = ". ".join(item for item in [provider_message, rate_limit] if item)
        suffix = f" {details}" if details else ""
        return f"{provider} rate limit reached.{suffix}"
    if status_code == 403:
        return "FMP access is forbidden. Check the FMP API key and plan entitlement."
    suffix = f": {provider_message}" if provider_message else ""
    return f"{provider} returned HTTP {status_code}{suffix}"


def _provider_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    # FMP returns a bare JSON list (e.g. []) on some 404s, such as symbols without options.
    if not isinstance(payload, dict):
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


def _as_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def demo_candles(symbol: str, countback: int = 60) -> list[Candle]:
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
