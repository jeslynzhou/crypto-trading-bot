import sys
import os
import json
import subprocess
import signal as sig_mod

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import yaml
import streamlit as st
import streamlit_authenticator as stauth
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import SYMBOLS, STRATEGIES, STRATEGY_NAMES, LEVERAGE_OPTIONS, TRADING_FEE_RATE, INITIAL_CAPITAL
from data.storage import get_trades, get_candles, get_portfolio_value, clear_trades, init_db
from data.feed import DataFeed
from data.prices import get_prices, get_24h_stats
from strategy.loader import get_all_strategy_names, build_strategy

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TEMPLATE_PATH = os.path.join(PROJECT_ROOT, "strategy", "custom", "_template.py")
AUTH_CONFIG_PATH = os.path.join(PROJECT_ROOT, "auth_config.yaml")


@st.cache_data(ttl=30)
def fetch_candles(symbol: str, interval: str, limit: int = 500) -> list[dict]:
    try:
        feed = DataFeed(symbol=symbol, interval=interval)
        feed.fetch_historical(limit=limit)
    except Exception:
        pass
    return get_candles(symbol, interval, limit=limit)


@st.cache_data(ttl=10)
def cached_prices(symbols: tuple) -> dict:
    return get_24h_stats(list(symbols))


@st.cache_data(ttl=60)
def cached_strategy_names(user_dir: str = "") -> list[str]:
    return get_all_strategy_names(user_dir if user_dir else None)


@st.cache_data(ttl=10)
def fetch_hl_portfolio(address: str) -> dict:
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        info = Info(constants.MAINNET_API_URL)
        state = info.user_state(address)
        balance = float(state.get("marginSummary", {}).get("accountValue", 0))
        withdrawable = float(state.get("withdrawable", 0))
        positions = []
        for p in state.get("assetPositions", []):
            pos = p.get("position", {})
            if float(pos.get("szi", 0)) != 0:
                positions.append({
                    "coin": pos.get("coin", ""),
                    "size": float(pos.get("szi", 0)),
                    "entry_price": float(pos.get("entryPx", 0)),
                    "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                    "leverage": int(float(pos.get("leverage", {}).get("value", 1))),
                    "liquidation_price": float(pos.get("liquidationPx", 0) or 0),
                })
        return {"balance": balance, "withdrawable": withdrawable, "positions": positions}
    except Exception as e:
        return {"balance": 0, "withdrawable": 0, "positions": [], "error": str(e)}


def calc_unrealized_pnl(positions: dict, prices: dict) -> float:
    total = 0.0
    for pos in positions.values():
        sym = pos["symbol"]
        current_price = prices.get(sym, 0)
        if current_price <= 0:
            continue
        if pos["side"] == "LONG":
            total += (current_price - pos["entry_price"]) * pos["quantity"]
        else:
            total += (pos["entry_price"] - current_price) * pos["quantity"]
    return total


# ── Per-user file helpers ──

def user_pid_file(username):
    return os.path.join(PROJECT_ROOT, f".bot_pid_{username}")


def user_log_file(username):
    return os.path.join(PROJECT_ROOT, f"bot_{username}.log")


def user_positions_file(username):
    return os.path.join(PROJECT_ROOT, f".positions_{username}.json")


def user_custom_dir(username):
    d = os.path.join(PROJECT_ROOT, "strategy", "custom", username)
    os.makedirs(d, exist_ok=True)
    return d


