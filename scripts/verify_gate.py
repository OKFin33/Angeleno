#!/usr/bin/env python3
"""Fail the offline gate when private/ or .env is tracked.

Bad path fixtures must be rejected. A clean listing, including .env.example,
must pass. The self-check then applies the same rule to git ls-files.
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
)

GOOD_FIXTURE = ".env.example\nREADME.md\ndocs/baseline.md\nLICENSES/Apache-2.0.txt\n"


def parse_paths(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def forbidden(paths: list[str]) -> list[str]:
    found = []
    for path in paths:
        normalized = path.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.strip("/")
        if not normalized:
            continue
        name = normalized.rsplit("/", 1)[-1]
        if normalized == "private" or normalized.startswith("private/"):
            found.append(path)
        elif name == ".env":
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
        print("tracked private/ or .env:", file=sys.stderr)
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
