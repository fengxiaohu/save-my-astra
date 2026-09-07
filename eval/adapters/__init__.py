from eval.adapters.aime2025 import load_aime2025
from eval.adapters.frontiermath import load_frontiermath_public12
from eval.adapters.math500 import load_math500
from eval.adapters.swebench_pro import load_swebench_pro
from eval.adapters.terminal_bench import load_terminal_bench

LOADERS = {
    "aime-2025": load_aime2025,
    "math-500": load_math500,
    "frontiermath-public12": load_frontiermath_public12,
    "terminal-bench-4": load_terminal_bench,
    "swebench-pro": load_swebench_pro,
}


def load_items(suite_name: str, slice_name: str | None, limit: int | None):
    return LOADERS[suite_name](slice_name=slice_name, limit=limit)
