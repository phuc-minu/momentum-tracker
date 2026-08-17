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
from schemas import DiscoveryResponse, ScanRequest, ScanResponse
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
        "marketdata_configured": bool(os.getenv("MARKETDATA_API_KEY")),
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

    response.set_cookie(
        AUTH_COOKIE,
        _session_token(),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return {"authenticated": True}


@app.post("/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(AUTH_COOKIE)
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
    benchmark: str = Query(default="QQQ", min_length=1),
    countback: int = Query(default=260, ge=80, le=520),
    demo: bool = Query(default=False, description="Use deterministic generated candles instead of live MarketData."),
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
    semaphore = asyncio.Semaphore(2)

    async def fetch(symbol: str):
        async with semaphore:
            return await client.candles(symbol, countback)

    return await asyncio.gather(*(fetch(symbol) for symbol in symbols))
