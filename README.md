# Crypto Trading Bot

A cryptocurrency trading bot with a Streamlit dashboard for live trading, backtesting, and custom strategy development. Connects to the Binance Testnet for paper trading.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get Binance Testnet API keys

1. Go to https://testnet.binance.vision/
2. Log in with your GitHub account
3. Click **Generate HMAC_SHA256 Key**
4. Copy the API Key and Secret Key

### 3. Configure `.env`

Create a `.env` file in the project root:

```
BINANCE_TESTNET_API_KEY=your_api_key_here
BINANCE_TESTNET_SECRET_KEY=your_secret_key_here
```

### 4. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

The dashboard opens at `http://localhost:8501`.

---

## Dashboard Tabs

### Markets

Browse live prices for all 18 supported coins.

- **Symbol / Timeframe** — pick any coin and candle interval (1m, 5m, 15m, 1h)
- **Chart Type** — toggle between Candlestick and Line view
- **Show Volume** — toggle volume bars on/off
- **Range buttons** (30m, 1h, 4h, 12h, All) — quick-zoom to different time windows
- **Scroll zoom** — mouse wheel zooms in/out; click-drag to select a region

### Trading

Control the live bot and monitor performance.

**Bot Controls:**

1. Select a **Coin**, one or more **Strategies**, a **Leverage** level, and an **Interval**
2. Click **Start Bot** to begin trading
3. Click **Stop Bot** to stop
4. Click **Reset Portfolio** to wipe all trades and reset capital to $1,000

**Sections below the controls:**

| Section | What it shows |
|---------|--------------|
| Portfolio / Net P&L / Fees | Current account value and cumulative P&L |
| Price Chart | Toggle on/off; shows live candlesticks with buy/sell markers |
| P&L Curve | Cumulative net P&L per strategy over time |
| Trade Log | Filterable table of all executed trades |
| Bot Log | Last 30 lines of bot output (signals, orders, errors) |

### Backtest

Test strategies on historical data before risking capital.

1. Pick a **Symbol**, **Interval**, and **Leverage**
2. Select one or more **Strategies**
3. Expand each strategy to **customize parameters** (e.g. RSI period, MACD fast/slow)
4. Click **Run Backtest**

Results include:
- Equity curve comparison chart
- Summary table with Final Capital, Return, Sharpe Ratio, Max Drawdown, Win Rate, and Fees
- Single-strategy equity curve (when only one strategy is selected)

### Strategy Editor

Write custom strategies in Python without restarting the bot.

1. Enter a **filename** for your strategy
2. Edit the code in the text area (a template is loaded by default)
3. Click **Save Strategy** — syntax is checked before saving
4. Your strategy immediately appears in the Trading and Backtest strategy selectors

To edit an existing custom strategy, select it from the **Load existing** dropdown.

---

## Built-in Strategies

| Strategy | Description | Key Parameters |
|----------|-------------|----------------|
| **MACD** | Moving Average Convergence Divergence — buys on bullish crossover, sells on bearish crossover | `fast` (12), `slow` (26), `signal_period` (9) |
| **RSI** | Relative Strength Index — buys when oversold, sells when overbought | `period` (14), `overbought` (70), `oversold` (30) |
| **BollingerBands** | Mean reversion on Bollinger Band touches — buys at lower band, sells at upper band | `period` (20), `num_std` (2.0) |
| **Supertrend** | ATR-based trend following — buys when trend flips bullish, sells when bearish | `period` (10), `multiplier` (3.0) |

---

## Writing a Custom Strategy

Create a `.py` file in `strategy/custom/` (or use the Strategy Editor tab). Your class must:

- Inherit from `BaseStrategy`
- Have a class name ending with `Strategy`
- Implement `generate_signal()` returning a `TradeSignal`

