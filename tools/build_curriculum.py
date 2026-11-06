#!/usr/bin/env python3
"""Build MathBank-compatible resources from an editable topic outline.

Nothing about the outline is hard-coded: neither its content, nor its field
names, nor the version code. All of that lives in topics/build.config.json.
The AI teaching context lives in topics/teaching_context.json.

Usage:
    python tools/build_curriculum.py                     # validate + generate
    python tools/build_curriculum.py --check            # validate only
    python tools/build_curriculum.py --install          # also write into source tree
    python tools/build_curriculum.py --input my.json    # override the source file

Outputs (build/ is ignored by upstream .gitignore, i.e. derived, never committed):
    build/<CODE>.json                  {group: {topic: [point, ...]}}  (upstream shape)
    build/topic_index.json             stable ids -> display path, plus untouched extras
    build/metadata_patch.json          values to patch into mathbank/curriculums.py
    build/curriculum_registry.json     version registry consumed by mathbank/curriculums.py
    build/knowledge_points.json        one stable local code per outline point
    build/teaching_context.json        stage labels, roles and AI scope rules
    build/paper_curriculum_standards.json  neutral placeholder when no excerpts are configured
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "topics" / "build.config.json"
DEFAULT_CONTEXT = ROOT / "topics" / "teaching_context.json"
DEFAULT_OUT = ROOT / "build"
CONFIG_SCHEMA = "topic-build-config-v1"
CONTEXT_SCHEMA = "teaching-context-v1"
KNOWLEDGE_SCHEMA = "mathbank-knowledge-points-v1"
REGISTRY_SCHEMA = "topic-registry-v1"

# Semantic slots. The actual JSON key for each slot comes from field_map.
SLOTS = ("groups", "group_name", "group_id", "children",
         "child_name", "child_id", "points")
UPSTREAM_CODES = {"A", "B", "S", "H", "N", "X"}

# Derived resources installed into the source tree. Paths are repo-relative.
INSTALL_TARGETS = {
    "curriculum_registry.json": ("mathbank", "resources", "curriculum_registry.json"),
    "knowledge_points.json": ("mathbank", "resources", "knowledge_points.json"),
    "teaching_context.json": ("mathbank", "resources", "teaching_context.json"),
    "paper_curriculum_standards.json": ("mathbank", "resources", "paper_curriculum_standards.json"),
}


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def resolve_path(raw: str, base: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(str(raw))))
    return expanded if expanded.is_absolute() else base / expanded


def as_text(value) -> str:
    return "" if value is None else str(value).strip()


def build(data: dict, config: dict):
    errors: list[str] = []
    warnings: list[str] = []
    fm = config["field_map"]

    code = as_text(config.get("version_code")).upper()
    if not re.fullmatch(r"[A-Z]", code):
        errors.append("version_code 必须是单个大写字母（上游按单字母文件命名）")
    if code in UPSTREAM_CODES:
        errors.append(f"version_code {code} 与上游六套教材冲突，请换一个字母")

    labels = config.get("display_labels") or {}
    for key in ("level1", "level2", "level3"):
        if not as_text(labels.get(key)):
            errors.append(f"display_labels.{key} 不能为空")

    groups_raw = data.get(fm["groups"])
    if not isinstance(groups_raw, list) or not groups_raw:
        return errors, warnings, None

    seen_ids: set[str] = set()
    seen_points: dict[str, str] = {}
    curriculum: dict[str, dict[str, list[str]]] = {}
    index_groups = []
    n_topics = 0
    n_points = 0
    empty_points: list[str] = []

    mapped_group_keys = {fm["group_name"], fm["group_id"], fm["children"]}
    mapped_child_keys = {fm["child_name"], fm["child_id"], fm["points"]}

    for gi, group in enumerate(groups_raw):
        if not isinstance(group, dict):
            errors.append(f"{fm['groups']}[{gi}] 必须是对象")
            continue
        gname = as_text(group.get(fm["group_name"]))
        gid = as_text(group.get(fm["group_id"]))
        where = f"{fm['groups']}[{gi}]"

        if not gname:
            errors.append(f"{where}.{fm['group_name']} 不能为空")
            continue
        if gname in curriculum:
            errors.append(f"重复专题大类名称: {gname}")
            continue
        if gid:
            if gid in seen_ids:
                errors.append(f"重复 id: {gid}")
            seen_ids.add(gid)

        children_raw = group.get(fm["children"])
        if not isinstance(children_raw, list) or not children_raw:
            errors.append(f"{where}.{fm['children']} 必须是非空数组")
            continue

        sections: dict[str, list[str]] = {}
        index_children = []
        for ti, topic in enumerate(children_raw):
            if not isinstance(topic, dict):
                errors.append(f"{where}.{fm['children']}[{ti}] 必须是对象")
                continue
            tname = as_text(topic.get(fm["child_name"]))
            tid = as_text(topic.get(fm["child_id"]))
            twhere = f"{where}.{fm['children']}[{ti}]"

            if not tname:
                errors.append(f"{twhere}.{fm['child_name']} 不能为空")
                continue
            if tname in sections:
                errors.append(f"同一大类下重复专题名称: {gname} / {tname}")
                continue
            if tid:
                if tid in seen_ids:
                    errors.append(f"重复 id: {tid}")
                seen_ids.add(tid)

            points_raw = topic.get(fm["points"])
            if not isinstance(points_raw, list):
                errors.append(f"{twhere}.{fm['points']} 必须是数组（允许为空）")
                continue

            clean_points = []
            for pi, point in enumerate(points_raw):
                text = as_text(point)
                if not text:
                    errors.append(f"{twhere}.{fm['points']}[{pi}] 为空字符串")
                    continue
                if text in clean_points:
                    errors.append(f"同一专题下重复考点: {gname} / {tname} / {text}")
                    continue
                clean_points.append(text)
                global_key = f"{gname}/{tname}/{text}"
                if global_key in seen_points:
                    warnings.append(f"同名考点出现在不同专题，AI 分类可能歧义: {text}")
                seen_points[global_key] = tid
                n_points += 1

            if not clean_points:
                empty_points.append(f"{tid or '?'} {gname} / {tname}")
                warnings.append(f"考点为空，上游三级筛选将显示空列表: {gname} / {tname}")

            sections[tname] = clean_points
            n_topics += 1
            index_children.append({
                "id": tid,
                "name": tname,
                "path": [gname, tname],
                "points": clean_points,
                "has_points": bool(clean_points),
                "extras": {k: v for k, v in topic.items() if k not in mapped_child_keys},
            })

        curriculum[gname] = sections
        index_groups.append({
            "id": gid,
            "name": gname,
            "children": index_children,
            "extras": {k: v for k, v in group.items() if k not in mapped_group_keys},
        })

    source_metadata = {}
    for key in config.get("metadata_fields") or []:
        if key in data:
            source_metadata[key] = data[key]

    path_by_id = {}
    for group in index_groups:
        for topic in group["children"]:
            if topic["id"]:
                path_by_id[topic["id"]] = topic["path"]

    result = {
        "code": code,
        "curriculum": curriculum,
        "index": {
            "schema_version": "junior-topic-index-v1",
            "version_code": code,
            "curriculum_name": config.get("curriculum_name"),
            "display_labels": labels,
            "source_metadata": source_metadata,
            "counts": {"groups": len(index_groups), "topics": n_topics,
                       "points": n_points, "empty_points": len(empty_points)},
            "empty_point_topics": empty_points,
            "warnings": warnings,
            "path_by_id": path_by_id,
            "groups": index_groups,
        },
        "patch": {
            "curriculum_version": code,
            "curriculum_name": config.get("curriculum_name"),
            "CURRICULUM_NAMES_entry": {code: config.get("curriculum_name")},
            "DEFAULT_DIFFICULTIES": config.get("difficulties"),
            "display_labels": labels,
            "note": "由 tools/build_curriculum.py 生成，勿手改；改专题大纲后重新运行",
        },
    }
    return errors, warnings, result


def build_registry(result: dict, config: dict) -> dict:
    return {
        "schema_version": REGISTRY_SCHEMA,
        "default_version": result["code"],
        "names": {result["code"]: config.get("curriculum_name")},
        "display_labels": config.get("display_labels") or {},
        "difficulties": config.get("difficulties") or [],
    }


def build_knowledge_points(result: dict, config: dict):
    """One stable local code per outline point, mapped onto the active version."""
    errors: list[str] = []
    cfg = config.get("knowledge_points") or {}
    prefix = as_text(cfg.get("code_prefix")) or "local:topic-"
    if not prefix.startswith("local:") or len(prefix) <= 6:
        errors.append("knowledge_points.code_prefix 必须以 local: 开头且长度大于 6")
    try:
        hash_length = int(cfg.get("code_hash_length", 8))
    except (TypeError, ValueError):
        hash_length = 0
    if not 6 <= hash_length <= 32:
        errors.append("knowledge_points.code_hash_length 必须在 6 到 32 之间")
    required = cfg.get("required_codes") or []
    if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
        errors.append("knowledge_points.required_codes 必须是字符串数组")

    code = result["code"]
    points, hierarchies, seen = [], [], set()
    for group in result["index"]["groups"]:
        gname = group["name"]
        for topic in group["children"]:
            tname = topic["name"]
            for point in topic["points"]:
                identity = f"{group['id']}|{topic['id']}|{point}"
                digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:hash_length]
                stable_code = f"{prefix}{topic['id'] or digest}-{digest}"
                if stable_code in seen:
                    errors.append(f"知识点编码冲突: {stable_code}")
                seen.add(stable_code)
                points.append({
                    "code": stable_code,
                    "name": point,
                    "original_name": point,
                    "source": "local",
                    "aliases": [],
                    "curriculum_paths": [{
                        "version_code": code,
                        "compulsory": gname,
                        "chapter": tname,
                        "knowledge": point,
                        "reference_section": point,
                        "level": "leaf",
                    }],
                })
                hierarchies.append({
                    "compulsory": gname,
                    "chapter": tname,
                    "knowledge": point,
                    "ancestors": [],
                })

    resource = {
        "schema_version": KNOWLEDGE_SCHEMA,
        "source": as_text(cfg.get("source_note")) or "由专题大纲生成",
        "provenance": {
            "generated_by": "tools/build_curriculum.py",
            "version_code": code,
            "policy": "一个考点对应一个稳定本地编码；编码由子专题 id 与考点文本哈希生成，与顺序无关",
        },
        "counts": {
            "points": len(points),
            "platform_points": 0,
            "local_points": len(points),
            "source_code_aliases": 0,
            "paths_by_version": {code: len(points)},
        },
        "required_codes": required,
        "source_code_aliases": {},
        "curriculum_hierarchies": {code: hierarchies},
        "points": points,
        "source_catalogs": {},
    }
    return errors, resource


def build_teaching_context(result: dict, context: dict) -> dict:
    labels = result["index"]["display_labels"]
    template_keys = {"classification_priority_rule", "paper_scope_rule"}
    rendered = {key: value for key, value in context.items() if key not in ("schema_version",)}
    for key in template_keys:
        if key in rendered:
            rendered[key] = rendered[key].format(
                c1=labels["level1"], c2=labels["level2"], c3=labels["level3"])
    return {
        "schema_version": CONTEXT_SCHEMA,
        "version_code": result["code"],
        "display_labels": labels,
        **rendered,
    }


def build_standards_placeholder(context: dict) -> dict:
    standards = context.get("standards") or {}
    return {
        "schema_version": 1,
        "enabled": False,
        "source": {
            "title": "未配置课程标准原文选摘",
            "edition": "",
            "source_pdf": "",
            "source_sha256": "0" * 64,
            "source_status": "not_configured",
            "scope": "当前版本不向 AI 组卷提供课标条文；需要时请提供带出处的原文选摘后重建本资源。",
            "excerpt_policy": "",
        },
        "warning": standards.get("disabled_notice", ""),
        "entries": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--input", type=Path, default=None,
                        help="override the source outline path")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repo", type=Path, default=ROOT,
                        help="root of the cloned MathBank source tree")
    parser.add_argument("--install", action="store_true",
                        help="also write generated resources into the source tree")
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[FAIL] 找不到配置文件: {args.config}")
        return 1
    config = read_json(args.config)
    if config.get("schema_version") != CONFIG_SCHEMA:
        print(f"[FAIL] 配置 schema_version 应为 {CONFIG_SCHEMA}")
        return 1
    fm = config.get("field_map") or {}
    missing = [slot for slot in SLOTS if not as_text(fm.get(slot))]
    if missing:
        print(f"[FAIL] field_map 缺少槽位: {', '.join(missing)}")
        return 1
    if not args.context.exists():
        print(f"[FAIL] 找不到学段语境配置: {args.context}")
        return 1
    context = read_json(args.context)
    if context.get("schema_version") != CONTEXT_SCHEMA:
        print(f"[FAIL] 学段语境 schema_version 应为 {CONTEXT_SCHEMA}")
        return 1

    source = args.input or resolve_path(config["source"], ROOT)
    if not source.exists():
        print(f"[FAIL] 找不到专题大纲: {source}")
        return 1

    data = read_json(source)
    errors, warnings, result = build(data, config)
    for warning in warnings:
        print(f"[WARN] {warning}")
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        print(f"\n校验失败：{len(errors)} 个错误，未生成任何文件。")
        return 1

    kp_errors, knowledge_points = build_knowledge_points(result, config)
    if kp_errors:
        for error in kp_errors:
            print(f"[FAIL] {error}")
        return 1

    counts = result["index"]["counts"]
    print(f"[OK] 大纲校验通过：{counts['groups']} 个大类 / {counts['topics']} 个子专题 / "
          f"{counts['points']} 个考点（{counts['empty_points']} 个专题考点为空）")
    print(f"[OK] 来源: {source}")
    print(f"[OK] AI 知识点目录: {len(knowledge_points['points'])} 个稳定本地编码")

    if args.check:
        print("[OK] --check 模式，未写入文件")
        return 0

    code = result["code"]
    try:
        teaching_context = build_teaching_context(result, context)
    except (KeyError, IndexError, ValueError) as exc:
        print(f"[FAIL] 学段语境模板渲染失败: {exc}")
        return 1
    standards = build_standards_placeholder(context)

    artifacts = {
        f"{code}.json": result["curriculum"],
        "topic_index.json": result["index"],
        "metadata_patch.json": result["patch"],
        "curriculum_registry.json": build_registry(result, config),
        "knowledge_points.json": knowledge_points,
        "teaching_context.json": teaching_context,
        "paper_curriculum_standards.json": standards,
    }
    for name, payload in artifacts.items():
        write_json(args.out_dir / name, payload)
        print(f"[OK] 已生成 {args.out_dir / name}")

    if args.install:
        curriculum_dir = args.repo / "mathbank" / "resources" / "curriculums"
        if not curriculum_dir.is_dir():
            print(f"[FAIL] 未找到 {curriculum_dir}，请先 clone 上游源码，或用 --repo 指定源码根目录")
            return 1
        write_json(curriculum_dir / f"{code}.json", result["curriculum"])
        print(f"[OK] 已安装到 {curriculum_dir / (code + '.json')}")
        for name, relative in INSTALL_TARGETS.items():
            if name == "paper_curriculum_standards.json" and (context.get("standards") or {}).get("enabled"):
                print(f"[SKIP] 课标选摘已启用，保留现有 {name}，不覆盖")
                continue
            target = args.repo.joinpath(*relative)
            write_json(target, artifacts[name])
            print(f"[OK] 已安装到 {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
