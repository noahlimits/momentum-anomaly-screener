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
    st.caption("Rank the selected stock universe, take the top eligible names, and allocate the portfolio across them with ATR-based risk parity.")

    universes = enabled_universes(db)
    labels = [f"{item['display_name']} ({item['universe_id']})" for item in universes]
    default_index = next((index for index, item in enumerate(universes) if item["default_profile"]), 0)

    controls = st.columns([1, 0.85, 1.35, 0.65])
    portfolio_value = controls[0].number_input("Portfolio amount", min_value=1.0, value=1000000.0, step=10000.0, format="%.2f")
    max_holdings = controls[1].number_input(
        "Max holdings",
        min_value=1,
        max_value=30,
        value=20,
        step=1,
        help="The screener buys the top eligible names up to this count.",
    )
    universe_label = controls[2].selectbox("Universe", labels, index=default_index)
    universe_id = universes[labels.index(universe_label)]["universe_id"]

    if controls[3].button("Run", type="primary"):
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
        show_faq()
        return

    portfolio = proposed_portfolio_frame(result)
    eligible = eligible_ranked_frame(result)
    show_summary(result, portfolio)

    left, right = st.columns([1.04, 0.96])
    with left:
        st.markdown("#### Risk-Parity Buy List")
        if portfolio.empty:
            st.warning("No portfolio could be built. The market regime may be blocking new buys, or no stocks passed all filters.")
        else:
            st.dataframe(
                portfolio,
                use_container_width=True,
                hide_index=True,
                height=420,
                column_config=portfolio_columns(),
            )

    with right:
        st.markdown("#### Eligible Stack Rank")
        if eligible.empty:
            st.warning("No stocks passed the eligibility filters.")
        else:
            st.dataframe(
                eligible,
                use_container_width=True,
                hide_index=True,
                height=420,
                column_config=eligible_columns(),
            )

    show_faq()


