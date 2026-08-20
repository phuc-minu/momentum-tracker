from statistics import fmean

from schemas import Candle, MomentumFactors, MomentumSignal


def score_symbol(symbol: str, candles: list[Candle], benchmark_candles: list[Candle]) -> MomentumSignal:
    if len(candles) < 80:
        raise ValueError(f"{symbol} needs at least 80 candles")

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    volumes = [c.volume for c in candles]
    last_close = closes[-1]

    return_1m = _return(closes, 21)
    return_3m = _return(closes, 63)
    return_6m = _return(closes, 126)
    return_12m = _return(closes, 252)

    benchmark_closes = [c.close for c in benchmark_candles]
    benchmark_return_3m = _return(benchmark_closes, 63)
    relative_strength_3m = _safe_subtract(return_3m, benchmark_return_3m)

    high_20d = max(highs[-20:])
    sma_50 = _sma(closes, 50)
    sma_200 = _sma(closes, 200)
    volume_20 = _average(volumes[-20:])
    volume_60 = _average(volumes[-60:])
    prior_volume_20 = _average(volumes[-21:-1]) if len(volumes) >= 21 else None
    rsi_14 = _rsi(closes, 14)

    distance_to_20d_high = _ratio(last_close, high_20d) - 1 if high_20d else None
    distance_to_50d_sma = _ratio(last_close, sma_50) - 1 if sma_50 else None
    distance_to_200d_sma = _ratio(last_close, sma_200) - 1 if sma_200 else None
    volume_ratio = _ratio(volume_20, volume_60) if volume_60 else None
    current_volume_ratio = _ratio(volumes[-1], prior_volume_20) if prior_volume_20 else None

    factors = MomentumFactors(
        return_1m=return_1m,
        return_3m=return_3m,
        return_6m=return_6m,
        return_12m=return_12m,
        benchmark_return_3m=benchmark_return_3m,
        relative_strength_3m=relative_strength_3m,
        distance_to_20d_high=distance_to_20d_high,
        distance_to_50d_sma=distance_to_50d_sma,
        distance_to_200d_sma=distance_to_200d_sma,
        volume_ratio_20d_vs_60d=volume_ratio,
        current_volume_ratio_20d=current_volume_ratio,
        rsi_14=rsi_14,
    )

    score = _score(factors)
    setup = _classify_setup(factors)
    notes = _notes(factors, setup)

    return MomentumSignal(
        symbol=symbol,
        score=round(score, 1),
        grade=_grade(score),
        setup=setup,
        last_close=round(last_close, 2),
        factors=factors,
        notes=notes,
    )


def _score(factors: MomentumFactors) -> float:
    raw = 50.0
    raw += _clamp((factors.return_3m or 0) * 120, -18, 22)
    raw += _clamp((factors.return_6m or 0) * 70, -16, 18)
    raw += _clamp((factors.return_12m or 0) * 35, -12, 14)
    raw += _clamp((factors.relative_strength_3m or 0) * 100, -15, 18)

    if factors.distance_to_50d_sma is not None:
        raw += _clamp(factors.distance_to_50d_sma * 70, -10, 10)
    if factors.distance_to_200d_sma is not None:
        raw += _clamp(factors.distance_to_200d_sma * 35, -10, 10)
    if factors.distance_to_20d_high is not None:
        raw += _clamp((0.03 + factors.distance_to_20d_high) * 120, -8, 8)
    if factors.volume_ratio_20d_vs_60d is not None:
        raw += _clamp((factors.volume_ratio_20d_vs_60d - 1) * 14, -4, 6)

    return adjusted_momentum_score(
        _clamp(raw, 0, 94),
        rsi_14=factors.rsi_14,
        current_volume_ratio_20d=factors.current_volume_ratio_20d,
    )


def adjusted_momentum_score(
    base_score: float,
    *,
    rsi_14: float | None = None,
    call_activity: str | None = None,
    current_volume_ratio_20d: float | None = None,
) -> float:
    score = base_score
    if current_volume_ratio_20d is not None:
        score += _clamp((current_volume_ratio_20d - 1) * 8, -6, 8)

    if rsi_14 is not None:
        if rsi_14 >= 85:
            score -= 10
        elif rsi_14 >= 75:
            score -= 6
        elif rsi_14 >= 70:
            score -= 3

    if call_activity == "unusually elevated":
        score += 6
    elif call_activity == "elevated":
        score += 3

    return round(_clamp(score, 0, 100), 1)


def grade_for_score(score: float) -> str:
    return _grade(score)


def _classify_setup(factors: MomentumFactors) -> str:
    near_high = factors.distance_to_20d_high is not None and factors.distance_to_20d_high >= -0.025
    above_50 = factors.distance_to_50d_sma is not None and factors.distance_to_50d_sma > 0
    above_200 = factors.distance_to_200d_sma is not None and factors.distance_to_200d_sma > 0
    stretched = factors.distance_to_50d_sma is not None and factors.distance_to_50d_sma > 0.12
    constructive_pullback = (
        factors.distance_to_50d_sma is not None
        and -0.04 <= factors.distance_to_50d_sma <= 0.04
        and above_200
    )

    if factors.return_3m is not None and factors.return_3m < -0.08:
        return "weak"
    if near_high and above_50:
        return "breakout"
    if constructive_pullback:
        return "pullback"
    if stretched and above_200:
        return "extended"
    return "base"


def _notes(factors: MomentumFactors, setup: str) -> list[str]:
    notes = [f"Setup reads as {setup}."]
    if factors.relative_strength_3m is not None:
        notes.append(f"3m relative strength vs benchmark is {_percent(factors.relative_strength_3m)}.")
    if factors.distance_to_50d_sma is not None:
        notes.append(f"Price is {_percent(factors.distance_to_50d_sma)} from the 50d SMA.")
    if factors.volume_ratio_20d_vs_60d is not None:
        notes.append(f"20d volume is {factors.volume_ratio_20d_vs_60d:.2f}x the 60d average.")
    if factors.current_volume_ratio_20d is not None:
        notes.append(f"Current volume is {factors.current_volume_ratio_20d:.2f}x the prior 20d average.")
    if factors.rsi_14 is not None:
        notes.append(f"14d RSI is {factors.rsi_14:.1f}.")
    return notes


def _return(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return values[-1] / values[-periods - 1] - 1


def _sma(values: list[float], periods: int) -> float | None:
    if len(values) < periods:
        return None
    return fmean(values[-periods:])


def _average(values: list[int]) -> float | None:
    if not values:
        return None
    return fmean(values)


def _rsi(values: list[float], periods: int = 14) -> float | None:
    if len(values) <= periods:
        return None
    changes = [
        values[index] - values[index - 1]
        for index in range(len(values) - periods, len(values))
    ]
    gains = [max(change, 0) for change in changes]
    losses = [abs(min(change, 0)) for change in changes]
    average_gain = fmean(gains)
    average_loss = fmean(losses)
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return round(100 - (100 / (1 + relative_strength)), 1)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _safe_subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _percent(value: float) -> str:
    return f"{value * 100:+.1f}%"