```python
import numpy as np
from strategy.base import BaseStrategy, Signal, TradeSignal


class MyCustomStrategy(BaseStrategy):
    def __init__(self, fast_period: int = 10, slow_period: int = 30):
        super().__init__(name="MyCustom")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.max_history = 200

    def generate_signal(self) -> TradeSignal:
        closes = self.closes
        if len(closes) < self.slow_period + 1:
            return TradeSignal(Signal.HOLD, 0.0, "Insufficient data")

        price = closes[-1]
        fast_avg = np.mean(closes[-self.fast_period:])
        slow_avg = np.mean(closes[-self.slow_period:])

        fast_prev = np.mean(closes[-(self.fast_period + 1):-1])
        slow_prev = np.mean(closes[-(self.slow_period + 1):-1])

        if fast_prev <= slow_prev and fast_avg > slow_avg:
            return TradeSignal(Signal.BUY, 0.7, "Fast crossed above slow", price=price)

        if fast_prev >= slow_prev and fast_avg < slow_avg:
            return TradeSignal(Signal.SELL, 0.7, "Fast crossed below slow", price=price)

        return TradeSignal(Signal.HOLD, 0.0, "No signal", price=price)
```

**Available data in your strategy:**

| Property | Type | Description |
|----------|------|-------------|
| `self.closes` | `list[float]` | Close prices |
| `self.highs` | `list[float]` | High prices |
| `self.lows` | `list[float]` | Low prices |
| `self.volumes` | `list[float]` | Volume |
| `self._candles` | `list[dict]` | Raw candle dicts with all fields |

**Signal confidence** must be between 0.0 and 1.0. Signals below **0.3** confidence are ignored by the bot.

---

## Project Structure

```
crypto_trading_bot/
├── dashboard/
│   └── app.py              # Streamlit dashboard
├── data/
│   ├── feed.py             # Binance WebSocket + REST data feeds
│   ├── storage.py          # SQLite database (candles + trades)
│   ├── replay.py           # Historical data replay for backtesting
│   └── prices.py           # 24h price stats
├── execution/
│   ├── executor.py         # Binance Testnet order execution
│   └── risk.py             # Position sizing, stop-loss, drawdown
├── strategy/
│   ├── base.py             # BaseStrategy + Signal/TradeSignal
│   ├── macd.py             # MACD strategy
│   ├── rsi.py              # RSI strategy
│   ├── mean_reversion.py   # Bollinger Bands strategy
│   ├── supertrend.py       # Supertrend strategy
│   ├── loader.py           # Strategy discovery + factory
│   └── custom/             # Drop custom strategies here
│       └── _template.py    # Template for new strategies
├── backtest.py             # Backtesting engine
├── bot_runner.py           # Live bot (runs as subprocess)
├── config.py               # Symbols, leverage, fees, strategy params
├── .env                    # API keys (not committed)
└── requirements.txt
```

---

## Configuration

Edit `config.py` to change:

| Setting | Default | Description |
|---------|---------|-------------|
| `SYMBOLS` | BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, DOT, POL, NEAR, SUI, ARB, LTC, INJ, RENDER, LINK, DASH | Supported trading pairs |
| `INITIAL_CAPITAL` | `1000.0` | Starting portfolio value |
| `TRADING_FEE_RATE` | `0.001` (0.1%) | Fee per trade |
| `LEVERAGE_OPTIONS` | 1, 2, 3, 5, 10, 20 | Available leverage levels |

---

## Tips

- **Warmup time**: strategies need historical candles before generating signals. MACD needs ~35 candles, so on a 5m interval expect ~3 hours before the first trade.
- **Flat markets**: if the price isn't moving, strategies won't signal — that's correct behavior.
- **Use Backtest first**: always backtest a strategy before running it live to understand its behavior.
- **Monitor the Bot Log**: check the Trading tab's Bot Log section or run `tail -f bot.log` in your terminal to see real-time signals and orders.
- **Reset before switching**: click **Reset Portfolio** before changing strategies or coins to start fresh.
