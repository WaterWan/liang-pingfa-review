"""Focused tests for the deterministic Skill repository validator."""

from __future__ import annotations

import json
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

    def mutate_multi_annotation_contract(self, mutation) -> None:
        """Apply a JSON-only mutation to the checked multi-annotation contract."""

        contract_path = self.repository / validate_skill.MULTI_ANNOTATION_CONTRACT_PATH
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        mutation(contract)
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

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

    def test_missing_multi_annotation_reference_fails(self) -> None:
        reference_path = (
            self.repository
            / validate_skill.SKILL_DIRECTORY
            / "references/multi-annotation-overlap.md"
        )
        reference_path.unlink()

        self.assert_validation_fails_with(
            "missing required reference file: references/multi-annotation-overlap.md"
        )

    def test_missing_multi_annotation_contract_fails(self) -> None:
        (self.repository / validate_skill.MULTI_ANNOTATION_CONTRACT_PATH).unlink()

        self.assert_validation_fails_with(
            "missing required multi-annotation contract: "
            "tests/contracts/multi-annotation-overlap.json"
        )

    def test_missing_multi_annotation_skill_phrase_fails(self) -> None:
        skill_path = self.repository / validate_skill.SKILL_PATH
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "重叠簇门在 P1 之前",
                "removed overlap ordering",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "SKILL.md is missing required multi-annotation wording: 重叠簇门在 P1 之前"
        )

    def test_multi_annotation_duplicate_candidate_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"][1]["candidate_id"] = "cluster-a"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract findings contain duplicate candidate ID: cluster-a"
        )

    def test_multi_annotation_missing_candidate_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"].pop()

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract findings must cover every affected candidate; "
            "missing: cluster-b"
        )

    def test_multi_annotation_extra_candidate_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"].append(
                {"candidate_id": "cluster-extra", "status": "证据不足"}
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract finding candidate ID is not an affected "
            "candidate: cluster-extra"
        )

    def test_multi_annotation_empty_candidate_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"][1]["candidate_id"] = ""

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract findings must use nonempty candidate IDs"
        )

    def test_multi_annotation_non_insufficient_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"][0]["status"] = "一致"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract every affected candidate finding must be 证据不足"
        )

    def test_multi_annotation_p1_pass_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["p1"] = "passed"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract must record P1 failure for every affected candidate"
        )

    def test_multi_annotation_p2_allowed_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["p2"] = "allowed"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract must block P2 for every failed affected candidate"
        )

    def test_multi_annotation_removed_concatenation_forbidden_behavior_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove(
                "candidate concatenation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: "
            "candidate concatenation"
        )

    def test_multi_annotation_field_merge_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove("field merge")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: field merge"
        )

    def test_multi_annotation_nearest_binding_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove(
                "nearest-distance binding"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: "
            "nearest-distance binding"
        )

    def test_multi_annotation_partial_ocr_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove("partial OCR pass")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: partial OCR pass"
        )

    def test_multi_annotation_color_layer_semantic_proof_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove(
                "color-or-layer semantic proof"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: "
            "color-or-layer semantic proof"
        )

    def test_multi_annotation_unresolved_consistent_region_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["region_status_must_not_be"] = "不同"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract must prohibit a region status of 一致 while "
            "overlap is unresolved"
        )

    def test_multi_annotation_readable_conflict_must_be_evidence_backed(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["expected"]["only_when"] = "automatic conflict"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract must permit only evidence-backed readable conflict"
        )

    def test_multi_annotation_proximity_only_overlap_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["overlap_evidence"] = ["proximity only"]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract overlap_evidence must use allowlisted "
            "actual intersection types and exactly identify affected candidates"
        )

    def test_multi_annotation_color_only_overlap_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["overlap_evidence"] = [
                {
                    "type": "color",
                    "candidate_ids": ["cluster-a", "cluster-b"],
                }
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract overlap_evidence must use allowlisted "
            "actual intersection types and exactly identify affected candidates"
        )

    def test_multi_annotation_empty_intersection_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["overlap_evidence"] = [
                {"type": "ink_mask_intersection", "candidate_ids": []}
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract overlap_evidence must use allowlisted "
            "actual intersection types and exactly identify affected candidates"
        )

    def test_multi_annotation_duplicate_readable_candidate_id_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["readable_candidate_ids"][1] = "cluster-c"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable_candidate_ids contain duplicate "
            "candidate ID: cluster-c"
        )

    def test_multi_annotation_missing_readable_candidate_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["readable_candidate_ids"].pop()

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable_candidate_ids must cover every "
            "candidate; missing: cluster-d"
        )

    def test_multi_annotation_extra_readable_candidate_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["readable_candidate_ids"].append("cluster-extra")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable candidate ID is not a candidate "
            "cluster: cluster-extra"
        )

    def test_multi_annotation_color_only_independent_evidence_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["independent_evidence"] = [
                {"type": "color", "value": "hint-c"}
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must not use free-form "
            "or legacy evidence fields"
        )

    def test_multi_annotation_color_only_visible_conflict_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["visible_expression_conflict_evidence"] = [
                {
                    "type": "color",
                    "candidate_ids": ["cluster-c", "cluster-d"],
                }
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract visible expression conflict evidence must "
            "be allowlisted and identify every readable candidate"
        )

    def test_multi_annotation_missing_readable_boundary_evidence_fails(self) -> None:
        def mutate(contract) -> None:
            del contract["scenarios"][1]["candidate_evidence"][0]["boundary_evidence"]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable evidence requires allowlisted "
            "boundary_evidence for: cluster-c"
        )

    def test_multi_annotation_missing_readable_scope_evidence_fails(self) -> None:
        def mutate(contract) -> None:
            del contract["scenarios"][1]["candidate_evidence"][0]["scope_evidence"]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable evidence requires allowlisted "
            "scope_evidence for: cluster-c"
        )

    def test_multi_annotation_missing_readable_binding_evidence_fails(self) -> None:
        def mutate(contract) -> None:
            del contract["scenarios"][1]["candidate_evidence"][0]["binding_evidence"]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable evidence requires allowlisted "
            "binding_evidence for: cluster-c"
        )

    def test_multi_annotation_missing_visible_conflict_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["visible_expression_conflict_evidence"] = []

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario requires separate visible "
            "expression conflict evidence"
        )

    def test_multi_annotation_readable_field_concatenation_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["expected"]["forbidden"].remove(
                "field concatenation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must forbid: "
            "field concatenation"
        )

    def test_multi_annotation_readable_field_merge_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["expected"]["forbidden"].remove("field merge")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must forbid: field merge"
        )

    def test_multi_annotation_readable_scope_merge_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["expected"]["forbidden"].remove("scope merge")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must forbid: scope merge"
        )

    def test_multi_annotation_merged_scope_string_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = (
                "concentrated-and-in-situ-merged"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract candidate scope must be exactly one "
            "allowlisted semantic scope: concentrated_annotation or "
            "in_situ_annotation"
        )

    def test_multi_annotation_combined_scope_string_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = (
                "concentrated_and_in_situ_combined"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract candidate scope must be exactly one "
            "allowlisted semantic scope: concentrated_annotation or "
            "in_situ_annotation"
        )

    def test_multi_annotation_scope_list_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = [
                "concentrated_annotation",
                "in_situ_annotation",
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract candidates must use separate scope and "
            "structured non-semantic candidate_hints"
        )

    def test_multi_annotation_unknown_scope_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = (
                "section_annotation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract candidate scope must be exactly one "
            "allowlisted semantic scope: concentrated_annotation or "
            "in_situ_annotation"
        )

    def test_multi_annotation_duplicate_same_scope_candidates_fail(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][1]["scope"] = (
                "concentrated_annotation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must not contain "
            "duplicate same-scope candidates"
        )

    def test_multi_annotation_missing_required_scope_role_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = (
                "in_situ_annotation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario is missing required "
            "scope role: concentrated_annotation"
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
