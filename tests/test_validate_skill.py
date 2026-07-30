"""Focused tests for the deterministic Skill repository validator."""

from __future__ import annotations

import shutil
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


if __name__ == "__main__":
    unittest.main()
