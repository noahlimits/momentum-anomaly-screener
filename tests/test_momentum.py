import pandas as pd

from src.momentum import calculate_scores


def _frame(start_price: float, days: int = 130) -> pd.DataFrame:
    close = [start_price * (1.002 ** index) for index in range(days)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [value * 1.01 for value in close],
            "low": [value * 0.99 for value in close],
            "close": close,
            "adj_close": close,
            "volume": [1000] * days,
        }
    )


def test_calculate_scores_marks_top_candidate_eligible_when_regime_allows():
    scores = calculate_scores(
        {"AAA": _frame(10), "BBB": _frame(20)},
        {"AAA": {"company_name": "A"}, "BBB": {"company_name": "B"}},
        {
            "momentum_lookback_days": 90,
            "atr_lookback_days": 20,
            "stock_ma_days": 100,
            "gap_threshold": 0.15,
            "rank_cutoff_percent": 0.50,
        },
        regime_allows_new_buys=True,
    )
    valid = [score for score in scores if score.data_status == "OK"]
    assert len(valid) == 2
    assert sum(score.eligible for score in valid) == 1
    assert [score.qualified_rank for score in valid if score.eligible] == [1]
