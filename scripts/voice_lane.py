#!/usr/bin/env python3
"""Validate a voice-lane record. No audio, network, devices, or env files.

Exit 0 means the JSON is well-formed. It does not mean a person heard the
voice, and it does not authorize a public artifact by itself. Public-artifact
records are rejected unless the licence fields and checklist passes are present.
The checker never opens evidence paths or reference audio.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SCHEMA = "angeleno.voice_lane.v1"
LICENCE_SCHEMA = "angeleno.voice_licence.v1"
REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ITEMS = (
    "place_names",
    "backchannel",
    "barge_in",
    "playback",
    "latency_asr",
    "latency_model",
    "latency_tts_first_audio",
    "latency_playback",
)
ITEM_BASIS = {
    "place_names": "heard_place",
    "backchannel": "heard_turn",
    "barge_in": "heard_stop",
    "playback": "human_heard",
    "latency_asr": "stage_clock",
    "latency_model": "stage_clock",
    "latency_tts_first_audio": "stage_clock",
    "latency_playback": "stage_clock",
}
VOICE_SLOTS = {"private_reference", "replacement"}
ASSET_KINDS = {"reference_clip", "vendor_voice", "consented_recording"}
USES = {"private_test", "public_demo", "published_recording", "generated_speech"}
PUBLIC_USES = ("public_demo", "published_recording", "generated_speech")
RESULTS = {"pass", "fail", "not_run"}
MARKS = (
    "user_speech_end",
    "asr_confirmed",
    "model_first_token",
    "tts_first_audio",
    "playback_audible",
)
DURATION_EDGES = {
    "asr": ("user_speech_end", "asr_confirmed"),
    "model": ("asr_confirmed", "model_first_token"),
    "tts_first_audio": ("model_first_token", "tts_first_audio"),
    "playback": ("tts_first_audio", "playback_audible"),
}
SIGNALS = {
    "asr": "recognition_final",
    "model": "first_model_token",
    "tts_first_audio": "answer_audio",
    "playback": "human_heard",
}
OPTIONAL_MARKS = ("query_start", "query_end", "interruption", "audio_stop")
TOP_KEYS = {
    "schema",
    "voice_slot",
    "public_artifact",
    "public_uses",
    "licence",
    "items",
    "latency",
}
LICENCE_KEYS = {
    "schema",
    "asset_kind",
    "covers",
    "cloning_explicit",
    "model_weight_licence_noted",
    "record_path",
}
LATENCY_REQUIRED = {
    "sample_count",
    "claims_stable_percentile",
    "human_heard",
    "answer_audio_not_filler",
    "marks_ms",
    "durations_ms",
    "signals",
}
PASS_KEYS = {"result", "basis", "heard", "evidence_path"}
FAIL_KEYS = {"result", "reason"}
NOT_RUN_KEYS = {"result"}

SECRET_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:api[_-]?key|secret|token|password)\s*[:=]\s*\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?<![A-Za-z0-9_])sk-[A-Za-z0-9]{16,}"
)
ENV_SECRET_RE = re.compile(
    r"\b(?:DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|LIVEKIT_API_KEY|LIVEKIT_API_SECRET"
    r"|VOXCPM_REFERENCE)\s*=\s*\S+"
)


def blank_document() -> dict:
    """Private-test sheet with every item still not run."""
    return {
        "schema": SCHEMA,
        "voice_slot": "private_reference",
        "public_artifact": False,
        "public_uses": [],
        "licence": None,
        "items": {name: {"result": "not_run"} for name in REQUIRED_ITEMS},
        "latency": None,
    }


def _nonneg_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def _sensitive_path(path: Path) -> bool:
    for part in path.parts:
        if part in {".env", "runtime.env"} or part.startswith(".env") or part.endswith(".env"):
            return True
    return False


def evidence_errors(path, *, repo_root: Path) -> list[str]:
    """Return path-rule errors. Do not open the path or require it to exist."""
    if not isinstance(path, str) or not path.strip():
        return ["must be an absolute path"]
    if "\n" in path or "\x00" in path:
        return ["must be a single path"]
    candidate = Path(path)
    if not candidate.is_absolute():
        return ["must be an absolute path"]
    resolved = candidate.resolve(strict=False)
    if _sensitive_path(resolved):
        return ["must not be an env file"]
    root = repo_root.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return []
    if not relative.parts or relative.parts[0] != "private":
        return ["inside the repository must stay under private/"]
    return []


def _scan_secrets(document, errors: list[str]) -> None:
    for text in _iter_strings(document):
        if SECRET_RE.search(text) or ENV_SECRET_RE.search(text):
            errors.append("document contains a credential-like value")
            return


def _unknown(fields, allowed: set[str]) -> str:
    return ", ".join(sorted(set(fields) - allowed))


def _validate_licence(licence, errors: list[str], *, repo_root: Path, public_uses: list[str]) -> None:
    if not isinstance(licence, dict):
        errors.append("licence must be an object or null")
        return
    extra = _unknown(licence, LICENCE_KEYS)
    if extra:
        errors.append(f"unknown licence fields: {extra}")
    if licence.get("schema") != LICENCE_SCHEMA:
        errors.append(f"licence.schema must be {LICENCE_SCHEMA}")
    kind = licence.get("asset_kind")
    if kind not in ASSET_KINDS:
        errors.append("licence.asset_kind is not recognized")
    covers = licence.get("covers")
    if not isinstance(covers, list) or not covers:
        errors.append("licence.covers must be a non-empty list")
        covers = []
    elif any(not isinstance(item, str) for item in covers):
        errors.append("licence.covers must be a list of strings")
        covers = []
    else:
        if len(covers) != len(set(covers)):
            errors.append("licence.covers contains a duplicate")
        for item in covers:
            if item not in USES:
                errors.append(f"licence cover is not recognized: {item}")
    for use in public_uses:
        if use not in covers:
            errors.append(f"public use is not covered: {use}")
    cloning = licence.get("cloning_explicit")
    if not isinstance(cloning, bool):
        errors.append("licence.cloning_explicit must be a boolean")
    elif public_uses and kind == "reference_clip" and cloning is not True:
        errors.append("reference_clip public use requires cloning_explicit true")
    noted = licence.get("model_weight_licence_noted")
    if not isinstance(noted, bool):
        errors.append("licence.model_weight_licence_noted must be a boolean")
    elif public_uses and noted is not True:
        errors.append("public artifact requires model_weight_licence_noted true")
    for message in evidence_errors(licence.get("record_path"), repo_root=repo_root):
        errors.append(f"licence.record_path {message}")


def _validate_items(items, errors: list[str], *, repo_root: Path) -> dict:
    if not isinstance(items, dict):
        errors.append("items must be an object")
        return {}
    extra = _unknown(items, set(REQUIRED_ITEMS))
    if extra:
        errors.append(f"unknown checklist item: {extra}")
    for name in REQUIRED_ITEMS:
        if name not in items:
            errors.append(f"missing checklist item: {name}")
            continue
        item = items[name]
        if not isinstance(item, dict):
            errors.append(f"item {name} must be an object")
            continue
        result = item.get("result")
        if result not in RESULTS:
            errors.append(f"item {name} result must be pass, fail, or not_run")
            continue
        allowed = {"pass": PASS_KEYS, "fail": FAIL_KEYS, "not_run": NOT_RUN_KEYS}[result]
        unknown = _unknown(item, allowed)
        if unknown:
            errors.append(f"item {name} has unknown fields: {unknown}")
        if result == "pass":
            if item.get("basis") != ITEM_BASIS[name]:
                errors.append(f"item {name} pass requires basis {ITEM_BASIS[name]}")
            heard = item.get("heard")
            if not isinstance(heard, str) or not heard.strip():
                errors.append(f"item {name} pass requires a non-empty heard note")
            for message in evidence_errors(item.get("evidence_path"), repo_root=repo_root):
                errors.append(f"item {name} evidence path {message}")
        elif result == "fail":
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"item {name} fail requires a non-empty reason")
    return items


def _validate_latency(latency, errors: list[str]) -> dict | None:
    if latency is None:
        return None
    if not isinstance(latency, dict):
        errors.append("latency must be an object or null")
        return None
    extra = _unknown(latency, LATENCY_REQUIRED | {"optional_marks_ms"})
    if extra:
        errors.append(f"unknown latency fields: {extra}")
    missing = sorted(LATENCY_REQUIRED - set(latency))
    if missing:
        errors.append("missing latency fields: " + ", ".join(missing))
    sample_count = latency.get("sample_count")
    if not _nonneg_int(sample_count) or sample_count < 1:
        errors.append("sample_count must be an integer of at least 1")
    if latency.get("claims_stable_percentile") is not False:
        errors.append("claims_stable_percentile must be false")
    if not isinstance(latency.get("human_heard"), bool):
        errors.append("human_heard must be a boolean")
    if not isinstance(latency.get("answer_audio_not_filler"), bool):
        errors.append("answer_audio_not_filler must be a boolean")
    marks = latency.get("marks_ms")
    durations = latency.get("durations_ms")
    if not isinstance(marks, dict):
        errors.append("marks_ms must be an object")
        marks = {}
    else:
        mark_extra = _unknown(marks, set(MARKS))
        if mark_extra:
            errors.append(f"unknown marks: {mark_extra}")
        previous = None
        for name in MARKS:
            value = marks.get(name)
            if not _nonneg_int(value):
                errors.append(f"marks_ms.{name} must be a non-negative integer")
                continue
            if name == "user_speech_end" and value != 0:
                errors.append("marks_ms.user_speech_end must be 0")
            if previous is not None and value < previous:
                errors.append(f"marks_ms.{name} is earlier than the previous mark")
            previous = value
    if not isinstance(durations, dict):
        errors.append("durations_ms must be an object")
    else:
        duration_extra = _unknown(durations, set(DURATION_EDGES))
        if duration_extra:
            errors.append(f"unknown durations: {duration_extra}")
        for name, (start, end) in DURATION_EDGES.items():
            got = durations.get(name)
            if not _nonneg_int(got):
                errors.append(f"durations_ms.{name} must be a non-negative integer")
                continue
            if _nonneg_int(marks.get(start)) and _nonneg_int(marks.get(end)) and got != marks[end] - marks[start]:
                errors.append(f"durations_ms.{name} must equal {end} minus {start}")
    if latency.get("signals") != SIGNALS:
        errors.append("signals must match the voice-lane stage signals")
    if "optional_marks_ms" in latency:
        optional = latency.get("optional_marks_ms")
        if not isinstance(optional, dict):
            errors.append("optional_marks_ms must be an object")
        else:
            unknown = _unknown(optional, set(OPTIONAL_MARKS))
            if unknown:
                errors.append(f"optional mark is not recognized: {unknown}")
            values = {}
            for name in OPTIONAL_MARKS:
                if name not in optional:
                    continue
                value = optional[name]
                if not _nonneg_int(value):
                    errors.append(f"optional_marks_ms.{name} must be a non-negative integer")
                else:
                    values[name] = value
            if "query_end" in values and "query_start" in values and values["query_end"] < values["query_start"]:
                errors.append("query_end is earlier than query_start")
            if "audio_stop" in values and "interruption" in values and values["audio_stop"] < values["interruption"]:
                errors.append("audio_stop is earlier than interruption")
    return latency


def validate_voice_lane(document, *, repo_root: Path = REPO_ROOT) -> list[str]:
    """Return structural errors. An empty list means the record is well-formed."""
    if not isinstance(document, dict):
        return ["document must be a JSON object"]
    errors: list[str] = []
    _scan_secrets(document, errors)
    extra = _unknown(document, TOP_KEYS)
    if extra:
        errors.append(f"unknown document fields: {extra}")
    if document.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    slot = document.get("voice_slot")
    if slot not in VOICE_SLOTS:
        errors.append("voice_slot must be private_reference or replacement")
    public = document.get("public_artifact")
    if not isinstance(public, bool):
        errors.append("public_artifact must be a boolean")
        public = False
    public_uses = document.get("public_uses")
    if not isinstance(public_uses, list):
        errors.append("public_uses must be a list")
        public_uses = []
    elif any(not isinstance(item, str) for item in public_uses):
        errors.append("public_uses must be a list of strings")
        public_uses = []
    else:
        if len(public_uses) != len(set(public_uses)):
            errors.append("public_uses contains a duplicate")
        for item in public_uses:
            if item not in PUBLIC_USES:
                errors.append(f"public use is not recognized: {item}")
        if public and not public_uses:
            errors.append("public_uses must name at least one public use")
        if not public and public_uses:
            errors.append("public_uses must be empty when public_artifact is false")
    if public and slot == "private_reference":
        errors.append("public artifact is blocked while voice_slot is private_reference")
    licence = document.get("licence", None)
    if public and licence is None:
        errors.append("public artifact requires a licence record")
    elif licence is not None:
        _validate_licence(licence, errors, repo_root=repo_root, public_uses=public_uses if public else [])
    items = _validate_items(document.get("items"), errors, repo_root=repo_root)
    if "latency" not in document:
        errors.append("latency must be an object or null")
        latency = None
    else:
        latency = _validate_latency(document.get("latency"), errors)
    if isinstance(items, dict):
        for name in REQUIRED_ITEMS:
            item = items.get(name)
            if not isinstance(item, dict) or item.get("result") != "pass":
                continue
            if name.startswith("latency_") and latency is None:
                errors.append(f"latency item {name} pass requires a latency record")
        if latency is not None:
            filler_ok = latency.get("answer_audio_not_filler") is True
            heard_ok = latency.get("human_heard") is True
            tts = items.get("latency_tts_first_audio")
            if isinstance(tts, dict) and tts.get("result") == "pass" and not filler_ok:
                errors.append("latency_tts_first_audio pass requires answer_audio_not_filler true")
            played = items.get("latency_playback")
            if isinstance(played, dict) and played.get("result") == "pass" and not heard_ok:
                errors.append("latency_playback pass requires human_heard true")
            playback = items.get("playback")
            if isinstance(playback, dict) and playback.get("result") == "pass" and not heard_ok:
                errors.append("playback pass contradicts latency.human_heard false")
    if public:
        if not isinstance(items, dict) or any(
            not isinstance(items.get(name), dict) or items[name].get("result") != "pass"
            for name in REQUIRED_ITEMS
        ):
            errors.append("public artifact requires every checklist item to pass")
        if latency is None:
            errors.append("public artifact requires a latency record")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("blank", "check"))
    parser.add_argument("path", nargs="?")
    parser.add_argument("--repo-root", help="Repository root used only for evidence-path checks")
    args = parser.parse_args(argv)
    if args.action == "blank":
        if args.path is not None:
            print("blank does not take a path", file=sys.stderr)
            return 2
        json.dump(blank_document(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if not args.path:
        print("check requires a path", file=sys.stderr)
        return 2
    try:
        raw = Path(args.path).read_text(encoding="utf-8")
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"invalid JSON: {exc}", file=sys.stderr)
        return 1
    root = Path(args.repo_root) if args.repo_root else REPO_ROOT
    errors = validate_voice_lane(document, repo_root=root)
    json.dump({"well_formed": not errors, "errors": errors}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
