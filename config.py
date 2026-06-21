SYMBOLS = [
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "DOT",
    "NEAR",
    "SUI",
    "ARB",
    "LTC",
    "INJ",
    "RENDER",
    "LINK",
    "HYPE",
]

TRADING_FEE_RATE = 0.00035
HYPERLIQUID_FEE_RATE = 0.00035

LEVERAGE_OPTIONS = [1, 2, 3, 5, 10, 20]
DEFAULT_LEVERAGE = 1

STRATEGIES = {
    "MACD": {
        "description": "Moving Average Convergence Divergence (12/26/9)",
        "params": {"fast": 12, "slow": 26, "signal_period": 9},
    },
    "RSI": {
        "description": "Relative Strength Index — overbought/oversold",
        "params": {"period": 14, "overbought": 70.0, "oversold": 30.0},
    },
    "BollingerBands": {
        "description": "Bollinger Bands mean reversion on band touches",
        "params": {"period": 20, "num_std": 2.0},
    },
    "Supertrend": {
        "description": "ATR-based trend following with dynamic support/resistance",
        "params": {"period": 10, "multiplier": 3.0},
    },
}

STRATEGY_NAMES = list(STRATEGIES.keys())

INITIAL_CAPITAL = 1000.0
