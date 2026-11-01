"""Opt-in local metadata receipts. Never store speech, prompts or credentials.

No file is created by default. The operator must select a non-symlink path
inside this checkout's ignored private/ directory. Failures cannot affect a turn.
"""
import json
import os
from pathlib import Path
import stat
import time

ROOT = Path(__file__).resolve().parents[1]
FIELDS = frozenset({"request_id", "result_id", "url", "request_hash", "remaining_seconds",
                    "reason", "interrupted", "phase", "attempt", "operation", "elapsed_seconds"})
EVENTS = frozenset({"tts_http_start", "tts_http_eof", "tts_http_end", "tts_stream_end",
                    "playout_finished", "generation_finished", "web_fetch_start", "web_fetch_end"})


def emit(event, **metadata):
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
