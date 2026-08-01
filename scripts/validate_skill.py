"""Deterministic standard-library validation for this Skill repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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
        "src/liang_pingfa_review/oda.py",
        "src/liang_pingfa_review/overlay_profile.py",
        "src/liang_pingfa_review/ownership.py",
        "src/liang_pingfa_review/plan.py",
        "src/liang_pingfa_review/raw_dxf.py",
        "src/liang_pingfa_review/reports.py",
        "src/liang_pingfa_review/snapshots.py",
        "src/liang_pingfa_review/temporary.py",
        "src/liang_pingfa_review/verify.py",
        "src/liang_pingfa_review/schemas/__init__.py",
        "src/liang_pingfa_review/schemas/audit-v1.schema.json",
        "src/liang_pingfa_review/schemas/edit-plan-v1.schema.json",
        "src/liang_pingfa_review/schemas/verification-v1.schema.json",
        "tests/support/__init__.py",
        "tests/support/owned_files.py",
        "tests/support/synthetic_dxf.py",
        "tests/test_apply_verify.py",
        "tests/test_audit_plan.py",
        "tests/test_canonical_contracts.py",
        "tests/test_handle_ownership.py",
        "tests/test_linux_ci_setup.py",
        "tests/test_oda_cli.py",
        "tests/test_source_binding.py",
        "tests/test_validate_skill.py",
        "tests/contracts/local-representation-readability.json",
        "tests/contracts/two-stage-overlay-workflow.json",
        "tests/local-fixtures/README.md",
    }
)

IGNORED_DIRECTORIES = frozenset({".git", "__pycache__"})
ROOT_GENERATED_DIRECTORIES = frozenset({"build", "dist"})
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
TEXT_FILE_SUFFIXES = frozenset({".md", ".py", ".yml", ".yaml", ".json", ".toml"})

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
UNC_PATH_PATTERN = re.compile(r"(?<!\x5c)\x5c\x5c[^\x5c/\s]+[\x5c/]")
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
)
SOURCE_SCOPE_SENTENCE = "Verified source scope: 22G101-1 printed pages 1-22 through 1-33."
BOUNDED_THREAT_MODEL = (
    "trusted Windows account/session, ODA executable, OS, and local NTFS volume; "
    "no hostile same-account/admin process"
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


def _validate_text_safety(root: Path, files: Iterable[Path], issues: list[str]) -> None:
    for path in files:
        if not _is_text_file(path):
            continue
        text = _read_text(path, issues)
        if text is None:
            continue
        relative = _relative_posix(path, root)
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
        for value in values:
            if WINDOWS_ABSOLUTE_PATH_PATTERN.search(value):
                issues.append(f"obvious Windows absolute local path found in {relative}")
                break
            if UNC_PATH_PATTERN.search(value):
                issues.append(f"UNC local path found in {relative}")
                break
            if POSIX_LOCAL_PATH_PATTERN.search(value):
                issues.append(f"obvious POSIX local path found in {relative}")
                break
            if SHA256_PATTERN.search(value):
                issues.append(f"possible source hash found in {relative}")
                break


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
        "src/liang_pingfa_review/schemas/edit-plan-v1.schema.json",
        "src/liang_pingfa_review/schemas/verification-v1.schema.json",
    )
    for relative_path in required_package_paths:
        if not (root / relative_path).is_file():
            issues.append(f"missing required pipeline package path: {relative_path}")


def _validate_ci_workflow(root: Path, issues: list[str]) -> None:
    """Require both hermetic Ubuntu checks and native Windows phase two."""

    workflow_path = root / ".github/workflows/validate.yml"
    workflow = _read_text(workflow_path, issues)
    if workflow is None:
        return
    for required_text in (
        "validate-ubuntu:",
        "runs-on: ubuntu-latest",
        "validate-windows:",
        "runs-on: windows-latest",
        'python-version: "3.13"',
        "python -m pip install .",
        'python -m unittest discover -s tests -p "test_*.py" -v',
        "python -m compileall -q src tests scripts",
    ):
        if required_text not in workflow:
            issues.append(
                f"validate workflow is missing required command or platform: {required_text}"
            )
    lowered = workflow.casefold()
    for forbidden_text in (
        "choco install oda",
        "winget install oda",
        "odafileconverter.exe",
        "download oda",
    ):
        if forbidden_text in lowered:
            issues.append(
                f"validate workflow must not install or invoke real ODA: {forbidden_text}"
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
    _validate_scope_and_ignore_files(repository_root, issues)
    _validate_packaging(repository_root, issues)
    _validate_ci_workflow(repository_root, issues)
    _validate_bounded_oda_contract(repository_root, issues)

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
