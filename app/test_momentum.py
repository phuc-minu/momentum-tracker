from fastapi.testclient import TestClient

from main import app
from marketdata import demo_candles
from momentum import score_symbol


def test_score_symbol_returns_rankable_signal():
    benchmark = demo_candles("QQQ", 260)
    signal = score_symbol("NVDA", demo_candles("NVDA", 260), benchmark)

    assert signal.symbol == "NVDA"
    assert 0 <= signal.score <= 100
    assert signal.grade in {"A", "B", "C", "D", "F"}
    assert signal.setup in {"breakout", "pullback", "extended", "base", "weak"}
    assert signal.factors.return_3m is not None
    assert signal.notes


def test_demo_scan_endpoint_ranks_results():
    client = TestClient(app)

    response = client.get("/scan", params={"symbols": "NVDA,MSFT,AAPL", "demo": "true"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "demo"
    assert payload["benchmark"] == "QQQ"
    assert payload["count"] == 3
    scores = [item["score"] for item in payload["results"]]
    assert scores == sorted(scores, reverse=True)


def test_live_scan_without_api_key_returns_503(monkeypatch):
    monkeypatch.delenv("MARKETDATA_API_KEY", raising=False)
    client = TestClient(app)

    response = client.get("/scan", params={"symbols": "NVDA", "demo": "false"})

    assert response.status_code == 503
    assert "MARKETDATA_API_KEY" in response.json()["detail"]
