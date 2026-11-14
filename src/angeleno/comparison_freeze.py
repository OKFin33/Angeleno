"""Offline freeze checklist for the same-material comparison.

This module pins the candidate, model parameters, shared sources and scoring
rubric that can be checked without a GPU, a ledger or Owner calibration.
It does not authorize ``comparison_live --run`` and it does not score anyone.
"""
import json
from pathlib import Path

from .author import assistant_control_pack, load_pack
from .comparison import digest, file_hash, load_config

PRODUCT_CANDIDATE = "4185b8eb29e8fa62ddc32300cc9db526f6704e15"
MODEL_KEYS = (
    "model", "temperature", "writer_temperature", "actor_max_tokens", "writer_max_tokens",
    "actor_requests_per_turn", "tools_per_turn", "writer_requests_per_segment",
    "actor_requests_total", "writer_requests_total", "turn_timeout_seconds",
    "maintenance_timeout_seconds", "variant", "clock_utc", "timezone", "language",
)
PINNED_FILES = (
    "examples/comparison/development.json",
    "examples/comparison/anchors.json",
    "examples/comparison/corpus.synthetic.json",
    "examples/comparison/scoring-rubric.json",
    "examples/comparison/scenarios.json",
    "characters/jo/author-pack.json",
    "characters/jo/NOTICE",
    "characters/jo/persona/role-core.md",
    "characters/jo/persona/character-bible.md",
    "characters/jo/persona/continuity.md",
    "characters/jo/persona/grounding.md",
    "data/places/fixtures/fisher-visit.html",
    "src/angeleno/author.py",
    "src/angeleno/comparison.py",
)
OPEN_GAPS = (
    {
        "id": "non_grounding_persona_facts_not_copied",
        "status": "open",
        "resolution": None,
        "blocks_formal_comparison": True,
        "summary": "人物核心、人物圣经和连续性里的现实说法不会复制给助手。通勤分钟数一类的具体估计只在这些文档里；共享的 grounding 只写明车程属于作者估计。本清单不新写补充，也不改 Jo 原稿。",
    },
    {
        "id": "historical_private_supplements_absent",
        "status": "open",
        "resolution": None,
        "blocks_formal_comparison": True,
        "summary": "2026-10-31 放在 private/evaluation 的六组共享补充不在本检出中，这里也没有按那份私有审计重核。",
    },
    {
        "id": "immutable_model_revision_unknown",
        "status": "open",
        "resolution": None,
        "blocks_formal_comparison": True,
        "summary": "deepseek-flash 是配置别名。没有不可变的供应商模型版本。",
    },
    {
        "id": "owner_calibration_absent",
        "status": "open",
        "resolution": None,
        "blocks_formal_comparison": True,
        "summary": "Owner 还没有对三对开发锚例给出判断和理由。量表因此不能用来打正式分。",
    },
    {
        "id": "unseen_holdout_absent",
        "status": "open",
        "resolution": None,
        "blocks_formal_comparison": True,
        "summary": "五段保留提案写在本仓库里，起草时已经读过 D1–D3。没有独立保管的未见集，也没有运行。",
    },
    {
        "id": "real_model_comparison_not_run",
        "status": "open",
        "resolution": None,
        "blocks_formal_comparison": True,
        "summary": "没有在这个候选上执行 comparison_live --run。早先见过的三对模型输出不在仓库里，不能当成这次结果。",
    },
)


def source_root():
    here = Path(__file__).resolve()
    candidates = [here.parents[2], Path.cwd().resolve()]
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / "examples/comparison/development.json").is_file():
            return candidate
    raise ValueError("Comparison freeze checklist is resolved from the source checkout")


def checklist_path(root=None):
    return (source_root() if root is None else Path(root)) / "examples/comparison/freeze-checklist.json"


