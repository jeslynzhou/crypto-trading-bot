import numpy as np

from strategy.base import BaseStrategy, Signal, TradeSignal


class MeanReversionStrategy(BaseStrategy):
    def __init__(self, period: int = 20, num_std: float = 2.0):
        super().__init__(name="MeanReversionBB")
        self.period = period
        self.num_std = num_std
        self.max_history = 200

    def _bollinger_bands(self, closes: list[float]) -> tuple[float, float, float]:
        window = closes[-self.period:]
        middle = np.mean(window)
        std = np.std(window)
        upper = middle + self.num_std * std
        lower = middle - self.num_std * std
        return lower, middle, upper

    def generate_signal(self) -> TradeSignal:
        closes = self.closes

        if len(closes) < self.period + 1:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient data")

        lower, middle, upper = self._bollinger_bands(closes)
        current_price = closes[-1]
        prev_price = closes[-2]

        band_width = upper - lower
        if band_width == 0:
            return TradeSignal(Signal.HOLD, 0.0, "Zero band width", price=current_price)

        if current_price <= lower and prev_price > lower:
            distance = (lower - current_price) / band_width
            confidence = min(0.5 + distance * 2, 1.0)
            return TradeSignal(
                Signal.BUY, confidence,
                f"Price touched lower BB ({lower:.2f})",
                price=current_price,
            )

        if current_price >= upper and prev_price < upper:
            distance = (current_price - upper) / band_width
            confidence = min(0.5 + distance * 2, 1.0)
            return TradeSignal(
                Signal.SELL, confidence,
                f"Price touched upper BB ({upper:.2f})",
                price=current_price,
            )

        position_in_band = (current_price - lower) / band_width
        if 0.4 <= position_in_band <= 0.6:
            return TradeSignal(Signal.HOLD, 0.0, "Price near middle band", price=current_price)

        return TradeSignal(Signal.HOLD, 0.0, "Price within bands", price=current_price)
