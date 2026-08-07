"""Native audit/intent/plan/manifest and exact transition tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import unittest

from liang_pingfa_review.canonical import attach_integrity, canonical_json_bytes, canonical_sha256, format_utc, utc_now
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.native_audit import build_native_audit
from liang_pingfa_review.native_contracts import (
    bits_from_float,
    bits_vector,
    derive_native_target_id,
    geometry_document_binding_digest,
    prewrite_semantic_projection,
    prewrite_semantic_projection_digest,
    require_qualification_host_binding,
    strict_native_json,
    validate_native_contract,
)
from liang_pingfa_review.native_manifest import build_native_manifest
from liang_pingfa_review.native_plan import (
    generate_native_plan,
    generate_qualification_translation_intent,
)
from liang_pingfa_review.native_protocol import derive_challenge_response
from liang_pingfa_review.native_verify import (
    build_native_verification,
    geometry_from_console_export,
    validate_console_result,
    verify_native_transition,
)
from tests.support.synthetic_native import config, digest, entity, geometry, intent, session, source


def _rehash_entity(value: dict[str, object]) -> None:
    projection = dict(value)
    projection.pop("geometry_fingerprint", None)
    projection.pop("opaque_state_digest", None)
    value["geometry_fingerprint"] = canonical_sha256({"geometry": projection})
    value["opaque_state_digest"] = canonical_sha256({"opaque_state": projection})


def _marker_from_operation(operation: dict, handle: str) -> dict:
    """Build one generated post-save marker matching its private operation."""

    position = tuple(bits_vector(operation["position"]))
    marker = entity(
        handle,
        sequence_index=operation["sequence_index"],
        layer=operation["layer"],
        text=operation["marker_text"],
        style=operation["style"],
        position=position,
    )
    marker["owner_handle"] = operation["owner_handle"]
    marker["space"] = deepcopy(operation["space"])
    marker["block_path"] = list(operation["block_path"])
    marker["height"] = operation["height"]
    marker["rotation"] = operation["rotation"]
    marker["overlay_evidence"] = deepcopy(operation["overlay_evidence"])
    marker["position"] = list(operation["position"])
    marker["bounds"] = {
        "minimum": list(operation["position"]),
        "maximum": list(operation["position"]),
    }
    marker["segments"] = []
    _rehash_entity(marker)
    return marker


def _renewed_session(value: dict, token: str = "f") -> dict:
    """Produce a distinct reopened host session with the same stable host."""

    renewed = deepcopy(value)
    renewed["session_id"] = "native-session-" + token * 32
    renewed["pid"] += 1
    renewed["pipe_name"] = (
        chr(92) * 2
        + "."
        + chr(92)
        + "pipe"
        + chr(92)
        + "liang-pingfa-native-z9"
        + token * 30
    )
    renewed["client_nonce"] = token * 43
    renewed["challenge"] = ("g" if token != "g" else "h") * 43
    renewed["bridge_nonce"] = ("h" if token != "h" else "i") * 43
    renewed["challenge_response"] = derive_challenge_response(
        renewed["client_nonce"],
        renewed["challenge"],
        renewed["bridge_nonce"],
        session_id=renewed["session_id"],
    )
    renewed["process"]["instance_fingerprint"] = canonical_sha256(
        {"renewed_session": renewed["session_id"]}
    )
    renewed["current_document"]["database_instance_fingerprint"] = canonical_sha256(
        {"renewed_database": renewed["session_id"]}
    )
    renewed["current_document"]["revision_fingerprint"] = canonical_sha256(
        {"renewed_revision": renewed["session_id"]}
    )
    return attach_integrity(renewed)


def _fresh_export(before: dict, fresh_session: dict) -> dict:
    return geometry(
        deepcopy(before["entities"]),
        containers=deepcopy(before["containers"]),
        source_value=deepcopy(before["source"]),
        session_value=fresh_session,
    )


def _actual_output_binding(manifest: dict) -> dict:
    """Derive deterministic generated post-save bytes, never a prediction."""

    prewrite = manifest["expected_prewrite_output_copy_binding"]
    return {
        "format": "DWG",
        "sha256": canonical_sha256(
            {
                "generated_actual_output": prewrite["sha256"],
                "operations": manifest["operations"],
            }
        ),
        "byte_size": prewrite["byte_size"] + 1,
        "path_fingerprint": prewrite["path_fingerprint"],
        "file_identity_fingerprint": canonical_sha256(
            {
                "generated_actual_identity": prewrite[
                    "file_identity_fingerprint"
                ],
                "operations": manifest["operations"],
            }
        ),
        "dwg_header_signature": prewrite["dwg_header_signature"],
    }


def _final_export(
    manifest: dict,
    entities: list[dict],
    **kwargs: object,
) -> dict:
    """Build generated readback evidence from an observed constrained output."""

    preconditions = strict_native_json(manifest["preconditions_geometry_json"])
    containers = deepcopy(preconditions["containers"])
    for operation in manifest["operations"]:
        if operation["kind"] != "create_review_marker":
            continue
        matching = [
            container
            for container in containers
            if (
                container["owner_handle"] == operation["owner_handle"]
                and container["space"] == operation["space"]
                and container["block_path"] == operation["block_path"]
            )
        ]
        if len(matching) != 1:
            raise AssertionError("generated marker has no unique physical container")
        matching[0]["physical_slot_count"] += 1
    # Malformed readback scenarios still need a schema-valid physical extent
    # so the transition verifier—not a malformed helper—reports their
    # count/index drift. Legitimate transitions never take this branch beyond
    # the exact marker increments above.
    for entity_value in entities:
        matching = [
            container
            for container in containers
            if (
                container["space"] == entity_value["space"]
                and container["block_path"] == entity_value["block_path"]
            )
        ]
        if len(matching) == 1:
            matching[0]["physical_slot_count"] = max(
                matching[0]["physical_slot_count"],
                entity_value["sequence_index"] + 1,
            )
        elif not matching:
            containers.append(
                {
                    "owner_handle": entity_value["owner_handle"],
                    "space": deepcopy(entity_value["space"]),
                    "block_path": list(entity_value["block_path"]),
                    "physical_slot_count": entity_value["sequence_index"] + 1,
                }
            )
    return geometry(
        entities,
        containers=containers,
        source_value=_actual_output_binding(manifest),
        **kwargs,
    )


class NativeAuditPlanTests(unittest.TestCase):
    """Test generated mocks only; no drawing, SDK, or plugin is used."""

    def test_audit_binds_the_full_host_identity_and_validated_config(self) -> None:
        """Qualification consumes these private bindings before it creates a plan."""

        native_config = config()
        audit = build_native_audit(geometry(), session(), native_config)
        self.assertEqual(
            native_config["full_host"]["sha256"],
            audit["host_executable_fingerprint"],
        )
        self.assertEqual(
            {
                "product": native_config["host_compatibility"]["host_product"],
                "release": native_config["host_compatibility"]["host_release"],
                "runtime": native_config["host_compatibility"]["host_runtime"],
                "mode": native_config["host_compatibility"]["audit_host_mode"],
            },
            audit["audited_host_identity"],
        )
        self.assertEqual(canonical_sha256(native_config), audit["config_fingerprint"])

    def test_qualification_host_binding_rejects_drift_and_identity_mismatch(self) -> None:
        """Generated evidence covers every fail-closed real-host handoff case."""

        native_config = config()
        audit = build_native_audit(geometry(), session(), native_config)
        expected_hash = native_config["full_host"]["sha256"]
        require_qualification_host_binding(
            audit,
            native_config,
            host_executable_sha256=expected_hash,
            profile=native_config["adapter"]["profile"],
        )
        for label, candidate_audit, candidate_config, candidate_hash, profile in (
            (
                "wrong-host-path-or-hash",
                audit,
                native_config,
                digest("different-retained-host"),
                native_config["adapter"]["profile"],
            ),
            (
                "audit-fingerprint-unavailable",
                attach_integrity({**audit, "host_executable_fingerprint": "unavailable"}),
                native_config,
                expected_hash,
                native_config["adapter"]["profile"],
            ),
            (
                "audit-missing-host-binding",
                {key: value for key, value in audit.items() if key != "host_executable_fingerprint"},
                native_config,
                expected_hash,
                native_config["adapter"]["profile"],
            ),
            (
                "profile-mismatch",
                audit,
                native_config,
                expected_hash,
                "different-profile",
            ),
            (
                "runtime-mismatch",
                audit,
                {
                    **native_config,
                    "host_compatibility": {
                        **native_config["host_compatibility"],
                        "host_runtime": "different-runtime",
                    },
                },
                expected_hash,
                native_config["adapter"]["profile"],
            ),
        ):
            with self.subTest(label=label):
                with self.assertRaises(PipelineError) as raised:
                    require_qualification_host_binding(
                        candidate_audit,
                        candidate_config,
                        host_executable_sha256=candidate_hash,
                        profile=profile,
                    )
                self.assertIn(
                    raised.exception.code,
                    {
                        ErrorCode.NATIVE_AUDIT_SCHEMA_INVALID,
                        ErrorCode.NATIVE_CAPABILITY_MISMATCH,
                    },
                )

    def _translation_workflow(self) -> tuple[dict, dict, dict, dict, dict]:
        before = geometry([entity("10", text="generated-private-text")])
        read_session = session()
        audit = build_native_audit(before, read_session, config())
        target = audit["records"][0]["target_id"]
        private_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "1" * 24,
                    "kind": "translate_dbtext",
                    "target_id": target,
                    "delta": [
                        bits_from_float(5.0),
                        bits_from_float(-2.0),
                        bits_from_float(0.0),
                    ],
                }
            ],
        )
        native_config = config()
        plan = generate_native_plan(audit, private_intent, native_config)
        fresh_session = _renewed_session(read_session)
        manifest = build_native_manifest(
            audit,
            plan,
            private_intent,
            _fresh_export(before, fresh_session),
            fresh_session,
            native_config,
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "f" * 64,
            },
            output_path=__import__("pathlib").Path("generated-output.dwg"),
        )
        return before, audit, private_intent, plan, manifest

    def test_generated_qualification_intent_is_one_safe_translation(self) -> None:
        before = geometry([entity("10", text="generated-private-text")])
        audit = build_native_audit(before, session(), config())
        generated = generate_qualification_translation_intent(audit)
        self.assertEqual(generated["created_at"], audit["created_at"])
        self.assertEqual(len(generated["operations"]), 1)
        operation = generated["operations"][0]
        self.assertEqual(operation["kind"], "translate_dbtext")
        self.assertIn(
            operation["target_id"],
            {record["target_id"] for record in audit["records"]},
        )
        self.assertEqual(
            operation["delta"],
            ["3ff0000000000000", "0000000000000000", "0000000000000000"],
        )
        self.assertEqual(
            generated,
            generate_qualification_translation_intent(audit),
        )

    def test_plan_is_deterministic_and_manifest_has_private_exact_preconditions(self) -> None:
        before, audit, private_intent, plan, manifest = self._translation_workflow()
        self.assertEqual(
            plan,
            generate_native_plan(audit, private_intent, config()),
        )
        self.assertIn("preconditions_geometry_json", manifest)
        self.assertEqual(
            plan["geometry_document_binding_digest"],
            audit["geometry_document_binding_digest"],
        )
        embedded = strict_native_json(manifest["preconditions_geometry_json"])
        self.assertEqual(
            embedded["binding"]["session_binding_digest"],
            manifest["session_renewal"]["fresh_session_binding"],
        )
        self.assertNotIn("generated-private-text", str(plan))
        self.assertNotIn("generated-private-text", str(audit))
        self.assertIn("generated-private-text", manifest["preconditions_geometry_json"])

    def test_portable_prewrite_projection_survives_copy_and_rejects_drift(self) -> None:
        """Only source/database instance identity may differ across contexts."""

        before, _audit, _intent, _plan, manifest = self._translation_workflow()
        bridge_projection = prewrite_semantic_projection(before)
        self.assertEqual(
            bridge_projection,
            before["portable_prewrite_projection"],
        )
        self.assertEqual(
            prewrite_semantic_projection_digest(before),
            before["portable_prewrite_projection_digest"],
        )
        copied_context = deepcopy(before)
        copied_context["source"]["path_fingerprint"] = digest("private-copy-path")
        copied_context["source"]["file_identity_fingerprint"] = digest(
            "private-copy-identity"
        )
        copied_context["document"]["database_instance_fingerprint"] = digest(
            "core-console-database"
        )
        copied_context["document"]["revision_fingerprint"] = digest(
            "core-console-revision"
        )
        copied_context["binding"]["session_id"] = "native-session-" + "e" * 32
        self.assertEqual(bridge_projection, prewrite_semantic_projection(copied_context))
        self.assertEqual(
            prewrite_semantic_projection_digest(before),
            prewrite_semantic_projection_digest(copied_context),
        )

        embedded = strict_native_json(manifest["preconditions_geometry_json"])
        prewrite = manifest["expected_prewrite_revision"]
        self.assertEqual(
            embedded["source"],
            manifest["expected_prewrite_output_copy_binding"],
        )
        self.assertEqual(
            {
                "database_instance_fingerprint": embedded["document"][
                    "database_instance_fingerprint"
                ],
                "revision_fingerprint": embedded["document"]["revision_fingerprint"],
            },
            prewrite["bridge_document_identity"],
        )
        self.assertEqual(
            prewrite_semantic_projection(embedded),
            prewrite["portable_prewrite_projection"],
        )
        self.assertEqual(
            prewrite_semantic_projection_digest(embedded),
            prewrite["portable_prewrite_projection_digest"],
        )

        for field in (
            "geometry_digest",
            "protected_semantic_digest",
            "table_state_digest",
        ):
            with self.subTest(field=field):
                forged = deepcopy(manifest)
                forged["expected_prewrite_revision"][
                    "portable_prewrite_projection"
                ][field] = digest("forged-" + field)
                forged = attach_integrity(forged)
                with self.assertRaises(PipelineError) as raised:
                    validate_native_contract("manifest", forged)
                self.assertEqual(
                    ErrorCode.NATIVE_MANIFEST_INVALID,
                    raised.exception.code,
                )

    def test_stale_audit_and_disabled_marker_fail_closed(self) -> None:
        before = geometry()
        audit = build_native_audit(before, session(), config())
        target = audit["records"][0]["target_id"]
        translation = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "2" * 24,
                    "kind": "translate_dbtext",
                    "target_id": target,
                    "delta": [bits_from_float(1), bits_from_float(0), bits_from_float(0)],
                }
            ],
        )
        stale = deepcopy(audit)
        stale["created_at"] = format_utc(utc_now() - timedelta(minutes=20))
        stale["expires_at"] = format_utc(utc_now() - timedelta(minutes=5))
        stale = attach_integrity(stale)
        with self.assertRaises(PipelineError) as raised:
            generate_native_plan(stale, translation, config())
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)

        marker_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "3" * 24,
                    "kind": "create_review_marker",
                    "position": [bits_from_float(1), bits_from_float(1), bits_from_float(0)],
                }
            ],
        )
        with self.assertRaises(PipelineError) as raised:
            generate_native_plan(audit, marker_intent, config())
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_OPERATION_INVALID)

    def test_unavailable_host_fingerprint_allows_audit_but_blocks_write_plan(self) -> None:
        before = geometry()
        audit = build_native_audit(before, session(), config())
        target = audit["records"][0]["target_id"]
        private_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "7" * 24,
                    "kind": "translate_dbtext",
                    "target_id": target,
                    "delta": [bits_from_float(1), bits_from_float(0), bits_from_float(0)],
                }
            ],
        )
        unavailable = deepcopy(audit)
        unavailable["host_executable_fingerprint"] = "unavailable"
        unavailable = attach_integrity(unavailable)
        with self.assertRaises(PipelineError) as raised:
            generate_native_plan(unavailable, private_intent, config())
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CAPABILITY_MISMATCH)

    def test_exact_translate_readback_and_unplanned_change_rejection(self) -> None:
        before, _audit, _intent, _plan, manifest = self._translation_workflow()
        changed = deepcopy(before["entities"][0])
        changed["position"] = [bits_from_float(6), bits_from_float(0), bits_from_float(0)]
        changed["bounds"] = {
            "minimum": [bits_from_float(6), bits_from_float(0), bits_from_float(0)],
            "maximum": [bits_from_float(6), bits_from_float(0), bits_from_float(0)],
        }
        _rehash_entity(changed)
        after = _final_export(
            manifest,
            [changed],
            database_instance=digest("final-database"),
            revision=digest("final-revision"),
        )
        result = verify_native_transition(manifest, after)
        self.assertEqual(result[0]["kind"], "translate_dbtext")

        unplanned = deepcopy(changed)
        unplanned["text"] = "tampered"
        _rehash_entity(unplanned)
        with self.assertRaises(PipelineError) as raised:
            verify_native_transition(manifest, _final_export(manifest, [unplanned]))
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)

    def test_exact_delete_readback_allows_only_the_audited_target_absence(self) -> None:
        before = geometry([entity("10", layer="textarea", text="generated-overlay")])
        audit_session = session()
        audit = build_native_audit(before, audit_session, config())
        record = next(
            item
            for item in audit["records"]
            if "delete_auxiliary_overlay_text/v1" in item["eligible_profiles"]
        )
        private_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "8" * 24,
                    "kind": "delete_auxiliary_overlay_text",
                    "target_id": record["target_id"],
                }
            ],
        )
        plan = generate_native_plan(audit, private_intent, config())
        fresh_session = _renewed_session(audit_session)
        manifest = build_native_manifest(
            audit,
            plan,
            private_intent,
            _fresh_export(before, fresh_session),
            fresh_session,
            config(),
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "a" * 64,
            },
            output_path=__import__("pathlib").Path("generated-delete.dwg"),
        )
        result = verify_native_transition(manifest, _final_export(manifest, []))
        self.assertEqual(result[0]["kind"], "delete_auxiliary_overlay_text")

    def test_marker_requires_exact_operation_derived_record(self) -> None:
        before = geometry([entity("10", layer="OTHER")])
        audit_session = session()
        native_config = config()
        native_config["operation_profiles"]["create_review_marker/v1"] = True
        native_config["marker_policy"]["enabled"] = True
        native_config["marker_policy"]["plugin_capability"] = True
        audit = build_native_audit(before, audit_session, native_config)
        private_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "9" * 24,
                    "kind": "create_review_marker",
                    "position": [bits_from_float(7), bits_from_float(8), bits_from_float(0)],
                }
            ],
        )
        plan = generate_native_plan(audit, private_intent, native_config)
        fresh_session = _renewed_session(audit_session)
        manifest = build_native_manifest(
            audit,
            plan,
            private_intent,
            _fresh_export(before, fresh_session),
            fresh_session,
            native_config,
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "b" * 64,
            },
            output_path=__import__("pathlib").Path("generated-marker.dwg"),
        )
        marker = _marker_from_operation(manifest["operations"][0], "20")
        after = _final_export(manifest, [before["entities"][0], marker])
        self.assertEqual(
            verify_native_transition(manifest, after)[0]["kind"],
            "create_review_marker",
        )
        malformed = deepcopy(marker)
        malformed["text"] = "LPF-REVIEW-" + "a" * 24
        _rehash_entity(malformed)
        with self.assertRaises(PipelineError) as raised:
            verify_native_transition(
                manifest,
                _final_export(manifest, [before["entities"][0], malformed]),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)

    def test_physical_slot_counts_bind_trailing_internal_gaps_and_markers(self) -> None:
        """Markers reserve erased-inclusive Modelspace slots, not active maxima."""

        before = geometry(
            [
                entity("10", sequence_index=0, layer="OTHER"),
                entity("11", sequence_index=3, layer="OTHER", text="later"),
                entity("20", sequence_index=0, space_kind="paperspace", layer="OTHER"),
            ],
            containers=[
                {
                    "owner_handle": "AA",
                    "space": {
                        "kind": "modelspace",
                        "layout_handle": "BB",
                        "block_handle": None,
                    },
                    "block_path": [],
                    # Active records occupy 0 and 3. Slots 1–2 are internal
                    # gaps and 4–7 are trailing erased slots.
                    "physical_slot_count": 8,
                },
                {
                    "owner_handle": "AA",
                    "space": {
                        "kind": "paperspace",
                        "layout_handle": "BB",
                        "block_handle": None,
                    },
                    "block_path": [],
                    "physical_slot_count": 4,
                },
            ],
        )
        self.assertEqual(8, before["containers"][0]["physical_slot_count"])
        self.assertLess(
            max(item["sequence_index"] for item in before["entities"] if item["space"]["kind"] == "modelspace"),
            before["containers"][0]["physical_slot_count"],
        )
        native_config = config()
        native_config["operation_profiles"]["create_review_marker/v1"] = True
        native_config["marker_policy"]["enabled"] = True
        native_config["marker_policy"]["plugin_capability"] = True
        audited_session = session()
        audit = build_native_audit(before, audited_session, native_config)
        requested = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "a" * 24,
                    "kind": "create_review_marker",
                    "position": [bits_from_float(7), bits_from_float(8), bits_from_float(0)],
                },
                {
                    "operation_id": "native-operation-" + "b" * 24,
                    "kind": "create_review_marker",
                    "position": [bits_from_float(9), bits_from_float(8), bits_from_float(0)],
                },
            ],
        )
        plan = generate_native_plan(audit, requested, native_config)
        fresh_session = _renewed_session(audited_session, "e")
        manifest = build_native_manifest(
            audit,
            plan,
            requested,
            _fresh_export(before, fresh_session),
            fresh_session,
            native_config,
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "c" * 64,
            },
            output_path=__import__("pathlib").Path("physical-slots.dwg"),
        )
        operations = manifest["operations"]
        self.assertEqual([8, 9], [item["sequence_index"] for item in operations])
        first = _marker_from_operation(operations[0], "30")
        second = _marker_from_operation(operations[1], "31")
        after = _final_export(
            manifest,
            [*before["entities"], first, second],
        )
        self.assertEqual(
            [10, 4],
            [
                container["physical_slot_count"]
                for container in after["containers"]
            ],
        )
        self.assertEqual(
            ["create_review_marker", "create_review_marker"],
            [item["kind"] for item in verify_native_transition(manifest, after)],
        )

        # A high-but-schema-valid forged count changes every v2 bound digest,
        # so it cannot satisfy the older audit/plan preconditions.
        forged_containers = deepcopy(before["containers"])
        forged_containers[0]["physical_slot_count"] = 9
        forged_fresh = geometry(
            deepcopy(before["entities"]),
            containers=forged_containers,
            source_value=deepcopy(before["source"]),
            session_value=fresh_session,
        )
        with self.assertRaises(PipelineError) as raised:
            build_native_manifest(
                audit,
                plan,
                requested,
                forged_fresh,
                fresh_session,
                native_config,
                private_source_copy={
                    "sha256": before["source"]["sha256"],
                    "byte_size": before["source"]["byte_size"],
                    "file_identity_fingerprint": "d" * 64,
                },
                output_path=__import__("pathlib").Path("forged-physical-slots.dwg"),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_DOCUMENT_CHANGED)

        drifted_containers = deepcopy(after["containers"])
        drifted_containers[0]["physical_slot_count"] += 1
        drifted = geometry(
            deepcopy(after["entities"]),
            containers=drifted_containers,
            source_value=deepcopy(after["source"]),
            database_instance=after["document"]["database_instance_fingerprint"],
            revision=after["document"]["revision_fingerprint"],
        )
        with self.assertRaises(PipelineError) as raised:
            verify_native_transition(manifest, drifted)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)

    def test_every_marker_policy_field_is_audit_plan_manifest_bound(self) -> None:
        """No later configuration can alter a marker after audit or planning."""

        before = geometry([entity("10", layer="OTHER")])
        audited_config = config()
        audited_config["operation_profiles"]["create_review_marker/v1"] = True
        audited_config["marker_policy"]["enabled"] = True
        audited_config["marker_policy"]["plugin_capability"] = True
        read_session = session()
        audit = build_native_audit(before, read_session, audited_config)
        private_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "d" * 24,
                    "kind": "create_review_marker",
                    "position": [
                        bits_from_float(3),
                        bits_from_float(4),
                        bits_from_float(0),
                    ],
                }
            ],
        )
        plan = generate_native_plan(audit, private_intent, audited_config)
        fresh = _renewed_session(read_session, "d")
        export = _fresh_export(before, fresh)
        private_copy = {
            "sha256": before["source"]["sha256"],
            "byte_size": before["source"]["byte_size"],
            "file_identity_fingerprint": "f" * 64,
        }
        manifest = build_native_manifest(
            audit,
            plan,
            private_intent,
            export,
            fresh,
            audited_config,
            private_source_copy=private_copy,
            output_path=__import__("pathlib").Path("marker-binding.dwg"),
        )
        self.assertEqual(plan["marker_policy_binding"], audit["marker_policy_binding"])
        self.assertEqual(
            manifest["marker_policy_binding"],
            audit["marker_policy_binding"],
        )
        self.assertEqual(plan["record_cardinality"], "explicit_private")
        self.assertEqual(manifest["record_cardinality"], "explicit_private")

        mutations = {
            "height": lambda value: value["marker_policy"].update(
                {"height_bits": bits_from_float(3.0)}
            ),
            "rotation": lambda value: value["marker_policy"].update(
                {"rotation_bits": bits_from_float(0.5)}
            ),
            "layer": lambda value: value["marker_policy"].update(
                {"layer": "REVIEW-ALT"}
            ),
            "layer-fingerprint": lambda value: value["marker_policy"].update(
                {"layer_fingerprint": digest("other-marker-layer")}
            ),
            "style": lambda value: value["marker_policy"].update(
                {"style": "ALT"}
            ),
            "style-fingerprint": lambda value: value["marker_policy"].update(
                {"style_fingerprint": digest("other-marker-style")}
            ),
            "enabled": lambda value: value["marker_policy"].update(
                {"enabled": False}
            ),
            "text-policy": lambda value: value["marker_policy"].update(
                {"text_derivation_version": "operation-id-suffix/v2"}
            ),
            "text-prefix": lambda value: value["marker_policy"].update(
                {"text_prefix": "LPF-ALT-"}
            ),
            "policy-version": lambda value: value["marker_policy"].update(
                {"policy_version": "marker-policy/v2"}
            ),
            "plugin-capability": lambda value: value["marker_policy"].update(
                {"plugin_capability": False}
            ),
            "profile-enabled": lambda value: value["operation_profiles"].update(
                {"create_review_marker/v1": False}
            ),
            "geometry-defaults": lambda value: value["marker_policy"][
                "geometry_defaults"
            ].update({"space_kind": "paperspace"}),
        }
        for name, mutation in mutations.items():
            with self.subTest(field=name):
                drifted = deepcopy(audited_config)
                mutation(drifted)
                with self.assertRaises(PipelineError):
                    generate_native_plan(audit, private_intent, drifted)
                with self.assertRaises(PipelineError):
                    build_native_manifest(
                        audit,
                        plan,
                        private_intent,
                        export,
                        fresh,
                        drifted,
                        private_source_copy=private_copy,
                        output_path=__import__("pathlib").Path(
                            "marker-drift-output.dwg"
                        ),
                    )

        disabled_config = config()
        disabled_audit = build_native_audit(before, read_session, disabled_config)
        disabled_intent = intent(
            disabled_audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "e" * 24,
                    "kind": "create_review_marker",
                    "position": [
                        bits_from_float(3),
                        bits_from_float(4),
                        bits_from_float(0),
                    ],
                }
            ],
        )
        later_enabled = config()
        later_enabled["operation_profiles"]["create_review_marker/v1"] = True
        later_enabled["marker_policy"]["enabled"] = True
        later_enabled["marker_policy"]["plugin_capability"] = True
        with self.assertRaises(PipelineError) as raised:
            generate_native_plan(disabled_audit, disabled_intent, later_enabled)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CAPABILITY_MISMATCH)

    def test_renewed_same_host_session_is_required_and_bound(self) -> None:
        before, audit, private_intent, plan, _manifest = self._translation_workflow()
        fresh = _renewed_session(session(), "c")
        export = _fresh_export(before, fresh)
        manifest = build_native_manifest(
            audit,
            plan,
            private_intent,
            export,
            fresh,
            config(),
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "f" * 64,
            },
            output_path=__import__("pathlib").Path("renewed-output.dwg"),
        )
        self.assertNotEqual(
            manifest["session_renewal"]["audited_session_binding"],
            manifest["session_renewal"]["fresh_session_binding"],
        )
        self.assertEqual(
            manifest["session_renewal"]["native_host_binding"],
            audit["native_host_binding"],
        )
        self.assertNotEqual(
            manifest["expected_prewrite_revision"]["bridge_document_identity"][
                "database_instance_fingerprint"
            ],
            audit["document_binding"]["database_instance_fingerprint"],
        )
        self.assertNotIn("expected_final_revision_fingerprint", manifest)

        for mutation in (
            lambda value: value["process"].update(
                {"executable_fingerprint": "b" * 64}
            ),
            lambda value: value["adapter"].update({"version": "2.0.0"}),
            lambda value: value["plugin"].update({"version": "2.0.0"}),
            lambda value: value["capabilities"].append("read.extra/v1"),
            lambda value: value["host"].update({"runtime": "other-runtime"}),
        ):
            with self.subTest(mutation=mutation):
                forged = deepcopy(fresh)
                mutation(forged)
                forged = attach_integrity(forged)
                with self.assertRaises(PipelineError) as raised:
                    build_native_manifest(
                        audit,
                        plan,
                        private_intent,
                        export,
                        forged,
                        config(),
                        private_source_copy={
                            "sha256": before["source"]["sha256"],
                            "byte_size": before["source"]["byte_size"],
                            "file_identity_fingerprint": "f" * 64,
                        },
                        output_path=__import__("pathlib").Path("renewed-output.dwg"),
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CAPABILITY_MISMATCH)

        for mutation in (
            lambda value: value["adapter"].update({"profile": "other-profile"}),
            lambda value: value["plugins"]["write"].update({"version": "2.0.0"}),
            lambda value: value["plugins"]["readback"].update({"version": "2.0.0"}),
            lambda value: value["required_capabilities"].append("read.extra/v1"),
            lambda value: value["marker_policy"].update({"enabled": True}),
            lambda value: (
                value.update(
                    {"write_revision_transition": "preserved_by_plugin_capability"}
                ),
                value["required_capabilities"].append(
                    "plugin.revision_preservation/v1"
                ),
            ),
        ):
            with self.subTest(config_mutation=mutation):
                drifted_config = config()
                mutation(drifted_config)
                with self.assertRaises(PipelineError) as raised:
                    build_native_manifest(
                        audit,
                        plan,
                        private_intent,
                        export,
                        fresh,
                        drifted_config,
                        private_source_copy={
                            "sha256": before["source"]["sha256"],
                            "byte_size": before["source"]["byte_size"],
                            "file_identity_fingerprint": "f" * 64,
                        },
                        output_path=__import__("pathlib").Path("renewed-output.dwg"),
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CAPABILITY_MISMATCH)

        stale = deepcopy(fresh)
        stale["current_document"]["revision_fingerprint"] = "d" * 64
        stale = attach_integrity(stale)
        with self.assertRaises(PipelineError) as raised:
            build_native_manifest(
                audit,
                plan,
                private_intent,
                export,
                stale,
                config(),
                private_source_copy={
                    "sha256": before["source"]["sha256"],
                    "byte_size": before["source"]["byte_size"],
                    "file_identity_fingerprint": "f" * 64,
                },
                output_path=__import__("pathlib").Path("renewed-output.dwg"),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_DOCUMENT_CHANGED)

        forged_manifest = deepcopy(manifest)
        forged_manifest["session_renewal"]["fresh_session_binding"] = (
            forged_manifest["session_renewal"]["audited_session_binding"]
        )
        forged_manifest = attach_integrity(forged_manifest)
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("manifest", forged_manifest)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_MANIFEST_INVALID)

    def test_prewrite_binding_rejects_stale_source_and_protected_state(self) -> None:
        before, audit, private_intent, plan, _manifest = self._translation_workflow()
        fresh = _renewed_session(session(), "e")
        export = _fresh_export(before, fresh)
        private_copy = {
            "sha256": before["source"]["sha256"],
            "byte_size": before["source"]["byte_size"],
            "file_identity_fingerprint": "f" * 64,
        }
        wrong_source = deepcopy(export)
        wrong_source["source"]["sha256"] = "e" * 64
        wrong_source["binding"][
            "document_binding_digest"
        ] = geometry_document_binding_digest(wrong_source)
        wrong_source = attach_integrity(wrong_source)
        with self.assertRaises(PipelineError) as raised:
            build_native_manifest(
                audit,
                plan,
                private_intent,
                wrong_source,
                fresh,
                config(),
                private_source_copy=private_copy,
                output_path=__import__("pathlib").Path("wrong-source-output.dwg"),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_DOCUMENT_CHANGED)

        altered = deepcopy(before["entities"][0])
        altered["text"] = "changed-protected-state"
        _rehash_entity(altered)
        wrong_protected = geometry(
            [altered],
            source_value=before["source"],
            session_value=fresh,
        )
        with self.assertRaises(PipelineError) as raised:
            build_native_manifest(
                audit,
                plan,
                private_intent,
                wrong_protected,
                fresh,
                config(),
                private_source_copy=private_copy,
                output_path=__import__("pathlib").Path("wrong-protected-output.dwg"),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_DOCUMENT_CHANGED)

    def test_final_readback_requires_stable_host_binding_but_allows_new_session(self) -> None:
        """Session/PID/database churn is allowed; stable execution drift is not."""

        before, _audit, _intent, _plan, manifest = self._translation_workflow()
        changed = deepcopy(before["entities"][0])
        changed["position"] = [bits_from_float(6), bits_from_float(0), bits_from_float(0)]
        changed["bounds"] = {
            "minimum": list(changed["position"]),
            "maximum": list(changed["position"]),
        }
        _rehash_entity(changed)
        renewed = _renewed_session(session(), "e")
        valid_new_session = _final_export(
            manifest,
            [changed],
            session_value=renewed,
            database_instance=digest("new-readback-database"),
            revision=digest("new-readback-revision"),
        )
        self.assertTrue(verify_native_transition(manifest, valid_new_session))

        mutations = (
            lambda value: value["adapter"].update({"profile": "other-profile"}),
            lambda value: value["adapter"].update({"version": "2.0.0"}),
            lambda value: value["plugin"].update({"version": "2.0.0"}),
            lambda value: value["host"].update({"runtime": "other-runtime"}),
            lambda value: value["process"].update(
                {"executable_fingerprint": "e" * 64}
            ),
            lambda value: value["capabilities"].append("read.extra/v1"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                drifted = deepcopy(renewed)
                mutation(drifted)
                drifted = attach_integrity(drifted)
                after = _final_export(
                    manifest,
                    [changed],
                    session_value=drifted,
                    database_instance=digest("drifted-readback-database"),
                    revision=digest("drifted-readback-revision"),
                )
                with self.assertRaises(PipelineError) as raised:
                    verify_native_transition(manifest, after)
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.NATIVE_READBACK_INVALID,
                )

    def test_sequence_and_container_changes_fail_after_recomputed_integrity(self) -> None:
        before = geometry(
            [
                entity("10", sequence_index=0),
                entity("11", sequence_index=1, text="second"),
                entity("20", sequence_index=0, space_kind="paperspace"),
                entity("30", sequence_index=0, space_kind="block", block_path=["CC"]),
            ]
        )
        read_session = session()
        audit = build_native_audit(before, read_session, config())
        target = derive_native_target_id(
            next(item for item in before["entities"] if item["handle"] == "10")
        )
        private_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "e" * 24,
                    "kind": "translate_dbtext",
                    "target_id": target,
                    "delta": [bits_from_float(1), bits_from_float(0), bits_from_float(0)],
                }
            ],
        )
        plan = generate_native_plan(audit, private_intent, config())
        fresh_session = _renewed_session(read_session)
        manifest = build_native_manifest(
            audit,
            plan,
            private_intent,
            _fresh_export(before, fresh_session),
            fresh_session,
            config(),
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "f" * 64,
            },
            output_path=__import__("pathlib").Path("sequence-output.dwg"),
        )
        before_by_handle = {item["handle"]: item for item in before["entities"]}
        translated = deepcopy(before_by_handle["10"])
        translated["position"] = [bits_from_float(2), bits_from_float(2), bits_from_float(0)]
        translated["bounds"] = {
            "minimum": list(translated["position"]),
            "maximum": list(translated["position"]),
        }
        _rehash_entity(translated)
        base_after = [
            translated,
            deepcopy(before_by_handle["11"]),
            deepcopy(before_by_handle["20"]),
            deepcopy(before_by_handle["30"]),
        ]
        self.assertTrue(verify_native_transition(manifest, _final_export(manifest, base_after)))
        for handle, sequence in (("11", 2), ("20", 1), ("30", 1)):
            with self.subTest(handle=handle):
                mutated = deepcopy(base_after)
                target_index = next(
                    index
                    for index, item in enumerate(mutated)
                    if item["handle"] == handle
                )
                mutated[target_index]["sequence_index"] = sequence
                _rehash_entity(mutated[target_index])
                with self.assertRaises(PipelineError) as raised:
                    verify_native_transition(manifest, _final_export(manifest, mutated))
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        reordered_container = deepcopy(base_after)
        paperspace_index = next(
            index
            for index, item in enumerate(reordered_container)
            if item["handle"] == "20"
        )
        reordered_container[paperspace_index]["space"]["layout_handle"] = "DD"
        _rehash_entity(reordered_container[paperspace_index])
        with self.assertRaises(PipelineError) as raised:
            verify_native_transition(
                manifest,
                _final_export(manifest, reordered_container),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)

    def test_multiple_markers_are_bijective_and_modelspace_local(self) -> None:
        before = geometry(
            [
                entity("10", sequence_index=0, layer="OTHER"),
                entity("20", sequence_index=0, space_kind="paperspace", layer="OTHER"),
            ]
        )
        native_config = config()
        native_config["operation_profiles"]["create_review_marker/v1"] = True
        native_config["marker_policy"]["enabled"] = True
        native_config["marker_policy"]["plugin_capability"] = True
        read_session = session()
        audit = build_native_audit(before, read_session, native_config)
        private_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "a" * 24,
                    "kind": "create_review_marker",
                    "position": [bits_from_float(4), bits_from_float(5), bits_from_float(0)],
                },
                {
                    "operation_id": "native-operation-" + "b" * 24,
                    "kind": "create_review_marker",
                    "position": [bits_from_float(6), bits_from_float(7), bits_from_float(0)],
                },
            ],
        )
        plan = generate_native_plan(audit, private_intent, native_config)
        fresh_session = _renewed_session(read_session)
        manifest = build_native_manifest(
            audit,
            plan,
            private_intent,
            _fresh_export(before, fresh_session),
            fresh_session,
            native_config,
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "f" * 64,
            },
            output_path=__import__("pathlib").Path("markers-output.dwg"),
        )
        operations = [
            operation
            for operation in manifest["operations"]
            if operation["kind"] == "create_review_marker"
        ]
        first = _marker_from_operation(operations[0], "30")
        second = _marker_from_operation(operations[1], "31")
        after = _final_export(
            manifest,
            [before["entities"][0], first, second, before["entities"][1]],
        )
        self.assertEqual(
            [result["operation_id"] for result in verify_native_transition(manifest, after)],
            [operation["operation_id"] for operation in manifest["operations"]],
        )
        actual_handles = {
            operations[0]["operation_id"]: first["handle"],
            operations[1]["operation_id"]: second["handle"],
        }
        receipt = attach_integrity(
            {
                "schema_version": "liang-pingfa/native-console-result/v2",
                "run_id": "native-run-" + "c" * 32,
                "manifest_id": manifest["manifest_id"],
                "manifest_integrity_sha256": manifest["integrity"]["sha256"],
                "manifest_schema_version": manifest["schema_version"],
                "nonce": manifest["nonce"],
                "runtime_package_fingerprint": manifest["environment"][
                    "runtime_package_fingerprint"
                ],
                "final_revision_fingerprint": after["document"][
                    "revision_fingerprint"
                ],
                "final_revision_transition": "save_reopen_changed",
                "final_document_binding": {
                    "database_instance_fingerprint": after["document"][
                        "database_instance_fingerprint"
                    ],
                    "revision_fingerprint": after["document"][
                        "revision_fingerprint"
                    ],
                    "output_copy_binding": after["source"],
                },
                "transaction": {
                    "preflight": "passed",
                    "outcome": "committed",
                    "rollback": "not_required",
                },
                "operation_results": [
                    {
                        "operation_id": operation["operation_id"],
                        "status": "applied",
                        "marker_handle": actual_handles[
                            operation["operation_id"]
                        ],
                        "postcondition_digest": canonical_sha256(
                            {
                                "operation": operation,
                                "marker_handle": actual_handles[
                                    operation["operation_id"]
                                ],
                            }
                        ),
                    }
                    for operation in manifest["operations"]
                ],
            }
        )
        checked_receipt = validate_console_result(
            manifest,
            receipt,
            run_id=receipt["run_id"],
        )
        verified = verify_native_transition(
            manifest,
            after,
            console_result=checked_receipt,
        )
        self.assertEqual(
            ["30", "31"],
            [
                result["marker_handle"]
                for result in verified
                if result["kind"] == "create_review_marker"
            ],
        )
        swapped = deepcopy(receipt)
        for result, operation, handle in zip(
            swapped["operation_results"],
            manifest["operations"],
            ("31", "30"),
            strict=True,
        ):
            result["marker_handle"] = handle
            result["postcondition_digest"] = canonical_sha256(
                {"operation": operation, "marker_handle": handle}
            )
        swapped = attach_integrity(swapped)
        checked_swapped = validate_console_result(
            manifest,
            swapped,
            run_id=swapped["run_id"],
        )
        with self.assertRaises(PipelineError) as raised:
            verify_native_transition(
                manifest,
                after,
                console_result=checked_swapped,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        extra = _marker_from_operation(operations[1], "32")
        extra["sequence_index"] += 4
        _rehash_entity(extra)
        duplicate = _marker_from_operation(operations[0], "32")
        duplicate["sequence_index"] += 4
        _rehash_entity(duplicate)
        for name, additions in (
            ("missing", [first]),
            ("extra", [first, second, extra]),
            ("duplicate", [first, duplicate]),
        ):
            with self.subTest(name=name):
                with self.assertRaises(PipelineError) as raised:
                    verify_native_transition(
                        manifest,
                        _final_export(
                            manifest,
                            [before["entities"][0], *additions, before["entities"][1]],
                        ),
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        wrong_container = deepcopy(first)
        wrong_container["space"] = deepcopy(before["entities"][1]["space"])
        wrong_container["sequence_index"] = 1
        _rehash_entity(wrong_container)
        with self.assertRaises(PipelineError) as raised:
            verify_native_transition(
                manifest,
                _final_export(
                    manifest,
                    [before["entities"][0], second, before["entities"][1], wrong_container],
                ),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        wrong_order_first = deepcopy(first)
        wrong_order_second = deepcopy(second)
        wrong_order_first["sequence_index"], wrong_order_second["sequence_index"] = (
            wrong_order_second["sequence_index"],
            wrong_order_first["sequence_index"],
        )
        _rehash_entity(wrong_order_first)
        _rehash_entity(wrong_order_second)
        with self.assertRaises(PipelineError) as raised:
            verify_native_transition(
                manifest,
                _final_export(
                    manifest,
                    [
                        before["entities"][0],
                        wrong_order_first,
                        wrong_order_second,
                        before["entities"][1],
                    ]
                ),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)

    def test_console_claim_and_readback_bindings_are_complete(self) -> None:
        before, _audit, _intent, _plan, manifest = self._translation_workflow()
        changed = deepcopy(before["entities"][0])
        changed["position"] = [bits_from_float(6), bits_from_float(0), bits_from_float(0)]
        changed["bounds"] = {
            "minimum": [bits_from_float(6), bits_from_float(0), bits_from_float(0)],
            "maximum": [bits_from_float(6), bits_from_float(0), bits_from_float(0)],
        }
        _rehash_entity(changed)
        after = _final_export(
            manifest,
            [changed],
            database_instance=digest("final-database"),
            revision=digest("final-revision"),
        )
        write_run = "native-run-" + "4" * 32
        result = {
            "schema_version": "liang-pingfa/native-console-result/v2",
            "run_id": write_run,
            "manifest_id": manifest["manifest_id"],
            "manifest_integrity_sha256": manifest["integrity"]["sha256"],
            "manifest_schema_version": manifest["schema_version"],
            "nonce": manifest["nonce"],
            "runtime_package_fingerprint": manifest["environment"][
                "runtime_package_fingerprint"
            ],
            "final_revision_fingerprint": after["document"]["revision_fingerprint"],
            "final_revision_transition": "save_reopen_changed",
            "final_document_binding": {
                "database_instance_fingerprint": after["document"][
                    "database_instance_fingerprint"
                ],
                "revision_fingerprint": after["document"]["revision_fingerprint"],
                "output_copy_binding": after["source"],
            },
            "transaction": {
                "preflight": "passed",
                "outcome": "committed",
                "rollback": "not_required",
            },
            "operation_results": [
                {
                    "operation_id": manifest["operations"][0]["operation_id"],
                    "status": "applied",
                    "postcondition_digest": canonical_sha256(manifest["operations"][0]),
                    "marker_handle": None,
                }
            ],
        }
        result = attach_integrity(result)
        checked_result = validate_console_result(manifest, result, run_id=write_run)
        self.assertNotEqual(
            checked_result["final_revision_fingerprint"],
            manifest["expected_prewrite_revision"]["bridge_document_identity"][
                "revision_fingerprint"
            ],
        )
        preserved_revision = deepcopy(result)
        preserved_revision["final_revision_fingerprint"] = manifest[
            "expected_prewrite_revision"
        ]["bridge_document_identity"]["revision_fingerprint"]
        preserved_revision["final_revision_transition"] = (
            "preserved_by_plugin_capability"
        )
        preserved_revision["final_document_binding"]["revision_fingerprint"] = (
            preserved_revision["final_revision_fingerprint"]
        )
        preserved_revision = attach_integrity(preserved_revision)
        with self.assertRaises(PipelineError) as raised:
            validate_console_result(manifest, preserved_revision, run_id=write_run)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONSOLE_RESULT_INVALID)
        read_run = "native-run-" + "5" * 32
        exported = {
            "schema_version": "liang-pingfa/native-console-export/v2",
            "run_id": read_run,
            "manifest_id": manifest["manifest_id"],
            "manifest_integrity_sha256": manifest["integrity"]["sha256"],
            "manifest_schema_version": manifest["schema_version"],
            "console_result_integrity_sha256": checked_result["integrity"][
                "sha256"
            ],
            "console_result_schema_version": checked_result["schema_version"],
            "nonce": manifest["nonce"],
            "runtime_package_fingerprint": manifest["environment"][
                "runtime_package_fingerprint"
            ],
            "final_revision_fingerprint": after["document"]["revision_fingerprint"],
            "final_document_binding": {
                "database_instance_fingerprint": after["document"][
                    "database_instance_fingerprint"
                ],
                "revision_fingerprint": after["document"]["revision_fingerprint"],
                "output_copy_binding": after["source"],
            },
            "geometry_json": canonical_json_bytes(after).decode("utf-8"),
            "geometry_sha256": canonical_sha256(after),
        }
        exported = attach_integrity(exported)
        self.assertEqual(
            geometry_from_console_export(
                manifest,
                exported,
                run_id=read_run,
                result=checked_result,
            )["document"]["complete_geometry_digest"],
            after["document"]["complete_geometry_digest"],
        )
        write_package_drift = deepcopy(result)
        write_package_drift["runtime_package_fingerprint"] = "f" * 64
        write_package_drift = attach_integrity(write_package_drift)
        with self.assertRaises(PipelineError) as raised:
            validate_console_result(
                manifest,
                write_package_drift,
                run_id=write_run,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONSOLE_RESULT_INVALID)

        readback_package_drift = deepcopy(exported)
        readback_package_drift["runtime_package_fingerprint"] = "e" * 64
        readback_package_drift = attach_integrity(readback_package_drift)
        with self.assertRaises(PipelineError) as raised:
            geometry_from_console_export(
                manifest,
                readback_package_drift,
                run_id=read_run,
                result=checked_result,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        forged_result = deepcopy(result)
        forged_result["final_revision_fingerprint"] = "f" * 64
        forged_result = attach_integrity(forged_result)
        with self.assertRaises(PipelineError) as raised:
            validate_console_result(manifest, forged_result, run_id=write_run)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONSOLE_RESULT_INVALID)

        forged_envelope = deepcopy(exported)
        forged_envelope["final_revision_fingerprint"] = "f" * 64
        forged_envelope = attach_integrity(forged_envelope)
        with self.assertRaises(PipelineError) as raised:
            geometry_from_console_export(
                manifest,
                forged_envelope,
                run_id=read_run,
                result=checked_result,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)

        forged_output_binding = deepcopy(exported)
        forged_output_binding["final_document_binding"]["output_copy_binding"][
            "file_identity_fingerprint"
        ] = "e" * 64
        forged_output_binding = attach_integrity(forged_output_binding)
        with self.assertRaises(PipelineError) as raised:
            geometry_from_console_export(
                manifest,
                forged_output_binding,
                run_id=read_run,
                result=checked_result,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)

        stale_geometry = _final_export(
            manifest,
            [changed],
            database_instance=after["document"]["database_instance_fingerprint"],
            revision="f" * 64,
        )
        forged_embedded = deepcopy(exported)
        forged_embedded["geometry_json"] = canonical_json_bytes(stale_geometry).decode(
            "utf-8"
        )
        forged_embedded["geometry_sha256"] = canonical_sha256(stale_geometry)
        forged_embedded = attach_integrity(forged_embedded)
        with self.assertRaises(PipelineError) as raised:
            geometry_from_console_export(
                manifest,
                forged_embedded,
                run_id=read_run,
                result=checked_result,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        verification = build_native_verification(
            manifest,
            after,
            {
                **after["source"],
                "file_identity_fingerprint": "e" * 64,
                "path_fingerprint": "d" * 64,
            },
            result=checked_result,
            console_export=exported,
        )
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["record_cardinality"], "explicit_private")
        self.assertTrue(verification["non_claims"]["external_transaction_claim_unproven"])

    def test_final_output_source_transition_is_constrained_not_predicted(self) -> None:
        """The private input binds exactly; the final output is constrained."""

        before, _audit, _intent, _plan, manifest = self._translation_workflow()
        changed = deepcopy(before["entities"][0])
        changed["position"] = [bits_from_float(6), bits_from_float(0), bits_from_float(0)]
        changed["bounds"] = {
            "minimum": list(changed["position"]),
            "maximum": list(changed["position"]),
        }
        _rehash_entity(changed)
        after = _final_export(
            manifest,
            [changed],
            database_instance=digest("transition-final-database"),
            revision=digest("transition-final-revision"),
        )
        run_id = "native-run-" + "d" * 32
        base = {
            "schema_version": "liang-pingfa/native-console-result/v2",
            "run_id": run_id,
            "manifest_id": manifest["manifest_id"],
            "manifest_integrity_sha256": manifest["integrity"]["sha256"],
            "manifest_schema_version": manifest["schema_version"],
            "nonce": manifest["nonce"],
            "runtime_package_fingerprint": manifest["environment"][
                "runtime_package_fingerprint"
            ],
            "final_revision_fingerprint": after["document"]["revision_fingerprint"],
            "final_revision_transition": "save_reopen_changed",
            "final_document_binding": {
                "database_instance_fingerprint": after["document"][
                    "database_instance_fingerprint"
                ],
                "revision_fingerprint": after["document"]["revision_fingerprint"],
                "output_copy_binding": after["source"],
            },
            "transaction": {
                "preflight": "passed",
                "outcome": "committed",
                "rollback": "not_required",
            },
            "operation_results": [
                {
                    "operation_id": operation["operation_id"],
                    "status": "applied",
                    "postcondition_digest": canonical_sha256(operation),
                    "marker_handle": None,
                }
                for operation in manifest["operations"]
            ],
        }
        result = attach_integrity(base)
        self.assertEqual(
            result,
            validate_console_result(manifest, result, run_id=run_id),
        )
        self.assertNotEqual(
            manifest["expected_prewrite_revision"]["source_binding"],
            _actual_output_binding(manifest),
        )

        unchanged = deepcopy(base)
        unchanged["final_document_binding"]["output_copy_binding"] = manifest[
            "expected_prewrite_revision"
        ]["source_binding"]
        unchanged = attach_integrity(unchanged)
        with self.assertRaises(PipelineError) as raised:
            validate_console_result(manifest, unchanged, run_id=run_id)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONSOLE_RESULT_INVALID)

        for field, value in (
            (
                "sha256",
                manifest["expected_prewrite_output_copy_binding"]["sha256"],
            ),
            (
                "byte_size",
                manifest["final_output_constraints"]["max_byte_size"] + 1,
            ),
            ("path_fingerprint", "e" * 64),
            ("dwg_header_signature", "AC1027"),
        ):
            with self.subTest(field=field):
                partial = deepcopy(base)
                partial["final_document_binding"]["output_copy_binding"][field] = value
                partial = attach_integrity(partial)
                with self.assertRaises(PipelineError) as raised:
                    validate_console_result(manifest, partial, run_id=run_id)
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.NATIVE_CONSOLE_RESULT_INVALID,
                )

        allowed_replacement = deepcopy(base)
        allowed_replacement["final_document_binding"]["output_copy_binding"][
            "file_identity_fingerprint"
        ] = "e" * 64
        allowed_replacement = attach_integrity(allowed_replacement)
        self.assertEqual(
            allowed_replacement,
            validate_console_result(
                manifest,
                allowed_replacement,
                run_id=run_id,
            ),
        )

        swapped_manifest = deepcopy(manifest)
        swapped_manifest["final_output_constraints"][
            "authorized_private_path_fingerprint"
        ] = manifest["source"]["path_fingerprint"]
        swapped_manifest = attach_integrity(swapped_manifest)
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("manifest", swapped_manifest)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_MANIFEST_INVALID)

    def test_explicit_plugin_revision_preservation_requires_its_transition_enum(self) -> None:
        preserving_config = config()
        preserving_config["write_revision_transition"] = "preserved_by_plugin_capability"
        preserving_config["required_capabilities"].append(
            "plugin.revision_preservation/v1"
        )
        before = geometry(
            [entity("10", text="generated-private-text")],
            capabilities=preserving_config["required_capabilities"],
        )
        preserving_session = session()
        preserving_session["capabilities"].append("plugin.revision_preservation/v1")
        preserving_session = attach_integrity(preserving_session)
        audit = build_native_audit(before, preserving_session, preserving_config)
        private_intent = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "a" * 24,
                    "kind": "translate_dbtext",
                    "target_id": audit["records"][0]["target_id"],
                    "delta": [
                        bits_from_float(1.0),
                        bits_from_float(0.0),
                        bits_from_float(0.0),
                    ],
                }
            ],
        )
        plan = generate_native_plan(audit, private_intent, preserving_config)
        fresh = _renewed_session(preserving_session, "b")
        manifest = build_native_manifest(
            audit,
            plan,
            private_intent,
            _fresh_export(before, fresh),
            fresh,
            preserving_config,
            private_source_copy={
                "sha256": before["source"]["sha256"],
                "byte_size": before["source"]["byte_size"],
                "file_identity_fingerprint": "f" * 64,
            },
            output_path=__import__("pathlib").Path("preserved-revision-output.dwg"),
        )
        prewrite = manifest["expected_prewrite_revision"]
        result = attach_integrity(
            {
                "schema_version": "liang-pingfa/native-console-result/v2",
                "run_id": "native-run-" + "a" * 32,
                "manifest_id": manifest["manifest_id"],
                "manifest_integrity_sha256": manifest["integrity"]["sha256"],
                "manifest_schema_version": manifest["schema_version"],
                "nonce": manifest["nonce"],
                "runtime_package_fingerprint": manifest["environment"][
                    "runtime_package_fingerprint"
                ],
                "final_revision_fingerprint": prewrite["bridge_document_identity"][
                    "revision_fingerprint"
                ],
                "final_revision_transition": "preserved_by_plugin_capability",
                "final_document_binding": {
                    "database_instance_fingerprint": prewrite[
                        "bridge_document_identity"
                    ]["database_instance_fingerprint"],
                    "revision_fingerprint": prewrite["bridge_document_identity"][
                        "revision_fingerprint"
                    ],
                    "output_copy_binding": _actual_output_binding(manifest),
                },
                "transaction": {
                    "preflight": "passed",
                    "outcome": "committed",
                    "rollback": "not_required",
                },
                "operation_results": [
                    {
                        "operation_id": operation["operation_id"],
                        "status": "applied",
                        "postcondition_digest": canonical_sha256(operation),
                        "marker_handle": None,
                    }
                    for operation in manifest["operations"]
                ],
            }
        )
        self.assertEqual(
            validate_console_result(
                manifest,
                result,
                run_id="native-run-" + "a" * 32,
            )["final_revision_transition"],
            "preserved_by_plugin_capability",
        )


if __name__ == "__main__":
    unittest.main()
