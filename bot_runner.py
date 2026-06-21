"""Bot runner that can be started as a subprocess from the dashboard."""

import asyncio
import json
import logging
import os
import sys
import signal

from dotenv import load_dotenv

load_dotenv()

from data.feed import DataFeed
from data.storage import init_db
from execution.paper_executor import PaperExecutor
from execution.hyperliquid_executor import HyperliquidExecutor
from strategy.base import Signal
from strategy.loader import build_strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.3


class BotRunner:
    def __init__(self, symbols: list[str], strategy_names: list[str],
                 leverage: int = 1, interval: str = "1m",
                 mode: str = "paper", hl_private_key: str = "",
                 user_id: str = "default"):
        self.symbols = symbols
        self.leverage = leverage
        self.interval = interval
        self.mode = mode
        self.user_id = user_id
        self.positions_file = os.path.join(
            os.path.dirname(__file__), f".positions_{user_id}.json")
        self.executors = {}
        self.strategies = {}
        self.feeds = {}
        self.positions = {}

        for sym in symbols:
            if mode == "live" and hl_private_key:
                self.executors[sym] = HyperliquidExecutor(
                    hl_private_key, symbol=sym, leverage=leverage,
                    user_id=user_id,
                )
            else:
                self.executors[sym] = PaperExecutor(
                    symbol=sym, leverage=leverage, user_id=user_id,
                )
            self.strategies[sym] = [build_strategy(name) for name in strategy_names]
            self.feeds[sym] = DataFeed(symbol=sym, interval=interval, mode="live")

    def _on_candle(self, symbol, candle):
        for strategy in self.strategies[symbol]:
            strategy.on_candle(candle)
            sig = strategy.generate_signal()
            if sig.signal == Signal.HOLD or sig.confidence < MIN_CONFIDENCE:
                continue
            logger.info("[%s] %s: %s", symbol, strategy.name, sig)
            self._handle_signal(symbol, sig, strategy.name)

    def _save_positions(self):
        data = {}
        for key, pos in self.positions.items():
            data[key] = {
                "symbol": key.split("_")[0],
                "strategy": "_".join(key.split("_")[1:]),
                "entry_price": pos["entry_price"],
                "quantity": pos["quantity"],
                "side": pos["side"],
            }
        with open(self.positions_file, "w") as f:
            json.dump(data, f)

    def _open_position(self, symbol, sig, strategy_name, side):
        executor = self.executors[symbol]
        pos_key = f"{symbol}_{strategy_name}"
        balance = executor.get_balance()
        alloc = balance / len(self.symbols)
        quantity = (alloc * 0.01 * self.leverage) / sig.price if sig.price > 0 else 0
        if quantity <= 0:
            return
        order_side = "BUY" if side == "LONG" else "SELL"
        result = executor.place_order(
            side=order_side, quantity=quantity,
            strategy_name=strategy_name, reason=sig.reason,
            price=sig.price,
        )
        if result:
            self.positions[pos_key] = {
                "entry_price": sig.price, "quantity": quantity, "side": side,
            }
            self._save_positions()
            logger.info("[%s] Opened %s: qty=%.6f @ %.2f", symbol, side, quantity, sig.price)

    def _close_position(self, symbol, pos, price, strategy_name, reason):
        executor = self.executors[symbol]
        pos_key = f"{symbol}_{strategy_name}"
        if pos["side"] == "LONG":
            pnl = (price - pos["entry_price"]) * pos["quantity"]
            order_side = "SELL"
        else:
            pnl = (pos["entry_price"] - price) * pos["quantity"]
            order_side = "BUY"
        result = executor.place_order(
            side=order_side, quantity=pos["quantity"],
            strategy_name=strategy_name, reason=reason, pnl=pnl,
            price=price,
        )
        if result:
            del self.positions[pos_key]
            self._save_positions()
            logger.info("[%s] Closed %s: pnl=%.4f", symbol, pos["side"], pnl)
            return True
        return False

    def _handle_signal(self, symbol, sig, strategy_name):
        pos_key = f"{symbol}_{strategy_name}"
        pos = self.positions.get(pos_key)
        price = sig.price if sig.price > 0 else (pos["entry_price"] if pos else 0)

        if sig.signal == Signal.BUY and pos is None:
            self._open_position(symbol, sig, strategy_name, "LONG")

        elif sig.signal == Signal.SELL and pos is not None:
            self._close_position(symbol, pos, price, strategy_name, sig.reason)

    def run(self):
        init_db()
        logger.info("Bot starting: user=%s, mode=%s, symbols=%s, leverage=%dx, strategies=%s",
                     self.user_id, self.mode, self.symbols, self.leverage,
                     [s.name for s in self.strategies[self.symbols[0]]])

        for sym in self.symbols:
            try:
                historical = self.feeds[sym].fetch_historical(limit=500)
                for candle in historical:
                    for strategy in self.strategies[sym]:
                        strategy.on_candle(candle)
                logger.info("[%s] Warmup: %d candles", sym, len(historical))
            except Exception as e:
                logger.warning("[%s] Warmup failed: %s", sym, e)

        for sym in self.symbols:
            self.feeds[sym].on_candle(lambda c, s=sym: self._on_candle(s, c))

        def shutdown(signum, frame):
            logger.info("Shutting down...")
            for f in self.feeds.values():
                f._running = False
            loop.stop()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        tasks = [self.feeds[sym].start_async() for sym in self.symbols]
        loop.run_until_complete(asyncio.gather(*tasks))


if __name__ == "__main__":
    config = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    symbols = config.get("symbols", ["BTC"])
    strategies = config.get("strategies", ["MACD", "RSI", "BollingerBands", "Supertrend"])
    leverage = config.get("leverage", 1)
    interval = config.get("interval", "1m")
    mode = config.get("mode", "paper")
    user_id = config.get("user_id", "default")
    hl_private_key = os.environ.get("HL_PRIVATE_KEY", "")

    bot = BotRunner(symbols, strategies, leverage=leverage, interval=interval,
                    mode=mode, hl_private_key=hl_private_key, user_id=user_id)
    bot.run()
