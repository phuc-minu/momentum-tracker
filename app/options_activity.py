import hashlib
from datetime import UTC, datetime

from schemas import PreferredContract


def analyze_options_activity(rows: list[dict]) -> dict:
    put_volume = _sum_side(rows, "put", "volume")
    call_volume = _sum_side(rows, "call", "volume")
    put_open_interest = _sum_side(rows, "put", "openInterest")
    call_open_interest = _sum_side(rows, "call", "openInterest")

    put_call_ratio = _ratio(put_volume, call_volume)
    put_call_source = "volume"
    if put_call_ratio is None:
        put_call_ratio = _ratio(put_open_interest, call_open_interest)
        put_call_source = "open_interest" if put_call_ratio is not None else "unavailable"

    call_activity_ratio = _ratio(call_volume, call_open_interest)
    call_activity = _classify_call_activity(call_volume, call_activity_ratio, put_call_ratio)
    preferred_contract = preferred_call_contract(rows)
    options_score = options_confirmation_score(
        put_call_ratio=put_call_ratio,
        call_volume=call_volume,
        call_open_interest=call_open_interest,
        call_activity_ratio=call_activity_ratio,
        call_activity=call_activity,
        preferred_contract=preferred_contract,
    )

    return {
        "put_call_ratio": round(put_call_ratio, 2) if put_call_ratio is not None else None,
        "put_call_source": put_call_source,
        "call_volume": int(call_volume) if call_volume else None,
        "call_open_interest": int(call_open_interest) if call_open_interest else None,
        "call_activity_ratio": round(call_activity_ratio, 2) if call_activity_ratio is not None else None,
        "call_activity": call_activity,
        "options_confirmation_score": options_score,
        "options_signal": options_signal(options_score, put_call_ratio, call_activity, call_activity_ratio),
        "preferred_contract": preferred_contract,
    }


def options_confirmation_score(
    *,
    put_call_ratio: float | None,
    call_volume: float,
    call_open_interest: float,
    call_activity_ratio: float | None,
    call_activity: str,
    preferred_contract: PreferredContract | None,
) -> float | None:
    if not call_volume and put_call_ratio is None and preferred_contract is None:
        return None

    score = 45.0
    if put_call_ratio is not None:
        if put_call_ratio <= 0.55:
            score += 20
        elif put_call_ratio <= 0.8:
            score += 13
        elif put_call_ratio <= 1.1:
            score += 4
        elif put_call_ratio >= 1.5:
            score -= 18
        elif put_call_ratio >= 1.2:
            score -= 10

    if call_activity == "unusually elevated":
        score += 18
    elif call_activity == "elevated":
        score += 10
    elif call_activity == "normal" and call_volume:
        score += 2

    if call_activity_ratio is not None:
        if 0.2 <= call_activity_ratio <= 1.5:
            score += 10
        elif 1.5 < call_activity_ratio <= 4:
            score += 4
        elif call_activity_ratio > 6:
            score -= 10

    if call_open_interest and call_volume:
        oi_ratio = call_volume / call_open_interest
        if 0.05 <= oi_ratio <= 0.75:
            score += 6
        elif oi_ratio > 3:
            score -= 8

    if preferred_contract and preferred_contract.delta is not None:
        delta = abs(preferred_contract.delta)
        if 0.45 <= delta <= 0.8:
            score += 8
        elif delta > 0.9:
            score -= 4

    return round(min(max(score, 0), 100), 1)


def options_signal(
    score: float | None,
    put_call_ratio: float | None,
    call_activity: str,
    call_activity_ratio: float | None,
) -> str:
    if score is None:
        return "Options unavailable"
    if score >= 80:
        return "Confirmed bullish momentum"
    if score >= 65:
        return "Options confirming"
    if call_activity in {"elevated", "unusually elevated"} and (call_activity_ratio or 0) > 4:
        return "Speculative call activity"
    if score >= 50:
        return "Options neutral"
    if put_call_ratio is not None and put_call_ratio >= 1.2:
        return "Options not confirming"
    return "Options weak"


