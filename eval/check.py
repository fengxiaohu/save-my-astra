from __future__ import annotations

from eval.paths import SKILL_FILE, SKILL_NAME, TEMPLATES
from eval.profiles import list_profiles, load_profile
from eval.render import render_template
from eval.routing import classify_routing
from eval.suites import KNOWN, load_suite


def check() -> list[str]:
    errors: list[str] = []
    required_templates = [
        "AGENTS.md",
        "AGENTS.eval.md",
        "config.snippet.toml",
        "agents/worker.toml",
    ]
    for name in required_templates:
        if not (TEMPLATES / name).exists():
            errors.append(f"missing template {name}")

    for profile_name in ("astra-luna", "astra-terra", "astra-solo", "sol-luna"):
        if profile_name not in list_profiles():
            errors.append(f"missing profile {profile_name}")
            continue
        profile = load_profile(profile_name)
        text = render_template("AGENTS.eval.md", profile)
        if "{{" in text:
            errors.append(f"unrendered placeholder in {profile_name} AGENTS.eval.md")
        if "immediately delegate" in text.lower() or "立刻派" in text:
            errors.append(f"{profile_name} eval agents still coerce spawn")

    daily = (TEMPLATES / "AGENTS.md").read_text()
    if "must immediately" in daily.lower() or "立刻派" in daily:
        errors.append("daily AGENTS.md still coerces spawn")

    skill = SKILL_FILE
    if not skill.exists():
        errors.append(f"missing skills/{SKILL_NAME}/SKILL.md")
    else:
        body = skill.read_text()
        if f"name: {SKILL_NAME}" not in body:
            errors.append(f"skill name must be {SKILL_NAME}")
        for needle in (
            "default_subagent_model",
            "gpt-6-astra",
            "gpt-5.6-luna",
            "hide_spawn_agent_metadata",
        ):
            if needle not in body:
                errors.append(f"skill missing {needle}")
        lowered = body.lower()
        if "this is the cheapest" in lowered or "最便宜" in body:
            errors.append("skill must not claim cheapest")

    for profile_name in ("astra-luna", "astra-terra"):
        if profile_name not in list_profiles():
            continue
        profile = load_profile(profile_name)
        if profile.subagent_model == profile.parent_model:
            errors.append(f"{profile_name} child clones parent")

    for suite_name in KNOWN:
        suite = load_suite(suite_name)
        if suite_name == "terminal-bench-4":
            smoke = suite.smoke_ids()
            if len(smoke) < 2:
                errors.append("TB 4.0 smoke list too short")
            blocked = set(suite.raw.get("gpu_or_multicontainer") or [])
            overlap = set(smoke) & blocked
            if overlap:
                errors.append(f"TB smoke includes GPU tasks: {sorted(overlap)}")
        if suite.role == "cost_calibration" and suite.default_delegation_expected != "optional":
            errors.append(f"{suite_name} calibration must default optional")

    if classify_routing("true", 0) != "routing_missed":
        errors.append("true+spawn0 must be routing_missed")
    if classify_routing("optional", 0) != "routing_ok":
        errors.append("optional+spawn0 must be routing_ok")
    if classify_routing("false", 1) != "over_delegated":
        errors.append("false+spawn>0 must be over_delegated")
    return errors
