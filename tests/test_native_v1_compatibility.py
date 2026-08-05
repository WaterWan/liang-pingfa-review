"""Frozen native v1 fixtures, hashes, dispatch, and execution-gate tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import unittest

from liang_pingfa_review.canonical import attach_integrity
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.native_apply import native_apply
from liang_pingfa_review.native_audit import require_fresh_native_audit
from liang_pingfa_review.native_bridge import (
    NativeBridgeClient,
    prepare_native_session,
    write_private_native_session_descriptor,
)
from liang_pingfa_review.native_contracts import (
    is_active_native_contract,
    migrate_native_v1_to_v2,
    require_active_native_contract,
    schema_for_native,
    validate_native_contract,
)
from liang_pingfa_review.native_manifest import (
    require_final_output_binding,
    require_fresh_native_manifest,
)
from liang_pingfa_review.native_verify import (
    require_published_output_binding,
    validate_console_result,
    verify_native_transition,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "native-v1"
BASELINE_MAP = ROOT / "tests" / "contracts" / "native-v1-baseline.json"
FIXTURE_KINDS = {
    "config": "config",
    "session": "session",
    "geometry": "geometry",
    "audit": "audit",
    "intent": "intent",
    "plan": "plan",
    "manifest": "manifest",
    "console-result": "console_result",
    "console-export": "console_export",
    "verification": "verification",
}
FIXTURE_SESSION_NOW = datetime(2030, 1, 1, 0, 1, tzinfo=UTC)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class NativeV1CompatibilityTests(unittest.TestCase):
    """Prove v1 remains immutable/readable while all active paths require v2."""

    def test_frozen_baseline_hashes_match_current_v1_bytes(self) -> None:
        baseline = json.loads(BASELINE_MAP.read_text(encoding="utf-8"))
        self.assertEqual(
            baseline["baseline_commit"],
            "c374e6c61fffaf6f487dd81544923a3072e293dc",
        )
        self.assertRegex(
            baseline["v1_public_api_signature_sha256"],
            r"^[a-f0-9]{64}$",
        )
        for relative, expected in baseline["artifacts"].items():
            with self.subTest(path=relative):
                payload = (ROOT / relative).read_bytes()
                self.assertEqual(
                    sha256(payload).hexdigest(),
                    expected["baseline_sha256"],
                )
                self.assertRegex(expected["introduced_commit"], r"^[a-f0-9]{40}$")
        for relative in baseline["absent_from_baseline"]:
            self.assertFalse((ROOT / relative).exists())

    def test_historical_source_free_fixtures_validate_under_v1(self) -> None:
        for name, kind in FIXTURE_KINDS.items():
            with self.subTest(fixture=name):
                fixture = _fixture(name)
                checked = validate_native_contract(
                    kind,
                    fixture,
                    now=FIXTURE_SESSION_NOW if kind == "session" else None,
                )
                self.assertEqual(checked, fixture)
                self.assertFalse(is_active_native_contract(kind, checked))

    def test_v1_rejects_v2_fields_and_renamed_versions(self) -> None:
        audit = _fixture("audit")
        audit["stable_host_binding_digest"] = "a" * 64
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("audit", attach_integrity(audit))
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_AUDIT_SCHEMA_INVALID)

        renamed = _fixture("audit")
        renamed["schema_version"] = "liang-pingfa/native-audit/v2"
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("audit", attach_integrity(renamed))
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_AUDIT_SCHEMA_INVALID)

    def test_v2_requires_its_new_security_bindings(self) -> None:
        config = migrate_native_v1_to_v2("config", _fixture("config"))
        session = _fixture("session")
        session["schema_version"] = "liang-pingfa/native-bridge-session/v2"
        session["config_schema_version"] = config["schema_version"]
        session["monotonic_clock"] = "windows-gettickcount64-ms/v1"
        session["monotonic_boot_id"] = "a" * 32
        session["monotonic_issued"] = "1000000"
        session["monotonic_expires"] = "1300000"
        session = attach_integrity(session)
        # A v2 descriptor itself is structurally complete at this point.
        self.assertEqual(
            validate_native_contract("session", session, now=FIXTURE_SESSION_NOW),
            session,
        )
        missing = deepcopy(session)
        missing.pop("monotonic_boot_id")
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract(
                "session",
                attach_integrity(missing),
                now=FIXTURE_SESSION_NOW,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)

    def test_only_configuration_can_be_explicitly_migrated(self) -> None:
        migrated = migrate_native_v1_to_v2("config", _fixture("config"))
        self.assertEqual(
            migrated["schema_version"],
            "liang-pingfa/native-adapter-config/v2",
        )
        for name, kind in FIXTURE_KINDS.items():
            if kind == "config":
                continue
            with self.subTest(kind=kind):
                with self.assertRaises(PipelineError) as raised:
                    migrate_native_v1_to_v2(kind, _fixture(name))
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.NATIVE_LEGACY_ARTIFACT_READ_ONLY,
                )

    def test_v1_keeps_its_published_two_thousand_operation_read_limit(self) -> None:
        legacy = _fixture("intent")
        legacy["operations"] = [
            {
                "operation_id": f"native-operation-{index:024x}",
                "kind": "create_review_marker",
                "position": [
                    "3ff0000000000000",
                    "4000000000000000",
                    "0000000000000000",
                ],
            }
            for index in range(1_025)
        ]
        checked = validate_native_contract("intent", attach_integrity(legacy))
        self.assertEqual(1_025, len(checked["operations"]))
        self.assertEqual(
            2_000,
            schema_for_native(
                "intent",
                schema_version="liang-pingfa/native-edit-intent/v1",
            )["properties"]["operations"]["maxItems"],
        )

    def test_execution_boundaries_reject_legacy_before_platform_work(self) -> None:
        config = _fixture("config")
        audit = _fixture("audit")
        intent = _fixture("intent")
        plan = _fixture("plan")
        manifest = _fixture("manifest")
        result = _fixture("console-result")
        export = _fixture("console-export")
        verification = _fixture("verification")
        geometry = _fixture("geometry")
        session = _fixture("session")

        gates = (
            lambda: require_active_native_contract("audit", audit),
            lambda: require_fresh_native_audit(audit),
            lambda: require_fresh_native_manifest(manifest),
            lambda: require_final_output_binding(manifest, geometry["source"]),
            lambda: validate_console_result(
                manifest,
                result,
                run_id=result["run_id"],
            ),
            lambda: verify_native_transition(manifest, geometry),
            lambda: require_published_output_binding(
                verification,
                verification["output_binding"],
            ),
            lambda: NativeBridgeClient(session, config=migrate_native_v1_to_v2("config", config)),
            lambda: prepare_native_session(
                pid=1234,
                pipe_name=(
                    chr(92) * 2
                    + "."
                    + chr(92)
                    + "pipe"
                    + chr(92)
                    + "liang-pingfa-native-a1b2c3d4e5f6g7h8"
                ),
                config=config,
            ),
            lambda: write_private_native_session_descriptor(
                Path("legacy-session.json"),
                session,
            ),
            lambda: native_apply(
                Path("legacy-input.dwg"),
                session,
                audit,
                plan,
                intent,
                config,
                confirm_plan=plan["plan_id"],
                output_path=Path("legacy-output.dwg"),
                verification_path=Path("legacy-verification.json"),
            ),
        )
        for gate in gates:
            with self.subTest(gate=gate):
                with self.assertRaises(PipelineError) as raised:
                    gate()
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.NATIVE_LEGACY_ARTIFACT_READ_ONLY,
                )


if __name__ == "__main__":
    unittest.main()
