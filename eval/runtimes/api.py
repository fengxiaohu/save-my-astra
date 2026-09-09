from __future__ import annotations

import os
from typing import Any

from eval.profiles import Profile
from eval.render import render_template
from eval.runtimes.spawn_scan import count_spawns
from eval.suites import Item
from eval.usage import RunUsage


def _client():
    from openai import OpenAI

    kwargs: dict[str, Any] = {}
    if os.environ.get("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    return OpenAI(**kwargs)


def run_api(item: Item, profile: Profile) -> tuple[str, RunUsage]:
    """One Responses API turn. Cost-calibration arm."""
    instructions = render_template("AGENTS.eval.md", profile)
    client = _client()
    try:
        response = client.responses.create(
            model=profile.parent_model,
            reasoning={"effort": profile.parent_effort},
            instructions=instructions,
            input=item.prompt,
        )
    except Exception as exc:
        raise RuntimeError(
            "API arm failed. Cost numbers need API credits, not ChatGPT Plus quota. "
            f"{exc}"
        ) from exc
    text = getattr(response, "output_text", None) or ""
    if not text:
        chunks = []
        for item_out in getattr(response, "output", []) or []:
            for content in getattr(item_out, "content", []) or []:
                value = getattr(content, "text", None)
                if value:
                    chunks.append(value)
        text = "\n".join(chunks)
    usage = RunUsage()
    raw = getattr(response, "usage", None)
    input_tokens = int(getattr(raw, "input_tokens", 0) or 0)
    output_tokens = int(getattr(raw, "output_tokens", 0) or 0)
    total_tokens = int(getattr(raw, "total_tokens", 0) or 0)
    usage.add(
        profile.parent_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        spawn_count=count_spawns(response.to_dict() if hasattr(response, "to_dict") else {}),
    )
    return text, usage
