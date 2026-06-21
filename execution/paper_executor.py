import logging
from datetime import datetime, timezone
from typing import Optional

from config import TRADING_FEE_RATE, INITIAL_CAPITAL
from data.storage import insert_trade, get_portfolio_value

logger = logging.getLogger(__name__)


class PaperExecutor:
    def __init__(self, symbol: str = "BTC", leverage: int = 1, user_id: str = "default"):
        self.symbol = symbol
        self.leverage = leverage
        self.fee_rate = TRADING_FEE_RATE
        self.user_id = user_id

    def get_balance(self, asset: str = "USDC") -> float:
        return get_portfolio_value(initial_capital=INITIAL_CAPITAL, user_id=self.user_id)

    def place_order(self, side: str, quantity: float, strategy_name: str,
                    reason: str, order_type: str = "MARKET",
                    price: Optional[float] = None,
                    pnl: float = 0.0) -> Optional[dict]:
        if quantity <= 0:
            return None

        fee = (price or 0) * quantity * self.fee_rate

        trade_record = {
            "user_id": self.user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": self.symbol,
            "side": side.upper(),
            "price": price or 0,
            "quantity": quantity,
            "strategy_name": strategy_name,
            "reason": reason,
            "order_id": "",
            "status": "FILLED",
            "pnl": pnl,
            "fee": fee,
            "leverage": self.leverage,
        }
        insert_trade(trade_record)
        logger.info("[PAPER] Order: %s %s %.6f @ %.2f [fee=%.4f, lev=%dx] (%s: %s)",
                    side, self.symbol, quantity, price or 0, fee,
                    self.leverage, strategy_name, reason)
        return {"status": "FILLED", "symbol": self.symbol}
