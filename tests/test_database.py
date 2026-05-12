from pathlib import Path

from src.config import AppConfig
from src.database import Database


def test_database_seeds_universe_sort_order(tmp_path: Path):
    config = AppConfig(
        root_dir=tmp_path,
        database_path=tmp_path / "state.sqlite",
        reports_dir=tmp_path / "reports",
        cache_dir=tmp_path / "cache",
        settings={},
        universe_profiles=[
            _profile("second", "Second", 2),
            _profile("first", "First", 1),
        ],
    )
    db = Database(config.database_path)
    db.initialize(config)

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT universe_id, sort_order FROM universe_profiles ORDER BY sort_order"
        ).fetchall()

    assert [(row["universe_id"], row["sort_order"]) for row in rows] == [("first", 1), ("second", 2)]


def _profile(universe_id: str, display_name: str, sort_order: int) -> dict:
    return {
        "universe_id": universe_id,
        "display_name": display_name,
        "sort_order": sort_order,
        "enabled": True,
        "default_profile": False,
        "constituent_source_type": "static_csv",
        "constituent_source": "data/universe_csv/test.csv",
        "regime_proxy": "SPY",
        "regime_ma_days": 200,
        "currency": "USD",
        "exchange_scope": "Test",
        "notes": "",
    }
