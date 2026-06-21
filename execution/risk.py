def calculate_position_size(portfolio_value: float, entry_price: float,
                            stop_loss_price: float, risk_pct: float = 0.01) -> float:
    risk_amount = portfolio_value * risk_pct
    price_risk = abs(entry_price - stop_loss_price)
    if price_risk == 0:
        return 0.0
    return risk_amount / price_risk


def check_stop_loss(entry_price: float, current_price: float, stop_pct: float = 0.02,
                    trailing: bool = False, highest_since_entry: float = 0.0,
                    lowest_since_entry: float = float('inf'),
                    side: str = "LONG") -> bool:
    if side == "LONG":
        if trailing:
            reference = highest_since_entry if highest_since_entry > 0 else entry_price
        else:
            reference = entry_price
        return current_price <= reference * (1 - stop_pct)
    else:
        if trailing:
            reference = lowest_since_entry if lowest_since_entry < float('inf') else entry_price
        else:
            reference = entry_price
        return current_price >= reference * (1 + stop_pct)


def check_daily_drawdown(starting_balance: float, current_balance: float,
                         max_drawdown_pct: float = 0.03) -> bool:
    if starting_balance <= 0:
        return False
    drawdown = (starting_balance - current_balance) / starting_balance
    return drawdown >= max_drawdown_pct
