"""Opt-in local metadata receipts. Never store speech, prompts or credentials.

No file is created by default. The operator must select a non-symlink path
inside this checkout's ignored private/ directory. Failures cannot affect a turn.
"""
import json
import hashlib
import os
from pathlib import Path
import stat
import time
import threading

ROOT = Path(__file__).resolve().parents[1]
FIELDS = frozenset({"request_id", "result_id", "url", "request_hash", "remaining_seconds",
                    "reason", "interrupted", "phase", "attempt", "operation", "elapsed_seconds", "http_status",
                    "chunk_bytes", "gap_seconds", "audio_seconds", "receive_rtf",
                    "lead_queue_seconds", "push_seconds", "sdk_queue_seconds", "stream_id",
                    "call_hash", "argument_hash", "turn_seq", "status"})
EVENTS = frozenset({"tts_http_start", "tts_http_eof", "tts_http_end", "tts_stream_end",
                    "playout_finished", "generation_finished", "web_fetch_start", "web_fetch_end",
                    "web_fetch_http_start", "web_fetch_http_end", "audio_http_chunk", "audio_push",
                    "tool_proposed", "tool_validated", "tool_not_executed", "tool_adapter_start",
                    "tool_adapter_end", "tool_result", "tool_rejected"})
AUDIO_EVENTS = frozenset({'audio_http_chunk', 'audio_push'})
AUDIO_METRIC_LIMIT = 2048  # Per worker process, across streams/requests/files.
_audio_metric_rows = 0
_audio_metric_lock = threading.Lock()


def emit(event, **metadata):
    global _audio_metric_rows
    if event in AUDIO_EVENTS and os.getenv('ANGELENO_AUDIO_METRICS') != '1':
        return False
    path = os.environ.get("ANGELENO_DIAGNOSTICS_FILE")
    if not path:
        return False
    try:
        if event not in EVENTS or not set(metadata) <= FIELDS:
            return False
        if any(type(v) not in (str, float, int, bool, type(None)) or
               isinstance(v, str) and len(v) > 2048 for v in metadata.values()):
            return False
        target = Path(path)
        if not target.is_absolute():
            target = ROOT / target
        private = ROOT / "private"
        if private.is_symlink() or not target.resolve().is_relative_to(private.resolve()):
            return False
        relative = target.relative_to(private)
        if any(p.is_symlink() for p in (target, *list(target.parents)[:len(relative.parts) - 1])):
            return False
        if event in AUDIO_EVENTS:
            with _audio_metric_lock:
                if _audio_metric_rows >= AUDIO_METRIC_LIMIT:
                    return False
                _audio_metric_rows += 1
        # Do not create directories, follow a link or append to a permissive file.
        fd = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                return False
            row = {"event": event, "monotonic_seconds": time.monotonic(), **metadata}
            os.write(fd, (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode())
        finally:
            os.close(fd)
        return True
    except (OSError, ValueError, TypeError):
        return False


_TOOL_NAMES = frozenset({'web_search','web_fetch','place_search','place_check','place_cancel','memory_search'})
_TRACE_REASONS = frozenset({'batch_validation','tool_limit_reached','actor_request_limit','cancelled',
    'provider_security_refused','budget_refused','adapter_error','tool_not_accepted'})
_TRACE_STATUSES = frozenset({'evidence','supported','insufficient','conflict','unavailable','cancelled',
    'out_of_scope','searched','invalid_arguments','not_executed'})
_tool_trace_rows = 0
_tool_trace_lock = threading.Lock()


def tool_trace(event, call, *, user_turn, reason=None, status=None):
    """Opt-in development metadata, never raw arguments, tool output or speech.

    Adapter start means only a local tool-method call, not an HTTP dispatch.
    The existing Fetch HTTP events remain the evidence of actual network I/O.
    """
    global _tool_trace_rows
    if (os.getenv('ANGELENO_TOOL_TRACE') != '1'
            or os.getenv('ANGELENO_MEMORY_PURPOSE') != 'development'
            or not os.getenv('ANGELENO_DIAGNOSTICS_FILE')):
        return False
    if event not in {'tool_proposed','tool_validated','tool_not_executed','tool_adapter_start','tool_adapter_end','tool_result','tool_rejected'}:
        return False
    try:
        function = call.get('function', {})
        name = function.get('name')
        metadata = {'operation': name if name in _TOOL_NAMES else 'unknown',
            'call_hash': hashlib.sha256(str(call.get('id','')).encode()).hexdigest(),
            'argument_hash': hashlib.sha256(json.dumps(function.get('arguments'),ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
        if type(user_turn) is int:metadata['turn_seq'] = user_turn
        if reason is not None:metadata['reason'] = reason if reason in _TRACE_REASONS else 'tool_not_accepted'
        if status is not None:metadata['status'] = status if status in _TRACE_STATUSES else 'other'
        with _tool_trace_lock:
            if _tool_trace_rows >= 256:return False
            _tool_trace_rows += 1
        return emit(event, **metadata)
    except (AttributeError, TypeError, ValueError):
        return False  # A diagnostic must not turn malformed tool data into a new failure.
