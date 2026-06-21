import asyncio
import logging
import time
from typing import Callable, Optional

from hyperliquid.info import Info
from hyperliquid.utils import constants

from data.storage import insert_candle, insert_candles

logger = logging.getLogger(__name__)

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
}


class DataFeed:
    def __init__(self, symbol: str = "BTC", interval: str = "1m", mode: str = "live"):
        self.symbol = symbol
        self.interval = interval
        self.mode = mode
        self._callbacks: list[Callable] = []
        self._running = False
        self._info = Info(constants.MAINNET_API_URL, skip_ws=True)

    def on_candle(self, callback: Callable):
        self._callbacks.append(callback)

    def _notify(self, candle: dict):
        for cb in self._callbacks:
            cb(candle)

    def fetch_historical(self, limit: int = 500, start_time: Optional[int] = None,
                         end_time: Optional[int] = None) -> list[dict]:
        now_ms = int(time.time() * 1000)
        interval_ms = INTERVAL_MS.get(self.interval, 60_000)

        if end_time is None:
            end_time = now_ms
        if start_time is None:
            start_time = end_time - (limit * interval_ms)

        try:
            raw = self._info.candles_snapshot(self.symbol, self.interval, start_time, end_time)
        except Exception as e:
            logger.error("Failed to fetch candles for %s: %s", self.symbol, e)
            return []

        candles = []
        for k in raw:
            candle = {
                "symbol": self.symbol,
                "interval": self.interval,
                "open_time": k["t"],
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": float(k["c"]),
                "volume": float(k["v"]),
                "close_time": k["t"] + interval_ms - 1,
                "quote_volume": 0.0,
                "num_trades": int(k.get("n", 0)),
            }
            candles.append(candle)

        if candles:
            insert_candles(candles)
        logger.info("Fetched %d historical candles for %s %s", len(candles), self.symbol, self.interval)
        return candles

    async def _poll_candles(self):
        interval_ms = INTERVAL_MS.get(self.interval, 60_000)
        poll_seconds = interval_ms / 1000
        last_open_time = 0

        logger.info("Polling candles for %s %s every %ds", self.symbol, self.interval, int(poll_seconds))

        while self._running:
            try:
                now_ms = int(time.time() * 1000)
                start = now_ms - (3 * interval_ms)
                raw = self._info.candles_snapshot(self.symbol, self.interval, start, now_ms)

                for k in raw:
                    if k["t"] > last_open_time:
                        candle = {
                            "symbol": self.symbol,
                            "interval": self.interval,
                            "open_time": k["t"],
                            "open": float(k["o"]),
                            "high": float(k["h"]),
                            "low": float(k["l"]),
                            "close": float(k["c"]),
                            "volume": float(k["v"]),
                            "close_time": k["t"] + interval_ms - 1,
                            "quote_volume": 0.0,
                            "num_trades": int(k.get("n", 0)),
                        }
                        insert_candle(candle)
                        self._notify(candle)
                        last_open_time = k["t"]

            except Exception as e:
                logger.warning("Poll error for %s: %s", self.symbol, e)

            await asyncio.sleep(poll_seconds)

    async def start_async(self):
        self._running = True
        if self.mode == "live":
            await self._poll_candles()

    def stop(self):
        self._running = False
