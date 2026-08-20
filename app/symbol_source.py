import csv
import os
from urllib.parse import urlencode
import urllib.request
from io import StringIO

from phase2_pipeline import phase2_symbol
from universe import dedupe_symbols

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
FMP_BASE_URL = "https://financialmodelingprep.com/stable/"


def configured_phase2_symbols() -> list[dict]:
    raw = os.getenv("PHASE2_SYMBOLS", "")
    symbols = dedupe_symbols(raw.split(","))
    return [phase2_symbol(symbol, exchange="configured") for symbol in symbols]


def load_phase2_symbols(*, source: str = "configured", limit: int = 500) -> list[dict]:
    if source == "configured":
        return configured_phase2_symbols()[:limit]
    if source in {"listed", "nasdaq"}:
        return fetch_listed_symbols(limit=limit)
    if source == "fmp":
        return fetch_fmp_screener_symbols(limit=limit)
    raise ValueError(f"Unsupported PHASE2_SYMBOL_SOURCE: {source}")


def fetch_fmp_screener_symbols(
    *,
    limit: int = 500,
    min_average_volume: int | None = None,
    min_price: float | None = None,
) -> list[dict]:
    api_key = os.getenv("FMP_API_KEY") or os.getenv("MARKETDATA_API_KEY")
    if not api_key:
        raise RuntimeError("FMP_API_KEY is required for PHASE2_SYMBOL_SOURCE=fmp")
    params = {
        "volumeMoreThan": str(min_average_volume or os.getenv("PHASE2_MIN_AVERAGE_VOLUME", "3000000")),
        "priceMoreThan": f"{min_price:g}" if min_price is not None else os.getenv("PHASE2_MIN_PRICE", "10"),
        "isActivelyTrading": "true",
        "isEtf": "false",
        "country": os.getenv("FMP_SCREENER_COUNTRY", "US"),
        "limit": str(limit),
        "apikey": api_key,
    }
    base_url = os.getenv("FMP_BASE_URL", FMP_BASE_URL).rstrip("/") + "/"
    rows = _json_rows(_download_text(f"{base_url}company-screener?{urlencode(params)}"))
    symbols = []
    for row in rows:
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol or not _looks_like_common_stock(symbol):
            continue
        symbols.append(
            phase2_symbol(
                symbol,
                name=row.get("companyName") or row.get("name"),
                exchange=row.get("exchangeShortName") or row.get("exchange"),
                sector=row.get("sector"),
            )
        )
    return symbols[:limit]


def fetch_listed_symbols(*, limit: int = 500) -> list[dict]:
    symbols = _fetch_nasdaq_listed() + _fetch_other_listed()
    filtered = [
        item
        for item in symbols
        if _looks_like_common_stock(item["symbol"])
    ]
    return filtered[:limit]


def _fetch_nasdaq_listed() -> list[dict]:
    text = _download_text(NASDAQ_LISTED_URL)
    rows = _pipe_rows(text)
    symbols = []
    for row in rows:
        symbol = row.get("Symbol", "").strip()
        if not symbol or row.get("Test Issue", "N") == "Y":
            continue
        symbols.append(
            phase2_symbol(
                symbol,
                name=row.get("Security Name"),
                exchange="NASDAQ",
            )
        )
    return symbols


def _fetch_other_listed() -> list[dict]:
    text = _download_text(OTHER_LISTED_URL)
    rows = _pipe_rows(text)
    symbols = []
    for row in rows:
        symbol = row.get("ACT Symbol", "").strip()
        if not symbol or row.get("Test Issue", "N") == "Y":
            continue
        symbols.append(
            phase2_symbol(
                symbol,
                name=row.get("Security Name"),
                exchange=row.get("Exchange"),
            )
        )
    return symbols


def _download_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _pipe_rows(text: str) -> list[dict[str, str]]:
    lines = [
        line
        for line in text.splitlines()
        if line and not line.startswith("File Creation Time")
    ]
    return list(csv.DictReader(StringIO("\n".join(lines)), delimiter="|"))


def _json_rows(text: str) -> list[dict]:
    import json

    payload = json.loads(text)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _looks_like_common_stock(symbol: str) -> bool:
    if not symbol:
        return False
    blocked_fragments = ("$", "^", "/", "+", "=")
    if any(fragment in symbol for fragment in blocked_fragments):
        return False
    suffixes = (".W", ".R", ".U", ".P")
    return not any(symbol.endswith(suffix) for suffix in suffixes)
