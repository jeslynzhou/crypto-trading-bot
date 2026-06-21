"""
Custom Strategy Template
========================
Copy this template and modify it to create your own strategy.

Rules:
  - Your class must inherit from BaseStrategy
  - You must implement generate_signal() -> TradeSignal
  - The class name must end with "Strategy"
  - Use self.closes, self.highs, self.lows, self.volumes for price data
  - Use self._candles for raw candle dicts (keys: open, high, low, close, volume)
  - Return TradeSignal(Signal.BUY/SELL/HOLD, confidence, reason, price)
  - confidence is 0.0 to 1.0 — signals below 0.3 are ignored
"""

import numpy as np
from strategy.base import BaseStrategy, Signal, TradeSignal


class MyCustomStrategy(BaseStrategy):
    def __init__(self, fast_period: int = 10, slow_period: int = 30):
        super().__init__(name="MyCustom")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.max_history = 200

    def generate_signal(self) -> TradeSignal:
        closes = self.closes
        if len(closes) < self.slow_period + 1:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient data")

        price = closes[-1]

        fast_avg = np.mean(closes[-self.fast_period:])
        slow_avg = np.mean(closes[-self.slow_period:])

        # Example: buy when fast crosses above slow
        fast_prev = np.mean(closes[-(self.fast_period + 1):-1])
        slow_prev = np.mean(closes[-(self.slow_period + 1):-1])

        if fast_prev <= slow_prev and fast_avg > slow_avg:
            return TradeSignal(Signal.BUY, 0.7, "Fast crossed above slow", price=price)

        if fast_prev >= slow_prev and fast_avg < slow_avg:
            return TradeSignal(Signal.SELL, 0.7, "Fast crossed below slow", price=price)

        return TradeSignal(Signal.HOLD, 0.0, "No signal", price=price)
