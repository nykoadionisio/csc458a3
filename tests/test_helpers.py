"""Rootless staff-helper tests; fixture measurements are synthetic, not results."""
from __future__ import annotations
import ast,csv,json
from pathlib import Path
import pytest
from csc458_pa3.parsers import parse_tc_qdisc,parse_ss_data,parse_ping
from csc458_pa3.analysis import build_summary,percentile
from csc458_pa3.plotting import aggregate,plot_run,plot_comparison


def tc(limit=60,handle='10:',kind='netem'):
    return (f'qdisc {kind} {handle} root refcnt 2 limit {limit} delay 5ms rate 10Mbit\n'
            ' Sent 300000 bytes 300 pkt (dropped 4, overlimits 0 requeues 0)\n'
            ' backlog 1.5Kb 12p requeues 0\n')


def ss(port=5301,cca='reno',state=True):
    return (('ESTAB ' if state else '')+f'0 0 10.0.1.1:5201 10.0.2.2:{port}\n'
            f'  {cca} wscale:7,7 rtt:51.5/2.0 mss:960 cwnd:73 bytes_sent:96000 '
            'bytes_acked:95000 retrans:0/3\n')


def dump_csv(path,values):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(values[0]));w.writeheader();w.writerows(values)


def fixture_run(path,cca='reno',limit=60,goodput=8.8):
    path.mkdir()
    config={'r/r-right':{'kind':'netem','rate_mbps':10.,'delay_ms':5.}}
    m=dict(recording_status='complete',algorithm=cca,buffer_packets=limit,duration_s=40,
           data_detected_epoch_s=1000,kernel='test-only',configuration=config,data_client_port=5301,schema_version=3,experiment='home-download',iperf_reverse=True)
    (path/'metadata.json').write_text(json.dumps(m))
    client=dict(start=dict(test_start=dict(reverse=1,protocol='TCP',num_streams=1),connected=[dict(local_host='10.0.2.2',local_port=5301,remote_host='10.0.1.1',remote_port=5201)]),end=dict(sum_sent=dict(bits_per_second=9e6,retransmits=8),sum_received=dict(seconds=40,bits_per_second=goodput*1e6)),
                intervals=[{'sum':{'end':i,'bits_per_second':9e6}} for i in range(1,41)])
    (path/'client.json').write_text(json.dumps(client))
    dump_csv(path/'queue.csv',[dict(time_s=i,epoch_s=1000+i,backlog_packets=30 if i<=35 else 0,backlog_bytes=30000,drops=i,overlimits=0) for i in range(43)])
    dump_csv(path/'tcp.csv',[dict(time_s=i,epoch_s=1000+i,cwnd_packets=100,rtt_ms=70,bytes_sent=10000*i,bytes_acked=9000*i,retrans_total=4,congestion_control=cca,mss_bytes=960) for i in range(40)])
    (path/'baseline_ping.txt').write_text('64 bytes: time=50.0 ms\n'*8)
    (path/'loaded_ping.txt').write_text('\n'.join(f'[{1000+i/5:.6f}] 64 bytes: icmp_seq={i} time=70.0 ms' for i in range(210)))
    return path


def test_netem_stats_and_units():
    q=parse_tc_qdisc(tc());assert q.kind=='netem';assert q.handle=='10:'
    assert q.backlog_bytes==1536 and q.backlog_packets==12 and q.limit_packets==60
    assert q.delay_ms==5 and q.rate_mbps==10


def test_handle_not_substring():
    q=parse_tc_qdisc(tc(handle='110:')+tc(limit=240))
    assert q.limit_packets==240


def test_absent_qdisc_is_not_zero():
    with pytest.raises(ValueError):parse_tc_qdisc('')


def test_missing_stats_is_not_zero():
    with pytest.raises(ValueError):parse_tc_qdisc('qdisc netem 10: root limit 60 delay 20ms rate 10Mbit')


