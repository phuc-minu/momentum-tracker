from typing import Literal

from pydantic import BaseModel, Field


class Candle(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class ScanRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    benchmark: str = "QQQ"
    countback: int = Field(default=260, ge=80, le=520)
    demo: bool = False


class MomentumFactors(BaseModel):
    return_1m: float | None
    return_3m: float | None
    return_6m: float | None
    return_12m: float | None
    benchmark_return_3m: float | None
    relative_strength_3m: float | None
    distance_to_20d_high: float | None
    distance_to_50d_sma: float | None
    distance_to_200d_sma: float | None
    volume_ratio_20d_vs_60d: float | None


class MomentumSignal(BaseModel):
    symbol: str
    score: float
    grade: Literal["A", "B", "C", "D", "F"]
    setup: Literal["breakout", "pullback", "extended", "base", "weak"]
    last_close: float
    factors: MomentumFactors
    notes: list[str]


class ScanResponse(BaseModel):
    benchmark: str
    count: int
    results: list[MomentumSignal]
    data_mode: Literal["live", "demo"]