def show_summary(result, portfolio: pd.DataFrame) -> None:
    buys = [rec for rec in result.recommendations if rec.action == "BUY"]
    invested = float(portfolio["Buy $"].sum()) if not portfolio.empty else 0.0
    residual = max(0.0, result.portfolio_value - invested)
    st.markdown(
        f"""
        <div class="summary-strip">
            <div><span>Regime</span><strong>{result.regime.status}</strong></div>
            <div><span>Universe</span><strong>{result.universe_profile["display_name"]}</strong></div>
            <div><span>Portfolio</span><strong>{money(result.portfolio_value)}</strong></div>
            <div><span>Target Names</span><strong>{len(buys)} / {result.target_positions}</strong></div>
            <div><span>Allocated</span><strong>{money(invested)}</strong></div>
            <div><span>Rounding Residual</span><strong>{money(residual)}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
                "Rank": score.rank if score else None,
                "Ticker": rec.ticker,
                "Company": score.company_name if score else "",
                "Price": rec.current_price,
                "Shares": rec.target_shares,
                "Buy $": rec.target_value,
                "Weight %": rec.target_weight * 100,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["_sort", "Ticker"]).drop(columns=["_sort"])


def eligible_ranked_frame(result) -> pd.DataFrame:
    selected = {rec.ticker for rec in result.recommendations if rec.action == "BUY"}
    rows = []
    for score in result.scores:
        if not score.eligible:
            continue
        rows.append(
            {
                "_sort": score.rank or 999999,
                "In Portfolio": "Yes" if score.ticker in selected else "",
                "Rank": score.rank,
                "Ticker": score.ticker,
                "Company": score.company_name,
                "Price": score.price,
                "Score": score.momentum_score,
                "ATR20": score.atr20,
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


def portfolio_columns() -> dict:
    return {
        "Rank": st.column_config.NumberColumn("Rank", width="small", format="%d"),
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Company": st.column_config.TextColumn("Company", width="medium"),
        "Price": st.column_config.NumberColumn("Price", width="small", format="$%.2f"),
        "Shares": st.column_config.NumberColumn("Shares", width="small", format="%d"),
        "Buy $": st.column_config.NumberColumn("Buy $", width="small", format="$%.0f"),
        "Weight %": st.column_config.NumberColumn("Weight", width="small", format="%.2f%%"),
    }


def eligible_columns() -> dict:
    return {
        "In Portfolio": st.column_config.TextColumn("Buy List", width="small"),
        "Rank": st.column_config.NumberColumn("Rank", width="small", format="%d"),
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Company": st.column_config.TextColumn("Company", width="medium"),
        "Price": st.column_config.NumberColumn("Price", width="small", format="$%.2f"),
        "Score": st.column_config.NumberColumn(
            "Score",
            width="small",
            format="%.2f",
            help="Annualized exponential-regression trend multiplied by R-squared. This is a ranking score, not a dollar return.",
        ),
        "ATR20": st.column_config.NumberColumn("ATR20", width="small", format="$%.2f"),
    }


def show_faq() -> None:
    with st.expander("FAQ: what the screener is doing"):
        st.markdown(
            """
            The screener loads the selected universe, downloads recent price data, ranks stocks by momentum quality, filters out names that fail the rules, then buys the highest-ranked eligible stocks up to the selected holding count.

            The public version does not save portfolios, create brokerage orders, or place trades. It shows a rules-based buy list and the ranked eligible list behind it.
            """
        )
    with st.expander("FAQ: how stocks are ranked"):
        st.markdown(
            """
            Ranking uses a 90-trading-day exponential regression on log prices. The trend is annualized, then multiplied by R-squared so smooth, persistent trends outrank noisy jumps.

            A stock must also be above its 100-day moving average, be in the top 20% of the universe by score, avoid a single-day move above the configured gap threshold, and pass the market-regime check for new buys.
            """
        )
    with st.expander("FAQ: what momentum anomaly means"):
        st.markdown(
            """
            The momentum anomaly is the tendency for stocks with strong intermediate-term relative strength to keep outperforming for a while. This screener is trying to isolate that behavior mechanically: strong trend, decent trend quality, no oversized one-day jump, and a market backdrop that permits new buys.
            """
        )
    with st.expander("FAQ: how risk parity allocation works"):
        st.markdown(
            """
            After ranking, the screener takes the top eligible names up to the max-holdings setting. It then allocates the portfolio across those stocks using ATR20 as the volatility measure.

            Lower-ATR stocks receive more shares and higher-ATR stocks receive fewer shares so a one-ATR move has roughly similar dollar impact across positions. Whole-share rounding can leave a small residual, and the app spends that residual into affordable under-allocated names until no additional whole share can be bought.
            """
        )
    with st.expander("FAQ: how many holdings to use"):
        st.markdown(
            """
            Around 20 holdings is a practical default for a diversified momentum portfolio. It spreads single-stock risk while still keeping the portfolio concentrated in the strongest names.

            Smaller accounts may use closer to 10 holdings because whole-share rounding and high-priced stocks make precise allocation harder. Fewer than 10 can become very concentrated; much more than 20 can dilute the signal and add turnover.
            """
        )
    with st.expander("FAQ: why switch universes"):
        st.markdown(
            """
            Different universes change the opportunity set. The S&P 500 is large-cap and usually more liquid. Nasdaq-100 tilts toward growth and technology. Mid-cap and small-cap universes can produce more aggressive lists with different volatility. International and emerging-market universes add geographic diversification but can introduce currency, liquidity, and data-quality differences.
            """
        )


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #0B1020; color: #F8FAFC; }
        .block-container {
            padding-top: 1.1rem;
            padding-bottom: 1.5rem;
            max-width: 1500px;
        }
        h1 {
            font-size: 1.7rem;
            line-height: 1.15;
            margin-bottom: 0.15rem;
        }
        h4 {
            margin-top: 0.75rem;
            margin-bottom: 0.35rem;
        }
        div[data-testid="stVerticalBlock"] {
            gap: 0.55rem;
        }
        .stButton button {
            min-height: 2.35rem;
            padding: 0.2rem 0.85rem;
        }
        .summary-strip {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.45rem;
            margin: 0.5rem 0 0.65rem;
        }
        .summary-strip div {
            background: #121A2F;
            border: 1px solid #25314D;
            border-radius: 8px;
            padding: 0.45rem 0.6rem;
            min-width: 0;
        }
        .summary-strip span {
            display: block;
            color: #CBD5E1;
            font-size: 0.72rem;
            line-height: 1.05;
        }
        .summary-strip strong {
            display: block;
            color: #F8FAFC;
            font-size: 0.9rem;
            line-height: 1.25;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        @media (max-width: 900px) {
            .summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def money(value: float) -> str:
    return f"${value:,.2f}"


if __name__ == "__main__":
    main()
