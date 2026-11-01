"""Small trial lifetime controller. Local audio reservations are not wallet credit."""
import asyncio
import os
import re
import sqlite3
import threading
import time


def tts_preparation(candidate='eager'):
    """Operator-only candidate; GPU preparation never consumes user audio time."""
    if candidate not in ('eager','compiled'):
        raise ValueError('TTS candidate must be eager or compiled')
    return {'compile_value':'1' if candidate=='compiled' else '0',
            'gpu_ready_seconds':160 if candidate=='compiled' else 80,
            'total_startup_seconds':195 if candidate=='compiled' else 95}


class AudioCallLifetime:
    def __init__(self, remaining, *, clock=time.monotonic, waiting_seconds=300,
                 idle_seconds=300, redial_seconds=5, drain_seconds=3):
        if type(remaining) is not int or remaining < 1:
            raise ValueError('Existing positive local audio reservation required')
        self.clock=clock;self.initial=remaining;self.remaining=remaining
        self.waiting_seconds=waiting_seconds;self.idle_seconds=idle_seconds
        self.redial_seconds=redial_seconds;self.drain_seconds=drain_seconds
        self.ready_at=None;self.last_progress=None;self.left_at=None;self.drain_at=None
        self.ever_joined=False;self.reason=None;self.calls={};self.lock=threading.RLock()

    def ready(self):
        with self.lock:
            self.ready_at=self.last_progress=self.clock()

    def can_join(self):
        with self.lock:
            return self.ready_at is not None and self.reason is None and self.remaining>0

    def register(self, room, identity):
        with self.lock:
            if not self.can_join() or len(self.calls)>=32:
                raise ValueError('Trial closed or too many pending calls')
            if not room.startswith('angeleno-jo-') or not re.fullmatch(r'user-[a-f0-9]{16}',identity):
                raise ValueError('Unexpected trial call identity')
            self.calls[room]=identity

    def owned_calls(self):
        with self.lock:return dict(self.calls)

    def observe(self, remaining, user_present):
        with self.lock:
            if self.reason or self.ready_at is None:return self.reason
            now=self.clock()
            if type(remaining) is not int or not 0<=remaining<=self.remaining:
                self.reason='audio_meter_changed';return self.reason
            if remaining<self.remaining:
                self.last_progress=now;self.ever_joined=True
            self.remaining=remaining
            if remaining==0:
                if self.drain_at is None:self.drain_at=now
                if now-self.drain_at>=self.drain_seconds:self.reason='local_audio_exhausted'
            elif user_present:
                if not self.ever_joined:self.last_progress=now
                self.ever_joined=True;self.left_at=None
                if now-self.last_progress>=self.idle_seconds:self.reason='connected_audio_idle'
            elif self.ever_joined:
                if self.left_at is None:self.left_at=now
                if now-self.left_at>=self.redial_seconds:self.reason='user_ended'
            elif now-self.ready_at>=self.waiting_seconds:self.reason='no_user_joined'
            return self.reason

    def snapshot(self):
        with self.lock:
            now=self.clock();idle=max(0,int(self.idle_seconds-(now-self.last_progress))) if self.last_progress is not None else None
            return {'enabled':True,'state':self.reason or ('preparing' if self.ready_at is None else 'finishing' if self.remaining==0 else 'active' if self.ever_joined else 'waiting'),
                    'remaining_audio_seconds':self.remaining,'allowance_source':'local_reserved_audio_not_account_balance',
                    'audio_warning':self.remaining<=60,'idle_seconds_remaining':idle if self.ever_joined else None,
                    'idle_warning':bool(self.ever_joined and self.left_at is None and idle is not None and idle<=60),
                    'waiting_seconds_remaining':max(0,int(self.waiting_seconds-(now-self.ready_at))) if self.ready_at is not None and not self.ever_joined else None,
                    'stop_reason':self.reason}


def owner_pipe(stop, stream):
    """Only an explicitly supplied parent pipe; EOF closes owned resources."""
    def read():
        try:
            try:fd=stream.fileno()
            except (AttributeError,OSError):stream.read(1)
            else:os.read(fd,1)  # No buffered-IO lock held by a daemon during shutdown.
        finally:stop.set()
    thread=threading.Thread(target=read,daemon=True);thread.start();return thread


def start_monitor(lifetime, env, stop, *, root):
    """Observe only identities minted by this web server on its own LiveKit."""
    from worker.audio_budget import asr_settings
    path,maximum=asr_settings(env,root=root)
    async def run():
        from livekit import api
        url=env['LIVEKIT_URL'].replace('ws://','http://',1).replace('wss://','https://',1)
        client=api.LiveKitAPI(url,env['LIVEKIT_API_KEY'],env['LIVEKIT_API_SECRET'])
        failed_at=None
        try:
            while not stop.is_set():
                try:
                    present=False
                    async with asyncio.timeout(3):
                        owned=lifetime.owned_calls()
                        rooms=await client.room.list_rooms(api.ListRoomsRequest(names=list(owned))) if owned else None
                        for room in rooms.rooms if rooms is not None else ():
                            if room.name not in owned:continue
                            peers=await client.room.list_participants(api.ListParticipantsRequest(room=room.name))
                            present=present or any(p.identity==owned[room.name] for p in peers.participants)
                    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
                        row=db.execute('SELECT max_seconds,reserved_seconds FROM asr_allowance WHERE id=1').fetchone()
                    failed_at=None
                    if not row or row[0]!=maximum:
                        with lifetime.lock:lifetime.reason='audio_meter_changed'
                        stop.set()
                    elif lifetime.observe(row[0]-row[1],present):stop.set()
                except Exception:
                    now=lifetime.clock()
                    if failed_at is None:failed_at=now
                    if now-failed_at>=10:
                        with lifetime.lock:lifetime.reason='lifecycle_observer_unavailable'
                        stop.set()
                await asyncio.sleep(.5)
        finally:
            try:
                async with asyncio.timeout(2):await client.aclose()
            except Exception:pass
    thread=threading.Thread(target=lambda:asyncio.run(run()),daemon=True);thread.start();return thread


def forward_lease_script(seconds, *, renewable=False):
    """Owned Windows SSH command: parent EOF/failed heartbeat releases forward."""
    if type(seconds) is not int or not 1<=seconds<=3600:
        raise ValueError('Invalid bounded forward lease')
    if renewable:
        if not 30<=seconds<=120:raise ValueError('Renewable heartbeat outside bounded range')
        return "$reader=[System.IO.StreamReader]::new([Console]::OpenStandardInput()); while($true){$next=$reader.ReadLineAsync();if(-not $next.Wait("+str(seconds*1000)+")){break};if($next.Result -ne 'heartbeat'){break}}"
    return "$reader=[System.IO.StreamReader]::new([Console]::OpenStandardInput()); $lease=$reader.ReadToEndAsync(); [void]$lease.Wait("+str(seconds*1000)+")"


def lease_expired(now, started, ready, payload, instance):
    """Model owner lease; only same-instance monotonic health renewals count."""
    if ready is None:return now-started>=180
    last=ready
    if isinstance(payload,dict) and payload.get('instance_id')==instance:
        stamp=payload.get('monotonic')
        if type(stamp) in (int,float) and 0<=now-stamp<=90:last=max(last,stamp)
    return now-last>=90
