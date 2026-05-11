from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import AppConfig
from src.database import Database
from src.data_provider import YFinanceProvider
from src.recommendations import accept_recommendations, create_portfolio_from_run, run_strategy
from src.report_excel import write_workbook


APP_ROOT = Path(__file__).resolve().parent


@st.cache_resource
def app_context() -> tuple[AppConfig, Database]:
    config = AppConfig.load(APP_ROOT / "config.yaml")
    db = Database(config.database_path)
    db.initialize(config)
    return config, db


def main() -> None:
    st.set_page_config(page_title="Momentum Anomaly Screener", layout="wide")
    apply_app_styles()
    config, db = app_context()

    st.title("Momentum Anomaly Screener")
    st.caption("Local decision-support dashboard. No brokerage connection. No remote access.")

    portfolios = db.portfolios()
    tab_create, tab_review = st.tabs(["Create New Portfolio", "Review Saved Portfolio"])

    with tab_create:
        create_portfolio_screen(config, db)

    with tab_review:
        review_portfolio_screen(config, db, portfolios)


def create_portfolio_screen(config: AppConfig, db: Database) -> None:
    st.subheader("Create New Portfolio")
    universes = enabled_universes(db)
    default_index = next((i for i, item in enumerate(universes) if item["default_profile"]), 0)
    col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 1.4])
    with col_a:
        starting_value = st.number_input("Starting amount", min_value=1.0, value=10000.0, step=1000.0, format="%.2f")
    with col_b:
        target_positions = st.number_input("Number of stocks", min_value=1, max_value=20, value=10, step=1)
    with col_c:
        universe_label = st.selectbox(
            "Index",
            options=[label_for_universe(item) for item in universes],
            index=default_index,
        )
    with col_d:
        portfolio_name = st.text_input("Portfolio name", placeholder="Example: S&P 500 Momentum")

    selected_universe = universes[[label_for_universe(item) for item in universes].index(universe_label)]["universe_id"]

    if st.button("Run Initial Portfolio", type="primary"):
        with st.spinner("Downloading prices and calculating the initial portfolio..."):
            result = run_strategy(
                db=db,
                config=config,
                data_provider=YFinanceProvider(config.cache_dir),
                mode="initial",
                portfolio_value=starting_value,
                universe_id=selected_universe,
                target_positions=int(target_positions),
            )
        st.session_state["initial_result"] = result
        st.session_state["initial_target_positions"] = int(target_positions)

    result = st.session_state.get("initial_result")
    if result:
        show_run_summary(result)
        show_tables(result)
        if st.button("Accept And Save Portfolio", disabled=not portfolio_name.strip()):
            try:
                saved_target_positions = int(st.session_state.get("initial_target_positions", result.target_positions))
                portfolio_id = create_portfolio_from_run(db, result.run_id, portfolio_name.strip(), target_positions=saved_target_positions)
                saved_result = replace(result, portfolio_id=portfolio_id)
                workbook_path = write_workbook(saved_result, db, config)
                db.update_portfolio_review(portfolio_id, result.portfolio_value, str(workbook_path))
                st.success(f"Saved portfolio and updated workbook: {workbook_path}")
                st.session_state.pop("initial_result", None)
            except Exception as exc:
                st.error(f"Could not save portfolio: {exc}")


def review_portfolio_screen(config: AppConfig, db: Database, portfolios: list[dict]) -> None:
    st.subheader("Review Saved Portfolio")
    if not portfolios:
        st.info("No saved portfolios yet. Create and accept an initial portfolio first.")
        return

    labels = [portfolio_label(item) for item in portfolios]
    selected_label = st.selectbox("Saved portfolio", labels)
    portfolio = portfolios[labels.index(selected_label)]
    portfolio_id = int(portfolio["portfolio_id"])

    holdings = db.active_holdings(portfolio["universe_id"], portfolio_id=portfolio_id)
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Saved positions", len(holdings))
    col_b.metric("Index", portfolio["universe_id"])
    col_c.metric("Target stocks", portfolio.get("target_positions", 10))
    col_d.metric("Last value", money(portfolio.get("latest_portfolio_value")))

    cash_adjustment = st.number_input(
        "Cash adjustment for this review",
        value=0.0,
        step=1000.0,
        format="%.2f",
        help="Use positive numbers for contributions and negative numbers for withdrawals.",
    )

    review_key = (portfolio_id, float(cash_adjustment))
    should_auto_scan = st.session_state.get("review_key") != review_key
    if should_auto_scan:
        run_review_scan(config, db, portfolio, portfolio_id, cash_adjustment)

    if st.button("Refresh Scan", type="primary"):
        run_review_scan(config, db, portfolio, portfolio_id, cash_adjustment)

    result = st.session_state.get("review_result")
    if result and result.portfolio_id == portfolio_id:
        show_run_summary(result)
        show_tables(result)
        workbook_path = st.session_state.get("review_workbook")
        if workbook_path:
            st.info(f"Workbook updated: {workbook_path}")
        if st.button("Accept Review And Update Saved Portfolio"):
            accepted = accept_recommendations(db, run_id=result.run_id)
            st.success(f"Accepted {accepted} recommendation(s) and updated the saved portfolio.")
            st.session_state.pop("review_result", None)
            st.session_state.pop("review_workbook", None)
            st.session_state.pop("review_key", None)


