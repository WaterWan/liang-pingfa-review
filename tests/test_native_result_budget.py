"""Native operation/result transport-budget contract tests."""

from __future__ import annotations

from copy import deepcopy
import unittest

from liang_pingfa_review.canonical import attach_integrity, canonical_json_bytes
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.native_audit import build_native_audit
from liang_pingfa_review.native_contracts import (
    MAX_NATIVE_CONSOLE_RESULT_BYTES,
    MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES,
    MAX_NATIVE_OPERATION_COUNT,
    canonical_console_result_bytes,
    require_console_result_transport_budget,
    validate_native_contract,
)
from liang_pingfa_review.native_manifest import build_native_manifest
from liang_pingfa_review.native_plan import generate_native_plan
from liang_pingfa_review.native_protocol import derive_challenge_response
from liang_pingfa_review.native_verify import validate_console_result
from tests.support.synthetic_native import (
    config,
    digest,
    entity,
    geometry,
    intent,
    session,
)


def _operation_id(index: int) -> str:
    return "native-operation-" + f"{index:024x}"


def _renewed_session(value: dict) -> dict:
    renewed = deepcopy(value)
    renewed["session_id"] = "native-session-" + "f" * 32
    renewed["pid"] += 1
    renewed["process"]["instance_fingerprint"] = digest("budget-renewed-process")
    renewed["current_document"]["database_instance_fingerprint"] = digest(
        "budget-renewed-database"
    )
    renewed["current_document"]["revision_fingerprint"] = digest(
        "budget-renewed-revision"
    )
    renewed["challenge_response"] = derive_challenge_response(
        renewed["client_nonce"],
        renewed["challenge"],
        renewed["bridge_nonce"],
        session_id=renewed["session_id"],
    )
    return attach_integrity(renewed)


