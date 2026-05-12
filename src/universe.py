from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from io import StringIO
import re

import pandas as pd
import requests

from .utils import safe_ticker_for_yfinance


@dataclass(frozen=True)
class Constituent:
    ticker: str
    company_name: str = ""
    sector: str = ""
    source: str = ""
    source_date: str = ""
    active: bool = True


def load_constituents(profile: dict, root_dir: Path) -> list[Constituent]:
    source_type = profile["constituent_source_type"]
    source = profile["constituent_source"]
    if source_type == "static_csv":
        return _load_static_csv(source, root_dir)
    if source_type == "wikipedia":
        return _load_wikipedia(profile["universe_id"], source)
    if source_type == "ishares_csv":
        return _load_ishares_csv(source)
    if source_type == "companiesmarketcap_holdings":
        return _load_companiesmarketcap_holdings(source, profile)
    raise ValueError(f"Unsupported constituent source type: {source_type}")


def metadata_from_constituents(constituents: list[Constituent]) -> dict[str, dict[str, str]]:
    return {
        item.ticker: {
            "company_name": item.company_name,
            "sector": item.sector,
            "source": item.source,
            "source_date": item.source_date,
        }
        for item in constituents
    }


def _load_static_csv(source: str, root_dir: Path) -> list[Constituent]:
    path = Path(source)
    if not path.is_absolute():
        path = root_dir / path
    if not path.exists():
        raise FileNotFoundError(f"Universe CSV not found: {path}")
    frame = pd.read_csv(path)
    normalized = {column.lower().strip(): column for column in frame.columns}
    if "ticker" not in normalized:
        raise ValueError(f"Universe CSV must include a ticker column: {path}")
    company_col = normalized.get("company_name") or normalized.get("company") or normalized.get("name")
    sector_col = normalized.get("sector")
    active_col = normalized.get("active")
    constituents = []
    for _, row in frame.iterrows():
        if active_col and str(row.get(active_col, "")).strip().lower() in {"false", "0", "no"}:
            continue
        ticker = safe_ticker_for_yfinance(str(row[normalized["ticker"]]))
        if ticker:
            constituents.append(
                Constituent(
                    ticker=ticker,
                    company_name="" if company_col is None else str(row.get(company_col, "")),
                    sector="" if sector_col is None else str(row.get(sector_col, "")),
                    source=str(path),
                    active=True,
                )
            )
    return constituents


def _load_wikipedia(universe_id: str, source: str) -> list[Constituent]:
    response = requests.get(
        source,
        headers={"User-Agent": "MomentumAnomalyScreener/1.0 (+local decision-support tool)"},
        timeout=30,
    )
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    if universe_id == "sp500":
        table = tables[0]
        return [
            Constituent(
                ticker=safe_ticker_for_yfinance(row["Symbol"]),
                company_name=str(row.get("Security", "")),
                sector=str(row.get("GICS Sector", "")),
                source=source,
                active=True,
            )
            for _, row in table.iterrows()
        ]
    if universe_id == "nasdaq100":
        table = _first_table_with_column(tables, "Ticker")
        return [
            Constituent(
                ticker=safe_ticker_for_yfinance(row["Ticker"]),
                company_name=str(row.get("Company", "")),
                sector=str(row.get("GICS Sector", row.get("Sector", ""))),
                source=source,
                active=True,
            )
            for _, row in table.iterrows()
        ]
    raise ValueError(f"Wikipedia loader is not implemented for {universe_id}")


def _load_ishares_csv(source: str) -> list[Constituent]:
    response = requests.get(
        source,
        headers={"User-Agent": "MomentumAnomalyScreener/1.0 (+local decision-support tool)"},
        timeout=60,
    )
    response.raise_for_status()
    text = response.text.replace("\ufeff", "")
    lines = text.splitlines()
    header_index = next((index for index, line in enumerate(lines) if line.startswith("Ticker,")), None)
    if header_index is None:
        raise ValueError(f"Could not find holdings header in iShares CSV: {source}")
    source_date = _source_date_from_ishares(lines)
    frame = pd.read_csv(StringIO("\n".join(lines[header_index:])), dtype=str)
    normalized = {column.lower().strip(): column for column in frame.columns}
    required = {"ticker", "name", "sector", "asset class"}
    missing = required - set(normalized)
    if missing:
        raise ValueError(f"iShares CSV missing expected columns {sorted(missing)}: {source}")

    constituents: list[Constituent] = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        asset_class = str(row.get(normalized["asset class"], "")).strip().lower()
        if asset_class != "equity":
            continue
        raw_ticker = str(row.get(normalized["ticker"], "")).strip()
        exchange = str(row.get(normalized.get("exchange", ""), "")).strip()
        location = str(row.get(normalized.get("location", ""), "")).strip()
        ticker = format_ishares_ticker(raw_ticker, exchange, location)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        constituents.append(
            Constituent(
                ticker=ticker,
                company_name=str(row.get(normalized["name"], "")).strip(),
                sector=str(row.get(normalized["sector"], "")).strip(),
                source=source,
                source_date=source_date,
                active=True,
            )
        )
    if not constituents:
        raise ValueError(f"No equity holdings found in iShares CSV: {source}")
    return constituents