def get_open_positions(username):
    path = user_positions_file(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def is_bot_running(username):
    path = user_pid_file(username)
    if not os.path.exists(path):
        return False
    with open(path) as f:
        pid = int(f.read().strip())
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        os.remove(path)
        return False


def start_bot(symbols, strategies, leverage, interval, username,
              mode="paper", hl_private_key=""):
    config = json.dumps({
        "symbols": symbols, "strategies": strategies,
        "leverage": leverage, "interval": interval,
        "mode": mode, "user_id": username,
    })
    log_fh = open(user_log_file(username), "a")
    env = os.environ.copy()
    if hl_private_key:
        env["HL_PRIVATE_KEY"] = hl_private_key
    proc = subprocess.Popen(
        [sys.executable, "-u", "bot_runner.py", config],
        cwd=PROJECT_ROOT,
        stdout=log_fh, stderr=log_fh,
        env=env,
    )
    with open(user_pid_file(username), "w") as f:
        f.write(str(proc.pid))


def stop_bot(username):
    path = user_pid_file(username)
    if not os.path.exists(path):
        return
    with open(path) as f:
        pid = int(f.read().strip())
    try:
        os.kill(pid, sig_mod.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    os.remove(path)


# ── Auth ──

init_db()
st.set_page_config(page_title="Velox", layout="wide")


def load_auth_config():
    if not os.path.exists(AUTH_CONFIG_PATH):
        cfg = {
            "credentials": {"usernames": {}},
            "cookie": {
                "expiry_days": 30,
                "key": "crypto_bot_auth_key",
                "name": "crypto_bot_auth",
            },
        }
        with open(AUTH_CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f)
        return cfg
    with open(AUTH_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_auth_config(cfg):
    with open(AUTH_CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f)


auth_config = load_auth_config()
authenticator = stauth.Authenticate(
    auth_config["credentials"],
    auth_config["cookie"]["name"],
    auth_config["cookie"]["key"],
    auth_config["cookie"]["expiry_days"],
)

if st.session_state.get("authentication_status") is None or st.session_state.get("authentication_status") is False:
    st.title("Velox")
    tab_login, tab_register = st.tabs(["Login", "Register"])
    with tab_login:
        authenticator.login(location="main")
        if st.session_state.get("authentication_status") is False:
            st.error("Wrong username or password.")
    with tab_register:
        try:
            res = authenticator.register_user()
            if res:
                save_auth_config(auth_config)
                st.success("Registered! Please log in.")
        except Exception as e:
            st.error(str(e))
    st.stop()

# ── Authenticated ──
username = st.session_state["username"]
user_dir = user_custom_dir(username)
all_strat_names = cached_strategy_names(user_dir)

st.title("Velox")
with st.sidebar:
    st.write(f"Logged in as **{username}**")
    authenticator.logout("Logout")

TAB_NAMES = ["Markets", "Trading", "Backtest", "Strategy Editor"]
params = st.query_params
active_tab = params.get("tab", "Markets")
if active_tab not in TAB_NAMES:
    active_tab = "Markets"

cols = st.columns(len(TAB_NAMES))
for i, tab_name in enumerate(TAB_NAMES):
    with cols[i]:
        btn_type = "primary" if active_tab == tab_name else "secondary"
        if st.button(tab_name, key=f"nav_{tab_name}", use_container_width=True, type=btn_type):
            st.query_params["tab"] = tab_name
            st.rerun()

st.divider()

# ── Markets ──

if active_tab == "Markets":
    col_sym, col_tf = st.columns([3, 1])
    with col_sym:
        selected = st.selectbox("Symbol", SYMBOLS,
                                format_func=lambda s: f"{s}/USD")
    with col_tf:
        timeframe = st.selectbox("Timeframe", ["1m", "5m", "15m", "1h"])

    stats = cached_prices(tuple(SYMBOLS))
    if stats:
        per_row = 6
        for row_start in range(0, len(SYMBOLS), per_row):
            row_syms = SYMBOLS[row_start:row_start + per_row]
            mcols = st.columns(per_row)
            for i, sym in enumerate(row_syms):
                s = stats.get(sym, {})
                with mcols[i]:
                    label = sym
                    price = s.get("price", 0)
                    change = s.get("change_pct", 0)
                    st.metric(label,
                              f"${price:,.2f}" if price >= 1 else f"${price:.4f}",
                              delta=f"{change:+.2f}%")

    chart_type = st.radio("Chart Type", ["Candlestick", "Line"], horizontal=True, key="mkt_chart_type")
    show_volume = st.toggle("Show Volume", value=True, key="mkt_vol")

    range_limits = {"30m": 30, "1h": 60, "4h": 240, "12h": 720, "All": 500}
    if f"mkt_range_{selected}" not in st.session_state:
        st.session_state[f"mkt_range_{selected}"] = "All"
    rcols = st.columns(len(range_limits))
    for i, (label, _) in enumerate(range_limits.items()):
        with rcols[i]:
            btn_type = "primary" if st.session_state[f"mkt_range_{selected}"] == label else "secondary"
            if st.button(label, key=f"mkt_r_{label}", use_container_width=True, type=btn_type):
                st.session_state[f"mkt_range_{selected}"] = label
                st.rerun()
    candle_limit = range_limits[st.session_state[f"mkt_range_{selected}"]]

    candles = fetch_candles(selected, timeframe, limit=candle_limit)
    if candles:
        df = pd.DataFrame(candles)
        df["time"] = pd.to_datetime(df["open_time"], unit="ms")

        row_heights = [0.8, 0.2] if show_volume else [1.0]
        num_rows = 2 if show_volume else 1
        fig = make_subplots(rows=num_rows, cols=1, shared_xaxes=True,
                            row_heights=row_heights, vertical_spacing=0.02)

        if chart_type == "Candlestick":
            fig.add_trace(go.Candlestick(
                x=df["time"], open=df["open"], high=df["high"],
                low=df["low"], close=df["close"], name="Price",
                increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
                increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
            ), row=1, col=1)
        else:
            fig.add_trace(go.Scatter(
                x=df["time"], y=df["close"], mode="lines", name="Price",
                line=dict(color="#26a69a", width=2),
            ), row=1, col=1)

        if show_volume:
            colors = ["#26a69a" if c >= o else "#ef5350"
                      for c, o in zip(df["close"], df["open"])]
            fig.add_trace(go.Bar(x=df["time"], y=df["volume"], name="Volume",
                                 marker_color=colors, opacity=0.5), row=2, col=1)

        latest_price = df.iloc[-1]["close"]
        price_fmt = f"${latest_price:,.2f}" if latest_price >= 1 else f"${latest_price:.4f}"
        fig.update_layout(
            template="plotly_dark", height=600,
            xaxis_rangeslider_visible=False, showlegend=False,
            margin=dict(l=0, r=0, t=40, b=0),
            title=f"{selected} — {timeframe} — {price_fmt}",
            yaxis=dict(title="Price", side="right"),
            dragmode="zoom",
        )
        if show_volume:
            fig.update_layout(yaxis2=dict(title="Vol", side="right"))
        fig.update_xaxes(showgrid=True, gridcolor="#1e1e1e")
        fig.update_yaxes(showgrid=True, gridcolor="#1e1e1e")
        st.plotly_chart(fig, use_container_width=True, config={
            "scrollZoom": True,
            "displayModeBar": True,
        })

        latest = df.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Open", f"${latest['open']:,.2f}" if latest['open'] >= 1 else f"${latest['open']:.4f}")
        c2.metric("High", f"${latest['high']:,.2f}" if latest['high'] >= 1 else f"${latest['high']:.4f}")
        c3.metric("Low", f"${latest['low']:,.2f}" if latest['low'] >= 1 else f"${latest['low']:.4f}")
        c4.metric("Volume", f"{latest['volume']:,.0f}")
    else:
        st.warning(f"No data for {selected} {timeframe}.")

# ── Trading ──

if active_tab == "Trading":

    st.subheader("Bot Controls")
    running_paper = is_bot_running(f"{username}_paper")
    running_live = is_bot_running(f"{username}_live")

    bot_mode = st.radio("Mode", ["Paper", "Live"], horizontal=True, key="bot_mode")

    hl_address = ""
    hl_key = os.environ.get("HL_PRIVATE_KEY", "")
    if bot_mode == "Live":
        hl_address = st.text_input("Wallet Address (public, for viewing portfolio)",
                                   key="hl_address", placeholder="0x...")
        if not hl_key:
            st.info("To trade live, add `HL_PRIVATE_KEY=0x...` to your `.env` file. Your wallet address above is only used to view your portfolio.")

    user_key = f"{username}_{bot_mode.lower()}"

    ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns(4)
    with ctrl_col1:
        bot_symbol = st.selectbox("Coin", SYMBOLS, key="bot_sym",
                                  format_func=lambda s: f"{s}/USD")
    with ctrl_col2:
        bot_strats = st.multiselect("Strategies", all_strat_names,
                                    default=all_strat_names[:2], key="bot_strat")
    with ctrl_col3:
        bot_leverage = st.selectbox("Leverage", LEVERAGE_OPTIONS, key="bot_lev")
    with ctrl_col4:
        bot_interval = st.selectbox("Interval", ["1m", "5m", "15m"], key="bot_int")

    if bot_mode == "Live":
        eff_fee = 0.035 * bot_leverage
        st.caption(f"Mode: **LIVE (Hyperliquid)** — Effective fee: {eff_fee:.2f}% per trade (0.035% x {bot_leverage}x)")
    else:
        eff_fee = TRADING_FEE_RATE * bot_leverage * 100
        st.caption(f"Mode: **Paper** — Effective fee: {eff_fee:.2f}% per trade ({TRADING_FEE_RATE*100:.3f}% x {bot_leverage}x)")

    can_start = bot_mode == "Paper" or (bot_mode == "Live" and hl_key and hl_address)

    running = is_bot_running(user_key)

    btn_col1, btn_col2, btn_col3, status_col = st.columns([1, 1, 1, 2])
    with btn_col1:
        if st.button("Start Bot", disabled=running or not can_start, type="primary"):
            stop_bot(user_key)
            start_bot([bot_symbol], bot_strats, bot_leverage, bot_interval,
                      username=user_key, mode=bot_mode.lower(), hl_private_key=hl_key)
            st.rerun()
    with btn_col2:
        if st.button("Stop Bot", disabled=not running):
            stop_bot(user_key)
            st.rerun()
    with btn_col3:
        if st.button("Reset Portfolio", disabled=running):
            stop_bot(user_key)
            clear_trades(user_id=user_key)
            pos_path = user_positions_file(user_key)
            if os.path.exists(pos_path):
                os.remove(pos_path)
            st.rerun()
    with status_col:
        if running:
            st.success(f"{bot_mode} bot is running")
        else:
            st.info(f"{bot_mode} — Portfolio: ${INITIAL_CAPITAL:,.0f}")

    st.divider()

    # ── P&L ──
    if bot_mode == "Live" and hl_address:
        hl_portfolio = fetch_hl_portfolio(hl_address)
        hl_balance = hl_portfolio["balance"]
        hl_positions = hl_portfolio["positions"]
        hl_unrealized = sum(p["unrealized_pnl"] for p in hl_positions)

        col1, col2, col3 = st.columns(3)
        col1.metric("Account Value", f"${hl_balance:,.2f}")
        col2.metric("Unrealized P&L", f"${hl_unrealized:,.2f}",
                    delta=f"{hl_unrealized:+,.2f}" if hl_unrealized != 0 else None)
        col3.metric("Withdrawable", f"${hl_portfolio['withdrawable']:,.2f}")

        if hl_positions:
            with st.expander(f"Hyperliquid Positions ({len(hl_positions)})", expanded=True):
                st.dataframe(pd.DataFrame([{
                    "Coin": p["coin"],
                    "Size": f"{p['size']:.4f}",
                    "Side": "LONG" if p["size"] > 0 else "SHORT",
                    "Entry": f"${p['entry_price']:,.2f}",
                    "Liq. Price": f"${p['liquidation_price']:,.2f}" if p["liquidation_price"] > 0 else "—",
                    "Leverage": f"{p['leverage']}x",
                    "Unrealized P&L": f"${p['unrealized_pnl']:,.2f}",
                } for p in hl_positions]), use_container_width=True, hide_index=True)

        if "error" in hl_portfolio:
            st.error(f"Failed to fetch portfolio: {hl_portfolio['error']}")

    else:
        all_trades = get_trades(limit=5000, user_id=user_key)
        total_pnl = sum(t["pnl"] for t in all_trades)
        total_fees = sum(t.get("fee", 0) for t in all_trades)
        realized_pnl = total_pnl - total_fees

        open_positions = get_open_positions(user_key)
        unrealized = 0.0
        if open_positions and running:
            price_stats = cached_prices(tuple(SYMBOLS))
            current_prices = {s: d.get("price", 0) for s, d in price_stats.items()} if price_stats else {}
            unrealized = calc_unrealized_pnl(open_positions, current_prices)

        portfolio_value = INITIAL_CAPITAL + realized_pnl + unrealized

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Portfolio", f"${portfolio_value:,.2f}", delta=f"{realized_pnl + unrealized:+,.2f}")
        col2.metric("Realized P&L", f"${realized_pnl:,.2f}")
        col3.metric("Unrealized P&L", f"${unrealized:,.2f}",
                    delta=f"{unrealized:+,.2f}" if unrealized != 0 else None)
        col4.metric("Fees Paid", f"${total_fees:,.2f}")

        if open_positions and running:
            with st.expander(f"Open Positions ({len(open_positions)})", expanded=True):
                pos_data = []
                for key, pos in open_positions.items():
                    sym = pos["symbol"]
                    cur_price = current_prices.get(sym, 0)
                    if pos["side"] == "LONG":
                        pos_pnl = (cur_price - pos["entry_price"]) * pos["quantity"]
                    else:
                        pos_pnl = (pos["entry_price"] - cur_price) * pos["quantity"]
                    pos_data.append({
                        "Symbol": sym,
                        "Strategy": pos["strategy"],
                        "Side": pos["side"],
                        "Entry": f"${pos['entry_price']:,.2f}",
                        "Current": f"${cur_price:,.2f}" if cur_price > 0 else "—",
                        "Qty": f"{pos['quantity']:.6f}",
                        "P&L": f"${pos_pnl:,.2f}",
                    })
                st.dataframe(pd.DataFrame(pos_data), use_container_width=True, hide_index=True)

    # ── Price Chart ──
    show_price = st.toggle("Show Price Chart", value=True, key="show_price")
    if show_price:
        chart_interval = st.radio("Timeframe", ["1m", "5m", "15m", "1h"],
                                  horizontal=True, key="chart_tf",
                                  index=["1m", "5m", "15m", "1h"].index(bot_interval))
        range_col1, range_col2, range_col3, range_col4, range_col5 = st.columns(5)
        candle_limits = {"30m": 30, "1h": 60, "4h": 240, "12h": 720, "Max": 500}
        range_labels = list(candle_limits.keys())
        selected_range = "1h"
        with range_col1:
            if st.button("30m", use_container_width=True, key="r_30m"):
                selected_range = "30m"
        with range_col2:
            if st.button("1h", use_container_width=True, key="r_1h", type="primary"):
                selected_range = "1h"
        with range_col3:
            if st.button("4h", use_container_width=True, key="r_4h"):
                selected_range = "4h"
        with range_col4:
            if st.button("12h", use_container_width=True, key="r_12h"):
                selected_range = "12h"
        with range_col5:
            if st.button("Max", use_container_width=True, key="r_max"):
                selected_range = "Max"

        if f"price_range_{bot_symbol}" not in st.session_state:
            st.session_state[f"price_range_{bot_symbol}"] = "1h"
        for r in range_labels:
            if st.session_state.get(f"r_{r.lower()}", False):
                st.session_state[f"price_range_{bot_symbol}"] = r
        selected_range = st.session_state.get(f"price_range_{bot_symbol}", "1h")
        limit = candle_limits[selected_range]

        price_candles = fetch_candles(bot_symbol, chart_interval, limit=limit)
        if price_candles:
            pdf = pd.DataFrame(price_candles)
            pdf["time"] = pd.to_datetime(pdf["open_time"], unit="ms")

            fig_price = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                      row_heights=[0.8, 0.2], vertical_spacing=0.02)
            fig_price.add_trace(go.Candlestick(
                x=pdf["time"], open=pdf["open"], high=pdf["high"],
                low=pdf["low"], close=pdf["close"], name="Price",
                increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
                increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
            ), row=1, col=1)
            vol_colors = ["#26a69a" if c >= o else "#ef5350"
                          for c, o in zip(pdf["close"], pdf["open"])]
            fig_price.add_trace(go.Bar(x=pdf["time"], y=pdf["volume"], name="Volume",
                                       marker_color=vol_colors, opacity=0.5), row=2, col=1)

            trade_list = get_trades(symbol=bot_symbol, limit=200, user_id=user_key)
            if trade_list:
                tdf = pd.DataFrame(trade_list)
                tdf["timestamp"] = pd.to_datetime(tdf["timestamp"], utc=True).dt.tz_localize(None)
                min_time = pdf["time"].min()
                tdf = tdf[tdf["timestamp"] >= min_time]
                buys = tdf[tdf["side"] == "BUY"]
                sells = tdf[tdf["side"] == "SELL"]
                if not buys.empty:
                    fig_price.add_trace(go.Scatter(
                        x=buys["timestamp"], y=buys["price"], mode="markers",
                        name="Buy", marker=dict(symbol="triangle-up", size=12,
                                                color="#26a69a", line=dict(width=1, color="white")),
                    ), row=1, col=1)
                if not sells.empty:
                    fig_price.add_trace(go.Scatter(
                        x=sells["timestamp"], y=sells["price"], mode="markers",
                        name="Sell", marker=dict(symbol="triangle-down", size=12,
                                                 color="#ef5350", line=dict(width=1, color="white")),
                    ), row=1, col=1)

            latest_price = pdf.iloc[-1]["close"]
            price_fmt = f"${latest_price:,.2f}" if latest_price >= 1 else f"${latest_price:.4f}"
            fig_price.update_layout(
                template="plotly_dark", height=500,
                title=dict(text=f"{bot_symbol} — {chart_interval} — {price_fmt}",
                           y=0.97, x=0.5, xanchor="center"),
                xaxis_rangeslider_visible=False, showlegend=True,
                legend=dict(orientation="h", y=1.15, x=0.5, xanchor="center"),
                margin=dict(l=0, r=0, t=80, b=0),
                yaxis=dict(title="Price", side="right"),
                yaxis2=dict(title="Vol", side="right"),
                dragmode="zoom",
            )
            fig_price.update_xaxes(showgrid=True, gridcolor="#1e1e1e")
            fig_price.update_yaxes(showgrid=True, gridcolor="#1e1e1e")
            st.plotly_chart(fig_price, use_container_width=True, config={
                "scrollZoom": True,
                "displayModeBar": True,
                "modeBarButtonsToAdd": ["drawline", "drawopenpath", "eraseshape"],
            })
        else:
            st.warning(f"No candle data for {bot_symbol} {chart_interval}.")

    st.divider()

    if all_trades:
        df = pd.DataFrame(all_trades)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")

        fig = go.Figure()
        for strat_name in df["strategy_name"].unique():
            sdf = df[df["strategy_name"] == strat_name].copy()
            sdf["cum_net"] = (sdf["pnl"] - sdf["fee"]).cumsum()
            fig.add_trace(go.Scatter(x=sdf["timestamp"], y=sdf["cum_net"],
                                     mode="lines", name=strat_name, line=dict(width=2)))
        df["cum_net_all"] = (df["pnl"] - df["fee"]).cumsum()
        fig.add_trace(go.Scatter(x=df["timestamp"], y=df["cum_net_all"],
                                 mode="lines", name="Total",
                                 line=dict(color="white", width=3, dash="dot")))
        fig.update_layout(template="plotly_dark", height=350,
                          xaxis_title="Time", yaxis_title="Net P&L ($)",
                          legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No trades yet. Configure settings above and click Start Bot.")

    # ── Trade Log ──
    st.subheader("Trade Log")
    col_sym, col_strat = st.columns(2)
    with col_sym:
        f_sym = st.selectbox("Symbol", ["All"] + SYMBOLS, key="t_sym")
    with col_strat:
        f_strat = st.selectbox("Strategy", ["All"] + all_strat_names, key="t_strat")

    filtered = get_trades(
        strategy_name=f_strat if f_strat != "All" else None,
        symbol=f_sym if f_sym != "All" else None,
        limit=50,
        user_id=user_key,
    )
    if filtered:
        st.dataframe(pd.DataFrame([{
            "Time": t["timestamp"], "Symbol": t["symbol"], "Side": t["side"],
            "Price": f"${t['price']:,.2f}", "Qty": f"{t['quantity']:.6f}",
            "Strategy": t["strategy_name"], "Reason": t["reason"],
            "P&L": f"${t['pnl']:,.2f}", "Fee": f"${t.get('fee', 0):,.4f}",
            "Lev": f"{t.get('leverage', 1)}x",
        } for t in filtered]), use_container_width=True, hide_index=True)
    else:
        st.info("No trades match the filter.")

    # ── Bot Log ──
    st.subheader("Bot Log")
    log_path = user_log_file(user_key)
    if os.path.exists(log_path):
        with open(log_path) as f:
            lines = f.readlines()
        tail = lines[-30:] if len(lines) > 30 else lines
        st.code("".join(tail), language="log")
        if st.button("Clear Log"):
            open(log_path, "w").close()
            st.rerun()
    else:
        st.info("No log file yet. Start the bot to see output here.")

# ── Backtest ──

if active_tab == "Backtest":
    st.header("Backtest")

    col_left, col_right = st.columns([1, 2])

    with col_left:
        bt_symbol = st.selectbox("Symbol", SYMBOLS, key="bt_sym",
                                 format_func=lambda s: f"{s}/USD")
        bt_interval = st.selectbox("Interval", ["1m", "5m", "15m", "1h"], key="bt_int")
        bt_leverage = st.selectbox("Leverage", LEVERAGE_OPTIONS, key="bt_lev")
        bt_strategies = st.multiselect("Strategies", all_strat_names,
                                       default=STRATEGY_NAMES, key="bt_s")
        eff = TRADING_FEE_RATE * bt_leverage * 100
        st.caption(f"Effective fee: {eff:.1f}% ({TRADING_FEE_RATE*100:.1f}% x {bt_leverage}x)")

    with col_right:
        st.subheader("Customize Parameters")
        custom_params = {}
        for sname in bt_strategies:
            info = STRATEGIES.get(sname, {})
            defaults = info.get("params", {})
            desc = info.get("description", sname)
            with st.expander(f"{sname} — {desc}", expanded=False):
                bt_params = {}
                for pname, default in defaults.items():
                    if isinstance(default, float):
                        bt_params[pname] = st.number_input(
                            pname, value=default, step=0.1, format="%.1f",
                            key=f"p_{sname}_{pname}")
                    elif isinstance(default, int):
                        bt_params[pname] = st.number_input(
                            pname, value=default, step=1, min_value=1,
                            key=f"p_{sname}_{pname}")
                custom_params[sname] = bt_params

    if st.button("Run Backtest", type="primary"):
        from backtest import Backtester

        results = {}
        progress = st.progress(0)
        for i, sname in enumerate(bt_strategies):
            with st.spinner(f"Running {sname}..."):
                p = custom_params.get(sname, {})
                strat = build_strategy(sname, p if p else None, user_dir=user_dir)
                bt = Backtester(strat, symbol=bt_symbol, interval=bt_interval,
                                fee_rate=TRADING_FEE_RATE, leverage=bt_leverage)
                results[sname] = bt.run()
            progress.progress((i + 1) / len(bt_strategies))
        progress.empty()

        if len(results) > 1:
            st.subheader("Comparison")
            fig_cmp = go.Figure()
            for name, res in results.items():
                if res.equity_curve:
                    fig_cmp.add_trace(go.Scatter(y=res.equity_curve, mode="lines",
                                                  name=name, line=dict(width=2)))
            fig_cmp.update_layout(
                title=f"{bt_symbol}/USD {bt_interval} ({bt_leverage}x lev, {TRADING_FEE_RATE*bt_leverage*100:.2f}% fee)",
                xaxis_title="Candle #", yaxis_title="Portfolio ($)",
                template="plotly_dark", height=400)
            st.plotly_chart(fig_cmp, use_container_width=True)

        comp = [{
            "Strategy": n,
            "Final": f"${r.summary()['final_capital']:,.2f}",
            "Return": f"{r.summary()['net_return']:.2%}",
            "Sharpe": f"{r.summary()['sharpe_ratio']:.2f}",
            "Max DD": f"{r.summary()['max_drawdown']:.2%}",
            "Win Rate": f"{r.summary()['win_rate']:.2%}",
            "Trades": r.summary()["num_trades"],
            "Fees": f"${r.summary()['total_fees']:,.2f}",
        } for n, r in results.items()]
        st.dataframe(pd.DataFrame(comp), use_container_width=True, hide_index=True)

        for name, res in results.items():
            if res.equity_curve and len(results) == 1:
                fig = go.Figure()
                fig.add_trace(go.Scatter(y=res.equity_curve, mode="lines",
                                         name="Equity", line=dict(width=2)))
                fig.update_layout(title=f"{name} ({bt_leverage}x)",
                                  xaxis_title="Candle #", yaxis_title="Portfolio ($)",
                                  template="plotly_dark", height=300)
                st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Select strategies, tweak parameters, then click Run Backtest.")

# ── Strategy Editor ──

if active_tab == "Strategy Editor":
    if "editor_mode" not in st.session_state:
        st.session_state.editor_mode = "new"
        st.session_state.editor_file = ""

    existing_custom = sorted([f[:-3] for f in os.listdir(user_dir)
                              if f.endswith(".py") and not f.startswith("_")])

    col_sidebar, col_editor = st.columns([1, 4])

    with col_sidebar:
        if st.button("+ New", use_container_width=True, type="primary"):
            st.session_state.editor_mode = "new"
            st.session_state.editor_file = ""
            st.rerun()

        for name in existing_custom:
            is_active = st.session_state.editor_mode == "edit" and st.session_state.editor_file == name
            btn_type = "primary" if is_active else "secondary"
            col_btn, col_x = st.columns([5, 1])
            with col_btn:
                if st.button(name, key=f"strat_btn_{name}",
                             use_container_width=True, type=btn_type):
                    st.session_state.editor_mode = "edit"
                    st.session_state.editor_file = name
                    st.rerun()
            with col_x:
                if st.button("x", key=f"strat_del_{name}"):
                    filepath = os.path.join(user_dir, f"{name}.py")
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        st.toast(f"Deleted {name}.py")
                    if st.session_state.editor_file == name:
                        st.session_state.editor_mode = "new"
                        st.session_state.editor_file = ""
                    st.rerun()

        if not existing_custom:
            st.caption("No custom strategies yet.")

    with col_editor:
        if st.session_state.editor_mode == "edit" and st.session_state.editor_file:
            filepath = os.path.join(user_dir, f"{st.session_state.editor_file}.py")
            if os.path.exists(filepath):
                with open(filepath) as f:
                    default_code = f.read()
            else:
                default_code = ""
            default_name = st.session_state.editor_file
            st.subheader(f"Editing: {default_name}.py")
        else:
            if os.path.exists(TEMPLATE_PATH):
                with open(TEMPLATE_PATH) as f:
                    default_code = f.read()
            else:
                default_code = ""
            default_name = ""
            st.subheader("New Strategy")

        strat_filename = st.text_input(
            "Filename (without .py)", value=default_name,
            key=f"ed_name_{st.session_state.editor_mode}_{st.session_state.editor_file}",
            placeholder="my_strategy")

        code = st.text_area(
            "Code", value=default_code, height=450,
            key=f"ed_code_{st.session_state.editor_mode}_{st.session_state.editor_file}")

        if code:
            try:
                compile(code, "<editor>", "exec")
                st.success("Syntax OK")
            except SyntaxError as e:
                st.error(f"Syntax error on line {e.lineno}: {e.msg}")

        if st.button("Save", type="primary"):
            name = strat_filename.strip()
            if not name:
                st.error("Enter a filename.")
            else:
                filepath = os.path.join(user_dir, f"{name}.py")
                try:
                    compile(code, filepath, "exec")
                    with open(filepath, "w") as f:
                        f.write(code)
                    st.toast(f"Saved {name}.py")
                    st.session_state.editor_mode = "edit"
                    st.session_state.editor_file = name
                    st.rerun()
                except SyntaxError as e:
                    st.error(f"Syntax error on line {e.lineno}: {e.msg}")
