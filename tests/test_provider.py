"""Real SSE parsing and closure with synthetic HTTP responses only."""

import asyncio
import gzip
import json
import unittest

import httpx

from angeleno.provider import DeepSeekChatStream


class OfflineBudget:
    """Protocol-only tests inject this instead of authorizing a real account."""
    def __init__(self):
        self.attempts, self.finished = [], []

    async def reserve(self, client, payload, purpose):
        self.attempts.append((payload, purpose))
        return len(self.attempts)

    def finish(self, *args):
        self.finished.append(args)


def event(content=None, reason=None, usage=None):
    return "data: " + json.dumps({"choices": [{"delta": {} if content is None else {"content": content},
                                               "finish_reason": reason}], "usage": usage}) + "\n\n"


DONE = "data: [DONE]\n\n"
USAGE = {"prompt_tokens": 1000, "completion_tokens": 20, "prompt_cache_hit_tokens": 100}


def response(text="Synthetic response", *, usage=USAGE):
    return httpx.Response(200, text=event(text) + event(reason="stop", usage=usage) + DONE)


def offline_provider(handle, **kwargs):
    provider = DeepSeekChatStream("synthetic-key", budget=OfflineBudget(), **kwargs)
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return provider


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_completed_streams_succeed_for_text_and_json(self):
        cases = {
            "complete": (event("complete") + event(reason="stop", usage=USAGE) + DONE, True),
            "empty": (event(reason="stop") + DONE, False),
            "partial_eof": (event("partial"), False),
            "empty_eof": ("", False),
            "length": (event("partial") + event(reason="length") + DONE, False),
            "missing_done": (event("partial") + event(reason="stop"), False),
            "missing_reason": (event("partial") + DONE, False),
            "error": (event("partial") + 'data: {"error":{"message":"do not expose"}}\n\n', False),
            "malformed": (event("partial") + 'data: {broken\n\n', False),
            "after_finish": (event("partial", "stop") + event("extra") + DONE, False),
        }
        for json_output in (False, True):
            for name, (body, succeeds) in cases.items():
                with self.subTest(name=name, json_output=json_output):
                    provider = offline_provider(lambda request: httpx.Response(200, text=body), json_output=json_output)
                    try:
                        if succeeds:
                            self.assertEqual([p async for p in provider.chat([])], ["complete"])
                            self.assertEqual(provider.budget.finished[-1][1], USAGE)
                        else:
                            with self.assertRaises(RuntimeError):
                                _ = [p async for p in provider.chat([])]
                            self.assertEqual(provider.budget.finished[-1][2], "failed")
                    finally:
                        await provider.aclose()

    async def test_json_and_usage_payload_preserve_non_thinking(self):
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return response('{"retire":[],"updates":[]}')
        provider = offline_provider(handle, json_output=True, max_tokens=2048)
        try:
            _ = [p async for p in provider.chat([], temperature=0.2)]
            self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
            self.assertEqual(requests[0]["thinking"], {"type": "disabled"})
            self.assertEqual(requests[0]["stream_options"], {"include_usage": True})
            self.assertEqual(requests[0]["temperature"], 0.2)
            with self.assertRaises(ValueError):
                provider.chat_events([], tools=[])
        finally:
            await provider.aclose()

    async def test_cancel_and_generator_close_close_transport_and_keep_prefix(self):
        for cancel in (True, False):
            closed = asyncio.Event()
            class Partial(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield event("shown").encode()
                    raise asyncio.CancelledError
                async def aclose(self):
                    closed.set()
            provider = offline_provider(lambda request: httpx.Response(200, stream=Partial()))
            iterator = provider.chat([])
            try:
                self.assertEqual(await anext(iterator), "shown")
                if cancel:
                    with self.assertRaises(asyncio.CancelledError):
                        await anext(iterator)
                await iterator.aclose()
                self.assertTrue(closed.is_set())
                self.assertEqual(provider.budget.finished[-1][2], "cancelled")
                self.assertIsNone(provider.budget.finished[-1][1])
            finally:
                await provider.aclose()

    async def test_http_error_body_is_not_reported_or_logged(self):
        provider = offline_provider(lambda request: httpx.Response(401, text="PRIVATE TOKEN"))
        try:
            with self.assertRaisesRegex(RuntimeError, "^DeepSeek HTTP 401$"):
                _ = [p async for p in provider.chat([])]
            self.assertEqual(provider.budget.finished[-1][2:], ("failed", 401))
        finally:
            await provider.aclose()

    async def test_compressed_usage_is_parsed_by_httpx(self):
        body = gzip.compress((event("ok") + event(reason="stop", usage=USAGE) + DONE).encode())
        provider = offline_provider(lambda request: httpx.Response(200, content=body, headers={"Content-Encoding": "gzip"}))
        try:
            self.assertEqual([p async for p in provider.chat([])], ["ok"])
            self.assertEqual(provider.budget.finished[-1][1], USAGE)
        finally:
            await provider.aclose()

    async def test_direct_unbudgeted_provider_cannot_send(self):
        provider = DeepSeekChatStream("synthetic-key")
        with self.assertRaisesRegex(ValueError, "budget is required"):
            _ = [p async for p in provider.chat([])]
        self.assertIsNone(provider._client)
