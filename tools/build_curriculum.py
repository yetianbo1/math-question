#!/usr/bin/env python3
"""Build MathBank-compatible curriculum JSON from an editable topic outline.

Nothing about the outline is hard-coded: neither its content, nor its field
names, nor the version code. All of that lives in topics/build.config.json.

Usage:
    python tools/build_curriculum.py                     # validate + generate
    python tools/build_curriculum.py --check            # validate only
    python tools/build_curriculum.py --install          # also write into source tree
    python tools/build_curriculum.py --input my.json    # override the source file

Outputs (build/ is ignored by upstream .gitignore, i.e. derived, never committed):
    build/<CODE>.json          {group: {topic: [point, ...]}}  (upstream shape)
    build/topic_index.json     stable ids -> display path, plus untouched extras
    build/metadata_patch.json  values to patch into mathbank/curriculums.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "topics" / "build.config.json"
DEFAULT_OUT = ROOT / "build"
CONFIG_SCHEMA = "topic-build-config-v1"

# Semantic slots. The actual JSON key for each slot comes from field_map.
SLOTS = ("groups", "group_name", "group_id", "children",
         "child_name", "child_id", "points")
UPSTREAM_CODES = {"A", "B", "S", "H", "N", "X"}


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--input", type=Path, default=None,
                        help="override the source outline path")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repo", type=Path, default=ROOT,
                        help="root of the cloned MathBank source tree")
    parser.add_argument("--install", action="store_true",
                        help="also write <repo>/mathbank/resources/curriculums/<CODE>.json")
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

    counts = result["index"]["counts"]
    print(f"[OK] 大纲校验通过：{counts['groups']} 个大类 / {counts['topics']} 个子专题 / "
          f"{counts['points']} 个考点（{counts['empty_points']} 个专题考点为空）")
    print(f"[OK] 来源: {source}")

    if args.check:
        print("[OK] --check 模式，未写入文件")
        return 0

    code = result["code"]
    write_json(args.out_dir / f"{code}.json", result["curriculum"])
    write_json(args.out_dir / "topic_index.json", result["index"])
    write_json(args.out_dir / "metadata_patch.json", result["patch"])
    print(f"[OK] 已生成 {args.out_dir / (code + '.json')}")
    print(f"[OK] 已生成 {args.out_dir / 'topic_index.json'}")
    print(f"[OK] 已生成 {args.out_dir / 'metadata_patch.json'}")

    if args.install:
        target = args.repo / "mathbank" / "resources" / "curriculums" / f"{code}.json"
        if not target.parent.is_dir():
            print(f"[FAIL] 未找到 {target.parent}，请先 clone 上游源码，或用 --repo 指定源码根目录")
            return 1
        write_json(target, result["curriculum"])
        print(f"[OK] 已安装到 {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
