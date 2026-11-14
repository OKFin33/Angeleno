"""The comparison-report checker fails closed, and accepts either honest outcome."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from angeleno.comparison_boundaries import BOUNDARIES
from angeleno.comparison_freeze import PRODUCT_CANDIDATE
from angeleno.comparison_report import evaluate, render

ROOT = Path(__file__).resolve().parents[1]
COMMIT = PRODUCT_CANDIDATE


def _segment(number):
    return {
        "id": f"U{number}",
        "character_commit": COMMIT,
        "assistant_commit": COMMIT,
        "unhandled_branch_uncovered": False,
    }


def _report(*, segments=5, counts=(2, 2, 1, 0), e7="fail", product=False, key_facts=True, device=None):
    boundaries = {item["id"]: "pass" for item in BOUNDARIES}
    boundaries["E7"] = e7
    preference_keys = (
        "character_more_like_friend", "assistant_more_like_friend", "tie", "cannot_judge",
    )
    return {
        "format": "angeleno-comparison-report/1",
        "candidate_commit": COMMIT,
        "owner_calibration": {
            "anchors": [
                {"id": "DEV-D1", "judgment": "tie", "reasons": ["说话方式：两边都短，分不出朋友感。"], "rater": "fixture-rater"},
                {"id": "DEV-D2", "judgment": "character_more_like_friend", "reasons": ["有立场的取舍：人物臂拒绝了热门展。"], "rater": "fixture-rater"},
                {"id": "DEV-D3", "judgment": "assistant_more_like_friend", "reasons": ["熟悉边界：助手先承认不确定。"], "rater": "fixture-rater"},
            ],
        },
        "holdout": {
            "author_read_d1_d3": False,
            "author_declaration": "撰写人声明没有读过 D1–D3。",
            "custodian": "fixture-custodian",
            "text_in_repo": False,
            "segments": [_segment(number) for number in range(1, segments + 1)],
        },
        "boundaries": boundaries,
        "e7_device_evidence": device,
        "preference": {
            **dict(zip(preference_keys, counts, strict=True)),
            "blind": False,
            "rater_could_identify_arms": True,
            "note": "评分者能认出人物臂，这不是盲评。",
        },
        "key_facts_not_worse": key_facts,
        "extra_key_fact_floor": {"owner": "fixture-owner", "added_rule": None, "result": None},
        "comparison_language": {"owner": "fixture-owner", "language": "zh"},
        "paid_run": {
            "invoked": True,
            "budget_reference": "操作者本地账本，不入库",
            "key_location": "操作者本地环境，不入库",
        },
        "conclusion": {
            "product_condition_met": product,
            "text": "按证据，产品条件成立。" if product else "按证据，产品条件不成立。",
        },
    }


def _run(report, **kwargs):
    options = {
        "freeze_ok": True,
        "tracked_paths": ["README.md", "docs/spec.md"],
        "verify_exit": 0,
        "frozen_commit": COMMIT,
    }
    options.update(kwargs)
    return evaluate(report, **options)


class CheckerTests(unittest.TestCase):
    def test_missing_report_names_each_gap_and_does_not_invent_scores(self):
        result = _run(None, load_error="比较报告不存在：examples/comparison/comparison-report.json")
        text = render(result)
        self.assertFalse(result["ok"])
        self.assertIn("检查1：scripts/check_comparison_freeze.py 的冻结指纹仍然通过。", text)
        self.assertIn("owner_calibration", text)
        self.assertIn("4–6", text)
        self.assertIn("无离线测试", text)
        self.assertIn("不能写成盲评", text)
        self.assertIn("不等于", text)
        self.assertIn("不代填", text)
        self.assertIn("偏好计数：缺失", text)
        self.assertIn("结论：缺失", text)
        self.assertIn("退出：失败", text)
        self.assertNotIn("fixture-rater", text)

    def test_honest_negative_and_honest_positive_both_pass(self):
        negative = _run(_report())
        self.assertTrue(negative["ok"], negative["errors"])
        positive = _report(
            counts=(4, 1, 0, 0), e7="pass", product=True,
            device={"kind": "device", "note": "夹具中的实机记录，不是本仓库的听感结论。"},
        )
        self.assertTrue(_run(positive)["ok"], _run(positive)["errors"])

    def test_report_shape_rejects_missing_or_dishonest_fields(self):
        cases = []
        thin = _report()
        thin["owner_calibration"] = None
        cases.append(thin)
        short = _report(segments=3, counts=(1, 1, 1, 0))
        cases.append(short)
        long = _report(segments=7, counts=(2, 2, 2, 1))
        cases.append(long)
        read = _report()
        read["holdout"]["author_read_d1_d3"] = True
        cases.append(read)
        unnamed = _report()
        unnamed["holdout"]["custodian"] = "待定"
        cases.append(unnamed)
        drifted = _report()
        drifted["holdout"]["segments"][0]["assistant_commit"] = "0" * 40
        cases.append(drifted)
        uncovered = _report()
        uncovered["holdout"]["segments"][1]["unhandled_branch_uncovered"] = True
        cases.append(uncovered)
        insufficient = _report()
        insufficient["boundaries"]["E5"] = "insufficient"
        cases.append(insufficient)
        naked_e7 = _report(counts=(4, 1, 0, 0), e7="pass", product=True)
        cases.append(naked_e7)
        blind = _report()
        blind["preference"]["blind"] = True
        cases.append(blind)
        no_floor = _report()
        del no_floor["extra_key_fact_floor"]
        cases.append(no_floor)
        no_language = _report()
        del no_language["comparison_language"]
        cases.append(no_language)
        lied = _report(counts=(4, 1, 0, 0), e7="pass", product=False,
                       device={"kind": "device", "note": "夹具实机记录"})
        cases.append(lied)
        for report in cases:
            with self.subTest(report=report):
                self.assertFalse(_run(report)["ok"])

    def test_insufficient_key_facts_are_not_a_pass(self):
        report = _report()
        report["key_facts_not_worse"] = "insufficient"
        text = render(_run(report))
        self.assertIn("证据不足不算通过", text)
        self.assertIn("退出：失败", text)

    def test_freeze_privacy_verify_and_secrets_are_separate_failures(self):
        self.assertFalse(_run(_report(), freeze_ok=False)["ok"])
        private = _run(_report(), tracked_paths=["private/state.sqlite"])
        self.assertFalse(private["ok"])
        self.assertTrue(any("private/state.sqlite" in item for item in private["errors"]))
        self.assertFalse(_run(_report(), verify_exit=1)["ok"])
        self.assertFalse(_run(_report(), verify_exit=None)["ok"])
        leaked = _report()
        leaked["paid_run"]["key_location"] = "sk-secretvalue"
        self.assertFalse(_run(leaked)["ok"])

    def test_holdout_of_four_and_six_can_be_recorded(self):
        four = _report(segments=4, counts=(1, 1, 1, 1))
        six = _report(segments=6, counts=(2, 2, 1, 1))
        self.assertTrue(_run(four)["ok"], _run(four)["errors"])
        self.assertTrue(_run(six)["ok"], _run(six)["errors"])

    def test_cli_on_this_checkout_fails_because_the_report_is_absent(self):
        completed = subprocess.run(
            [sys.executable, "scripts/check_comparison_report.py"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("owner_calibration", completed.stdout)
        self.assertIn("无离线测试", completed.stdout)
        self.assertIn("退出：失败", completed.stdout)
        self.assertNotIn("sk-", completed.stdout)
        self.assertFalse((ROOT / "examples/comparison/comparison-report.json").exists())

    def test_cli_does_not_treat_a_partial_file_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps({"format": "angeleno-comparison-report/1"}), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "scripts/check_comparison_report.py", "--report", str(path)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("退出：失败", completed.stdout)

    def test_delivery_note_leaves_the_owner_blanks_open(self):
        text = (ROOT / "docs/delivery/2026-11-15-comparison-report-r1.md").read_text()
        self.assertIn("（空白，不代填）", text)
        self.assertIn("无离线测试", text)
        self.assertIn("docs/spec.md:258", text)
        self.assertNotIn('"product_condition_met": true', text)
        self.assertEqual(text.count("（空白，不代填）"), 5)
