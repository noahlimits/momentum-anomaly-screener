from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class RiskParityTarget:
    ticker: str
    shares: float
    value: float
    weight: float
    atr_risk: float


def target_shares(portfolio_value: float, atr20: float | None, allow_fractional: bool = False) -> float:
    if atr20 is None or atr20 <= 0 or portfolio_value <= 0:
        return 0.0
    shares = (portfolio_value * 0.001) / atr20
    return float(shares if allow_fractional else floor(shares))


def target_value(shares: float, price: float | None) -> float:
    if price is None:
        return 0.0
    return float(max(0.0, shares) * price)


def target_weight(value: float, portfolio_value: float) -> float:
    if portfolio_value <= 0:
        return 0.0
    return float(value / portfolio_value)


def risk_parity_targets(
    portfolio_value: float,
    candidates: list[tuple[str, float | None, float | None]],
    allow_fractional: bool = False,
) -> list[RiskParityTarget]:
    valid = [
        (ticker, float(price), float(atr20))
        for ticker, price, atr20 in candidates
        if price is not None and price > 0 and atr20 is not None and atr20 > 0
    ]
    if portfolio_value <= 0 or not valid:
        return []

    denominator = sum(price / atr20 for _, price, atr20 in valid)
    if denominator <= 0:
        return []

    working: list[dict[str, float | str]] = []
    for ticker, price, atr20 in valid:
        target_dollars = portfolio_value * ((price / atr20) / denominator)
        raw_shares = target_dollars / price
        shares = raw_shares if allow_fractional else floor(raw_shares)
        working.append(
            {
                "ticker": ticker,
                "price": price,
                "atr20": atr20,
                "ideal_value": target_dollars,
                "shares": float(shares),
            }
        )

    if not allow_fractional:
        remaining_cash = portfolio_value - sum(float(item["shares"]) * float(item["price"]) for item in working)
        while True:
            affordable = [item for item in working if float(item["price"]) <= remaining_cash]
            if not affordable:
                break
            candidate = max(
                affordable,
                key=lambda item: (float(item["ideal_value"]) - (float(item["shares"]) * float(item["price"])), -float(item["price"])),
            )
            candidate["shares"] = float(candidate["shares"]) + 1.0
            remaining_cash -= float(candidate["price"])

    return [
        RiskParityTarget(
            ticker=str(item["ticker"]),
            shares=float(item["shares"]),
            value=target_value(float(item["shares"]), float(item["price"])),
            weight=target_weight(target_value(float(item["shares"]), float(item["price"])), portfolio_value),
            atr_risk=float(item["shares"]) * float(item["atr20"]),
        )
        for item in working
    ]