@pytest.mark.parametrize('state',[True,False])
def test_exact_data_socket_and_control_exclusion(state):
    data=parse_ss_data(ss(49000,'cubic')+ss(5301,'reno',state),5301)
    assert data['congestion_control']=='reno' and data['cwnd_packets']==73
    assert data['mss_bytes']==960 and data['retrans_total']==3
    assert parse_ss_data(ss(49000),5301) is None


def test_timestamped_ping_retains_timing():
    a=parse_ping('[1002.40] 64 bytes: time=72.6 ms\nno answer yet\n[1003.20] 64 bytes: time=80 ms')
    assert len(a)==2 and a[1]['epoch_s']==1003.2


def test_percentile_interpolation():
    assert percentile([10,20],50)==15
    assert percentile([],95) is None


def test_summary_steady_window(tmp_path):
    run=fixture_run(tmp_path/'a');s=build_summary(run)
    assert s['analysis_window_s']==[5,35]
    assert s['throughput']['receiver_mbps']==8.8
    assert s['queue_packets']['mean']==30 # drain zeros excluded
    assert s['loaded_rtt_inflation_ms']==20
    assert s['queue_drop_delta']==30
    assert s['observed_algorithms']==['reno']


def test_failed_transfer_rejected(tmp_path):
    run=fixture_run(tmp_path/'a');(run/'client.json').write_text('{"error":"unable to connect"}')
    with pytest.raises(ValueError,match='iperf3 failed'):build_summary(run)


def test_incomplete_json_rejected(tmp_path):
    run=fixture_run(tmp_path/'a');(run/'client.json').write_text('{')
    with pytest.raises(ValueError):build_summary(run)


def test_wrong_algorithm_rejected(tmp_path):
    run=fixture_run(tmp_path/'a');p=run/'metadata.json';m=json.loads(p.read_text());m['algorithm']='bbr';p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='disagrees'):build_summary(run)


def test_aborted_run_rejected(tmp_path):
    run=fixture_run(tmp_path/'a');p=run/'metadata.json';m=json.loads(p.read_text());m['recording_aborted']=True;p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='aborted'):build_summary(run)


def test_empty_ping_rejected(tmp_path):
    run=fixture_run(tmp_path/'a');(run/'loaded_ping.txt').write_text('100% packet loss')
    with pytest.raises(ValueError,match='ping samples'):build_summary(run)


def test_partial_window_rejected(tmp_path):
    run=fixture_run(tmp_path/'a');p=run/'tcp.csv';p.write_text('\n'.join(p.read_text().splitlines()[:8]))
    with pytest.raises(ValueError,match='analysis window'):build_summary(run)


def test_queue_reset_rejected(tmp_path):
    run=fixture_run(tmp_path/'a')
    dump_csv(run/'queue.csv',[dict(time_s=i,backlog_packets=20,drops=100-i) for i in range(43)])
    with pytest.raises(ValueError,match='Counter decreased'):build_summary(run)


def test_repeated_trial_mean_sd(tmp_path):
    a=fixture_run(tmp_path/'a',goodput=8);b=fixture_run(tmp_path/'b',goodput=10)
    result=aggregate([('Reno',a),('Reno',b)])[0]
    assert result['goodput_mean']==9 and result['goodput_sd']==pytest.approx(2**.5)
    assert result['runs']==2


def test_duplicate_trial_rejected(tmp_path):
    a=fixture_run(tmp_path/'a')
    with pytest.raises(ValueError,match='Duplicate'):aggregate([('a',a),('a',a)])


def test_inconsistent_group_rejected(tmp_path):
    a=fixture_run(tmp_path/'a');b=fixture_run(tmp_path/'b',limit=240)
    with pytest.raises(ValueError,match='Inconsistent'):aggregate([('x',a),('x',b)])


