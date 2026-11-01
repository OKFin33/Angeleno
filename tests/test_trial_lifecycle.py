"""Offline fake-time user lifetime; no device/service/paid calls."""
import io
import threading
import unittest
from scripts.trial_lifecycle import AudioCallLifetime, owner_pipe, lease_expired, forward_lease_script


class Clock:
    now=0
    def __call__(self):return self.now


class LifetimeTests(unittest.TestCase):
    def lifetime(self,remaining=1000):
        clock=Clock();value=AudioCallLifetime(remaining,clock=clock)
        return clock,value

    def test_startup_does_not_consume_waiting_or_audio_and_active_exceeds_540(self):
        clock,value=self.lifetime();clock.now=180;value.ready()
        self.assertEqual(value.snapshot()['waiting_seconds_remaining'],300)
        for second in range(1,701):
            clock.now=180+second
            self.assertIsNone(value.observe(1000-second,True))
        self.assertEqual(value.snapshot()['remaining_audio_seconds'],300)
        self.assertEqual(value.snapshot()['state'],'active')

    def test_no_user_and_connected_no_audio_are_bounded_separately(self):
        clock,value=self.lifetime();value.ready();clock.now=299
        self.assertIsNone(value.observe(1000,False));clock.now=300
        self.assertEqual(value.observe(1000,False),'no_user_joined')
        clock,value=self.lifetime();value.ready();clock.now=250;value.observe(1000,True)
        clock.now=490;value.observe(1000,True)
        self.assertTrue(value.snapshot()['idle_warning']);clock.now=550
        self.assertEqual(value.observe(1000,True),'connected_audio_idle')

    def test_hangup_redial_grace_and_then_user_end(self):
        clock,value=self.lifetime();value.ready();value.observe(999,True)
        clock.now=1;value.observe(999,False);clock.now=5
        self.assertIsNone(value.observe(999,True))
        clock.now=6;value.observe(999,False);clock.now=11
        self.assertEqual(value.observe(999,False),'user_ended')

    def test_budget_warning_drain_invalid_increase_and_registered_scope(self):
        clock,value=self.lifetime(61);value.ready();value.observe(60,True)
        self.assertTrue(value.snapshot()['audio_warning'])
        self.assertEqual(value.snapshot()['allowance_source'],'local_reserved_audio_not_account_balance')
        value.observe(0,True);clock.now=2.9;self.assertIsNone(value.observe(0,True))
        clock.now=3;self.assertEqual(value.observe(0,True),'local_audio_exhausted')
        for bad in (True,None,-1,62):
            _,value=self.lifetime(61);value.ready()
            self.assertEqual(value.observe(bad,False),'audio_meter_changed')
        _,value=self.lifetime();value.ready();value.register('angeleno-jo-123','user-'+'a'*16)
        owned=value.owned_calls();owned.clear();self.assertEqual(len(value.owned_calls()),1)
        for room,identity in [('other','user-'+'a'*16),('angeleno-jo-123','agent')]:
            with self.assertRaises(ValueError):value.register(room,identity)
        value.reason='user_ended'
        with self.assertRaises(ValueError):value.register('angeleno-jo-456','user-'+'b'*16)

    def test_parent_eof_requests_cleanup(self):
        stop=threading.Event();thread=owner_pipe(stop,io.BytesIO(b''));thread.join(1)
        self.assertTrue(stop.is_set())

    def test_owned_lease_startup_renewal_and_wrong_or_future_stamps(self):
        self.assertFalse(lease_expired(179,0,None,None,'one'))
        self.assertTrue(lease_expired(180,0,None,None,'one'))
        self.assertFalse(lease_expired(700,0,20,{'instance_id':'one','monotonic':699},'one'))
        for payload in (None,{}, {'instance_id':'other','monotonic':699},
                        {'instance_id':'one','monotonic':701}, {'instance_id':'one','monotonic':True}):
            self.assertTrue(lease_expired(700,0,20,payload,'one'))
        self.assertTrue(lease_expired(790,0,20,{'instance_id':'one','monotonic':700},'one'))
        script=forward_lease_script(90,renewable=True)
        self.assertIn('ReadLineAsync',script);self.assertIn('90000',script);self.assertIn("-ne 'heartbeat'",script)
        for bad in (0,121,True):
            with self.assertRaises(ValueError):forward_lease_script(bad,renewable=True)


