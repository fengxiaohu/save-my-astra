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


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


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
    p_run.add_argument("--n", type=_positive_int, default=None)
    p_run.add_argument("--limit", type=_positive_int, default=None)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--print-cmd", action="store_true")

    phase = sub.add_parser("phase3a", help="Frozen Phase 3A: prepare offline, execute only after Gate 0")
    phase_sub = phase.add_subparsers(dest="phase_action", required=True)
    prepare = phase_sub.add_parser("prepare", help="Freeze local inputs; never starts Harbor or Codex")
    prepare.add_argument("--tasks", required=True, help="Local snapshot containing the four task directories")
    prepare.add_argument("--codex-version", required=True, help="Exact CLI package version, not latest")
    prepare.add_argument("--out", required=True)
    inspect = phase_sub.add_parser("inspect", help="Validate frozen inputs and Gate 0 evidence without execution")
    inspect.add_argument("--out", required=True)
    execute = phase_sub.add_parser("execute", help="Starts real experiments; requires frozen Gate 0 evidence")
    execute.add_argument("--out", required=True)
    execute.add_argument("--telemetry", required=True, help="Fresh external quota and resource JSON")
    execute.add_argument("--auth-source", required=True, help="ChatGPT auth.json; never copied to reports")

    args = parser.parse_args(argv)

    if args.cmd == "phase3a":
        from pathlib import Path
        from eval.phase3 import prepare, execute, validate_manifest, validate_gate0

        try:
            if args.phase_action == "prepare":
                print(prepare(Path(args.out), Path(args.tasks), args.codex_version))
                print("Prepared only. No model called; Gate 0 is still required.")
            elif args.phase_action == "inspect":
                manifest = validate_manifest(Path(args.out))
                print("Frozen inputs match.")
                validate_gate0(Path(args.out), manifest)
                print("Gate 0 artifact references match. No runtime probe executed.")
            else:
                print(execute(Path(args.out), Path(args.telemetry), Path(args.auth_source)))
        except (OSError, ValueError, KeyError) as exc:
            print(f"Phase 3A blocked: {exc}", file=sys.stderr)
            return 1
        return 0

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
