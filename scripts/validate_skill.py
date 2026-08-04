"""Deterministic standard-library validation for this Skill repository."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Iterable, Sequence


SKILL_NAME = "liang-pingfa-tuzhi-shencha"
SKILL_DIRECTORY = Path(".github") / "skills" / SKILL_NAME
SKILL_PATH = SKILL_DIRECTORY / "SKILL.md"

REFERENCE_PATHS = (
    Path("references/workflow-output.md"),
    Path("references/notation-fields.md"),
    Path("references/source-scope.md"),
    Path("references/local-regression.md"),
    Path("references/dwg-two-stage-workflow.md"),
    Path("references/multi-annotation-overlap.md"),
    Path("references/beam-topology-audit.md"),
    Path("references/native-cad-bridge.md"),
)
MULTI_ANNOTATION_CONTRACT_PATH = Path("tests/contracts/multi-annotation-overlap.json")
TOPOLOGY_CONTRACT_PATH = Path("tests/contracts/beam-topology-in-situ.json")
NATIVE_PROTOCOL_CONTRACT_PATH = Path("tests/contracts/native-bridge-protocol.json")
NATIVE_SCHEMA_PATHS = (
    "native-adapter-config-v1.schema.json",
    "native-bridge-request-v1.schema.json",
    "native-bridge-response-v1.schema.json",
    "native-bridge-session-v1.schema.json",
    "native-geometry-export-v1.schema.json",
    "native-audit-v1.schema.json",
    "native-edit-intent-v1.schema.json",
    "native-edit-plan-v1.schema.json",
    "native-edit-manifest-v1.schema.json",
    "native-console-result-v1.schema.json",
    "native-console-export-v1.schema.json",
    "native-verification-v1.schema.json",
)
NATIVE_PROTOCOL_TEXT_PATHS = frozenset(
    {
        "src/liang_pingfa_review/native_bridge.py",
        "src/liang_pingfa_review/schemas/native-bridge-session-v1.schema.json",
        "tests/support/mock_native_bridge.py",
        "tests/support/synthetic_native.py",
        "tests/test_native_protocol.py",
    }
)
NATIVE_PIPE_LITERAL_CONTEXTS = NATIVE_PROTOCOL_TEXT_PATHS | frozenset(
    {
        ".github/skills/liang-pingfa-tuzhi-shencha/references/native-cad-bridge.md",
        "scripts/validate_skill.py",
    }
)
TOPOLOGY_ROLE_ARRAYS = (
    "beam_edges",
    "beam_ids",
    "column_supports",
    "wall_supports",
    "generic_supports",
    "support_upper_annotations",
    "span_lower_annotations",
    "leaders",
)
TOPOLOGY_REQUIRED_RULES = frozenset(
    {
        "direct-visible-opaque-coplanar-modelspace-only",
        "actual-overlap-gate-before-binding",
        "controlled-text-overlap-before-role-eligibility",
        "no-nearest-binding",
        "explicit-support-polygons-only",
        "different-id-collinear-beams-stay-separate",
        "support-upper-adjacent-zones-side-neutral",
        "bounded-chain-relations",
        "unpaired-controlled-geometry-gate",
        "single-relation-budget",
        "bounded-spatial-and-interval-indexes",
        "token-equality-in-memory-only",
        "trace-manifest-eligibility-and-owned-tuples",
        "topology-findings-non-actionable",
        "phase-two-overlay-only",
    }
)
TOPOLOGY_FORBIDDEN_PROFILE_CONTROLS = frozenset(
    {
        "tolerances",
        "regexes",
        "entity_types",
        "fallback_rules",
        "mutation",
        "arbitrary_code",
    }
)
TOPOLOGY_AUDIT_TRUST = {
    "self_integrity": "accidental-corruption-detection-only",
    "not_authenticated_against": "malicious-same-account-editor",
    "external_or_edited_audit_requirement": (
        "fresh-audit-topology-profile-against-bound-source-profile-before-reliance"
    ),
    "validate_artifact": (
        "schema-self-integrity-and-internal-links-only-not-geometric-truth"
    ),
}
TOPOLOGY_AUDIT_TRUST_PHRASES = (
    "自完整性 SHA-256 只用于检测意外损坏；它不能认证恶意同帐户编辑者重新签名的工件。",
    "任何外部提供、手工编辑或不受信任的 audit/v2 在依赖结论前，必须针对其绑定的源文件和 profile 重新运行全新的 audit --topology-profile。",
    "validate_artifact 只验证工件模式、规范自完整性和内部关联；没有源文件时，它不证明几何事实。",
)
UNRESOLVED_OVERLAP_P1_FAILURE = "failed-for-each-affected-cluster"
UNRESOLVED_OVERLAP_P2_BLOCKED = "blocked"
UNRESOLVED_OVERLAP_FORBIDDEN_BEHAVIORS = (
    "partial OCR pass",
    "nearest-distance binding",
    "candidate concatenation",
    "field concatenation",
    "field merge",
    "scope merge",
    "color-or-layer semantic proof",
)
ACTUAL_INTERSECTION_EVIDENCE_TYPES = frozenset(
    {
        "ink_mask_intersection",
        "glyph_intersection",
        "leader_intersection",
        "field_boundary_intersection",
        "vector_intersection",
    }
)
CANDIDATE_HINT_TYPES = frozenset({"color", "layer", "line_type"})
SEMANTIC_SCOPE_VALUES = frozenset(
    {"concentrated_annotation", "in_situ_annotation"}
)
READABLE_REQUIRED_SCOPES = frozenset(
    {"concentrated_annotation", "in_situ_annotation"}
)
READABLE_EVIDENCE_TYPES = {
    "readability_evidence": frozenset({"glyphs_or_symbols_readable"}),
    "boundary_evidence": frozenset({"field_boundaries_or_rows_clear"}),
    "binding_evidence": frozenset(
        {
            "ownership_binding_clear",
            "leader_binding_clear",
            "topology_binding_clear",
        }
    ),
    "scope_evidence": frozenset({"scope_kept_separate"}),
}
VISIBLE_CONFLICT_EVIDENCE_TYPE = "visible_expression_conflict"
READABLE_FORBIDDEN_BEHAVIORS = (
    "candidate concatenation",
    "field concatenation",
    "field merge",
    "scope merge",
)

ALLOWED_FILES = frozenset(
    {
        ".gitignore",
        "LICENSE",
        "README.md",
        "pyproject.toml",
        ".github/workflows/validate.yml",
        ".github/skills/liang-pingfa-tuzhi-shencha/SKILL.md",
        ".github/skills/liang-pingfa-tuzhi-shencha/references/dwg-two-stage-workflow.md",
        ".github/skills/liang-pingfa-tuzhi-shencha/references/workflow-output.md",
        ".github/skills/liang-pingfa-tuzhi-shencha/references/notation-fields.md",
        ".github/skills/liang-pingfa-tuzhi-shencha/references/source-scope.md",
        ".github/skills/liang-pingfa-tuzhi-shencha/references/local-regression.md",
        ".github/skills/liang-pingfa-tuzhi-shencha/references/multi-annotation-overlap.md",
        ".github/skills/liang-pingfa-tuzhi-shencha/references/beam-topology-audit.md",
        ".github/skills/liang-pingfa-tuzhi-shencha/references/native-cad-bridge.md",
        "scripts/validate_skill.py",
        "src/liang_pingfa_review/__init__.py",
        "src/liang_pingfa_review/__main__.py",
        "src/liang_pingfa_review/apply.py",
        "src/liang_pingfa_review/atomic_output.py",
        "src/liang_pingfa_review/audit.py",
        "src/liang_pingfa_review/canonical.py",
        "src/liang_pingfa_review/cli.py",
        "src/liang_pingfa_review/contracts.py",
        "src/liang_pingfa_review/errors.py",
        "src/liang_pingfa_review/local_regression.py",
        "src/liang_pingfa_review/native_protocol.py",
        "src/liang_pingfa_review/native_contracts.py",
        "src/liang_pingfa_review/native_bridge.py",
        "src/liang_pingfa_review/native_audit.py",
        "src/liang_pingfa_review/native_plan.py",
        "src/liang_pingfa_review/native_manifest.py",
        "src/liang_pingfa_review/core_console.py",
        "src/liang_pingfa_review/native_apply.py",
        "src/liang_pingfa_review/native_verify.py",
        "src/liang_pingfa_review/oda.py",
        "src/liang_pingfa_review/overlay_profile.py",
        "src/liang_pingfa_review/ownership.py",
        "src/liang_pingfa_review/plan.py",
        "src/liang_pingfa_review/raw_dxf.py",
        "src/liang_pingfa_review/reports.py",
        "src/liang_pingfa_review/snapshots.py",
        "src/liang_pingfa_review/temporary.py",
        "src/liang_pingfa_review/topology_ids.py",
        "src/liang_pingfa_review/topology_profile.py",
        "src/liang_pingfa_review/verify.py",
        "src/liang_pingfa_review/schemas/__init__.py",
        "src/liang_pingfa_review/schemas/audit-v1.schema.json",
        "src/liang_pingfa_review/schemas/audit-v2.schema.json",
        "src/liang_pingfa_review/schemas/beam-topology-profile-v1.schema.json",
        "src/liang_pingfa_review/schemas/edit-plan-v1.schema.json",
        "src/liang_pingfa_review/schemas/verification-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-adapter-config-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-bridge-request-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-bridge-response-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-bridge-session-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-geometry-export-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-audit-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-edit-intent-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-edit-plan-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-edit-manifest-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-console-result-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-console-export-v1.schema.json",
        "src/liang_pingfa_review/schemas/native-verification-v1.schema.json",
        "tests/support/__init__.py",
        "tests/support/owned_files.py",
        "tests/support/synthetic_dxf.py",
        "tests/support/synthetic_native.py",
        "tests/support/mock_native_bridge.py",
        "tests/support/mock_core_console.py",
        "tests/test_apply_verify.py",
        "tests/test_audit_plan.py",
        "tests/test_canonical_contracts.py",
        "tests/test_handle_ownership.py",
        "tests/test_oda_cli.py",
        "tests/test_source_binding.py",
        "tests/test_validate_skill.py",
        "tests/test_topology_profile.py",
        "tests/test_native_protocol.py",
        "tests/test_native_contracts.py",
        "tests/test_native_audit_plan.py",
        "tests/test_native_apply_verify.py",
        "tests/test_native_core_console.py",
        "tests/test_native_cli.py",
        "tests/test_native_publication_transaction.py",
        "tests/test_native_real_integration.py",
        "tests/contracts/local-representation-readability.json",
        "tests/contracts/two-stage-overlay-workflow.json",
        "tests/contracts/multi-annotation-overlap.json",
        "tests/contracts/beam-topology-in-situ.json",
        "tests/contracts/native-bridge-protocol.json",
        "tests/local-fixtures/README.md",
        "native-bridge-contracts/LiangPingfa.NativeBridge.Contracts.csproj",
        "native-bridge-contracts/ProtocolV1.cs",
        "native-bridge-contracts/Interfaces.cs",
        "native-bridge-contracts/README.md",
        "native-bridge-contracts/LICENSE",
    }
)

IGNORED_DIRECTORIES = frozenset({".git", "__pycache__"})
ROOT_GENERATED_DIRECTORIES = frozenset({"build", "dist"})
# These are the only generated ``bin``/``obj`` trees accepted in a working
# tree.  The contracts project is built in CI, so its normal SDK output must
# not make a subsequent full-tree validation fail.  Keep this path-exact:
# arbitrary nested build output remains subject to the strict allowlist.
CSHARP_GENERATED_DIRECTORIES = frozenset(
    {
        "native-bridge-contracts/bin",
        "native-bridge-contracts/obj",
    }
)
FORBIDDEN_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".dwg",
        ".dxf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
        ".ocr",
        ".hocr",
        ".alto",
        ".djvu",
        ".zip",
        ".7z",
        ".rar",
        ".tar",
        ".tar.gz",
        ".tgz",
        ".gz",
        ".bz2",
        ".tbz",
        ".tbz2",
        ".tar.bz2",
        ".xz",
        ".txz",
        ".tar.xz",
        ".zst",
        ".tar.zst",
        ".cab",
        ".iso",
        ".bin",
        ".exe",
        ".dll",
        ".whl",
    }
)
TEXT_FILE_SUFFIXES = frozenset(
    {".md", ".py", ".yml", ".yaml", ".json", ".toml", ".cs", ".csproj"}
)

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
UNC_PATH_PATTERN = re.compile(r"(?<!\x5c)\x5c\x5c[^\x5c/\s]+[\x5c/]")
_GENERIC_NATIVE_PIPE_PREFIX = (
    chr(92) * 2 + "." + chr(92) + "pipe" + chr(92) + "liang-pingfa-native-"
)
GENERIC_NATIVE_PIPE_LITERAL_PATTERN = re.compile(
    re.escape(_GENERIC_NATIVE_PIPE_PREFIX)
    + r"(?:<[A-Za-z0-9_-]{1,96}>|[A-Za-z0-9_-]{16,128})"
    + r"(?=$|[\s,;:'\"`)\]}>])"
)
GENERIC_NATIVE_PIPE_GRAMMAR_PATTERN = re.compile(
    r"\\\\pipe\\\\liang-pingfa-native-\[A-Za-z0-9_-\]\{16,128\}"
)
_GENERIC_NATIVE_PIPE_GRAMMAR_PREFIX = "^" + chr(92) * 5 + "."
POSIX_LOCAL_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._-])/(?:Users|home|private|mnt|var|tmp|opt|root)(?:/|$)",
    re.IGNORECASE,
)
SHA256_PATTERN = re.compile(r"\b[a-fA-F0-9]{64}\b")
MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")

REQUIRED_HEADINGS = (
    "## 使命与结论边界",
    "## 适用场景与触发条件",
    "## 资源加载",
    "## 输入与前置条件",
    "## 审查工作流",
    "## 集中标注、原位标注与局部覆盖",
    "## 固定输出格式",
    "## 安全边界与升级",
    "## 两阶段 DWG 工作流",
)
REQUIRED_PHRASES = (
    "阅读梁平法注写",
    "解释集中标注与原位标注",
    "疑似不一致",
    "比较修改前后",
    "截图、PDF 页面图像或导出的 CAD 图像",
    "原始 DWG",
    "环境相关",
    "图像可读性检查",
    "拓扑/位置绑定",
    "集中标注",
    "原位标注",
    "局部覆盖",
    "一致",
    "证据不足",
    "对象/位置",
    "字段",
    "可见证据",
    "推理",
    "来源页题",
    "不可读部分",
    "下一步",
    "不得臆造",
    "结构计算",
    "设计批准",
    "施工指令",
    "结构专业人员",
    "两阶段 DWG 工作流",
    "beam-plan-in-situ/v1",
    "不得按最近梁、支座或跨绑定",
    "拓扑发现永不授权编辑",
)
REQUIRED_GITIGNORE_RULES = (
    "tmp/",
    "output/",
    "*.pdf",
    "*.dwg",
    "*.dxf",
    "*.png",
    "/tests/local-fixtures/*",
    "!/tests/local-fixtures/README.md",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".hypothesis/",
    ".coverage",
    "audit.json",
    "edit-plan.json",
    "verification.json",
    "audit.md",
    "audit-v2.json",
    "audit-v2.md",
    "beam-topology-profile.json",
    "plan-review.md",
    ".liang-pingfa-oda/",
    "/build/",
    "/dist/",
    "*.egg-info/",
    ".vscode/",
    ".DS_Store",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.gz",
    "*.bz2",
    "*.tbz",
    "*.tbz2",
    "*.tar.bz2",
    "*.xz",
    "*.txz",
    "*.tar.xz",
    "*.zst",
    "*.tar.zst",
    "*.cab",
    "*.iso",
    ".liang-pingfa-native/",
    "/native-session*.json",
    "/native-export*.json",
    "/native-intent*.json",
    "/native-manifest*.json",
    "/native-console-result*.json",
    "/native-console-export*.json",
    "/native-verification*.json",
    "/native-plan*.json",
    "/native-audit*.json",
    "/native-*.log",
    "*.native-output.dwg",
    "/native-bridge-contracts/bin/",
    "/native-bridge-contracts/obj/",
)
SOURCE_SCOPE_SENTENCE = "Verified source scope: 22G101-1 printed pages 1-22 through 1-33."
BOUNDED_THREAT_MODEL = (
    "trusted Windows account/session, ODA executable, OS, and local NTFS volume; "
    "no hostile same-account/admin process"
)
NATIVE_PRIVATE_ARTIFACT_PRIVACY_PHRASES = (
    "PRIVATE-ARTIFACT-PRIVACY:",
    "retained no-follow",
    "owner/DACL validation is required",
    "never commit or upload",
    "nonce/challenge, and process/document bindings.",
    "raw text, coordinates, layers, paths,",
    "Manifests contain raw preconditions/geometry and plugin,",
    "Intent can contain requested deltas and",
    "Console results/logs are sensitive and bounded.",
    "audit/plan/verification/recovery JSON is private redacted-or-opaque machine data",
    "Only public Markdown reports, CLI error events, and CI logs are redacted and",
)
NATIVE_PRIVATE_ARTIFACT_FALSE_REDACTION_PHRASES = (
    "private artifacts contain no raw data",
    "raw private artifacts are publicly redacted",
)
SUPPORT_BOUNDARY_PHRASES = {
    "README.md": (
        "R2018/AC1032 DXF-exposable",
        "UNSAFE_ENTITY_TYPE",
        "File Converter 只是文件转换，不是原生数据库编辑",
        "ODA Drawings SDK",
        "Autodesk RealDWG/AutoCAD",
        "object enablers",
        "私有资格夹具不会发布",
        "per_file_compatibility",
        "audit_required",
    ),
    "dwg workflow reference": (
        "R2018/AC1032 DXF-exposable",
        "代理实体或自定义实体/对象",
        "代理图形",
        "非空或不受支持的 `ACDSDATA`",
        "未建模的原始标签或节",
        "SORTENTSTABLE",
        "对象启用器",
        "File Converter 不是原生数据库编辑",
        "ODA Drawings SDK",
        "Autodesk RealDWG/AutoCAD",
        "object enablers",
        "私有资格夹具不会发布",
    ),
    "SKILL.md": (
        "R2018/AC1032 DXF-exposable",
        "不得绕过这些兼容性门",
        "File Converter 不是原生数据库编辑",
        "ODA Drawings SDK",
        "Autodesk RealDWG/AutoCAD",
        "object enablers",
    ),
    "local regression reference": (
        "安全的兼容性结果",
        "不要剥离代理/自定义状态来强迫",
    ),
}
MULTI_ANNOTATION_DOCUMENT_PHRASES = {
    "SKILL.md": (
        "重叠簇门在 P1 之前",
        "单纯文字接近不能合并簇或证明重叠",
        "颜色、图层和线型只能提示不同候选",
        "不得让部分 OCR 通过 P1",
        "P1 未通过时禁止 P2",
        "绝不拼接字符串或合并字段",
        "区域摘要不得为 `一致`",
    ),
    "workflow-output.md": (
        "重叠簇（P1 前）",
        "每个受影响候选分别输出 `证据不足`",
        "禁止 P2",
        "文字接近本身不能合并候选或证明重叠",
        "未解决的重叠区域摘要不得填写 `一致`",
    ),
    "notation-fields.md": (
        "不得串接可见字符串、合并字段",
        "用颜色/图层/线型确定语义或所有权",
        "逐候选标为 `证据不足` 并阻断绑定",
    ),
    "README.md": (
        "先逐簇完成重叠门",
        "文字接近不能合并候选",
        "未解决重叠区域不得报告 `一致`",
        "仅当每个候选独立可读",
    ),
    "multi-annotation-overlap.md": (
        "两个及以上",
        "单纯文字接近",
        "每个受影响候选分别创建一项 `证据不足`",
        "部分 OCR 的可读片段不得让该簇通过 P1",
        "不得按最近文字/对象距离强制绑定",
        "区域摘要不得为 `一致`",
        "全部候选簇的行和列",
    ),
}


class ValidationError(Exception):
    """Raised when one or more repository validation checks fail."""

    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("\n".join(self.issues))


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _has_forbidden_extension(path: Path) -> bool:
    return any(path.name.lower().endswith(extension) for extension in FORBIDDEN_EXTENSIONS)


def _read_text(path: Path, issues: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        issues.append(f"cannot read UTF-8 text file {path}: {error}")
        return None


def _iter_repository_files(root: Path, issues: list[str]) -> Iterable[Path]:
    if root.is_symlink():
        issues.append(f"repository root must not be a symlink: {root}")
        return

    for directory_text, directory_names, file_names in os.walk(root, followlinks=False):
        directory = Path(directory_text)
        retained_directories: list[str] = []
        for directory_name in sorted(directory_names):
            child = directory / directory_name
            relative = _relative_posix(child, root)
            if child.is_symlink():
                issues.append(f"symlink is not allowed: {relative}")
                continue
            if directory_name in IGNORED_DIRECTORIES:
                continue
            if directory == root and directory_name in ROOT_GENERATED_DIRECTORIES:
                continue
            if relative in CSHARP_GENERATED_DIRECTORIES:
                continue
            if directory_name.endswith(".egg-info"):
                continue
            retained_directories.append(directory_name)
        directory_names[:] = retained_directories

        for file_name in sorted(file_names):
            path = directory / file_name
            relative = _relative_posix(path, root)
            if path.is_symlink():
                issues.append(f"symlink is not allowed: {relative}")
                continue
            yield path


def _validate_repository_policy(root: Path, files: Iterable[Path], issues: list[str]) -> list[Path]:
    collected = sorted(files, key=lambda path: _relative_posix(path, root))
    for path in collected:
        relative = _relative_posix(path, root)
        if _has_forbidden_extension(path):
            issues.append(f"forbidden artifact extension: {relative}")
        if relative not in ALLOWED_FILES:
            issues.append(f"path is not allowed by repository policy: {relative}")
    return collected


def _parse_front_matter(text: str, issues: list[str]) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        issues.append("SKILL.md must begin with an opening front matter delimiter")
        return None

    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            closing_index = index
            break
    if closing_index is None:
        issues.append("SKILL.md is missing a closing front matter delimiter")
        return None

    fields: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:closing_index], start=2):
        if not line.strip():
            continue
        if ":" not in line:
            issues.append(f"invalid front matter line {line_number}: {line}")
            continue
        key, value = line.split(":", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            issues.append(f"front matter key and value are required on line {line_number}")
            continue
        if key in fields:
            issues.append(f"duplicate front matter field: {key}")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        fields[key] = value
    return fields


def _validate_front_matter(root: Path, skill_text: str, issues: list[str]) -> None:
    fields = _parse_front_matter(skill_text, issues)
    if fields is None:
        return

    name = fields.get("name")
    description = fields.get("description")
    if name is None:
        issues.append("front matter is missing required field: name")
    else:
        if not 1 <= len(name) <= 64:
            issues.append("skill name must be 1-64 characters long")
        if NAME_PATTERN.fullmatch(name) is None:
            issues.append(f"invalid skill name: {name}")
        if name != SKILL_NAME:
            issues.append(
                f"front matter name must equal canonical directory name: {SKILL_NAME}"
            )

    if description is None:
        issues.append("front matter is missing required field: description")
    elif not 1 <= len(description) <= 1024:
        issues.append("description must be nonempty and at most 1024 characters")

    skill_directory_name = (root / SKILL_PATH).parent.name
    if name is not None and name != skill_directory_name:
        issues.append("front matter name must equal its Skill directory name")


def _validate_skill_content(skill_text: str, issues: list[str]) -> None:
    for heading in REQUIRED_HEADINGS:
        if heading not in skill_text:
            issues.append(f"missing required Skill heading: {heading}")
    for phrase in REQUIRED_PHRASES:
        if phrase not in skill_text:
            issues.append(f"missing required Skill phrase: {phrase}")
    if len(skill_text.splitlines()) >= 500:
        issues.append("SKILL.md must stay below 500 lines")


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_FILE_SUFFIXES or path.name in {"LICENSE", ".gitignore"}


def _unc_span_is_allowed(
    value: str,
    start: int,
    end: int,
    *,
    relative: str,
    path: Path,
) -> bool:
    """Allow only one complete documented project pipe span at ``start``."""

    literal_match = GENERIC_NATIVE_PIPE_LITERAL_PATTERN.match(value, start)
    # Regex source encodes an exact project pipe grammar rather than a live
    # path. It remains allowlisted after Python decoding as well, but only
    # with the exact grammar prefix and in an explicit protocol context.
    grammar_match = GENERIC_NATIVE_PIPE_GRAMMAR_PATTERN.match(value, start)
    return relative in NATIVE_PIPE_LITERAL_CONTEXTS and (
        (
            literal_match is not None
            and literal_match.end() >= end
        )
        or (
            path.suffix.lower() in {".py", ".json"}
            and grammar_match is not None
            and grammar_match.end() >= end
            and value[:start].endswith(_GENERIC_NATIVE_PIPE_GRAMMAR_PREFIX)
        )
    )


def _text_safety_issue(
    value: str,
    *,
    relative: str,
    path: Path,
) -> str | None:
    """Return the first path/hash policy violation in a text value."""

    if WINDOWS_ABSOLUTE_PATH_PATTERN.search(value):
        return f"obvious Windows absolute local path found in {relative}"
    for unc_match in UNC_PATH_PATTERN.finditer(value):
        if not _unc_span_is_allowed(
            value,
            unc_match.start(),
            unc_match.end(),
            relative=relative,
            path=path,
        ):
            return f"UNC local path found in {relative}"
    if POSIX_LOCAL_PATH_PATTERN.search(value):
        return f"obvious POSIX local path found in {relative}"
    if SHA256_PATTERN.search(value):
        return f"possible source hash found in {relative}"
    return None


def _literal_text(value: object) -> str | None:
    """Decode a Python literal value without evaluating any source expression."""

    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        # Latin-1 is total and preserves every byte's code point, including
        # escaped ``\x5c`` backslashes, without guessing an executable codec.
        return value.decode("latin-1")
    return None


def _static_python_expression_text(node: ast.AST) -> str | None:
    """Return a safely decoded static string/bytes expression, if exact."""

    if isinstance(node, ast.Constant):
        return _literal_text(node.value)
    if isinstance(node, ast.JoinedStr):
        values: list[str] = []
        for item in node.values:
            if not isinstance(item, ast.Constant):
                return None
            text = _literal_text(item.value)
            if text is None:
                return None
            values.append(text)
        return "".join(values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_python_expression_text(node.left)
        right = _static_python_expression_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _python_literal_values(
    text: str,
    *,
    relative: str,
    issues: list[str],
) -> list[str]:
    """Tokenize and AST-decode Python literals without executing source.

    Adjacent literals and dynamic f-string/addition expressions are handled
    conservatively: when their literal portions can form a doubled
    backslash, the repository is rejected instead of trying to model runtime
    interpolation or concatenation.
    """

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        tree = ast.parse(text, filename=relative, mode="exec")
    except (SyntaxError, tokenize.TokenError, IndentationError):
        # A malformed Python source cannot be safely decoded. Raw scanning
        # still runs, but an encoded backslash syntax fails closed here.
        if chr(92) in text:
            issues.append(f"Python literals cannot be safely decoded in {relative}")
        return []

    values: list[str] = []
    unsafe_composition = False
    slash_pair = chr(92) * 2

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            value = _literal_text(node.value)
            if value is not None:
                values.append(value)
        elif isinstance(node, ast.JoinedStr):
            static = _static_python_expression_text(node)
            if static is not None:
                values.append(static)
            else:
                literal_parts = [
                    value
                    for item in node.values
                    if isinstance(item, ast.Constant)
                    and (value := _literal_text(item.value)) is not None
                ]
                if slash_pair in "".join(literal_parts):
                    unsafe_composition = True
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            static = _static_python_expression_text(node)
            if static is not None:
                values.append(static)
            else:
                literal_parts = [
                    value
                    for item in ast.walk(node)
                    if isinstance(item, ast.Constant)
                    and (value := _literal_text(item.value)) is not None
                ]
                if slash_pair in "".join(literal_parts):
                    unsafe_composition = True

    index = 0
    while index < len(tokens):
        if tokens[index].type != tokenize.STRING:
            index += 1
            continue
        group = [tokens[index]]
        cursor = index + 1
        while cursor < len(tokens):
            token = tokens[cursor]
            if token.type in {tokenize.NL, tokenize.COMMENT}:
                cursor += 1
                continue
            if token.type == tokenize.STRING:
                group.append(token)
                cursor += 1
                continue
            break
        if len(group) > 1:
            decoded: list[str] = []
            for token in group:
                try:
                    value = _literal_text(ast.literal_eval(token.string))
                except (SyntaxError, ValueError):
                    value = None
                if value is not None:
                    decoded.append(value)
            if slash_pair in "".join(decoded):
                unsafe_composition = True
        index += 1

    if unsafe_composition:
        issues.append(
            f"dynamic or concatenated Python literal with backslashes found in {relative}"
        )
    return values


def _validate_text_safety(root: Path, files: Iterable[Path], issues: list[str]) -> None:
    for path in files:
        if not _is_text_file(path):
            continue
        text = _read_text(path, issues)
        if text is None:
            continue
        relative = _relative_posix(path, root)
        if path.suffix.lower() == ".py":
            # Python source has escape syntax. Its literals are checked only
            # after tokenize/AST decoding so a normal escaped pipe spelling is
            # neither missed nor mistaken for a second raw UNC span. Raw text
            # scanning remains authoritative for documentation, JSON, YAML,
            # C#, and other non-Python repository text.
            decoded_issue = next(
                (
                    issue
                    for value in _python_literal_values(
                        text,
                        relative=relative,
                        issues=issues,
                    )
                    if (
                        issue := _text_safety_issue(
                            value,
                            relative=relative,
                            path=path,
                        )
                    )
                    is not None
                ),
                None,
            )
            if decoded_issue is not None:
                issues.append(decoded_issue)
            continue
        values = [text]
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                values = []

                def collect_strings(value: object) -> None:
                    if isinstance(value, str):
                        values.append(value)
                    elif isinstance(value, list):
                        for item in value:
                            collect_strings(item)
                    elif isinstance(value, dict):
                        for key, item in value.items():
                            collect_strings(key)
                            collect_strings(item)

                collect_strings(parsed)
        raw_issue = next(
            (
                issue
                for value in values
                if (
                    issue := _text_safety_issue(
                        value,
                        relative=relative,
                        path=path,
                    )
                )
                is not None
            ),
            None,
        )
        if raw_issue is not None:
            issues.append(raw_issue)
            continue


def _validate_markdown_links(skill_directory: Path, issues: list[str]) -> None:
    skill_root = skill_directory.resolve()
    for document in sorted(skill_directory.rglob("*.md")):
        text = _read_text(document, issues)
        if text is None:
            continue
        for raw_target in MARKDOWN_LINK_PATTERN.findall(text):
            target = raw_target.strip()
            relative_document = document.relative_to(skill_directory).as_posix()
            if target.startswith("#"):
                continue
            if (
                "://" in target
                or target.startswith("/")
                or target.startswith("\\")
                or target.startswith("~")
                or re.match(r"^[A-Za-z]:[\\/]", target) is not None
            ):
                issues.append(f"resource link must be local and relative in {relative_document}: {target}")
                continue
            path_target = target.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
            if not path_target:
                continue
            if "\\" in path_target or ".." in path_target.replace("\\", "/").split("/"):
                issues.append(f"resource link escapes local Skill resources in {relative_document}: {target}")
                continue
            candidate = (document.parent / path_target).resolve()
            if not candidate.is_relative_to(skill_root):
                issues.append(f"resource link escapes Skill directory in {relative_document}: {target}")
                continue
            if not candidate.is_file():
                issues.append(f"missing referenced resource in {relative_document}: {target}")


def _validate_resources(root: Path, skill_text: str, issues: list[str]) -> None:
    skill_directory = root / SKILL_DIRECTORY
    for relative_reference in REFERENCE_PATHS:
        reference = skill_directory / relative_reference
        if not reference.is_file():
            issues.append(f"missing required reference file: {relative_reference.as_posix()}")
        markdown_link = f"]({relative_reference.as_posix()})"
        if markdown_link not in skill_text:
            issues.append(f"SKILL.md does not link required reference: {relative_reference.as_posix()}")
    _validate_markdown_links(skill_directory, issues)


def _validate_candidate_clusters(
    scenario: dict[str, object],
    issues: list[str],
    *,
    require_distinct_readable_scopes: bool = False,
) -> set[str] | None:
    """Validate independent candidates, scopes, and non-semantic visual hints."""

    candidates = scenario.get("candidate_clusters")
    if not isinstance(candidates, list) or len(candidates) < 2:
        issues.append(
            "multi-annotation contract must model at least two independent candidates"
        )
        return None

    candidate_ids: list[str] = []
    candidate_scopes: list[str] = []
    valid = True
    for candidate in candidates:
        if (
            not isinstance(candidate, dict)
            or set(candidate) != {"id", "scope", "candidate_hints"}
            or not isinstance(candidate.get("id"), str)
            or not candidate["id"].strip()
            or not isinstance(candidate.get("scope"), str)
            or not candidate["scope"].strip()
        ):
            valid = False
            continue

        candidate_ids.append(candidate["id"])
        scope = candidate["scope"]
        if scope not in SEMANTIC_SCOPE_VALUES:
            issues.append(
                "multi-annotation contract candidate scope must be exactly one "
                "allowlisted semantic scope: concentrated_annotation or "
                "in_situ_annotation"
            )
            valid = False
        else:
            candidate_scopes.append(scope)
        hints = candidate["candidate_hints"]
        if not isinstance(hints, list):
            valid = False
            continue
        for hint in hints:
            if (
                not isinstance(hint, dict)
                or set(hint) != {"type", "value", "non_semantic"}
                or hint.get("type") not in CANDIDATE_HINT_TYPES
                or not isinstance(hint.get("value"), str)
                or not hint["value"].strip()
                or hint.get("non_semantic") is not True
            ):
                valid = False

    if not valid:
        issues.append(
            "multi-annotation contract candidates must use separate scope and "
            "structured non-semantic candidate_hints"
        )
        return None
    if len(set(candidate_ids)) != len(candidate_ids):
        issues.append("multi-annotation contract candidate IDs must be nonempty and unique")
        return None
    if require_distinct_readable_scopes:
        if len(set(candidate_scopes)) != len(candidate_scopes):
            issues.append(
                "multi-annotation contract readable scenario must not contain "
                "duplicate same-scope candidates"
            )
        missing_scopes = sorted(READABLE_REQUIRED_SCOPES - set(candidate_scopes))
        if missing_scopes:
            issues.append(
                "multi-annotation contract readable scenario is missing required "
                f"scope role: {', '.join(missing_scopes)}"
            )
    return set(candidate_ids)


def _validate_actual_intersection_evidence(
    evidence: object, candidate_ids: set[str] | None, issues: list[str]
) -> None:
    """Require typed visual/vector intersections, never heuristic hints."""

    if not isinstance(evidence, list) or not evidence:
        issues.append(
            "multi-annotation contract overlap_evidence must be a nonempty list "
            "of structured actual intersection evidence"
        )
        return

    valid = candidate_ids is not None
    for item in evidence:
        if (
            not isinstance(item, dict)
            or set(item) != {"type", "candidate_ids"}
            or item.get("type") not in ACTUAL_INTERSECTION_EVIDENCE_TYPES
            or not isinstance(item.get("candidate_ids"), list)
        ):
            valid = False
            continue
        item_ids = item["candidate_ids"]
        if (
            not all(isinstance(candidate_id, str) and candidate_id.strip() for candidate_id in item_ids)
            or len(item_ids) != len(set(item_ids))
            or candidate_ids is None
            or set(item_ids) != candidate_ids
        ):
            valid = False
    if not valid:
        issues.append(
            "multi-annotation contract overlap_evidence must use allowlisted "
            "actual intersection types and exactly identify affected candidates"
        )


def _validate_readable_candidate_ids(
    readable: dict[str, object], candidate_ids: set[str] | None, issues: list[str]
) -> set[str] | None:
    """Require a unique, complete readable candidate set."""

    readable_ids = readable.get("readable_candidate_ids")
    if not isinstance(readable_ids, list) or not all(
        isinstance(candidate_id, str) and candidate_id.strip()
        for candidate_id in readable_ids
    ):
        issues.append(
            "multi-annotation contract readable_candidate_ids must list at least "
            "two nonempty candidate IDs"
        )
        return None

    too_few_ids = len(readable_ids) < 2
    if too_few_ids:
        issues.append(
            "multi-annotation contract readable_candidate_ids must list at least "
            "two nonempty candidate IDs"
        )
    duplicate_ids = sorted(
        candidate_id
        for candidate_id in set(readable_ids)
        if readable_ids.count(candidate_id) > 1
    )
    for candidate_id in duplicate_ids:
        issues.append(
            "multi-annotation contract readable_candidate_ids contain duplicate "
            f"candidate ID: {candidate_id}"
        )
    readable_id_set = set(readable_ids)
    if candidate_ids is not None:
        for candidate_id in sorted(readable_id_set - candidate_ids):
            issues.append(
                "multi-annotation contract readable candidate ID is not a "
                f"candidate cluster: {candidate_id}"
            )
        missing_ids = sorted(candidate_ids - readable_id_set)
        if missing_ids:
            issues.append(
                "multi-annotation contract readable_candidate_ids must cover every "
                f"candidate; missing: {', '.join(missing_ids)}"
            )
    if (
        too_few_ids
        or duplicate_ids
        or candidate_ids is None
        or readable_id_set != candidate_ids
    ):
        return None
    return readable_id_set


def _validate_readable_evidence(
    readable: dict[str, object], readable_ids: set[str] | None, issues: list[str]
) -> None:
    """Require candidate-local readability, boundary, binding, and scope proof."""

    evidence_rows = readable.get("candidate_evidence")
    if not isinstance(evidence_rows, list):
        issues.append(
            "multi-annotation contract readable scenario must contain "
            "candidate-specific evidence"
        )
        return

    row_ids: list[str] = []
    rows_by_id: dict[str, dict[str, object]] = {}
    for row in evidence_rows:
        if not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str):
            issues.append(
                "multi-annotation contract readable evidence must use nonempty "
                "candidate IDs"
            )
            continue
        candidate_id = row["candidate_id"]
        if not candidate_id.strip():
            issues.append(
                "multi-annotation contract readable evidence must use nonempty "
                "candidate IDs"
            )
            continue
        row_ids.append(candidate_id)
        rows_by_id[candidate_id] = row

    duplicate_ids = sorted(
        candidate_id for candidate_id in set(row_ids) if row_ids.count(candidate_id) > 1
    )
    for candidate_id in duplicate_ids:
        issues.append(
            "multi-annotation contract readable evidence contains duplicate "
            f"candidate ID: {candidate_id}"
        )

    if readable_ids is None:
        return
    for candidate_id in sorted(set(row_ids) - readable_ids):
        issues.append(
            "multi-annotation contract readable evidence candidate ID is not "
            f"readable: {candidate_id}"
        )
    missing_ids = sorted(readable_ids - set(row_ids))
    if missing_ids:
        issues.append(
            "multi-annotation contract readable evidence must cover every readable "
            f"candidate; missing: {', '.join(missing_ids)}"
        )

    required_keys = {"candidate_id", *READABLE_EVIDENCE_TYPES}
    for candidate_id in sorted(readable_ids & set(rows_by_id)):
        row = rows_by_id[candidate_id]
        if set(row) != required_keys:
            issues.append(
                "multi-annotation contract readable evidence must contain only "
                "candidate-specific readability, boundary, binding, and scope fields"
            )
        for field_name, allowed_types in READABLE_EVIDENCE_TYPES.items():
            values = row.get(field_name)
            if not isinstance(values, list) or not values or any(
                not isinstance(value, dict)
                or set(value) != {"type"}
                or value.get("type") not in allowed_types
                for value in values
            ):
                issues.append(
                    "multi-annotation contract readable evidence requires "
                    f"allowlisted {field_name} for: {candidate_id}"
                )


def _validate_visible_expression_conflict(
    readable: dict[str, object], readable_ids: set[str] | None, issues: list[str]
) -> None:
    """Require a separate, typed conflict visible across the readable candidates."""

    evidence = readable.get("visible_expression_conflict_evidence")
    if not isinstance(evidence, list) or not evidence:
        issues.append(
            "multi-annotation contract readable scenario requires separate visible "
            "expression conflict evidence"
        )
        return
    valid = readable_ids is not None
    for item in evidence:
        if (
            not isinstance(item, dict)
            or set(item) != {"type", "candidate_ids"}
            or item.get("type") != VISIBLE_CONFLICT_EVIDENCE_TYPE
            or not isinstance(item.get("candidate_ids"), list)
            or readable_ids is None
            or set(item["candidate_ids"]) != readable_ids
            or len(item["candidate_ids"]) != len(set(item["candidate_ids"]))
        ):
            valid = False
    if not valid:
        issues.append(
            "multi-annotation contract visible expression conflict evidence must "
            "be allowlisted and identify every readable candidate"
        )


def _validate_multi_annotation_overlap_contract(root: Path, issues: list[str]) -> None:
    """Require the fail-closed text-only multi-cluster overlap regression."""

    documents = (
        ("SKILL.md", root / SKILL_PATH),
        (
            "workflow-output.md",
            root / SKILL_DIRECTORY / "references" / "workflow-output.md",
        ),
        (
            "notation-fields.md",
            root / SKILL_DIRECTORY / "references" / "notation-fields.md",
        ),
        ("README.md", root / "README.md"),
        (
            "multi-annotation-overlap.md",
            root / SKILL_DIRECTORY / "references" / "multi-annotation-overlap.md",
        ),
    )
    for name, path in documents:
        text = _read_text(path, issues)
        if text is None:
            continue
        for phrase in MULTI_ANNOTATION_DOCUMENT_PHRASES[name]:
            if phrase not in text:
                issues.append(
                    f"{name} is missing required multi-annotation wording: {phrase}"
                )

    contract_path = root / MULTI_ANNOTATION_CONTRACT_PATH
    contract_text = _read_text(contract_path, issues)
    if contract_text is None:
        issues.append(
            "missing required multi-annotation contract: "
            f"{MULTI_ANNOTATION_CONTRACT_PATH.as_posix()}"
        )
        return
    try:
        contract = json.loads(contract_text)
    except json.JSONDecodeError as error:
        issues.append(f"invalid multi-annotation contract JSON: {error.msg}")
        return
    if not isinstance(contract, dict):
        issues.append("multi-annotation contract must be a JSON object")
        return

    scenarios = contract.get("scenarios")
    if contract.get("case_id") != "multi-annotation-overlap" or not isinstance(scenarios, list):
        issues.append("multi-annotation contract must identify its scenarios")
        return
    by_id = {
        scenario.get("scenario_id"): scenario
        for scenario in scenarios
        if isinstance(scenario, dict)
    }
    unresolved = by_id.get("unresolved-overlap")
    readable = by_id.get("readable-independent-conflict")
    if not isinstance(unresolved, dict) or not isinstance(readable, dict):
        issues.append("multi-annotation contract must contain unresolved and readable scenarios")
        return

    expected = unresolved.get("expected")
    affected_candidate_ids = _validate_candidate_clusters(unresolved, issues)
    _validate_actual_intersection_evidence(
        unresolved.get("overlap_evidence"), affected_candidate_ids, issues
    )
    if not isinstance(unresolved.get("unresolved"), list) or not unresolved["unresolved"]:
        issues.append("multi-annotation contract must record unresolved evidence")

    if not isinstance(expected, dict):
        issues.append("multi-annotation contract unresolved scenario must define expected results")
        return

    findings = expected.get("findings")
    if not isinstance(findings, list):
        issues.append(
            "multi-annotation contract findings must contain one entry for every affected candidate"
        )
    elif affected_candidate_ids is not None:
        finding_ids: list[str] = []
        for finding in findings:
            if not isinstance(finding, dict) or not isinstance(
                finding.get("candidate_id"), str
            ) or not finding["candidate_id"].strip():
                issues.append(
                    "multi-annotation contract findings must use nonempty candidate IDs"
                )
                continue
            finding_ids.append(finding["candidate_id"])

        duplicate_ids = sorted(
            candidate_id
            for candidate_id in set(finding_ids)
            if finding_ids.count(candidate_id) > 1
        )
        for candidate_id in duplicate_ids:
            issues.append(
                "multi-annotation contract findings contain duplicate candidate ID: "
                f"{candidate_id}"
            )
        for candidate_id in sorted(set(finding_ids) - affected_candidate_ids):
            issues.append(
                "multi-annotation contract finding candidate ID is not an affected "
                f"candidate: {candidate_id}"
            )
        missing_ids = sorted(affected_candidate_ids - set(finding_ids))
        if missing_ids:
            issues.append(
                "multi-annotation contract findings must cover every affected "
                f"candidate; missing: {', '.join(missing_ids)}"
            )
        if (
            len(findings) != len(affected_candidate_ids)
            and not missing_ids
            and not duplicate_ids
            and not (set(finding_ids) - affected_candidate_ids)
        ):
            issues.append(
                "multi-annotation contract findings must contain exactly one entry "
                "for every affected candidate"
            )
        if any(
            not isinstance(finding, dict) or finding.get("status") != "证据不足"
            for finding in findings
        ):
            issues.append(
                "multi-annotation contract every affected candidate finding must be "
                "证据不足"
            )

    if expected.get("p1") != UNRESOLVED_OVERLAP_P1_FAILURE:
        issues.append(
            "multi-annotation contract must record P1 failure for every affected candidate"
        )
    if expected.get("p2") != UNRESOLVED_OVERLAP_P2_BLOCKED:
        issues.append(
            "multi-annotation contract must block P2 for every failed affected candidate"
        )
    if expected.get("region_status_must_not_be") != "一致":
        issues.append(
            "multi-annotation contract must prohibit a region status of 一致 while overlap is unresolved"
        )

    forbidden = expected.get("forbidden")
    if not isinstance(forbidden, list) or not all(
        isinstance(behavior, str) for behavior in forbidden
    ):
        issues.append("multi-annotation contract unresolved scenario must list forbidden behaviors")
    else:
        forbidden_set = set(forbidden)
        for behavior in UNRESOLVED_OVERLAP_FORBIDDEN_BEHAVIORS:
            if behavior not in forbidden_set:
                issues.append(
                    "multi-annotation contract unresolved scenario must forbid: "
                    f"{behavior}"
                )

    allowed_readable_fields = {
        "scenario_id",
        "candidate_clusters",
        "overlap_evidence",
        "readable_candidate_ids",
        "candidate_evidence",
        "visible_expression_conflict_evidence",
        "expected",
    }
    if set(readable) != allowed_readable_fields:
        issues.append(
            "multi-annotation contract readable scenario must not use free-form "
            "or legacy evidence fields"
        )
    readable_candidate_ids = _validate_candidate_clusters(
        readable,
        issues,
        require_distinct_readable_scopes=True,
    )
    _validate_actual_intersection_evidence(
        readable.get("overlap_evidence"), readable_candidate_ids, issues
    )
    readable_ids = _validate_readable_candidate_ids(
        readable, readable_candidate_ids, issues
    )
    _validate_readable_evidence(readable, readable_ids, issues)
    _validate_visible_expression_conflict(readable, readable_ids, issues)

    readable_expected = readable.get("expected")
    if (
        not isinstance(readable_expected, dict)
        or readable_expected.get("permitted_status") != "疑似不一致"
        or readable_expected.get("only_when") != "all independent evidence is present"
        or not isinstance(readable_expected.get("not_a_claim"), list)
    ):
        issues.append(
            "multi-annotation contract must permit only evidence-backed readable conflict"
        )
        return

    readable_forbidden = readable_expected.get("forbidden")
    if not isinstance(readable_forbidden, list) or not all(
        isinstance(behavior, str) for behavior in readable_forbidden
    ):
        issues.append(
            "multi-annotation contract readable scenario must list forbidden behaviors"
        )
        return
    for behavior in READABLE_FORBIDDEN_BEHAVIORS:
        if behavior not in set(readable_forbidden):
            issues.append(
                "multi-annotation contract readable scenario must forbid: "
                f"{behavior}"
            )


def _validate_beam_topology_contract(root: Path, issues: list[str]) -> None:
    """Keep the source-free v2 topology contract narrow and machine-checkable."""

    contract_path = root / TOPOLOGY_CONTRACT_PATH
    contract_text = _read_text(contract_path, issues)
    if contract_text is None:
        issues.append(
            "missing required beam topology contract: "
            f"{TOPOLOGY_CONTRACT_PATH.as_posix()}"
        )
        return
    try:
        contract = json.loads(contract_text)
    except json.JSONDecodeError as error:
        issues.append(f"invalid beam topology contract JSON: {error.msg}")
        return
    if not isinstance(contract, dict):
        issues.append("beam topology contract must be a JSON object")
        return
    expected_keys = {
        "case_id",
        "scope",
        "input_storage",
        "policy",
        "profile",
        "audit_trust",
        "required_rules",
        "scenarios",
        "prohibited_output",
        "non_claims",
    }
    if set(contract) != expected_keys:
        issues.append("beam topology contract must use exactly the approved fields")
        return
    if (
        contract.get("case_id") != "beam-topology-in-situ"
        or contract.get("scope") != "representation-and-readability-only"
        or contract.get("input_storage") != "local-only"
        or contract.get("policy") != "beam-plan-in-situ/v1"
    ):
        issues.append("beam topology contract must identify the fixed local read-only policy")
    profile = contract.get("profile")
    if not isinstance(profile, dict) or set(profile) != {
        "local_only",
        "required_role_arrays",
        "forbidden_profile_controls",
    }:
        issues.append("beam topology contract profile must use exact role/control fields")
    else:
        roles = profile.get("required_role_arrays")
        controls = profile.get("forbidden_profile_controls")
        if (
            profile.get("local_only") is not True
            or not isinstance(roles, list)
            or tuple(roles) != TOPOLOGY_ROLE_ARRAYS
        ):
            issues.append("beam topology contract must require every exact role array")
        if not isinstance(controls, list) or set(controls) != TOPOLOGY_FORBIDDEN_PROFILE_CONTROLS:
            issues.append("beam topology contract must forbid profile execution controls")
    if contract.get("audit_trust") != TOPOLOGY_AUDIT_TRUST:
        issues.append(
            "beam topology contract must state self-integrity and fresh re-audit limits"
        )
    rules = contract.get("required_rules")
    if not isinstance(rules, list) or set(rules) != TOPOLOGY_REQUIRED_RULES:
        issues.append("beam topology contract must require fixed no-nearest/read-only rules")
    scenarios = contract.get("scenarios")
    required_scenarios = {
        "unique-legal-placement": "一致",
        "wrong-side-or-zone": "疑似不一致",
        "repeated-or-overlapping-evidence": "证据不足",
    }
    if not isinstance(scenarios, list):
        issues.append("beam topology contract must define all required scenarios")
    else:
        by_id = {
            item.get("scenario_id"): item
            for item in scenarios
            if isinstance(item, dict)
        }
        if set(by_id) != set(required_scenarios):
            issues.append("beam topology contract scenarios must be exact and complete")
        for scenario_id, status in required_scenarios.items():
            scenario = by_id.get(scenario_id)
            if (
                not isinstance(scenario, dict)
                or set(scenario) != {
                    "scenario_id",
                    "expected_statuses",
                    "actionability",
                }
                or scenario.get("expected_statuses") != [status]
                or scenario.get("actionability") is not False
            ):
                issues.append(
                    "beam topology contract scenario must be non-actionable: "
                    f"{scenario_id}"
                )
    prohibited = contract.get("prohibited_output")
    required_prohibited = {
        "raw_annotation_text",
        "coordinates",
        "layer_names",
        "colors",
        "paths",
        "raw_hashes",
        "token_only_fingerprints",
    }
    if not isinstance(prohibited, list) or set(prohibited) != required_prohibited:
        issues.append("beam topology contract must prohibit raw private metadata")
    non_claims = contract.get("non_claims")
    if not isinstance(non_claims, list) or not {
        "structural calculation",
        "capacity",
        "compliance",
        "design correctness",
    }.issubset(set(non_claims)):
        issues.append("beam topology contract must retain non-design boundary")


def _validate_topology_trace_privacy_schema(root: Path, issues: list[str]) -> None:
    """Forbid a token-only fingerprint oracle in the shipped v2 contract."""

    schema_path = root / "src/liang_pingfa_review/schemas/audit-v2.schema.json"
    schema_text = _read_text(schema_path, issues)
    if schema_text is None:
        return
    try:
        schema = json.loads(schema_text)
    except json.JSONDecodeError as error:
        issues.append(f"invalid audit v2 schema JSON: {error.msg}")
        return
    if not isinstance(schema, dict):
        issues.append("audit v2 schema must be a JSON object")
        return
    try:
        trace = schema["$defs"]["topologyTrace"]
        required = trace["required"]
        properties = trace["properties"]
    except (KeyError, TypeError):
        issues.append("audit v2 schema is missing topology trace privacy fields")
        return
    if (
        not isinstance(required, list)
        or not isinstance(properties, dict)
        or "parsed_value_fingerprint" in required
        or "parsed_value_fingerprint" in properties
        or "opaque_token" in schema_text
        or required.count("token_equality_established") != 1
        or properties.get("token_equality_established") != {"type": "boolean"}
    ):
        issues.append(
            "audit v2 schema must expose only a boolean token equality relation"
        )


def _validate_scope_and_ignore_files(root: Path, issues: list[str]) -> None:
    source_scope_path = root / SKILL_DIRECTORY / "references/source-scope.md"
    source_scope_text = _read_text(source_scope_path, issues)
    if source_scope_text is not None:
        if SOURCE_SCOPE_SENTENCE not in source_scope_text:
            issues.append("source scope reference is missing the verified printed-page scope")
        if "## 页题追踪表" not in source_scope_text:
            issues.append("source scope reference is missing the page-topic trace table")

    gitignore_path = root / ".gitignore"
    gitignore_text = _read_text(gitignore_path, issues)
    if gitignore_text is not None:
        for rule in REQUIRED_GITIGNORE_RULES:
            if rule not in gitignore_text:
                issues.append(f".gitignore is missing required rule: {rule}")


def _validate_packaging(root: Path, issues: list[str]) -> None:
    """Require the pinned local pipeline package and its shipped contracts."""

    pyproject_path = root / "pyproject.toml"
    pyproject_text = _read_text(pyproject_path, issues)
    if pyproject_text is not None:
        for required_text in (
            'requires-python = ">=3.11"',
            '"ezdxf==1.4.4"',
            '"jsonschema==4.23.0"',
            'liang-pingfa-review = "liang_pingfa_review.cli:main"',
            'liang_pingfa_review = ["schemas/*.json"]',
        ):
            if required_text not in pyproject_text:
                issues.append(f"pyproject.toml is missing required package setting: {required_text}")

    required_package_paths = (
        "src/liang_pingfa_review/cli.py",
        "src/liang_pingfa_review/audit.py",
        "src/liang_pingfa_review/plan.py",
        "src/liang_pingfa_review/apply.py",
        "src/liang_pingfa_review/verify.py",
        "src/liang_pingfa_review/ownership.py",
        "src/liang_pingfa_review/oda.py",
        "src/liang_pingfa_review/schemas/audit-v1.schema.json",
        "src/liang_pingfa_review/schemas/audit-v2.schema.json",
        "src/liang_pingfa_review/schemas/beam-topology-profile-v1.schema.json",
        "src/liang_pingfa_review/schemas/edit-plan-v1.schema.json",
        "src/liang_pingfa_review/schemas/verification-v1.schema.json",
        "src/liang_pingfa_review/native_protocol.py",
        "src/liang_pingfa_review/native_contracts.py",
        "src/liang_pingfa_review/native_bridge.py",
        "src/liang_pingfa_review/native_audit.py",
        "src/liang_pingfa_review/native_plan.py",
        "src/liang_pingfa_review/native_manifest.py",
        "src/liang_pingfa_review/core_console.py",
        "src/liang_pingfa_review/native_apply.py",
        "src/liang_pingfa_review/native_verify.py",
    )
    required_package_paths += tuple(
        "src/liang_pingfa_review/schemas/" + filename
        for filename in NATIVE_SCHEMA_PATHS
    )
    for relative_path in required_package_paths:
        if not (root / relative_path).is_file():
            issues.append(f"missing required pipeline package path: {relative_path}")


def _validate_ci_workflow(root: Path, issues: list[str]) -> None:
    """Require the Windows-only validation path and native phase two."""

    workflow_path = root / ".github/workflows/validate.yml"
    workflow = _read_text(workflow_path, issues)
    if workflow is None:
        return
    for required_text in (
        "validate-windows:",
        "runs-on: windows-latest",
        'python-version: "3.13"',
        "python -m pip install .",
        'python -m unittest discover -s tests -p "test_*.py" -v',
        "python -m compileall -q src tests scripts",
        "actions/setup-dotnet@v4",
        'dotnet-version: "8.0.x"',
        "dotnet build native-bridge-contracts/LiangPingfa.NativeBridge.Contracts.csproj",
        "python -m liang_pingfa_review doctor",
        "python -m liang_pingfa_review native-doctor",
    ):
        if required_text not in workflow:
            issues.append(
                f"validate workflow is missing required command or platform: {required_text}"
            )
    lowered = workflow.casefold()
    for forbidden_text in (
        "validate-ubuntu:",
        "ubuntu-latest",
        "runs-on: ubuntu",
        "linux",
        "portable_ci",
        "portable unit suite",
        "choco install oda",
        "winget install oda",
        "odafileconverter.exe",
        "download oda",
    ):
        if forbidden_text in lowered:
            issues.append(
                f"validate workflow must not install or invoke real ODA: {forbidden_text}"
            )

    # The SDK creates the exact C# ``bin``/``obj`` paths above.  Validate the
    # tracked allowlist before that build so a generated tree cannot mask a
    # tracked artifact in a fresh checkout.
    build_command = (
        "dotnet build native-bridge-contracts/"
        "LiangPingfa.NativeBridge.Contracts.csproj"
    )
    tracked_command = "python scripts/validate_skill.py --tracked"
    jobs_section = workflow.split("\njobs:\n", 1)[-1]
    job_names = re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", jobs_section)
    if job_names != ["validate-windows"]:
        issues.append(
            "validate workflow must contain exactly one job named validate-windows"
        )
    for job_name in ("validate-windows",):
        job_match = re.search(
            rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:|\Z)",
            workflow,
        )
        if job_match is None:
            continue
        job = job_match.group("body")
        tracked_index = job.find(tracked_command)
        build_index = job.find(build_command)
        if tracked_index < 0 or build_index < 0 or tracked_index > build_index:
            issues.append(
                "validate workflow must run tracked validation before C# build: "
                + job_name
            )


def _validate_native_bridge_contract(root: Path, issues: list[str]) -> None:
    """Keep the optional native lane SDK-free, strict, redacted, and separate."""

    contract_path = root / NATIVE_PROTOCOL_CONTRACT_PATH
    contract_text = _read_text(contract_path, issues)
    if contract_text is None:
        issues.append(
            "missing native protocol contract: "
            + NATIVE_PROTOCOL_CONTRACT_PATH.as_posix()
        )
    else:
        try:
            contract = json.loads(contract_text)
        except json.JSONDecodeError as error:
            issues.append(f"invalid native protocol contract JSON: {error.msg}")
            contract = None
        expected_keys = {
            "case_id",
            "scope",
            "input_storage",
            "session_rules",
            "rpc_allowlist",
            "write_rules",
            "prohibited_output",
            "non_claims",
        }
        if not isinstance(contract, dict) or set(contract) != expected_keys:
            issues.append("native protocol contract must use exactly the approved fields")
        elif (
            contract.get("case_id") != "native-bridge-protocol"
            or contract.get("scope") != "optional-local-native-bridge"
            or contract.get("input_storage") != "private-local-only"
            or contract.get("rpc_allowlist")
            != [
                "health",
                "get_session",
                "get_current_document",
                "export_inventory",
                "export_exact_geometry",
            ]
        ):
            issues.append("native protocol contract must retain the fixed read-only allowlist")
        else:
            required_session_rules = {
                "private-ntfs-session-descriptor-current-user-system-dacl",
                "post-rename-secret-cleanup-through-retained-handle",
                "atomic-single-flight-configured-deadlines",
                "overlapped-cancellable-absolute-deadline-io",
            }
            required_write_rules = {
                "fresh-prewrite-binding-never-predicts-final-revision",
                "final-revision-database-output-copy-readback-bound",
            }
            if (
                not isinstance(contract["session_rules"], list)
                or not required_session_rules.issubset(set(contract["session_rules"]))
            ):
                issues.append(
                    "native protocol contract must retain private descriptor and RPC rules"
                )
            if (
                not isinstance(contract["write_rules"], list)
                or not required_write_rules.issubset(set(contract["write_rules"]))
            ):
                issues.append(
                    "native protocol contract must retain separate pre/final revision rules"
                )
            if (
                not isinstance(contract["prohibited_output"], list)
                or "record_counts" not in set(contract["prohibited_output"])
            ):
                issues.append(
                    "native protocol contract must redact record counts from public events"
                )

    for filename in NATIVE_SCHEMA_PATHS:
        path = root / "src" / "liang_pingfa_review" / "schemas" / filename
        text = _read_text(path, issues)
        if text is None:
            continue
        try:
            schema = json.loads(text)
        except json.JSONDecodeError as error:
            issues.append(f"invalid native schema {filename}: {error.msg}")
            continue

        def require_strict_objects(value: object) -> None:
            if isinstance(value, dict):
                if (
                    value.get("type") == "object"
                    and value.get("additionalProperties") is not False
                ):
                    issues.append(
                        f"native schema object is not strict: {filename}"
                    )
                for item in value.values():
                    require_strict_objects(item)
            elif isinstance(value, list):
                for item in value:
                    require_strict_objects(item)

        require_strict_objects(schema)
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            issues.append(f"native schema is not Draft 2020-12: {filename}")
        if not str(schema.get("$id", "")).startswith("liang-pingfa/native-"):
            issues.append(f"native schema has wrong namespace: {filename}")

    manifest_schema = _read_text(
        root
        / "src"
        / "liang_pingfa_review"
        / "schemas"
        / "native-edit-manifest-v1.schema.json",
        issues,
    )
    if manifest_schema is not None and (
        '"expected_prewrite_revision"' not in manifest_schema
        or '"expected_final_revision_fingerprint"' in manifest_schema
    ):
        issues.append("native manifest schema must bind pre-write state without final prediction")
    cardinality_schemas = (
        "native-audit-v1.schema.json",
        "native-edit-plan-v1.schema.json",
        "native-edit-manifest-v1.schema.json",
        "native-verification-v1.schema.json",
    )
    for filename in cardinality_schemas:
        text = _read_text(
            root / "src" / "liang_pingfa_review" / "schemas" / filename,
            issues,
        )
        if text is None:
            continue
        try:
            schema = json.loads(text)
            required = schema["required"]
            properties = schema["properties"]
        except (json.JSONDecodeError, KeyError, TypeError):
            issues.append(f"native cardinality schema is invalid: {filename}")
            continue
        if (
            "record_cardinality" not in required
            or properties.get("record_cardinality") != {"const": "explicit_private"}
            or "cardinality_disclosed" in text
        ):
            issues.append(
                "native enumerating artifact must admit explicit private cardinality: "
                f"{filename}"
            )
    for filename in (
        "native-audit-v1.schema.json",
        "native-edit-plan-v1.schema.json",
        "native-edit-manifest-v1.schema.json",
    ):
        text = _read_text(
            root / "src" / "liang_pingfa_review" / "schemas" / filename,
            issues,
        )
        if text is None:
            continue
        try:
            schema = json.loads(text)
            required = schema["required"]
            properties = schema["properties"]
        except (json.JSONDecodeError, KeyError, TypeError):
            issues.append(f"native marker-binding schema is invalid: {filename}")
            continue
        if (
            "marker_policy_binding" not in required
            or "marker_policy_binding" not in properties
            or "height_bits" not in text
            or "rotation_bits" not in text
            or "text_derivation_version" not in text
            or "geometry_defaults" not in text
        ):
            issues.append(
                "native marker policy must be fully audit/plan/manifest-bound: "
                f"{filename}"
            )
    for filename in (
        "native-console-result-v1.schema.json",
        "native-console-export-v1.schema.json",
    ):
        text = _read_text(
            root / "src" / "liang_pingfa_review" / "schemas" / filename,
            issues,
        )
        if text is not None and (
            '"final_revision_fingerprint"' not in text
            or '"final_document_binding"' not in text
        ):
            issues.append(f"native schema lacks final transaction binding: {filename}")

    cli_text = _read_text(root / "src/liang_pingfa_review/cli.py", issues)
    if cli_text is not None:
        for command in (
            '"native-session"',
            '"native-doctor"',
            '"native-audit"',
            '"native-plan"',
            '"native-review-plan"',
            '"native-apply"',
            '"native-verify"',
        ):
            if command not in cli_text:
                issues.append(f"native CLI command is missing: {command}")
        if "--backend" in cli_text:
            issues.append("native lane must not add a generic backend selector")

    native_sources = (
        "native_protocol.py",
        "native_contracts.py",
        "native_bridge.py",
        "native_audit.py",
        "native_plan.py",
        "native_manifest.py",
        "core_console.py",
        "native_apply.py",
        "native_verify.py",
    )
    for filename in native_sources:
        text = _read_text(root / "src/liang_pingfa_review" / filename, issues)
        if text is not None and (
            "from .oda import" in text or "OdaRunner" in text or "ODA File Converter" in text
        ):
            issues.append(f"native source must not invoke ODA: {filename}")

    contracts_root = root / "native-bridge-contracts"
    project = _read_text(
        contracts_root / "LiangPingfa.NativeBridge.Contracts.csproj", issues
    )
    csharp_files = (
        contracts_root / "ProtocolV1.cs",
        contracts_root / "Interfaces.cs",
        contracts_root / "README.md",
        contracts_root / "LICENSE",
    )
    if project is not None:
        for required in ("<TargetFramework>net8.0</TargetFramework>",):
            if required not in project:
                issues.append(f"SDK-free C# project is missing: {required}")
    for path in csharp_files + ((contracts_root / "LiangPingfa.NativeBridge.Contracts.csproj"),):
        text = _read_text(path, issues)
        if text is None:
            continue
        lowered = text.casefold()
        for forbidden in (
            "autodesk",
            "teigha",
            "tssd",
            "oda",
            "packagereference",
            "<reference",
        ):
            if forbidden in lowered:
                issues.append(
                    f"SDK-free C# contracts contain forbidden proprietary/package reference: {path.name}"
                )
    protocol_dtos = _read_text(contracts_root / "ProtocolV1.cs", issues)
    if protocol_dtos is not None:
        for required in (
            "NativePrewriteRevisionV1",
            "NativeFinalDocumentBindingV1",
            "FinalRevisionFingerprint",
            "NativeSessionHandshakeResultV1",
            "ChallengeResponse",
            "NativeInventoryExportV1",
            "InventoryJson",
            "NativeExactGeometryResponseV1",
            'JsonPropertyName("inventory_json")',
        ):
            if required not in protocol_dtos:
                issues.append(f"SDK-free C# contracts lack final/pre-write DTO: {required}")
    interface_dtos = _read_text(contracts_root / "Interfaces.cs", issues)
    if interface_dtos is not None:
        for required in (
            "NativeHealthResponseV1",
            "NativeSessionHandshakeResponseV1",
            "NativeCurrentDocumentResponseV1",
            "NativeInventoryResponseV1",
            "NativeExactGeometryResponseV1",
        ):
            if required not in interface_dtos:
                issues.append(f"read-only C# interface lacks wire response DTO: {required}")

    report_source = _read_text(root / "src/liang_pingfa_review/reports.py", issues)
    if report_source is not None and (
        "record counts" not in report_source
        or "Private machine-readable audit, plan, manifest, and verification" not in report_source
    ):
        issues.append(
            "native reports must redact cardinality while acknowledging private artifacts"
        )

    native_reference = _read_text(
        root / SKILL_DIRECTORY / "references" / "native-cad-bridge.md", issues
    )
    readme = _read_text(root / "README.md", issues)
    skill = _read_text(root / SKILL_PATH, issues)
    for name, text in (
        ("native bridge reference", native_reference),
        ("README.md", readme),
    ):
        if text is None:
            continue
        for required in (
            "默认只读",
            "PID",
            "copy-only",
            "固定",
            "读回",
            "不",
        ):
            if required not in text:
                issues.append(f"{name} is missing native boundary wording: {required}")
    for name, text in (
        ("native bridge reference", native_reference),
        ("README.md", readme),
        ("SKILL.md", skill),
    ):
        if text is None:
            continue
        for required in NATIVE_PRIVATE_ARTIFACT_PRIVACY_PHRASES:
            if required not in text:
                issues.append(
                    f"{name} is missing private-artifact privacy wording: {required}"
                )
        for forbidden in NATIVE_PRIVATE_ARTIFACT_FALSE_REDACTION_PHRASES:
            if forbidden in text:
                issues.append(
                    f"{name} falsely claims private artifacts are redacted: {forbidden}"
                )


def _validate_bounded_oda_contract(root: Path, issues: list[str]) -> None:
    """Keep docs and implementation aligned with the accepted local boundary."""

    readme = _read_text(root / "README.md", issues)
    workflow = _read_text(
        root
        / ".github"
        / "skills"
        / SKILL_NAME
        / "references"
        / "dwg-two-stage-workflow.md",
        issues,
    )
    for name, text in (("README.md", readme), ("dwg workflow reference", workflow)):
        if text is None:
            continue
        normalized = re.sub(r"\s+", " ", text)
        if BOUNDED_THREAT_MODEL not in normalized:
            issues.append(f"{name} is missing the bounded trusted-local-session threat model")
        for required in (
            "ODA 执行不是",
            "恶意软件",
            "完整重新审计",
        ):
            if required not in normalized:
                issues.append(f"{name} is missing bounded ODA contract wording: {required}")

    support_documents = (
        ("README.md", readme),
        ("dwg workflow reference", workflow),
        ("SKILL.md", _read_text(root / SKILL_PATH, issues)),
        (
            "local regression reference",
            _read_text(
                root / SKILL_DIRECTORY / "references" / "local-regression.md",
                issues,
            ),
        ),
    )
    for name, text in support_documents:
        if text is None:
            continue
        normalized = re.sub(r"\s+", " ", text)
        for required in SUPPORT_BOUNDARY_PHRASES[name]:
            if re.sub(r"\s+", " ", required) not in normalized:
                issues.append(
                    f"{name} is missing required public support-boundary wording: {required}"
                )

    topology_documents = (
        ("README.md", readme),
        (
            "beam topology reference",
            _read_text(
                root / SKILL_DIRECTORY / "references" / "beam-topology-audit.md",
                issues,
            ),
        ),
        ("dwg workflow reference", workflow),
    )
    for name, text in topology_documents:
        if text is None:
            continue
        normalized = text.replace("`", "")
        for required in TOPOLOGY_AUDIT_TRUST_PHRASES:
            if required not in normalized:
                issues.append(
                    f"{name} is missing topology audit trust-boundary wording: {required}"
                )

    oda_path = root / "src/liang_pingfa_review/oda.py"
    oda_text = _read_text(oda_path, issues)
    if oda_text is not None:
        for forbidden in (
            "_Preowned" + "ConverterOutput",
            "create_new_output_" + "reservation_file",
            "write_reservation_" + "marker",
            "read_reservation_" + "marker",
            "remove_reservation_" + "marker",
        ):
            if forbidden in oda_text:
                issues.append(f"ODA wrapper retains incompatible reservation behavior: {forbidden}")
        for required in (
            "create_private_oda_root",
            "_capture_pre_run_inventory",
            "_adopt_converter_output",
            "expected_state_proof",
        ):
            if required not in oda_text:
                issues.append(f"ODA wrapper is missing bounded conversion control: {required}")

    ownership_text = _read_text(root / "src/liang_pingfa_review/ownership.py", issues)
    if ownership_text is not None:
        for required in (
            "private_staging_capability",
            "secure_private_staging_directory",
            "_is_ntfs_volume",
        ):
            if required not in ownership_text:
                issues.append(f"ownership layer is missing private staging control: {required}")


def validate_repository(root: Path | str) -> None:
    """Validate the full working tree without requiring Git metadata."""

    repository_root = Path(root).resolve()
    issues: list[str] = []
    if not repository_root.is_dir():
        raise ValidationError([f"repository root does not exist: {repository_root}"])

    canonical_skill_path = repository_root / SKILL_PATH
    if not canonical_skill_path.is_file():
        issues.append(f"canonical SKILL.md is missing: {SKILL_PATH.as_posix()}")

    files = _validate_repository_policy(
        repository_root,
        _iter_repository_files(repository_root, issues),
        issues,
    )
    _validate_text_safety(repository_root, files, issues)

    skill_text = _read_text(canonical_skill_path, issues)
    if skill_text is not None:
        _validate_front_matter(repository_root, skill_text, issues)
        _validate_skill_content(skill_text, issues)
        _validate_resources(repository_root, skill_text, issues)
    _validate_multi_annotation_overlap_contract(repository_root, issues)
    _validate_scope_and_ignore_files(repository_root, issues)
    _validate_packaging(repository_root, issues)
    _validate_ci_workflow(repository_root, issues)
    _validate_beam_topology_contract(repository_root, issues)
    _validate_topology_trace_privacy_schema(repository_root, issues)
    _validate_bounded_oda_contract(repository_root, issues)
    _validate_native_bridge_contract(repository_root, issues)

    if issues:
        raise ValidationError(issues)


def validate_tracked_files(root: Path | str) -> None:
    """Reject forbidden or out-of-policy files that Git would publish."""

    repository_root = Path(root).resolve()
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=False,
        )
    except OSError as error:
        raise ValidationError([f"unable to execute git ls-files: {error}"]) from error
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValidationError([f"git ls-files failed: {stderr or result.returncode}"])

    issues: list[str] = []
    for raw_path in sorted(path for path in result.stdout.split(b"\0") if path):
        relative = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        candidate = repository_root / Path(relative)
        if _has_forbidden_extension(Path(relative)):
            issues.append(f"forbidden tracked artifact extension: {relative}")
        if relative not in ALLOWED_FILES:
            issues.append(f"tracked path is not allowed by repository policy: {relative}")
        if candidate.is_symlink():
            issues.append(f"tracked symlink is not allowed: {relative}")
    if issues:
        raise ValidationError(issues)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Liang Pingfa Review Skill repository."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root to validate; defaults to the current directory",
    )
    parser.add_argument(
        "--tracked",
        action="store_true",
        help="also validate the files reported by git ls-files",
    )
    arguments = parser.parse_args(argv)

    try:
        validate_repository(arguments.root)
        if arguments.tracked:
            validate_tracked_files(arguments.root)
    except ValidationError as error:
        print("Validation failed:", file=sys.stderr)
        for issue in error.issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    print("Skill repository validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
