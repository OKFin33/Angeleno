"""Opt-in one-search actor loop; protocol stays outside conversation evidence."""

import asyncio
import json

from .author import MAX_REQUEST_CHARS
from .capabilities import capability_contract, set_capabilities
from .memory import encoded
from . import trace as tracing


TOOLS = [{"type": "function", "function": {
    "name": "memory_search",
    "description": "在当前角色与用户的有效可知记忆中按话题搜索。仅需要补读已有交流时使用；无命中不表示从未发生。",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                   "required": ["query"], "additionalProperties": False},
}}]
CAPABILITIES = capability_contract(memory=True, memory_search=True)

def request_chars(provider, messages, tools=None):
    """Budget the whole logical request, including tool definitions and protocol."""
    payload = {"model": provider.model, "messages": messages, "stream": True,
               "temperature": 0.7, "max_tokens": provider.max_tokens,
               "thinking": {"type": "disabled"},
               "stream_options": {"include_usage": True}}
    if tools is not None:
        payload["tools"] = tools
    elif any(m.get("role") == "tool" for m in messages):
        payload["tool_choice"] = "none"
    return len(encoded(payload))


def prepare_messages(messages):
    return set_capabilities(messages, memory_search=True)


def parse_call(calls):
    if len(calls) != 1:
        raise ValueError("One memory search only; multiple calls rejected")
    call = calls[0]
    if (set(call) != {"id", "type", "function"} or call["type"] != "function"
            or not isinstance(call["id"], str) or not call["id"] or len(call["id"]) > 256
            or set(call["function"]) != {"name", "arguments"}
            or call["function"]["name"] != "memory_search"):
        raise ValueError("Unknown or malformed memory tool call")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Repeated tool argument")
            result[key] = value
        return result
    arguments = json.loads(call["function"]["arguments"], object_pairs_hook=unique_object)
    if (not isinstance(arguments, dict) or set(arguments) != {"query"}
            or not isinstance(arguments["query"], str) or not arguments["query"].strip()
            or len(arguments["query"]) > 500):
        raise ValueError("memory_search requires one nonempty query of at most 500 characters")
    return call, arguments["query"]


def fill_search(provider, messages, store, query, tool, *, user_seq=None, tools=None):
    """Fit whole memory evidence in the next request and record its exact exposure.

    Shared by the memory-only and combined place/memory loops. There is no await
    between selection and exposure, and temporary turns never write exposure.
    """
    budget = MAX_REQUEST_CHARS - request_chars(provider, messages, tools)
    result = store.search(query, budget=budget)
    tool["content"] = encoded(result)
    while request_chars(provider, messages, tools) > MAX_REQUEST_CHARS:
        excess = request_chars(provider, messages, tools) - MAX_REQUEST_CHARS
        budget -= max(excess, 1)
        result = store.search(query, budget=budget)
        tool["content"] = encoded(result)
    if user_seq is not None:
        store.extend_input(user_seq, result["records"],
                           (e["seq"] for e in result.get("source_readback", {}).get("events", [])))


async def reply(provider, messages, store, *, user_seq=None):
    """At most two actor requests, no retry. user_seq=None is read-only temporary chat."""
    # Deferred so the standard-library preview remains offline.
    from .provider import TextDelta, ToolCalls
    current = prepare_messages(messages)
    if request_chars(provider, current, TOOLS) > MAX_REQUEST_CHARS:
        raise ValueError("Memory-tool request exceeds the input budget")
    prefix, pending = [], None
    iterator = provider.chat_events(current, tools=TOOLS)
    try:
        async for event in iterator:
            if isinstance(event, TextDelta):
                if pending is not None:
                    raise ValueError("Text after completed tool calls")
                prefix.append(event.text)
                yield event.text
            elif isinstance(event, ToolCalls):
                if pending is not None:
                    raise ValueError("Repeated tool call event")
                pending = event.calls
            else:
                raise ValueError("Unknown provider event")
    finally:
        await iterator.aclose()
    if pending is None:
        return
    for proposed in pending:tracing.tool('tool_proposed',proposed)
    try:
        call, query = parse_call(pending)
    except ValueError:
        for proposed in pending:tracing.tool('tool_rejected',proposed,reason='validation')
        raise
    tracing.tool('tool_validated',call)
    # Cancellation delivered before this point prevents both reading and followup.
    await asyncio.sleep(0)
    assistant = {"role": "assistant", "content": "".join(prefix) or None, "tool_calls": [call]}
    tool = {"role": "tool", "tool_call_id": call["id"], "content": ""}
    followup = [*current, assistant, tool]
    tracing.tool('tool_adapter_start',call)
    try:
        fill_search(provider, followup, store, query, tool, user_seq=user_seq)
    except Exception as exc:
        tracing.tool('tool_adapter_end',call,reason=type(exc).__name__)
        raise
    tracing.tool('tool_adapter_end',call,status='searched')
    tracing.record('tool_evidence',operation='memory_search',call_id=call['id'],result=json.loads(tool['content']))
    # There was no await between querying the SQLite versions and saving exposure.
    await asyncio.sleep(0)
    iterator = provider.chat_events(followup)
    try:
        async for event in iterator:
            if not isinstance(event, TextDelta):
                raise ValueError("A second tool call is forbidden")
            yield event.text
    finally:
        await iterator.aclose()
