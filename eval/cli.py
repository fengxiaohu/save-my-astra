from __future__ import annotations

import argparse
import json
import sys

from eval.check import check
from eval.download import download_suite
from eval.install_merge import format_verify, verify_install
from eval.profiles import list_profiles
from eval.render import render_profile_to
from eval.run import run_eval
from eval.suites import KNOWN


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="Validate templates, profiles, and routing rules")

    p_render = sub.add_parser("render", help="Render a profile into a CODEX_HOME fragment")
    p_render.add_argument("--profile", default="astra-luna")
    p_render.add_argument("--out", required=True)
    p_render.add_argument("--daily", action="store_true", help="Use daily AGENTS.md, not eval copy")

    p_dl = sub.add_parser("download", help="Fetch official items (not git-vendored)")
    p_dl.add_argument("--suite", required=True, choices=KNOWN)

    p_verify = sub.add_parser("verify", help="Check a CODEX_HOME after install")
    p_verify.add_argument("--profile", default="astra-luna")
    p_verify.add_argument("--codex-home", default=None)

    p_run = sub.add_parser("run", help="Two-arm eval")
    p_run.add_argument("--profile", default="astra-luna")
    p_run.add_argument("--baseline", default="astra-solo")
    p_run.add_argument("--suite", required=True, choices=KNOWN)
    p_run.add_argument("--slice", default="smoke")
    p_run.add_argument("--runtime", default="api", choices=("api", "codex", "harbor"))
    p_run.add_argument("--n", type=int, default=None)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--print-cmd", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "check":
        errors = check()
        if errors:
            print("FAIL")
            for err in errors:
                print(f"- {err}")
            return 1
        print("OK")
        print("profiles:", ", ".join(list_profiles()))
        print("suites:", ", ".join(KNOWN))
        return 0

    if args.cmd == "render":
        from pathlib import Path

        dest = render_profile_to(args.profile, Path(args.out), eval_agents=not args.daily)
        print(dest)
        return 0

    if args.cmd == "download":
        path = download_suite(args.suite)
        print(path)
        return 0

    if args.cmd == "verify":
        from pathlib import Path

        home = Path(args.codex_home) if args.codex_home else Path.home() / ".codex"
        errors, summary = verify_install(home, args.profile)
        print(format_verify(errors, summary))
        return 1 if errors else 0

    if args.cmd == "run":
        path = run_eval(
            profile=args.profile,
            baseline=args.baseline,
            suite_name=args.suite,
            slice_name=args.slice,
            runtime=args.runtime,
            n=args.n,
            limit=args.limit,
            dry_run=args.dry_run,
            print_cmd=args.print_cmd,
        )
        print(path)
        if getattr(path, "read_text", None):
            print(json.loads(path.read_text()).get("question", ""))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
