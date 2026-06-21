import logging
import math
from datetime import datetime, timezone
from typing import Optional

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from data.storage import insert_trade

logger = logging.getLogger(__name__)

HYPERLIQUID_FEE_RATE = 0.00035


class HyperliquidExecutor:
    def __init__(self, private_key: str, symbol: str = "BTCUSDT", leverage: int = 1,
                 user_id: str = "default"):
        self.symbol = symbol
        self.coin = symbol.replace("USDT", "")
        self.leverage = leverage
        self.fee_rate = HYPERLIQUID_FEE_RATE
        self.user_id = user_id

        self._account = eth_account.Account.from_key(private_key)
        self.address = self._account.address
        self._info = Info(constants.MAINNET_API_URL)
        self._exchange = Exchange(self._account, constants.MAINNET_API_URL)

        self._sz_decimals = self._fetch_sz_decimals()
        self._set_leverage()

    def _fetch_sz_decimals(self) -> int:
        try:
            meta = self._info.meta()
            for asset in meta["universe"]:
                if asset["name"] == self.coin:
                    return asset["szDecimals"]
        except Exception as e:
            logger.warning("Failed to fetch szDecimals for %s: %s", self.coin, e)
        return 3

    def _set_leverage(self):
        try:
            self._exchange.update_leverage(self.leverage, self.coin)
            logger.info("[HL] Set leverage to %dx for %s", self.leverage, self.coin)
        except Exception as e:
            logger.warning("Failed to set leverage for %s: %s", self.coin, e)

    def _round_qty(self, qty: float) -> float:
        return math.floor(qty * 10**self._sz_decimals) / 10**self._sz_decimals

    def get_balance(self, asset: str = "USDC") -> float:
        try:
            state = self._info.user_state(self.address)
            return float(state.get("marginSummary", {}).get("accountValue", 0))
        except Exception as e:
            logger.error("Failed to get balance: %s", e)
            return 0.0

    def get_mid_price(self) -> float:
        try:
            mids = self._info.all_mids()
            return float(mids.get(self.coin, 0))
        except Exception:
            return 0.0

    def place_order(self, side: str, quantity: float, strategy_name: str,
                    reason: str, order_type: str = "MARKET",
                    price: Optional[float] = None,
                    pnl: float = 0.0) -> Optional[dict]:
        quantity = self._round_qty(quantity)
        if quantity <= 0:
            logger.warning("Quantity too small after rounding for %s", self.coin)
            return None

        is_buy = side.upper() == "BUY"

        try:
            if price and order_type == "LIMIT":
                result = self._exchange.order(
                    self.coin, is_buy, quantity, price,
                    {"limit": {"tif": "Gtc"}},
                )
            else:
                mid = self.get_mid_price()
                if mid <= 0:
                    logger.error("Cannot get mid price for %s", self.coin)
                    return None
                slippage = 0.01
                px = mid * (1 + slippage) if is_buy else mid * (1 - slippage)
                px = round(px, 6)
                result = self._exchange.order(
                    self.coin, is_buy, quantity, px,
                    {"limit": {"tif": "Ioc"}},
                )

            status = result.get("response", {}).get("data", {}).get("statuses", [{}])
            if status and "error" in status[0]:
                logger.error("[HL] Order error: %s", status[0]["error"])
                return None

            fill_price = self._extract_fill_price(status)
            if fill_price <= 0:
                fill_price = self.get_mid_price()

            fee = fill_price * quantity * self.fee_rate

            trade_record = {
                "user_id": self.user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": self.symbol,
                "side": side.upper(),
                "price": fill_price,
                "quantity": quantity,
                "strategy_name": strategy_name,
                "reason": reason,
                "order_id": str(status[0].get("resting", {}).get("oid", "")) if status else "",
                "status": "FILLED",
                "pnl": pnl,
                "fee": fee,
                "leverage": self.leverage,
            }
            insert_trade(trade_record)
            logger.info("[HL] Order placed: %s %s %.6f @ %.2f [fee=%.4f, lev=%dx] (%s: %s)",
                        side, self.coin, quantity, fill_price, fee,
                        self.leverage, strategy_name, reason)
            return result

        except Exception as e:
            logger.error("[HL] Order failed: %s", e)
            return None

    def _extract_fill_price(self, statuses: list) -> float:
        if not statuses:
            return 0.0
        s = statuses[0]
        if "filled" in s:
            return float(s["filled"].get("avgPx", 0))
        return 0.0
