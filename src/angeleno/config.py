"""Explicit runtime settings; no host account discovery."""

import os
from pathlib import Path
import re
import shlex


from angeleno.paths import ROOT


from .paths import resolve_path, resource_or_path, data_directory, private_file


def load_env(environ=None, *, env_file=None, root=ROOT):
    """Load optional KEY=value settings, then overlay explicit environment.

    The small file format supports quoted values and comments, but no shell
    execution, interpolation, implicit parent discovery, or credential lookup.
    A missing default file is fine. An explicitly requested file must exist.
    """
    explicit = dict(os.environ if environ is None else environ)
    selected = env_file if env_file is not None else explicit.get("ANGELENO_ENV_FILE")
    path = resolve_path(selected or "private/runtime.env", root=root)
    values = {}
    if path.exists():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, raw = line.partition("=")
            key = key.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"Invalid environment setting on line {number}")
            try:
                parts = shlex.split(raw, comments=True, posix=True)
            except ValueError:
                raise ValueError(f"Invalid environment value on line {number}") from None
            if len(parts) > 1:
                raise ValueError(f"Quote environment values containing spaces on line {number}")
            values[key] = parts[0] if parts else ""
    elif selected:
        raise ValueError("Explicit ANGELENO_ENV_FILE does not exist")
    values.update(explicit)
    return values


def flag(env, name, default="0"):
    value = env.get(name, default)
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1")
    return value == "1"
