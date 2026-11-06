"""Bounded, source-backed curriculum context for existing paper-model calls.

The topic fork does not ship curriculum-standard excerpts: inventing clause
text would be worse than providing none. ``topics/teaching_context.json``
declares whether excerpts are configured. While they are not, selection uses
teacher constraints and the existing structural filters only.

When excerpts are configured later, they must be verbatim, source-addressable
passages in ``paper_curriculum_standards.json``; this module verifies each
excerpt's identity and never reads a user's curriculum folder or database.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from . import teaching_context
from .paths import CURRICULUMS_DIR


RESOURCE_PATH = CURRICULUMS_DIR.parent / "paper_curriculum_standards.json"
MAX_RESOURCE_BYTES = 512 * 1024
MAX_CONTEXT_CHARS = 14000
MAX_REQUIREMENTS = 16
_SHA = re.compile(r"[0-9a-f]{64}")
_NEGATIVE = r"(?:不要|不选|排除|不含|不考|不涉及|不包含|不使用|不得|禁止|尚未学(?:习)?|还没学(?:习)?|没有学(?:习)?|未学(?:习)?|没学(?:习)?)(?:过|到|的)?"
_KINDS = {"general", "content", "achievement", "teaching"}


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _finish(context):
    context["context_sha256"] = _digest(_serialize(context))
    return context


def _constraints(prompt):
    return [part.strip() for part in re.split(r"[，。；;\n]", prompt)
            if re.search(_NEGATIVE, part) and part.strip()][:8]


@lru_cache(maxsize=4)
def _load_resource(path: str, modified_ns: int, size: int):
    """Verify the bundled excerpt identities; size/stat bust this cache."""
    if size > MAX_RESOURCE_BYTES:
        raise ValueError("oversized resource")
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_RESOURCE_BYTES + 1)
    if len(raw) > MAX_RESOURCE_BYTES:
        raise ValueError("oversized resource")
    resource = json.loads(raw)
    if (not isinstance(resource, dict) or type(resource.get("schema_version")) is not int
            or resource["schema_version"] != 1 or not isinstance(resource.get("entries"), list)
            or not 1 <= len(resource["entries"]) <= 500):
        raise ValueError("invalid resource schema")
    source = resource.get("source")
    if not isinstance(source, dict) or not _SHA.fullmatch(str(source.get("source_sha256", ""))):
        raise ValueError("missing source identity")
    seen = set()
    for entry in resource["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"]:
            raise ValueError("missing excerpt")
        if entry.get("id") in seen or not isinstance(entry.get("id"), str):
            raise ValueError("ambiguous excerpt identity")
        seen.add(entry["id"])
        if entry.get("source_sha256") != source["source_sha256"] or _digest(entry["text"]) != entry.get("text_sha256"):
            raise ValueError("excerpt identity mismatch")
        if (not isinstance(entry.get("document"), str) or not entry["document"].startswith("docs/")
                or ".." in entry["document"].split("/") or "\\" in entry["document"]):
            raise ValueError("invalid document reference")
        for field in ("pdf_pages", "book_pages"):
            if not isinstance(entry.get(field), list) or not entry[field] or any(
                    type(page) is not int or page < 1 for page in entry[field]):
                raise ValueError("missing source pages")
        if (not isinstance(entry.get("topics"), list)
                or any(not isinstance(topic, str) for topic in entry["topics"])
                or not isinstance(entry.get("heading"), str)
                or entry.get("kind") not in _KINDS
                or not isinstance(entry.get("course_type"), str) or not entry["course_type"]):
            raise ValueError("invalid source heading")
    return resource, hashlib.sha256(raw).hexdigest()


def _topic_words():
    """Topic names come from the active outline, never from a fixed word list."""
    from .curriculums import load_curriculum
    words = set()
    for group, topics in load_curriculum().items():
        words.add(group)
        for topic, points in topics.items():
            words.add(topic)
            words.update(points)
    return {word for word in words if len(word) > 1}


def _reference(entry):
    return {key: deepcopy(entry[key]) for key in
            ("id", "document", "heading", "pdf_pages", "book_pages", "source_sha256", "text_sha256")}


def _node(entry):
    return {"reference_id": entry["id"], "course_type": entry["course_type"], "kind": entry["kind"],
            "topics": list(entry["topics"]), "text": entry["text"]}


def build_paper_standard_context(teacher_prompt, filters=None, candidates=None):
    """Return one frozen selection snapshot for all existing model stages.

    ``filters`` and ``candidates`` remain in the signature for request
    compatibility; no scope is inferred from them when no excerpts exist.
    """
    del filters, candidates
    settings = teaching_context.standards()
    prompt = teacher_prompt if isinstance(teacher_prompt, str) else ""
    context = {
        "enabled": False, "source": {}, "mode": "classroom", "matched_topics": [],
        "rules": [], "requirements": [], "references": [], "warnings": [],
        "teacher_constraints": _constraints(prompt),
        "usage_policy": settings.get("usage_policy", ""),
        "course_scope": [], "excluded_topics": [],
    }
    if not settings.get("enabled"):
        context["warnings"].append(settings.get(
            "disabled_notice", "当前未配置课程标准原文选摘，本次不提供条文依据。"))
        return _finish(context)

    try:
        info = RESOURCE_PATH.stat()
        resource, resource_sha = _load_resource(str(RESOURCE_PATH), info.st_mtime_ns, info.st_size)
    except (OSError, ValueError, TypeError, KeyError):
        context["warnings"].append("课标选摘资源暂不可用，本次未提供条文依据；仍按教师要求和原有筛选选题。")
        return _finish(context)

    context["enabled"] = True
    context["source"] = {**resource["source"], "resource_sha256": resource_sha}
    words = _topic_words()
    chosen = sorted((word for word in words if word in prompt), key=len, reverse=True)
    context["matched_topics"] = chosen[:16]
    if not chosen:
        context["warnings"].append("未能可靠定位具体专题，本次仅提供通用命题要求。")

    selected = set()

    def append(entry, field):
        if entry["id"] in selected:
            return True
        context[field].append(_node(entry))
        context["references"].append(_reference(entry))
        if len(json.dumps(context, ensure_ascii=False)) > MAX_CONTEXT_CHARS - 260:
            context[field].pop()
            context["references"].pop()
            return False
        selected.add(entry["id"])
        return True

    for entry in resource["entries"]:
        if entry["kind"] == "general":
            append(entry, "rules")
    suitable = [entry for entry in resource["entries"] if entry["kind"] != "general"]
    suitable.sort(key=lambda item: (
        -sum(1 for word in chosen if word in item["text"] or word in item["topics"]),
        {"content": 0, "achievement": 1, "teaching": 2}[item["kind"]],
        len(item["text"]),
    ))
    for entry in suitable[:MAX_REQUIREMENTS]:
        if not append(entry, "requirements"):
            context["warnings"].append("本次课标选摘超过文字预算，已保留完整可引用条目，请按实际引用审阅。")
            break
    return _finish(context)
