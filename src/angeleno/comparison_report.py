"""Structural checks for the same-material comparison report.

Exit-worthy success is a complete, consistent record. It is not a claim that
Angeleno won. Missing calibration, holdout, verdicts or an honest conclusion
fail closed. 「insufficient」 is never a pass.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

from angeleno.comparison_boundaries import BOUNDARIES, NO_OFFLINE_TEST, boundary_ids, e7_verdict_allowed
from angeleno.comparison_freeze import PRODUCT_CANDIDATE, assess, source_root

REPORT_FORMAT = "angeleno-comparison-report/1"
ANCHORS = ("DEV-D1", "DEV-D2", "DEV-D3")
PREF_KEYS = (
    "character_more_like_friend",
    "assistant_more_like_friend",
    "tie",
    "cannot_judge",
)
PLACEHOLDERS = {"", "空白", "待定", "未定", "TBD", "tbd", "TODO", "todo", "none", "null", "N/A", "n/a"}
SECRET = re.compile(
    r"(?i)(api[_-]?key\s*[:=]\s*\S+|secret\s*[:=]\s*\S+|sk-[A-Za-z0-9]{8,}|"
    r"BEGIN (?:RSA |OPENSSH )?PRIVATE KEY|bearer\s+[A-Za-z0-9._-]{8,})"
)
OWNER_BLANKS = (
    "D1／D2／D3 的 Owner 校准",
    "「关键事实不比助手差」有没有额外下限",
    "付费运行的预算和密钥放在哪",
    "未见保留集的保管人",
    "比较语言（冻结锚例是 zh，产品默认现在是 en）",
)


def default_report_path(root=None):
    root = source_root() if root is None else Path(root)
    return root / "examples" / "comparison" / "comparison-report.json"


def _gate():
    root = source_root()
    path = root / "scripts" / "verify_gate.py"
    spec = importlib.util.spec_from_file_location("angeleno_verify_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_report(path):
    path = Path(path)
    if not path.is_file():
        return None, None, f"比较报告不存在：{path}"
    try:
        raw = path.read_text()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, f"比较报告读不了：{exc}"
    return data, raw, None


def _name(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text in PLACEHOLDERS:
        return ""
    return text


def _contains_secret(value):
    if isinstance(value, str):
        return SECRET.search(value) is not None
    if isinstance(value, dict):
        return any(_contains_secret(key) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return False


def check_calibration(report):
    block = report.get("owner_calibration")
    if not isinstance(block, dict):
        return ["检查2：owner_calibration 为空。需要 D1、D2、D3 各有判断、理由和评分者名字（docs/spec.md:253）。不代填。"]
    rows = block.get("anchors")
    if not isinstance(rows, list) or [row.get("id") if isinstance(row, dict) else None for row in rows] != list(ANCHORS):
        return ["检查2：开发锚例必须恰好是 DEV-D1、DEV-D2、DEV-D3，顺序固定。"]
    errors = []
    raters = []
    for row in rows:
        anchor = row["id"]
        judgment = row.get("judgment")
        reasons = row.get("reasons")
        rater = _name(row.get("rater"))
        if judgment not in PREF_KEYS:
            errors.append(f"检查2：{anchor} 没有成对判断。")
        if not isinstance(reasons, list) or not reasons or any(not isinstance(item, str) or not item.strip() for item in reasons):
            errors.append(f"检查2：{anchor} 没有理由。")
        if not rater:
            errors.append(f"检查2：{anchor} 没有评分者名字。")
        else:
            raters.append(rater)
    if raters and len(set(raters)) != 1:
        errors.append("检查2：三对锚例的评分者不一致。")
    return errors


def check_holdout(report, frozen_commit):
    block = report.get("holdout")
    errors = []
    if not isinstance(block, dict):
        return ["检查3：没有未见保留集。需要 4–6 段、作者声明没有读过 D1–D3、两臂同一候选提交，且没有未处理的 branch_uncovered（docs/spec.md:245）。"]
    if block.get("author_read_d1_d3") is not False:
        errors.append("检查3：作者没有声明自己未读 D1–D3。")
    declaration = block.get("author_declaration")
    if not isinstance(declaration, str) or "没有读过" not in declaration or "D1" not in declaration or "D3" not in declaration:
        errors.append("检查3：作者声明里要写明没有读过 D1–D3。")
    if not _name(block.get("custodian")):
        errors.append("检查3：未见保留集的保管人仍是空白，不代填姓名。")
    if block.get("text_in_repo") is not False:
        errors.append("检查3：保留集正文不能进调优树（docs/spec.md:247）。")
    segments = block.get("segments")
    if not isinstance(segments, list) or not 4 <= len(segments) <= 6:
        errors.append("检查3：保留集必须是 4–6 段。")
        segments = []
    seen = set()
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"检查3：第 {index + 1} 段不是对象。")
            continue
        label = _name(segment.get("id")) or str(index + 1)
        if label in seen:
            errors.append(f"检查3：段 {label} 重复。")
        seen.add(label)
        commits = (segment.get("character_commit"), segment.get("assistant_commit"))
        if commits[0] != commits[1] or commits[0] != report.get("candidate_commit") or commits[0] != frozen_commit:
            errors.append(f"检查3：段 {label} 的两臂不在同一冻结候选 {frozen_commit} 上。")
        if segment.get("unhandled_branch_uncovered") is not False:
            errors.append(f"检查3：段 {label} 有未处理的 branch_uncovered。")
    return errors


def check_boundaries(report):
    got = report.get("boundaries")
    if not isinstance(got, dict):
        return ["检查4：E3–E8 和五条工程边界没有 pass/fail。证据不足不算通过（docs/spec.md:257）。"]
    errors = []
    for boundary_id in boundary_ids():
        value = got.get(boundary_id)
        if value not in {"pass", "fail"}:
            errors.append(f"检查4：{boundary_id} 的记录是 {value!r}，不是 pass 或 fail。证据不足不算通过。")
            continue
        if boundary_id == "E7" and not e7_verdict_allowed(value, report.get("e7_device_evidence")):
            errors.append("检查4：E7 写成 pass，但没有实机证据。无离线测试不算通过。")
    extra = sorted(set(got) - set(boundary_ids()))
    if extra:
        errors.append("检查4：未知边界 " + "、".join(extra) + "。")
    return errors


def _counts(report):
    preference = report.get("preference")
    if not isinstance(preference, dict):
        return None
    counts = []
    for key in PREF_KEYS:
        value = preference.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        counts.append(value)
    return counts


def _product_supported(report):
    boundaries = report.get("boundaries")
    if not isinstance(boundaries, dict):
        return False
    if any(boundaries.get(item["id"]) != "pass" for item in BOUNDARIES):
        return False
    if not e7_verdict_allowed("pass", report.get("e7_device_evidence")):
        return False
    if report.get("key_facts_not_worse") is not True:
        return False
    counts = _counts(report)
    if counts is None:
        return False
    total = sum(counts)
    if counts[0] * 2 <= total:
        return False
    floor = report.get("extra_key_fact_floor")
    if isinstance(floor, dict):
        rule = floor.get("added_rule")
        if isinstance(rule, str) and rule.strip() and floor.get("result") != "pass":
            return False
    return True


def check_preference(report):
    errors = []
    preference = report.get("preference")
    if not isinstance(preference, dict):
        errors.append("检查5：没有偏好计数。")
    else:
        counts = _counts(report)
        segments = report.get("holdout", {}).get("segments") if isinstance(report.get("holdout"), dict) else None
        if counts is None:
            errors.append("检查5：偏好计数必须是四个非负整数。")
        elif isinstance(segments, list) and len(segments) != sum(counts):
            errors.append("检查5：偏好计数之和要等于保留集段数。")
        if preference.get("blind") is not False or preference.get("rater_could_identify_arms") is not True:
            errors.append("检查5：必须写明评分者能认出两臂，这不是盲评（docs/spec.md:253）。")
        note = preference.get("note")
        if not isinstance(note, str) or "认出" not in note or "盲" not in note:
            errors.append("检查5：结论旁要注明评分者能认出两臂、不是盲评。")
    if report.get("key_facts_not_worse") not in {True, False}:
        errors.append("检查5：关键事实是否不比助手差必须是 true 或 false。证据不足不算通过（docs/spec.md:258）。")
    conclusion = report.get("conclusion")
    if not isinstance(conclusion, dict) or not isinstance(conclusion.get("text"), str) or not conclusion.get("text").strip():
        errors.append("检查5：没有结论。spec.md:258 的产品条件就是这条结论，成立或不成立都要写明。")
    elif not isinstance(conclusion.get("product_condition_met"), bool):
        errors.append("检查5：产品条件必须写成 true 或 false，不能靠报告填满来暗示通过。")
    elif not errors and conclusion["product_condition_met"] is not _product_supported(report):
        errors.append("检查5：结论和计数、边界、关键事实不一致。报告写完不等于产品条件成立。")
    return errors


def check_owner_fields(report):
    errors = []
    floor = report.get("extra_key_fact_floor")
    if not isinstance(floor, dict) or not _name(floor.get("owner")):
        errors.append("检查5：关键事实的额外下限没有 Owner 声明，不代为设定。")
    else:
        rule = floor.get("added_rule")
        result = floor.get("result")
        if rule is None:
            if result is not None:
                errors.append("检查5：没有额外下限时，不能再写一条额外结果。")
        elif not isinstance(rule, str) or not rule.strip() or result not in {"pass", "fail"}:
            errors.append("检查5：额外下限一旦写了，结果只能是 pass 或 fail。证据不足不算通过。")
    language = report.get("comparison_language")
    if not isinstance(language, dict) or not _name(language.get("owner")) or language.get("language") not in {"zh", "en"}:
        errors.append("检查5：比较语言没有 Owner 决定。冻结锚例是 zh，产品默认是 en，不代选。")
    paid = report.get("paid_run")
    if not isinstance(paid, dict) or paid.get("invoked") is not True:
        errors.append("检查3：没有已执行的付费同料运行记录。")
    elif not _name(paid.get("budget_reference")) or not _name(paid.get("key_location")):
        errors.append("检查3：付费预算和密钥位置仍是空白，不代填，也不把密钥写进报告。")
    return errors


def check_secrets(report, raw_text):
    if _contains_secret(report) or (raw_text and SECRET.search(raw_text)):
        return ["检查6：报告里有密钥样式，拒绝入库。"]
    return []


def check_privacy(tracked_paths):
    found = _gate().forbidden(list(tracked_paths))
    if found:
        return ["检查6：跟踪路径含私有文件、账本或未登记媒体：" + "、".join(found)]
    return []


def missing_report_errors(load_error):
    detail = load_error or "比较报告不存在。"
    return [
        f"检查2：{detail} owner_calibration 为空。D1、D2、D3 的判断、理由和评分者名字都还是空白，不代填（docs/spec.md:253）。",
        "检查3：未见保留集还没有。需要 4–6 段、作者声明没有读过 D1–D3、两臂跑在同一冻结候选上，且没有未处理的 branch_uncovered（docs/spec.md:245）。保管人空白。",
        "检查4：E3–E8 和五条工程边界没有 pass/fail。insufficient 和无离线测试都不算通过（docs/spec.md:257）。",
        "检查5：没有偏好计数，也没有结论。评分者能认出两臂，不能写成盲评（docs/spec.md:253）。",
        "报告写完不等于 spec.md:258 的产品条件成立。条件成立或不成立都要按证据写；两种结果都算交付，但现在还没有这份结果。",
    ]


def evaluate(report, *, freeze_ok, tracked_paths, verify_exit, frozen_commit=PRODUCT_CANDIDATE, raw_text=None, load_error=None):
    notes = []
    errors = []
    if freeze_ok:
        notes.append("检查1：scripts/check_comparison_freeze.py 的冻结指纹仍然通过。")
    else:
        errors.append("检查1：冻结指纹变了，scripts/check_comparison_freeze.py 不能退出 0。")
    privacy = check_privacy(tracked_paths)
    errors.extend(privacy)
    if not privacy:
        notes.append("检查6：已跟踪路径没有 private、账本、密钥或未登记媒体。")
    if report is None:
        errors.extend(missing_report_errors(load_error))
        notes.append("检查6：比较报告缺失，本次不运行 make verify，也不把别的通过写成比较结论。")
        blanks = "仍留空、不代填：\n" + "\n".join(f"- {item}" for item in OWNER_BLANKS)
    else:
        blanks = ""
        if not isinstance(report, dict) or report.get("format") != REPORT_FORMAT:
            errors.append("检查2：比较报告格式不是 angeleno-comparison-report/1。")
        else:
            if report.get("candidate_commit") != frozen_commit:
                errors.append(f"检查3：报告候选 {report.get('candidate_commit')!r} 不是冻结候选 {frozen_commit}。")
            errors.extend(check_calibration(report))
            errors.extend(check_holdout(report, frozen_commit))
            errors.extend(check_boundaries(report))
            errors.extend(check_owner_fields(report))
            errors.extend(check_preference(report))
            errors.extend(check_secrets(report, raw_text))
        blocking = [item for item in errors if item.startswith(("检查2", "检查3", "检查4", "检查5"))]
        if blocking:
            if verify_exit == 0:
                notes.append("检查6：make verify 退出码 0，但不能抵消上面的缺项。")
            elif verify_exit not in (None, 0):
                errors.append(f"检查6：make verify 退出码 {verify_exit}。")
            else:
                notes.append("检查6：比较输入不完整，没有把 make verify 当成这项比较的通过。")
        elif verify_exit != 0:
            shown = "没有退出码" if verify_exit is None else f"退出码 {verify_exit}"
            errors.append(f"检查6：make verify {shown}。")
        else:
            notes.append("检查6：make verify 退出码 0。")
    return {
        "ok": not errors,
        "errors": errors,
        "notes": notes,
        "preference_text": render_preference(report),
        "map_text": render_boundary_map(),
        "blanks": blanks,
    }


def render_preference(report):
    if not isinstance(report, dict) or not isinstance(report.get("preference"), dict):
        return (
            "偏好计数：缺失\n"
            "结论：缺失\n"
            "盲评：不能声称。评判者通常能认出人物臂（docs/spec.md:253）。这次没有评分记录。"
        )
    preference = report["preference"]
    counts = "、".join(f"{key}={preference.get(key)!r}" for key in PREF_KEYS)
    conclusion = report.get("conclusion") if isinstance(report.get("conclusion"), dict) else {}
    met = conclusion.get("product_condition_met")
    text = conclusion.get("text") or "缺失"
    blind = "评分者能认出两臂，这不是盲评。" if preference.get("blind") is False else "盲评声明不合规格。"
    return f"偏好计数：{counts}\n结论：product_condition_met={met!r} {text}\n{blind}"


def render_boundary_map():
    lines = ["A1 边界映射（不是比较分数）："]
    for item in BOUNDARIES:
        if item["status"] == NO_OFFLINE_TEST:
            lines.append(f"- {item['id']} {item['title']}：无离线测试。{item['reason']}")
        else:
            first = item["tests"][0]
            lines.append(f"- {item['id']} {item['title']}：{first['file']}:{first['line']} {first['name']}")
    return "\n".join(lines)


def render(result):
    parts = [*result["errors"], *result["notes"], result["preference_text"], result["map_text"]]
    if result.get("blanks"):
        parts.append(result["blanks"])
    parts.append("退出：通过" if result["ok"] else "退出：失败")
    return "\n".join(parts) + "\n"


def tracked_files(root):
    return _gate().git_ls_files(Path(root))


def run_make_verify(root):
    completed = subprocess.run(["make", "verify"], cwd=Path(root), check=False)
    return completed.returncode


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="检查文字同料比较报告。缺输入时失败。")
    parser.add_argument("--report", default=None, help="比较报告 JSON。默认路径故意不存在。")
    args = parser.parse_args(argv)
    root = source_root()
    freeze = assess(root)
    path = Path(args.report) if args.report else default_report_path(root)
    report, raw, load_error = load_report(path)
    tracked = tracked_files(root)
    verify_exit = None
    if isinstance(report, dict) and freeze.get("ok"):
        preview = evaluate(
            report, freeze_ok=True, tracked_paths=tracked, verify_exit=0,
            frozen_commit=PRODUCT_CANDIDATE, raw_text=raw,
        )
        if preview["ok"]:
            verify_exit = run_make_verify(root)
    result = evaluate(
        report, freeze_ok=bool(freeze.get("ok")), tracked_paths=tracked,
        verify_exit=verify_exit, frozen_commit=PRODUCT_CANDIDATE, raw_text=raw, load_error=load_error,
    )
    sys.stdout.write(render(result))
    return 0 if result["ok"] else 1