def _packs(root):
    pack = load_pack(root / "characters/jo/author-pack.json")
    assistant = assistant_control_pack(pack)
    shared = tuple((d.path, d.sha256, d.body) for d in pack.documents if d.kind == "grounding")
    copied = tuple((d.path, d.sha256, d.body) for d in assistant.documents if d.kind == "grounding")
    if pack.role != "character" or shared != copied:
        raise ValueError("Jo grounding is not shared verbatim with the assistant arm")
    if [d.path for d in assistant.documents] != ["assistant-control", "persona/grounding.md"]:
        raise ValueError("Assistant arm received material other than its setting and shared grounding")
    if [d.path for d in pack.documents] != [
            "persona/role-core.md", "persona/character-bible.md", "persona/continuity.md", "persona/grounding.md"]:
        raise ValueError("Jo document list drifted")
    return pack, assistant


def _materials(root, anchors):
    scenarios = json.loads((root / "examples/comparison/scenarios.json").read_text())
    rubric = json.loads((root / "examples/comparison/scoring-rubric.json").read_text())
    holdout = scenarios["holdout_proposals"]
    anchor_ids = [item["id"] for item in anchors]
    if (scenarios["format"] != "angeleno-comparison-scenarios/1"
            or scenarios["unseen"] is not False or scenarios["holdout_executed"] is not False
            or scenarios["holdout_count"] != 5 or len(holdout) != 5
            or [item["id"] for item in scenarios["development"]] != anchor_ids
            or scenarios["language"] != "zh"):
        raise ValueError("Scenario materials are not the prepared visible set")
    for item in (*scenarios["development"], *holdout):
        if item.get("outputs_in_repo") is True or item.get("unseen") is True or item.get("executed") is True:
            raise ValueError("Scenario materials claim unseen or completed outputs")
        for key in ("jo_output", "assistant_output", "owner_preference"):
            if key in item and item[key] is not None:
                raise ValueError("Scenario materials contain scores or outputs")
    if [item["id"] for item in holdout] != ["H1", "H2", "H3", "H4", "H5"]:
        raise ValueError("Holdout proposal ids drifted")
    for item in holdout:
        if not item["user_turns"] or any(not turn.strip() for turn in item["user_turns"]):
            raise ValueError("Empty holdout turn")
        text = "\n".join(item["user_turns"])
        if "2026-11-26" in text or "不用查了" in text:
            raise ValueError("Fisher development exception reused as a holdout turn")
    if rubric["format"] != "angeleno-comparison-rubric/1" or rubric["status"] != "criteria_frozen_scores_empty":
        raise ValueError("Scoring rubric is not frozen criteria")
    if [feature["id"] for feature in rubric["features"]] != [
            "stance", "lived_context", "knows_you", "speech", "familiarity_limit", "checks_what_matters"]:
        raise ValueError("Scoring features drifted")
    if any(rubric[key] is not None for key in ("owner_calibration", "applied_scores", "second_model_scores")):
        raise ValueError("Scoring rubric contains scores")
    if scenarios["not_prepared"] != [{
            "id": "E7-voice",
            "reason": "播放中插话、挂断重拨和未播完结果属于真实语音。这次不写假听感，也不把文字回合当成 E7 通过。"}]:
        raise ValueError("Voice scenario must stay unprepared")
    return scenarios, rubric


