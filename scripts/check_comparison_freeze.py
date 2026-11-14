#!/usr/bin/env python3
"""Check the committed offline comparison freeze. No credentials, GPU or network."""
import json

from angeleno.comparison_freeze import assess


def main():
    report = assess()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
