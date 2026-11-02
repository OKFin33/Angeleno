"""Owned local SSH forward: bounded heartbeat/EOF supervision, no remote script."""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time


class HeartbeatLease:
    def __init__(self, seconds, *, clock=time.monotonic):
        self.seconds=seconds;self.clock=clock;self.last=clock();self.buffer=b''
    def feed(self, chunk):
        if not chunk:return 'owner_eof'
        self.buffer+=chunk
        if len(self.buffer)>1024:return 'invalid_heartbeat'
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1)
            if line!=b'heartbeat':return 'invalid_heartbeat'
            self.last=self.clock()
        return None
    def expired(self):return self.clock()-self.last>=self.seconds


def supervise(command, *, seconds, owner_fd, pid_file, parent_pid=None):
    lease=HeartbeatLease(seconds);child=None;reason=None;stop=False
    def stopping(*_):
        nonlocal stop
        stop=True
    previous={s:signal.signal(s,stopping) for s in (signal.SIGTERM,signal.SIGINT)}
    try:
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL)
        # O_EXCL prevents overwriting another instance's ownership record.
        fd=os.open(pid_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as out:json.dump({'pid':child.pid,'guard_pid':os.getpid()},out)
        while True:
            if stop:reason='supervisor_signal';break
            if parent_pid is not None and os.getppid()!=parent_pid:reason='owner_exited';break
            if child.poll() is not None:reason='forward_exited';break
            if lease.expired():reason='heartbeat_expired';break
            readable,_,_=select.select([owner_fd],[],[],min(.2,max(0,seconds-(lease.clock()-lease.last))))
            if readable:
                reason=lease.feed(os.read(owner_fd,1024))
                if reason:break
    finally:
        if child is not None:
            if child.poll() is None:child.terminate()
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=2)
        for s,handler in previous.items():signal.signal(s,handler)
    return {'reason':reason,'forward_returncode':child.returncode,'forward_exited':child.poll() is not None}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,required=True)
    p.add_argument('--pid-file',type=Path,required=True);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    if not 30<=a.seconds<=120:p.error('heartbeat must be30..120seconds')
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    if not command or Path(command[0]).name!='ssh' or '-N' not in command:p.error('only owned SSH pure forwarding is supported')
    result=supervise(command,seconds=a.seconds,owner_fd=0,pid_file=a.pid_file,parent_pid=os.getppid())
    print(json.dumps(result),flush=True)
    return 0 if result['reason'] in ('owner_eof','supervisor_signal','owner_exited') else 1

if __name__=='__main__':raise SystemExit(main())
