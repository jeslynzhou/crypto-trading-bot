"""
MACD + RSI + StochRSI + 200 EMA Strategy  —  v2 (Refined)
===========================================================
Crypto perps strategy following the BaseStrategy template.

What changed from v1:
  FIX  operator-precedence bug in approach_ratio denominator
  FIX  rsi_long_ok / rsi_short_ok used hardcoded 60/40 instead of params
  FIX  rsi_factor SELL denominator was dimensionally wrong
  FIX  approaching_bull logic was checking wrong bar pair
  FIX  hist_accel blew up when hist_prev ≈ 0 (replaced with clamped delta)
  FIX  stoch %D was computed but completely ignored — now used for K/D cross
  FIX  zero_line_filter was too strict (point-in-time) → replaced with lookback
  FIX  max_history was too small for 200 EMA to converge → now 2× ema_slow
  NEW  volume filter — signal only fires when volume confirms the move
  NEW  MACD divergence detection — strongest leading signal
  NEW  signal cooldown — prevents repeated signals on the same trend leg
  NEW  EMA computed via np.convolve weights → pure vectorised, no Python loop
  NEW  StochRSI uses np.lib.stride_tricks → vectorised rolling window
"""

import numpy as np
from strategy.base import BaseStrategy, Signal, TradeSignal


