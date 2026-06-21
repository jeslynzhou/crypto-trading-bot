import asyncio
import logging
import os
import signal
import sys

from dotenv import load_dotenv

from config import SYMBOLS, DEFAULT_LEVERAGE
from data.feed import DataFeed
from data.storage import init_db
from execution.executor import BinanceTestnetExecutor
from strategy.base import Signal
from strategy.loader import build_all_strategies

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

INTERVAL = "1m"
MIN_CONFIDENCE = 0.3


class TradingBot:
    def __init__(self, symbols: list[str] = None):
        api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
        secret_key = os.getenv("BINANCE_TESTNET_SECRET_KEY", "")

        if not api_key or not secret_key:
            logger.error("Missing BINANCE_TESTNET_API_KEY or BINANCE_TESTNET_SECRET_KEY in .env")
            sys.exit(1)

        self.symbols = symbols or SYMBOLS
        self.executors: dict[str, BinanceTestnetExecutor] = {}
        self.strategies: dict[str, list] = {}
        self.feeds: dict[str, DataFeed] = {}
        self.positions: dict[str, dict] = {}
        self._running = True

        for sym in self.symbols:
            self.executors[sym] = BinanceTestnetExecutor(
                api_key, secret_key, symbol=sym, leverage=DEFAULT_LEVERAGE,
            )
            self.strategies[sym] = build_all_strategies()
            self.feeds[sym] = DataFeed(symbol=sym, interval=INTERVAL, mode="live")

    def _on_candle(self, symbol: str, candle: dict):
        for strategy in self.strategies[symbol]:
            strategy.on_candle(candle)
            sig = strategy.generate_signal()

            if sig.signal == Signal.HOLD or sig.confidence < MIN_CONFIDENCE:
                continue

            logger.info("[%s] Signal from %s: %s", symbol, strategy.name, sig)
            self._handle_signal(symbol, sig, strategy.name)

    def _handle_signal(self, symbol: str, sig, strategy_name: str):
        executor = self.executors[symbol]
        pos_key = f"{symbol}_{strategy_name}"

        if sig.signal == Signal.BUY and pos_key not in self.positions:
            balance = executor.get_balance("USDT")
            alloc = balance / len(self.symbols)
            quantity = (alloc * 0.01) / sig.price if sig.price > 0 else 0
            if quantity > 0:
                result = executor.place_order(
                    side="BUY", quantity=quantity,
                    strategy_name=strategy_name, reason=sig.reason,
                )
                if result:
                    self.positions[pos_key] = {
                        "side": "BUY", "entry_price": sig.price,
                        "quantity": quantity, "strategy": strategy_name,
                    }

        elif sig.signal == Signal.SELL and pos_key in self.positions:
            pos = self.positions[pos_key]
            sell_price = sig.price if sig.price > 0 else pos["entry_price"]
            pnl = (sell_price - pos["entry_price"]) * pos["quantity"] * executor.leverage
            result = executor.place_order(
                side="SELL", quantity=pos["quantity"],
                strategy_name=strategy_name, reason=sig.reason,
                pnl=pnl,
            )
            if result:
                del self.positions[pos_key]

    def run(self):
        init_db()
        logger.info("Starting trading bot on %d symbols: %s", len(self.symbols), self.symbols)
        logger.info("Strategies: %s", [s.name for s in self.strategies[self.symbols[0]]])

        for sym in self.symbols:
            feed = self.feeds[sym]
            logger.info("Fetching historical candles for %s warmup...", sym)
            try:
                historical = feed.fetch_historical(limit=500)
                for candle in historical:
                    for strategy in self.strategies[sym]:
                        strategy.on_candle(candle)
                logger.info("[%s] Warmup complete with %d candles", sym, len(historical))
            except Exception as e:
                logger.warning("[%s] Failed to fetch historical data: %s", sym, e)

        for sym in self.symbols:
            self.feeds[sym].on_candle(lambda c, s=sym: self._on_candle(s, c))

        def shutdown(signum, frame):
            logger.info("Shutting down...")
            self._running = False
            for f in self.feeds.values():
                f.stop()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        logger.info("Connecting to live feeds...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        tasks = [self.feeds[sym].start_async() for sym in self.symbols]
        loop.run_until_complete(asyncio.gather(*tasks))


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
