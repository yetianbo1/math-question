"""Pure annotation guidance: task targets, solution evidence and narrow boundaries.

This module never reads a catalogue or changes an identity. Request builders
supply the complete frozen catalogue; optional notes appear only when both a
known stable code and a verified name are present in that same snapshot.
Topic-specific boundary notes are declared in topics/teaching_context.json.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from mathbank import teaching_context


KNOWLEDGE_ANNOTATION_GUIDANCE_VERSION = "knowledge-target-evidence.v4"

_TARGET_FIRST_RULE = (
    "\n【先覆盖目标，再选辅助，最后定位专题】只做分类，不输出新解答。\n"
    "1. 通读全部条件、小问与选项。把每个待求/待证/待判断产物分别列在内部清单中，"
    "按“数学对象+要完成的动作+条件范围”在全目录比较候选，而非只匹配主题词。"
    "annotation_targets的goal_quotes是本地从题干提取的原文目标，可直接复制；"
    "不将已给公式、背景名词或设问前置条件当成目标。\n"
    "2. 先为每个能匹配的目标选择最贴切的main，再考虑辅助。"
    "main是直接任务或该任务对应的专门应用能力；求某个量时，求该量的运算本身是main，"
    "不能因为它先于后续步骤就降为auxiliary。求参数范围、求某个事件结果、列完整结果、"
    "求角大小与证明性质是不同任务。优先检查含参、由性质求参、基本量计算或综合应用等"
    "准确条目；只有名字更长或出现字母参数并不足以选它。"
    "同一目标已选准确具体点时，省去重复的背景定义/泛公式点。\n"
    "3. 逐小问复核main覆盖：不能让某小问只有auxiliary而没有其直接目标的main。"
    "目录确无贴切条目时允许该目标不标，不造码或错套；其他小问仍正常标注。"
    "同一编码服务多个目标时合并target_evidence数组；一个点只要是某问的直接目标，"
    "就按main处理，不因它同时帮助另一问而整体降级。专题先后规则只决定专题位置。\n"
    "4. main覆盖完成后，通常只保留0至2个重要auxiliary，不凑数量。"
    "辅助点须有实际提供的解析/评析步骤和相符的完整点名，不能用“体现了/涉及”来泛化。"
    "常规代入、四则运算、移项、计算普通中间量默认省略；专门方法若本身正是待求任务，"
    "可为main，不能仅凭它是原解核心步骤就升级。"
    "已有专门应用主点时，一次简单前置计算是先备计算，不再附加基础公式/四则运算辅助点；"
    "若题目直接考查该运算，它仍是main。"
    "对“利用某性质求值/解题”的条目，须确认该性质已知或已证并实际被用作前提，"
    "不能将正在证明的性质反当成工具。"
    "只有最终答案、选项字母、孤立结论、图片或假设改题的另解说明不算可核对解析，"
    "不得据此猜auxiliary，也不现场补一种解法。\n"
)

_TARGET_EVIDENCE_RULE = (
    "【证据】每项含target_evidence：本题content中含待求/待证对象的逐字连续短句，"
    "一个非空字符串或1至12条不重复原句的数组，不拼接改写。"
    "main的evidence_source为content；auxiliary只能为answer_markdown或review_markdown，"
    "且evidence必须是该字段的真实关键步骤，target_evidence另锚定所服务的题干目标。"
    "选项可作判断目标，不能当解答步骤。优先复制短原句，数学符号、空白与反斜杠保持原文，"
    "JSON转义后仍须逐字相同。不引用目录、题头、其他题或旧标签。"
    "reason用一句短话（通常不超过60字）说明对象、任务及主辅，scope可记小问；"
    "输出前逐项检查全部小问、具体粒度与两类引文。\n"
)

_CONTRAST_EXAMPLES = (
    "【判定边界示例，仅说明规则，不预设本次目录内容】\n"
    "- 已给条件与待求目标不同：题干给出某个对象，不等于要求求该对象；"
    "应按设问的实际产物选择考点，不选只描述背景的条目。\n"
    "- 目标与方法不同：解析中用到某个公式或方法，不等于题目考查该公式本身；"
    "只有题目直接要求运用或判断它时才作为主目标。\n"
    "- 目标相近的条目按动作与适用范围区分：由性质反推参数与直接求取值、"
    "求单个量与列出完整结果是不同任务；出现相同背景词不足以等同。\n"
    "- 证明某个性质时，证明中的代入或中间计算不等于利用已成立的性质求值；"
    "另一问实际使用该性质，才可能是辅助。\n"
    "- 多小问题：每问独立目标分别覆盖；同一考点服务多个目标时合并引文，"
    "不重复添加宽泛背景点。\n"
)


def _semantic_boundaries():
    """Verified identity/name pairs, supplied by the editable teaching context."""
    rows = teaching_context.context().get("semantic_boundaries") or []
    if not isinstance(rows, list):
        raise ValueError("易混条目语义边界必须是数组")
    result = []
    for row in rows:
        if (not isinstance(row, Mapping) or not isinstance(row.get("code"), str)
                or not row["code"] or not isinstance(row.get("guidance"), str)
                or not row["guidance"] or not isinstance(row.get("names"), (list, tuple))
                or not row["names"] or any(not isinstance(name, str) or not name
                                           for name in row["names"])):
            raise ValueError("易混条目语义边界字段无效")
        result.append((row["code"], tuple(row["names"]), row["guidance"]))
    return tuple(result)


def _known_names(point: Mapping) -> set[str]:
    names = [point.get("name"), point.get("original_name")]
    aliases = point.get("aliases", ())
    if isinstance(aliases, (list, tuple)):
        names.extend(aliases)
    return {value.strip() for value in names if isinstance(value, str) and value.strip()}


def build_verified_boundary_notes(catalog: Iterable[Mapping] | None = None) -> str:
    """Return only scope notes whose identities exist in this snapshot."""
    installed = {
        point["code"]: point for point in (() if catalog is None else catalog)
        if isinstance(point, Mapping) and isinstance(point.get("code"), str)
    }
    notes = []
    for code, expected_names, guidance in _semantic_boundaries():
        point = installed.get(code)
        if point is None or not _known_names(point).intersection(expected_names):
            continue
        notes.append([code, guidance])
    if notes:
        return (
            "【当前目录中少数易混条目的语义边界】以下说明不新增点码、不改变名称，"
            "也不预先指定本题应选的条目；仍按原文目标判断：\n"
            + json.dumps(notes, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    return ""


def build_knowledge_annotation_guidance(catalog: Iterable[Mapping] | None = None) -> str:
    """Return target-first instructions, plus notes verified in this snapshot."""
    return (_TARGET_FIRST_RULE + _TARGET_EVIDENCE_RULE + _CONTRAST_EXAMPLES
            + build_verified_boundary_notes(catalog))
