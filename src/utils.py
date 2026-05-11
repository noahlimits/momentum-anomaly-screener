from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def as_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(float(value))


def safe_ticker_for_yfinance(ticker: str) -> str:
    return ticker.strip().upper().replace(".", "-")


def report_filename(universe_id: str, mode: str, reports_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return reports_dir / f"momentum_anomaly_{universe_id}_{mode}_{stamp}.xlsx"


def safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "portfolio"
