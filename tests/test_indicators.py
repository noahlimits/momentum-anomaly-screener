import numpy as np
import pandas as pd

from src.indicators import atr, linear_regression_r2, max_abs_return, simple_moving_average


def test_simple_moving_average_uses_tail_window():
    values = pd.Series([1, 2, 3, 4, 5])
    assert simple_moving_average(values, 3) == 4


def test_max_abs_return_uses_lookback_window():
    values = pd.Series([100, 110, 99, 101])
    assert round(max_abs_return(values, 3), 4) == 0.1


def test_atr_true_range():
    frame = pd.DataFrame(
        {
            "high": [11, 13, 14],
            "low": [9, 10, 12],
            "close": [10, 12, 13],
        }
    )
    assert atr(frame, 2) == 2.5


def test_linear_regression_r2_perfect_fit():
    slope, r2 = linear_regression_r2(np.array([1.0, 2.0, 3.0]))
    assert round(slope, 6) == 1
    assert r2 == 1
