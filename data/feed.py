import asyncio
import json
import logging
from typing import Callable, Optional

import requests
import websockets

from data.storage import insert_candle, insert_candles

logger = logging.getLogger(__name__)

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
BINANCE_REST_URL = "https://api.binance.com/api/v3"


class DataFeed:
    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1m", mode: str = "live"):
        self.symbol = symbol.upper()
        self.interval = interval
        self.mode = mode
        self._callbacks: list[Callable] = []
        self._running = False
        self._ws = None

    def on_candle(self, callback: Callable):
        self._callbacks.append(callback)

    def _notify(self, candle: dict):
        for cb in self._callbacks:
            cb(candle)

    def fetch_historical(self, limit: int = 500, start_time: Optional[int] = None,
                         end_time: Optional[int] = None) -> list[dict]:
        params = {
            "symbol": self.symbol,
            "interval": self.interval,
            "limit": limit,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        resp = requests.get(f"{BINANCE_REST_URL}/klines", params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()

        candles = []
        for k in raw:
            candle = {
                "symbol": self.symbol,
                "interval": self.interval,
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "num_trades": int(k[8]),
            }
            candles.append(candle)

        insert_candles(candles)
        logger.info("Fetched %d historical candles for %s %s", len(candles), self.symbol, self.interval)
        return candles

    async def _ws_stream(self):
        stream = f"{self.symbol.lower()}@kline_{self.interval}"
        url = f"{BINANCE_WS_URL}/{stream}"
        logger.info("Connecting to WebSocket: %s", url)

        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=10):
            self._ws = ws
            try:
                async for msg in ws:
                    if not self._running:
                        break
                    data = json.loads(msg)
                    k = data.get("k", {})
                    if not k:
                        continue

                    candle = {
                        "symbol": k["s"],
                        "interval": k["i"],
                        "open_time": k["t"],
                        "open": float(k["o"]),
                        "high": float(k["h"]),
                        "low": float(k["l"]),
                        "close": float(k["c"]),
                        "volume": float(k["v"]),
                        "close_time": k["T"],
                        "quote_volume": float(k["q"]),
                        "num_trades": k["n"],
                    }

                    is_closed = k["x"]
                    if is_closed:
                        insert_candle(candle)
                        logger.debug("Candle closed: %s", candle)
                        self._notify(candle)

            except websockets.ConnectionClosed:
                if not self._running:
                    break
                logger.warning("WebSocket disconnected, reconnecting...")
                continue

    def start(self):
        self._running = True
        if self.mode == "live":
            asyncio.get_event_loop().run_until_complete(self._ws_stream())

    async def start_async(self):
        self._running = True
        if self.mode == "live":
            await self._ws_stream()

    def stop(self):
        self._running = False
        if self._ws:
            asyncio.ensure_future(self._ws.close())


class OrderBookFeed:
    def __init__(self, symbol: str = "BTCUSDT", depth: int = 10):
        self.symbol = symbol.upper()
        self.depth = depth
        self._callbacks: list[Callable] = []
        self._running = False

    def on_update(self, callback: Callable):
        self._callbacks.append(callback)

    def fetch_snapshot(self) -> dict:
        resp = requests.get(
            f"{BINANCE_REST_URL}/depth",
            params={"symbol": self.symbol, "limit": self.depth},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    async def stream(self):
        stream = f"{self.symbol.lower()}@depth{self.depth}@100ms"
        url = f"{BINANCE_WS_URL}/{stream}"
        self._running = True

        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=10):
            try:
                async for msg in ws:
                    if not self._running:
                        break
                    data = json.loads(msg)
                    for cb in self._callbacks:
                        cb(data)
            except websockets.ConnectionClosed:
                if not self._running:
                    break
                logger.warning("OrderBook WebSocket disconnected, reconnecting...")
                continue

    def stop(self):
        self._running = False