def build_checklist(root=None):
    """Canonical checklist from the checkout. Scores and holdout results stay empty."""
    root = source_root() if root is None else Path(root)
    config_path = root / "examples/comparison/development.json"
    config, anchors, corpus = load_config(config_path)
    pack, assistant = _packs(root)
    scenarios, _rubric = _materials(root, anchors)
    fisher = next(row for row in corpus["records"] if row["id"] == "SYN-FISHER")
    fixture = root / fisher["content_fixture"]
    if file_hash(fixture) != fisher["content_fixture_sha256"]:
        raise ValueError("Fisher development fixture drifted")
    if config["frozen_for_real_comparison"] is not False or config["paid_execution"] is not False:
        raise ValueError("Development runner config must stay offline")
    if config["holdout_included"] is not False or config["owner_calibration"] is not None:
        raise ValueError("Development runner config must not carry holdout or calibration")
    queries = corpus["queries"]
    if len(queries) != 24 or [row["id"] for row in corpus["records"]] != ["SYN-A", "SYN-B", "SYN-FISHER"]:
        raise ValueError("Synthetic corpus shape drifted")
    return {
        "format": "angeleno-comparison-freeze-checklist/1",
        "status": "offline_controls_frozen",
        "executable_live_manifest": False,
        "authorizes_paid_run": False,
        "owner_calibration": None,
        "holdout_executed": False,
        "preference_scores": None,
        "product_candidate": {
            "commit": PRODUCT_CANDIDATE,
            "merged_pull_requests": [30, 31, 32, 33],
            "meaning": "main 上已合入的产品代码。PR #30 是中文 v0.1.0，PR #31 是公开交接，PR #32 是 v0.1.1 默认英文，PR #33 是可安装的 src 布局。比较锚例仍是中文。本文件不是可执行的付费 manifest；之后的 --run 必须另绑当时干净的 HEAD、原账本和授权。产品循环若再改，这份候选就要重建。",
        },
        "model_parameters": {key: config[key] for key in MODEL_KEYS},
        "model_version_limitation": "configured_alias_no_immutable_provider_revision",
        "comparison_language_enforced_by_runner": True,
        "file_sha256": {rel: file_hash(root / rel) for rel in PINNED_FILES},
        "roles_sha256": {
            "character": digest(pack.provenance()),
            "assistant": digest(assistant.provenance()),
        },
        "character_documents": [d.path for d in pack.documents],
        "assistant_documents": [d.path for d in assistant.documents],
        "shared_grounding": ["characters/jo/persona/grounding.md"],
        "corpus": {
            "file": "examples/comparison/corpus.synthetic.json",
            "cases": corpus["cases"],
            "query_count": len(queries),
            "record_ids": [row["id"] for row in corpus["records"]],
            "acceptance": "双方同一套合成开发集合。24 组映射里的空匹配只说明这个集合没有记录，不是现实里不存在。Fisher 契约样本只属于开发锚例。",
        },
        "development_anchors": [item["id"] for item in anchors],
        "scenarios_file": "examples/comparison/scenarios.json",
        "rubric_file": "examples/comparison/scoring-rubric.json",
        "holdout_proposal_ids": [item["id"] for item in scenarios["holdout_proposals"]],
        "holdout_count": 5,
        "holdout_unseen": False,
        "open_gaps": [dict(item) for item in OPEN_GAPS],
    }


def _diff(left, right, prefix=""):
    if left == right:
        return ""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right), key=str):
            if left.get(key) != right.get(key):
                deeper = _diff(left.get(key), right.get(key), prefix + str(key) + ".")
                if deeper:
                    return deeper
        return prefix or "root"
    return prefix or "root"


def difference(data, root=None):
    root = source_root() if root is None else Path(root)
    expected = build_checklist(root)
    if data == expected:
        return []
    return ["checklist drift: " + _diff(data, expected)]


def _public_report(data):
    return {
        "status": data.get("status"),
        "product_candidate": (data.get("product_candidate") or {}).get("commit"),
        "model": (data.get("model_parameters") or {}).get("model"),
        "language": (data.get("model_parameters") or {}).get("language"),
        "shared_grounding": data.get("shared_grounding"),
        "development_anchors": data.get("development_anchors"),
        "holdout_proposals": data.get("holdout_count"),
        "holdout_executed": data.get("holdout_executed"),
        "owner_calibration": data.get("owner_calibration"),
        "executable_live_manifest": data.get("executable_live_manifest"),
        "open_gaps": [item.get("id") for item in data.get("open_gaps") or []],
    }


def assess(root=None):
    """Compare the committed checklist with the checkout. No network and no credentials."""
    root = source_root() if root is None else Path(root)
    path = checklist_path(root)
    try:
        expected = build_checklist(root)
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        return {"ok": False, "errors": [type(exc).__name__ + ": " + str(exc)],
                "owner_calibration": None, "holdout_executed": False,
                "executable_live_manifest": False, "open_gaps": []}
    report = _public_report(data)
    report["errors"] = [] if data == expected else ["checklist drift: " + _diff(data, expected)]
    report["ok"] = not report["errors"]
    return report