class NativeResultBudgetTests(unittest.TestCase):
    """Operation cardinality is derived from the bounded result envelope."""

    def _workflow(self, count: int) -> tuple[dict, dict]:
        native_config = config()
        native_config["operation_profiles"]["create_review_marker/v1"] = True
        native_config["marker_policy"]["enabled"] = True
        native_config["marker_policy"]["plugin_capability"] = True
        before = geometry([entity("10", layer="OTHER")])
        audited_session = session()
        audit = build_native_audit(before, audited_session, native_config)
        operations = [
            {
                "operation_id": _operation_id(index),
                "kind": "create_review_marker",
                "position": [
                    "3ff0000000000000",
                    "4000000000000000",
                    "0000000000000000",
                ],
            }
            for index in range(count)
        ]
        requested = intent(audit, operations=operations)
        plan = generate_native_plan(audit, requested, native_config)
        fresh_session = _renewed_session(audited_session)
        fresh_export = geometry(
            deepcopy(before["entities"]),
            source_value=deepcopy(before["source"]),
            session_value=fresh_session,
        )
        manifest = build_native_manifest(
            audit,
            plan,
            requested,
            fresh_export,
            fresh_session,
            native_config,
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "e" * 64,
            },
            output_path=__import__("pathlib").Path("budget-output.dwg"),
        )
        return manifest, requested

    @staticmethod
    def _result(manifest: dict, *, status: str) -> dict:
        result = {
            "schema_version": "liang-pingfa/native-console-result/v2",
            "run_id": "native-run-" + "a" * 32,
            "manifest_id": manifest["manifest_id"],
            "manifest_integrity_sha256": manifest["integrity"]["sha256"],
            "manifest_schema_version": manifest["schema_version"],
            "nonce": manifest["nonce"],
            "final_revision_fingerprint": "b" * 64,
            "final_revision_transition": "save_reopen_changed",
            "final_document_binding": {
                "database_instance_fingerprint": "c" * 64,
                "revision_fingerprint": "b" * 64,
                "output_copy_binding": {
                    "format": "DWG",
                    "sha256": digest(
                        "budget-actual-output-"
                        + manifest["expected_prewrite_output_copy_binding"]["sha256"]
                    ),
                    "byte_size": manifest[
                        "expected_prewrite_output_copy_binding"
                    ]["byte_size"]
                    + 1,
                    "path_fingerprint": manifest[
                        "expected_prewrite_output_copy_binding"
                    ]["path_fingerprint"],
                    "file_identity_fingerprint": digest(
                        "budget-actual-identity-"
                        + manifest["expected_prewrite_output_copy_binding"][
                            "file_identity_fingerprint"
                        ]
                    ),
                    "dwg_header_signature": manifest[
                        "expected_prewrite_output_copy_binding"
                    ]["dwg_header_signature"],
                },
            },
            "transaction": {
                "preflight": "passed",
                "outcome": "committed",
                "rollback": "not_required",
            },
            "operation_results": [
                {
                    "operation_id": operation["operation_id"],
                    "status": status,
                    "postcondition_digest": digest(
                        "budget-postcondition-" + operation["operation_id"]
                    ),
                }
                for operation in manifest["operations"]
            ],
        }
        return attach_integrity(result)

    def test_maximum_and_623_operation_results_fit_reader_budget(self) -> None:
        manifest_623, _ = self._workflow(623)
        result_623 = self._result(manifest_623, status="applied")
        # Use exact expected postconditions for the successful cross-language
        # shape, rather than relying on item count as a transport proxy.
        for item, operation in zip(
            result_623["operation_results"],
            manifest_623["operations"],
            strict=True,
        ):
            item["postcondition_digest"] = __import__(
                "liang_pingfa_review.canonical",
                fromlist=["canonical_sha256"],
            ).canonical_sha256(operation)
        result_623 = attach_integrity(result_623)
        self.assertEqual(
            result_623,
            validate_console_result(
                manifest_623,
                result_623,
                run_id=result_623["run_id"],
            ),
        )

        manifest_max, _ = self._workflow(MAX_NATIVE_OPERATION_COUNT)
        self.assertTrue(
            all(
                len(operation["operation_id"]) == len("native-operation-") + 24
                for operation in manifest_max["operations"]
            )
        )
        failure_results = [
            validate_native_contract(
                "console_result",
                self._result(manifest_max, status=status),
            )
            for status in ("rejected", "rolled_back")
        ]
        for checked_failure in failure_results:
            self.assertEqual(
                MAX_NATIVE_OPERATION_COUNT,
                len(checked_failure["operation_results"]),
            )
        for result in (result_623, *failure_results):
            with self.subTest(status=result["operation_results"][0]["status"]):
                encoded = canonical_console_result_bytes(result)
                self.assertLessEqual(len(encoded), MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES)
                self.assertLess(len(encoded), MAX_NATIVE_CONSOLE_RESULT_BYTES)

    def test_max_plus_one_and_two_thousand_operations_reject_before_manifest(self) -> None:
        manifest, requested = self._workflow(1)
        base = deepcopy(requested)
        audit_binding = base["audit_binding"]
        for count in (MAX_NATIVE_OPERATION_COUNT + 1, 2000):
            with self.subTest(count=count):
                oversized = {
                    "schema_version": "liang-pingfa/native-edit-intent/v2",
                    "intent_id": "native-intent-" + digest(f"budget-{count}")[:32],
                    "created_at": base["created_at"],
                    "audit_binding": audit_binding,
                    "operations": [
                        {
                            "operation_id": _operation_id(index),
                            "kind": "create_review_marker",
                            "position": [
                                "3ff0000000000000",
                                "4000000000000000",
                                "0000000000000000",
                            ],
                        }
                        for index in range(count)
                    ],
                }
                with self.assertRaises(PipelineError) as raised:
                    validate_native_contract("intent", attach_integrity(oversized))
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.NATIVE_INTENT_SCHEMA_INVALID,
                )
        self.assertEqual(1, len(manifest["operations"]))

    def test_canonical_budget_has_an_exact_boundary_and_long_ids_are_invalid(self) -> None:
        chunks: list[str] = []
        exact = {"padding": chunks}
        while len(canonical_json_bytes(exact)) < MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES:
            current = len(canonical_json_bytes(exact))
            # Adding one array string costs its UTF-8 contents, two quotes,
            # and one comma except for the first element.
            overhead = 2 + (1 if chunks else 0)
            length = min(
                65_536,
                MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES - current - overhead,
            )
            self.assertGreater(length, 0)
            chunks.append("x" * length)
        self.assertEqual(
            MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES,
            len(canonical_json_bytes(exact)),
        )
        require_console_result_transport_budget(exact)
        with self.assertRaises(ValueError):
            require_console_result_transport_budget({"padding": [*chunks, "x"]})

        manifest, requested = self._workflow(1)
        malformed = deepcopy(requested)
        malformed["operations"][0]["operation_id"] = "native-operation-" + "a" * 25
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("intent", attach_integrity(malformed))
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_INTENT_SCHEMA_INVALID)
        self.assertEqual(1, len(manifest["operations"]))


if __name__ == "__main__":
    unittest.main()
