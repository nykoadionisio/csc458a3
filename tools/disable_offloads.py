#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess


FEATURES = ("tso", "gso", "gro", "lro", "sg")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Disable packet aggregation/segmentation offloads on selected Mininet interfaces."
    )
    parser.add_argument("interfaces", nargs="+")
    args = parser.parse_args()

    if shutil.which("ethtool") is None:
        print("ERROR: ethtool is not installed")
        return 1

    failed = False
    for interface in args.interfaces:
        check = subprocess.run(
            ["ip", "link", "show", "dev", interface],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if check.returncode != 0:
            print(f"ERROR: interface {interface!r} does not exist")
            failed = True
            continue
        print(f"{interface}:")
        for feature in FEATURES:
            result = subprocess.run(
                ["ethtool", "-K", interface, feature, "off"],
                text=True,
                capture_output=True,
                check=False,
            )
            # Some veth/OVS combinations report fixed or unsupported features.
            status = "off" if result.returncode == 0 else "unsupported/fixed"
            print(f"  {feature}: {status}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
