"""Controlled consumer/model clocks, no sockets/GPU or real audio."""
import asyncio
from types import SimpleNamespace
import unittest
from livekit import rtc
from her_agent.voice_timing import tagged_tts, AUDIO_TAG

async def ticks():
    for _ in range(8): await asyncio.sleep(0)

class Bridge:
    active = True
    def __init__(self):self.deadlines=SimpleNamespace(accepts=lambda _:self.active)
    def consume_speech(self,chunk,**_):
        return [chunk if isinstance(chunk,tuple) else (chunk,('turn','ordinary'))]

class Synth:
    def __init__(self,counts=(3,3),block_second=False,fail_second=False):
        self.counts=counts;self.calls=[];self.active=self.maximum=self.produced=0
        self.clock=0;self.block_second=block_second;self.fail_second=fail_second;self.closed=0
    def synthesize(self,text,*,conn_options):
        assert conn_options.max_retry==0
        owner=self;idx=len(self.calls);self.calls.append((text,self.clock))
        class Stream:
            async def __aenter__(self):
                owner.active+=1;owner.maximum=max(owner.maximum,owner.active);return self
            async def __aexit__(self,*_):owner.active-=1;owner.closed+=1
            def __aiter__(self):return self.audio()
            async def audio(self):
                if idx==1 and owner.block_second:await asyncio.Event().wait()
                if idx==1 and owner.fail_second:raise ValueError('synthetic provider failure')
                for _ in range(owner.counts[min(idx,len(owner.counts)-1)]):
                    owner.produced+=1
                    yield SimpleNamespace(frame=rtc.AudioFrame(bytes(9600),24000,1,4800))
        return Stream()

async def text(*chunks):
    for chunk in chunks:yield chunk

class PrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumer_hold_advances_next_model_without_initial_wait_or_concurrency(self):
        starts=[]
        for mode in (0,2):
            synth=Synth();stream=tagged_tts(text('甲。乙。'),Bridge(),synth,prefetch_frames=mode)
            first=await anext(stream);await ticks()
            self.assertEqual(synth.clock,0);self.assertEqual(first.duration,.2)
            if mode==2:
                self.assertEqual(len(synth.calls),2)
                self.assertLessEqual(synth.produced,4,'two queued plus one pending after consumer frame')
            else:self.assertEqual(len(synth.calls),1)
            synth.clock=.6
            result=[first]+[frame async for frame in stream]
            starts.append(synth.calls[1][1])
            self.assertEqual(len(result),6);self.assertEqual(synth.maximum,1);self.assertEqual(synth.active,0)
        self.assertEqual(starts,[.6,0])
        # A300ms next-model cold gap is covered in this controlled600ms consumer hold.
        self.assertAlmostEqual(max(0,starts[0]+.3-.6),.3)
        self.assertEqual(max(0,starts[1]+.3-.6),0)

    async def test_long_stream_bounded_and_close_releases_owner(self):
        synth=Synth((100,));stream=tagged_tts(text('长句。'),Bridge(),synth,prefetch_frames=2)
        await anext(stream);await ticks();self.assertLessEqual(synth.produced,4)
        await asyncio.wait_for(stream.aclose(),.2)
        self.assertEqual(synth.active,0);self.assertEqual(synth.closed,1)
        self.assertFalse(any(t.get_name()=='bounded-tts-lookahead' and not t.done() for t in asyncio.all_tasks()))

    async def test_cancel_discards_already_queued_audio_and_closes_prefetched_model(self):
        bridge=Bridge();synth=Synth(block_second=True)
        stream=tagged_tts(text('甲。乙。'),bridge,synth,prefetch_frames=2)
        await anext(stream);await ticks();self.assertEqual(len(synth.calls),2)
        bridge.active=False
        with self.assertRaises(asyncio.CancelledError):await anext(stream)
        self.assertEqual(synth.active,0)

    async def test_waiting_answer_order_short_empty_and_provider_failure(self):
        synth=Synth((1,1));stream=tagged_tts(text(('甲。',('turn','waiting')),('乙。',('turn','answer'))),Bridge(),synth,prefetch_frames=2)
        frames=[f async for f in stream]
        self.assertEqual([f.userdata[AUDIO_TAG][1] for f in frames],['waiting','answer'])
        empty=Synth((0,));self.assertEqual([f async for f in tagged_tts(text('空。'),Bridge(),empty,prefetch_frames=2)],[])
        failed=Synth(fail_second=True)
        with self.assertRaisesRegex(ValueError,'synthetic provider'):
            _=[f async for f in tagged_tts(text('甲。乙。'),Bridge(),failed,prefetch_frames=2)]
        self.assertEqual(failed.active,0)

    async def test_no_arbitrary_or_boolean_prefetch_capacity(self):
        for value in (1,3,-1,None,True):
            synth=Synth()
            with self.assertRaises(ValueError):await anext(tagged_tts(text('甲。'),Bridge(),synth,prefetch_frames=value))
            self.assertEqual(synth.calls,[])
