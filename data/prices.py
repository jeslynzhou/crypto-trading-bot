import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BINANCE_REST = "https://api.binance.com/api/v3"
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"


def get_binance_prices(symbols: list[str]) -> dict[str, float]:
    try:
        resp = requests.get(f"{BINANCE_REST}/ticker/price", timeout=5)
        resp.raise_for_status()
        all_prices = {t["symbol"]: float(t["price"]) for t in resp.json()}
        return {s: all_prices.get(s, 0.0) for s in symbols}
    except Exception as e:
        logger.error("Failed to fetch Binance prices: %s", e)
        return {s: 0.0 for s in symbols}


def get_binance_24h_stats(symbols: list[str]) -> dict[str, dict]:
    try:
        resp = requests.get(f"{BINANCE_REST}/ticker/24hr", timeout=5)
        resp.raise_for_status()
        all_stats = {t["symbol"]: t for t in resp.json()}
        result = {}
        for s in symbols:
            raw = all_stats.get(s, {})
            result[s] = {
                "price": float(raw.get("lastPrice", 0)),
                "change_pct": float(raw.get("priceChangePercent", 0)),
                "high": float(raw.get("highPrice", 0)),
                "low": float(raw.get("lowPrice", 0)),
                "volume": float(raw.get("quoteVolume", 0)),
            }
        return result
    except Exception as e:
        logger.error("Failed to fetch Binance 24h stats: %s", e)
        return {}


def get_hyperliquid_prices(assets: Optional[list[str]] = None) -> dict[str, float]:
    try:
        resp = requests.post(HYPERLIQUID_API, json={"type": "allMids"}, timeout=5)
        resp.raise_for_status()
        mids = resp.json()
        if assets:
            return {a: float(mids.get(a, 0)) for a in assets}
        return {k: float(v) for k, v in mids.items()}
    except Exception as e:
        logger.error("Failed to fetch Hyperliquid prices: %s", e)
        return {}


def get_hyperliquid_meta() -> list[dict]:
    try:
        resp = requests.post(HYPERLIQUID_API, json={"type": "meta"}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("universe", [])
    except Exception as e:
        logger.error("Failed to fetch Hyperliquid meta: %s", e)
        return []
