"""Empty Mininet CLI plus recording/plotting conveniences."""
from __future__ import annotations
import json,os,platform,shlex,subprocess,time
from dataclasses import asdict
from pathlib import Path
from .monitor import RecordingSession,capture
from .parsers import parse_tc_qdisc,parse_ss_data,parse_ping
from .analysis import write_summary

from .model import (EXPECTED_INTERFACES, EXPECTED_LINKS, QDISC_PROFILE, REVISION,
                    SCHEMA_VERSION, EXPERIMENT, SERVER_IP, HOME_IP, SERVER_PORT)


class InteractiveMixin:
    """No commands here build/configure the student's network."""
    def __init__(self,mn,*,root,script=None):
        self.root=Path(root).resolve();self.session=None
        stamp=time.strftime('%Y%m%d-%H%M%S')+f'-{os.getpid()}'
        self.log=self.root/'commands'/f'mininet-{stamp}.log'
        self.replay=self.log.with_suffix('.cli');self.log.parent.mkdir(exist_ok=True)
        self.uid=int(os.environ.get('SUDO_UID',os.getuid()));self.gid=int(os.environ.get('SUDO_GID',os.getgid()))
        self.script_errors=[]
        super().__init__(mn,script=script)

    def onecmd(self,line):
        # Mininet's source command calls onecmd directly, bypassing precmd.
        # Keep replay scripts parseable; don't log both source and its expansion.
        if line.strip() and not line.lstrip().startswith('#') and not line.startswith('source '):
            with self.log.open('a') as f:f.write(f'[{time.strftime("%H:%M:%S")}] {line}\n')
            with self.replay.open('a') as f:f.write(line+'\n')
        return super().onecmd(line)

    def _path(self,text,folder='runs'):
        p=Path(text);p=(self.root/p).resolve() if not p.is_absolute() else p.resolve()
        if not p.is_relative_to(self.root/folder):raise ValueError(f'Use a path under {folder}/ inside this release.')
        return p

    def _chown(self,path):
        # Only outputs under this release; never follow symlinks.
        path=Path(path)
        if not self.uid or not path.exists():return
        for p in [path]+(list(path.rglob('*')) if path.is_dir() else []):
            if not p.is_symlink():os.chown(p,self.uid,self.gid)

    def _snapshot(self):
        sections=[]
        for name,ifs in EXPECTED_INTERFACES.items():
            if name not in self.mn:continue
            n=self.mn[name]
            commands=[['ip','-br','addr'],['ip','-4','route'],['uname','-r'],
                      ['sysctl','net.ipv4.ip_forward','net.ipv4.tcp_timestamps','net.ipv4.tcp_ecn']]
            for dev in sorted(ifs):
                commands.extend([['tc','-s','qdisc','show','dev',dev],['ethtool','-k',dev],
                                 ['sysctl',f'net.ipv4.conf.{dev}.rp_filter']])
            for cmd in commands:
                try:out=capture(n,cmd)
                except Exception as exc:out=str(exc)
                sections.append(f'=== {name}: {shlex.join(cmd)} ===\n{out}\n')
        return '\n'.join(sections)

    def _topology_errors(self):
        problems=[]
        if set(self.mn.keys())!=set(EXPECTED_INTERFACES):problems.append('Exactly s, r and d must exist; no switch/controller.')
        for name,ifs in EXPECTED_INTERFACES.items():
            if name not in self.mn:continue
            actual=set(self.mn[name].intfNames())-{'lo'}
            if actual!=ifs:problems.append(f'{name}: expected {sorted(ifs)}, found {sorted(actual)}')
        links={frozenset((x.intf1.name,x.intf2.name)) for x in self.mn.links}
        if links!=EXPECTED_LINKS:problems.append('Expected ONLY two direct links s-r and r-d with the documented interface names.')
        return problems

    def do_labcheck(self,_line):
        """Check the S--R--D line and display addresses, routes, qdiscs."""
        errors=self._topology_errors()
        if errors: self.script_errors.extend(errors)
        print('\n'.join(errors) if errors else 'PASS: three nodes / two direct links / interface names.')
        print(self._snapshot())

    def do_record(self,line):
        """record RUN_DIR DURATION_S CCA LIMIT CLIENT_PORT: arm download monitors."""
        try:
            if self.session and not self.session.done.is_set():
                raise ValueError('A recording is active. Use waitrecord/stoprecord.')
            args=shlex.split(line)
            if len(args)!=5:
                raise ValueError('Usage: record runs/reno-buffer60-run1 40 reno 60 5301')
            raw,dur,cca,limit,client_port=args
            duration=float(dur);limit=int(limit);client_port=int(client_port)
            if not 15<=duration<=600 or limit<1 or cca not in {'reno','cubic','vegas','bbr'} or not 1024<=client_port<=65535 or client_port==SERVER_PORT:
                raise ValueError('Duration 15..600 s; positive limit; CCA reno/cubic/vegas/bbr; unique client port 1024..65535 except 5201.')
            problems=self._topology_errors()
            if problems:raise ValueError('; '.join(problems))
            server,router,home=self.mn['s'],self.mn['r'],self.mn['d']
            if parse_ss_data(capture(server,['ss','-tinH','dst',HOME_IP]),client_port):
                raise ValueError('A data connection with this CLIENT_PORT is still active at S.')
            config={}
            for name,dev,rate,delay,fixed_limit in QDISC_PROFILE:
                q=parse_tc_qdisc(capture(self.mn[name],['tc','-s','qdisc','show','dev',dev]))
                if q.kind!='netem' or q.rate_mbps!=rate or q.delay_ms!=delay:
                    raise ValueError(f'{name}/{dev}: expected root netem handle 10:, rate {rate} Mbit/s, delay {delay} ms.')
                expected_limit=limit if fixed_limit is None else fixed_limit
                if q.limit_packets!=expected_limit:
                    raise ValueError(f'{name}/{dev}: expected limit {expected_limit}, found {q.limit_packets}.')
                config[f'{name}/{dev}']=asdict(q)
            if capture(router,['sysctl','-n','net.ipv4.ip_forward']).strip()!='1':
                raise ValueError('Enable IP forwarding at R before recording.')
            available=capture(server,['sysctl','-n','net.ipv4.tcp_available_congestion_control']).split()
            if cca not in available:
                raise ValueError(f'{cca} unavailable at the sender S; load its module first.')
            run=self._path(raw)
            if run.exists() and any(run.iterdir()):
                raise ValueError('Choose a NEW run directory; existing results are never overwritten.')
            # Probe FROM the home host, just as a user would test a remote service.
            time.sleep(1)
            baseline=capture(home,['ping','-n','-I',HOME_IP,'-c','8','-i','0.2','-W','2',SERVER_IP],timeout=20)
            if len(parse_ping(baseline))<6:
                raise ValueError('Home-to-server baseline ping failed; fix connectivity first.')
            run.mkdir(parents=True,exist_ok=True)
            (run/'baseline_ping.txt').write_text(baseline)
            (run/'network_before.txt').write_text(self._snapshot())
            version=subprocess.run(['iperf3','--version'],capture_output=True,text=True,check=False)
            metadata=dict(assignment='CSC458 PA3',revision=REVISION,schema_version=SCHEMA_VERSION,
                experiment=EXPERIMENT,algorithm=cca,buffer_packets=limit,duration_s=duration,
                kernel=platform.release(),iperf_version=version.stdout or version.stderr,
                nominal_ipv4_packet_bytes=1000,mss_bytes=960,data_client_port=client_port,
                iperf_reverse=True,server_node='s',receiver_node='d',server_ip=SERVER_IP,
                receiver_ip=HOME_IP,server_port=SERVER_PORT,ping_origin='d',
                configuration=config,created_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                queue_semantics='r-right netem backlog including packets held to emulate delay')
            self.session=RecordingSession(server,router,home,run,metadata)
            self.session.start()
            print(f'ARMED: {run}\nWaiting at S for download data to D:{client_port}.\nStart the iperf3 server at S, then the client at D with -R and --cport {client_port}.\nRun waitrecord after the client finishes; arming timeout: 180 s.')
        except Exception as exc:
            print(f'ERROR: {exc}');self.script_errors.append(str(exc))

    def do_waitrecord(self,_line):
        """Wait for data detection, then the specified experiment duration."""
        if not self.session:print('No recording armed.');return
        self.session.done.wait()
        run=self.session.run_dir
        (run/'network_after.txt').write_text(self._snapshot())
        if self.session.error:print('RECORDING FAILED:',self.session.error);self.script_errors.append(self.session.error)
        else:print('Recording complete:',run)
        self._chown(run)

    def do_stoprecord(self,_line):
        """Abort a trial; an aborted trial is excluded from summaries."""
        if self.session:self.session.stop();self._chown(self.session.run_dir)
        print('Recording aborted, if one was active. Use a new run directory to repeat.')

    def do_plot(self,line):
        """plot RUN_DIR: validate then create figures (no fabricated missing values)."""
        try:
            p=self._path(shlex.split(line)[0]);write_summary(p)
            from .plotting import plot_run
            for out in plot_run(p):print(out)
            self._chown(p)
        except Exception as exc:print(f'ERROR: {exc}');self.script_errors.append(str(exc))

    def do_summarize(self,line):
        """summarize RUN_DIR: recompute and print the validated summary."""
        try:
            p=self._path(shlex.split(line)[0]);out=write_summary(p);print(out.read_text());self._chown(p)
        except Exception as exc:print(f'ERROR: {exc}');self.script_errors.append(str(exc))

    def close(self):
        if self.session and not self.session.done.is_set():self.session.stop()
        if self.session:self._chown(self.session.run_dir)
        self._chown(self.root/'commands');self._chown(self.root/'figures')

    def do_exit(self,line):self.close();return super().do_exit(line)
    def do_quit(self,line):return self.do_exit(line)
    def do_EOF(self,line):return self.do_exit(line)


def run_interactive(project_root,script=None):
    # Lazy imports: rootless helper tests work without Mininet installed.
    from mininet.net import Mininet
    from mininet.node import Host
    from mininet.link import Link
    from mininet.cli import CLI
    from mininet.log import setLogLevel
    class PA3CLI(InteractiveMixin,CLI):pass
    root=Path(project_root).resolve();os.chdir(root);setLogLevel('info')
    net=Mininet(topo=None,build=False,controller=None,switch=None,host=Host,link=Link,
                autoSetMacs=False,autoStaticArp=False)
    cli=None
    print('CSC458 PA3 home-download revision: EMPTY network. Create S--R--D with TWO links yourself.\nNo switch, bridge, routes, offload settings, or qdiscs have been configured.')
    try:
        cli=PA3CLI(net,root=root,script=script)
        if script and cli.script_errors:raise RuntimeError('Script had helper errors: '+'; '.join(cli.script_errors))
    finally:
        if cli:cli.close()
        net.stop()
