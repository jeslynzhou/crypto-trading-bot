import numpy as np
from strategy.base import BaseStrategy, Signal, TradeSignal


class MacdRsiStrategy(BaseStrategy):
    """
    MACD + RSI Confluence Strategy
    ================================
    BUY  when:
        - MACD line is about to cross above Signal line (bullish crossover imminent or just happened)
        - RSI is in oversold recovery zone (< 55, was previously below 40) — confirms momentum
        - Price is above the slow EMA (trend filter)

    SELL when:
        - MACD line is about to cross below Signal line (bearish crossover imminent or just happened)
        - RSI is in overbought territory (> 60, was previously above 65) — confirms exhaustion
        - Price is below the slow EMA (trend filter)

    Confidence is scaled by how far RSI is from neutral (50) and the MACD histogram delta.
    """

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        rsi_period: int = 14,
        trend_ema_period: int = 50,
        rsi_oversold: float = 40.0,
        rsi_overbought: float = 60.0,
    ):
        super().__init__(name="MacdRsi")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.rsi_period = rsi_period
        self.trend_ema_period = trend_ema_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.max_history = 300

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Exponential moving average — full series."""
        alpha = 2.0 / (period + 1)
        ema = np.empty_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    def _macd(self, closes: np.ndarray):
        """Returns (macd_line, signal_line, histogram) as full arrays."""
        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        macd_line = fast_ema - slow_ema
        signal_line = self._ema(macd_line, self.signal_period)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _rsi(self, closes: np.ndarray) -> np.ndarray:
        """Wilder's RSI — full series."""
        period = self.rsi_period
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        rsi = np.full(len(closes), np.nan)
        if len(gains) < period:
            return rsi

        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        rs = avg_gain / avg_loss if avg_loss != 0 else np.inf
        current_rsi = 100 - (100 / (1 + rs))
        rsi[len(closes) - 1] = current_rsi

        # Back-fill the last few values we need for prev-candle check
        ag, al = np.mean(gains[:period]), np.mean(losses[:period])
        rsi_series = []
        for i in range(period, len(deltas)):
            ag = (ag * (period - 1) + gains[i]) / period
            al = (al * (period - 1) + losses[i]) / period
            rs_i = ag / al if al != 0 else np.inf
            rsi_series.append(100 - (100 / (1 + rs_i)))

        # Align to closes array
        start = period + 1
        for j, val in enumerate(rsi_series):
            rsi[start + j] = val

        return rsi

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def generate_signal(self) -> TradeSignal:
        closes = self.closes
        min_len = self.slow_period + self.signal_period + self.rsi_period + 5
        if len(closes) < min_len:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient data")

        price = closes[-1]

        # --- Indicators ---
        macd_line, signal_line, histogram = self._macd(closes)
        rsi_series = self._rsi(closes)
        trend_ema = self._ema(closes, self.trend_ema_period)

        # Current and previous bar values
        macd_cur = macd_line[-1]
        macd_prev = macd_line[-2]
        sig_cur = signal_line[-1]
        sig_prev = signal_line[-2]
        hist_cur = histogram[-1]
        hist_prev = histogram[-2]

        rsi_cur = rsi_series[-1]
        rsi_prev = rsi_series[-2]
        trend = trend_ema[-1]

        if np.isnan(rsi_cur) or np.isnan(rsi_prev):
            return TradeSignal(Signal.HOLD, 0.0, "RSI not ready")

        # --- BUY logic ---
        # 1. MACD crossing above signal (just crossed or on the verge)
        macd_bullish_cross = (macd_prev <= sig_prev) and (macd_cur > sig_cur)
        macd_approaching_cross = (
            macd_cur < sig_cur                          # not yet crossed
            and (sig_cur - macd_cur) < abs(hist_prev)  # gap is closing fast
            and hist_cur > hist_prev                    # histogram rising (momentum building)
        )

        # 2. RSI confirms: was oversold, now recovering — not yet overbought
        rsi_buy_confirm = (rsi_prev < self.rsi_oversold + 5) and (rsi_cur < self.rsi_overbought)

        # 3. Trend filter: price above trend EMA
        above_trend = price > trend

        if (macd_bullish_cross or macd_approaching_cross) and rsi_buy_confirm and above_trend:
            # Scale confidence by RSI distance from neutral and histogram momentum
            rsi_factor = max(0.0, (50 - rsi_prev) / 50)          # stronger if RSI was deeper
            hist_delta = abs(hist_cur - hist_prev)
            hist_factor = min(1.0, hist_delta / (abs(hist_prev) + 1e-9))
            confidence = 0.5 + 0.25 * rsi_factor + 0.25 * hist_factor
            confidence = min(0.95, confidence)

            cross_type = "crossed" if macd_bullish_cross else "approaching cross"
            reason = (
                f"MACD {cross_type} above Signal | "
                f"RSI={rsi_cur:.1f} recovering from {rsi_prev:.1f} | "
                f"Price above {self.trend_ema_period}-EMA"
            )
            return TradeSignal(Signal.BUY, confidence, reason, price=price)

        # --- SELL logic ---
        macd_bearish_cross = (macd_prev >= sig_prev) and (macd_cur < sig_cur)
        macd_approaching_bearish = (
            macd_cur > sig_cur                          # not yet crossed below
            and (macd_cur - sig_cur) < abs(hist_prev)  # gap closing
            and hist_cur < hist_prev                    # histogram falling
        )

        rsi_sell_confirm = (rsi_prev > self.rsi_overbought - 5) and (rsi_cur > self.rsi_oversold)

        below_trend = price < trend

        if (macd_bearish_cross or macd_approaching_bearish) and rsi_sell_confirm and below_trend:
            rsi_factor = max(0.0, (rsi_prev - 50) / 50)
            hist_delta = abs(hist_cur - hist_prev)
            hist_factor = min(1.0, hist_delta / (abs(hist_prev) + 1e-9))
            confidence = 0.5 + 0.25 * rsi_factor + 0.25 * hist_factor
            confidence = min(0.95, confidence)

            cross_type = "crossed" if macd_bearish_cross else "approaching cross"
            reason = (
                f"MACD {cross_type} below Signal | "
                f"RSI={rsi_cur:.1f} fading from {rsi_prev:.1f} | "
                f"Price below {self.trend_ema_period}-EMA"
            )
            return TradeSignal(Signal.SELL, confidence, reason, price=price)

        return TradeSignal(Signal.HOLD, 0.0, "No confluence", price=price)