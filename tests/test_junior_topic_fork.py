"""Fork contract: the editable topic outline drives every AI-facing resource.

These checks are independent of the upstream high-school fixtures. They fail
when a topic name, version code or wording rule is accidentally hardcoded.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mathbank import teaching_context as tc  # noqa: E402
from mathbank.curriculums import CURRICULUM_NAMES, DEFAULT_VERSION, load_curriculum  # noqa: E402
from mathbank.knowledge_points import _resource_catalog  # noqa: E402
from mathbank.paper_standards import build_paper_standard_context  # noqa: E402
from mathbank.prompts import (  # noqa: E402
    CLASSIFICATION_PRIORITY_RULE, build_ai_solve_prompts, build_classification_system_prompt,
    build_import_parse_system_prompt, build_paper_selection_prompts, build_pdf_parse_system_prompt,
)


def _read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


CONFIG = _read("topics/build.config.json")
OUTLINE = json.loads((ROOT / CONFIG["source"]).read_text(encoding="utf-8"))
FM = CONFIG["field_map"]


def _builder():
    spec = importlib.util.spec_from_file_location("topic_builder", ROOT / "tools/build_curriculum.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _outline_groups():
    return OUTLINE[FM["groups"]]


def test_registry_and_installed_outline_agree():
    registry = _read("mathbank/resources/curriculum_registry.json")
    code = CONFIG["version_code"].upper()
    assert DEFAULT_VERSION == registry["default_version"] == code
    assert CURRICULUM_NAMES == {code: CONFIG["curriculum_name"]}
    tree = load_curriculum()
    groups = _outline_groups()
    assert list(tree) == [group[FM["group_name"]] for group in groups]
    assert sum(len(chapters) for chapters in tree.values()) == sum(
        len(group[FM["children"]]) for group in groups)
    for group in groups:
        for topic in group[FM["children"]]:
            assert tree[group[FM["group_name"]]][topic[FM["child_name"]]] == topic[FM["points"]]


def test_knowledge_codes_are_local_stable_and_point_at_real_topics():
    errors, resource = _builder().build_knowledge_points(
        _builder().build(OUTLINE, CONFIG)[2], CONFIG)
    assert errors == []
    tree = load_curriculum()
    paths = [path for point in resource["points"] for path in point["curriculum_paths"]]
    assert len(resource["points"]) == sum(
        len(topic[FM["points"]]) for group in _outline_groups()
        for topic in group[FM["children"]])
    for point in resource["points"]:
        assert point["source"] == "local"
        assert point["code"].startswith(CONFIG["knowledge_points"]["code_prefix"])
        assert point["aliases"] == []
    for path in paths:
        assert path["version_code"] == DEFAULT_VERSION
        assert path["knowledge"] in tree[path["compulsory"]][path["chapter"]]
        assert path["level"] == "leaf"
    catalog = _resource_catalog()
    assert len(catalog["points"]) == len(resource["points"])
    assert set(catalog["hierarchies"]) == {DEFAULT_VERSION}


def test_reordering_the_outline_does_not_change_a_point_code():
    builder = _builder()
    _, _, result = builder.build(OUTLINE, CONFIG)
    _, first = builder.build_knowledge_points(result, CONFIG)
    shuffled = json.loads(json.dumps(OUTLINE, ensure_ascii=False))
    for group in shuffled[FM["groups"]]:
        group[FM["children"]].reverse()
    _, _, moved = builder.build(shuffled, CONFIG)
    _, second = builder.build_knowledge_points(moved, CONFIG)
    def codes(payload):
        return {(point["curriculum_paths"][0]["compulsory"],
                 point["curriculum_paths"][0]["chapter"], point["name"]): point["code"]
                for point in payload["points"]}
    assert codes(first) == codes(second)


def test_ai_prompts_contain_no_high_school_context():
    tree = load_curriculum()
    prompts = [
        build_ai_solve_prompts("detailed_answer", "求 $(-2)^4$")[0],
        build_classification_system_prompt(tree, []),
        build_classification_system_prompt(tree, [], knowledge_profile="target_first"),
        build_pdf_parse_system_prompt(tree, False, []),
        build_import_parse_system_prompt(tree, "tex", []),
        build_paper_selection_prompts("有理数的乘方，5题", 5, [], False)[0],
        CLASSIFICATION_PRIORITY_RULE,
    ]
    forbidden = ["高考", "高中数学", "必修", "选修", "强基", "平面向量",
                 "圆锥曲线", "学段", "数列", "椭圆", "奇偶性", "单调性"]
    for prompt in prompts:
        for word in forbidden:
            assert word not in prompt, word
    assert tc.stage_label() in prompts[0]
    assert tc.label("level1") in prompts[1]
    assert tc.text("scope_rule") in prompts[0]


def test_standard_context_is_disabled_without_verbatim_excerpts():
    result = build_paper_standard_context("有理数的乘方，5道题，不要超纲")
    assert set(result) >= {"enabled", "source", "mode", "matched_topics", "rules",
                           "requirements", "references", "warnings",
                           "teacher_constraints", "usage_policy", "context_sha256"}
    if not tc.standards()["enabled"]:
        assert result["enabled"] is False
        assert result["requirements"] == result["references"] == []
        assert result["warnings"]
        assert any("不要超纲" in row for row in result["teacher_constraints"])


def test_builder_contains_no_outline_content_or_version_code():
    source = (ROOT / "tools/build_curriculum.py").read_text(encoding="utf-8")
    for group in _outline_groups():
        assert group[FM["group_name"]] not in source
        for topic in group[FM["children"]]:
            assert topic[FM["child_name"]] not in source
            for point in topic[FM["points"]]:
                assert point not in source
    assert f'"{CONFIG["version_code"]}"' not in source
    assert f'"{FM["children"]}"' not in source


def test_outline_validation_still_passes():
    result = subprocess.run(
        [sys.executable, "tools/build_curriculum.py", "--check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "大纲校验通过" in result.stdout
