#!/usr/bin/env python3
"""Aggregate explicit repeated labels; never silently skip failed trials."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from csc458_pa3.plotting import plot_comparison

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',action='append',required=True,metavar='LABEL=DIRECTORY')
    p.add_argument('--output',required=True);a=p.parse_args()
    runs=[]
    for text in a.run:
        if '=' not in text:p.error('--run must be LABEL=DIRECTORY')
        label,path=text.split('=',1);runs.append((label,Path(path)))
    try:
        for path in plot_comparison(runs,a.output):print(path)
    except (ValueError,OSError) as exc:p.error(str(exc))
if __name__=='__main__':main()