class StatusRouteTests(unittest.TestCase):
    def test_local_counter_route_is_read_only_and_optional(self):
        from worker.web_server import handler_for
        _,lifetime=LifetimeTests().lifetime(493);lifetime.ready()
        for value in (None,lifetime):
            handler=object.__new__(handler_for({},lifecycle=value));handler.path='/trial-status'
            result=[];handler.send_json=lambda status,data:result.append((status,data))
            handler.do_GET()
            self.assertEqual(result[0][0],200)
            self.assertEqual(result[0][1]['enabled'],value is not None)
        self.assertEqual(lifetime.remaining,493)


class ObserverTests(unittest.TestCase):
    def test_only_issued_room_identity_is_observed_and_audio_ledger_is_read_only(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import AsyncMock,patch
        from scripts.trial_lifecycle import start_monitor
        from worker.audio_budget import ASRAllowance
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);meter=root/'private/state/asr.sqlite3';ASRAllowance(meter,10).reserve(2)
            before=meter.read_bytes();stop=threading.Event();lifetime=AudioCallLifetime(8)
            lifetime.ready();lifetime.register('angeleno-jo-one','user-'+'a'*16)
            async def peers(request):
                self.assertEqual(request.room,'angeleno-jo-one');stop.set()
                return SimpleNamespace(participants=[SimpleNamespace(identity='user-'+'a'*16)])
            client=SimpleNamespace(room=SimpleNamespace(list_rooms=AsyncMock(return_value=SimpleNamespace(rooms=[SimpleNamespace(name='angeleno-jo-one')])),
                                                       list_participants=AsyncMock(side_effect=peers)),aclose=AsyncMock())
            env={'ANGELENO_ASR_MAX_SECONDS':'10','LIVEKIT_URL':'ws://127.0.0.1:7880','LIVEKIT_API_KEY':'synthetic','LIVEKIT_API_SECRET':'synthetic'}
            with patch('livekit.api.LiveKitAPI',return_value=client) as factory:
                thread=start_monitor(lifetime,env,stop,root=root);thread.join(3)
            self.assertFalse(thread.is_alive());self.assertTrue(lifetime.ever_joined)
            self.assertEqual(list(client.room.list_rooms.call_args.args[0].names),['angeleno-jo-one'])
            factory.assert_called_once_with('http://127.0.0.1:7880','synthetic','synthetic')
            client.aclose.assert_awaited_once();self.assertEqual(before,meter.read_bytes())


class CandidatePreparationTests(unittest.TestCase):
    def test_compiled_preparation_is_bounded_and_separate_from_join_and_audio(self):
        from scripts.trial_lifecycle import tts_preparation
        plan=tts_preparation('compiled');self.assertGreater(plan['gpu_ready_seconds'],117)
        self.assertGreaterEqual(plan['total_startup_seconds'],plan['gpu_ready_seconds']+35)
        self.assertLessEqual(plan['total_startup_seconds'],240)
        self.assertEqual(tts_preparation()['compile_value'],'0')
        for bad in ('auto','1',None,True):
            with self.assertRaises(ValueError):tts_preparation(bad)
        clock=Clock();life=AudioCallLifetime(493,clock=clock)
        clock.now=150;self.assertFalse(life.can_join());self.assertEqual(life.snapshot()['state'],'preparing')
        life.ready();self.assertTrue(life.can_join());self.assertEqual(life.snapshot()['remaining_audio_seconds'],493)
        self.assertEqual(life.snapshot()['waiting_seconds_remaining'],300)

    def test_token_not_created_before_ready_or_after_failed_preparation(self):
        from worker.web_server import handler_for
        from unittest.mock import AsyncMock,patch
        life=AudioCallLifetime(493);handler=object.__new__(handler_for({},lifecycle=life))
        handler.path='/token';handler.headers={'Origin':'http://127.0.0.1:8767'}
        result=[];handler.send_json=lambda status,body:result.append(status)
        token=AsyncMock(return_value={'room':'angeleno-jo-synthetic','identity':'user-'+'a'*16,'token':'synthetic'})
        with patch('worker.web_server.create_room_and_token',token):
            handler.do_POST();self.assertEqual(result[-1],503);token.assert_not_called()
            life.ready();handler.rfile=io.BytesIO(b'');handler.do_POST()
            self.assertEqual(result[-1],200);token.assert_awaited_once()
            life.reason='supervisor_stopped';handler.do_POST()
            self.assertEqual(result[-1],503);self.assertEqual(token.call_count,1)