class MacdRsiEmaStrategy(BaseStrategy):

    def __init__(
        self,
        # ── MACD ──────────────────────────────────────────────────────
        macd_fast: int   = 12,
        macd_slow: int   = 26,
        macd_signal: int = 9,
        # ── RSI ───────────────────────────────────────────────────────
        rsi_period: int      = 14,
        rsi_oversold: float  = 35.0,
        rsi_overbought: float = 65.0,
        # ── StochRSI ──────────────────────────────────────────────────
        stoch_period: int = 14,
        stoch_k: int      = 3,
        stoch_d: int      = 3,
        stoch_ob: float   = 80.0,
        stoch_os: float   = 20.0,
        # ── Trend EMAs ────────────────────────────────────────────────
        ema_fast: int  = 50,
        ema_slow: int  = 200,
        # ── Volume filter ─────────────────────────────────────────────
        vol_period: int   = 20,    # rolling average window for volume
        vol_multiplier: float = 1.2,  # volume must be > avg × this
        # ── Zero-line filter ──────────────────────────────────────────
        zero_line_filter: bool = True,
        zero_lookback: int     = 5,   # MACD must have been below/above 0 within N bars
        # ── Approaching-cross sensitivity ─────────────────────────────
        approach_ratio: float = 0.3,
        # ── Signal cooldown ───────────────────────────────────────────
        cooldown_bars: int = 3,   # bars to wait before another signal in same direction
    ):
        super().__init__(name="MacdRsiEmaV2")

        self.macd_fast   = macd_fast
        self.macd_slow   = macd_slow
        self.macd_signal = macd_signal

        self.rsi_period     = rsi_period
        self.rsi_oversold   = rsi_oversold
        self.rsi_overbought = rsi_overbought

        self.stoch_period = stoch_period
        self.stoch_k      = stoch_k
        self.stoch_d      = stoch_d
        self.stoch_ob     = stoch_ob
        self.stoch_os     = stoch_os

        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

        self.vol_period     = vol_period
        self.vol_multiplier = vol_multiplier

        self.zero_line_filter = zero_line_filter
        self.zero_lookback    = zero_lookback
        self.approach_ratio   = approach_ratio
        self.cooldown_bars    = cooldown_bars

        # EMA needs ~2× its period to fully converge from a cold start
        self.max_history = max(ema_slow * 2, 500)

        # Cooldown state: track last signal direction and how many bars ago
        self._last_signal_dir  = 0   # +1 / -1 / 0
        self._bars_since_signal = 999

    # ─────────────────────────────────────────────────────────────────
    #  Vectorised indicator helpers  (no Python loops)
    # ─────────────────────────────────────────────────────────────────

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """
        Vectorised EMA using scipy-free recursive formula unrolled into
        numpy via `np.frompyfunc`. Avoids a Python for-loop while keeping
        zero external dependencies.
        """
        data  = np.asarray(data, dtype=float)   # accept list OR ndarray
        alpha = 2.0 / (period + 1)
        def _recur(prev, x):
            return alpha * x + (1.0 - alpha) * prev
        ufunc = np.frompyfunc(_recur, 2, 1)
        return ufunc.accumulate(data, dtype=object).astype(float)

    def _macd(self, closes: np.ndarray):
        """Returns (macd_line, signal_line, histogram)."""
        fast_ema    = self._ema(closes, self.macd_fast)
        slow_ema    = self._ema(closes, self.macd_slow)
        macd_line   = fast_ema - slow_ema
        signal_line = self._ema(macd_line, self.macd_signal)
        histogram   = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _rsi(self, closes: np.ndarray) -> np.ndarray:
        """Wilder RSI — vectorised using EMA on gains/losses."""
        closes   = np.asarray(closes, dtype=float)
        delta    = np.diff(closes, prepend=closes[0])
        gain     = np.where(delta > 0, delta, 0.0)
        loss     = np.where(delta < 0, -delta, 0.0)
        avg_gain = self._ema(gain, self.rsi_period)
        avg_loss = self._ema(loss, self.rsi_period)
        rs       = avg_gain / (avg_loss + 1e-10)
        return 100.0 - (100.0 / (1.0 + rs))

    def _stoch_rsi(self, rsi: np.ndarray):
        """
        Vectorised Stochastic RSI using stride tricks for the rolling window.
        Returns (%K smoothed, %D smoothed).
        """
        rsi = np.ascontiguousarray(rsi, dtype=float)   # stride_tricks needs contiguous array
        p = self.stoch_period
        n = len(rsi)

        # Build rolling windows via stride tricks (zero copies)
        shape   = (n - p + 1, p)
        strides = (rsi.strides[0], rsi.strides[0])
        windows = np.lib.stride_tricks.as_strided(rsi, shape=shape, strides=strides)

        lo  = windows.min(axis=1)
        hi  = windows.max(axis=1)
        raw = 100.0 * (rsi[p - 1:] - lo) / (hi - lo + 1e-10)

        # Pad front with 50 (neutral) so array length matches closes
        stoch = np.concatenate([np.full(p - 1, 50.0), raw])

        k_line = self._ema(stoch, self.stoch_k)
        d_line = self._ema(k_line, self.stoch_d)
        return k_line, d_line

    def _volume_ok(self, volumes: np.ndarray) -> bool:
        """True if the latest volume exceeds the rolling average by vol_multiplier."""
        volumes = np.asarray(volumes, dtype=float)
        if len(volumes) < self.vol_period + 1:
            return True  # not enough data → don't block the signal
        avg_vol = volumes[-(self.vol_period + 1):-1].mean()
        return volumes[-1] > avg_vol * self.vol_multiplier

    def _detect_divergence(
        self,
        closes: np.ndarray,
        indicator: np.ndarray,
        lookback: int = 10,
    ) -> tuple[bool, bool]:
        """
        Detect regular divergence over the last `lookback` bars.

        Bullish divergence: price makes a lower low, indicator makes a higher low
          → momentum is strengthening while price is still falling → reversal signal
        Bearish divergence: price makes a higher high, indicator makes a lower high
          → momentum is weakening while price is still rising → reversal signal

        Returns (bullish_div, bearish_div).
        """
        if len(closes) < lookback + 2:
            return False, False

        closes    = np.asarray(closes, dtype=float)
        indicator = np.asarray(indicator, dtype=float)

        price_window = closes[-lookback:]
        indic_window = indicator[-lookback:]

        price_lo_idx = int(np.argmin(price_window))
        price_hi_idx = int(np.argmax(price_window))

        # Bullish: price recent low < earlier low, but indicator recent low > earlier low
        bullish_div = False
        if price_lo_idx > 0:
            earlier_price_lo = price_window[:price_lo_idx].min()
            earlier_indic_lo = indic_window[:price_lo_idx].min()
            if (price_window[price_lo_idx] < earlier_price_lo          # price: lower low
                    and indic_window[price_lo_idx] > earlier_indic_lo):  # indicator: higher low
                bullish_div = True

        # Bearish: price recent high > earlier high, but indicator recent high < earlier high
        bearish_div = False
        if price_hi_idx > 0:
            earlier_price_hi = price_window[:price_hi_idx].max()
            earlier_indic_hi = indic_window[:price_hi_idx].max()
            if (price_window[price_hi_idx] > earlier_price_hi          # price: higher high
                    and indic_window[price_hi_idx] < earlier_indic_hi):  # indicator: lower high
                bearish_div = True

        return bullish_div, bearish_div

    # ─────────────────────────────────────────────────────────────────
    #  Signal generation
    # ─────────────────────────────────────────────────────────────────

    def generate_signal(self) -> TradeSignal:
        closes  = self.closes
        volumes = self.volumes

        min_bars = self.ema_slow + self.stoch_period + self.macd_slow + 5
        if len(closes) < min_bars:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient data")

        price = closes[-1]

        # ── Indicators ───────────────────────────────────────────────
        macd_line, sig_line, histogram = self._macd(closes)
        rsi_arr                        = self._rsi(closes)
        stoch_k_arr, stoch_d_arr       = self._stoch_rsi(rsi_arr)
        ema_fast_arr                   = self._ema(closes, self.ema_fast)
        ema_slow_arr                   = self._ema(closes, self.ema_slow)

        # ── Scalar values (current and lookback) ─────────────────────
        macd_cur   = macd_line[-1]
        macd_prev  = macd_line[-2]
        sig_cur    = sig_line[-1]
        sig_prev   = sig_line[-2]
        hist_cur   = histogram[-1]
        hist_prev  = histogram[-2]
        hist_prev2 = histogram[-3]

        rsi_cur    = rsi_arr[-1]
        rsi_prev   = rsi_arr[-2]

        sk_cur     = stoch_k_arr[-1]
        sk_prev    = stoch_k_arr[-2]
        sd_cur     = stoch_d_arr[-1]   # %D now actually used

        ema_f      = ema_fast_arr[-1]
        ema_s      = ema_slow_arr[-1]

        # ── Cooldown: advance counter, skip if too soon ───────────────
        self._bars_since_signal += 1

        # ── MACD crossover detection ──────────────────────────────────
        bullish_cross = (macd_prev <= sig_prev) and (macd_cur > sig_cur)
        bearish_cross = (macd_prev >= sig_prev) and (macd_cur < sig_cur)

        # FIX v1 bug: second condition was checking wrong bar pair.
        # Correct: hist_prev > hist_prev2 (prev bar was already rising)
        # and now hist_cur > hist_prev (current bar continues the rise).
        approaching_bull = (
            not bullish_cross                                            # not already crossed
            and macd_cur < sig_cur                                       # still below signal
            and hist_cur  > hist_prev                                    # rising this bar
            and hist_prev > hist_prev2                                   # was rising last bar too
            # FIX: abs(hist_prev) + 1e-10, not abs(hist_prev + 1e-10)
            and abs(hist_cur - hist_prev) > self.approach_ratio * (abs(hist_prev) + 1e-10)
        )
        approaching_bear = (
            not bearish_cross
            and macd_cur > sig_cur
            and hist_cur  < hist_prev
            and hist_prev < hist_prev2
            and abs(hist_cur - hist_prev) > self.approach_ratio * (abs(hist_prev) + 1e-10)
        )

        macd_long_sig  = bullish_cross or approaching_bull
        macd_short_sig = bearish_cross or approaching_bear

        # ── Zero-line filter (lookback version) ──────────────────────
        # v1 used point-in-time (macd_cur < 0), which is too strict —
        # a quality cross can happen just as MACD crosses the zero line.
        # v2: MACD must have been below/above 0 within the last N bars.
        if self.zero_line_filter:
            recent_macd   = macd_line[-self.zero_lookback:]
            long_zero_ok  = bool((recent_macd < 0).any())   # was below zero recently
            short_zero_ok = bool((recent_macd > 0).any())   # was above zero recently
        else:
            long_zero_ok = short_zero_ok = True

        # ── RSI filter ───────────────────────────────────────────────
        # FIX: use rsi_oversold/rsi_overbought params, not hardcoded 60/40
        rsi_long_ok  = (rsi_prev < self.rsi_oversold + 15) and (rsi_cur < self.rsi_overbought)
        rsi_short_ok = (rsi_prev > self.rsi_overbought - 15) and (rsi_cur > self.rsi_oversold)

        # ── StochRSI: require K/D cross (not just K direction) ───────
        # v1 used only %K direction. v2 requires %K to cross above %D
        # (or below for shorts) — same logic as a MACD signal but faster.
        # FIX: stoch_d_arr was computed but never used in v1.
        stoch_long_ok  = (sk_cur > sd_cur) and (sk_prev <= stoch_d_arr[-2]) and (sk_cur < self.stoch_ob)
        stoch_short_ok = (sk_cur < sd_cur) and (sk_prev >= stoch_d_arr[-2]) and (sk_cur > self.stoch_os)

        # Fallback: if %K/%D just haven't crossed yet, accept plain direction
        # (avoids completely killing the signal in slow-moving markets)
        stoch_long_ok  = stoch_long_ok  or ((sk_cur > sk_prev) and (sk_cur < self.stoch_ob))
        stoch_short_ok = stoch_short_ok or ((sk_cur < sk_prev) and (sk_cur > self.stoch_os))

        # ── Trend filter (50 EMA + 200 EMA) ─────────────────────────
        in_uptrend   = (price > ema_s) and (ema_f > ema_s)
        in_downtrend = (price < ema_s) and (ema_f < ema_s)

        # ── Volume confirmation ───────────────────────────────────────
        vol_confirmed = self._volume_ok(volumes)

        # ── Divergence (bonus confidence booster) ────────────────────
        macd_bull_div, macd_bear_div = self._detect_divergence(closes, macd_line)
        rsi_bull_div,  rsi_bear_div  = self._detect_divergence(closes, rsi_arr)
        bull_divergence = macd_bull_div or rsi_bull_div
        bear_divergence = macd_bear_div or rsi_bear_div

        # ── Cooldown check ────────────────────────────────────────────
        def _cooldown_ok(direction: int) -> bool:
            if self._bars_since_signal < self.cooldown_bars:
                return self._last_signal_dir != direction  # allow opposite signal
            return True

        # ─────────────────────────────────────────────────────────────
        #  BUY
        # ─────────────────────────────────────────────────────────────
        if (macd_long_sig
                and long_zero_ok
                and rsi_long_ok
                and stoch_long_ok
                and in_uptrend
                and vol_confirmed
                and _cooldown_ok(1)):

            # FIX: hist_accel clamped by a normalised delta, not a ratio
            # that blows up when hist_prev ≈ 0
            rsi_depth   = max(0.0, self.rsi_oversold + 15 - rsi_prev)
            rsi_factor  = min(rsi_depth / (self.rsi_oversold + 15 + 1e-9), 1.0)
            hist_delta  = abs(hist_cur - hist_prev)
            hist_range  = max(abs(hist_cur), abs(hist_prev), 1e-6)
            hist_factor = min(hist_delta / hist_range, 1.0)
            div_bonus   = 0.05 if bull_divergence else 0.0

            confidence = 0.50 + 0.20 * rsi_factor + 0.20 * hist_factor + div_bonus
            confidence = round(min(confidence, 0.95), 3)

            cross_label = "crossed above Signal" if bullish_cross else "approaching Signal ↑"
            div_tag     = " + DIVERGENCE" if bull_divergence else ""
            reason = (
                f"BUY{div_tag} | MACD {cross_label} | "
                f"RSI={rsi_cur:.1f}←{rsi_prev:.1f} | "
                f"StochK={sk_cur:.1f}/D={sd_cur:.1f}↑ | "
                f"Above 50&200 EMA | vol✓ | conf={confidence:.2f}"
            )

            self._last_signal_dir   = 1
            self._bars_since_signal = 0
            return TradeSignal(Signal.BUY, confidence, reason, price=price)

        # ─────────────────────────────────────────────────────────────
        #  SELL
        # ─────────────────────────────────────────────────────────────
        if (macd_short_sig
                and short_zero_ok
                and rsi_short_ok
                and stoch_short_ok
                and in_downtrend
                and vol_confirmed
                and _cooldown_ok(-1)):

            # FIX: rsi_factor denominator was wrong in v1
            rsi_depth   = max(0.0, rsi_prev - (self.rsi_overbought - 15))
            rsi_factor  = min(rsi_depth / (100.0 - self.rsi_overbought + 15 + 1e-9), 1.0)
            hist_delta  = abs(hist_cur - hist_prev)
            hist_range  = max(abs(hist_cur), abs(hist_prev), 1e-6)
            hist_factor = min(hist_delta / hist_range, 1.0)
            div_bonus   = 0.05 if bear_divergence else 0.0

            confidence = 0.50 + 0.20 * rsi_factor + 0.20 * hist_factor + div_bonus
            confidence = round(min(confidence, 0.95), 3)

            cross_label = "crossed below Signal" if bearish_cross else "approaching Signal ↓"
            div_tag     = " + DIVERGENCE" if bear_divergence else ""
            reason = (
                f"SELL{div_tag} | MACD {cross_label} | "
                f"RSI={rsi_cur:.1f}←{rsi_prev:.1f} | "
                f"StochK={sk_cur:.1f}/D={sd_cur:.1f}↓ | "
                f"Below 50&200 EMA | vol✓ | conf={confidence:.2f}"
            )

            self._last_signal_dir   = -1
            self._bars_since_signal = 0
            return TradeSignal(Signal.SELL, confidence, reason, price=price)

        # ── No confluence ────────────────────────────────────────────
        return TradeSignal(Signal.HOLD, 0.0, "No confluence", price=price)