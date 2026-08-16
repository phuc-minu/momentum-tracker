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


class NewsItem(BaseModel):
    headline: str
    source: str | None = None
    publication_date: str | int | None = None


class DiscoveryCandidate(BaseModel):
    symbol: str
    sector: str
    return_2w: float
    start_close: float
    last_close: float
    earnings_near_move: bool
    earnings_checked: bool
    likely_earnings_related: bool
    catalyst: str
    headlines: list[NewsItem] = Field(default_factory=list)


class DiscoveryResponse(BaseModel):
    count: int
    limit: int
    growth_min: float
    period_days: int
    exclude_earnings: bool
    data_mode: Literal["live", "demo"]
    results: list[DiscoveryCandidate]
