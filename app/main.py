import os
from fastapi import FastAPI

app = FastAPI(title="Tech Momentum AI", version="0.1.0")

@app.get("/health")
def health():
    return {
        "status": "ok",
        "marketdata_configured": bool(os.getenv("MARKETDATA_API_KEY")),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
    }

@app.get("/")
def root():
    return {
        "service": "Tech Momentum AI",
        "version": "0.1.0",
        "next": "momentum scanner + LLM ranking",
    }
