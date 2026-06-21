# Velox

A cryptocurrency trading bot with a Streamlit dashboard for paper trading, live trading (via Hyperliquid), backtesting, and custom strategy development. All market data powered by Hyperliquid API.

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

The dashboard opens at `http://localhost:8501`. Register an account and you're ready to go.

**No API keys needed for paper trading** — it works out of the box.

### 3. (Optional) Enable live trading

To trade with real money on Hyperliquid, add your wallet private key to `.env`:

```
HL_PRIVATE_KEY=0x_your_private_key_here
```

Then enter your **wallet address** (public) in the Trading tab to view your portfolio. The private key is only used when the bot places orders — it never appears in the UI.

---

## Dashboard

### Markets

Browse live prices for all 17 supported coins.

- **Symbol / Timeframe** — pick any coin and candle interval (1m, 5m, 15m, 1h)
- **Chart Type** — toggle between Candlestick and Line view
- **Show Volume** — toggle volume bars on/off
- **Range buttons** (30m, 1h, 4h, 12h, All) — quick-zoom to different time windows
- **Scroll zoom** — mouse wheel zooms in/out; click-drag to select a region

### Trading

Control the bot and monitor performance.

1. Choose **Paper** or **Live** mode
2. Select a **Coin**, one or more **Strategies**, a **Leverage** level, and an **Interval**
3. Click **Start Bot** to begin trading
4. Click **Stop Bot** to stop
5. Click **Reset Portfolio** to wipe all trades and reset capital to $1,000

| Mode | How it works | Fees | Real Money |
|------|-------------|------|------------|
| Paper | Simulated trades using live Hyperliquid prices | 0.035% | No |
| Live | Real orders on Hyperliquid | 0.035% | Yes |

**Dashboard sections:**

| Section | What it shows |
|---------|--------------|
| Portfolio / Realized / Unrealized / Fees | Live account value including open positions |
| Open Positions | Current positions with entry, current price, and P&L |
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

Results include equity curve comparison, summary table (Return, Sharpe, Max Drawdown, Win Rate), and per-strategy breakdown.

### Strategy Editor

Write custom strategies in Python.

- Click **+ New** to start from a template
- Click an existing strategy to edit it
- Click **x** to delete
- Saved strategies auto-appear in Trading and Backtest selectors
- Each user's strategies are isolated — other users can't see yours

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

Create a `.py` file via the Strategy Editor tab. Your class must:

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
velox/
├── dashboard/
│   └── app.py                  # Streamlit dashboard
├── data/
│   ├── feed.py                 # Hyperliquid candle data feed
│   ├── storage.py              # SQLite database (candles + trades)
│   ├── replay.py               # Historical data replay for backtesting
│   └── prices.py               # Live prices from Hyperliquid
├── execution/
│   ├── paper_executor.py       # Paper trading (simulated)
│   ├── hyperliquid_executor.py # Live trading on Hyperliquid
│   ├── executor.py             # Binance executor (legacy)
│   └── risk.py                 # Position sizing, stop-loss, drawdown
├── strategy/
│   ├── base.py                 # BaseStrategy + Signal/TradeSignal
│   ├── macd.py                 # MACD strategy
│   ├── rsi.py                  # RSI strategy
│   ├── mean_reversion.py       # Bollinger Bands strategy
│   ├── supertrend.py           # Supertrend strategy
│   ├── loader.py               # Strategy discovery + factory
│   └── custom/                 # Per-user custom strategies
│       └── _template.py        # Template for new strategies
├── backtest.py                 # Backtesting engine
├── bot_runner.py               # Bot process (runs as subprocess)
├── config.py                   # Symbols, leverage, fees, strategy params
├── auth_config.yaml            # User credentials (auto-created, not committed)
├── .env                        # Private key for live trading (not committed)
└── requirements.txt
```

---

## Configuration

Edit `config.py` to change:

| Setting | Default | Description |
|---------|---------|-------------|
| `SYMBOLS` | BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, DOT, NEAR, SUI, ARB, LTC, INJ, RENDER, LINK, HYPE | Supported coins |
| `INITIAL_CAPITAL` | `1000.0` | Starting portfolio value (paper mode) |
| `TRADING_FEE_RATE` | `0.00035` (0.035%) | Fee per trade |
| `LEVERAGE_OPTIONS` | 1, 2, 3, 5, 10, 20 | Available leverage levels |

---

## User Isolation

Each registered user gets:
- Their own trade history and portfolio
- Their own custom strategies folder
- Their own bot instance and log file
- Paper and Live modes are fully separate

---

## Tips

- **No setup needed for paper trading** — just install deps and run. No API keys required.
- **Warmup time**: strategies need historical candles before generating signals. MACD needs ~35 candles, so on a 5m interval expect ~3 hours before the first trade.
- **Flat markets**: if the price isn't moving, strategies won't signal — that's correct behavior.
- **Use Backtest first**: always backtest a strategy before running it live.
- **Monitor the Bot Log**: check the Trading tab's Bot Log section to see real-time signals and orders.
- **Reset before switching**: click **Reset Portfolio** before changing strategies or coins to start fresh.
- **Live trading security**: your private key is stored in `.env` (never committed to git) and passed to the bot via environment variable (never visible in process listings or the UI). Only your public wallet address is entered in the dashboard.
