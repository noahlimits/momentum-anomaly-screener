from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir

import pandas as pd
import streamlit as st

from src.config import AppConfig
from src.database import Database
from src.data_provider import YFinanceProvider
from src.recommendations import run_strategy


APP_ROOT = Path(__file__).resolve().parent


@st.cache_resource
def app_context() -> tuple[AppConfig, Database]:
    config = AppConfig.load(APP_ROOT / "config.yaml")
    db = Database(Path(gettempdir()) / "momentum_anomaly_demo_state.sqlite")
    db.initialize(config)
    return config, db


def main() -> None:
    st.set_page_config(page_title="Momentum Anomaly Screener", layout="wide")
    apply_styles()
    config, db = app_context()

    st.title("Momentum Anomaly Screener")
    st.caption("Enter a portfolio amount and universe to generate a ranked momentum portfolio. No brokerage connection. No trade execution.")

    universes = enabled_universes(db)
    labels = [f"{item['display_name']} ({item['universe_id']})" for item in universes]
    default_index = next((index for index, item in enumerate(universes) if item["default_profile"]), 0)

    controls = st.columns([1, 1, 1])
    portfolio_value = controls[0].number_input("Portfolio amount", min_value=1.0, value=10000.0, step=1000.0, format="%.2f")
    max_holdings = controls[1].number_input("Max holdings", min_value=1, max_value=30, value=20, step=1)
    universe_label = controls[2].selectbox("Universe", labels, index=default_index)
    universe_id = universes[labels.index(universe_label)]["universe_id"]

    if st.button("Run Screener", type="primary"):
        with st.spinner("Downloading data, ranking stocks, and calculating ATR risk-parity sizing..."):
            result = run_strategy(
                db=db,
                config=config,
                data_provider=YFinanceProvider(config.cache_dir),
                mode="initial",
                portfolio_value=portfolio_value,
                universe_id=universe_id,
                target_positions=int(max_holdings),
                persist=False,
            )
        st.session_state["demo_result"] = result

    result = st.session_state.get("demo_result")
    if not result:
        return

    show_summary(result)
    portfolio = proposed_portfolio_frame(result)
    eligible = eligible_ranked_frame(result)

    st.subheader("Proposed Risk-Parity Portfolio")
    if portfolio.empty:
        st.warning("No portfolio could be built. The market regime may be blocking new buys, or no stocks passed all filters.")
    else:
        st.dataframe(style_table(portfolio), use_container_width=True, hide_index=True)

    st.subheader("Eligible Stack-Ranked Equities")
    if eligible.empty:
        st.warning("No stocks passed the eligibility filters.")
    else:
        st.dataframe(style_table(eligible), use_container_width=True, hide_index=True)


def show_summary(result) -> None:
    buys = [rec for rec in result.recommendations if rec.action == "BUY"]
    invested = sum(rec.target_value for rec in buys)
    cols = st.columns(5)
    cols[0].metric("Regime", result.regime.status)
    cols[1].metric("Universe", result.universe_profile["display_name"])
    cols[2].metric("Portfolio amount", money(result.portfolio_value))
    cols[3].metric("Holdings", len(buys))
    cols[4].metric("Estimated cash", money(result.portfolio_value - invested))


def proposed_portfolio_frame(result) -> pd.DataFrame:
    score_by_ticker = {score.ticker: score for score in result.scores}
    rows = []
    for rec in result.recommendations:
        if rec.action != "BUY":
            continue
        score = score_by_ticker.get(rec.ticker)
        rows.append(
            {
                "_sort": score.rank if score else 999999,
                "Ticker": rec.ticker,
                "Company": score.company_name if score else "",
                "Price": rec.current_price,
                "Shares": rec.target_shares,
                "Dollars": rec.target_value,
                "Weight": rec.target_weight,
                "Momentum Score": score.momentum_score if score else None,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["_sort", "Ticker"]).drop(columns=["_sort"])


def eligible_ranked_frame(result) -> pd.DataFrame:
    rows = []
    for score in result.scores:
        if not score.eligible:
            continue
        rows.append(
            {
                "_sort": score.rank or 999999,
                "Ticker": score.ticker,
                "Company": score.company_name,
                "Price": score.price,
                "Momentum Score": score.momentum_score,
                "ATR20": score.atr20,
                "100DMA": score.ma100,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["_sort", "Ticker"]).drop(columns=["_sort"])


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


def style_table(frame: pd.DataFrame):
    return frame.style.set_table_styles(
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
                    ("background-color", "#0F172A"),
                    ("color", "#F8FAFC"),
                    ("border-bottom", "1px solid #334155"),
                ],
            },
        ]
    ).format(
        {
            "Price": "${:,.2f}",
            "Dollars": "${:,.2f}",
            "Weight": "{:.2%}",
            "Momentum Score": "{:.2%}",
            "ATR20": "${:,.2f}",
            "100DMA": "${:,.2f}",
            "Shares": "{:,.0f}",
        },
        na_rep="",
    )


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #0B1020; color: #F8FAFC; }
        div[data-testid="stMetric"] {
            background: #121A2F;
            border: 1px solid #25314D;
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def money(value: float) -> str:
    return f"${value:,.2f}"


if __name__ == "__main__":
    main()
