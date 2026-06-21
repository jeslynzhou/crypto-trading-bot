import logging
import time
from typing import Callable, Optional

from data.storage import get_candles
from data.feed import DataFeed

logger = logging.getLogger(__name__)


class ReplayFeed:
    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1m",
                 start_time: Optional[int] = None, end_time: Optional[int] = None,
                 speed: float = 0.0):
        self.symbol = symbol.upper()
        self.interval = interval
        self.start_time = start_time
        self.end_time = end_time
        self.speed = speed
        self._callbacks: list[Callable] = []

    def on_candle(self, callback: Callable):
        self._callbacks.append(callback)

    def _notify(self, candle: dict):
        for cb in self._callbacks:
            cb(candle)

    def run(self) -> list[dict]:
        candles = get_candles(
            symbol=self.symbol,
            interval=self.interval,
            limit=100000,
            start_time=self.start_time,
            end_time=self.end_time,
        )
        if not candles:
            feed = DataFeed(symbol=self.symbol, interval=self.interval)
            feed.fetch_historical(limit=500)
            candles = get_candles(
                symbol=self.symbol,
                interval=self.interval,
                limit=100000,
                start_time=self.start_time,
                end_time=self.end_time,
            )
        logger.info("Replaying %d candles for %s %s", len(candles), self.symbol, self.interval)

        for candle in candles:
            self._notify(candle)
            if self.speed > 0:
                time.sleep(self.speed)

        return candles
