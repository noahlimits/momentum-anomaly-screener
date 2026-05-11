from __future__ import annotations

from dataclasses import dataclass
from math import exp, floor, log

import numpy as np
import pandas as pd

from .indicators import atr, linear_regression_r2, max_abs_return, simple_moving_average
from .utils import as_bool, as_float, as_int


@dataclass
class SecurityScore:
    ticker: str
    company_name: str = ""
    sector: str = ""
    price: float | None = None
    rank: int | None = None
    qualified_rank: int | None = None
    percentile_rank: float | None = None
    momentum_score: float | None = None
    annualized_slope: float | None = None
    r_squared: float | None = None
    atr20: float | None = None
    ma100: float | None = None
    above_100dma: bool = False
    gap_max_abs_move: float | None = None
    gap_pass: bool = False
    top_20pct: bool = False
    in_universe: bool = True
    eligible: bool = False
    data_status: str = "UNKNOWN"


def calculate_scores(
    market_data: dict[str, pd.DataFrame],
    metadata: dict[str, dict[str, str]],
    settings: dict[str, object],
    regime_allows_new_buys: bool,
) -> list[SecurityScore]:
    momentum_lookback = as_int(settings.get("momentum_lookback_days"), 90)
    atr_lookback = as_int(settings.get("atr_lookback_days"), 20)
    stock_ma_days = as_int(settings.get("stock_ma_days"), 100)
    gap_threshold = as_float(settings.get("gap_threshold"), 0.15)
    rank_cutoff_percent = as_float(settings.get("rank_cutoff_percent"), 0.20)

    scores = [
        _score_one(
            ticker=ticker,
            data=data,
            meta=metadata.get(ticker, {}),
            momentum_lookback=momentum_lookback,
            atr_lookback=atr_lookback,
            stock_ma_days=stock_ma_days,
            gap_threshold=gap_threshold,
        )
        for ticker, data in market_data.items()
    ]

    valid = [score for score in scores if score.data_status == "OK" and score.momentum_score is not None]
    valid.sort(key=lambda score: score.momentum_score or float("-inf"), reverse=True)

    denominator = len(valid)
    cutoff = max(1, floor(denominator * rank_cutoff_percent)) if denominator else 0
    for index, score in enumerate(valid, start=1):
        score.rank = index
        score.percentile_rank = index / denominator if denominator else None
        score.top_20pct = index <= cutoff
        score.eligible = all(
            [
                score.above_100dma,
                score.gap_pass,
                score.top_20pct,
                regime_allows_new_buys,
                score.atr20 is not None and score.atr20 > 0,
                score.price is not None and score.price > 0,
            ]
        )

    qualified = [score for score in valid if score.eligible]
    for index, score in enumerate(qualified, start=1):
        score.qualified_rank = index

    return sorted(scores, key=lambda score: (score.rank is None, score.rank or 999999, score.ticker))


def _score_one(
    ticker: str,
    data: pd.DataFrame,
    meta: dict[str, str],
    momentum_lookback: int,
    atr_lookback: int,
    stock_ma_days: int,
    gap_threshold: float,
) -> SecurityScore:
    score = SecurityScore(
        ticker=ticker,
        company_name=meta.get("company_name", ""),
        sector=meta.get("sector", ""),
    )
    if data is None or data.empty:
        score.data_status = "NO_DATA"
        return score

    frame = data.sort_index().copy()
    price_series = _price_series(frame)
    valid_prices = price_series.dropna()
    minimum = max(momentum_lookback + 1, stock_ma_days, atr_lookback + 1)
    if len(valid_prices) < minimum:
        score.data_status = "INSUFFICIENT_DATA"
        return score

    score.price = float(valid_prices.iloc[-1])
    ma = simple_moving_average(valid_prices, stock_ma_days)
    score.ma100 = _none_if_nan(ma)
    score.above_100dma = bool(score.ma100 is not None and score.price > score.ma100)

    max_move = max_abs_return(valid_prices, momentum_lookback)
    score.gap_max_abs_move = _none_if_nan(max_move)
    score.gap_pass = bool(score.gap_max_abs_move is not None and score.gap_max_abs_move <= gap_threshold)

    score.atr20 = _none_if_nan(atr(frame, atr_lookback))

    lookback_prices = valid_prices.tail(momentum_lookback)
    if (lookback_prices <= 0).any() or len(lookback_prices) < momentum_lookback:
        score.data_status = "BAD_PRICE_DATA"
        return score

    log_prices = np.array([log(value) for value in lookback_prices], dtype=float)
    slope, r_squared = linear_regression_r2(log_prices)
    if np.isnan(slope) or np.isnan(r_squared):
        score.data_status = "REGRESSION_ERROR"
        return score

    score.annualized_slope = exp(slope * 252) - 1.0
    score.r_squared = r_squared
    score.momentum_score = score.annualized_slope * r_squared
    score.data_status = "OK"
    return score


def _price_series(frame: pd.DataFrame) -> pd.Series:
    if "adj_close" in frame.columns and not frame["adj_close"].dropna().empty:
        return pd.to_numeric(frame["adj_close"], errors="coerce")
    return pd.to_numeric(frame["close"], errors="coerce")


def _none_if_nan(value: float) -> float | None:
    return None if value is None or np.isnan(value) else float(value)
