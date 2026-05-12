from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self, config: Any) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_existing_schema(conn)
            self._seed_settings(conn, config.settings)
            self._seed_universe_profiles(conn, config.universe_profiles)

    def latest_run_id(self, portfolio_id: int | None = None) -> int | None:
        with self.connect() as conn:
            if portfolio_id is None:
                row = conn.execute("SELECT MAX(run_id) AS run_id FROM run_log").fetchone()
            else:
                row = conn.execute(
                    "SELECT MAX(run_id) AS run_id FROM run_log WHERE portfolio_id = ?",
                    (portfolio_id,),
                ).fetchone()
            return row["run_id"] if row and row["run_id"] is not None else None

    def settings(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT setting_key, setting_value FROM settings").fetchall()
        return {row["setting_key"]: row["setting_value"] for row in rows}

    def universe_profile(self, universe_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM universe_profiles WHERE universe_id = ?",
                (universe_id,),
            ).fetchone()
        if not row:
            raise ValueError(f"Unknown universe profile: {universe_id}")
        return dict(row)

    def active_holdings(self, universe_id: str | None = None, portfolio_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mirror_portfolio WHERE active = 1"
        params: list[Any] = []
        if portfolio_id is not None:
            sql += " AND portfolio_id = ?"
            params.append(portfolio_id)
        if universe_id:
            sql += " AND universe_id = ?"
            params.append(universe_id)
        sql += " ORDER BY ticker"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    def portfolios(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*,
                       COUNT(mp.position_id) AS active_positions
                FROM portfolios p
                LEFT JOIN mirror_portfolio mp
                  ON mp.portfolio_id = p.portfolio_id
                 AND mp.active = 1
                WHERE p.active = 1
                GROUP BY p.portfolio_id
                ORDER BY COALESCE(p.latest_reviewed_at, p.created_at) DESC, p.name
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def portfolio(self, portfolio_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM portfolios WHERE portfolio_id = ?",
                (portfolio_id,),
            ).fetchone()
        if not row:
            raise ValueError(f"Unknown portfolio: {portfolio_id}")
        return dict(row)

    def create_portfolio(
        self,
        name: str,
        universe_id: str,
        initial_portfolio_value: float,
        workbook_path: str | None = None,
        target_positions: int = 10,
    ) -> int:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO portfolios(
                    name, universe_id, target_positions, initial_portfolio_value, latest_portfolio_value,
                    latest_workbook_path, active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (name, universe_id, target_positions, initial_portfolio_value, initial_portfolio_value, workbook_path, now, now),
            )
            return int(cursor.lastrowid)

    def update_portfolio_review(
        self,
        portfolio_id: int,
        latest_portfolio_value: float | None = None,
        latest_workbook_path: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE portfolios
                SET latest_portfolio_value = COALESCE(?, latest_portfolio_value),
                    latest_workbook_path = COALESCE(?, latest_workbook_path),
                    latest_reviewed_at = ?,
                    updated_at = ?
                WHERE portfolio_id = ?
                """,
                (latest_portfolio_value, latest_workbook_path, now, now, portfolio_id),
            )

    def _migrate_existing_schema(self, conn: sqlite3.Connection) -> None:
        columns_by_table = {
            table: {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for table in ["portfolios", "mirror_portfolio", "run_log", "security_scores", "recommendations"]
        }
        universe_columns = {row["name"] for row in conn.execute("PRAGMA table_info(universe_profiles)").fetchall()}
        additions = {
            "universe_profiles": {
                "sort_order": "ALTER TABLE universe_profiles ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 999",
            },
            "portfolios": {
                "target_positions": "ALTER TABLE portfolios ADD COLUMN target_positions INTEGER NOT NULL DEFAULT 10",
            },
            "mirror_portfolio": {
                "portfolio_id": "ALTER TABLE mirror_portfolio ADD COLUMN portfolio_id INTEGER",
            },
            "run_log": {
                "portfolio_id": "ALTER TABLE run_log ADD COLUMN portfolio_id INTEGER",
                "cash_adjustment": "ALTER TABLE run_log ADD COLUMN cash_adjustment REAL NOT NULL DEFAULT 0",
            },
            "security_scores": {
                "portfolio_id": "ALTER TABLE security_scores ADD COLUMN portfolio_id INTEGER",
                "qualified_rank": "ALTER TABLE security_scores ADD COLUMN qualified_rank INTEGER",
            },
            "recommendations": {
                "portfolio_id": "ALTER TABLE recommendations ADD COLUMN portfolio_id INTEGER",
                "target_rank": "ALTER TABLE recommendations ADD COLUMN target_rank INTEGER",
            },
        }
        columns_by_table["universe_profiles"] = universe_columns
        for table, table_additions in additions.items():
            for column, sql in table_additions.items():
                if column not in columns_by_table[table]:
                    conn.execute(sql)

    def _seed_settings(self, conn: sqlite3.Connection, settings: dict[str, Any]) -> None:
        now = utc_now()
        for key, value in settings.items():
            conn.execute(
                """
                INSERT OR IGNORE INTO settings(setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, str(value), now),
            )

    def _seed_universe_profiles(self, conn: sqlite3.Connection, profiles: Iterable[dict[str, Any]]) -> None:
        now = utc_now()
        for profile in profiles:
            conn.execute(
                """
                INSERT INTO universe_profiles(
                    universe_id, display_name, sort_order, enabled, default_profile,
                    constituent_source_type, constituent_source, regime_proxy,
                    regime_ma_days, currency, exchange_scope, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(universe_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    sort_order = excluded.sort_order,
                    enabled = excluded.enabled,
                    default_profile = excluded.default_profile,
                    constituent_source_type = excluded.constituent_source_type,
                    constituent_source = excluded.constituent_source,
                    regime_proxy = excluded.regime_proxy,
                    regime_ma_days = excluded.regime_ma_days,
                    currency = excluded.currency,
                    exchange_scope = excluded.exchange_scope,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    profile["universe_id"],
                    profile.get("display_name", profile["universe_id"]),
                    int(profile.get("sort_order", 999)),
                    int(bool(profile.get("enabled", True))),
                    int(bool(profile.get("default_profile", False))),
                    profile.get("constituent_source_type", "static_csv"),
                    profile.get("constituent_source", ""),
                    profile.get("regime_proxy", "SPY"),
                    int(profile.get("regime_ma_days", 200)),
                    profile.get("currency", "USD"),
                    profile.get("exchange_scope", ""),
                    profile.get("notes", ""),
                    now,
                ),
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_profiles (
    universe_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 999,
    enabled INTEGER NOT NULL DEFAULT 1,
    default_profile INTEGER NOT NULL DEFAULT 0,
    constituent_source_type TEXT NOT NULL,
    constituent_source TEXT NOT NULL,
    regime_proxy TEXT NOT NULL,
    regime_ma_days INTEGER NOT NULL DEFAULT 200,
    currency TEXT NOT NULL DEFAULT 'USD',
    exchange_scope TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolios (
    portfolio_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    universe_id TEXT NOT NULL,
    target_positions INTEGER NOT NULL DEFAULT 10,
    initial_portfolio_value REAL NOT NULL,
    latest_portfolio_value REAL,
    latest_workbook_path TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    latest_reviewed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mirror_portfolio (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER,
    ticker TEXT NOT NULL,
    shares REAL NOT NULL,
    entry_date TEXT,
    entry_price REAL,
    universe_id TEXT NOT NULL,
    manual_override INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    last_reviewed TEXT,
    last_action TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER,
    run_datetime TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    portfolio_value REAL NOT NULL,
    regime_proxy TEXT NOT NULL,
    regime_status TEXT NOT NULL,
    holdings_reviewed INTEGER NOT NULL,
    exits_flagged INTEGER NOT NULL,
    additions_suggested INTEGER NOT NULL,
    resize_flags INTEGER NOT NULL,
    data_errors INTEGER NOT NULL,
    cash_adjustment REAL NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS security_scores (
    run_id INTEGER NOT NULL,
    portfolio_id INTEGER,
    universe_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    sector TEXT,
    price REAL,
    rank INTEGER,
    qualified_rank INTEGER,
    percentile_rank REAL,
    momentum_score REAL,
    annualized_slope REAL,
    r_squared REAL,
    atr20 REAL,
    ma100 REAL,
    above_100dma INTEGER,
    gap_max_abs_move REAL,
    gap_pass INTEGER,
    top_20pct INTEGER,
    in_universe INTEGER,
    eligible INTEGER,
    data_status TEXT NOT NULL,
    PRIMARY KEY(run_id, ticker)
);

CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    portfolio_id INTEGER,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    current_shares REAL NOT NULL,
    target_shares REAL NOT NULL,
    share_change REAL NOT NULL,
    current_price REAL,
    target_value REAL,
    target_weight REAL,
    target_rank INTEGER,
    reason TEXT,
    universe_id TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""
