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
    benchmark: str = ""
    countback: int = Field(default=60, ge=30, le=120)
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
    current_volume_ratio_20d: float | None
    rsi_14: float | None


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
    skipped_symbols: list[str] = Field(default_factory=list)


class NewsItem(BaseModel):
    headline: str
    source: str | None = None
    publication_date: str | int | None = None


class PreferredContract(BaseModel):
    option_symbol: str
    expiration: str | int | None = None
    strike: float | None = None
    delta: float | None = None
    mid: float | None = None
    bid: float | None = None
    ask: float | None = None


class DiscoveryCandidate(BaseModel):
    symbol: str
    sector: str
    momentum_score: float
    setup: str
    price_signal: str
    relative_strength_percentile: float
    volume_ratio_20d: float | None = None
    return_1m: float
    return_2w: float
    start_close: float
    last_close: float
    rsi_14: float | None = None
    put_call_ratio: float | None = None
    put_call_source: Literal["volume", "open_interest", "institutional", "demo", "unavailable"] = "unavailable"
    call_volume: int | None = None
    call_open_interest: int | None = None
    call_activity_ratio: float | None = None
    call_activity: Literal["unusually elevated", "elevated", "normal", "unavailable"] = "unavailable"
    options_confirmation_score: float | None = None
    options_signal: Literal[
        "Confirmed bullish momentum",
        "Options confirming",
        "Options neutral",
        "Speculative call activity",
        "Options not confirming",
        "Options weak",
        "Options unavailable",
    ] = "Options unavailable"
    preferred_contract: PreferredContract | None = None
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


class Phase2Candidate(BaseModel):
    symbol: str
    name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    score: float
    grade: str
    setup: str
    last_close: float
    factors: dict
    notes: list[str]
    scored_at: str
    options_confirmation_score: float | None = None
    options_signal: str | None = None
    call_activity: str | None = None
    call_volume: int | None = None
    call_open_interest: int | None = None
    call_activity_ratio: float | None = None
    put_call_ratio: float | None = None
    put_call_source: str | None = None
    preferred_contract: dict | None = None


class Phase2CandidateResponse(BaseModel):
    count: int
    limit: int
    results: list[Phase2Candidate]


class Phase2RefreshLog(BaseModel):
    id: int
    run_id: int
    created_at: str
    level: str
    source: str
    message: str
    details: dict = Field(default_factory=dict)


class Phase2RefreshStatus(BaseModel):
    id: int
    started_at: str
    finished_at: str | None = None
    status: str
    symbols_requested: int
    symbols_scored: int
    error: str | None = None
    logs: list[Phase2RefreshLog] = Field(default_factory=list)