def preferred_call_contract(rows: list[dict]) -> PreferredContract | None:
    calls = [
        row
        for row in rows
        if str(row.get("side", "")).lower() == "call"
        and _as_float(row.get("delta")) is not None
    ]
    if not calls:
        return None

    ranked = sorted(
        calls,
        key=lambda row: (
            _expiration_distance(row.get("expiration")),
            abs(abs(_as_float(row.get("delta")) or 0) - 0.75),
            -(_as_float(row.get("volume")) or 0),
        ),
    )
    row = ranked[0]
    return PreferredContract(
        option_symbol=str(row.get("optionSymbol") or row.get("symbol") or ""),
        expiration=row.get("expiration"),
        strike=_as_float(row.get("strike")),
        delta=_as_float(row.get("delta")),
        mid=_as_float(row.get("mid")),
        bid=_as_float(row.get("bid")),
        ask=_as_float(row.get("ask")),
    )


def demo_options_activity(symbol: str) -> dict:
    seed = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)
    put_call_ratio = round(0.45 + (seed % 130) / 100, 2)
    call_volume = 1_000 + seed % 22_000
    call_open_interest = 4_000 + seed % 40_000
    call_activity_ratio = round(call_volume / call_open_interest, 2)
    call_activity = _classify_call_activity(call_volume, call_activity_ratio, put_call_ratio)
    strike = 50 + seed % 260
    preferred_contract = PreferredContract(
        option_symbol=f"{symbol}270115C{strike:08d}",
        expiration="2027-01-15",
        strike=float(strike),
        delta=0.75,
        mid=round(8 + (seed % 900) / 100, 2),
        bid=round(7.8 + (seed % 900) / 100, 2),
        ask=round(8.2 + (seed % 900) / 100, 2),
    )
    options_score = options_confirmation_score(
        put_call_ratio=put_call_ratio,
        call_volume=call_volume,
        call_open_interest=call_open_interest,
        call_activity_ratio=call_activity_ratio,
        call_activity=call_activity,
        preferred_contract=preferred_contract,
    )
    return {
        "put_call_ratio": put_call_ratio,
        "put_call_source": "demo",
        "call_volume": call_volume,
        "call_open_interest": call_open_interest,
        "call_activity_ratio": call_activity_ratio,
        "call_activity": call_activity,
        "options_confirmation_score": options_score,
        "options_signal": options_signal(options_score, put_call_ratio, call_activity, call_activity_ratio),
        "preferred_contract": preferred_contract,
    }


def _sum_side(rows: list[dict], side: str, metric: str) -> float:
    total = 0.0
    for row in rows:
        if str(row.get("side", "")).lower() != side:
            continue
        value = _as_float(row.get(metric))
        if value:
            total += value
    return total


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _classify_call_activity(
    call_volume: float,
    call_activity_ratio: float | None,
    put_call_ratio: float | None,
) -> str:
    if not call_volume:
        return "unavailable"
    if call_volume >= 5_000 and (call_activity_ratio or 0) >= 0.35 and (put_call_ratio is None or put_call_ratio <= 0.8):
        return "unusually elevated"
    if call_volume >= 2_000 and ((call_activity_ratio or 0) >= 0.2 or (put_call_ratio is not None and put_call_ratio <= 0.7)):
        return "elevated"
    return "normal"


def _expiration_distance(raw_expiration) -> int:
    target = datetime(2027, 1, 15, tzinfo=UTC).date()
    parsed = _parse_expiration(raw_expiration)
    if parsed is None:
        return 99_999
    return abs((parsed - target).days)


def _parse_expiration(raw_expiration):
    if raw_expiration is None:
        return None
    if isinstance(raw_expiration, (int, float)):
        return datetime.fromtimestamp(raw_expiration, UTC).date()
    try:
        return datetime.fromisoformat(str(raw_expiration).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _as_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