def run_review_scan(config: AppConfig, db: Database, portfolio: dict, portfolio_id: int, cash_adjustment: float) -> None:
    with st.spinner("Running the saved portfolio review..."):
        result = run_strategy(
            db=db,
            config=config,
            data_provider=YFinanceProvider(config.cache_dir),
            mode="review",
            portfolio_value=None,
            universe_id=portfolio["universe_id"],
            portfolio_id=portfolio_id,
            cash_adjustment=cash_adjustment,
        )
        workbook_path = write_workbook(result, db, config)
    st.session_state["review_result"] = result
    st.session_state["review_workbook"] = str(workbook_path)
    st.session_state["review_key"] = (portfolio_id, float(cash_adjustment))


def legacy_manual_review_block(config: AppConfig, db: Database, portfolio: dict, portfolio_id: int, cash_adjustment: float) -> None:
    if False:
        with st.spinner("Reviewing saved holdings and building the updated target portfolio..."):
            result = run_strategy(
                db=db,
                config=config,
                data_provider=YFinanceProvider(config.cache_dir),
                mode="review",
                portfolio_value=None,
                universe_id=portfolio["universe_id"],
                portfolio_id=portfolio_id,
                cash_adjustment=cash_adjustment,
            )
            workbook_path = write_workbook(result, db, config)


def show_run_summary(result) -> None:
    exits = sum(1 for rec in result.recommendations if rec.action == "EXIT")
    additions = sum(1 for rec in result.recommendations if rec.action in {"BUY", "ADD"})
    resizes = sum(1 for rec in result.recommendations if rec.action in {"RESIZE_UP", "RESIZE_DOWN"})
    errors = sum(1 for rec in result.recommendations if rec.action == "DATA_ERROR")
    cols = st.columns(6)
    cols[0].metric("Regime", result.regime.status)
    cols[1].metric("Review value", money(result.portfolio_value))
    cols[2].metric("Target stocks", result.target_positions)
    cols[3].metric("Sells", exits)
    cols[4].metric("Buys", additions)
    cols[5].metric("Data errors", errors)
    if result.calculated_portfolio_value is not None:
        st.caption(
            f"Calculated current value: {money(result.calculated_portfolio_value)} | "
            f"Cash adjustment: {money(result.cash_adjustment)}"
        )


def show_tables(result) -> None:
    if result.mode == "initial":
        buy_list = buy_list_frame(result)
        st.markdown("#### Buy List")
        if buy_list.empty:
            st.warning("No stocks passed all buy filters for this run.")
        else:
            st.dataframe(style_actions(buy_list), use_container_width=True, hide_index=True)
        return

    sells = exit_list_frame(result)
    actions = trade_actions_frame(result)
    st.markdown("#### Actions To Take")
    if actions.empty:
        st.success("No sells, buys, or share-count changes are required from this scan.")
    else:
        st.dataframe(style_actions(actions), use_container_width=True, hide_index=True)
    if not sells.empty:
        st.markdown("#### Exit Criteria")
        st.dataframe(style_actions(sells), use_container_width=True, hide_index=True)

    target = updated_target_frame(result)
    st.markdown("#### Updated Target Portfolio")
    if target.empty:
        st.warning("No target holdings were produced for this review.")
    else:
        st.dataframe(style_actions(target), use_container_width=True, hide_index=True)


