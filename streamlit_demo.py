from __future__ import annotations

from html import escape
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import quote_plus

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
    st.set_page_config(page_title="Momentum Anomaly Screener", layout="wide", initial_sidebar_state="collapsed")
    apply_styles()
    config, db = app_context()
    show_faq_sidebar()

    st.markdown(
        """
        <div class="app-heading">
            <h1>Momentum Anomaly Screener</h1>
            <p>Rules-based equity ranking and ATR risk-parity allocation.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
        st.info("Enter a portfolio amount, choose a universe, and run the screener.")
        return

    portfolio = proposed_portfolio_frame(result)
    eligible = eligible_ranked_frame(result)
    show_summary(result, portfolio)

    st.markdown("#### Risk-Parity Buy List")
    if portfolio.empty:
        st.warning("No portfolio could be built. The market regime may be blocking new buys, or no stocks passed all filters.")
    else:
        render_buy_table(portfolio)

    with st.expander("Eligible Stack Rank", expanded=False):
        if eligible.empty:
            st.warning("No stocks passed the eligibility filters.")
        else:
            st.dataframe(
                eligible,
                use_container_width=True,
                hide_index=True,
                height=table_height(eligible, maximum=520),
                column_config=eligible_columns(),
            )


def show_summary(result, portfolio: pd.DataFrame) -> None:
    buys = [rec for rec in result.recommendations if rec.action == "BUY"]
    invested = float(portfolio["Position Value"].sum()) if not portfolio.empty else 0.0
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
                "Ticker": rec.ticker,
                "Company": score.company_name if score else "",
                "Price": rec.current_price,
                "Shares": rec.target_shares,
                "Position Value": rec.target_value,
                "Weight %": rec.target_weight * 100,
            }
        )
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values(["_sort", "Ticker"]).reset_index(drop=True)
    frame.insert(0, "Rank", range(1, len(frame) + 1))
    return frame.drop(columns=["_sort"])


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
                "Ticker": score.ticker,
                "Company": score.company_name,
                "Price": score.price,
                "Score": score.momentum_score,
                "ATR20": score.atr20,
            }
        )
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values(["_sort", "Ticker"]).reset_index(drop=True)
    frame.insert(1, "Rank", range(1, len(frame) + 1))
    return frame.drop(columns=["_sort"])


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
        "Position Value": st.column_config.NumberColumn("Position Value", width="small", format="$%.0f"),
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


def render_buy_table(frame: pd.DataFrame) -> None:
    headers = ["Rank", "Ticker", "Company", "Price", "Shares", "Position Value", "Weight %"]
    rows = []
    for _, row in frame.iterrows():
        ticker = str(row["Ticker"])
        company = "" if pd.isna(row["Company"]) else str(row["Company"])
        ticker_url = escape(market_url(ticker), quote=True)
        wiki_url_value = escape(wiki_url(company or ticker), quote=True)
        rows.append(
            "<tr>"
            f"<td class='num'>{int(row['Rank']) if pd.notna(row['Rank']) else ''}</td>"
            f"<td class='ticker'><a href='{ticker_url}' target='_blank' rel='noopener noreferrer'>{escape(ticker)}</a></td>"
            f"<td><a class='company-link' href='{wiki_url_value}' target='_blank' rel='noopener noreferrer'>{escape(company)}</a></td>"
            f"<td class='num'>{money(float(row['Price'])) if pd.notna(row['Price']) else ''}</td>"
            f"<td class='num'>{float(row['Shares']):,.0f}</td>"
            f"<td class='num'>{money(float(row['Position Value']))}</td>"
            f"<td class='num'>{float(row['Weight %']):.2f}%</td>"
            "</tr>"
        )
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    st.markdown(
        f"""
        <div class="buy-table-wrap">
            <table class="buy-table">
                <thead><tr>{header_html}</tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def market_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{quote_plus(ticker)}"


def wiki_url(company: str) -> str:
    return f"https://en.wikipedia.org/wiki/Special:Search?search={quote_plus(company)}"


def show_faq_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Method")
        st.markdown(
            """
            **What the momentum anomaly is**

            Stocks with strong intermediate-term relative strength have historically tended to keep outperforming for a while. The anomaly is not just "price went up"; the goal is to find persistent, smooth upward trends and avoid one-day jumps.

            **How the score is calculated**

            For each stock, the screener runs a 90-trading-day linear regression on log price. It annualizes the regression slope, then multiplies it by R-squared:

            `momentum score = annualized trend × R-squared`

            A high score means the stock has a steep trend and that the trend has been relatively clean. It is a ranking score, not an expected return.

            **What must pass before a stock can be bought**

            The stock must be in the top 20% of the selected universe by score, above its 100-day moving average, and free of a single-day move larger than the configured gap threshold. The universe proxy also has to be above its long-term moving average before new buys are allowed. Stocks that fail any rule are excluded from the visible stack rank.

            **How the buy list is built**

            Qualified stocks are sorted from strongest to weakest and displayed as rank 1 through N. The app takes the top names up to the max-holdings setting. A 20-stock setting means "buy the top 20 qualified names," not "hold cash if the fixed-risk formula produces fewer shares."

            **How risk parity works here**

            ATR20 is used as the volatility estimate. Share counts are sized so each position has roughly the same dollar risk for a one-ATR move:

            `shares × ATR20 ≈ equal risk per position`

            Higher-volatility stocks get fewer shares. Lower-volatility stocks get more shares. The portfolio is invested across the selected names; any residual is only whole-share rounding that cannot buy another share.

            **Why max holdings matters**

            Around 20 names is a practical default: diversified enough to reduce single-stock risk, concentrated enough that the ranking signal still matters. Smaller accounts may use closer to 10 because high share prices and whole-share rounding make exact allocation harder.

            **Why switch universes**

            The universe defines the opportunity set. S&P 500 is broad large-cap U.S. Nasdaq-100 is growth-heavy. Mid-cap and small-cap universes can be more aggressive. International and emerging-market universes add geographic diversification but can introduce more data, currency, and liquidity noise.
            """
        )


def table_height(frame: pd.DataFrame, maximum: int) -> int:
    if frame.empty:
        return 120
    return min(maximum, max(180, 38 * len(frame) + 48))


def apply_styles() -> None:
    st.markdown(
        """
        <div class="faq-open-prompt" aria-hidden="true">FAQ</div>
        <style>
        .stApp { background: #080C14; color: #F8FAFC; }
        .block-container {
            padding-top: 3.75rem;
            padding-bottom: 1.5rem;
            max-width: 1500px;
        }
        .faq-open-prompt {
            align-items: center;
            background: #0E7490;
            border: 1px solid #22D3EE;
            border-radius: 999px;
            color: #ECFEFF;
            display: inline-flex;
            font-size: 0.72rem;
            font-weight: 800;
            height: 1.45rem;
            justify-content: center;
            left: 3.1rem;
            letter-spacing: 0;
            line-height: 1;
            padding: 0 0.55rem;
            pointer-events: none;
            position: fixed;
            top: 0.8rem;
            z-index: 1000000;
        }
        div[data-testid="stSidebarCollapsedControl"],
        button[data-testid="collapsedControl"],
        button[data-testid="stSidebarCollapsedControl"] {
            align-items: center;
            border-radius: 999px;
            gap: 0.35rem;
            width: auto;
        }
        div[data-testid="stSidebarCollapsedControl"]::after,
        button[data-testid="collapsedControl"]::after,
        button[data-testid="stSidebarCollapsedControl"]::after {
            content: "FAQ";
            background: #0E7490;
            border: 1px solid #22D3EE;
            border-radius: 999px;
            color: #ECFEFF;
            display: inline-flex;
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0;
            line-height: 1;
            margin-left: 0.25rem;
            padding: 0.26rem 0.5rem;
            pointer-events: none;
        }
        section[data-testid="stSidebar"] {
            background: #0E1420;
            border-right: 1px solid #25314D;
        }
        section[data-testid="stSidebar"] h3 {
            font-size: 1rem;
            letter-spacing: 0;
        }
        .app-heading {
            border-bottom: 1px solid #25314D;
            margin-bottom: 0.7rem;
            padding-bottom: 0.65rem;
        }
        .app-heading h1 {
            color: #F8FAFC;
            font-size: 1.85rem;
            line-height: 1.08;
            margin: 0;
        }
        .app-heading p {
            color: #A7F3D0;
            font-size: 0.92rem;
            margin: 0.25rem 0 0;
        }
        h1 {
            font-size: 1.7rem;
            line-height: 1.15;
            margin-bottom: 0.15rem;
        }
        h4 {
            margin-top: 0.75rem;
            margin-bottom: 0.35rem;
            color: #F8FAFC;
        }
        div[data-testid="stVerticalBlock"] {
            gap: 0.55rem;
        }
        div[data-testid="stAlert"] {
            padding: 0.65rem 0.85rem;
        }
        div[data-testid="stExpander"] {
            border: 1px solid #25314D;
            border-radius: 8px;
            background: #0E1420;
        }
        .stButton button {
            min-height: 2.35rem;
            padding: 0.2rem 0.85rem;
            border-radius: 7px;
            font-weight: 700;
            border: 1px solid #22D3EE;
            background: #0E7490;
            color: #ECFEFF;
        }
        .summary-strip {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.45rem;
            margin: 0.5rem 0 0.65rem;
        }
        .summary-strip div {
            background: #0E1420;
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
        .buy-table-wrap {
            border: 1px solid #25314D;
            border-radius: 8px;
            overflow: hidden;
            background: #0A0F19;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
        }
        .buy-table {
            border-collapse: collapse;
            width: 100%;
            table-layout: fixed;
        }
        .buy-table th {
            background: #111827;
            border-bottom: 1px solid #334155;
            color: #93C5FD;
            font-size: 0.72rem;
            font-weight: 800;
            padding: 0.42rem 0.55rem;
            text-align: left;
            text-transform: uppercase;
        }
        .buy-table td {
            border-bottom: 1px solid rgba(51, 65, 85, 0.72);
            color: #F8FAFC;
            font-size: 0.82rem;
            line-height: 1.18;
            overflow: hidden;
            padding: 0.36rem 0.55rem;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .buy-table tr:nth-child(even) td {
            background: #0D1524;
        }
        .buy-table tr:nth-child(odd) td {
            background: #0A0F19;
        }
        .buy-table tr:hover td {
            background: #132238;
        }
        .buy-table th:nth-child(1), .buy-table td:nth-child(1) { width: 5%; }
        .buy-table th:nth-child(2), .buy-table td:nth-child(2) { width: 8%; }
        .buy-table th:nth-child(3), .buy-table td:nth-child(3) { width: 33%; }
        .buy-table th:nth-child(4), .buy-table td:nth-child(4) { width: 10%; }
        .buy-table th:nth-child(5), .buy-table td:nth-child(5) { width: 10%; }
        .buy-table th:nth-child(6), .buy-table td:nth-child(6) { width: 14%; }
        .buy-table th:nth-child(7), .buy-table td:nth-child(7) { width: 10%; }
        .buy-table .ticker {
            font-weight: 800;
        }
        .buy-table a {
            color: #67E8F9;
            text-decoration: none;
        }
        .buy-table a:hover {
            color: #A7F3D0;
            text-decoration: underline;
        }
        .buy-table .company-link {
            color: #E2E8F0;
        }
        .buy-table .num {
            font-variant-numeric: tabular-nums;
            text-align: right;
        }
        @media (max-width: 900px) {
            .block-container { padding-top: 4.25rem; }
            .summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .buy-table th, .buy-table td { font-size: 0.72rem; padding: 0.32rem 0.38rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def money(value: float) -> str:
    return f"${value:,.2f}"


if __name__ == "__main__":
    main()
