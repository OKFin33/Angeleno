# SPDX-License-Identifier: Apache-2.0
# Modified from talk-to-fengge. Explicit provider and usage configuration; see NOTICE.
"""DeepSeek stream adapter extracted from talk-to-fengge; see NOTICE.

Live entry points use configured_providers so actor, tool continuation and JSON
maintenance share one budget. Constructor injection exists for offline tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
import uuid
from . import trace as tracing
from pathlib import Path
from typing import AsyncIterator

import httpx

_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_TOKENS = 2048
OPENAI_COMPAT_DEEPSEEK = "https://api.deepseek.com"
MODEL_DEEPSEEK_CHAT = "deepseek-flash"


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCalls:
    calls: tuple


# ── DeepSeek 流式包装 ───────────────────────────────────────
class DeepSeekChatStream:
    """DeepSeek Chat (OpenAI 兼容) 流式包装。"""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = MODEL_DEEPSEEK_CHAT,
        base_url: str = OPENAI_COMPAT_DEEPSEEK,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        diagnostics: bool = False,
        json_output: bool = False,
        budget=None,
        purpose="actor",
    ) -> None:
        if base_url.rstrip("/") != OPENAI_COMPAT_DEEPSEEK:
            raise ValueError("Only the configured DeepSeek endpoint is supported")
        self.budget, self.purpose = budget, purpose
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.diagnostics = diagnostics
        self.json_output = json_output
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s,
                trust_env=False,
                http2=False,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            )
        return self._client

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        return self._stream(messages, temperature)

    def chat_events(self, messages, *, tools=None, temperature=0.7):
        """Internal typed stream; tool protocol never becomes spoken text."""
        if self.json_output:
            raise ValueError("JSON maintenance does not use tools")
        return self._events(messages, temperature, tools=tools)

    async def _stream(self, messages, temperature):
        iterator = self._events(messages, temperature)
        try:
            async for event in iterator:
                if isinstance(event, TextDelta):
                    yield event.text
                else:
                    raise RuntimeError("Unexpected tools in a text-only request")
        finally:
            await iterator.aclose()

    async def _events(self, messages, temperature, *, tools=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        payload = {
            "model": self.model, "messages": messages, "stream": True,
            "temperature": temperature, "max_tokens": self.max_tokens,
            "thinking": {"type": "disabled"},
            "stream_options": {"include_usage": True},
        }
        if self.json_output:
            payload["response_format"] = {"type": "json_object"}
        if tools is not None:
            payload["tools"] = tools
        elif any(m.get("role") == "tool" for m in messages):
            payload["tool_choice"] = "none"
        if self.budget is None:
            raise ValueError("A paid budget is required; use configured_providers")
        client = self._get_client()
        trace_request = uuid.uuid4().hex
        started = time.monotonic()
        capture = tracing.current()
        if capture is not None and capture.sink.path is None:capture=None
        captured_text, captured_reasoning = [], []
        reasoning_chars = 0
        first_chunk_at = first_text_at = http_started = None
        tracing.record('model_request', request_id=trace_request, purpose=self.purpose,
                       model=self.model, thinking=payload['thinking'], payload=payload)
        try:
            request_id = await self.budget.reserve(client, payload, self.purpose)
        except BaseException as exc:
            tracing.record('model_end', request_id=trace_request, model=self.model, purpose=self.purpose,
                           status='reservation_refused', error_type=type(exc).__name__,
                           http_dispatched=False, elapsed_seconds=time.monotonic()-started)
            raise
        usage, status, http_status = None, "interrupted", None
        first_token, saw_done = True, False
        finish_reason = None
        calls = {}
        error_type = None
        try:
            http_started = time.monotonic()
            tracing.record('model_http_start', request_id=trace_request, ledger_request_id=request_id,
                           model=self.model, purpose=self.purpose)
            async with client.stream("POST", f"{self.base_url}/v1/chat/completions",
                                     headers=headers, json=payload) as r:
                http_status = r.status_code
                if r.status_code != 200:
                    raise RuntimeError(f"DeepSeek HTTP {r.status_code}")
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        saw_done = True
                        break
                    try:
                        obj = json.loads(data)
                        if not isinstance(obj, dict):
                            raise ValueError("SSE object required")
                    except (ValueError, TypeError) as exc:
                        raise RuntimeError("DeepSeek returned invalid SSE data") from exc
                    if first_chunk_at is None:
                        first_chunk_at = time.monotonic()
                        tracing.record('model_first_chunk', request_id=trace_request,
                                       after_http_seconds=first_chunk_at-http_started)
                    if obj.get("error"):
                        raise RuntimeError("DeepSeek returned an in-stream error")
                    if obj.get("usage") is not None:
                        usage = obj["usage"]
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    if len(choices) != 1 or not isinstance(choices[0], dict):
                        raise RuntimeError("Unexpected DeepSeek choices")
                    choice = choices[0]
                    if finish_reason is not None:
                        raise RuntimeError("DeepSeek sent data after completion")
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        raise RuntimeError("Invalid DeepSeek delta")
                    reasoning = delta.get('reasoning_content')
                    if isinstance(reasoning,str):
                        reasoning_chars += len(reasoning)
                        if capture and capture.sink.capture_reasoning and sum(map(len,captured_reasoning))<16384:
                            captured_reasoning.append(reasoning[:16384-sum(map(len,captured_reasoning))])
                    piece = delta.get("content")
                    if piece:
                        if not isinstance(piece, str):
                            raise RuntimeError("Invalid text delta")
                        if first_token:
                            first_token = False
                            first_text_at = time.monotonic()
                        if capture and sum(map(len,captured_text))<32000:
                            captured_text.append(piece[:32000-sum(map(len,captured_text))])
                        yield TextDelta(piece)
                    fragments = delta.get("tool_calls", [])
                    if not isinstance(fragments, list):
                        raise RuntimeError("Invalid tool delta")
                    for fragment in fragments:
                        if not isinstance(fragment, dict):
                            raise RuntimeError("Invalid tool fragment")
                        index = fragment.get("index")
                        if type(index) is not int or not 0 <= index < 8:
                            raise RuntimeError("Invalid tool index")
                        call = calls.setdefault(index, {"id": "", "type": "function",
                                                        "function": {"name": "", "arguments": ""}})
                        if fragment.get("type", "function") != "function":
                            raise RuntimeError("Unsupported tool type")
                        function = fragment.get("function") or {}
                        if not isinstance(function, dict):
                            raise RuntimeError("Invalid tool function")
                        for destination, key, source in ((call, "id", fragment),
                                                          (call["function"], "name", function),
                                                          (call["function"], "arguments", function)):
                            value = source.get(key)
                            if value is not None:
                                if not isinstance(value, str):
                                    raise RuntimeError("Invalid tool field")
                                destination[key] += value
                        if len(json.dumps(calls, ensure_ascii=False)) > 8000:
                            raise RuntimeError("Tool protocol exceeds budget")
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice["finish_reason"]
                if not saw_done:
                    raise RuntimeError("DeepSeek stream did not complete")
                if finish_reason == "tool_calls" and calls:
                    if tools is None:
                        raise RuntimeError("Unexpected tools in a text-only request")
                    if sorted(calls) != list(range(len(calls))) or any(
                            not c["id"] or not c["function"]["name"] or not c["function"]["arguments"]
                            for c in calls.values()):
                        raise RuntimeError("Incomplete tool calls")
                    status = "completed"
                    yield ToolCalls(tuple(calls[i] for i in sorted(calls)))
                elif finish_reason != "stop" or calls or first_token:
                    raise RuntimeError("DeepSeek stream did not complete a text reply")
                status = "completed"
        except (asyncio.CancelledError, GeneratorExit):
            if status != "completed":
                status = "cancelled"
            raise
        except BaseException as exc:
            status = "failed"
            error_type = type(exc).__name__
            raise
        finally:
            try:
                self.budget.finish(request_id, usage, status, http_status)
            finally:
                extra = {}
                if capture and capture.sink.capture_reasoning and reasoning_chars:
                    extra['reasoning_content'] = ''.join(captured_reasoning)
                    extra['reasoning_truncated_chars'] = reasoning_chars-len(extra['reasoning_content'])
                tracing.record('model_end', request_id=trace_request, ledger_request_id=request_id,
                    model=self.model, thinking=payload['thinking'], purpose=self.purpose,
                    status=status, error_type=error_type, http_status=http_status,
                    elapsed_seconds=time.monotonic()-started,
                    http_elapsed_seconds=time.monotonic()-http_started if http_started else None,
                    first_chunk_seconds=first_chunk_at-http_started if first_chunk_at else None,
                    first_text_seconds=first_text_at-http_started if first_text_at else None,
                    finish_reason=finish_reason, usage=usage, output=''.join(captured_text),
                    tool_calls=list(calls.values()),
                    proposal='present' if calls else 'none', tools_offered=tools is not None,
                    reasoning_capture='supplier_returned' if extra else 'not_returned' if capture and capture.sink.capture_reasoning else 'disabled',
                    **extra)


def _provider_configuration(env, root):
    from .budget import Prices, decimal_setting
    from .config import flag, resolve_path
    if not flag(env, "ANGELENO_PAID_ENABLED"):
        raise ValueError("Paid requests are disabled; set ANGELENO_PAID_ENABLED=1 explicitly")
    key = env.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is not configured")
    model = env.get("DEEPSEEK_MODEL", MODEL_DEEPSEEK_CHAT)
    prices = Prices.from_env(env, model)
    for name in ("ANGELENO_MIN_BALANCE", "ANGELENO_BUDGET_CARRYOVER"):
        if name not in env or not str(env[name]).strip():
            raise ValueError(f"{name} must be explicit (0 is allowed)")
        decimal_setting(env[name], name)
    data_dir = resolve_path(env.get("ANGELENO_DATA_DIR", "private/state"), root=root)
    if not data_dir.is_relative_to((Path(root) / "private").resolve()):
        raise ValueError("Budget data directory must remain inside this repository private directory")
    path = resolve_path(env.get("ANGELENO_BUDGET_LEDGER", str(data_dir / "budget.sqlite3")), root=root)
    if not path.is_relative_to(data_dir):
        raise ValueError("Budget ledger must remain inside ANGELENO_DATA_DIR")
    return key, model, prices, path


def validate_provider_configuration(env, *, root=None):
    """Offline validation only: no ledger, account lookup or HTTP client."""
    from .config import ROOT
    _provider_configuration(env, ROOT if root is None else root)


def configured_providers(env, *, root=None):
    """Return paid actor and JSON writer, with no HTTP before a stream starts."""
    from .budget import Ledger, PaidBudget
    from .config import ROOT
    key, model, prices, path = _provider_configuration(env, ROOT if root is None else root)
    ledger = Ledger(path, prices, minimum_balance=env["ANGELENO_MIN_BALANCE"],
                    carryover=env["ANGELENO_BUDGET_CARRYOVER"], account=key)
    budget = PaidBudget(ledger, key)
    return (DeepSeekChatStream(key, model=model, max_tokens=512, budget=budget),
            DeepSeekChatStream(key, model=model, max_tokens=2048, json_output=True,
                               budget=budget, purpose="maintenance"))
