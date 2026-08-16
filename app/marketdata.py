import hashlib
import math
import os
from datetime import UTC, datetime, timedelta

import httpx

from schemas import Candle


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
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()

        status = payload.get("s")
        if status == "no_data":
            return []
        if status != "ok":
            message = payload.get("errmsg", f"Unexpected MarketData status: {status}")
            raise MarketDataError(message)

        return _parse_marketdata_candles(payload)


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
