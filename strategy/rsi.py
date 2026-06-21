import numpy as np

from strategy.base import BaseStrategy, Signal, TradeSignal


class RSIStrategy(BaseStrategy):
    def __init__(self, period: int = 14, overbought: float = 70.0, oversold: float = 30.0):
        super().__init__(name="RSI")
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
        self.max_history = 200

    def _calculate_rsi(self, closes: list[float]) -> float:
        deltas = np.diff(closes[-(self.period + 1):])
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def generate_signal(self) -> TradeSignal:
        closes = self.closes
        if len(closes) < self.period + 2:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient data")

        rsi = self._calculate_rsi(closes)
        prev_closes = closes[:-1]
        prev_rsi = self._calculate_rsi(prev_closes)
        price = closes[-1]

        if rsi <= self.oversold and prev_rsi > self.oversold:
            confidence = min((self.oversold - rsi) / self.oversold, 1.0)
            return TradeSignal(Signal.BUY, max(confidence, 0.5),
                               f"RSI oversold ({rsi:.1f})", price=price)

        if rsi >= self.overbought and prev_rsi < self.overbought:
            confidence = min((rsi - self.overbought) / (100 - self.overbought), 1.0)
            return TradeSignal(Signal.SELL, max(confidence, 0.5),
                               f"RSI overbought ({rsi:.1f})", price=price)

        return TradeSignal(Signal.HOLD, 0.0, f"RSI neutral ({rsi:.1f})", price=price)
