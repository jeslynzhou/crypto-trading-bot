import numpy as np

from strategy.base import BaseStrategy, Signal, TradeSignal


class SupertrendStrategy(BaseStrategy):
    """Supertrend indicator — ATR-based trend following.
    BUY when price crosses above Supertrend (trend flips bullish).
    SELL when price crosses below Supertrend (trend flips bearish).
    """

    def __init__(self, period: int = 10, multiplier: float = 3.0):
        super().__init__(name="Supertrend")
        self.period = period
        self.multiplier = multiplier
        self.max_history = 200

    def _calculate_atr(self) -> float:
        if len(self._candles) < self.period + 1:
            return 0.0
        trs = []
        for i in range(-self.period, 0):
            c = self._candles[i]
            prev_c = self._candles[i - 1]
            tr = max(
                c["high"] - c["low"],
                abs(c["high"] - prev_c["close"]),
                abs(c["low"] - prev_c["close"]),
            )
            trs.append(tr)
        return np.mean(trs)

    def generate_signal(self) -> TradeSignal:
        if len(self._candles) < self.period + 3:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient data")

        closes = self.closes
        price = closes[-1]
        prev_price = closes[-2]
        atr = self._calculate_atr()

        hl2 = (self._candles[-1]["high"] + self._candles[-1]["low"]) / 2.0
        upper_band = hl2 + self.multiplier * atr
        lower_band = hl2 - self.multiplier * atr

        prev_hl2 = (self._candles[-2]["high"] + self._candles[-2]["low"]) / 2.0
        prev_atr = atr
        prev_upper = prev_hl2 + self.multiplier * prev_atr
        prev_lower = prev_hl2 - self.multiplier * prev_atr

        in_uptrend = price > lower_band
        was_in_uptrend = prev_price > prev_lower
        in_downtrend = price < upper_band
        was_in_downtrend = prev_price < prev_upper

        confidence = min(atr / price * 50, 1.0) if price > 0 else 0

        if in_uptrend and not was_in_uptrend:
            return TradeSignal(Signal.BUY, max(confidence, 0.5),
                               f"Supertrend flipped bullish (ATR={atr:.2f})",
                               price=price)

        if not in_uptrend and was_in_uptrend:
            return TradeSignal(Signal.SELL, max(confidence, 0.5),
                               f"Supertrend flipped bearish (ATR={atr:.2f})",
                               price=price)

        return TradeSignal(Signal.HOLD, 0.0, "No Supertrend flip", price=price)
