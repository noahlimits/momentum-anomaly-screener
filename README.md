# Momentum Anomaly Screener

Local-first equity momentum anomaly screener and mirror portfolio maintenance tool.

This tool does not place trades and does not connect to a brokerage. It downloads market data, ranks a selected equity universe, stores local mirror portfolio state in SQLite, and writes Excel workbooks for review.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py init-db
python run.py run --mode initial --portfolio-value 10000 --universe sp500
```

Reports are written to `reports/`. Local state is stored in `data/momentum_anomaly_state.sqlite`.

## Common Commands

Create the database and default universe profiles:

```powershell
python run.py init-db
```

Generate an initial portfolio proposal:

```powershell
python run.py run --mode initial --portfolio-value 10000 --universe sp500
```

Run a weekly review against the persistent mirror portfolio:

```powershell
python run.py run --mode review --portfolio-value 10000 --universe sp500
```

Generate a report without changing mirror state:

```powershell
python run.py run --mode report-only --portfolio-value 10000 --universe sp500
```

Run the public-demo style app locally:

```powershell
python -m streamlit run streamlit_demo.py
```

This demo entry point does not save portfolios. It accepts a portfolio amount and universe, then shows eligible stack-ranked equities and a proposed ATR risk-parity portfolio.

## Public Demo

For a hosted Streamlit demo, use `streamlit_demo.py` as the app entry point. The demo is intentionally stateless for visitors: it does not save portfolios, create reports, or require any brokerage connection.

If Streamlit asks for the main file path, `streamlit_app.py` is also available as a conventional entry point.

The local saved-portfolio dashboard remains `dashboard.py` and is started with `Start Momentum Anomaly Screener.bat`.

Accept all recommendations from the most recent run:

```powershell
python run.py accept --latest
```

Import or replace mirror holdings from CSV:

```powershell
python run.py import-portfolio --csv holdings.csv --universe sp500
```

Expected CSV columns: `ticker`, `shares`, and optionally `entry_date`, `entry_price`, `notes`.

## Strategy Defaults

- 90 trading day exponential regression momentum
- Momentum score: annualized log-price regression slope multiplied by R squared
- Top 20% rank requirement
- Current price above 100-day moving average
- No absolute single-day move above 15% in the 90-day lookback
- New buys allowed only when the universe regime proxy is above its 200-day moving average
- ATR20 risk-parity position sizing using `portfolio_value * 0.001 / ATR20`

## Universe Profiles

The default configuration includes profile templates for the universes in the project brief. The MVP supports:

- `wikipedia` constituents for `sp500` and `nasdaq100`
- `static_csv` constituents for manually maintained universes

Static CSV files should include at least a `ticker` column. Optional columns are `company_name` and `sector`.

## Notes

Free market data can be delayed, adjusted, unavailable, or rate-limited. Treat output as decision support and inspect data errors in the workbook before acting.
