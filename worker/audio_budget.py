"""Explicit remote permission and durable ASR seconds; no audio is stored."""
import ipaddress
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


class BudgetExceeded(RuntimeError):
    pass


def is_loopback(url):
    host = urlparse(url).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def require_remote_permission(url, *, paid_enabled=False):
    if urlparse(url).scheme not in {"http", "https", "ws", "wss"}:
        raise ValueError("Audio endpoint requires an explicit HTTP/WebSocket URL")
    if not is_loopback(url) and not paid_enabled:
        raise ValueError("Remote audio requires ANGELENO_PAID_ENABLED=1")


class ASRAllowance:
    """Reserve whole audio seconds before sending, across calls/processes."""
    def __init__(self, path, max_seconds):
        self.path, self.max_seconds = Path(path), int(max_seconds)
        if self.max_seconds < 1:
            raise ValueError("An explicit positive ASR allowance is required")
        if self.path.is_symlink():
            raise ValueError("ASR ledger must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existing = self.path.exists()
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            if application_id != 0x414E4741:
                if existing or application_id != 0 or db.execute("SELECT name FROM sqlite_master").fetchone():
                    raise ValueError("Not an Angeleno ASR ledger; left untouched")
                db.execute("PRAGMA application_id=1095649089")
                db.execute("CREATE TABLE asr_allowance (id INTEGER PRIMARY KEY CHECK(id=1), max_seconds INTEGER, reserved_seconds INTEGER)")
                db.execute("INSERT INTO asr_allowance VALUES(1,?,0)", (self.max_seconds,))
            if db.execute("SELECT max_seconds FROM asr_allowance WHERE id=1").fetchone()[0] != self.max_seconds:
                raise ValueError("Existing ASR allowance differs; do not silently enlarge it")
        self.path.chmod(0o600)

    def reserve(self, seconds):
        if type(seconds) is not int or seconds < 1:
            raise ValueError("Reserve whole positive audio seconds")
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT reserved_seconds FROM asr_allowance WHERE id=1").fetchone()[0]
            if used + seconds > self.max_seconds:
                raise BudgetExceeded("Trial ASR time allowance exhausted; audio not sent")
            db.execute("UPDATE asr_allowance SET reserved_seconds=reserved_seconds+? WHERE id=1", (seconds,))

    def check_available(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            used = db.execute("SELECT reserved_seconds FROM asr_allowance WHERE id=1").fetchone()[0]
        if used >= self.max_seconds:
            raise BudgetExceeded("Trial ASR time allowance exhausted")



def asr_settings(env, *, root=ROOT):
    value = env.get("ANGELENO_ASR_MAX_SECONDS", "")
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("ANGELENO_ASR_MAX_SECONDS must be an explicit positive integer") from exc
    if seconds < 1:
        raise ValueError("ANGELENO_ASR_MAX_SECONDS must be positive")
    data_dir = Path(env.get("ANGELENO_DATA_DIR", "private/state"))
    if not data_dir.is_absolute():
        data_dir = Path(root) / data_dir
    path = Path(env.get("ANGELENO_ASR_LEDGER", str(data_dir / "asr.sqlite3")))
    if not path.is_absolute():
        path = Path(root) / path
    if not path.resolve().is_relative_to((Path(root) / "private").resolve()):
        raise ValueError("ANGELENO_ASR_LEDGER must remain inside this repository's private directory")
    return path, seconds


def configured_allowance(env, *, root=ROOT):
    if env.get("ANGELENO_PAID_ENABLED", "0") != "1":
        raise ValueError("ASR requires ANGELENO_PAID_ENABLED=1")
    return ASRAllowance(*asr_settings(env, root=root))
