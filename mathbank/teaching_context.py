"""Stage-specific wording for AI prompts, loaded from editable JSON resources.

Prompt builders never embed a stage, subject, topic name or difficulty label.
The generated copy lives in mathbank/resources/; the editable sources live in
topics/ and are used as a fallback before ``build_curriculum.py --install``
has been run.
"""
from __future__ import annotations

from functools import lru_cache
import json

from .paths import CURRICULUMS_DIR, PROJECT_ROOT


RESOURCE_PATH = CURRICULUMS_DIR.parent / "teaching_context.json"
SOURCE_PATH = PROJECT_ROOT / "topics" / "teaching_context.json"
CONFIG_PATH = PROJECT_ROOT / "topics" / "build.config.json"
SCHEMA = "teaching-context-v1"
_TEMPLATE_KEYS = ("classification_priority_rule", "paper_scope_rule")


def _render(raw: dict, labels: dict) -> dict:
    data = dict(raw)
    data["display_labels"] = labels
    for key in _TEMPLATE_KEYS:
        if key in data:
            data[key] = data[key].format(
                c1=labels["level1"], c2=labels["level2"], c3=labels["level3"])
    return data


@lru_cache(maxsize=1)
def _load() -> dict:
    if RESOURCE_PATH.exists():
        data = json.loads(RESOURCE_PATH.read_text(encoding="utf-8"))
        source = RESOURCE_PATH
    elif SOURCE_PATH.exists():
        data = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
        source = SOURCE_PATH
    else:
        raise RuntimeError("缺少学段语境配置：请运行 python tools/build_curriculum.py --install")
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
        raise ValueError(f"学段语境资源版本无效: {source}")
    if "display_labels" not in data:
        if not CONFIG_PATH.exists():
            raise ValueError("学段语境缺少 display_labels，且找不到 topics/build.config.json")
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        data = _render(data, config["display_labels"])
        data["version_code"] = str(config["version_code"]).upper()
    return data


def context() -> dict:
    """Read-only snapshot of the active teaching context."""
    return _load()


def labels() -> dict:
    return _load()["display_labels"]


def label(level: str) -> str:
    return labels()[level]


def role(key: str) -> str:
    value = _load().get("roles", {}).get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"学段语境缺少角色: {key}")
    return value


def text(key: str) -> str:
    value = _load().get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"学段语境缺少文本: {key}")
    return value


def classification_priority_rule() -> str:
    return text("classification_priority_rule")


def standards() -> dict:
    value = _load().get("standards")
    if not isinstance(value, dict):
        raise ValueError("学段语境缺少 standards 配置")
    return value


def stage_label() -> str:
    data = _load()
    return f"{data.get('stage', '')}{data.get('subject', '')}".strip()
