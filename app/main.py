import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from marketdata import MarketDataClient, MarketDataError, demo_candles
from momentum import score_symbol
from schemas import ScanRequest, ScanResponse
from universe import configured_universe, dedupe_symbols, parse_symbols

app = FastAPI(title="Tech Momentum AI", version="0.1.0")
STATIC_DIR = Path(__file__).parent / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "marketdata_configured": bool(os.getenv("MARKETDATA_API_KEY")),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/universe")
def universe():
    return {"symbols": configured_universe()}


@app.get("/scan", response_model=ScanResponse)
async def scan(
    symbols: str | None = Query(default=None, description="Comma-separated tickers. Defaults to DEFAULT_UNIVERSE."),
    benchmark: str = Query(default="QQQ", min_length=1),
    countback: int = Query(default=260, ge=80, le=520),
    demo: bool = Query(default=False, description="Use deterministic generated candles instead of live MarketData."),
):
    request = ScanRequest(
        symbols=parse_symbols(symbols),
        benchmark=benchmark,
        countback=countback,
        demo=demo,
    )
    return await run_scan(request)


@app.post("/scan", response_model=ScanResponse)
async def scan_post(request: ScanRequest):
    request.symbols = dedupe_symbols(request.symbols) or configured_universe()
    return await run_scan(request)


async def run_scan(request: ScanRequest) -> ScanResponse:
    benchmark = request.benchmark.strip().upper()
    symbols = [symbol for symbol in dedupe_symbols(request.symbols) if symbol != benchmark]
    if not symbols:
        raise HTTPException(status_code=400, detail="At least one non-benchmark symbol is required")

    try:
        if request.demo:
            benchmark_candles = demo_candles(benchmark, request.countback)
            candle_map = {symbol: demo_candles(symbol, request.countback) for symbol in symbols}
            data_mode = "demo"
        else:
            client = MarketDataClient()
            benchmark_candles, symbol_candles = await asyncio.gather(
                client.candles(benchmark, request.countback),
                _fetch_symbols(client, symbols, request.countback),
            )
            candle_map = dict(zip(symbols, symbol_candles, strict=True))
            data_mode = "live"

        results = [
            score_symbol(symbol, candles, benchmark_candles)
            for symbol, candles in candle_map.items()
            if candles
        ]
    except MarketDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    results.sort(key=lambda item: item.score, reverse=True)
    return ScanResponse(
        benchmark=benchmark,
        count=len(results),
        results=results,
        data_mode=data_mode,
    )


async def _fetch_symbols(client: MarketDataClient, symbols: list[str], countback: int):
    return await asyncio.gather(*(client.candles(symbol, countback) for symbol in symbols))
