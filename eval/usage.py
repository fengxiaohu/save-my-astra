from __future__ import annotations

from dataclasses import dataclass, field

from eval.pricing import estimate_usd


@dataclass
class ModelUsage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, input_tokens: int = 0, output_tokens: int = 0, total_tokens: int = 0) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += total_tokens or (input_tokens + output_tokens)

    @property
    def usd(self) -> float:
        return estimate_usd(self.model, self.input_tokens, self.output_tokens)


@dataclass
class RunUsage:
    by_model: dict[str, ModelUsage] = field(default_factory=dict)
    spawn_count: int = 0

    def add(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        spawn_count: int = 0,
    ) -> None:
        bucket = self.by_model.setdefault(model, ModelUsage(model=model))
        bucket.add(input_tokens, output_tokens, total_tokens)
        self.spawn_count += spawn_count

    @property
    def total_tokens(self) -> int:
        return sum(m.total_tokens for m in self.by_model.values())

    @property
    def usd(self) -> float:
        return sum(m.usd for m in self.by_model.values())

    def as_dict(self) -> dict:
        return {
            "spawn_count": self.spawn_count,
            "total_tokens": self.total_tokens,
            "usd_estimate": round(self.usd, 6),
            "by_model": {
                name: {
                    "input_tokens": m.input_tokens,
                    "output_tokens": m.output_tokens,
                    "total_tokens": m.total_tokens,
                    "usd_estimate": round(m.usd, 6),
                }
                for name, m in self.by_model.items()
            },
        }
