from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .database import Database, utc_now
from .data_provider import DataProvider
from .momentum import SecurityScore, calculate_scores
from .regime import RegimeStatus, calculate_regime
from .sizing import risk_parity_targets, target_value, target_weight
from .universe import Constituent, load_constituents, metadata_from_constituents
from .utils import as_bool, as_float, as_int


@dataclass
class Recommendation:
    portfolio_id: int | None
    ticker: str
    action: str
    current_shares: float
    target_shares: float
    share_change: float
    current_price: float | None
    target_value: float
    target_weight: float
    reason: str
    universe_id: str


@dataclass
class StrategyResult:
    run_id: int
    portfolio_id: int | None
    mode: str
    universe_id: str
    universe_profile: dict[str, Any]
    portfolio_value: float
    target_positions: int
    cash_adjustment: float
    calculated_portfolio_value: float | None
    regime: RegimeStatus
    constituents: list[Constituent]
    scores: list[SecurityScore]
    recommendations: list[Recommendation]
    holdings: list[dict[str, Any]]
    settings: dict[str, Any]


def run_strategy(
    db: Database,
    config: AppConfig,
    data_provider: DataProvider,
    mode: str,
    portfolio_value: float | None,
    universe_id: str | None,
    portfolio_id: int | None = None,
    cash_adjustment: float = 0.0,
    target_positions: int | None = None,
    persist: bool = True,
) -> StrategyResult:
    db_settings = db.settings()
    settings = {**config.settings, **db_settings}
    portfolio = db.portfolio(portfolio_id) if portfolio_id is not None else None
    selected_universe = universe_id or (portfolio["universe_id"] if portfolio else str(settings.get("selected_universe_id", "sp500")))
    selected_value = as_float(portfolio_value, as_float(settings.get("portfolio_value"), 10000))
    selected_target_positions = int(target_positions or (portfolio["target_positions"] if portfolio else settings.get("target_positions", 10)))
    profile = db.universe_profile(selected_universe)
    constituents = load_constituents(profile, config.root_dir)
    metadata = metadata_from_constituents(constituents)
    tickers = [item.ticker for item in constituents]
    holdings = db.active_holdings(selected_universe, portfolio_id=portfolio_id)
    held_tickers = [holding["ticker"] for holding in holdings]

    proxy = _regime_proxy(profile, settings)
    history = data_provider.get_price_history(sorted(set(tickers + held_tickers + [proxy])), period="420d")
    regime = calculate_regime(proxy, history.get(proxy), as_int(profile.get("regime_ma_days"), 200))
    market_data = {ticker: history.get(ticker) for ticker in tickers}
    scores = calculate_scores(market_data, metadata, settings, regime.allows_new_buys)
    holding_prices = {ticker: _latest_price_from_history(history.get(ticker)) for ticker in held_tickers}
    calculated_value = _calculate_current_portfolio_value(holdings, scores, holding_prices) if portfolio_id is not None else None
    if portfolio_id is not None and portfolio_value is None:
        selected_value = max(0.0, (calculated_value or 0.0) + cash_adjustment)
    recommendations = _build_recommendations(mode, portfolio_id, selected_universe, selected_value, selected_target_positions, scores, holdings, holding_prices, settings, regime)
    run_id = _persist_run(db, portfolio_id, selected_universe, selected_value, cash_adjustment, profile, regime, scores, recommendations, holdings) if persist else 0
    return StrategyResult(
        run_id=run_id,
        portfolio_id=portfolio_id,
        mode=mode,
        universe_id=selected_universe,
        universe_profile=profile,
        portfolio_value=selected_value,
        target_positions=selected_target_positions,
        cash_adjustment=cash_adjustment,
        calculated_portfolio_value=calculated_value,
        regime=regime,
        constituents=constituents,
        scores=scores,
        recommendations=recommendations,
        holdings=holdings,
        settings=settings,
    )


