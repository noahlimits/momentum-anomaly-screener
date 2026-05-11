from pathlib import Path

import pandas as pd

from src.config import AppConfig
from src.database import Database
from src.data_provider import DataProvider
from src.recommendations import create_portfolio_from_run, run_strategy
from src.report_excel import write_workbook


class FakeProvider(DataProvider):
    def get_price_history(self, tickers: list[str], period: str = "420d") -> dict[str, pd.DataFrame]:
        return {ticker: _frame(ticker) for ticker in tickers}


def test_initial_portfolio_is_not_saved_until_accepted(tmp_path: Path):
    config, db = _setup(tmp_path)
    result = run_strategy(db, config, FakeProvider(), "initial", 10000, "test")

    assert db.active_holdings("test") == []

    portfolio_id = create_portfolio_from_run(db, result.run_id, "Test Portfolio")
    holdings = db.active_holdings("test", portfolio_id=portfolio_id)

    assert portfolio_id > 0
    assert holdings
    assert all(holding["portfolio_id"] == portfolio_id for holding in holdings)


def test_saved_portfolios_do_not_mix_holdings(tmp_path: Path):
    config, db = _setup(tmp_path)
    first = run_strategy(db, config, FakeProvider(), "initial", 10000, "test")
    first_id = create_portfolio_from_run(db, first.run_id, "First")
    second = run_strategy(db, config, FakeProvider(), "initial", 20000, "test")
    second_id = create_portfolio_from_run(db, second.run_id, "Second")

    first_holdings = db.active_holdings("test", portfolio_id=first_id)
    second_holdings = db.active_holdings("test", portfolio_id=second_id)

    assert first_id != second_id
    assert first_holdings
    assert second_holdings
    assert {item["portfolio_id"] for item in first_holdings} == {first_id}
    assert {item["portfolio_id"] for item in second_holdings} == {second_id}


def test_review_uses_calculated_value_plus_cash_adjustment(tmp_path: Path):
    config, db = _setup(tmp_path)
    initial = run_strategy(db, config, FakeProvider(), "initial", 10000, "test")
    portfolio_id = create_portfolio_from_run(db, initial.run_id, "Cash Test")

    review = run_strategy(
        db,
        config,
        FakeProvider(),
        mode="review",
        portfolio_value=None,
        universe_id="test",
        portfolio_id=portfolio_id,
        cash_adjustment=10000,
    )

    assert review.calculated_portfolio_value is not None
    assert review.portfolio_value == review.calculated_portfolio_value + 10000
    assert any(rec.action in {"HOLD", "RESIZE_UP", "RESIZE_DOWN"} for rec in review.recommendations)


def test_review_outputs_exact_share_changes_without_threshold(tmp_path: Path):
    config, db = _setup(tmp_path)
    initial = run_strategy(db, config, FakeProvider(), "initial", 10000, "test")
    portfolio_id = create_portfolio_from_run(db, initial.run_id, "Exact Review")

    review = run_strategy(
        db,
        config,
        FakeProvider(),
        mode="review",
        portfolio_value=None,
        universe_id="test",
        portfolio_id=portfolio_id,
        cash_adjustment=5000,
    )

    changed = [rec for rec in review.recommendations if rec.action in {"RESIZE_UP", "RESIZE_DOWN", "EXIT", "ADD"}]
    assert changed
    assert all(rec.target_shares != rec.current_shares for rec in changed)


def test_portfolio_workbook_path_is_stable(tmp_path: Path):
    config, db = _setup(tmp_path)
    initial = run_strategy(db, config, FakeProvider(), "initial", 10000, "test")
    portfolio_id = create_portfolio_from_run(db, initial.run_id, "Workbook Test")

    first_review = run_strategy(db, config, FakeProvider(), "review", None, "test", portfolio_id=portfolio_id)
    first_path = write_workbook(first_review, db, config)
    second_review = run_strategy(db, config, FakeProvider(), "review", None, "test", portfolio_id=portfolio_id)
    second_path = write_workbook(second_review, db, config)

    assert first_path == second_path
    assert first_path.exists()
    assert "Workbook_Test" in first_path.name


def _setup(tmp_path: Path) -> tuple[AppConfig, Database]:
    universe_dir = tmp_path / "data" / "universe_csv"
    universe_dir.mkdir(parents=True)
    (universe_dir / "test.csv").write_text("ticker,company_name,sector\nAAA,AAA Inc,Tech\nBBB,BBB Inc,Tech\n", encoding="utf-8")
    config = AppConfig(
        root_dir=tmp_path,
        database_path=tmp_path / "data" / "state.sqlite",
        reports_dir=tmp_path / "reports",
        cache_dir=tmp_path / "data" / "cache",
        settings={
            "selected_universe_id": "test",
            "momentum_lookback_days": 90,
            "atr_lookback_days": 20,
            "stock_ma_days": 100,
            "regime_ma_days": 200,
            "gap_threshold": 0.15,
            "rank_cutoff_percent": 0.50,
            "allow_fractional_shares": False,
            "rebalance_threshold": 0.10,
        },
        universe_profiles=[
            {
                "universe_id": "test",
                "display_name": "Test Universe",
                "enabled": True,
                "default_profile": True,
                "constituent_source_type": "static_csv",
                "constituent_source": "data/universe_csv/test.csv",
                "regime_proxy": "SPY",
                "regime_ma_days": 200,
                "currency": "USD",
                "exchange_scope": "Test",
                "notes": "",
            }
        ],
    )
    config.reports_dir.mkdir(parents=True)
    config.cache_dir.mkdir(parents=True)
    db = Database(config.database_path)
    db.initialize(config)
    return config, db


def _frame(ticker: str) -> pd.DataFrame:
    days = 260
    base = {"AAA": 50, "BBB": 30, "SPY": 100}.get(ticker, 20)
    growth = {"AAA": 1.003, "BBB": 1.001, "SPY": 1.001}.get(ticker, 1.001)
    close = [base * (growth ** index) for index in range(days)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [value * 1.01 for value in close],
            "low": [value * 0.99 for value in close],
            "close": close,
            "adj_close": close,
            "volume": [1000] * days,
        },
        index=pd.date_range("2025-01-01", periods=days, freq="B"),
    )
