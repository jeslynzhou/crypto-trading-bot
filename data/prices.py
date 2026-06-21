import logging

logger = logging.getLogger(__name__)


def get_all_prices() -> dict[str, float]:
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        info = Info(constants.MAINNET_API_URL)
        mids = info.all_mids()
        return {k: float(v) for k, v in mids.items()}
    except Exception as e:
        logger.error("Failed to fetch prices: %s", e)
        return {}


def get_prices(symbols: list[str]) -> dict[str, float]:
    all_prices = get_all_prices()
    return {s: all_prices.get(s, 0.0) for s in symbols}


def get_24h_stats(symbols: list[str]) -> dict[str, dict]:
    prices = get_prices(symbols)
    result = {}
    for s in symbols:
        price = prices.get(s, 0)
        result[s] = {
            "price": price,
            "change_pct": 0.0,
        }
    return result