def accept_recommendations(
    db: Database,
    latest: bool = False,
    run_id: int | None = None,
    recommendation_ids: list[int] | None = None,
) -> int:
    selected_run_id = db.latest_run_id() if latest else run_id
    if selected_run_id is None:
        raise ValueError("No run_id supplied and no latest run exists.")
    params: list[Any] = [selected_run_id]
    sql = "SELECT * FROM recommendations WHERE run_id = ? AND accepted = 0"
    if recommendation_ids:
        placeholders = ",".join("?" for _ in recommendation_ids)
        sql += f" AND recommendation_id IN ({placeholders})"
        params.extend(recommendation_ids)
    now = utc_now()
    accepted = 0
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            action = row["action"]
            if action in {"BUY", "ADD", "RESIZE_UP", "RESIZE_DOWN", "HOLD"} and row["target_shares"] > 0:
                _upsert_holding(conn, row, now)
            elif action == "EXIT":
                conn.execute(
                    """
                    UPDATE mirror_portfolio
                    SET active = 0, last_reviewed = ?, last_action = 'EXIT',
                        updated_at = ?
                    WHERE ticker = ? AND universe_id = ? AND active = 1
                      AND (portfolio_id IS ? OR portfolio_id = ?)
                    """,
                    (now, now, row["ticker"], row["universe_id"], row["portfolio_id"], row["portfolio_id"]),
                )
            conn.execute(
                "UPDATE recommendations SET accepted = 1 WHERE recommendation_id = ?",
                (row["recommendation_id"],),
            )
            accepted += 1
        if rows and rows[0]["portfolio_id"] is not None:
            portfolio_id = int(rows[0]["portfolio_id"])
            value_row = conn.execute(
                "SELECT portfolio_value FROM run_log WHERE run_id = ?",
                (selected_run_id,),
            ).fetchone()
            conn.execute(
                """
                UPDATE portfolios
                SET latest_portfolio_value = ?,
                    latest_reviewed_at = ?,
                    updated_at = ?
                WHERE portfolio_id = ?
                """,
                (value_row["portfolio_value"] if value_row else None, now, now, portfolio_id),
            )
    return accepted


def create_portfolio_from_run(
    db: Database,
    run_id: int,
    name: str,
    workbook_path: str | None = None,
    target_positions: int = 10,
) -> int:
    with db.connect() as conn:
        run = conn.execute("SELECT * FROM run_log WHERE run_id = ?", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"Unknown run_id: {run_id}")
    portfolio_id = db.create_portfolio(name, run["universe_id"], float(run["portfolio_value"]), workbook_path, target_positions)
    now = utc_now()
    with db.connect() as conn:
        conn.execute("UPDATE run_log SET portfolio_id = ? WHERE run_id = ?", (portfolio_id, run_id))
        conn.execute("UPDATE security_scores SET portfolio_id = ? WHERE run_id = ?", (portfolio_id, run_id))
        conn.execute("UPDATE recommendations SET portfolio_id = ? WHERE run_id = ?", (portfolio_id, run_id))
        conn.execute(
            "UPDATE portfolios SET latest_reviewed_at = ?, updated_at = ? WHERE portfolio_id = ?",
            (now, now, portfolio_id),
        )
    accept_recommendations(db, run_id=run_id)
    return portfolio_id


def _build_recommendations(
    mode: str,
    portfolio_id: int | None,
    universe_id: str,
    portfolio_value: float,
    target_positions: int,
    scores: list[SecurityScore],
    holdings: list[dict[str, Any]],
    holding_prices: dict[str, float | None],
    settings: dict[str, Any],
    regime: RegimeStatus,
) -> list[Recommendation]:
    allow_fractional = as_bool(settings.get("allow_fractional_shares"))
    by_ticker = {score.ticker: score for score in scores}
    recommendations: list[Recommendation] = []

    if mode == "initial":
        return _initial_recommendations(portfolio_id, universe_id, portfolio_value, target_positions, scores, allow_fractional, regime)

    held_tickers = {holding["ticker"] for holding in holdings}
    holding_reviews: list[tuple[dict[str, Any], SecurityScore | None, str, str]] = []
    target_by_ticker: dict[str, SecurityScore] = {}
    for holding in holdings:
        ticker = holding["ticker"]
        score = by_ticker.get(ticker)
        action, reason = _holding_action(score, float(holding["shares"]))
        holding_reviews.append((holding, score, action, reason))
        if action == "HOLD" and score is not None:
            target_by_ticker[score.ticker] = score

    if regime.allows_new_buys:
        for score in [item for item in scores if item.eligible and item.ticker not in held_tickers]:
            if len(target_by_ticker) >= target_positions:
                break
            target_by_ticker[score.ticker] = score

    target_scores = sorted(target_by_ticker.values(), key=lambda score: score.rank or 999999)[:target_positions]
    allocations = _allocation_map(portfolio_value, target_scores, allow_fractional)
    target_tickers = set(allocations)

    for holding, score, action, reason in holding_reviews:
        ticker = holding["ticker"]
        current_shares = float(holding["shares"])
        price = score.price if score else holding_prices.get(ticker)
        allocation = allocations.get(ticker)
        if action == "HOLD":
            if ticker not in target_tickers:
                action = "EXIT"
                reason = "Outside the selected target count after momentum rank ordering."
                target = 0.0
            elif allocation is None or allocation.shares <= 0:
                action = "EXIT"
                reason = "No whole-share target after ATR risk-parity sizing."
                target = 0.0
            else:
                target = allocation.shares
                if target > current_shares:
                    action = "RESIZE_UP"
                    reason = "Increase to ATR risk-parity target."
                elif target < current_shares:
                    action = "RESIZE_DOWN"
                    reason = "Reduce to ATR risk-parity target."
                else:
                    reason = "Still satisfies hold rules and ATR risk-parity target."
        else:
            target = 0.0 if action in {"EXIT", "DATA_ERROR"} else current_shares

        value = target_value(target, price)
        recommendations.append(
            Recommendation(
                portfolio_id=portfolio_id,
                ticker=ticker,
                action=action,
                current_shares=current_shares,
                target_shares=target,
                share_change=target - current_shares,
                current_price=price,
                target_value=value,
                target_weight=target_weight(value, portfolio_value),
                reason=reason,
                universe_id=universe_id,
            )
        )

    for score in target_scores:
        if score.ticker in held_tickers:
            continue
        allocation = allocations.get(score.ticker)
        shares = allocation.shares if allocation else 0.0
        action = "ADD" if shares > 0 else "NO_CASH"
        reason = "Eligible replacement candidate sized by ATR risk parity." if shares > 0 else "No whole-share target after ATR risk-parity sizing."
        recommendations.append(_candidate_recommendation(score, portfolio_id, universe_id, shares, action, reason, portfolio_value))
    return recommendations


