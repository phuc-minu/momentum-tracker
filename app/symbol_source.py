import csv
import os
import urllib.request
from io import StringIO

from phase2_pipeline import phase2_symbol
from universe import dedupe_symbols

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def configured_phase2_symbols() -> list[dict]:
    raw = os.getenv("PHASE2_SYMBOLS", "")
    symbols = dedupe_symbols(raw.split(","))
    return [phase2_symbol(symbol, exchange="configured") for symbol in symbols]


def load_phase2_symbols(*, source: str = "configured", limit: int = 500) -> list[dict]:
    if source == "configured":
        return configured_phase2_symbols()[:limit]
    if source in {"listed", "nasdaq"}:
        return fetch_listed_symbols(limit=limit)
    raise ValueError(f"Unsupported PHASE2_SYMBOL_SOURCE: {source}")


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


def _looks_like_common_stock(symbol: str) -> bool:
    if not symbol:
        return False
    blocked_fragments = ("$", "^", "/", "+", "=")
    if any(fragment in symbol for fragment in blocked_fragments):
        return False
    suffixes = (".W", ".R", ".U", ".P")
    return not any(symbol.endswith(suffix) for suffix in suffixes)
