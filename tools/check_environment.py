#!/usr/bin/env python3
"""Read-only checks. No package installation, sysctl edits or module loading."""
import importlib.util,platform,shutil,sys
from pathlib import Path

def main():
    bad=[]
    print('CSC458 PA3 revision 3 | system Python | home-download S--R--D line')
    print('Kernel:',platform.release(),'Python:',sys.version.split()[0])
    for cmd in ['mn','ip','tc','ss','ethtool','iperf3','ping','sysctl','modprobe']:
        found=shutil.which(cmd);print(f'{cmd:12s} {found or "MISSING"}')
        if not found:bad.append(cmd)
    for mod in ['mininet','matplotlib','pytest']:
        ok=importlib.util.find_spec(mod) is not None;print(f'Python {mod:12s} {"OK" if ok else "MISSING"}')
        if not ok:bad.append(mod)
    p=Path('/proc/sys/net/ipv4/tcp_available_congestion_control')
    print('TCP algorithms:',p.read_text().strip() if p.exists() else 'unknown')
    print('Open vSwitch and an OpenFlow controller are NOT required for this PA3.')
    print('FAIL: '+', '.join(bad) if bad else 'Basic environment checks passed. Verify all four CCAs in the lab.')
    return bool(bad)
if __name__=='__main__':raise SystemExit(main())
