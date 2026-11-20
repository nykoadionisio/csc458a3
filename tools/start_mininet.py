#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from csc458_pa3.interactive_lab import run_interactive


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start the empty CSC458 PA3 interactive Mininet laboratory."
    )
    parser.add_argument(
        "--script",
        help="Optional Mininet CLI command file, intended mainly for instructor validation.",
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        print(
            'Run this launcher as shown in the handout:\n'
            '  sudo env PYTHONPATH="$PWD/src" python3 tools/start_mininet.py',
            file=sys.stderr,
        )
        return 2
    project_root = Path(__file__).resolve().parents[1]
    run_interactive(project_root, script=args.script)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
