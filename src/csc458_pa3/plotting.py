from __future__ import annotations
import csv,json
from pathlib import Path
from statistics import fmean,stdev
import matplotlib
matplotlib.use('Agg')
from .analysis import build_summary,rows,load_json
from .parsers import parse_ping


def _line(xs,ys,path,xlabel,ylabel,title,limit=None):
    import matplotlib.pyplot as plt
    if not xs:raise ValueError(f'No measurements for {path.name}')
    fig,ax=plt.subplots(figsize=(7.4,4.2))
    ax.plot(xs,ys,linewidth=1.2)
    if limit is not None:ax.axhline(limit,linestyle='--',label=f'Configured netem limit: {limit}');ax.legend()
    ax.set(xlabel=xlabel,ylabel=ylabel,title=title);ax.grid(True,alpha=.25)
    fig.tight_layout();fig.savefig(path,dpi=170);plt.close(fig)
    return path


def plot_run(run_dir):
    run=Path(run_dir);summary=build_summary(run);m=load_json(run/'metadata.json')
    out=[];tag=f"{summary['algorithm'].upper()}, limit={summary['buffer_packets']} packets"
    specs=[('tcp.csv','cwnd_packets','cwnd.png','Congestion window (segments)','Remote server S: congestion window'),
           ('tcp.csv','rtt_ms','tcp_rtt.png','Smoothed TCP RTT (ms)','TCP RTT from ss'),
           ('queue.csv','backlog_packets','queue.png','Bottleneck qdisc backlog (packets)','Netem backlog (includes emulated delay)')]
    for name,field,filename,ylabel,title in specs:
        data=[r for r in rows(run/name) if r.get(field,'') not in ('',None)]
        out.append(_line([float(r['time_s']) for r in data],[float(r[field]) for r in data],run/filename,
                         'Time since data detected (s)',ylabel,f'{title}\n{tag}',m['buffer_packets'] if name=='queue.csv' else None))
    ping=[r for r in parse_ping((run/'loaded_ping.txt').read_text()) if r['epoch_s'] is not None]
    out.append(_line([r['epoch_s']-m['data_detected_epoch_s'] for r in ping],[r['rtt_ms'] for r in ping],
                     run/'loaded_ping_rtt.png','Time since data detected (s)','Ping RTT (ms)',f'Home D to remote server S: loaded RTT\n{tag}'))
    # With -R, client.json is emitted by D and contains RECEIVER interval goodput.
    client=load_json(run/'client.json');ints=[r['sum'] for r in client.get('intervals',[]) if 'sum' in r]
    out.append(_line([float(x['end']) for x in ints],[float(x['bits_per_second'])/1e6 for x in ints],
                     run/'throughput.png','iperf transfer time (s)','Receiver goodput (Mbit/s)',f'Home download: receiver interval goodput\n{tag}'))
    return out


METRICS=[('goodput',('throughput','receiver_mbps'),'Receiver goodput (Mbit/s)'),
         ('loaded_rtt',('loaded_ping_rtt_ms','mean'),'Mean loaded ping RTT (ms)'),
         ('rtt_inflation',('loaded_rtt_inflation_ms',),'RTT inflation above baseline (ms)'),
         ('backlog',('queue_packets','mean'),'Mean bottleneck qdisc backlog (packets)'),
         ('retransmissions',('throughput','sender_retransmits'),'Sender retransmissions (full transfer)'),
         ('drops',('queue_drop_delta',),'Bottleneck drops (analysis window)')]


def _metric(s,path):
    for key in path:s=s.get(key) if isinstance(s,dict) else None
    return float(s) if s is not None else None


def aggregate(runs):
    groups={};seen=set()
    for label,path in runs:
        p=Path(path).resolve()
        if p in seen:raise ValueError(f'Duplicate run directory: {p}')
        seen.add(p);s=build_summary(p)
        groups.setdefault(label,[]).append(s)
    out=[]
    for label,group in groups.items():
        fingerprints={(s['algorithm'],s['buffer_packets'],s['duration_s'],s.get('kernel'),s.get('experiment'),s.get('iperf_reverse')) for s in group}
        if len(fingerprints)!=1:raise ValueError(f'Inconsistent trials under label {label}')
        row=dict(label=label,runs=len(group),algorithm=group[0]['algorithm'],limit_packets=group[0]['buffer_packets'])
        for name,path,_ in METRICS:
            values=[_metric(s,path) for s in group]
            if any(v is None for v in values):raise ValueError(f'Missing {name} for {label}; not plotted as zero.')
            row[name+'_mean']=fmean(values);row[name+'_sd']=stdev(values) if len(values)>1 else 0.
        out.append(row)
    return out


def plot_comparison(runs,output):
    import matplotlib.pyplot as plt
    table=aggregate(runs)
    if not table:raise ValueError('No trials supplied')
    # Every group compares the same path profile. A label cannot hide config changes.
    configurations=[]
    for _,p in runs:
        s=build_summary(p)
        configurations.append({name:(v.get('kind'),v.get('rate_mbps'),v.get('delay_ms'),None if name=='r/r-right' else v.get('limit_packets')) for name,v in s.get('configuration',{}).items()})
    if any(c!=configurations[0] for c in configurations):raise ValueError('Do not mix different path profiles in a comparison.')
    dest=Path(output);dest.mkdir(parents=True,exist_ok=True)
    csv_path=dest/'comparison.csv'
    with csv_path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(table[0]));w.writeheader();w.writerows(table)
    result=[csv_path]
    for name,_,ylabel in METRICS:
        fig,ax=plt.subplots(figsize=(7.4,4.3));positions=list(range(len(table)))
        ax.bar(positions,[r[name+'_mean'] for r in table],yerr=[r[name+'_sd'] for r in table],capsize=4)
        # Show individual runs to avoid overstating what SD from n=2 means.
        for i,(label,_) in enumerate([(r['label'],r) for r in table]):
            vals=[_metric(build_summary(p),next(path for key,path,_ in METRICS if key==name)) for l,p in runs if l==label]
            ax.plot([i]*len(vals),vals,'o',fillstyle='none')
        ax.set_xticks(positions,[r['label'] for r in table]);ax.set(ylabel=ylabel,title='Trial mean and sample SD; circles show individual trials')
        ax.grid(True,axis='y',alpha=.25);fig.tight_layout()
        p=dest/f'comparison_{name}.png';fig.savefig(p,dpi=170);plt.close(fig);result.append(p)
    # The central objective: goodput versus latency, not a predetermined winner.
    fig,ax=plt.subplots(figsize=(7.4,4.3))
    for r in table:
        x,y=r['loaded_rtt_mean'],r['goodput_mean'];ax.plot(x,y,'o');ax.annotate(r['label'],(x,y),xytext=(5,5),textcoords='offset points')
    ax.set(xlabel='Mean loaded ping RTT (ms)',ylabel='Receiver goodput (Mbit/s)',title='Throughput-latency tradeoff');ax.grid(True,alpha=.25);fig.tight_layout()
    p=dest/'comparison_tradeoff.png';fig.savefig(p,dpi=170);plt.close(fig);result.append(p)
    return result