def _load_companiesmarketcap_holdings(source: str, profile: dict) -> list[Constituent]:
    response = requests.get(
        source,
        headers={"User-Agent": "MomentumAnomalyScreener/1.0 (+local decision-support tool)"},
        timeout=60,
    )
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    table = _first_table_with_column(tables, "Ticker")
    normalized = {str(column).lower().strip(): column for column in table.columns}
    required = {"ticker", "name"}
    missing = required - set(normalized)
    if missing:
        raise ValueError(f"CompaniesMarketCap holdings table missing expected columns {sorted(missing)}: {source}")

    source_date = _source_date_from_companiesmarketcap(response.text)
    constituents: list[Constituent] = []
    seen: set[str] = set()
    for _, row in table.iterrows():
        raw_ticker = str(row.get(normalized["ticker"], "")).strip()
        ticker = safe_ticker_for_yfinance(raw_ticker)
        if not ticker or ticker.lower() in {"nan", "n/a"} or ticker in seen:
            continue
        seen.add(ticker)
        constituents.append(
            Constituent(
                ticker=ticker,
                company_name=str(row.get(normalized["name"], "")).strip(),
                sector=str(profile.get("exchange_scope", "")).strip(),
                source=source,
                source_date=source_date,
                active=True,
            )
        )
    if not constituents:
        raise ValueError(f"No equity holdings found in CompaniesMarketCap table: {source}")
    return constituents


def format_ishares_ticker(raw_ticker: str, exchange: str = "", location: str = "") -> str:
    ticker = raw_ticker.strip().upper()
    if not ticker or ticker in {"-", "CASH", "USD"}:
        return ""
    exchange_lower = exchange.lower()
    location_lower = location.lower()
    if "hong kong" in exchange_lower:
        return f"{ticker.zfill(4)}.HK" if ticker.isdigit() else f"{ticker}.HK"
    if "tokyo" in exchange_lower:
        return f"{ticker}.T"
    if "korea exchange (kosdaq" in exchange_lower:
        return f"{ticker}.KQ"
    if "korea exchange" in exchange_lower:
        return f"{ticker}.KS"
    if "taiwan stock" in exchange_lower:
        return f"{ticker}.TW"
    if "gretai" in exchange_lower or "taipei exchange" in exchange_lower:
        return f"{ticker}.TWO"
    if "national stock exchange of india" in exchange_lower:
        return f"{ticker}.NS"
    if "london stock" in exchange_lower:
        return f"{ticker}.L"
    if "six swiss" in exchange_lower:
        return f"{ticker}.SW"
    if "euronext amsterdam" in exchange_lower:
        return f"{ticker}.AS"
    if "euronext paris" in exchange_lower:
        return f"{ticker}.PA"
    if "bolsa de madrid" in exchange_lower:
        return f"{ticker}.MC"
    if "xetra" in exchange_lower:
        return f"{ticker}.DE"
    if "asx" in exchange_lower or "australia" in location_lower:
        return f"{ticker}.AX"
    if "stockholm" in exchange_lower:
        return f"{ticker}.ST"
    if "copenhagen" in exchange_lower:
        return f"{ticker}.CO"
    if "helsinki" in exchange_lower:
        return f"{ticker}.HE"
    if "oslo" in exchange_lower:
        return f"{ticker}.OL"
    if "borsa italiana" in exchange_lower or "italiana" in exchange_lower:
        return f"{ticker}.MI"
    if "toronto" in exchange_lower:
        return f"{ticker}.TO"
    if "xbsp" in exchange_lower or "brazil" in location_lower:
        return f"{ticker}.SA"
    if "mexico" in location_lower:
        return f"{ticker}.MX"
    if "indonesia" in location_lower:
        return f"{ticker}.JK"
    if "thailand" in location_lower:
        return f"{ticker}.BK"
    if "malaysia" in location_lower:
        return f"{ticker}.KL"
    if "singapore" in location_lower:
        return f"{ticker}.SI"
    if "johannesburg" in exchange_lower or "south africa" in location_lower:
        return f"{ticker}.JO"
    return safe_ticker_for_yfinance(ticker)


def _source_date_from_ishares(lines: list[str]) -> str:
    for line in lines[:8]:
        if line.startswith("Fund Holdings as of,"):
            return line.split(",", 1)[1].strip().strip('"')
    return ""


def _source_date_from_companiesmarketcap(text: str) -> str:
    match = re.search(r"Etf holdings as of\s*<span[^>]*>([^<]+)</span>", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _first_table_with_column(tables: list[pd.DataFrame], column: str) -> pd.DataFrame:
    for table in tables:
        if column in table.columns:
            return table
    raise ValueError(f"No holdings table found with column {column}")
