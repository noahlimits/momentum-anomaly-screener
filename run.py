from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import AppConfig
from src.database import Database
from src.data_provider import YFinanceProvider
from src.portfolio import import_portfolio_csv
from src.recommendations import accept_recommendations, run_strategy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Momentum Anomaly Screener and mirror portfolio tool.")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or update SQLite schema and seed defaults.")

    run_parser = subparsers.add_parser("run", help="Generate an initial, review, or report-only workbook.")
    run_parser.add_argument("--mode", choices=["initial", "review", "report-only"], default="review")
    run_parser.add_argument("--portfolio-value", type=float, default=None)
    run_parser.add_argument("--universe", default=None)
    run_parser.add_argument("--output", default=None, help="Optional workbook output path.")
    run_parser.add_argument("--accept-all", action="store_true", help="Accept generated recommendations after report creation.")

    accept_parser = subparsers.add_parser("accept", help="Apply recommendations to the mirror portfolio.")
    accept_group = accept_parser.add_mutually_exclusive_group(required=True)
    accept_group.add_argument("--latest", action="store_true", help="Accept all recommendations from the latest run.")
    accept_group.add_argument("--run-id", type=int, help="Accept recommendations from a specific run.")
    accept_parser.add_argument("--ids", default=None, help="Comma-separated recommendation IDs to accept.")

    import_parser = subparsers.add_parser("import-portfolio", help="Replace active mirror holdings from CSV.")
    import_parser.add_argument("--csv", required=True)
    import_parser.add_argument("--universe", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AppConfig.load(args.config)
    db = Database(config.database_path)

    if args.command == "init-db":
        db.initialize(config)
        print(f"Initialized database: {config.database_path}")
        return 0

    db.initialize(config)

    if args.command == "import-portfolio":
        universe_id = args.universe or config.settings["selected_universe_id"]
        count = import_portfolio_csv(db, Path(args.csv), universe_id)
        print(f"Imported {count} active mirror position(s).")
        return 0

    if args.command == "accept":
        accepted = accept_recommendations(
            db,
            latest=args.latest,
            run_id=args.run_id,
            recommendation_ids=_parse_ids(args.ids),
        )
        print(f"Accepted {accepted} recommendation(s) and updated mirror portfolio.")
        return 0

    if args.command == "run":
        from src.report_excel import write_workbook

        provider = YFinanceProvider(config.cache_dir)
        result = run_strategy(
            db=db,
            config=config,
            data_provider=provider,
            mode=args.mode,
            portfolio_value=args.portfolio_value,
            universe_id=args.universe,
        )
        output_path = Path(args.output) if args.output else None
        workbook_path = write_workbook(result, db, config, output_path)
        if args.accept_all:
            accepted = accept_recommendations(db, run_id=result.run_id)
            print(f"Accepted {accepted} recommendation(s).")
        print(f"Report written: {workbook_path}")
        print(f"Run ID: {result.run_id}")
        return 0

    return 1


def _parse_ids(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
