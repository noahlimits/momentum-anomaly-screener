from __future__ import annotations

from math import floor


def target_shares(portfolio_value: float, atr20: float | None, allow_fractional: bool = False) -> float:
    if atr20 is None or atr20 <= 0 or portfolio_value <= 0:
        return 0.0
    shares = (portfolio_value * 0.001) / atr20
    return float(shares if allow_fractional else floor(shares))


def target_value(shares: float, price: float | None) -> float:
    if price is None:
        return 0.0
    return float(max(0.0, shares) * price)


def target_weight(value: float, portfolio_value: float) -> float:
    if portfolio_value <= 0:
        return 0.0
    return float(value / portfolio_value)
