"""Measurement plus low-rate ping probes: never creates a topology or changes routes/qdiscs/CCA."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import subprocess
import threading
import time
from .model import SERVER_IP, HOME_IP
from .parsers import parse_ss_data, parse_tc_qdisc


def capture(node, argv: list[str], timeout: float = 3.0) -> str:
    """Separate subprocess, not Node.cmd: safe alongside the interactive CLI."""
    p=node.popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try: out,err=p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill();p.communicate();raise RuntimeError(f'Timed out: {argv}')
    if p.returncode: raise RuntimeError(f'{argv}: {err.strip()}')
    return out


def stop_process(p):
    if p is None or p.poll() is not None: return
    p.terminate()
    try:p.wait(timeout=2)
    except subprocess.TimeoutExpired:p.kill();p.wait(timeout=2)


class RecordingSession:
    """Arm now, start measurement on reverse-mode data from S to D:CLIENT_PORT.

    Avoids silently measuring idle time while students copy server/client
    commands. All samples use a common clock; periodic tools are sampled,
    so short queue spikes may be missed. Does not run iperf itself.
    """
    def __init__(self, s, r, d, run_dir: Path, metadata: dict, interval: float=.05,
                 arm_timeout: float=180.0):
        self.s,self.r,self.d,self.run_dir,self.metadata=s,r,d,run_dir,metadata
        self.interval,self.arm_timeout=interval,arm_timeout
        self.stop_event=threading.Event();self.done=threading.Event()
        self.started=threading.Event();self.threads=[];self.error=None
        self.origin_mono=None;self.origin_epoch=None;self.ping=None
        self._lock=threading.Lock()

    def save_metadata(self):
        with self._lock:
            (self.run_dir/'metadata.json').write_text(json.dumps(self.metadata,indent=2,sort_keys=True)+'\n')

    def _fail(self, exc):
        with self._lock:
            self.error=str(exc);self.metadata['recording_error']=self.error
        self.stop_event.set()
        with (self.run_dir/'monitor_errors.log').open('a') as f:f.write(str(exc)+'\n')

    def start(self):
        self.thread=threading.Thread(target=self._run,name='pa3-recorder',daemon=True)
        self.thread.start()

    def _timestamp(self):
        now=time.monotonic()
        return now-self.origin_mono, self.origin_epoch+now-self.origin_mono

    def _queue(self):
        fields=['time_s','epoch_s','backlog_bytes','backlog_packets','drops','overlimits']
        try:
            with (self.run_dir/'queue.csv').open('w',newline='') as f, (self.run_dir/'tc_raw.log').open('w') as raw:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
                while not self.stop_event.is_set():
                    text=capture(self.r,['tc','-s','qdisc','show','dev','r-right'])
                    t,epoch=self._timestamp();raw.write(f'===== {t:.6f} s =====\n{text}\n');raw.flush()
                    q=parse_tc_qdisc(text)
                    if q.kind!='netem' or q.limit_packets!=self.metadata['buffer_packets'] or q.rate_mbps!=10.0 or q.delay_ms!=5.0:
                        raise RuntimeError('Bottleneck qdisc changed during recording; do not change tc mid-trial.')
                    w.writerow(dict(time_s=f'{t:.6f}',epoch_s=f'{epoch:.6f}',
                                    **{k:getattr(q,k) for k in fields[2:]}));f.flush()
                    self.stop_event.wait(self.interval)
        except Exception as exc:self._fail(exc)

    def _tcp(self):
        fields=['time_s','epoch_s','cwnd_packets','rtt_ms','bytes_sent','bytes_acked','retrans_total','congestion_control','mss_bytes']
        try:
            with (self.run_dir/'tcp.csv').open('w',newline='') as f, (self.run_dir/'ss_raw.log').open('w') as raw:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
                while not self.stop_event.is_set():
                    text=capture(self.s,['ss','-tinH','dst',HOME_IP])
                    t,epoch=self._timestamp();raw.write(f'===== {t:.6f} s =====\n{text}\n');raw.flush()
                    data=parse_ss_data(text, self.metadata['data_client_port'])
                    if data:
                        w.writerow(dict(time_s=f'{t:.6f}',epoch_s=f'{epoch:.6f}',
                                        **{k:data.get(k) for k in fields[2:]}));f.flush()
                    self.stop_event.wait(self.interval)
        except Exception as exc:self._fail(exc)

    def _run(self):
        try:
            deadline=time.monotonic()+self.arm_timeout
            self.metadata['recording_status']='armed';self.save_metadata()
            while not self.stop_event.is_set():
                data=parse_ss_data(capture(self.s,['ss','-tinH','dst',HOME_IP]), self.metadata['data_client_port'])
                if data and (data.get('bytes_sent') or 0)>0:break
                if time.monotonic()>deadline:raise RuntimeError(f'No iperf data detected at S for D:CLIENT_PORT within {self.arm_timeout:g} s. Check -R and --cport.')
                self.stop_event.wait(.1)
            if self.stop_event.is_set():return
            self.origin_mono=time.monotonic();self.origin_epoch=time.time()
            if data.get('congestion_control')!=self.metadata['algorithm']:
                raise RuntimeError(f"Sender S is using {data.get('congestion_control')}, not declared {self.metadata['algorithm']}. Check -C at the reverse client.")
            self.metadata.update(recording_status='running',data_detected_epoch_s=self.origin_epoch,
                                 detected_algorithm=data.get('congestion_control'))
            self.save_metadata();self.started.set()
            with (self.run_dir/'loaded_ping.txt').open('w') as ping_file:
                self.ping=self.d.popen(['ping','-D','-n','-I',HOME_IP,'-i','0.2',SERVER_IP],
                                      stdout=ping_file,stderr=subprocess.STDOUT)
                for name,target in [('queue',self._queue),('tcp',self._tcp)]:
                    th=threading.Thread(target=target,name=f'pa3-{name}',daemon=True)
                    self.threads.append(th);th.start()
                # Complete 40-second transfers can take slightly longer to finish
                # their JSON exchange; record a small grace interval then stop.
                self.stop_event.wait(float(self.metadata['duration_s'])+2.0)
                self.stop_event.set()
                for th in self.threads:th.join(timeout=5)
                stop_process(self.ping)
            self.metadata['recording_status']='failed' if self.error else 'complete'
            self.metadata['finished_epoch_s']=time.time();self.save_metadata()
        except Exception as exc:
            self._fail(exc)
            self.metadata['recording_status']='failed';self.save_metadata()
        finally:
            self.stop_event.set();stop_process(self.ping)
            for th in self.threads:th.join(timeout=5)
            self.done.set()

    def stop(self):
        if not self.done.is_set():
            self.metadata['recording_aborted']=True
            self.stop_event.set();self.thread.join(timeout=12)
            self.save_metadata()
