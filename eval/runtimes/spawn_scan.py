from __future__ import annotations

import json
import re
from typing import Any

SPAWN_HINTS = (
    "spawn_agent",
    "spawn-agent",
    "collab_spawn",
    "delegate_to",
    "luna_max_worker",
    "terra_max_worker",
)


def count_spawns(payload: Any) -> int:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    lowered = text.lower()
    hits = 0
    for hint in SPAWN_HINTS:
        hits += len(re.findall(re.escape(hint.lower()), lowered))
    return hits
