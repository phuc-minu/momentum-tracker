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

SECTOR_UNIVERSE = {
    "Technology": ["NVDA", "MSFT", "AAPL", "AVGO", "AMD", "CRM", "ORCL", "ADBE", "NOW", "PLTR"],
    "Communication Services": ["META", "GOOGL", "NFLX", "DIS", "TTD", "ROKU", "SPOT", "PINS"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "BKNG", "MELI", "NKE", "SBUX", "RCL"],
    "Consumer Staples": ["COST", "WMT", "PG", "KO", "PEP", "CL", "MDLZ", "TGT"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP"],
    "Health Care": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ISRG", "REGN"],
    "Industrials": ["GE", "CAT", "DE", "HON", "ETN", "UBER", "BA", "LMT"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "OXY", "LNG"],
    "Materials": ["LIN", "APD", "SHW", "FCX", "NEM", "DD", "NUE", "ALB"],
    "Real Estate": ["PLD", "AMT", "EQIX", "WELL", "SPG", "O", "DLR", "VICI"],
    "Utilities": ["NEE", "SO", "DUK", "CEG", "AEP", "SRE", "D", "PCG"],
}

SYMBOL_TO_SECTOR = {
    symbol: sector
    for sector, symbols in SECTOR_UNIVERSE.items()
    for symbol in symbols
}


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


def discovery_universe() -> dict[str, str]:
    raw = os.getenv("DISCOVERY_UNIVERSE")
    if not raw:
        return SYMBOL_TO_SECTOR.copy()

    universe: dict[str, str] = {}
    for item in raw.split(","):
        if ":" in item:
            symbol, sector = item.split(":", 1)
            normalized = normalize_symbol(symbol)
            if normalized:
                universe[normalized] = sector.strip() or "Unclassified"
        else:
            normalized = normalize_symbol(item)
            if normalized:
                universe[normalized] = SYMBOL_TO_SECTOR.get(normalized, "Unclassified")
    return universe


def dedupe_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for symbol in symbols:
        value = normalize_symbol(symbol)
        if value and value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized
