"""Local heartbeat/process cleanup only; no SSH, GPU, network or paid calls."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import unittest
from scripts.forward_guard import HeartbeatLease,supervise

class LeaseTests(unittest.TestCase):
    def test_renewal_spans_prewarm_waiting_and_active_without_wall_cut(self):
        now=[0];lease=HeartbeatLease(90,clock=lambda:now[0])
        # Includes >90s preparation, 300s waiting and a connected call.
        for t in range(0,1001,5):
            now[0]=t;self.assertFalse(lease.expired());self.assertIsNone(lease.feed(b'heartbeat\n'))
        now[0]=1089;self.assertFalse(lease.expired())
        now[0]=1090;self.assertTrue(lease.expired())
    def test_eof_bad_or_partial_input_cannot_renew(self):
        now=[0];lease=HeartbeatLease(90,clock=lambda:now[0]);now[0]=89
        self.assertIsNone(lease.feed(b'heart'));now[0]=90;self.assertTrue(lease.expired())
        self.assertEqual(lease.feed(b'wrong\n'),'invalid_heartbeat')
        self.assertEqual(HeartbeatLease(90).feed(b''),'owner_eof')
        self.assertEqual(HeartbeatLease(90).feed(b'x'*1025),'invalid_heartbeat')

class ProcessTests(unittest.TestCase):
    def run_guard(self,data=None,*,seconds=.05,parent_pid=None,command=None):
        with tempfile.TemporaryDirectory() as tmp:
            read,write=os.pipe()
            try:
                if data is not None:os.write(write,data);os.close(write);write=None
                file=Path(tmp)/'owner.json'
                result=supervise(command or [sys.executable,'-c','import time;time.sleep(30)'],seconds=seconds,owner_fd=read,pid_file=file,parent_pid=parent_pid)
                pid=json.loads(file.read_text())['pid']
                with self.assertRaises(ProcessLookupError):os.kill(pid,0)
                self.assertTrue(result['forward_exited']);return result
            finally:
                os.close(read)
                if write is not None:os.close(write)
    def test_owner_eof_stops_only_owned_forward(self):self.assertEqual(self.run_guard(b'')['reason'],'owner_eof')
    def test_missing_renewal_is_bounded(self):self.assertEqual(self.run_guard()['reason'],'heartbeat_expired')
    def test_bad_renewal_stops_forward(self):self.assertEqual(self.run_guard(b'other\n')['reason'],'invalid_heartbeat')
    def test_parent_death_stops_forward(self):self.assertEqual(self.run_guard(parent_pid=-1)['reason'],'owner_exited')
    def test_unexpected_forward_exit_is_reported(self):self.assertEqual(self.run_guard(seconds=2,command=[sys.executable,'-c','pass'])['reason'],'forward_exited')
    def test_signal_releases_owned_child_and_restores_handler(self):
        before=signal.getsignal(signal.SIGTERM);timer=threading.Timer(.1,lambda:os.kill(os.getpid(),signal.SIGTERM));timer.start()
        try:self.assertEqual(self.run_guard(seconds=2)['reason'],'supervisor_signal')
        finally:timer.cancel();timer.join()
        self.assertEqual(signal.getsignal(signal.SIGTERM),before)
