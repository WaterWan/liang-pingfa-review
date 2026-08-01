"""Strict canonical JSON and artifact-contract regression tests."""

from __future__ import annotations

import copy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from liang_pingfa_review.canonical import (
    CanonicalJsonError,
    attach_integrity,
    canonical_json_bytes,
    load_json_file,
    strict_json_loads,
    utc_now,
)
from liang_pingfa_review.contracts import (
    load_artifact,
    validate_artifact,
)
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.plan import generate_edit_plan, validate_plan_against_audit
from tests.support.synthetic_dxf import (
    build_synthetic_audit as audit_dxf_for_testing,
    create_fake_dwg,
    create_synthetic_dxf,
)
from tests.support.owned_files import install_non_windows_test_ownership


class CanonicalJsonTests(unittest.TestCase):
    """Exercise deterministic encoding and hostile JSON rejection."""

    def test_nfc_and_sorting_are_deterministic(self) -> None:
        decomposed = "e\u0301"
        encoded = canonical_json_bytes({"z": decomposed, "a": [1, 2]})
        self.assertEqual(encoded.decode("utf-8"), '{"a":[1,2],"z":"é"}')

    def test_duplicate_keys_and_non_finite_constants_are_rejected(self) -> None:
        with self.assertRaises(CanonicalJsonError):
            strict_json_loads('{"item":1,"item":2}')
        with self.assertRaises(CanonicalJsonError):
            strict_json_loads('{"value":NaN}')
        with self.assertRaises(CanonicalJsonError):
            strict_json_loads('{"value":"e\\u0301"}')


class ArtifactContractTests(unittest.TestCase):
    """Verify schemas, semantic gates, and forged-plan rejection."""

    def setUp(self) -> None:
        install_non_windows_test_ownership(self)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.dxf = self.root / "fixture.dxf"
        self.source = self.root / "source.dwg"
        create_synthetic_dxf(self.dxf)
        create_fake_dwg(self.source)
        self.audit = audit_dxf_for_testing(self.dxf, self.source)
        self.plan = generate_edit_plan(self.audit)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_unknown_key_and_invalid_status_fail_schema(self) -> None:
        unknown = copy.deepcopy(self.audit)
        unknown["unexpected"] = True
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("audit", attach_integrity(unknown))
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)

        invalid_status = copy.deepcopy(self.audit)
        invalid_status["findings"][0]["status"] = "unknown"
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("audit", attach_integrity(invalid_status))
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)

        unknown_version = copy.deepcopy(self.audit)
        unknown_version["schema_version"] = "liang-pingfa/audit/v9"
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("audit", attach_integrity(unknown_version))
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)

        unbound_target = copy.deepcopy(self.audit)
        non_actionable = next(
            finding
            for finding in unbound_target["findings"]
            if not finding["actionability"]
        )
        non_actionable["target_id"] = unbound_target["audited_targets"][0]["target_id"]
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("audit", attach_integrity(unbound_target))
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)

    def test_duplicate_key_file_is_rejected_before_schema_validation(self) -> None:
        artifact_path = self.root / "duplicate.json"
        artifact_path.write_text('{"schema_version":"x","schema_version":"y"}', encoding="utf-8")
        with self.assertRaises(PipelineError) as raised:
            load_artifact("audit", artifact_path)
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)
        with self.assertRaises(CanonicalJsonError):
            load_json_file(artifact_path)

    def test_forged_plan_cannot_match_audit(self) -> None:
        forged = copy.deepcopy(self.plan)
        forged["expected_after"]["full_manifest_digest"] = "0" * 64
        forged["expected_after"]["non_target_manifest_digest"] = "0" * 64
        forged = attach_integrity(forged)
        validate_artifact("plan", forged)
        with self.assertRaises(PipelineError) as raised:
            validate_plan_against_audit(self.audit, forged)
        self.assertEqual(raised.exception.code, ErrorCode.PLAN_AUDIT_MISMATCH)

    def test_duplicate_plan_target_is_a_stable_failure(self) -> None:
        duplicate = copy.deepcopy(self.plan)
        duplicate["operations"].append(copy.deepcopy(duplicate["operations"][0]))
        duplicate = attach_integrity(duplicate)
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("plan", duplicate)
        self.assertEqual(raised.exception.code, ErrorCode.DUPLICATE_TARGET)

    def test_expired_audit_is_rejected_by_apply_gate(self) -> None:
        created_at = utc_now() - timedelta(hours=25)
        old_audit = audit_dxf_for_testing(
            self.dxf,
            self.source,
            now=created_at,
        )
        with self.assertRaises(PipelineError) as raised:
            generate_edit_plan(old_audit)
        self.assertEqual(raised.exception.code, ErrorCode.STALE_AUDIT)

        # Audit validity is a gate, not a source of nondeterminism for fresh
        # audit inputs.
        self.assertEqual(
            generate_edit_plan(self.audit),
            generate_edit_plan(self.audit),
        )


if __name__ == "__main__":
    unittest.main()
