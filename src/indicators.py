from __future__ import annotations

import numpy as np
import pandas as pd


def simple_moving_average(values: pd.Series, lookback: int) -> float:
    series = pd.to_numeric(values, errors="coerce").dropna()
    if len(series) < lookback:
        return float("nan")
    return float(series.tail(lookback).mean())


def max_abs_return(values: pd.Series, lookback: int) -> float:
    series = pd.to_numeric(values, errors="coerce").dropna()
    if len(series) < lookback + 1:
        return float("nan")
    returns = series.tail(lookback + 1).pct_change().dropna()
    if returns.empty:
        return float("nan")
    return float(returns.abs().max())


def atr(data: pd.DataFrame, lookback: int = 20) -> float:
    required = {"high", "low", "close"}
    if not required.issubset(set(data.columns)):
        return float("nan")
    frame = data[["high", "low", "close"]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(frame) < lookback + 1:
        return float("nan")
    recent = frame.tail(lookback + 1).copy()
    previous_close = recent["close"].shift(1)
    true_range = pd.concat(
        [
            recent["high"] - recent["low"],
            (recent["high"] - previous_close).abs(),
            (recent["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(true_range.dropna().tail(lookback).mean())


def linear_regression_r2(y: np.ndarray) -> tuple[float, float]:
    if len(y) < 2 or np.isnan(y).any():
        return float("nan"), float("nan")
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = intercept + slope * x
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 0.0 if ss_tot == 0 else 1.0 - (ss_res / ss_tot)
    return float(slope), float(max(0.0, min(1.0, r_squared)))
