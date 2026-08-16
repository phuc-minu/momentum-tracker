import os


DEFAULT_UNIVERSE = [
    "NVDA",
    "MSFT",
    "AAPL",
    "AVGO",
    "AMD",
    "META",
    "GOOGL",
    "AMZN",
    "TSLA",
    "QQQ",
]


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def parse_symbols(raw_symbols: str | None) -> list[str]:
    if not raw_symbols:
        return configured_universe()
    return dedupe_symbols(raw_symbols.split(","))


def configured_universe() -> list[str]:
    raw = os.getenv("DEFAULT_UNIVERSE")
    if not raw:
        return DEFAULT_UNIVERSE.copy()
    return dedupe_symbols(raw.split(","))


def dedupe_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for symbol in symbols:
        value = normalize_symbol(symbol)
        if value and value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized
