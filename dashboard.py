from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from html import escape
from pathlib import Path
from urllib.parse import quote_plus

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
    st.set_page_config(page_title="Momentum Anomaly Screener", layout="wide", initial_sidebar_state="collapsed")
    apply_app_styles()
    config, db = app_context()
    show_method_sidebar()

    st.markdown(
        """
        <div class="app-heading">
            <h1>Momentum Anomaly Screener</h1>
            <p>Momentum-based equity ranking, exact rebalance actions, and SQL-backed run history.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
        universe_labels = [label_for_universe(item) for item in universes]
        universe_label = st.selectbox(
            "Index",
            options=universe_labels,
            index=default_index,
        )
    with col_d:
        portfolio_name = st.text_input("Portfolio name", placeholder="Example: S&P 500 Momentum")

    selected_universe = universes[universe_labels.index(universe_label)]["universe_id"]

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
    items = [
        ("Regime", result.regime.status),
        ("Review Value", money(result.portfolio_value)),
        ("Target Names", str(result.target_positions)),
        ("Sells", str(exits)),
        ("Buys", str(additions)),
        ("Resizes", str(resizes)),
        ("Data Alerts", str(errors)),
    ]
    html = "".join(f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in items)
    st.markdown(f"<div class='summary-strip'>{html}</div>", unsafe_allow_html=True)
    if result.calculated_portfolio_value is not None:
        st.caption(
            f"Calculated current value: {money(result.calculated_portfolio_value)} | "
            f"Cash adjustment: {money(result.cash_adjustment)}"
        )


def show_tables(result) -> None:
    if result.mode == "initial":
        buy_list = buy_list_frame(result)
        st.markdown("#### Risk-Parity Buy List")
        if buy_list.empty:
            st.warning("No stocks passed all buy filters for this run.")
        else:
            render_buy_table(buy_list)
            render_csv_download(buy_list, "Export Buy List CSV", "momentum-anomaly-buy-list.csv")
            render_yahoo_csv_download(buy_list, "Export Yahoo Finance CSV", "momentum-anomaly-yahoo-finance-import.csv", "shares_to_buy")
        with st.expander("Run Details", expanded=False):
            st.dataframe(style_actions(recommendations_frame(result)), use_container_width=True, hide_index=True)
        return

    sells = exit_list_frame(result)
    actions = trade_actions_frame(result)
    st.markdown("#### Exact Rebalance Actions")
    if actions.empty:
        st.success("No sells, buys, or share-count changes are required from this scan.")
    else:
        render_actions_table(actions)
        render_csv_download(actions, "Export Actions CSV", "momentum-anomaly-actions.csv")
    if not sells.empty:
        with st.expander("Sell Rules Triggered", expanded=True):
            render_exit_table(sells)

    target = updated_target_frame(result)
    st.markdown("#### Updated Target Portfolio")
    if target.empty:
        st.warning("No target holdings were produced for this review.")
    else:
        render_target_table(target)
        render_csv_download(target, "Export Target CSV", "momentum-anomaly-target-portfolio.csv")
        render_yahoo_csv_download(target, "Export Yahoo Target CSV", "momentum-anomaly-yahoo-finance-target.csv", "target_shares")
    with st.expander("Run Details", expanded=False):
        st.dataframe(style_actions(recommendations_frame(result)), use_container_width=True, hide_index=True)


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
                "target_rank": rec.target_rank,
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
                "_sort": rec.target_rank or (score.qualified_rank if score else None) or (score.rank if score else None),
                "ticker": rec.ticker,
                "company": score.company_name if score else "",
                "price": rec.current_price,
                "atr20": score.atr20 if score else None,
                "shares_to_buy": rec.target_shares,
                "position_value": rec.target_value,
                "weight": rec.target_weight,
            }
        )
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values(["_sort", "ticker"], na_position="last").reset_index(drop=True)
    frame.insert(1, "stack", range(1, len(frame) + 1))
    return frame.drop(columns=["_sort"])


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
                "_rank": rec.target_rank or (score.qualified_rank if score else None) or (score.rank if score else None),
                "ticker": rec.ticker,
                "company": score.company_name if score else "",
                "current_shares": rec.current_shares,
                "target_shares": rec.target_shares,
                "share_change": rec.share_change,
                "price": rec.current_price,
                "atr20": score.atr20 if score else None,
                "trade_value": abs(rec.share_change) * rec.current_price if rec.current_price is not None else None,
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
                "_sort": rec.target_rank or (score.qualified_rank if score else None) or (score.rank if score else None),
                "ticker": rec.ticker,
                "company": score.company_name if score else "",
                "current_shares": rec.current_shares,
                "target_shares": rec.target_shares,
                "share_change": rec.share_change,
                "price": rec.current_price,
                "atr20": score.atr20 if score else None,
                "target_value": rec.target_value,
                "weight": rec.target_weight,
            }
        )
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values(["_sort", "ticker"], na_position="last").reset_index(drop=True)
    frame.insert(1, "stack", range(1, len(frame) + 1))
    return frame.drop(columns=["_sort"])


def render_buy_table(frame: pd.DataFrame) -> None:
    headers = ["Stack", "Ticker", "Company", "Price", "ATR20", "Shares", "Position Value", "Weight"]
    rows = []
    for _, row in frame.iterrows():
        ticker = str(row["ticker"])
        company = "" if pd.isna(row["company"]) else str(row["company"])
        rows.append(
            "<tr>"
            f"<td class='num'>{int(row['stack'])}</td>"
            f"<td class='ticker'><a href='{escape(market_url(ticker), quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(ticker)}</a></td>"
            f"<td><a class='company-link' href='{escape(wiki_url(company or ticker), quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(company)}</a></td>"
            f"<td class='num'>{money_or_blank(row['price'])}</td>"
            f"<td class='num'>{money_or_blank(row['atr20'])}</td>"
            f"<td class='num'>{shares_or_blank(row['shares_to_buy'], whole=True)}</td>"
            f"<td class='num'>{money_or_blank(row['position_value'])}</td>"
            f"<td class='num'>{percent_or_blank(row['weight'])}</td>"
            "</tr>"
        )
    render_table(headers, rows, "local-table buy-list")


def render_actions_table(frame: pd.DataFrame) -> None:
    headers = ["Action", "Ticker", "Company", "Current", "Target", "Change", "Price", "ATR20", "Trade Value", "Reason"]
    rows = []
    for _, row in frame.iterrows():
        ticker = str(row["ticker"])
        company = "" if pd.isna(row["company"]) else str(row["company"])
        action = str(row["action"])
        rows.append(
            f"<tr class='{action_class(action)}'>"
            f"<td>{action_badge(action)}</td>"
            f"<td class='ticker'><a href='{escape(market_url(ticker), quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(ticker)}</a></td>"
            f"<td><a class='company-link' href='{escape(wiki_url(company or ticker), quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(company)}</a></td>"
            f"<td class='num'>{shares_or_blank(row['current_shares'])}</td>"
            f"<td class='num'>{shares_or_blank(row['target_shares'])}</td>"
            f"<td class='num'>{signed_shares_or_blank(row['share_change'])}</td>"
            f"<td class='num'>{money_or_blank(row['price'])}</td>"
            f"<td class='num'>{money_or_blank(row['atr20'])}</td>"
            f"<td class='num'>{money_or_blank(row['trade_value'])}</td>"
            f"<td>{escape(str(row['reason']))}</td>"
            "</tr>"
        )
    render_table(headers, rows, "local-table action-table")


def render_exit_table(frame: pd.DataFrame) -> None:
    headers = ["Ticker", "Company", "Shares To Sell", "Price", "Estimated Value", "Exit Criteria"]
    rows = []
    for _, row in frame.iterrows():
        ticker = str(row["ticker"])
        company = "" if pd.isna(row["company"]) else str(row["company"])
        rows.append(
            "<tr class='sell'>"
            f"<td class='ticker'><a href='{escape(market_url(ticker), quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(ticker)}</a></td>"
            f"<td><a class='company-link' href='{escape(wiki_url(company or ticker), quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(company)}</a></td>"
            f"<td class='num'>{shares_or_blank(row['shares_to_sell'])}</td>"
            f"<td class='num'>{money_or_blank(row['price'])}</td>"
            f"<td class='num'>{money_or_blank(row['estimated_value'])}</td>"
            f"<td>{escape(str(row['exit_reason']))}</td>"
            "</tr>"
        )
    render_table(headers, rows, "local-table exit-table")


def render_target_table(frame: pd.DataFrame) -> None:
    headers = ["Stack", "Ticker", "Company", "Current", "Target", "Change", "Price", "ATR20", "Target Value", "Weight"]
    rows = []
    for _, row in frame.iterrows():
        ticker = str(row["ticker"])
        company = "" if pd.isna(row["company"]) else str(row["company"])
        rows.append(
            f"<tr class='{action_class(str(row['action']))}'>"
            f"<td class='num'>{int(row['stack'])}</td>"
            f"<td class='ticker'><a href='{escape(market_url(ticker), quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(ticker)}</a></td>"
            f"<td><a class='company-link' href='{escape(wiki_url(company or ticker), quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(company)}</a></td>"
            f"<td class='num'>{shares_or_blank(row['current_shares'])}</td>"
            f"<td class='num'>{shares_or_blank(row['target_shares'])}</td>"
            f"<td class='num'>{signed_shares_or_blank(row['share_change'])}</td>"
            f"<td class='num'>{money_or_blank(row['price'])}</td>"
            f"<td class='num'>{money_or_blank(row['atr20'])}</td>"
            f"<td class='num'>{money_or_blank(row['target_value'])}</td>"
            f"<td class='num'>{percent_or_blank(row['weight'])}</td>"
            "</tr>"
        )
    render_table(headers, rows, "local-table target-table")


def render_table(headers: list[str], rows: list[str], class_name: str) -> None:
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    st.markdown(
        f"""
        <div class="local-table-wrap">
            <table class="{escape(class_name)}">
                <thead><tr>{header_html}</tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_csv_download(frame: pd.DataFrame, label: str, filename: str) -> None:
    export = frame.copy()
    if "ticker" in export.columns:
        export.insert(export.columns.get_loc("ticker") + 1, "ticker_url", export["ticker"].map(market_url))
    if "company" in export.columns:
        export.insert(export.columns.get_loc("company") + 1, "company_url", export["company"].map(lambda value: wiki_url(str(value))))
    st.download_button(
        label,
        data=export.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
        key=filename,
    )


def render_yahoo_csv_download(frame: pd.DataFrame, label: str, filename: str, quantity_col: str) -> None:
    export = yahoo_import_frame(
        tickers=frame["ticker"],
        prices=frame["price"],
        quantities=frame[quantity_col],
        comment="Momentum Anomaly Screener",
    )
    st.download_button(
        label,
        data=export.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
        key=filename,
    )


def yahoo_import_frame(tickers: pd.Series, prices: pd.Series, quantities: pd.Series, comment: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Symbol": tickers.astype(str),
            "Trade Date": datetime.now().strftime("%Y%m%d"),
            "Purchase Price": pd.to_numeric(prices, errors="coerce").round(4),
            "Quantity": pd.to_numeric(quantities, errors="coerce").round(6),
            "Comment": comment,
            "Extra": "",
        }
    )


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
            "atr20": "${:,.2f}",
            "current_value": "${:,.2f}",
            "estimated_value": "${:,.2f}",
            "target_value": "${:,.2f}",
            "position_value": "${:,.2f}",
            "trade_value": "${:,.2f}",
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


def show_method_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Method")
        st.markdown(
            """
            **Regime filter**

            Each universe has a market proxy. The screener compares that proxy with its 200-day moving average. Above the average is bullish and permits new buys. Below it is bearish and blocks new buys.

            The regime filter is not a forced-sell rule. In a saved portfolio, it only decides whether open slots can be filled with replacement stocks.

            **Six sell checks**

            A saved holding is flagged for sale if it leaves the selected universe, has missing or unreliable data, falls below its 100-day moving average, has a single-day gap above the configured threshold, is no longer in the top 20% by momentum score, or no longer fits inside the selected top-N target portfolio after qualified names are stacked.

            **Rebalance workflow**

            Run the same universe and holding count on each review date. Sell the red rows, make the listed buy or resize trades, then accept the review to update the saved SQL portfolio. The target portfolio is resized with current prices and current ATR20 values.

            **Risk parity**

            ATR20 is the volatility estimate. Lower-volatility stocks receive more shares and higher-volatility stocks receive fewer shares so each position contributes similar one-ATR dollar risk.

            **Open screening universes**

            1. S&P 500: best default. Broad, liquid, large-cap U.S. stocks with clean data.

            2. Russell 1000 Growth: broad large/mid-cap growth universe. Good when you want momentum candidates beyond the S&P 500 but still want liquid U.S. names.

            3. U.S. Information Technology (VGT): best pure technology-sector screen. It uses VGT holdings as a practical proxy for the MSCI US IMI Information Technology 25/50 universe. It excludes tech-adjacent names classified outside information technology.

            4. Nasdaq Composite Proxy (ONEQ): broad technology-adjacent and innovation-heavy screen using ONEQ holdings as a practical Nasdaq Composite proxy. Larger and more diverse than Nasdaq-100, but noisier than S&P 500 or Russell 1000 Growth.

            5. S&P MidCap 400: mid-cap U.S. stocks. More opportunity for momentum, more volatility than large caps.

            6. S&P SmallCap 600: quality-screened small caps. More aggressive, but cleaner than the full Russell 2000.

            7. Russell 2000: broad small-cap universe. Useful for aggressive screens, but the noisiest enabled universe because many names are smaller and less liquid.

            8. Nasdaq-100: concentrated large-cap growth/innovation list. Useful for a narrow mega-cap screen, but not as good as the broader tech or growth universes for candidate discovery.

            Developed ex-U.S., emerging markets, emerging-market small caps, and frontier markets are disabled because currency, ticker mapping, liquidity, and data-quality issues make them weaker first-class screening universes in this local tool.
            """
        )