def test_all_figures(tmp_path):
    a=fixture_run(tmp_path/'a');b=fixture_run(tmp_path/'b',cca='vegas',goodput=8.5)
    files=plot_run(a);assert len(files)==5 and all(p.stat().st_size>1000 for p in files)
    files=plot_comparison([('Reno',a),('Vegas',b)],tmp_path/'figs')
    assert len(files)==8 and any(p.name=='comparison_tradeoff.png' for p in files)


def test_cli_topology_check_with_fakes(tmp_path):
    from csc458_pa3.interactive_lab import InteractiveMixin,EXPECTED_INTERFACES
    class Intf:
        def __init__(self,n):self.name=n
    class Link:
        def __init__(self,a,b):self.intf1=Intf(a);self.intf2=Intf(b)
    class Host:
        def __init__(self,ifs):self.ifs=ifs
        def intfNames(self):return list(self.ifs)+['lo']
    class Net(dict):pass
    class Lab(InteractiveMixin):pass
    lab=object.__new__(Lab);lab.mn=Net({n:Host(v) for n,v in EXPECTED_INTERFACES.items()})
    lab.mn.links=[Link('s-data','r-left'),Link('r-right','d-data')]
    assert lab._topology_errors()==[]
    lab.mn['b']=Host(set());assert lab._topology_errors()


def test_python_310_syntax():
    root=Path(__file__).resolve().parents[1]
    for sub in ('src','tools'):
        for p in (root/sub).rglob('*.py'):ast.parse(p.read_text(),feature_version=(3,10))


def test_recorder_arms_then_measures_without_network_mutation(tmp_path,monkeypatch):
    from csc458_pa3 import monitor
    calls=[];counter={'n':0}
    class Process:
        def poll(self):return 0
    class Node:
        def popen(self,args,**kwargs):
            calls.append(args);return Process()
    def fake_capture(node,args,timeout=3):
        calls.append(args)
        if args[0]=='ss':
            counter['n']+=1
            return '' if counter['n']==1 else ss(5309,'vegas')
        if args[0]=='tc':return tc()
        raise AssertionError(args)
    monkeypatch.setattr(monitor,'capture',fake_capture)
    m=dict(duration_s=.02,buffer_packets=60,algorithm='vegas',data_client_port=5309)
    session=monitor.RecordingSession(Node(),Node(),Node(),tmp_path,m,interval=.03,arm_timeout=2)
    session.start();assert session.done.wait(6)
    saved=json.loads((tmp_path/'metadata.json').read_text())
    assert saved['recording_status']=='complete'
    assert saved['detected_algorithm']=='vegas'
    assert (tmp_path/'queue.csv').exists() and (tmp_path/'tcp.csv').exists()
    assert all(cmd[0] in {'ss','tc','ping'} for cmd in calls)
    assert all('replace' not in cmd and 'add' not in cmd for cmd in calls)


def test_recorder_times_out_without_fake_data(tmp_path,monkeypatch):
    from csc458_pa3 import monitor
    monkeypatch.setattr(monitor,'capture',lambda *a,**kw:'')
    m=dict(duration_s=.02,buffer_packets=60,algorithm='reno',data_client_port=5319)
    session=monitor.RecordingSession(object(),object(),object(),tmp_path,m,interval=.02,arm_timeout=.1)
    session.start();assert session.done.wait(3)
    saved=json.loads((tmp_path/'metadata.json').read_text())
    assert saved['recording_status']=='failed' and 'No iperf data' in saved['recording_error']


def test_upload_not_mislabelled_as_download(tmp_path):
    run=fixture_run(tmp_path/'a');p=run/'client.json';data=json.loads(p.read_text())
    data['start']['test_start']['reverse']=0;p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='reverse-mode'):build_summary(run)


def test_wrong_client_port_in_json_is_rejected(tmp_path):
    run=fixture_run(tmp_path/'a');p=run/'client.json';data=json.loads(p.read_text())
    data['start']['connected'][0]['local_port']=5999;p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='endpoints/--cport'):build_summary(run)


