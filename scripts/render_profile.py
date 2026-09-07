#!/usr/bin/env python3
"""Render a profile into a directory. Used by install.sh and Harbor --ak config=."""

from __future__ import annotations

import argparse
from pathlib import Path

from eval.render import render_profile_to


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="astra-luna")
    parser.add_argument("--out", required=True)
    parser.add_argument("--daily", action="store_true")
    args = parser.parse_args()
    print(render_profile_to(args.profile, Path(args.out), eval_agents=not args.daily))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