def apply_app_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            color: #F8FAFC;
            background: #080C14;
        }
        .block-container {
            max-width: 1500px;
            padding-top: 3.75rem;
            padding-bottom: 1.5rem;
        }
        section[data-testid="stSidebar"] {
            background: #0E1420;
            border-right: 1px solid #25314D;
        }
        .app-heading {
            border-bottom: 1px solid #25314D;
            margin-bottom: 0.85rem;
            padding-bottom: 0.75rem;
        }
        .app-heading h1 {
            color: #F8FAFC;
            font-size: 1.9rem;
            line-height: 1.08;
            margin: 0;
        }
        .app-heading p {
            color: #A7F3D0;
            font-size: 0.92rem;
            margin: 0.25rem 0 0;
        }
        h3 {
            color: #F8FAFC;
            font-size: 1.1rem;
            margin-top: 0.6rem;
        }
        h4 {
            color: #F8FAFC;
            margin-top: 0.9rem;
            margin-bottom: 0.4rem;
        }
        div[data-testid="stTabs"] button {
            font-weight: 750;
        }
        div[data-testid="stVerticalBlock"] {
            gap: 0.62rem;
        }
        div[data-testid="stMetric"] {
            background: #0E1420;
            border: 1px solid #25314D;
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
        }
        .summary-strip {
            display: grid;
            gap: 0.45rem;
            grid-template-columns: repeat(7, minmax(0, 1fr));
            margin: 0.5rem 0 0.75rem;
        }
        .summary-strip div {
            background: #0E1420;
            border: 1px solid #25314D;
            border-radius: 8px;
            min-width: 0;
            padding: 0.5rem 0.62rem;
        }
        .summary-strip span {
            color: #CBD5E1;
            display: block;
            font-size: 0.7rem;
            line-height: 1.05;
        }
        .summary-strip strong {
            color: #F8FAFC;
            display: block;
            font-size: 0.92rem;
            line-height: 1.22;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .local-table-wrap {
            background: #0A0F19;
            border: 1px solid #25314D;
            border-radius: 8px;
            overflow-x: auto;
            overflow-y: hidden;
        }
        .local-table {
            border-collapse: collapse;
            table-layout: fixed;
            width: 100%;
        }
        .local-table th {
            background: #111827;
            border-bottom: 1px solid #334155;
            color: #93C5FD;
            font-size: 0.7rem;
            font-weight: 800;
            padding: 0.42rem 0.52rem;
            text-align: left;
            text-transform: uppercase;
        }
        .local-table td {
            border-bottom: 1px solid rgba(51, 65, 85, 0.72);
            color: #F8FAFC;
            font-size: 0.8rem;
            line-height: 1.18;
            overflow: hidden;
            padding: 0.35rem 0.52rem;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .local-table tr:nth-child(even) td { background: #0D1524; }
        .local-table tr:nth-child(odd) td { background: #0A0F19; }
        .local-table tr.sell td { background: #341316; color: #FEE2E2; }
        .local-table tr.buy td { background: #0D2219; color: #DCFCE7; }
        .local-table tr.resize td { background: #2A210D; color: #FEF3C7; }
        .local-table tr.hold td { color: #D1FAE5; }
        .local-table tr:hover td { background: #132238; }
        .local-table a {
            color: #67E8F9;
            text-decoration: none;
        }
        .local-table a:hover {
            color: #A7F3D0;
            text-decoration: underline;
        }
        .local-table .company-link {
            color: #E2E8F0;
        }
        .local-table .ticker {
            font-weight: 800;
        }
        .local-table .num {
            font-variant-numeric: tabular-nums;
            text-align: right;
        }
        .buy-list th:nth-child(1), .buy-list td:nth-child(1) { width: 6%; }
        .buy-list th:nth-child(2), .buy-list td:nth-child(2) { width: 9%; }
        .buy-list th:nth-child(3), .buy-list td:nth-child(3) { width: 28%; }
        .buy-list th:nth-child(4), .buy-list td:nth-child(4) { width: 10%; }
        .buy-list th:nth-child(5), .buy-list td:nth-child(5) { width: 9%; }
        .buy-list th:nth-child(6), .buy-list td:nth-child(6) { width: 9%; }
        .buy-list th:nth-child(7), .buy-list td:nth-child(7) { width: 15%; }
        .buy-list th:nth-child(8), .buy-list td:nth-child(8) { width: 10%; }
        .action-table th:nth-child(1), .action-table td:nth-child(1) { width: 11%; }
        .action-table th:nth-child(2), .action-table td:nth-child(2) { width: 8%; }
        .action-table th:nth-child(3), .action-table td:nth-child(3) { width: 18%; }
        .action-table th:nth-child(4), .action-table td:nth-child(4) { width: 8%; }
        .action-table th:nth-child(5), .action-table td:nth-child(5) { width: 8%; }
        .action-table th:nth-child(6), .action-table td:nth-child(6) { width: 8%; }
        .action-table th:nth-child(7), .action-table td:nth-child(7) { width: 8%; }
        .action-table th:nth-child(8), .action-table td:nth-child(8) { width: 8%; }
        .action-table th:nth-child(9), .action-table td:nth-child(9) { width: 11%; }
        .action-table th:nth-child(10), .action-table td:nth-child(10) { width: 12%; }
        .target-table th:nth-child(1), .target-table td:nth-child(1) { width: 6%; }
        .target-table th:nth-child(2), .target-table td:nth-child(2) { width: 8%; }
        .target-table th:nth-child(3), .target-table td:nth-child(3) { width: 22%; }
        .target-table th:nth-child(4), .target-table td:nth-child(4) { width: 8%; }
        .target-table th:nth-child(5), .target-table td:nth-child(5) { width: 8%; }
        .target-table th:nth-child(6), .target-table td:nth-child(6) { width: 8%; }
        .target-table th:nth-child(7), .target-table td:nth-child(7) { width: 9%; }
        .target-table th:nth-child(8), .target-table td:nth-child(8) { width: 8%; }
        .target-table th:nth-child(9), .target-table td:nth-child(9) { width: 13%; }
        .target-table th:nth-child(10), .target-table td:nth-child(10) { width: 8%; }
        .action-badge {
            border-radius: 999px;
            display: inline-flex;
            font-size: 0.66rem;
            font-weight: 850;
            letter-spacing: 0;
            line-height: 1;
            padding: 0.26rem 0.46rem;
            white-space: nowrap;
        }
        .action-badge.sell { background: #7F1D1D; color: #FEE2E2; }
        .action-badge.buy { background: #14532D; color: #DCFCE7; }
        .action-badge.resize { background: #78350F; color: #FEF3C7; }
        .action-badge.hold { background: #164E3A; color: #D1FAE5; }
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
        .stButton button {
            border-radius: 7px;
            font-weight: 760;
        }
        div[data-testid="stDownloadButton"] button {
            border-radius: 7px;
            font-weight: 760;
            margin-top: 0.45rem;
            min-height: 2.15rem;
            width: auto;
        }
        div[data-testid="stExpander"] {
            background: #0E1420;
            border: 1px solid #25314D;
            border-radius: 8px;
        }
        @media (max-width: 1000px) {
            .summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .local-table { min-width: 920px; }
        }
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
            ORDER BY sort_order, display_name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def action_class(action: str) -> str:
    if action in {"SELL", "EXIT", "DATA_ERROR"}:
        return "sell"
    if action in {"BUY", "ADD"}:
        return "buy"
    if action in {"BUY_MORE", "SELL_PARTIAL", "RESIZE_UP", "RESIZE_DOWN"}:
        return "resize"
    return "hold"


def action_badge(action: str) -> str:
    labels = {
        "BUY_MORE": "BUY MORE",
        "SELL_PARTIAL": "SELL PARTIAL",
        "DATA_ERROR": "DATA ALERT",
    }
    label = labels.get(action, action.replace("_", " "))
    return f"<span class='action-badge {action_class(action)}'>{escape(label)}</span>"


def market_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{quote_plus(ticker)}"


def wiki_url(company: str) -> str:
    return f"https://en.wikipedia.org/wiki/Special:Search?search={quote_plus(company)}"


def has_value(value) -> bool:
    return value is not None and pd.notna(value)


def money_or_blank(value) -> str:
    return f"${float(value):,.2f}" if has_value(value) else ""


def shares_or_blank(value, whole: bool = False) -> str:
    if not has_value(value):
        return ""
    return f"{float(value):,.0f}" if whole else f"{float(value):,.2f}"


def signed_shares_or_blank(value) -> str:
    if not has_value(value):
        return ""
    return f"{float(value):+,.2f}"


def percent_or_blank(value) -> str:
    return f"{float(value):.2%}" if has_value(value) else ""


def label_for_universe(universe: dict) -> str:
    return f"{int(universe.get('sort_order') or 999)}. {universe['display_name']}"


def portfolio_label(portfolio: dict) -> str:
    return f"{portfolio['name']} | {portfolio['universe_id']} | {portfolio['created_at'][:10]}"


def money(value) -> str:
    if value is None:
        return "$0.00"
    return f"${float(value):,.2f}"


if __name__ == "__main__":
    main()
