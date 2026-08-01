"""Focused tests for the deterministic Skill repository validator."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import validate_skill


class ValidateSkillTests(unittest.TestCase):
    """Exercise valid and invalid repository states in isolated copies."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "repository"
        shutil.copytree(
            PROJECT_ROOT,
            self.repository,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                "build",
                "dist",
                "*.egg-info",
            ),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def assert_validation_fails_with(self, expected_message: str) -> None:
        with self.assertRaises(validate_skill.ValidationError) as raised:
            validate_skill.validate_repository(self.repository)
        self.assertIn(expected_message, raised.exception.issues)

    def test_clean_repository_passes(self) -> None:
        validate_skill.validate_repository(self.repository)

    def test_generated_build_dist_and_egg_info_are_ignored(self) -> None:
        generated_files = (
            self.repository / "build/lib/generated.py",
            self.repository / "dist/liang_pingfa_review-0.1.0.whl",
            self.repository / "dist/liang-pingfa-review-0.1.0.tar.gz",
            self.repository / "src/liang_pingfa_review.egg-info/PKG-INFO",
        )
        for generated_file in generated_files:
            generated_file.parent.mkdir(parents=True, exist_ok=True)
            generated_file.write_bytes(b"generated")

        validate_skill.validate_repository(self.repository)

    def test_nested_build_directory_remains_subject_to_policy(self) -> None:
        nested_generated_file = self.repository / "src/build/generated.py"
        nested_generated_file.parent.mkdir(parents=True, exist_ok=True)
        nested_generated_file.write_text("generated\n", encoding="utf-8")

        self.assert_validation_fails_with(
            "path is not allowed by repository policy: src/build/generated.py"
        )

    def test_wheel_outside_dist_remains_forbidden(self) -> None:
        artifact_path = self.repository / "release.whl"
        artifact_path.write_bytes(b"not a wheel")

        self.assert_validation_fails_with(
            "forbidden artifact extension: release.whl"
        )

    def test_sdist_outside_dist_remains_forbidden(self) -> None:
        artifact_path = self.repository / "release.tar.gz"
        artifact_path.write_bytes(b"not a source archive")

        self.assert_validation_fails_with(
            "forbidden artifact extension: release.tar.gz"
        )

    def test_tracked_build_and_egg_info_files_fail(self) -> None:
        tracked_repository = Path(self.temporary_directory.name) / "tracked-repository"
        tracked_repository.mkdir()
        tracked_files = (
            tracked_repository / "build/manifest.txt",
            tracked_repository / "dist/manifest.txt",
            tracked_repository / "src/liang_pingfa_review.egg-info/PKG-INFO",
        )
        for tracked_file in tracked_files:
            tracked_file.parent.mkdir(parents=True, exist_ok=True)
            tracked_file.write_text("generated\n", encoding="utf-8")

        subprocess.run(
            ["git", "init"],
            cwd=tracked_repository,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=tracked_repository,
            check=True,
            capture_output=True,
            text=True,
        )

        with self.assertRaises(validate_skill.ValidationError) as raised:
            validate_skill.validate_tracked_files(tracked_repository)

        self.assertIn(
            "tracked path is not allowed by repository policy: build/manifest.txt",
            raised.exception.issues,
        )
        self.assertIn(
            "tracked path is not allowed by repository policy: dist/manifest.txt",
            raised.exception.issues,
        )
        self.assertIn(
            "tracked path is not allowed by repository policy: "
            "src/liang_pingfa_review.egg-info/PKG-INFO",
            raised.exception.issues,
        )

    def test_pip_install_then_validation_passes_without_cleanup(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", ".", "--no-deps"],
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        validate_skill.validate_repository(self.repository)

    def test_invalid_name_fails(self) -> None:
        skill_path = self.repository / validate_skill.SKILL_PATH
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "name: liang-pingfa-tuzhi-shencha",
                "name: invalid--name",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with("invalid skill name: invalid--name")

    def test_missing_reference_fails(self) -> None:
        reference_path = (
            self.repository
            / validate_skill.SKILL_DIRECTORY
            / "references/workflow-output.md"
        )
        reference_path.unlink()

        self.assert_validation_fails_with(
            "missing referenced resource in SKILL.md: references/workflow-output.md"
        )

    def test_forbidden_source_artifact_fails(self) -> None:
        artifact_path = self.repository / "tests/local-fixtures/fixture.pdf"
        artifact_path.write_bytes(b"not a real PDF")

        self.assert_validation_fails_with(
            "forbidden artifact extension: tests/local-fixtures/fixture.pdf"
        )

    def test_forbidden_archive_in_pytest_cache_fails(self) -> None:
        artifact_path = self.repository / ".pytest_cache/source-drawing.tar.gz"
        artifact_path.parent.mkdir()
        artifact_path.write_bytes(b"not a real source archive")

        self.assert_validation_fails_with(
            "forbidden artifact extension: .pytest_cache/source-drawing.tar.gz"
        )

    def test_absolute_local_path_fails(self) -> None:
        readme_path = self.repository / "README.md"
        separator = chr(92)
        private_path = "C" + chr(58) + separator + "private" + separator + "drawing.pdf"
        readme_path.write_text(
            readme_path.read_text(encoding="utf-8") + f"\nPrivate input: {private_path}\n",
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "obvious Windows absolute local path found in README.md"
        )

    def test_local_audit_artifact_fails(self) -> None:
        artifact_path = self.repository / "output/audit.json"
        artifact_path.parent.mkdir()
        artifact_path.write_text("{}", encoding="utf-8")

        self.assert_validation_fails_with(
            "path is not allowed by repository policy: output/audit.json"
        )

    def test_unapproved_package_file_fails(self) -> None:
        source_path = self.repository / "src/liang_pingfa_review/unapproved.py"
        source_path.write_text("pass\n", encoding="utf-8")

        self.assert_validation_fails_with(
            "path is not allowed by repository policy: src/liang_pingfa_review/unapproved.py"
        )

    def test_missing_windows_phase_two_workflow_fails(self) -> None:
        workflow_path = self.repository / ".github/workflows/validate.yml"
        workflow_path.write_text(
            workflow_path.read_text(encoding="utf-8").replace(
                "validate-windows:",
                "removed-windows-job:",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "validate workflow is missing required command or platform: validate-windows:"
        )

    def test_missing_bounded_oda_threat_model_fails(self) -> None:
        readme_path = self.repository / "README.md"
        readme_path.write_text(
            readme_path.read_text(encoding="utf-8").replace(
                "trusted Windows account/session, ODA executable,",
                "removed threat model,",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "README.md is missing the bounded trusted-local-session threat model"
        )

    def test_missing_public_support_boundary_fails(self) -> None:
        readme_path = self.repository / "README.md"
        readme_path.write_text(
            readme_path.read_text(encoding="utf-8").replace(
                "R2018/AC1032 DXF-exposable",
                "removed support profile",
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "README.md is missing required public support-boundary wording: "
            "R2018/AC1032 DXF-exposable"
        )

    def test_skill_rejects_bypassing_unsupported_drawings(self) -> None:
        skill_path = self.repository / validate_skill.SKILL_PATH
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "不得绕过这些兼容性门",
                "removed no-bypass instruction",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "SKILL.md is missing required public support-boundary wording: "
            "不得绕过这些兼容性门"
        )

if __name__ == "__main__":
    unittest.main()