def _initial_recommendations(
    portfolio_id: int | None,
    universe_id: str,
    portfolio_value: float,
    target_positions: int,
    scores: list[SecurityScore],
    allow_fractional: bool,
    regime: RegimeStatus,
) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    cash = portfolio_value
    if not regime.allows_new_buys:
        for score in [item for item in scores if item.top_20pct]:
            recommendations.append(_candidate_recommendation(score, portfolio_id, universe_id, 0.0, "BLOCKED_BY_REGIME", "Regime proxy is below its moving average.", portfolio_value))
        return recommendations

    selected = [item for item in scores if item.eligible][:target_positions]
    allocations = _allocation_map(portfolio_value, selected, allow_fractional)
    for score in selected:
        allocation = allocations.get(score.ticker)
        desired = allocation.shares if allocation else 0.0
        if desired > 0:
            action, reason = "BUY", "Eligible initial portfolio candidate sized by ATR risk parity."
            cash -= target_value(desired, score.price)
            recommendations.append(_candidate_recommendation(score, portfolio_id, universe_id, desired, action, reason, portfolio_value))
    return recommendations


def _allocation_map(portfolio_value: float, scores: list[SecurityScore], allow_fractional: bool) -> dict[str, Any]:
    candidates = [(score.ticker, score.price, score.atr20) for score in scores]
    return {target.ticker: target for target in risk_parity_targets(portfolio_value, candidates, allow_fractional)}


def _holding_action(score: SecurityScore | None, current_shares: float) -> tuple[str, str]:
    if score is None:
        return "EXIT", "Ticker is no longer in the selected universe."
    if score.data_status != "OK":
        return "DATA_ERROR", f"Data status is {score.data_status}."
    if not score.above_100dma:
        return "EXIT", "Below 100-day moving average."
    if not score.gap_pass:
        return "EXIT", "Single-day move exceeded threshold in lookback window."
    if not score.top_20pct:
        return "EXIT", "Dropped out of top 20% momentum rank."
    return "HOLD", "Still satisfies stock-level hold rules."


def _candidate_recommendation(
    score: SecurityScore,
    portfolio_id: int | None,
    universe_id: str,
    shares: float,
    action: str,
    reason: str,
    portfolio_value: float,
) -> Recommendation:
    value = target_value(shares, score.price)
    return Recommendation(
        portfolio_id=portfolio_id,
        ticker=score.ticker,
        action=action,
        current_shares=0.0,
        target_shares=shares,
        share_change=shares,
        current_price=score.price,
        target_value=value,
        target_weight=target_weight(value, portfolio_value),
        reason=reason,
        universe_id=universe_id,
    )


