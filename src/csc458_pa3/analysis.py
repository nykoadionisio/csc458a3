"""Validated, windowed summaries. Invalid/missing experiments are not zeros."""
from __future__ import annotations
import csv,json,math
from pathlib import Path
from statistics import fmean
from .parsers import parse_ping
from .model import SCHEMA_VERSION, EXPERIMENT, SERVER_IP, HOME_IP, SERVER_PORT


def load_json(path:Path)->dict:
    try:data=json.loads(path.read_text())
    except (FileNotFoundError,json.JSONDecodeError) as exc:raise ValueError(f'Missing/incomplete JSON: {path}') from exc
    if not isinstance(data,dict):raise ValueError(f'Expected JSON object: {path}')
    return data


def percentile(values,p):
    if not values:return None
    a=sorted(values);i=(len(a)-1)*p/100;lo=math.floor(i);hi=math.ceil(i)
    return a[lo]+(a[hi]-a[lo])*(i-lo)


def stats(values):
    a=[float(v) for v in values if v is not None]
    return dict(count=len(a),mean=fmean(a) if a else None,median=percentile(a,50),
                p95=percentile(a,95),min=min(a) if a else None,max=max(a) if a else None)


def rows(path:Path):
    if not path.exists():raise ValueError(f'Missing measurement: {path}')
    with path.open(newline='') as f:return list(csv.DictReader(f))


def numeric_rows(data,column,start,end):
    out=[]
    for r in data:
        if r.get(column,'') in ('',None):continue
        if start<=float(r['time_s'])<=end:out.append(float(r[column]))
    return out


def counter_delta(data,column,start,end):
    # The closest sample at/before each boundary reduces interval bias.
    def at(t):
        rr=[r for r in data if r.get(column,'') not in ('',None) and float(r['time_s'])<=t]
        return float(rr[-1][column]) if rr else None
    a,b=at(start),at(end)
    if a is None or b is None:return None
    if b<a:raise ValueError('Counter decreased: qdisc was reset mid-trial.')
    return b-a


def validate_download_json(client:dict, metadata:dict) -> None:
    """Reject accidental uploads or mis-bound streams before drawing figures."""
    start=client.get('start',{})
    spec=start.get('test_start',{})
    if spec.get('reverse') not in (1,True):
        raise ValueError('Expected reverse-mode download (-R), not an upload.')
    if spec.get('protocol')!='TCP' or spec.get('num_streams')!=1:
        raise ValueError('Use one TCP data stream; do not add -u, -P or --bidir.')
    connections=start.get('connected',[])
    expected=(HOME_IP,metadata['data_client_port'],SERVER_IP,SERVER_PORT)
    actual=[(c.get('local_host'),c.get('local_port'),c.get('remote_host'),c.get('remote_port')) for c in connections]
    if actual!=[expected]:
        raise ValueError(f'iperf3 endpoints/--cport do not match the recorded download: {actual!r}')


def build_summary(run_dir)->dict:
    run=Path(run_dir);m=load_json(run/'metadata.json');client=load_json(run/'client.json')
    if m.get('schema_version')!=SCHEMA_VERSION or m.get('experiment')!=EXPERIMENT or not m.get('iperf_reverse'):
        raise ValueError('This is not a home-download revision-3 run; do not mix previous datasets.')
    if m.get('recording_status')!='complete' or m.get('recording_error') or m.get('recording_aborted'):
        raise ValueError('Recording failed/aborted/not complete; see metadata.json and monitor_errors.log.')
    if client.get('error'):raise ValueError(f"iperf3 failed: {client['error']}")
    validate_download_json(client,m)
    end=client.get('end',{});sent=end.get('sum_sent',{});received=end.get('sum_received',{})
    if not received or received.get('seconds',0)<=0:raise ValueError('No completed iperf receiver result.')
    duration=float(m['duration_s'])
    if float(received['seconds'])<.9*duration:raise ValueError('Transfer ended too early.')
    q=rows(run/'queue.csv');tcp=rows(run/'tcp.csv')
    start=5.0;finish=duration-5.0
    if finish<=start:raise ValueError('Record at least 15 seconds.')
    tselected=[r for r in tcp if start<=float(r['time_s'])<=finish]
    if not tselected or max(float(r['time_s']) for r in tcp)<finish:
        raise ValueError('TCP data not observed over the analysis window; verify that --cport matches record CLIENT_PORT (the peer port observed at S).')
    observed=sorted({r['congestion_control'] for r in tselected if r.get('congestion_control')})
    if observed!=[m['algorithm']]:
        raise ValueError(f"Declared CCA {m['algorithm']} disagrees with data-socket observations {observed}.")
    if not q or max(float(r['time_s']) for r in q)<finish:raise ValueError('Queue recording ended too early.')
    b=parse_ping((run/'baseline_ping.txt').read_text())
    ping=parse_ping((run/'loaded_ping.txt').read_text())
    origin=m['data_detected_epoch_s']
    loaded=[x['rtt_ms'] for x in ping if x['epoch_s'] is not None and start<=x['epoch_s']-origin<=finish]
    if not b or not loaded:raise ValueError('No baseline or loaded ping samples; do not treat missing RTT as zero.')
    qs=stats(numeric_rows(q,'backlog_packets',start,finish));cs=stats(numeric_rows(tcp,'cwnd_packets',start,finish))
    br=stats([x['rtt_ms'] for x in b]);lr=stats(loaded)
    ms=sorted({int(r['mss_bytes']) for r in tselected if r.get('mss_bytes')})
    warnings=[]
    if ms!=[960]:warnings.append(f'Observed MSS values {ms}, expected 960; verify -M and tcp_timestamps.')
    if len(loaded)<(finish-start)*5*.8:warnings.append('Many ping replies are missing; RTT statistics cover replies only.')
    goodput=float(received['bits_per_second'])/1e6
    if goodput>10.5:warnings.append('Receiver goodput exceeds the nominal bottleneck; check qdiscs/offloads.')
    return dict(schema_version=SCHEMA_VERSION,experiment=EXPERIMENT,iperf_reverse=True,label=run.name,algorithm=m['algorithm'],buffer_packets=m['buffer_packets'],
                duration_s=duration,analysis_window_s=[start,finish],observed_algorithms=observed,
                observed_mss_bytes=ms,throughput=dict(receiver_mbps=goodput,
                sender_mbps=float(sent['bits_per_second'])/1e6 if 'bits_per_second' in sent else None,sender_retransmits=sent.get('retransmits')),
                cwnd_packets=cs,tcp_rtt_ms=stats(numeric_rows(tcp,'rtt_ms',start,finish)),
                baseline_ping_rtt_ms=br,loaded_ping_rtt_ms=lr,loaded_rtt_inflation_ms=lr['mean']-br['mean'],
                ping_replies_in_window=len(loaded),queue_packets=qs,
                queue_drop_delta=counter_delta(q,'drops',start,finish),warnings=warnings,
                configuration=m.get('configuration',{}),kernel=m.get('kernel'),
                interval_rate_source='D: reverse-mode iperf3 client receiver intervals',
                queue_semantics='netem backlog includes delay-held packets; not an isolated physical FIFO')


def write_summary(run_dir):
    run=Path(run_dir);summary=build_summary(run)
    path=run/'summary.json';path.write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n');return path
