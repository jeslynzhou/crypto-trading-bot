import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import math

import requests

from config import TRADING_FEE_RATE
from data.storage import insert_trade

logger = logging.getLogger(__name__)

TESTNET_BASE_URL = "https://testnet.binance.vision/api/v3"


class BinanceTestnetExecutor:
    def __init__(self, api_key: str, secret_key: str, symbol: str = "BTCUSDT",
                 leverage: int = 1, user_id: str = "default"):
        self.api_key = api_key
        self.secret_key = secret_key
        self.symbol = symbol
        self.leverage = leverage
        self.fee_rate = TRADING_FEE_RATE
        self.user_id = user_id
        self._session = requests.Session()
        self._session.headers.update({"X-MBX-APIKEY": self.api_key})
        self._step_size = self._fetch_step_size()

    def _fetch_step_size(self) -> float:
        try:
            resp = self._session.get(
                f"{TESTNET_BASE_URL}/exchangeInfo",
                params={"symbol": self.symbol}, timeout=10)
            resp.raise_for_status()
            for f in resp.json()["symbols"][0]["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    return float(f["stepSize"])
        except Exception as e:
            logger.warning("Failed to fetch step size for %s: %s", self.symbol, e)
        return 0.00001

    def _round_qty(self, qty: float) -> float:
        precision = max(0, round(-math.log10(self._step_size)))
        return math.floor(qty * 10**precision) / 10**precision

    def _sign(self, params: dict) -> str:
        query_string = urlencode(params)
        signature = hmac.new(
            self.secret_key.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()
        return signature

    def _request(self, method: str, endpoint: str, params: dict, signed: bool = True) -> dict:
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            params["signature"] = self._sign(params)

        url = f"{TESTNET_BASE_URL}/{endpoint}"
        resp = self._session.request(method, url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_account(self) -> dict:
        return self._request("GET", "account", {})

    def get_balance(self, asset: str = "USDT") -> float:
        account = self.get_account()
        for b in account.get("balances", []):
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def place_order(self, side: str, quantity: float, strategy_name: str,
                    reason: str, order_type: str = "MARKET",
                    price: Optional[float] = None,
                    pnl: float = 0.0) -> Optional[dict]:
        quantity = self._round_qty(quantity)
        if quantity <= 0:
            logger.warning("Quantity too small after rounding for %s", self.symbol)
            return None
        precision = max(0, round(-math.log10(self._step_size)))
        params = {
            "symbol": self.symbol,
            "side": side.upper(),
            "type": order_type,
            "quantity": f"{quantity:.{precision}f}",
        }

        if order_type == "LIMIT" and price is not None:
            params["price"] = f"{price:.2f}"
            params["timeInForce"] = "GTC"

        try:
            result = self._request("POST", "order", params)
            fill_price = self._extract_fill_price(result)
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
                "order_id": str(result.get("orderId", "")),
                "status": result.get("status", "UNKNOWN"),
                "pnl": pnl,
                "fee": fee,
                "leverage": self.leverage,
            }
            insert_trade(trade_record)
            logger.info("Order placed: %s %s %.6f @ %.2f [fee=%.4f, lev=%dx] (%s: %s)",
                        side, self.symbol, quantity, fill_price, fee,
                        self.leverage, strategy_name, reason)
            return result

        except requests.exceptions.HTTPError as e:
            logger.error("Order failed: %s", e.response.text if e.response is not None else str(e))
            return None

    def _extract_fill_price(self, order_result: dict) -> float:
        fills = order_result.get("fills", [])
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            total_cost = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            return total_cost / total_qty if total_qty > 0 else 0.0
        return float(order_result.get("price", 0))

    def get_open_orders(self) -> list[dict]:
        return self._request("GET", "openOrders", {"symbol": self.symbol})

    def cancel_order(self, order_id: int) -> dict:
        return self._request("DELETE", "order", {
            "symbol": self.symbol,
            "orderId": order_id,
        })

    def get_ticker_price(self) -> float:
        result = self._request("GET", "ticker/price", {"symbol": self.symbol}, signed=False)
        return float(result["price"])
