from __future__ import annotations

from dataclasses import dataclass

import yaml

from eval.paths import PRICING


@dataclass(frozen=True)
class Price:
    input_per_1m: float
    output_per_1m: float


def load_prices() -> dict[str, Price]:
    raw = yaml.safe_load(PRICING.read_text())
    per = raw.get("per_1m", {})
    default = raw.get("default", {"input": 1.0, "output": 4.0})
    prices = {
        name: Price(float(row["input"]), float(row["output"]))
        for name, row in per.items()
    }
    prices["*"] = Price(float(default["input"]), float(default["output"]))
    return prices


def estimate_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    prices: dict[str, Price] | None = None,
) -> float:
    table = prices or load_prices()
    price = table.get(model, table["*"])
    return (input_tokens / 1_000_000) * price.input_per_1m + (
        output_tokens / 1_000_000
    ) * price.output_per_1m