def _persist_run(
    db: Database,
    portfolio_id: int | None,
    universe_id: str,
    portfolio_value: float,
    cash_adjustment: float,
    profile: dict[str, Any],
    regime: RegimeStatus,
    scores: list[SecurityScore],
    recommendations: list[Recommendation],
    holdings: list[dict[str, Any]],
) -> int:
    now = utc_now()
    exits = sum(1 for item in recommendations if item.action == "EXIT")
    additions = sum(1 for item in recommendations if item.action in {"BUY", "ADD"})
    resize = sum(1 for item in recommendations if item.action in {"RESIZE_UP", "RESIZE_DOWN"})
    errors = sum(1 for item in recommendations if item.action == "DATA_ERROR") + sum(1 for item in scores if item.data_status != "OK")
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO run_log(
                portfolio_id, run_datetime, universe_id, portfolio_value, regime_proxy, regime_status,
                holdings_reviewed, exits_flagged, additions_suggested, resize_flags,
                data_errors, cash_adjustment, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                portfolio_id,
                now,
                universe_id,
                portfolio_value,
                profile["regime_proxy"],
                regime.status,
                len(holdings),
                exits,
                additions,
                resize,
                errors,
                cash_adjustment,
                "",
            ),
        )
        run_id = int(cursor.lastrowid)
        for score in scores:
            conn.execute(
                """
                INSERT INTO security_scores(
                    run_id, portfolio_id, universe_id, ticker, company_name, sector, price, rank,
                    percentile_rank, momentum_score, annualized_slope, r_squared,
                    atr20, ma100, above_100dma, gap_max_abs_move, gap_pass,
                    top_20pct, in_universe, eligible, data_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    portfolio_id,
                    universe_id,
                    score.ticker,
                    score.company_name,
                    score.sector,
                    score.price,
                    score.rank,
                    score.percentile_rank,
                    score.momentum_score,
                    score.annualized_slope,
                    score.r_squared,
                    score.atr20,
                    score.ma100,
                    int(score.above_100dma),
                    score.gap_max_abs_move,
                    int(score.gap_pass),
                    int(score.top_20pct),
                    int(score.in_universe),
                    int(score.eligible),
                    score.data_status,
                ),
            )
        for rec in recommendations:
            conn.execute(
                """
                INSERT INTO recommendations(
                    run_id, portfolio_id, ticker, action, current_shares, target_shares,
                    share_change, current_price, target_value, target_weight,
                    reason, universe_id, accepted, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    run_id,
                    rec.portfolio_id,
                    rec.ticker,
                    rec.action,
                    rec.current_shares,
                    rec.target_shares,
                    rec.share_change,
                    rec.current_price,
                    rec.target_value,
                    rec.target_weight,
                    rec.reason,
                    rec.universe_id,
                    now,
                ),
            )
    return run_id


def _upsert_holding(conn: Any, row: Any, now: str) -> None:
    existing = conn.execute(
        """
        SELECT position_id FROM mirror_portfolio
        WHERE ticker = ? AND universe_id = ? AND active = 1
          AND (portfolio_id IS ? OR portfolio_id = ?)
        """,
        (row["ticker"], row["universe_id"], row["portfolio_id"], row["portfolio_id"]),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE mirror_portfolio
            SET shares = ?, entry_price = COALESCE(entry_price, ?),
                last_reviewed = ?, last_action = ?, updated_at = ?
            WHERE position_id = ?
            """,
            (row["target_shares"], row["current_price"], now, row["action"], now, existing["position_id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO mirror_portfolio(
                ticker, shares, entry_date, entry_price, universe_id,
                portfolio_id, manual_override, active, notes, last_reviewed, last_action,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 1, '', ?, ?, ?, ?)
            """,
            (
                row["ticker"],
                row["target_shares"],
                now[:10],
                row["current_price"],
                row["universe_id"],
                row["portfolio_id"],
                now,
                row["action"],
                now,
                now,
            ),
        )


def _regime_proxy(profile: dict[str, Any], settings: dict[str, Any]) -> str:
    if as_bool(settings.get("allow_global_regime_proxy")):
        return str(settings.get("global_regime_proxy", profile["regime_proxy"]))
    return str(profile["regime_proxy"])


def _calculate_current_portfolio_value(
    holdings: list[dict[str, Any]],
    scores: list[SecurityScore],
    holding_prices: dict[str, float | None],
) -> float:
    by_ticker = {score.ticker: score for score in scores}
    total = 0.0
    for holding in holdings:
        score = by_ticker.get(holding["ticker"])
        price = score.price if score and score.price is not None else holding_prices.get(holding["ticker"])
        if price is not None:
            total += float(holding["shares"]) * price
    return total


def _latest_price_from_history(data: Any) -> float | None:
    if data is None or data.empty:
        return None
    column = "adj_close" if "adj_close" in data.columns and not data["adj_close"].dropna().empty else "close"
    series = data[column].dropna()
    if series.empty:
        return None
    return float(series.iloc[-1])
