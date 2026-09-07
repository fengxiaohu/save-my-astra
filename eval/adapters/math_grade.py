from __future__ import annotations

import re

BOXED = re.compile(r"\\boxed\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")


def extract_boxed(text: str) -> str | None:
    matches = BOXED.findall(text or "")
    if matches:
        return matches[-1].strip()
    return None


def _strip_math(value: str) -> str:
    text = value.strip()
    text = text.replace(",", "")
    text = re.sub(r"^\$|\$$", "", text)
    text = text.replace("\\left", "").replace("\\right", "")
    text = re.sub(r"\s+", "", text)
    return text


def answers_equal(pred: str | None, gold: str | None) -> bool:
    if pred is None or gold is None:
        return False
    left = _strip_math(pred)
    right = _strip_math(gold)
    if left == right:
        return True
    if left.lstrip("-").isdigit() and right.lstrip("-").isdigit():
        return int(left) == int(right)
    try:
        from sympy import simplify
        from sympy.parsing.sympy_parser import parse_expr

        return bool(simplify(parse_expr(left) - parse_expr(right)) == 0)
    except Exception:
        return left.lower() == right.lower()


def grade_math(completion: str, gold: str | None) -> tuple[bool, str | None]:
    pred = extract_boxed(completion)
    if pred is None:
        # last integer-looking token as a weak fallback for AIME
        nums = re.findall(r"-?\d+", completion or "")
        pred = nums[-1] if nums else None
    return answers_equal(pred, gold), pred
