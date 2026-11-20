"""Parsers for Linux diagnostic output; no Mininet/root dependencies."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import re


@dataclass(frozen=True)
class QdiscSample:
    kind: str
    handle: str
    limit_packets: int
    backlog_bytes: int
    backlog_packets: int
    drops: int
    overlimits: int
    delay_ms: float | None
    rate_mbps: float | None
    raw: str


def parse_tc_qdisc(text: str, handle: str = '10:') -> QdiscSample:
    """Select EXACTLY the requested qdisc, never a parent/child/fallback.

    A missing qdisc or incomplete stats is an error, not zero occupancy.
    Linux/iproute2 can print byte backlogs as 2500b, 2Kb, or 1.5Mb.
    """
    blocks = re.split(r'(?m)(?=^qdisc )', text)
    for block in blocks:
        h = re.match(r'qdisc (\S+) (\S+) (.*)', block)
        if not h or h[2] != handle:
            continue
        limit = re.search(r'\blimit (\d+)(?:p)?\b', h[3])
        back = re.search(r'backlog\s+([\d.]+)([KMG]?)b\s+(\d+)p', block, re.I)
        drop = re.search(r'dropped (\d+)', block)
        over = re.search(r'overlimits (\d+)', block)
        if not (limit and back and drop):
            raise ValueError(f'Incomplete qdisc {handle} statistics: {block!r}')
        delay = re.search(r'\bdelay\s+([\d.]+)(us|ms|s)\b', h[3])
        rate = re.search(r'\brate\s+([\d.]+)([KMG]?)bit\b', h[3], re.I)
        return QdiscSample(
            kind=h[1], handle=h[2], limit_packets=int(limit[1]),
            backlog_bytes=round(float(back[1]) * 1024 ** ('KMG'.find(back[2].upper())+1 if back[2] else 0)),
            backlog_packets=int(back[3]), drops=int(drop[1]), overlimits=int(over[1]) if over else 0,
            delay_ms=float(delay[1])*{'us':.001,'ms':1,'s':1000}[delay[2]] if delay else None,
            rate_mbps=float(rate[1])*{'':1e-6,'K':1e-3,'M':1,'G':1e3}[rate[2].upper()] if rate else None,
            raw=block.strip())
    raise ValueError(f'No qdisc with handle {handle}; output: {text!r}')


def parse_ss_data(text: str, client_port: int, *,
                  server_ip: str = '10.0.1.1', client_ip: str = '10.0.2.2',
                  server_port: int = 5201) -> dict | None:
    """Select the reverse-mode DATA socket as seen by the server S.

    iperf3 --cport binds D's client/data-receiver port. At the sender S,
    the data connection is LOCAL S:5201 -> PEER D:client_port. The control
    connection also has local port 5201 but a different peer port. Exact
    endpoint matching prevents reporting its cwnd instead of the data flow's.
    """
    blocks, current = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line.strip())
    if current:
        blocks.append(current)

    def endpoint(token):
        try:
            address, port = token.rsplit(':', 1)
            address = address.strip('[]')
            if address.startswith('::ffff:'):
                address = address[7:]
            return address, int(port)
        except (ValueError, TypeError):
            return None

    for lines in blocks:
        endpoints = [endpoint(x) for x in lines[0].split() if ':' in x]
        if len(endpoints) < 2:
            continue
        if endpoints[:2] != [(server_ip, server_port), (client_ip, client_port)]:
            continue
        block = ' '.join(lines)
        def integer(name):
            match = re.search(rf'(?<!\S){name}:(\d+)', block)
            return int(match[1]) if match else None
        rtt = re.search(r'(?<!\S)rtt:([\d.]+)(?:/([\d.]+))?', block)
        cca = re.search(r'(?<!\S)(reno|cubic|vegas|bbr)(?=\s|$)', block)
        retrans = re.search(r'(?<!\S)retrans:(\d+)/(\d+)', block)
        return dict(cwnd_packets=integer('cwnd'),
                    rtt_ms=float(rtt[1]) if rtt else None,
                    bytes_sent=integer('bytes_sent'), bytes_acked=integer('bytes_acked'),
                    retrans_total=int(retrans[2]) if retrans else 0,
                    congestion_control=cca[1] if cca else None,
                    mss_bytes=integer('mss'), local_address=server_ip,
                    local_port=server_port, peer_address=client_ip,
                    peer_port=client_port, raw=block)
    return None


def parse_ping(text: str) -> list[dict]:
    out=[]
    for line in text.splitlines():
        rtt=re.search(r'time[=<]([\d.]+)\s*ms',line)
        if not rtt: continue
        ts=re.search(r'\[([\d.]+)\]',line)
        out.append(dict(epoch_s=float(ts[1]) if ts else None, rtt_ms=float(rtt[1])))
    return out


def qdisc_dict(text: str) -> dict:
    return asdict(parse_tc_qdisc(text))
