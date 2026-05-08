"""Generate a sample analysis-model template Excel.

Run::

    python samples/generate_template.py

Produces ``samples/输出模板_情感分类.xlsx`` — upload this in the 『分析模型』
page (上传输出模板 Excel) to instantly get a ready-to-use 客服情感分析 model
that pairs with ``客户反馈_测试数据.xlsx``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kdv.excel.template import export_template_skeleton  # noqa: E402
from kdv.llm.schema import FieldSpec  # noqa: E402

OUT = Path(__file__).resolve().parent / "输出模板_情感分类.xlsx"

FIELDS = [
    FieldSpec(
        name="情感倾向",
        type="enum",
        enum_values=["正向", "中性", "负向"],
        description="客户反馈整体情感",
        example="正向",
        required=True,
    ),
    FieldSpec(
        name="主要问题点",
        type="string",
        description="如果是负向反馈，提取最关键的一个问题（10字以内）；正向写'无'",
        example="物流慢",
        required=True,
    ),
    FieldSpec(
        name="风险等级",
        type="enum",
        enum_values=["低", "中", "高"],
        description="基于情感+问题严重性，是否需要客服介入：质量/安全=高，态度=中，物流=低",
        example="中",
        required=True,
    ),
    FieldSpec(
        name="改进建议",
        type="string",
        description="一句话给运营/产品团队的可执行建议（≤30字）",
        example="增加包装防摔层",
        required=False,
    ),
    FieldSpec(
        name="是否需要回访",
        type="boolean",
        description="高风险或表达极度不满时返回 true，其他 false",
        example="false",
        required=True,
    ),
    FieldSpec(
        name="提取标签",
        type="array",
        description="0–3 个关键词标签，用、分隔",
        example="物流、包装",
        required=False,
    ),
]


def main() -> None:
    export_template_skeleton(OUT, FIELDS)
    print(f"已生成输出模板：{OUT}")
    print(f"  · 共 {len(FIELDS)} 个字段（含情感/风险/建议/回访标记/标签）")
    print("  · 在『分析模型』页点『上传输出模板 Excel』直接导入")


if __name__ == "__main__":
    main()
