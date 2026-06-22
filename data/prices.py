import logging
import time

logger = logging.getLogger(__name__)


def get_all_prices() -> dict[str, float]:
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        info = Info(constants.MAINNET_API_URL, skip_ws=True)
        mids = info.all_mids()
        return {k: float(v) for k, v in mids.items()}
    except Exception as e:
        logger.error("Failed to fetch prices: %s", e)
        return {}


def get_prices(symbols: list[str]) -> dict[str, float]:
    all_prices = get_all_prices()
    return {s: all_prices.get(s, 0.0) for s in symbols}


def get_24h_stats(symbols: list[str]) -> dict[str, dict]:
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        info = Info(constants.MAINNET_API_URL, skip_ws=True)
        mids = info.all_mids()

        now_ms = int(time.time() * 1000)
        ago_24h = now_ms - 86_400_000

        result = {}
        for s in symbols:
            price = float(mids.get(s, 0))
            change_pct = 0.0
            try:
                candles = info.candles_snapshot(s, "1h", ago_24h, now_ms)
                if candles:
                    open_24h = float(candles[0]["o"])
                    if open_24h > 0:
                        change_pct = ((price - open_24h) / open_24h) * 100
            except Exception:
                pass
            result[s] = {"price": price, "change_pct": change_pct}
        return result
    except Exception as e:
        logger.error("Failed to fetch 24h stats: %s", e)
        return {s: {"price": 0, "change_pct": 0} for s in symbols}
