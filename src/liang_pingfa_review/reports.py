"""Chinese-first, privacy-preserving human summaries for local artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def render_audit_report(audit: Mapping[str, Any]) -> str:
    """Render a cardinality-independent report without private CAD details."""

    has_actionable = any(
        bool(finding.get("actionability")) for finding in audit["findings"]
    )
    conclusion = (
        "已建立可操作的疑似不一致；如需修改，只能生成受控删除计划。"
        if has_actionable
        else "未建立可操作目标；结论保持只读并建议补充可读证据后重新审计。"
    )

    lines = [
        "# DWG 两阶段审计摘要",
        "",
        "## 审查范围",
        "",
        "本报告仅覆盖图纸表示与可读性；不包含结构计算、设计批准、施工指令或安全结论。",
        "",
        "## 审计结论",
        "",
        f"- 状态：{conclusion}",
        "- 分类：审计只区分可操作的疑似不一致与证据不足/只读结论，不逐项披露。",
        "- 下一步：由本地机器可读审计工件保留精确细节；人类摘要不重复这些记录。",
        "",
        "## 两阶段边界",
        "",
        "只有状态为 `疑似不一致` 且通过精确证据门的本地审计目标，才可能生成删除辅助覆盖文字的计划。",
        "本审计不直接修改 DWG，也不公开源图文字、坐标、路径、句柄、散列或私有元数据。",
        "",
    ]
    if audit.get("schema_version") == "liang-pingfa/audit/v2":
        lines.extend(
            [
                "## 可选梁图拓扑审计",
                "",
                "已执行本地只读的梁轴、显式支座、跨和原位注写语义位置审计。",
                "该分支不公开文字、坐标、图层、路径、颜色、句柄、原始散列或 token 单独散列；不报告数量。",
                "token 只在本机内存中比较；工件至多保留布尔的相等性门结果。",
                "无论其状态为何，拓扑发现永不授权编辑，也不会进入第二阶段目标。",
                "",
            ]
        )
    return "\n".join(lines)


def render_plan_review(_plan: Mapping[str, Any] | None = None) -> str:
    """Render a reviewable operation summary without target identifiers."""

    lines = [
        "# DWG 修改计划审阅",
        "",
        "## 受控操作",
        "",
        "本计划仅允许删除已审计的直接 Modelspace TEXT 辅助覆盖文字。",
        "不允许图层删除、文字替换、坐标移动、几何修改或任何设计参数修改。",
        "",
        "## 后置条件",
        "",
        "- 目标删除后必须不存在。",
        "- 不得新增实体。",
        "- 非目标实体、对应右侧区域、图层、布局和边界必须保持。",
        "- 必须经临时 DWG 往返和新鲜复审验证。",
    ]
    lines.extend(
        [
            "",
            "## 执行确认",
            "",
            "执行阶段必须从本地计划文件读取精确计划标识，并使用新的、尚不存在的输出 DWG 路径。",
            "确认不是绕过；源文件变化、计划变化或复审不一致都会失败关闭。",
            "",
        ]
    )
    return "\n".join(lines)
