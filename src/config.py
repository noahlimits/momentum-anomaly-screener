from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    database_path: Path
    reports_dir: Path
    cache_dir: Path
    settings: dict[str, Any]
    universe_profiles: list[dict[str, Any]]

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path).resolve()
        root_dir = config_path.parent
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}

        database_path = _resolve(root_dir, raw.get("database_path", "data/momentum_anomaly_state.sqlite"))
        reports_dir = _resolve(root_dir, raw.get("reports_dir", "reports"))
        cache_dir = _resolve(root_dir, raw.get("cache_dir", "data/cache"))
        reports_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        database_path.parent.mkdir(parents=True, exist_ok=True)

        return cls(
            root_dir=root_dir,
            database_path=database_path,
            reports_dir=reports_dir,
            cache_dir=cache_dir,
            settings=raw.get("settings", {}),
            universe_profiles=raw.get("universe_profiles", []),
        )


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path