def test_old_release_results_rejected(tmp_path):
    run=fixture_run(tmp_path/'a');p=run/'metadata.json';data=json.loads(p.read_text())
    data['schema_version']=2;p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='revision-3'):build_summary(run)


def test_sender_vs_receiver_socket_not_confused():
    # The client's cwnd is not the data sender's cwnd during a download.
    receiver=ss().replace('10.0.1.1:5201 10.0.2.2:5301','10.0.2.2:5301 10.0.1.1:5201')
    assert parse_ss_data(receiver,5301) is None
    assert parse_ss_data(ss(),5301)['local_port']==5201
    assert parse_ss_data(ss(),5301)['peer_port']==5301


def test_wrong_peer_address_rejected():
    assert parse_ss_data(ss().replace('10.0.2.2:', '10.0.9.9:'),5301) is None


def test_full_duplex_profile_has_four_distinct_egresses():
    from csc458_pa3.model import QDISC_PROFILE,EXPECTED_LINKS,EXPECTED_INTERFACES
    assert len(EXPECTED_LINKS)==2 and len(EXPECTED_INTERFACES)==3
    assert len(QDISC_PROFILE)==4
    assert sum(x[3] for x in QDISC_PROFILE)==50
    assert [(x[0],x[1]) for x in QDISC_PROFILE if x[4] is None]==[('r','r-right')]
    assert next(x for x in QDISC_PROFILE if x[0]=='d')[2:]==(10.,5.,10000)


def test_recorder_monitors_sender_and_pings_from_home(tmp_path,monkeypatch):
    from csc458_pa3 import monitor
    calls=[]
    class Process:
        def poll(self): return 0
    class Node:
        def __init__(self,name): self.name=name
        def popen(self,args,**kw): calls.append((self.name,args));return Process()
    def fake_capture(node,args,timeout=3):
        calls.append((node.name,args))
        if args[0]=='ss': return ss(5310,'vegas')
        if args[0]=='tc': return tc()
        raise AssertionError(args)
    monkeypatch.setattr(monitor,'capture',fake_capture)
    m=dict(duration_s=.01,buffer_packets=60,algorithm='vegas',data_client_port=5310)
    session=monitor.RecordingSession(Node('s'),Node('r'),Node('d'),tmp_path,m,interval=.03,arm_timeout=2)
    session.start();assert session.done.wait(6)
    assert any(name=='s' and cmd[0]=='ss' for name,cmd in calls)
    assert any(name=='r' and cmd[0]=='tc' for name,cmd in calls)
    pings=[(name,cmd) for name,cmd in calls if cmd[0]=='ping']
    assert len(pings)==1 and pings[0][0]=='d' and pings[0][1][-1]=='10.0.1.1'


def test_recorded_cca_must_match_at_sender(tmp_path,monkeypatch):
    from csc458_pa3 import monitor
    monkeypatch.setattr(monitor,'capture',lambda *a,**kw:ss(5301,'cubic'))
    m=dict(duration_s=.01,buffer_packets=60,algorithm='reno',data_client_port=5301)
    session=monitor.RecordingSession(object(),object(),object(),tmp_path,m,arm_timeout=1)
    session.start();assert session.done.wait(3)
    saved=json.loads((tmp_path/'metadata.json').read_text())
    assert saved['recording_status']=='failed' and 'Sender S is using cubic' in saved['recording_error']


def test_longer_reverse_path_was_accounted_for():
    from csc458_pa3.model import QDISC_PROFILE, BASE_PROPAGATION_RTT_MS
    assert sum(item[3] for item in QDISC_PROFILE)==BASE_PROPAGATION_RTT_MS==50
    rate=next(item[2] for item in QDISC_PROFILE if item[1]=='r-right')
    bdp=rate*1e6*(BASE_PROPAGATION_RTT_MS/1000)/(8*1000)
    assert bdp==62.5
