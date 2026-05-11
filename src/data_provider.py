from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd


class DataProvider(ABC):
    @abstractmethod
    def get_price_history(self, tickers: list[str], period: str = "420d") -> dict[str, pd.DataFrame]:
        raise NotImplementedError


class YFinanceProvider(DataProvider):
    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_price_history(self, tickers: list[str], period: str = "420d") -> dict[str, pd.DataFrame]:
        try:
            import yfinance as yf
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install requirements first: pip install -r requirements.txt") from exc

        clean = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
        if not clean:
            return {}
        raw = yf.download(
            tickers=clean,
            period=period,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        result: dict[str, pd.DataFrame] = {}
        if raw.empty:
            return {ticker: pd.DataFrame() for ticker in clean}
        if isinstance(raw.columns, pd.MultiIndex):
            for ticker in clean:
                if ticker in raw.columns.get_level_values(0):
                    result[ticker] = _normalize(raw[ticker])
                else:
                    result[ticker] = pd.DataFrame()
        else:
            result[clean[0]] = _normalize(raw)
        for ticker in clean:
            result.setdefault(ticker, pd.DataFrame())
        return result


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    renamed = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }
    out = frame.rename(columns=renamed).copy()
    keep = [column for column in ["open", "high", "low", "close", "adj_close", "volume"] if column in out.columns]
    out = out[keep].dropna(how="all")
    out.index = pd.to_datetime(out.index)
    return out.sort_index()