def recommendations_frame(result) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "action": rec.action,
                "ticker": rec.ticker,
                "current_shares": rec.current_shares,
                "target_shares": rec.target_shares,
                "share_change": rec.share_change,
                "price": rec.current_price,
                "target_value": rec.target_value,
                "target_weight": rec.target_weight,
                "reason": rec.reason,
            }
            for rec in result.recommendations
        ]
    )


def buy_list_frame(result) -> pd.DataFrame:
    score_by_ticker = {score.ticker: score for score in result.scores}
    rows = []
    for rec in result.recommendations:
        if rec.action not in {"BUY", "ADD"}:
            continue
        score = score_by_ticker.get(rec.ticker)
        rows.append(
            {
                "action": rec.action,
                "_rank": score.rank if score else None,
                "ticker": rec.ticker,
                "company": score.company_name if score else "",
                "price": rec.current_price,
                "shares_to_buy": rec.target_shares,
                "dollars": rec.target_value,
                "weight": rec.target_weight,
            }
        )
    return pd.DataFrame(rows).sort_values(["_rank", "ticker"], na_position="last").drop(columns=["_rank"]) if rows else pd.DataFrame()


def exit_list_frame(result) -> pd.DataFrame:
    score_by_ticker = {score.ticker: score for score in result.scores}
    rows = []
    for rec in result.recommendations:
        if rec.action not in {"EXIT", "DATA_ERROR"}:
            continue
        score = score_by_ticker.get(rec.ticker)
        rows.append(
            {
                "action": rec.action,
                "ticker": rec.ticker,
                "company": score.company_name if score else "",
                "shares_to_sell": rec.current_shares,
                "price": rec.current_price,
                "estimated_value": rec.current_shares * rec.current_price if rec.current_price else None,
                "exit_reason": rec.reason,
            }
        )
    return pd.DataFrame(rows).sort_values(["ticker"]) if rows else pd.DataFrame()


def trade_actions_frame(result) -> pd.DataFrame:
    score_by_ticker = {score.ticker: score for score in result.scores}
    rows = []
    for rec in result.recommendations:
        if rec.action in {"HOLD", "NO_CASH", "SKIP_NO_CASH", "WATCHLIST", "BLOCKED_BY_REGIME"}:
            continue
        if rec.action not in {"EXIT", "DATA_ERROR"} and abs(rec.share_change) < 1e-9:
            continue
        score = score_by_ticker.get(rec.ticker)
        trade_action = _display_trade_action(rec.action, rec.share_change)
        rows.append(
            {
                "action": trade_action,
                "_rank": score.rank if score else None,
                "ticker": rec.ticker,
                "company": score.company_name if score else "",
                "current_shares": rec.current_shares,
                "target_shares": rec.target_shares,
                "share_change": rec.share_change,
                "price": rec.current_price,
                "approx_dollars": abs(rec.share_change) * rec.current_price if rec.current_price is not None else None,
                "reason": rec.reason,
            }
        )
    if not rows:
        return pd.DataFrame()
    action_order = {"SELL": 0, "BUY": 1, "BUY_MORE": 2, "SELL_PARTIAL": 3, "DATA_ERROR": 4}
    frame = pd.DataFrame(rows)
    frame["_order"] = frame["action"].map(action_order).fillna(9)
    return frame.sort_values(["_order", "_rank", "ticker"], na_position="last").drop(columns=["_order", "_rank"])


def updated_target_frame(result) -> pd.DataFrame:
    score_by_ticker = {score.ticker: score for score in result.scores}
    keep_actions = {"HOLD", "ADD", "BUY", "RESIZE_UP", "RESIZE_DOWN"}
    rows = []
    for rec in result.recommendations:
        if rec.action not in keep_actions or rec.target_shares <= 0:
            continue
        score = score_by_ticker.get(rec.ticker)
        rows.append(
            {
                "action": rec.action,
                "_rank": score.rank if score else None,
                "ticker": rec.ticker,
                "company": score.company_name if score else "",
                "current_shares": rec.current_shares,
                "target_shares": rec.target_shares,
                "share_change": rec.share_change,
                "price": rec.current_price,
                "target_value": rec.target_value,
                "weight": rec.target_weight,
            }
        )
    return pd.DataFrame(rows).sort_values(["_rank", "ticker"], na_position="last").drop(columns=["_rank"]) if rows else pd.DataFrame()


