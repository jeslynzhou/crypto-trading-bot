import logging
from typing import Optional

from config import TRADING_FEE_RATE, INITIAL_CAPITAL
from data.replay import ReplayFeed
from data.storage import init_db
from strategy.base import BaseStrategy, Signal
from execution.risk import calculate_position_size, check_stop_loss, check_daily_drawdown

logger = logging.getLogger(__name__)


class BacktestResult:
    def __init__(self):
        self.trades: list[dict] = []
        self.equity_curve: list[float] = []
        self.initial_capital: float = INITIAL_CAPITAL
        self.final_capital: float = INITIAL_CAPITAL
        self.total_fees: float = 0.0

    @property
    def total_return(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return (self.final_capital - self.initial_capital) / self.initial_capital

    @property
    def num_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.get("pnl", 0) > 0)
        return wins / len(self.trades)

    @property
    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for value in self.equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def sharpe_ratio(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        import numpy as np
        returns = np.diff(self.equity_curve) / self.equity_curve[:-1]
        if np.std(returns) == 0:
            return 0.0
        return float(np.mean(returns) / np.std(returns) * np.sqrt(252))

    def summary(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "final_capital": self.final_capital,
            "total_return": self.total_return,
            "num_trades": self.num_trades,
            "win_rate": self.win_rate,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "total_fees": self.total_fees,
            "net_return": self.total_return,
        }


class Backtester:
    def __init__(self, strategy: BaseStrategy, symbol: str = "BTCUSDT",
                 interval: str = "1m", initial_capital: float = INITIAL_CAPITAL,
                 fee_rate: float = TRADING_FEE_RATE, leverage: int = 1):
        self.strategy = strategy
        self.symbol = symbol
        self.interval = interval
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.leverage = leverage

    def load_data(self, start_time: Optional[int] = None,
                  end_time: Optional[int] = None) -> list[dict]:
        feed = ReplayFeed(
            symbol=self.symbol,
            interval=self.interval,
            start_time=start_time,
            end_time=end_time,
        )
        candles = []
        feed.on_candle(lambda c: candles.append(c))
        feed.run()
        return candles

    def _calc_pnl(self, position: dict, price: float) -> float:
        if position["side"] == "LONG":
            return (price - position["entry_price"]) * position["quantity"]
        return (position["entry_price"] - price) * position["quantity"]

    def _close_position(self, position: dict, price: float, reason: str,
                        result: BacktestResult, total_fees: float, capital: float):
        pnl = self._calc_pnl(position, price)
        fee = price * position["quantity"] * self.fee_rate
        total_fees += fee
        capital += pnl - fee
        close_side = "SELL" if position["side"] == "LONG" else "BUY"
        result.trades.append({
            "side": close_side, "price": price,
            "quantity": position["quantity"], "pnl": pnl,
            "fee": fee, "reason": reason,
        })
        return capital, total_fees

    def _open_position(self, side: str, price: float, capital: float,
                       reason: str, result: BacktestResult, total_fees: float):
        stop_price = price * (0.98 if side == "LONG" else 1.02)
        qty = calculate_position_size(capital, price, stop_price)
        qty *= self.leverage
        if qty > 0 and qty * price <= capital * self.leverage:
            fee = price * qty * self.fee_rate
            total_fees += fee
            capital -= fee
            position = {"entry_price": price, "quantity": qty, "side": side}
            open_side = "BUY" if side == "LONG" else "SELL"
            result.trades.append({
                "side": open_side, "price": price,
                "quantity": qty, "pnl": 0.0,
                "fee": fee, "reason": reason,
            })
            return position, capital, total_fees
        return None, capital, total_fees

    def run(self, start_time: Optional[int] = None,
            end_time: Optional[int] = None) -> BacktestResult:
        init_db()
        result = BacktestResult()
        result.initial_capital = self.initial_capital
        candles = self.load_data(start_time, end_time)

        if not candles:
            logger.warning("No candle data available for backtest")
            return result

        capital = self.initial_capital
        day_start_capital = capital
        position = None
        highest_since_entry = 0.0
        lowest_since_entry = float('inf')
        total_fees = 0.0
        self.strategy.reset()

        for candle in candles:
            self.strategy.on_candle(candle)
            price = candle["close"]

            if check_daily_drawdown(day_start_capital, capital):
                if position:
                    capital, total_fees = self._close_position(
                        position, price, "daily drawdown halt", result, total_fees, capital)
                    position = None
                result.equity_curve.append(capital)
                continue

            if position:
                if position["side"] == "LONG":
                    highest_since_entry = max(highest_since_entry, price)
                    stop_hit = check_stop_loss(
                        position["entry_price"], price, trailing=True,
                        highest_since_entry=highest_since_entry, side="LONG")
                else:
                    lowest_since_entry = min(lowest_since_entry, price)
                    stop_hit = check_stop_loss(
                        position["entry_price"], price, trailing=True,
                        lowest_since_entry=lowest_since_entry, side="SHORT")
                if stop_hit:
                    capital, total_fees = self._close_position(
                        position, price, "stop-loss triggered", result, total_fees, capital)
                    position = None

            sig = self.strategy.generate_signal()

            if sig.signal == Signal.BUY and position is None:
                position, capital, total_fees = self._open_position(
                    "LONG", price, capital, sig.reason, result, total_fees)
                highest_since_entry = price

            elif sig.signal == Signal.SELL and position is not None:
                capital, total_fees = self._close_position(
                    position, price, sig.reason, result, total_fees, capital)
                position = None

            unrealized = 0.0
            if position:
                unrealized = self._calc_pnl(position, price)
            result.equity_curve.append(capital + unrealized)

        if position:
            final_price = candles[-1]["close"]
            capital, total_fees = self._close_position(
                position, final_price, "backtest end — close position",
                result, total_fees, capital)

        result.final_capital = capital
        result.total_fees = total_fees
        if not result.equity_curve:
            result.equity_curve = [capital]
        return result


if __name__ == "__main__":
    from strategy.loader import build_all_strategies

    logging.basicConfig(level=logging.INFO)
    init_db()

    for strat in build_all_strategies():
        bt = Backtester(strat)
        res = bt.run()
        print(f"\n{strat.name} Backtest Results:")
        for k, v in res.summary().items():
            print(f"  {k}: {v}")
