from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import simple_moving_average


@dataclass
class RegimeStatus:
    proxy: str
    proxy_close: float | None
    proxy_ma: float | None
    ma_days: int
    status: str
    allows_new_buys: bool


def calculate_regime(proxy: str, data: pd.DataFrame, ma_days: int) -> RegimeStatus:
    if data is None or data.empty:
        return RegimeStatus(proxy, None, None, ma_days, "DATA_ERROR", False)
    series = data["adj_close"] if "adj_close" in data.columns and not data["adj_close"].dropna().empty else data["close"]
    series = pd.to_numeric(series, errors="coerce").dropna()
    if len(series) < ma_days:
        return RegimeStatus(proxy, None, None, ma_days, "DATA_ERROR", False)
    close = float(series.iloc[-1])
    ma = simple_moving_average(series, ma_days)
    allows = close > ma
    return RegimeStatus(
        proxy=proxy,
        proxy_close=close,
        proxy_ma=ma,
        ma_days=ma_days,
        status="BULLISH" if allows else "BEARISH",
        allows_new_buys=allows,
    )
