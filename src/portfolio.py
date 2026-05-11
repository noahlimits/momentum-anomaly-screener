from __future__ import annotations

from pathlib import Path

import pandas as pd

from .database import Database, utc_now
from .utils import safe_ticker_for_yfinance


def import_portfolio_csv(db: Database, path: Path, universe_id: str, portfolio_id: int | None = None) -> int:
    frame = pd.read_csv(path)
    columns = {column.lower().strip(): column for column in frame.columns}
    if "ticker" not in columns or "shares" not in columns:
        raise ValueError("Portfolio CSV must include ticker and shares columns.")
    now = utc_now()
    with db.connect() as conn:
        if portfolio_id is None:
            conn.execute("UPDATE mirror_portfolio SET active = 0, updated_at = ? WHERE universe_id = ?", (now, universe_id))
        else:
            conn.execute(
                "UPDATE mirror_portfolio SET active = 0, updated_at = ? WHERE universe_id = ? AND portfolio_id = ?",
                (now, universe_id, portfolio_id),
            )
        count = 0
        for _, row in frame.iterrows():
            ticker = safe_ticker_for_yfinance(str(row[columns["ticker"]]))
            shares = float(row[columns["shares"]])
            if not ticker or shares <= 0:
                continue
            entry_date = _optional(row, columns, "entry_date")
            entry_price = _optional_float(row, columns, "entry_price")
            notes = _optional(row, columns, "notes")
            conn.execute(
                """
                INSERT INTO mirror_portfolio(
                    ticker, shares, entry_date, entry_price, universe_id, portfolio_id,
                    manual_override, active, notes, last_reviewed, last_action,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, NULL, 'IMPORT', ?, ?)
                """,
                (ticker, shares, entry_date, entry_price, universe_id, portfolio_id, notes, now, now),
            )
            count += 1
    return count


def _optional(row: pd.Series, columns: dict[str, str], name: str) -> str | None:
    column = columns.get(name)
    if not column:
        return None
    value = row.get(column)
    return None if pd.isna(value) else str(value)


def _optional_float(row: pd.Series, columns: dict[str, str], name: str) -> float | None:
    value = _optional(row, columns, name)
    return None if value is None or value == "" else float(value)
