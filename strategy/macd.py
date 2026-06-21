import numpy as np

from strategy.base import BaseStrategy, Signal, TradeSignal


def ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    result = []
    multiplier = 2.0 / (period + 1)
    sma = np.mean(values[:period])
    result.append(sma)
    for price in values[period:]:
        result.append((price - result[-1]) * multiplier + result[-1])
    return result


class MACDStrategy(BaseStrategy):
    def __init__(self, fast: int = 12, slow: int = 26, signal_period: int = 9):
        super().__init__(name="MACD")
        self.fast = fast
        self.slow = slow
        self.signal_period = signal_period
        self.max_history = 200

    def generate_signal(self) -> TradeSignal:
        closes = self.closes
        if len(closes) < self.slow + self.signal_period:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient data")

        fast_ema = ema(closes, self.fast)
        slow_ema = ema(closes, self.slow)

        min_len = min(len(fast_ema), len(slow_ema))
        fast_ema = fast_ema[-min_len:]
        slow_ema = slow_ema[-min_len:]

        macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
        if len(macd_line) < self.signal_period + 1:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient MACD data")

        signal_line = ema(macd_line, self.signal_period)
        if len(signal_line) < 2:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient signal line data")

        macd_current = macd_line[-1]
        macd_prev = macd_line[-2]
        sig_current = signal_line[-1]
        sig_prev = signal_line[-2]

        histogram = macd_current - sig_current
        confidence = min(abs(histogram) / (abs(sig_current) + 1e-10), 1.0)

        bullish = macd_prev <= sig_prev and macd_current > sig_current
        bearish = macd_prev >= sig_prev and macd_current < sig_current

        if bullish:
            return TradeSignal(Signal.BUY, confidence,
                               f"MACD bullish crossover (hist={histogram:.4f})",
                               price=closes[-1])
        if bearish:
            return TradeSignal(Signal.SELL, confidence,
                               f"MACD bearish crossover (hist={histogram:.4f})",
                               price=closes[-1])

        return TradeSignal(Signal.HOLD, 0.0, "No MACD crossover", price=closes[-1])
