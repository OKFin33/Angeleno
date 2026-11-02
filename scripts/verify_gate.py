#!/usr/bin/env python3
"""Reject tracked runtime data, credentials and unapproved reference media.

This path gate is a guardrail, not a content or full Git-history secret scan.
Synthetic JSON/HTML/text fixtures remain normal source assets. Any future audio
fixture needs an exact reviewed allowlist entry under tests/fixtures/synthetic-media/.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BAD_FIXTURES = (
    "private/state.sqlite\n",
    ".env\n",
    "notes.md\nprivate/ops.md\n",
    "worker/.env\n",
    ".env.production\n",
    "notes/state.sqlite3-wal\n",
    ".scratch/work/output.json\n",
    "assets/reference.wav\n",
)

GOOD_FIXTURE = ".env.example\nREADME.md\ndocs/baseline.md\nLICENSES/Apache-2.0.txt\n"


def parse_paths(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


# No audio fixtures are currently shipped. Add only individually reviewed,
# generated test clips here; a directory name alone must never allow a recording.
SYNTHETIC_MEDIA_ALLOWLIST: frozenset[str] = frozenset()
MEDIA_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".wma", ".mp4", ".mov", ".webm", ".mkv"}
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def forbidden(paths: list[str], *, synthetic_media_allowlist=SYNTHETIC_MEDIA_ALLOWLIST) -> list[str]:
    for allowed in synthetic_media_allowlist:
        parts = allowed.split("/")
        if (not allowed.startswith("tests/fixtures/synthetic-media/")
                or any(p in {"", ".", ".."} for p in parts)
                or "\\" in allowed or Path(allowed).suffix.lower() not in MEDIA_SUFFIXES):
            raise ValueError("Synthetic media exceptions must name exact test fixture files")
    found = []
    for path in paths:
        normalized = path.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.strip("/")
        if not normalized:
            continue
        lowered = normalized.lower()
        name = lowered.rsplit("/", 1)[-1]
        database_name = name
        for sidecar in ("-wal", "-shm", "-journal"):
            if database_name.endswith(sidecar):
                database_name = database_name[:-len(sidecar)]
                break
        if (lowered in {"private", ".scratch", "evidence/raw"}
                or lowered.startswith(("private/", ".scratch/", "evidence/raw/"))):
            found.append(path)
        elif name == ".env" or (name.startswith(".env.") and normalized != ".env.example"):
            found.append(path)
        elif Path(database_name).suffix in DATABASE_SUFFIXES:
            found.append(path)
        elif Path(name).suffix in MEDIA_SUFFIXES and normalized not in synthetic_media_allowlist:
            found.append(path)
    return found


def git_ls_files(root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or "git ls-files failed"
        raise RuntimeError(detail)
    raw = result.stdout.decode("utf-8", "surrogateescape")
    return [item for item in raw.split("\0") if item]


def self_check(tracked: list[str] | None = None) -> int:
    for fixture in BAD_FIXTURES:
        if not forbidden(parse_paths(fixture)):
            print("bad fixture must fail", file=sys.stderr)
            return 1
    if forbidden(parse_paths(GOOD_FIXTURE)):
        print("good fixture must pass", file=sys.stderr)
        return 1
    if tracked is None:
        try:
            tracked = git_ls_files()
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
    found = forbidden(tracked)
    if found:
        print("tracked runtime data, credentials or unapproved media:", file=sys.stderr)
        for item in found:
            print(item, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in ([], ["--self-check"]):
        return self_check()
    if len(args) == 2 and args[0] == "--fixture":
        try:
            text = Path(args[1]).read_text(encoding="utf-8")
        except OSError as exc:
            print(exc, file=sys.stderr)
            return 1
        found = forbidden(parse_paths(text))
        if found:
            print("bad fixture must fail:", file=sys.stderr)
            for item in found:
                print(item, file=sys.stderr)
            return 1
        return 0
    print("usage: verify_gate.py [--self-check | --fixture PATH]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
