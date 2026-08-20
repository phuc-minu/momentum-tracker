import asyncio
import hmac
import hashlib
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from discover import discover_movers
from marketdata import MarketDataClient, MarketDataError, demo_candles
from momentum import score_symbol
from phase2_db import connect, fetch_latest_refresh_run, fetch_phase2_candidates
from schemas import DiscoveryResponse, Phase2CandidateResponse, Phase2RefreshStatus, ScanRequest, ScanResponse
from universe import configured_universe, dedupe_symbols, parse_symbols

app = FastAPI(title="Stock Momentum AI", version="0.1.0")
STATIC_DIR = Path(__file__).parent / "static"
AUTH_COOKIE = "momentum_session"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_static_assets(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/health")
def health():
    return {
        "status": "ok",
        "marketdata_configured": bool(os.getenv("FMP_API_KEY") or os.getenv("MARKETDATA_API_KEY")),
        "provider": "fmp",
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
    }


class LoginRequest(BaseModel):
    password: str


def _app_password() -> str:
    password = os.getenv("APP_PASSWORD")
    if not password:
        raise HTTPException(status_code=503, detail="APP_PASSWORD is not configured")
    return password


def _session_secret() -> str:
    return os.getenv("APP_SESSION_SECRET") or _app_password()


def _session_token() -> str:
    return hmac.new(_session_secret().encode("utf-8"), b"authenticated", hashlib.sha256).hexdigest()


def _is_authenticated(request: Request) -> bool:
    if not os.getenv("APP_PASSWORD"):
        return False
    cookie = request.cookies.get(AUTH_COOKIE, "")
    return hmac.compare_digest(cookie, _session_token())


def require_auth(request: Request) -> None:
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")


@app.get("/auth/status")
def auth_status(request: Request):
    return {"authenticated": _is_authenticated(request)}


@app.post("/auth/login")
def auth_login(payload: LoginRequest, request: Request, response: Response):
    if not hmac.compare_digest(payload.password, _app_password()):
        raise HTTPException(status_code=401, detail="Invalid password")

    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        AUTH_COOKIE,
        _session_token(),
        httponly=True,
        secure=forwarded_proto == "https",
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )
    return {"authenticated": True}


@app.post("/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"authenticated": False}


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/universe")
def universe(_: None = Depends(require_auth)):
    return {"symbols": configured_universe()}


@app.get("/scan", response_model=ScanResponse)
async def scan(
    symbols: str | None = Query(default=None, description="Comma-separated tickers. Defaults to DEFAULT_UNIVERSE."),
    benchmark: str = Query(default=""),
    countback: int = Query(default=260, ge=80, le=520),
    demo: bool = Query(default=False, description="Use deterministic generated candles instead of live FMP data."),
    _: None = Depends(require_auth),
):
    request = ScanRequest(
        symbols=parse_symbols(symbols),
        benchmark=benchmark,
        countback=countback,
        demo=demo,
    )
    return await run_scan(request)


@app.get("/discover", response_model=DiscoveryResponse)
async def discover(
    growth_min: float = Query(default=0.20, ge=0, le=5),
    period_days: int = Query(default=10, ge=5, le=60),
    limit: int = Query(default=10, ge=1, le=50),
    exclude_earnings: bool = Query(default=True),
    demo: bool = Query(default=False),
    _: None = Depends(require_auth),
):
    try:
        results = await discover_movers(
            growth_min=growth_min,
            period_days=period_days,
            limit=limit,
            exclude_earnings=exclude_earnings,
            demo=demo,
        )
    except MarketDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return DiscoveryResponse(
        count=len(results),
        limit=limit,
        growth_min=growth_min,
        period_days=period_days,
        exclude_earnings=exclude_earnings,
        data_mode="demo" if demo else "live",
        results=results,
    )


@app.post("/scan", response_model=ScanResponse)
async def scan_post(request: ScanRequest, _: None = Depends(require_auth)):
    request.symbols = dedupe_symbols(request.symbols) or configured_universe()
    return await run_scan(request)


@app.get("/phase2/candidates", response_model=Phase2CandidateResponse)
def phase2_candidates(limit: int = Query(default=50, ge=1, le=200), _: None = Depends(require_auth)):
    try:
        with connect() as connection:
            rows = fetch_phase2_candidates(connection, limit=limit)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "count": len(rows),
        "limit": limit,
        "results": [_serialize_phase2_row(row) for row in rows],
    }


@app.get("/phase2/status", response_model=Phase2RefreshStatus | None)
def phase2_status(_: None = Depends(require_auth)):
    try:
        with connect() as connection:
            row = fetch_latest_refresh_run(connection)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return _serialize_phase2_row(row) if row else None


async def run_scan(request: ScanRequest) -> ScanResponse:
    benchmark = request.benchmark.strip().upper()
    symbols = [symbol for symbol in dedupe_symbols(request.symbols) if not benchmark or symbol != benchmark]
    if not symbols:
        raise HTTPException(status_code=400, detail="At least one symbol is required")

    try:
        if request.demo:
            benchmark_candles = demo_candles(benchmark, request.countback) if benchmark else []
            candle_map = {symbol: demo_candles(symbol, request.countback) for symbol in symbols}
            data_mode = "demo"
        else:
            client = MarketDataClient()
            if not client.api_key:
                raise MarketDataError("FMP_API_KEY is required for live market data")
            if benchmark:
                benchmark_candles, symbol_rows = await asyncio.gather(
                    client.candles(benchmark, request.countback),
                    _fetch_symbols(client, symbols, request.countback),
                )
            else:
                benchmark_candles = []
                symbol_rows = await _fetch_symbols(client, symbols, request.countback)
            candle_map = {symbol: candles for symbol, candles, error in symbol_rows if candles and not error}
            skipped_symbols = [symbol for symbol, candles, error in symbol_rows if error or not candles]
            data_mode = "live"

        results = [
            score_symbol(symbol, candles, benchmark_candles)
            for symbol, candles in candle_map.items()
            if candles
        ]
        skipped_symbols = skipped_symbols if not request.demo else []
        if not results and skipped_symbols:
            raise MarketDataError(f"FMP returned no usable candle data for {', '.join(skipped_symbols)}")
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
        skipped_symbols=skipped_symbols,
    )


def _serialize_phase2_row(row):
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else float(value) if _is_decimal(value) else value
        for key, value in row.items()
    }


def _is_decimal(value) -> bool:
    return value.__class__.__name__ == "Decimal"


async def _fetch_symbols(client: MarketDataClient, symbols: list[str], countback: int):
    semaphore = asyncio.Semaphore(2)

    async def fetch(symbol: str):
        async with semaphore:
            try:
                return symbol, await client.candles(symbol, countback), None
            except MarketDataError as error:
                return symbol, [], str(error)

    return await asyncio.gather(*(fetch(symbol) for symbol in symbols))
