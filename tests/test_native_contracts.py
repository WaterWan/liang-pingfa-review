"""Strict native schemas, semantic mutation gates, and privacy tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter
import unittest
import unicodedata
from collections.abc import Callable
from unittest import mock

import liang_pingfa_review.canonical as canonical_module
import liang_pingfa_review.native_contracts as native_contracts_module
from liang_pingfa_review.atomic_output import (
    ArtifactPublication,
    publish_artifacts,
    publish_private_artifacts,
)
from liang_pingfa_review.canonical import (
    CanonicalJsonError,
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
)
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.native_audit import build_native_audit
from liang_pingfa_review.native_contracts import (
    MAX_NATIVE_SESSION_LIFETIME,
    canonical_geometry_json_bytes,
    MAX_NATIVE_GEOMETRY_ENTITIES,
    MAX_NATIVE_GEOMETRY_JSON_BYTES,
    MAX_NATIVE_GEOMETRY_SEGMENTS,
    MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS,
    bits_from_float,
    load_native_artifact,
    require_geometry_json_utf8_bytes,
    require_inventory_json_utf8_bytes,
    schema_for_native,
    translate_binary64_bits,
    translated_geometry_bits,
    strict_native_json,
    validate_native_contract,
)
from liang_pingfa_review.native_manifest import build_native_manifest
from liang_pingfa_review.native_plan import generate_native_plan
from liang_pingfa_review.native_protocol import (
    NativeProtocolError,
    derive_challenge_response,
)
from liang_pingfa_review.reports import (
    render_native_audit_report,
    render_native_plan_review,
)
from tests.support.synthetic_native import (
    config,
    entity,
    geometry,
    intent,
    session,
)


class NativeContractTests(unittest.TestCase):
    """Prove generic validation fails closed before orchestration."""

    def test_opaque_embedded_json_allowlist_has_only_exact_schema_paths(self) -> None:
        self.assertEqual(
            {
                kind: dict(paths)
                for kind, paths in native_contracts_module.NATIVE_OPAQUE_EMBEDDED_JSON_RULES.items()
            },
            {
                "response": {
                    ("result", "geometry_json"): MAX_NATIVE_GEOMETRY_JSON_BYTES,
                    (
                        "result",
                        "inventory_json",
                    ): native_contracts_module.MAX_NATIVE_INVENTORY_JSON_BYTES,
                },
                "manifest": {
                    (
                        "preconditions_geometry_json",
                    ): MAX_NATIVE_GEOMETRY_JSON_BYTES,
                },
                "console_export": {
                    ("geometry_json",): MAX_NATIVE_GEOMETRY_JSON_BYTES,
                },
            },
        )

    def test_all_packaged_native_schemas_are_strict_recursively(self) -> None:
        def check(value: object) -> None:
            if not isinstance(value, dict):
                return
            if value.get("type") == "object":
                self.assertIn("additionalProperties", value)
                self.assertFalse(value["additionalProperties"])
            for nested in value.values():
                if isinstance(nested, dict):
                    check(nested)
                elif isinstance(nested, list):
                    for item in nested:
                        check(item)

        for kind in (
            "config",
            "request",
            "response",
            "inventory",
            "session",
            "geometry",
            "audit",
            "intent",
            "plan",
            "manifest",
            "console_result",
            "console_export",
            "verification",
        ):
            with self.subTest(kind=kind):
                check(schema_for_native(kind))

    def test_geometry_rejects_extra_field_and_noncanonical_bits(self) -> None:
        valid = geometry()
        forged = deepcopy(valid)
        forged["entities"][0]["unexpected"] = True
        forged = attach_integrity(forged)
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("geometry", forged)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)

        invalid_bits = deepcopy(valid)
        invalid_bits["entities"][0]["position"][0] = "7ff8000000000000"
        invalid_bits = attach_integrity(invalid_bits)
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("geometry", invalid_bits)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)

    def test_geometry_export_requires_every_exact_session_document_binding(self) -> None:
        """Source-equal exports cannot cross session, host, or document tuples."""

        exact_session = session()
        exact_export = geometry(session_value=exact_session)
        exact_audit = build_native_audit(
            exact_export,
            exact_session,
            config(),
            expected_source=exact_export["source"],
        )
        self.assertEqual(
            exact_audit["session_binding_digest"],
            exact_export["binding"]["session_binding_digest"],
        )
        self.assertEqual(
            exact_audit["geometry_document_binding_digest"],
            exact_export["binding"]["document_binding_digest"],
        )

        def signed(mutator: Callable[[dict], None]) -> dict:
            candidate = deepcopy(exact_session)
            mutator(candidate)
            candidate["challenge_response"] = derive_challenge_response(
                candidate["client_nonce"],
                candidate["challenge"],
                candidate["bridge_nonce"],
                session_id=candidate["session_id"],
            )
            return attach_integrity(candidate)

        # Each candidate remains a valid session in isolation.  Its source is
        # unchanged unless the case explicitly exercises a document field.
        cases: tuple[tuple[str, Callable[[dict], None]], ...] = (
            (
                "same-source-different-pid",
                lambda value: value.update({"pid": value["pid"] + 1}),
            ),
            (
                "same-stable-host-new-session",
                lambda value: value.update(
                    {"session_id": "native-session-" + "d" * 32}
                ),
            ),
            (
                "windows-session",
                lambda value: value.update(
                    {"windows_session_id": value["windows_session_id"] + 1}
                ),
            ),
            (
                "process-instance",
                lambda value: value["process"].update(
                    {"instance_fingerprint": "a" * 64}
                ),
            ),
            (
                "process-creation-time",
                lambda value: value["process"].update(
                    {"creation_time_100ns": "987654321"}
                ),
            ),
            (
                "host-executable",
                lambda value: value["process"].update(
                    {"executable_fingerprint": "b" * 64}
                ),
            ),
            (
                "host-runtime",
                lambda value: value["host"].update({"runtime": "other-runtime"}),
            ),
            (
                "adapter-id",
                lambda value: value["adapter"].update({"id": "other-adapter"}),
            ),
            (
                "adapter-profile",
                lambda value: value["adapter"].update({"profile": "other-profile"}),
            ),
            (
                "adapter-version",
                lambda value: value["adapter"].update({"version": "2.0.0"}),
            ),
            (
                "plugin-id",
                lambda value: value["plugin"].update({"id": "other-plugin"}),
            ),
            (
                "plugin-version",
                lambda value: value["plugin"].update({"version": "2.0.0"}),
            ),
            (
                "plugin-fingerprint",
                lambda value: value["plugin"].update({"fingerprint": "c" * 64}),
            ),
            (
                "source-sha",
                lambda value: value["current_document"].update({"sha256": "d" * 64}),
            ),
            (
                "source-size",
                lambda value: value["current_document"].update({"byte_size": 129}),
            ),
            (
                "source-header",
                lambda value: value["current_document"].update(
                    {"dwg_header_signature": "AC1027"}
                ),
            ),
            (
                "source-path",
                lambda value: value["current_document"].update(
                    {"path_fingerprint": "e" * 64}
                ),
            ),
            (
                "source-file-identity",
                lambda value: value["current_document"].update(
                    {"file_identity_fingerprint": "f" * 64}
                ),
            ),
            (
                "database-instance",
                lambda value: value["current_document"].update(
                    {"database_instance_fingerprint": "1" * 64}
                ),
            ),
            (
                "revision",
                lambda value: value["current_document"].update(
                    {"revision_fingerprint": "2" * 64}
                ),
            ),
        )
        for name, mutate in cases:
            with self.subTest(binding=name):
                mismatched_session = signed(mutate)
                with self.assertRaises(PipelineError):
                    build_native_audit(exact_export, mismatched_session, config())

        for name, capabilities in (
            (
                "capability-superset",
                [
                    "read.inventory/v1",
                    "read.exact_geometry/v1",
                    "read.metadata/v1",
                ],
            ),
            (
                "capability-subset",
                ["read.inventory/v1", "read.metadata/v1"],
            ),
        ):
            with self.subTest(binding=name):
                mismatched_export = geometry(capabilities=capabilities)
                with self.assertRaises(PipelineError):
                    build_native_audit(
                        mismatched_export,
                        exact_session,
                        config(),
                    )

        wrong_header = deepcopy(exact_export["source"])
        wrong_header["dwg_header_signature"] = "AC1027"
        with self.assertRaises(PipelineError) as raised:
            build_native_audit(
                exact_export,
                exact_session,
                config(),
                expected_source=wrong_header,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_DOCUMENT_CHANGED)

    def test_geometry_capacity_covers_623_and_maximum_deadline_guard(self) -> None:
        """The conservative v1 cap remains far above the validated 623-case."""

        supported = geometry(
            [
                entity(f"{index + 1:04X}", sequence_index=index)
                for index in range(623)
            ]
        )
        self.assertEqual(
            len(validate_native_contract("geometry", supported)["entities"]),
            623,
        )

        started = perf_counter()
        max_segment_entity = entity("10", native_type="LWPOLYLINE")
        max_segment_entity["segments"] = [
            deepcopy(max_segment_entity["segments"][0])
            for _ in range(MAX_NATIVE_GEOMETRY_SEGMENTS)
        ]
        segment_projection = dict(max_segment_entity)
        segment_projection.pop("geometry_fingerprint")
        segment_projection.pop("opaque_state_digest")
        max_segment_entity["geometry_fingerprint"] = canonical_sha256(
            {"geometry": segment_projection}
        )
        max_segment_entity["opaque_state_digest"] = canonical_sha256(
            {"opaque_state": segment_projection}
        )
        maximum_segments = geometry([max_segment_entity])
        self.assertEqual(
            len(
                validate_native_contract(
                    "geometry",
                    maximum_segments,
                    deadline_check=lambda _stage: None,
                )["entities"][0]["segments"]
            ),
            MAX_NATIVE_GEOMETRY_SEGMENTS,
        )

        maximum = geometry(
            [
                entity(f"{index + 1:04X}", sequence_index=index)
                for index in range(MAX_NATIVE_GEOMETRY_ENTITIES)
            ]
        )
        self.assertEqual(
            len(
                validate_native_contract(
                    "geometry",
                    maximum,
                    deadline_check=lambda _stage: None,
                )["entities"]
            ),
            MAX_NATIVE_GEOMETRY_ENTITIES,
        )
        # Measured locally at roughly five seconds including fixture creation;
        # leave a generous CI margin while remaining well below the fixed
        # 60-second geometry RPC limit.
        self.assertLess(perf_counter() - started, 30.0)

    def test_cap_plus_one_rejects_before_expensive_geometry_traversal(self) -> None:
        """Entity and aggregate segment hard limits fail before normalization."""

        too_many_entities = geometry()
        too_many_entities["entities"] = [
            too_many_entities["entities"][0]
        ] * (MAX_NATIVE_GEOMETRY_ENTITIES + 1)
        with mock.patch.object(native_contracts_module, "_validate_common") as common:
            with self.assertRaises(PipelineError) as raised:
                validate_native_contract("geometry", too_many_entities)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)
        common.assert_not_called()

    def test_geometry_limits_match_schema_config_and_csharp_contract(self) -> None:
        """One v1 capacity is carried through every public contract surface."""

        self.assertEqual(
            config()["geometry_limits"],
            {
                "max_entities": MAX_NATIVE_GEOMETRY_ENTITIES,
                "max_segments": MAX_NATIVE_GEOMETRY_SEGMENTS,
                "max_geometry_json_bytes": MAX_NATIVE_GEOMETRY_JSON_BYTES,
                "max_inventory_json_bytes": 64 * 1024,
            },
        )
        geometry_schema = schema_for_native("geometry")
        self.assertEqual(
            geometry_schema["properties"]["entities"]["maxItems"],
            MAX_NATIVE_GEOMETRY_ENTITIES,
        )
        self.assertEqual(
            geometry_schema["$defs"]["entity"]["properties"]["segments"]["maxItems"],
            MAX_NATIVE_GEOMETRY_SEGMENTS,
        )
        protocol_dtos = (
            Path(__file__).parents[1]
            / "native-bridge-contracts"
            / "ProtocolV1.cs"
        ).read_text(encoding="utf-8")
        for declaration in (
            "MaxNativeGeometryEntities = 2_000",
            "MaxNativeGeometrySegments = 10_000",
            "MaxGeometryJsonBytes = 16 * 1024 * 1024",
            "MaxInventoryJsonBytes = 64 * 1024",
        ):
            self.assertIn(declaration, protocol_dtos)
        self.assertIn("measured in UTF-8 encoded bytes", protocol_dtos)

        too_many_segments = geometry([entity("10", native_type="LWPOLYLINE")])
        too_many_segments["entities"][0]["segments"] = [
            too_many_segments["entities"][0]["segments"][0]
        ] * (MAX_NATIVE_GEOMETRY_SEGMENTS + 1)
        with mock.patch.object(native_contracts_module, "_validate_common") as common:
            with self.assertRaises(PipelineError) as raised:
                validate_native_contract("geometry", too_many_segments)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)
        common.assert_not_called()

    def test_read_only_session_rejects_write_capability(self) -> None:
        forged = session()
        forged["capabilities"].append("write.anything/v1")
        forged = attach_integrity(forged)
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("session", forged)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)

    def test_session_temporal_bounds_reject_re_signed_future_and_boundary_values(self) -> None:
        """The signed descriptor is usable only in its strict current interval."""

        now = datetime(2030, 1, 1, tzinfo=UTC)

        def signed(created: datetime, expires: datetime) -> dict:
            candidate = session()
            candidate["created_at"] = canonical_module.format_utc(created)
            candidate["expires_at"] = canonical_module.format_utc(expires)
            return attach_integrity(candidate)

        cases = (
            (
                "thirty-days-future",
                signed(
                    now + timedelta(days=30),
                    now + timedelta(days=30) + MAX_NATIVE_SESSION_LIFETIME,
                ),
                False,
            ),
            (
                "one-second-future",
                signed(now + timedelta(seconds=1), now + timedelta(seconds=2)),
                False,
            ),
            (
                "exactly-now",
                signed(now, now + MAX_NATIVE_SESSION_LIFETIME),
                True,
            ),
            (
                "just-expired",
                signed(now - MAX_NATIVE_SESSION_LIFETIME, now - timedelta(seconds=1)),
                False,
            ),
            (
                "exactly-expiry",
                signed(now - MAX_NATIVE_SESSION_LIFETIME, now),
                False,
            ),
            (
                "fixed-maximum",
                signed(now, now + MAX_NATIVE_SESSION_LIFETIME),
                True,
            ),
            (
                "maximum-plus-one-second",
                signed(now, now + MAX_NATIVE_SESSION_LIFETIME + timedelta(seconds=1)),
                False,
            ),
        )
        for label, candidate, accepted in cases:
            with self.subTest(case=label), mock.patch.object(
                native_contracts_module,
                "utc_now",
                return_value=now,
            ):
                if accepted:
                    self.assertEqual(validate_native_contract("session", candidate), candidate)
                else:
                    with self.assertRaises(PipelineError) as raised:
                        validate_native_contract("session", candidate)
                    self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)

    def test_session_challenge_response_binds_the_full_versioned_transcript(self) -> None:
        """Re-signed descriptors still cannot alter or replay the handshake."""

        valid = session()
        self.assertEqual(validate_native_contract("session", valid), valid)

        copied_from_other_session = session()
        copied_from_other_session["session_id"] = "native-session-" + "d" * 32
        copied_from_other_session["client_nonce"] = "d" * 43
        copied_from_other_session["challenge"] = "e" * 43
        copied_from_other_session["bridge_nonce"] = "f" * 43
        copied_from_other_session["challenge_response"] = derive_challenge_response(
            copied_from_other_session["client_nonce"],
            copied_from_other_session["challenge"],
            copied_from_other_session["bridge_nonce"],
            session_id=copied_from_other_session["session_id"],
        )
        copied_from_other_session = attach_integrity(copied_from_other_session)
        self.assertEqual(
            validate_native_contract("session", copied_from_other_session),
            copied_from_other_session,
        )

        cases: list[tuple[str, Callable[[dict], object]]] = [
            (
                "mismatched-response",
                lambda value: value.__setitem__("challenge_response", "0" * 64),
            ),
            (
                "swapped-nonces",
                lambda value: (
                    value.__setitem__("client_nonce", valid["challenge"]),
                    value.__setitem__("challenge", valid["client_nonce"]),
                ),
            ),
            (
                "copied-response",
                lambda value: value.__setitem__(
                    "challenge_response",
                    copied_from_other_session["challenge_response"],
                ),
            ),
            (
                "changed-challenge",
                lambda value: value.__setitem__("challenge", "d" * 43),
            ),
            (
                "changed-session",
                lambda value: value.__setitem__(
                    "session_id", "native-session-" + "e" * 32
                ),
            ),
            (
                "nonce-case-drift",
                lambda value: value.__setitem__("client_nonce", "A" * 43),
            ),
            (
                "nonce-encoding-drift",
                lambda value: value.__setitem__("bridge_nonce", "a" * 42 + "="),
            ),
            (
                "missing-bridge-nonce",
                lambda value: value.pop("bridge_nonce"),
            ),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                re_signed = deepcopy(valid)
                mutate(re_signed)
                re_signed = attach_integrity(re_signed)
                with self.assertRaises(PipelineError) as raised:
                    validate_native_contract("session", re_signed)
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)

        with self.assertRaises(NativeProtocolError):
            derive_challenge_response(
                valid["client_nonce"],
                valid["challenge"],
                valid["bridge_nonce"],
                session_id=valid["session_id"],
                protocol_version="liang-pingfa/native-bridge/v0",
            )

    def test_request_method_requires_its_exact_parameter_shape(self) -> None:
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract(
                "request",
                {
                    "protocol_version": "liang-pingfa/native-bridge/v1",
                    "id": "a" * 32,
                    "method": "health",
                    "params": {
                        "session_id": "native-session-" + "a" * 32,
                        "client_nonce": "a" * 43,
                        "challenge": "b" * 43,
                    },
                },
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)

    def test_intent_rejects_non_xy_and_duplicate_target(self) -> None:
        audit = build_native_audit(geometry(), session(), config())
        target = audit["records"][0]["target_id"]
        with self.assertRaises(PipelineError) as raised:
            intent(
                audit,
                operations=[
                    {
                        "operation_id": "native-operation-" + "1" * 24,
                        "kind": "translate_dbtext",
                        "target_id": target,
                        "delta": [
                            bits_from_float(1.0),
                            bits_from_float(0.0),
                            bits_from_float(1.0),
                        ],
                    }
                ],
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_INTENT_SCHEMA_INVALID)

    def test_binary64_translation_accepts_only_finite_representable_changes(self) -> None:
        """Cover normal, subnormal, nextafter, overflow, and rounded-no-op axes."""

        smallest = float.fromhex("0x0.0000000000001p-1022")
        largest = float.fromhex("0x1.fffffffffffffp+1023")
        positive_step = math.nextafter(1.0, math.inf) - 1.0
        negative_step = math.nextafter(-1.0, -math.inf) - -1.0
        cases = (
            ("ordinary", 1.0, 1.0, 2.0),
            ("subnormal", 0.0, smallest, smallest),
            ("subnormal-cancellation", smallest, -smallest, 0.0),
            ("nextafter-positive", 1.0, positive_step, math.nextafter(1.0, math.inf)),
            ("nextafter-negative", -1.0, negative_step, math.nextafter(-1.0, -math.inf)),
            ("large-representable", 1e300, 1e294, 1e300 + 1e294),
        )
        for name, original, delta, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    translate_binary64_bits(
                        bits_from_float(original), bits_from_float(delta)
                    ),
                    bits_from_float(expected),
                )
        self.assertEqual(
            translate_binary64_bits(bits_from_float(1e300), bits_from_float(0.0)),
            bits_from_float(1e300),
        )
        for name, original, delta in (
            ("rounded-no-op", 1e300, 1.0),
            ("overflow", largest, largest),
            ("negative-zero", 1.0, -0.0),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    translate_binary64_bits(
                        bits_from_float(original),
                        "8000000000000000"
                        if name == "negative-zero"
                        else bits_from_float(delta),
                    )

    def test_translation_checks_all_serialized_geometry_scalars(self) -> None:
        delta = [bits_from_float(1.0), bits_from_float(0.0), bits_from_float(0.0)]
        with self.assertRaises(ValueError):
            translated_geometry_bits(
                entity(
                    "10",
                    position=(1.0, 2.0, 0.0),
                    bounds=((1e300, 2.0, 0.0), (1e300, 2.0, 0.0)),
                ),
                delta,
            )
        segment_entity = entity("11", native_type="LINE", position=(1.0, 2.0, 0.0))
        segment_entity["segments"][0]["start"][0] = bits_from_float(1e300)
        segment_entity["segments"][0]["end"][0] = bits_from_float(1e300)
        projection = dict(segment_entity)
        projection.pop("geometry_fingerprint")
        projection.pop("opaque_state_digest")
        segment_entity["geometry_fingerprint"] = canonical_sha256({"geometry": projection})
        segment_entity["opaque_state_digest"] = canonical_sha256({"opaque_state": projection})
        with self.assertRaises(ValueError):
            translated_geometry_bits(segment_entity, delta)

        original = entity("12", position=(1.0, 2.0, 0.0))
        expected = translated_geometry_bits(
            original,
            [bits_from_float(0.0), bits_from_float(1.0), bits_from_float(0.0)],
        )
        self.assertEqual(expected["position"][0], original["position"][0])
        self.assertEqual(expected["position"][2], original["position"][2])
        self.assertNotEqual(expected["position"][1], original["position"][1])
        self.assertEqual(expected["bounds"]["minimum"][0], original["bounds"]["minimum"][0])
        self.assertNotEqual(
            expected["bounds"]["minimum"][1], original["bounds"]["minimum"][1]
        )

    def test_manifest_rejects_nonrepresentable_axis_before_console_launch(self) -> None:
        before = geometry(
            [entity("10", position=(1e300, 2.0, 0.0), bounds=((1e300, 2.0, 0.0), (1e300, 2.0, 0.0)))]
        )
        audited_session = session()
        audit = build_native_audit(before, audited_session, config())
        target = audit["records"][0]["target_id"]
        requested = intent(
            audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "a" * 24,
                    "kind": "translate_dbtext",
                    "target_id": target,
                    "delta": [bits_from_float(1.0), bits_from_float(0.0), bits_from_float(0.0)],
                }
            ],
        )
        plan = generate_native_plan(audit, requested, config())
        fresh_session = deepcopy(audited_session)
        fresh_session["session_id"] = "native-session-" + "e" * 32
        fresh_session["pid"] += 1
        fresh_session["process"]["instance_fingerprint"] = canonical_sha256(
            {"precision": "fresh-session"}
        )
        fresh_session["current_document"]["database_instance_fingerprint"] = canonical_sha256(
            {"precision": "database"}
        )
        fresh_session["current_document"]["revision_fingerprint"] = canonical_sha256(
            {"precision": "revision"}
        )
        fresh_session["challenge_response"] = derive_challenge_response(
            fresh_session["client_nonce"],
            fresh_session["challenge"],
            fresh_session["bridge_nonce"],
            session_id=fresh_session["session_id"],
        )
        fresh_session = attach_integrity(fresh_session)
        fresh = geometry(
            deepcopy(before["entities"]),
            source_value=before["source"],
            session_value=fresh_session,
        )
        with self.assertRaises(PipelineError) as raised:
            build_native_manifest(
                audit,
                plan,
                requested,
                fresh,
                fresh_session,
                config(),
                private_source_copy={
                    "sha256": before["source"]["sha256"],
                    "byte_size": before["source"]["byte_size"],
                    "file_identity_fingerprint": "f" * 64,
                },
                output_path=Path("generated-precision-output.dwg"),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_MANIFEST_INVALID)

    def test_plan_id_binds_exact_requested_delta_bits(self) -> None:
        audit = build_native_audit(geometry(), session(), config())
        target = audit["records"][0]["target_id"]
        plans = []
        for delta in (1.0, math.nextafter(1.0, math.inf)):
            requested = intent(
                audit,
                operations=[
                    {
                        "operation_id": "native-operation-" + "b" * 24,
                        "kind": "translate_dbtext",
                        "target_id": target,
                        "delta": [bits_from_float(delta), bits_from_float(0.0), bits_from_float(0.0)],
                    }
                ],
            )
            plans.append(generate_native_plan(audit, requested, config()))
        self.assertNotEqual(plans[0]["plan_id"], plans[1]["plan_id"])
        self.assertNotEqual(
            plans[0]["operations"][0]["allowed_delta_digest"],
            plans[1]["operations"][0]["allowed_delta_digest"],
        )

    def test_audit_is_redacted_and_report_never_leaks_geometry(self) -> None:
        raw = geometry([entity("ABCD", text="secret-overlay", position=(42.0, 99.0, 0.0))])
        audit = build_native_audit(raw, session(), config())
        serialized = json.dumps(audit, ensure_ascii=False)
        report = render_native_audit_report(audit)
        for forbidden in ("secret-overlay", "TEMP"):
            self.assertNotIn(forbidden, serialized)
            self.assertNotIn(forbidden, report)

    def test_private_artifacts_admit_explicit_cardinality_while_reports_do_not(self) -> None:
        """Arrays in local artifacts cannot truthfully claim hidden cardinality."""

        one = build_native_audit(geometry([entity("10")]), session(), config())
        many = build_native_audit(
            geometry([entity("10"), entity("11", sequence_index=1)]),
            session(),
            config(),
        )
        self.assertEqual(one["record_cardinality"], "explicit_private")
        self.assertEqual(many["record_cardinality"], "explicit_private")
        self.assertNotIn("cardinality_disclosed", one)
        forged = deepcopy(many)
        forged["record_cardinality"] = "redacted"
        forged = attach_integrity(forged)
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract("audit", forged)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_AUDIT_SCHEMA_INVALID)

        # Both reports have actionable conclusions, but their structure and
        # wording remain identical regardless of the locally visible record
        # array length.  The plan summary similarly has no count-bearing slot.
        self.assertEqual(
            render_native_audit_report(one),
            render_native_audit_report(many),
        )
        self.assertEqual(
            render_native_plan_review({"operations": [{"opaque": "one"}]}),
            render_native_plan_review({"operations": [{"opaque": "many"}] * 9}),
        )

    def test_legacy_oda_artifact_cannot_enter_native_contract(self) -> None:
        with self.assertRaises(PipelineError) as raised:
            validate_native_contract(
                "audit",
                attach_integrity(
                    {
                        "schema_version": "liang-pingfa/audit/v1",
                        "audit_id": "audit-" + "0" * 32,
                    }
                ),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_AUDIT_SCHEMA_INVALID)


@unittest.skipUnless(os.name == "nt", "private native DACL checks are Windows-only")
class NativePrivateArtifactAclTests(unittest.TestCase):
    """Use generated JSON and ACLs only; no drawing or external host is read."""

    @staticmethod
    def _grant(root: Path, sid: str, right: str) -> None:
        result = subprocess.run(
            [
                "icacls",
                str(root),
                "/grant",
                f"*{sid}:(OI)(CI){right}",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("cannot create generated ACL probe")

    @staticmethod
    def _set_owner(path: Path, sid: str) -> None:
        """Assign a generated test owner without reading any external input."""

        result = subprocess.run(
            ["icacls", str(path), "/setowner", f"*{sid}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("cannot reassign generated private-file owner")

    @staticmethod
    def _everyone_read_access(path: Path) -> tuple[int, int]:
        """Ask Authz in a second process whether the Everyone SID can read."""

        probe = r'''
import ctypes
from ctypes import wintypes
import sys

class Luid(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]

class AccessRequest(ctypes.Structure):
    _fields_ = [
        ("DesiredAccess", wintypes.DWORD),
        ("PrincipalSelfSid", ctypes.c_void_p),
        ("ObjectTypeList", ctypes.c_void_p),
        ("ObjectTypeListLength", wintypes.DWORD),
        ("OptionalArguments", ctypes.c_void_p),
    ]

class AccessReply(ctypes.Structure):
    _fields_ = [
        ("ResultListLength", wintypes.DWORD),
        ("GrantedAccessMask", ctypes.POINTER(wintypes.DWORD)),
        ("SaclEvaluationResults", ctypes.POINTER(wintypes.DWORD)),
        ("Error", ctypes.POINTER(wintypes.DWORD)),
    ]

advapi = ctypes.WinDLL("advapi32", use_last_error=True)
authz = ctypes.WinDLL("authz", use_last_error=True)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
security_information = 0x00000007
file_generic_read = 0x00120089

size = wintypes.DWORD()
advapi.GetFileSecurityW(sys.argv[1], security_information, None, 0, ctypes.byref(size))
descriptor = ctypes.create_string_buffer(size.value)
if not advapi.GetFileSecurityW(
    sys.argv[1], security_information, descriptor, size.value, ctypes.byref(size)
):
    raise ctypes.WinError(ctypes.get_last_error())

sid = ctypes.c_void_p()
advapi.ConvertStringSidToSidW.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.c_void_p),
]
if not advapi.ConvertStringSidToSidW("S-1-1-0", ctypes.byref(sid)):
    raise ctypes.WinError(ctypes.get_last_error())
manager = ctypes.c_void_p()
authz.AuthzInitializeResourceManager.argtypes = [
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.c_void_p),
]
if not authz.AuthzInitializeResourceManager(
    0x1, None, None, None, "generated-private-artifact-probe", ctypes.byref(manager)
):
    raise ctypes.WinError(ctypes.get_last_error())
context = ctypes.c_void_p()
authz.AuthzInitializeContextFromSid.argtypes = [
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    Luid,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
if not authz.AuthzInitializeContextFromSid(
    0, sid, manager, None, Luid(), None, ctypes.byref(context)
):
    raise ctypes.WinError(ctypes.get_last_error())
granted = wintypes.DWORD()
sacl = wintypes.DWORD()
error = wintypes.DWORD()
request = AccessRequest(file_generic_read, None, None, 0, None)
reply = AccessReply(
    1,
    ctypes.pointer(granted),
    ctypes.pointer(sacl),
    ctypes.pointer(error),
)
access_handle = wintypes.HANDLE()
authz.AuthzAccessCheck.argtypes = [
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.POINTER(AccessRequest),
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(AccessReply),
    ctypes.POINTER(wintypes.HANDLE),
]
if not authz.AuthzAccessCheck(
    0,
    context,
    ctypes.byref(request),
    None,
    ctypes.byref(descriptor),
    None,
    0,
    ctypes.byref(reply),
    ctypes.byref(access_handle),
):
    raise ctypes.WinError(ctypes.get_last_error())
print(f"{granted.value} {error.value}")
if access_handle.value:
    authz.AuthzFreeHandle(access_handle)
authz.AuthzFreeContext(context)
authz.AuthzFreeResourceManager(manager)
kernel.LocalFree(sid)
'''
        result = subprocess.run(
            [sys.executable, "-c", probe, str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("Authz generated second-process probe unavailable")
        return tuple(map(int, result.stdout.split()))  # type: ignore[return-value]

    def test_private_json_stays_private_while_markdown_inherits_public_read(self) -> None:
        """A public parent affects only the explicit public Markdown member."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Everyone gets only inherited read/execute: enough to make the
            # Markdown public, never enough to delete/replace the private
            # retained JSON final.
            self._grant(root, "S-1-1-0", "RX")
            descriptor = session()
            machine = root / "native-session.json"
            report = root / "native-session.md"
            publish_artifacts(
                (
                    ArtifactPublication(
                        machine,
                        canonical_json_bytes(descriptor) + b"\n",
                        private=True,
                    ),
                    ArtifactPublication(
                        report,
                        b"# Native session summary\n\nNo private cardinality.\n",
                        private=False,
                    ),
                )
            )

            # The controller can reopen the private JSON through the same
            # production DACL verifier after its no-replace final rename.
            self.assertEqual(
                load_native_artifact("session", machine)["session_id"],
                descriptor["session_id"],
            )
            self.assertNotIn("explicit_private", report.read_text(encoding="utf-8"))

            # A separate process uses Authz with the untrusted Everyone SID,
            # rather than this controller's allowed current-user SID. It can
            # read inherited public Markdown but is denied the protected JSON.
            private_granted, private_error = self._everyone_read_access(machine)
            public_granted, public_error = self._everyone_read_access(report)
            self.assertEqual((private_granted, private_error), (0, 5))
            self.assertEqual(public_error, 0)
            self.assertEqual(public_granted & 0x00120089, 0x00120089)

    def test_broad_private_input_dacl_is_rejected_without_consuming_file(self) -> None:
        """A persisted native machine file must not be accepted when broadened."""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "native-session.json"
            descriptor = session()
            publish_private_artifacts(
                ((path, canonical_json_bytes(descriptor) + b"\n"),)
            )
            grant = subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/grant",
                    "*S-1-1-0:R",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if grant.returncode != 0:
                self.skipTest("cannot broaden generated private file ACL")
            with self.assertRaises(PipelineError) as raised:
                load_native_artifact("session", path)
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
            self.assertTrue(path.exists())

    def test_dacl_safe_private_input_rejects_untrusted_generated_owners(self) -> None:
        """Owner validation is independent from the current-user/SYSTEM DACL."""

        for sid in (
            "S-1-5-32-545",  # Builtin Users
            "S-1-1-0",  # Everyone
            "S-1-5-21-424242",  # unrelated account
        ):
            with self.subTest(owner=sid), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "native-session.json"
                descriptor = session()
                publish_private_artifacts(
                    ((path, canonical_json_bytes(descriptor) + b"\n"),)
                )
                self._set_owner(path, sid)
                with self.assertRaises(PipelineError) as raised:
                    load_native_artifact("session", path)
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
                self.assertTrue(path.exists())

    def test_system_owned_generated_private_input_remains_acceptable_when_supported(self) -> None:
        """SYSTEM is the sole service owner accepted by the private-file policy."""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "native-session.json"
            descriptor = session()
            publish_private_artifacts(((path, canonical_json_bytes(descriptor) + b"\n"),))
            self._set_owner(path, "S-1-5-18")
            self.assertEqual(
                load_native_artifact("session", path)["session_id"],
                descriptor["session_id"],
            )

    def test_parent_child_add_rights_pass_but_delete_child_fails(self) -> None:
        """Direct FILE_ADD_FILE/FILE_ADD_SUBDIRECTORY do not imply replacement."""

        for label, right, should_pass in (
            ("add-file", "(WD)", True),
            ("add-subdirectory", "(AD)", True),
            ("delete-child", "(DC)", False),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._grant(root, "S-1-5-11", right)
                target = root / "generated-private.json"
                if should_pass:
                    publish_private_artifacts(((target, b'{"generated":true}\n'),))
                    self.assertTrue(target.exists())
                else:
                    with self.assertRaises(PipelineError) as raised:
                        publish_private_artifacts(((target, b'{"generated":true}\n'),))
                    self.assertEqual(
                        raised.exception.code,
                        ErrorCode.ATOMIC_PUBLISH_FAILED,
                    )
                    self.assertFalse(target.exists())


@unittest.skipUnless(shutil.which("dotnet"), ".NET SDK is required for wire conformance")
class NativeCsharpWireConformanceTests(unittest.TestCase):
    """Serialize generated C# fixtures and validate the packaged Python schema."""

    def test_csharp_wire_dtos_match_response_schema_and_declared_fields(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contracts = (
            root / "native-bridge-contracts" / "LiangPingfa.NativeBridge.Contracts.csproj"
        ).as_posix()
        project = (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <OutputType>Exe</OutputType>\n"
            "    <TargetFramework>net8.0</TargetFramework>\n"
            "    <ImplicitUsings>enable</ImplicitUsings>\n"
            "    <Nullable>enable</Nullable>\n"
            "    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>\n"
            "  </PropertyGroup>\n"
            "  <ItemGroup>\n"
            f'    <ProjectReference Include="{contracts}" />\n'
            "  </ItemGroup>\n"
            "</Project>\n"
        )
        program = r'''
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using LiangPingfa.NativeBridge.Contracts;

var sha = new string('a', 64);
var nonce = new string('b', 43);
var id = new string('c', 32);
var adapter = new NativeWireAdapterV1("test-adapter", "test-profile", "1.0.0");
var plugin = new NativeWirePluginV1("test-plugin", "1.0.0", sha);
var host = new NativeWireHostV1(
    "external-host",
    "1.0",
    "test-runtime",
    NativeWireHostModeV1.full_host);
var document = new NativeCurrentDocumentV1(true, sha, sha, sha, 128, "AC1032", sha, sha);
var capabilities = new[] { "read.inventory/v1", "read.exact_geometry/v1" };
var responses = new object[]
{
    new NativeHealthResponseV1(
        NativeBridgeProtocolV1.Version,
        id,
        new NativeBridgeHealthResultV1(
            NativeWireResultKindV1.health,
            NativeBridgeProtocolV1.Major,
            NativeBridgeProtocolV1.Minor,
            adapter,
            plugin,
            host,
            capabilities)),
    new NativeSessionHandshakeResponseV1(
        NativeBridgeProtocolV1.Version,
        id,
        new NativeSessionHandshakeResultV1(
            NativeWireResultKindV1.session,
            nonce,
            sha,
            adapter,
            plugin,
            host,
            capabilities,
            document)),
    new NativeCurrentDocumentResponseV1(
        NativeBridgeProtocolV1.Version,
        id,
        new NativeCurrentDocumentResultV1(NativeWireResultKindV1.document, document)),
    new NativeInventoryResponseV1(
        NativeBridgeProtocolV1.Version,
        id,
        new NativeInventoryExportV1(NativeWireResultKindV1.inventory, "{}")),
    new NativeExactGeometryResponseV1(
        NativeBridgeProtocolV1.Version,
        id,
        new NativeExactGeometryExportV1(NativeWireResultKindV1.geometry, "{}")),
    new NativeBridgeFailureResponseV1(
        NativeBridgeProtocolV1.Version,
        id,
        new NativeBridgeErrorV1(NativeWireErrorCodeV1.INTERNAL_ERROR)),
};

Console.WriteLine(JsonSerializer.Serialize(new
{
    shapes = new Dictionary<string, string[]>
    {
        [nameof(NativeBridgeHealthResultV1)] = Names<NativeBridgeHealthResultV1>(),
        [nameof(NativeSessionHandshakeResultV1)] = Names<NativeSessionHandshakeResultV1>(),
        [nameof(NativeCurrentDocumentResultV1)] = Names<NativeCurrentDocumentResultV1>(),
        [nameof(NativeInventoryExportV1)] = Names<NativeInventoryExportV1>(),
        [nameof(NativeExactGeometryExportV1)] = Names<NativeExactGeometryExportV1>(),
        [nameof(NativeCurrentDocumentV1)] = Names<NativeCurrentDocumentV1>(),
        [nameof(NativeSessionHandshakeParametersV1)] = Names<NativeSessionHandshakeParametersV1>(),
        [nameof(NativeDocumentBoundParametersV1)] = Names<NativeDocumentBoundParametersV1>(),
    },
    responses,
}));

static string[] Names<T>() =>
    typeof(T).GetProperties()
        .Select(property => property.GetCustomAttribute<JsonPropertyNameAttribute>()?.Name)
        .Where(name => name is not null)
        .Select(name => name!)
        .OrderBy(name => name, StringComparer.Ordinal)
        .ToArray();
'''
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            (temporary_root / "WireConformance.csproj").write_text(
                project,
                encoding="utf-8",
            )
            (temporary_root / "Program.cs").write_text(program, encoding="utf-8")
            try:
                result = subprocess.run(
                    [
                        "dotnet",
                        "run",
                        "--project",
                        str(temporary_root / "WireConformance.csproj"),
                        "--configuration",
                        "Release",
                        "--nologo",
                    ],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                )
            finally:
                # A ProjectReference normally emits into the contract project.
                # This generated test must leave the repository validator's
                # artifact policy exactly as it found it.
                for generated in (
                    root / "native-bridge-contracts" / "bin",
                    root / "native-bridge-contracts" / "obj",
                ):
                    shutil.rmtree(generated, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        payload = json.loads(
            next(
                line
                for line in reversed(result.stdout.splitlines())
                if line.startswith("{")
            )
        )
        expected_shapes = {
            "NativeBridgeHealthResultV1": {
                "kind",
                "protocol_major",
                "protocol_minor",
                "adapter",
                "plugin",
                "host",
                "capabilities",
            },
            "NativeSessionHandshakeResultV1": {
                "kind",
                "bridge_nonce",
                "challenge_response",
                "adapter",
                "plugin",
                "host",
                "capabilities",
                "current_document",
            },
            "NativeCurrentDocumentResultV1": {"kind", "current_document"},
            "NativeInventoryExportV1": {"kind", "inventory_json"},
            "NativeExactGeometryExportV1": {"kind", "geometry_json"},
            "NativeCurrentDocumentV1": {
                "saved",
                "path_fingerprint",
                "file_identity_fingerprint",
                "sha256",
                "byte_size",
                "dwg_header_signature",
                "database_instance_fingerprint",
                "revision_fingerprint",
            },
            "NativeSessionHandshakeParametersV1": {
                "session_id",
                "client_nonce",
                "challenge",
            },
            "NativeDocumentBoundParametersV1": {
                "session_id",
                "expected_document_revision",
            },
        }
        self.assertEqual(
            {name: set(properties) for name, properties in payload["shapes"].items()},
            expected_shapes,
        )
        for response in payload["responses"]:
            with self.subTest(kind=response.get("result", {}).get("kind", "failure")):
                self.assertEqual(
                    validate_native_contract("response", response),
                    response,
                )


class NativeGeometryUtf8ByteLimitTests(unittest.TestCase):
    """Prove the 16 MiB geometry limit counts UTF-8 bytes, not characters."""

    @staticmethod
    def _require(value: str) -> str:
        return require_geometry_json_utf8_bytes(
            value,
            error=ErrorCode.NATIVE_GEOMETRY_INVALID,
        )

    def test_ascii_exactly_at_and_over_the_utf8_byte_cap(self) -> None:
        at_limit = "a" * MAX_NATIVE_GEOMETRY_JSON_BYTES
        self.assertIs(self._require(at_limit), at_limit)
        over_limit = at_limit + "a"
        with self.assertRaises(PipelineError) as raised:
            self._require(over_limit)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)

    def test_bounded_canonical_stream_preserves_exact_geometry_wire_bytes(self) -> None:
        exported = geometry()
        self.assertEqual(
            canonical_geometry_json_bytes(
                exported,
                error=ErrorCode.NATIVE_GEOMETRY_INVALID,
            ),
            canonical_json_bytes(exported),
        )

    def test_chinese_and_four_byte_unicode_count_encoded_bytes(self) -> None:
        chinese = "中"
        chinese_at_limit = chinese * (
            MAX_NATIVE_GEOMETRY_JSON_BYTES // len(chinese.encode("utf-8"))
        )
        self.assertLessEqual(
            len(chinese_at_limit.encode("utf-8")),
            MAX_NATIVE_GEOMETRY_JSON_BYTES,
        )
        self.assertIs(self._require(chinese_at_limit), chinese_at_limit)
        with self.assertRaises(PipelineError):
            self._require(chinese_at_limit + chinese)

        astral = "\U0001F600"
        astral_at_limit = astral * (
            MAX_NATIVE_GEOMETRY_JSON_BYTES // len(astral.encode("utf-8"))
        )
        self.assertEqual(
            len(astral_at_limit.encode("utf-8")),
            MAX_NATIVE_GEOMETRY_JSON_BYTES,
        )
        self.assertIs(self._require(astral_at_limit), astral_at_limit)
        with self.assertRaises(PipelineError):
            self._require(astral_at_limit + astral)

    def test_nfc_expansion_and_contraction_cannot_change_raw_byte_budget(self) -> None:
        # U+0344 expands under NFC from two UTF-8 bytes to two combining
        # scalars/four UTF-8 bytes. The raw boundary accepts the exact
        # received byte count and leaves canonical validation to the parser.
        expanding = "\u0344"
        self.assertGreater(
            len(unicodedata.normalize("NFC", expanding).encode("utf-8")),
            len(expanding.encode("utf-8")),
        )
        expanded_raw_at_limit = expanding * (
            MAX_NATIVE_GEOMETRY_JSON_BYTES // len(expanding.encode("utf-8"))
        )
        self.assertIs(self._require(expanded_raw_at_limit), expanded_raw_at_limit)

        # U+212B contracts to U+00C5 under NFC. A raw over-limit input must
        # still fail instead of gaining capacity by normalization.
        contracting = "\u212B"
        self.assertLess(
            len(unicodedata.normalize("NFC", contracting).encode("utf-8")),
            len(contracting.encode("utf-8")),
        )
        raw_over_limit = contracting * (
            MAX_NATIVE_GEOMETRY_JSON_BYTES // len(contracting.encode("utf-8")) + 1
        )
        with self.assertRaises(PipelineError) as raised:
            self._require(raw_over_limit)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)

    def test_inventory_utf8_boundary_is_exact_and_independent(self) -> None:
        """Inventory receives its own opaque 64 KiB raw-carrier cap."""

        at_limit = "a" * native_contracts_module.MAX_NATIVE_INVENTORY_JSON_BYTES
        self.assertIs(
            require_inventory_json_utf8_bytes(
                at_limit,
                error=ErrorCode.NATIVE_PROTOCOL_INVALID,
            ),
            at_limit,
        )
        with self.assertRaises(PipelineError) as raised:
            require_inventory_json_utf8_bytes(
                at_limit + "a",
                error=ErrorCode.NATIVE_PROTOCOL_INVALID,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)

    def test_opaque_manifest_hash_uses_exact_carrier_and_keeps_valid_v1_hashes(
        self,
    ) -> None:
        """Outer carrier codepoints bind exactly without changing valid v1 bytes."""

        rules = native_contracts_module.opaque_embedded_json_rules("manifest")
        outer = {"preconditions_geometry_json": "\u0344"}
        encoded = canonical_json_bytes(outer, opaque_string_rules=rules)
        self.assertEqual(
            encoded,
            json.dumps(
                outer,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        self.assertEqual(
            canonical_sha256(outer, opaque_string_rules=rules),
            canonical_sha256(
                json.loads(encoded.decode("utf-8")),
                opaque_string_rules=rules,
            ),
        )

        # All previously valid carriers contain canonical inner JSON, so the
        # v1 digest is byte-identical under the explicit opaque semantics.
        valid_outer = {
            "preconditions_geometry_json": canonical_json_bytes(geometry()).decode(
                "utf-8"
            )
        }
        self.assertEqual(
            canonical_sha256(valid_outer),
            canonical_sha256(valid_outer, opaque_string_rules=rules),
        )

    def test_inner_geometry_scalar_cap_precedes_nfc_and_preserves_hangul_policy(
        self,
    ) -> None:
        """A 4,097-code-point text scalar is rejected before NFC work."""

        below_limit = "\u0344" * MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS
        above_limit = below_limit + "\u0344"
        calls: list[int] = []
        original_normalize = canonical_module.unicodedata.normalize

        def observe(form: str, value: str) -> str:
            calls.append(len(value))
            return original_normalize(form, value)

        with mock.patch.object(
            canonical_module.unicodedata,
            "normalize",
            side_effect=observe,
        ):
            with self.assertRaises(CanonicalJsonError):
                strict_native_json(
                    json.dumps({"text": below_limit}, ensure_ascii=False),
                    maximum_string_codepoints=MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS,
                )
            with self.assertRaises(CanonicalJsonError):
                strict_native_json(
                    json.dumps({"text": above_limit}, ensure_ascii=False),
                    maximum_string_codepoints=MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS,
                )

        self.assertIn(len(below_limit), calls)
        self.assertNotIn(len(above_limit), calls)
        self.assertTrue(
            all(length <= MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS for length in calls)
        )

        composed = "가"
        self.assertEqual(
            strict_native_json(
                json.dumps({"text": composed}, ensure_ascii=False),
                maximum_string_codepoints=MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS,
            )["text"],
            composed,
        )
        with self.assertRaises(CanonicalJsonError):
            strict_native_json(
                json.dumps({"text": "\u1100\u1161"}, ensure_ascii=False),
                maximum_string_codepoints=MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS,
            )

    def test_maximum_geometry_carrier_rejects_huge_inner_text_before_nfc(self) -> None:
        """A 16 MiB valid JSON carrier cannot normalize its oversized scalar."""

        prefix = '{"text":"'
        suffix = '"}'
        remaining = MAX_NATIVE_GEOMETRY_JSON_BYTES - len(
            (prefix + suffix).encode("utf-8")
        )
        carrier = (
            prefix
            + "\u0344" * (remaining // len("\u0344".encode("utf-8")))
            + "a" * (remaining % len("\u0344".encode("utf-8")))
            + suffix
        )
        self.assertEqual(
            len(carrier.encode("utf-8")),
            MAX_NATIVE_GEOMETRY_JSON_BYTES,
        )
        calls: list[int] = []
        original_normalize = canonical_module.unicodedata.normalize

        def bounded_normalize(form: str, value: str) -> str:
            calls.append(len(value))
            if len(value) > MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS:
                raise AssertionError("oversized inner text reached NFC")
            return original_normalize(form, value)

        with mock.patch.object(
            canonical_module.unicodedata,
            "normalize",
            side_effect=bounded_normalize,
        ):
            with self.assertRaises(PipelineError) as raised:
                native_contracts_module._embedded_geometry(
                    carrier,
                    error=ErrorCode.NATIVE_READBACK_INVALID,
                )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        self.assertTrue(
            all(length <= MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS for length in calls)
        )

    def test_private_loader_and_embedded_parser_reject_before_decoding_geometry(self) -> None:
        payload = b"x" * (MAX_NATIVE_GEOMETRY_JSON_BYTES + 1)

        def read_payload(
            _kind: str,
            _path: Path,
            *,
            consume,
            **_kwargs: object,
        ) -> dict[str, object]:
            return consume(payload)

        with mock.patch.object(
            native_contracts_module,
            "read_private_native_artifact_bytes",
            side_effect=read_payload,
        ):
            with self.assertRaises(PipelineError) as raised:
                load_native_artifact("geometry", Path("generated-geometry.json"))
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)

        raw_embedded = "中" * (
            MAX_NATIVE_GEOMETRY_JSON_BYTES // len("中".encode("utf-8")) + 1
        )
        with mock.patch.object(native_contracts_module, "strict_native_json") as parser:
            with self.assertRaises(PipelineError) as raised:
                native_contracts_module._embedded_geometry(
                    raw_embedded,
                    error=ErrorCode.NATIVE_READBACK_INVALID,
                )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        parser.assert_not_called()

    def test_two_thousand_entities_with_four_kib_text_fail_before_audit_binding(self) -> None:
        """Reproduce the character-count bypass with valid v1 cardinality."""

        oversized = geometry()
        generated = deepcopy(oversized["entities"][0])
        generated["text"] = "中" * 4096
        oversized["entities"] = [generated] * MAX_NATIVE_GEOMETRY_ENTITIES
        with mock.patch(
            "liang_pingfa_review.native_audit.require_geometry_export_matches_session"
        ) as binding:
            with self.assertRaises(PipelineError) as raised:
                build_native_audit(oversized, session(), config())
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)
        binding.assert_not_called()


if __name__ == "__main__":
    unittest.main()
