#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from csc458_pa3.analysis import write_summary
from csc458_pa3.plotting import plot_run
p=argparse.ArgumentParser();p.add_argument('run_dir');a=p.parse_args()
try:
    print(write_summary(a.run_dir))
    for path in plot_run(a.run_dir):print(path)
except (ValueError,OSError) as exc:p.error(str(exc))