def _display_trade_action(action: str, share_change: float) -> str:
    if action == "EXIT":
        return "SELL"
    if action == "DATA_ERROR":
        return "DATA_ERROR"
    if action in {"BUY", "ADD"}:
        return "BUY"
    if action == "RESIZE_UP":
        return "BUY_MORE"
    if action == "RESIZE_DOWN":
        return "SELL_PARTIAL"
    return "BUY" if share_change > 0 else "SELL"


def style_actions(frame: pd.DataFrame):
    if frame.empty or "action" not in frame.columns:
        return frame

    row_styles = {
        "EXIT": "background-color: #7F1D1D; color: #FEE2E2; font-weight: 700;",
        "SELL": "background-color: #7F1D1D; color: #FEE2E2; font-weight: 700;",
        "SELL_PARTIAL": "background-color: #78350F; color: #FEF3C7; font-weight: 700;",
        "DATA_ERROR": "background-color: #7F1D1D; color: #FEE2E2; font-weight: 700;",
        "BUY": "background-color: #0F172A; color: #F8FAFC;",
        "BUY_MORE": "background-color: #0F172A; color: #F8FAFC;",
        "ADD": "background-color: #0F172A; color: #F8FAFC;",
        "RESIZE_UP": "background-color: #0F172A; color: #F8FAFC;",
        "RESIZE_DOWN": "background-color: #0F172A; color: #F8FAFC;",
        "BLOCKED_BY_REGIME": "background-color: #334155; color: #F8FAFC;",
        "NO_CASH": "background-color: #1F2937; color: #E5E7EB;",
        "SKIP_NO_CASH": "background-color: #1F2937; color: #E5E7EB;",
        "WATCHLIST": "background-color: #1E3A8A; color: #DBEAFE;",
        "HOLD": "background-color: #164E3A; color: #D1FAE5;",
    }

    def row_style(row):
        style = row_styles.get(row.get("action"), "background-color: #0F172A; color: #F8FAFC;")
        return [style] * len(row)

    return (
        frame.style
        .apply(row_style, axis=1)
        .set_table_styles(
            [
                {
                    "selector": "th",
                    "props": [
                        ("background-color", "#111827"),
                        ("color", "#F8FAFC"),
                        ("font-weight", "700"),
                        ("border-bottom", "2px solid #475569"),
                    ],
                },
                {
                    "selector": "td",
                    "props": [
                        ("border-bottom", "1px solid #334155"),
                        ("font-size", "0.92rem"),
                    ],
                },
            ]
        )
        .format(
        {
            "current_price": "${:,.2f}",
            "target_price": "${:,.2f}",
            "price": "${:,.2f}",
            "current_value": "${:,.2f}",
            "estimated_value": "${:,.2f}",
            "target_value": "${:,.2f}",
            "dollars": "${:,.2f}",
            "approx_dollars": "${:,.2f}",
            "target_weight": "{:.2%}",
            "weight": "{:.2%}",
            "current_shares": "{:,.2f}",
            "target_shares": "{:,.2f}",
            "shares_to_buy": "{:,.0f}",
            "shares_to_sell": "{:,.0f}",
            "share_change": "{:,.2f}",
        },
        na_rep="",
        )
    )


def apply_app_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            color: #F8FAFC;
            background: #0B1020;
        }
        div[data-testid="stMetric"] {
            background: #121A2F;
            border: 1px solid #25314D;
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
        }
        .action-legend {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin: 0.5rem 0 1rem;
        }
        .legend-item {
            border-radius: 999px;
            border: 1px solid rgba(248, 250, 252, 0.18);
            font-size: 0.82rem;
            font-weight: 700;
            padding: 0.25rem 0.7rem;
        }
        .legend-item.exit { background: #7f1d1d; color: #fee2e2; }
        .legend-item.buy { background: #14532d; color: #dcfce7; }
        .legend-item.resize { background: #78350f; color: #fef3c7; }
        .legend-item.hold { background: #164e3a; color: #d1fae5; }
        .legend-item.data { background: #4c1d95; color: #f5f3ff; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def enabled_universes(db: Database) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM universe_profiles
            WHERE enabled = 1
            ORDER BY default_profile DESC, display_name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def label_for_universe(universe: dict) -> str:
    return f"{universe['display_name']} ({universe['universe_id']})"


def portfolio_label(portfolio: dict) -> str:
    return f"{portfolio['name']} | {portfolio['universe_id']} | {portfolio['created_at'][:10]}"


def money(value) -> str:
    if value is None:
        return "$0.00"
    return f"${float(value):,.2f}"


if __name__ == "__main__":
    main()
