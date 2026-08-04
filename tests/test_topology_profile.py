"""Generated-fixture regression coverage for audit/v2 topology evidence."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ezdxf
from ezdxf.enums import TextEntityAlignment

from liang_pingfa_review.audit import build_audit
from liang_pingfa_review.canonical import (
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
    describe_source,
    verify_integrity,
)
from liang_pingfa_review import cli
from liang_pingfa_review.cli import build_parser
from liang_pingfa_review.contracts import (
    audit_semantic_projection,
    schema_for,
    validate_artifact,
)
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.oda import SUPPORTED_ODA_VERSION
from liang_pingfa_review.plan import generate_edit_plan
from liang_pingfa_review.snapshots import snapshot_dxf
import liang_pingfa_review.snapshots as snapshots
import liang_pingfa_review.topology_profile as topology_profile
from liang_pingfa_review.verify import assert_postconditions
from liang_pingfa_review.topology_profile import (
    Aabb,
    MAX_CHAIN_RELATIONS,
    MAX_DERIVED_SCALAR,
    MAX_INPUT_COORDINATE,
    MAX_ROLE_ENTITIES,
    extract_topology_evidence,
    load_topology_profile,
    topology_snapshot_context,
)
from liang_pingfa_review.topology_ids import (
    derive_annotation_target_provenance_id,
    derive_topology_finding_id,
    derive_trace_id,
)
from tests.support.synthetic_dxf import (
    create_fake_dwg,
    create_synthetic_dxf,
    create_topology_dxf,
    delete_audited_text_in_synthetic_dxf,
    topology_profile_payload,
)


class TopologyProfileTests(unittest.TestCase):
    """Exercise the opt-in, bounded, permanently non-actionable v2 branch."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source.dwg"
        self.profile_path = self.root / "profile.json"
        create_fake_dwg(self.source)
        self.profile_path.write_text(
            json.dumps(topology_profile_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        self.profile = load_topology_profile(self.profile_path)
        self.now = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def snapshot_topology(self, path: Path, *, profile: object | None = None) -> object:
        """Snapshot v2 evidence using the profile's validated role boundary."""

        return snapshot_dxf(
            path,
            include_topology_evidence=True,
            topology_context=topology_snapshot_context(profile or self.profile),
        )

    def audit_topology(
        self,
        *,
        variant: str = "consistent",
        orientation_degrees: float = 0.0,
        translation: tuple[float, float] = (0.0, 0.0),
        scale: float = 1.0,
        reversed_edges: bool = False,
        shuffle_seed: int | None = None,
        support_vertex_start: int = 0,
        reverse_support_winding: bool = False,
    ) -> dict[str, object]:
        dxf = self.root / (
            f"{variant}-{orientation_degrees}-{scale}-{reversed_edges}-{shuffle_seed}.dxf"
        )
        create_topology_dxf(
            dxf,
            variant=variant,
            orientation_degrees=orientation_degrees,
            translation=translation,
            scale=scale,
            reversed_edges=reversed_edges,
            shuffle_seed=shuffle_seed,
            support_vertex_start=support_vertex_start,
            reverse_support_winding=reverse_support_winding,
        )
        return build_audit(
            self.snapshot_topology(dxf),
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=self.now,
            topology_profile=self.profile,
        )

    def audit_on_work_plane(
        self,
        *,
        plane: float,
        line_residual: float = 0.0,
        mixed_plane: bool = False,
    ) -> dict[str, object]:
        """Build one generated fixture whose source stays on a chosen WCS plane."""

        dxf = self.root / (
            f"work-plane-{plane!r}-{line_residual!r}-{mixed_plane}.dxf"
        )
        create_topology_dxf(dxf)
        document = ezdxf.readfile(dxf)
        modelspace = document.modelspace()
        residual_applied = False
        beam_lines = 0
        for entity in modelspace:
            if entity.dxftype() == "LINE":
                start = entity.dxf.start
                end = entity.dxf.end
                z = plane
                if entity.dxf.layer == "BEAM":
                    beam_lines += 1
                    if mixed_plane and beam_lines == 2:
                        z = plane + topology_profile._PLANE_TOLERANCE * 2.0
                entity.dxf.start = (start.x, start.y, z)
                entity.dxf.end = (
                    end.x,
                    end.y,
                    z + (line_residual if not residual_applied else 0.0),
                )
                residual_applied = True
            elif entity.dxftype() == "LWPOLYLINE":
                entity.dxf.elevation = plane
            elif entity.dxftype() == "TEXT":
                insert = entity.dxf.insert
                entity.dxf.insert = (insert.x, insert.y, plane)
        document.saveas(dxf)
        return build_audit(
            self.snapshot_topology(dxf),
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=self.now,
            topology_profile=self.profile,
        )

    @staticmethod
    def topology_statuses(audit: dict[str, object]) -> list[tuple[str, str]]:
        topology = audit["topology_assessment"]  # type: ignore[index]
        return sorted(
            (finding["category"], finding["status"])  # type: ignore[index]
            for finding in topology["findings"]  # type: ignore[index]
        )

    @staticmethod
    def topology_ids_and_statuses(audit: dict[str, object]) -> dict[str, object]:
        assessment = audit["topology_assessment"]  # type: ignore[index]
        return {
            "findings": sorted(
                (
                    finding["finding_id"],
                    finding["category"],
                    finding["status"],
                )
                for finding in assessment["findings"]  # type: ignore[index]
            ),
            "chains": sorted(
                (
                    chain["chain_id"],
                    tuple(
                        support["support_id"] for support in chain["supports"]
                    ),
                    tuple(span["span_id"] for span in chain["spans"]),
                )
                for chain in assessment["chains"]  # type: ignore[index]
            ),
        }

    def assert_incomplete_topology_covers_every_candidate(
        self,
        audit: dict[str, object],
        expected_categories: dict[str, str],
    ) -> None:
        """Require one canonical ambiguity finding for each visible input."""

        assessment = audit["topology_assessment"]  # type: ignore[index]
        traces = {
            trace["entity_handle"]: trace
            for trace in assessment["traces"]  # type: ignore[index]
        }
        findings_by_trace: dict[str, list[object]] = {}
        for finding in assessment["findings"]:  # type: ignore[index]
            findings_by_trace.setdefault(finding["trace_ids"][0], []).append(finding)

        self.assertEqual(set(traces), set(expected_categories))
        self.assertEqual(set(findings_by_trace), {trace["trace_id"] for trace in traces.values()})
        for handle, category in expected_categories.items():
            with self.subTest(handle=handle, category=category):
                trace = traces[handle]
                self.assertEqual(trace["role"], "ambiguity")
                candidate_findings = findings_by_trace[trace["trace_id"]]
                self.assertEqual(len(candidate_findings), 1)
                finding = candidate_findings[0]
                self.assertEqual(finding["category"], category)
                self.assertEqual(finding["status"], "证据不足")
                self.assertFalse(finding["actionability"])
                self.assertIsNone(finding["target_id"])
        validate_artifact("audit", audit)

    def assert_required_trace_coverage(
        self,
        audit: dict[str, object],
        *,
        required_count: int,
        chainless_beam_count: int,
    ) -> None:
        """Require exactly one canonical insufficiency per unresolved trace."""

        assessment = audit["topology_assessment"]  # type: ignore[index]
        traces = assessment["traces"]
        findings_by_trace: dict[str, list[object]] = {}
        for finding in assessment["findings"]:
            findings_by_trace.setdefault(finding["trace_ids"][0], []).append(finding)

        required = [
            trace
            for trace in traces
            if trace["role"] == "ambiguity"
            or (
                trace["role"] == "beam_edges"
                and trace["chain_id"] is None
            )
        ]
        self.assertEqual(len(required), required_count)
        self.assertEqual(
            sum(trace["role"] == "beam_edges" for trace in required),
            chainless_beam_count,
        )
        for trace in required:
            with self.subTest(trace_id=trace["trace_id"]):
                coverage = findings_by_trace.get(trace["trace_id"], [])
                self.assertEqual(len(coverage), 1)
                finding = coverage[0]
                self.assertEqual(finding["status"], "证据不足")
                self.assertFalse(finding["actionability"])
                self.assertIsNone(finding["target_id"])
                self.assertEqual(
                    finding["finding_id"],
                    derive_topology_finding_id(
                        trace["trace_id"],
                        finding["status"],
                        trace["role"],
                        trace["chain_id"],
                        trace["support_id"],
                        trace["span_id"],
                    ),
                )

        admitted_beam_trace_ids = {
            trace["trace_id"]
            for trace in traces
            if trace["role"] == "beam_edges" and trace["chain_id"] is not None
        }
        self.assertFalse(admitted_beam_trace_ids & set(findings_by_trace))
        validate_artifact("audit", audit)

    def audit_incomplete_topology(
        self,
        name: str,
        mutate,
    ) -> tuple[dict[str, object], dict[str, str]]:
        """Create one soft-incomplete topology fixture and its candidates."""

        dxf = self.root / f"incomplete-{name}.dxf"
        create_topology_dxf(dxf)
        document = ezdxf.readfile(dxf)
        mutate(document)
        document.saveas(dxf)
        role_categories = {
            "UPPER": "support_upper_annotation",
            "LOWER": "span_lower_annotation",
        }
        controlled_layers = {
            "BEAM",
            "BEAM_ID",
            "SUPPORT",
            "UPPER",
            "LOWER",
            "LEADER",
        }
        expected_categories = {
            str(entity.dxf.handle).upper(): role_categories.get(
                str(entity.dxf.layer),
                "topology",
            )
            for entity in document.modelspace()
            if str(entity.dxf.layer) in controlled_layers
        }
        audit = build_audit(
            self.snapshot_topology(dxf),
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=self.now,
            topology_profile=self.profile,
        )
        return audit, expected_categories

    def assert_resigned_forgery_is_rejected(self, forged: dict[str, object]) -> None:
        """Integrity must not turn forged topology evidence into valid evidence."""

        resigned = attach_integrity(forged)
        self.assertTrue(verify_integrity(resigned))
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("audit", resigned)
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)

    def test_profile_loads_only_closed_role_schema(self) -> None:
        profile = load_topology_profile(self.profile_path)
        self.assertEqual(profile.roles.beam_edges, frozenset({"beam"}))
        normalized_payload = topology_profile_payload(
            layers={"beam_edges": ["Be\u0301AM"]}
        )
        normalized_path = self.root / "normalized.json"
        normalized_path.write_text(
            json.dumps(normalized_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        self.assertEqual(
            load_topology_profile(normalized_path).roles.beam_edges,
            frozenset({"béam"}),
        )
        cases = (
            {
                "layers": {
                    **topology_profile_payload()["layers"],  # type: ignore[index]
                    "beam_ids": ["BEAM"],
                }
            },
            {
                "layers": {
                    **topology_profile_payload()["layers"],  # type: ignore[index]
                    "beam_edges": ["TEMP"],
                }
            },
            {"unexpected": True},
        )
        for index, mutation in enumerate(cases):
            with self.subTest(index=index):
                payload = topology_profile_payload()
                payload.update(mutation)
                path = self.root / f"invalid-{index}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(PipelineError) as raised:
                    load_topology_profile(path)
                self.assertEqual(raised.exception.code, ErrorCode.TOPOLOGY_PROFILE_INVALID)

    def test_audit_v1_remains_no_profile_shape(self) -> None:
        dxf = self.root / "v1.dxf"
        create_synthetic_dxf(dxf)
        snapshot = snapshot_dxf(dxf)
        source = describe_source(self.source)
        fixed_uuid = type("FixedUuid", (), {"hex": "a" * 32})()
        with mock.patch("liang_pingfa_review.audit.uuid4", return_value=fixed_uuid):
            audit = build_audit(
                snapshot,
                source,
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
            )
        with mock.patch("liang_pingfa_review.audit.uuid4", return_value=fixed_uuid):
            explicit_none = build_audit(
                snapshot,
                source,
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
                topology_profile=None,
            )
        self.assertEqual(audit["schema_version"], "liang-pingfa/audit/v1")
        self.assertNotIn("topology_assessment", audit)
        self.assertNotIn(b"topology", canonical_json_bytes(audit))
        self.assertEqual(canonical_json_bytes(audit), canonical_json_bytes(explicit_none))
        self.assertTrue(
            all(record.topology_evidence is None for record in snapshot.records)
        )
        validate_artifact("audit", audit)

    def test_topology_snapshot_is_explicit_and_v1_keeps_full_fingerprints(self) -> None:
        """The opt-in analysis branch must not tax v1 snapshot construction."""

        dxf = self.root / "topology-opt-in.dxf"
        create_topology_dxf(dxf)
        with mock.patch(
            "liang_pingfa_review.snapshots._topology_evidence",
            return_value=None,
        ) as extract:
            v1_snapshot = snapshot_dxf(dxf)
        extract.assert_not_called()
        # The profile-derived role boundary is mandatory.  A boolean opt-in
        # alone has no eligible source layers and remains topology-free.
        with mock.patch(
            "liang_pingfa_review.snapshots._topology_evidence",
            return_value=None,
        ) as extract:
            unscoped_snapshot = snapshot_dxf(dxf, include_topology_evidence=True)
        extract.assert_not_called()
        v2_snapshot = self.snapshot_topology(dxf)
        self.assertTrue(
            all(record.topology_evidence is None for record in v1_snapshot.records)
        )
        self.assertTrue(
            all(
                record.topology_evidence is None
                for record in unscoped_snapshot.records
            )
        )
        self.assertTrue(
            any(record.topology_evidence is not None for record in v2_snapshot.records)
        )
        self.assertEqual(
            [(record.handle, record.content_fingerprint) for record in v1_snapshot.records],
            [(record.handle, record.content_fingerprint) for record in v2_snapshot.records],
        )

    def test_snapshot_extracts_only_eligible_role_entities(self) -> None:
        """Unrelated/display-excluded records never enter private extraction."""

        dxf = self.root / "eligible-only.dxf"
        create_topology_dxf(dxf)
        document = ezdxf.readfile(dxf)
        document.layers.new("UNRELATED")
        modelspace = document.modelspace()
        unrelated_handles: set[str] = set()
        for index in range(32):
            unrelated_handles.add(
                str(
                    modelspace.add_text(
                        "DO-NOT-RETAIN-PRIVATE-TEXT",
                        dxfattribs={
                            "layer": "UNRELATED",
                            "height": 2.0,
                            "insert": (float(index * 10), 200.0),
                        },
                    ).dxf.handle
                ).upper()
            )
            unrelated_handles.add(
                str(
                    modelspace.add_lwpolyline(
                        [(index * 10, 220), (index * 10 + 5, 220)],
                        dxfattribs={"layer": "UNRELATED"},
                    ).dxf.handle
                ).upper()
            )
        hidden = modelspace.add_text(
            "HIDDEN-CONTROLLED",
            dxfattribs={"layer": "UPPER", "height": 2.0, "insert": (0, 250)},
        )
        hidden.dxf.invisible = 1
        transparent = modelspace.add_text(
            "TRANSPARENT-CONTROLLED",
            dxfattribs={"layer": "LOWER", "height": 2.0, "insert": (20, 250)},
        )
        transparent.transparency = 1.0
        excluded_handles = {
            str(hidden.dxf.handle).upper(),
            str(transparent.dxf.handle).upper(),
        }
        paperspace = document.layout("Layout1")
        paperspace_text = paperspace.add_text(
            "PAPERSPACE-PRIVATE-TEXT",
            dxfattribs={"layer": "UPPER", "height": 2.0, "insert": (0, 0)},
        )
        paperspace_polyline = paperspace.add_lwpolyline(
            [(0, 0), (5, 0)],
            dxfattribs={"layer": "BEAM"},
        )
        block = document.blocks.new("UNRELATED_TOPOLOGY_BLOCK")
        block_text = block.add_text(
            "BLOCK-PRIVATE-TEXT",
            dxfattribs={"layer": "UPPER", "height": 2.0, "insert": (0, 0)},
        )
        block_polyline = block.add_lwpolyline(
            [(0, 0), (5, 0)],
            dxfattribs={"layer": "BEAM"},
        )
        excluded_handles.update(
            str(entity.dxf.handle).upper()
            for entity in (
                paperspace_text,
                paperspace_polyline,
                block_text,
                block_polyline,
            )
        )
        document.saveas(dxf)

        with mock.patch.object(
            snapshots,
            "_topology_evidence",
            wraps=snapshots._topology_evidence,
        ) as extract:
            snapshot = self.snapshot_topology(dxf)

        extracted_handles = {
            str(call.args[0].dxf.handle).upper() for call in extract.call_args_list
        }
        evidence_handles = {
            record.handle
            for record in snapshot.records
            if record.topology_evidence is not None
        }
        self.assertTrue(extracted_handles)
        self.assertEqual(extracted_handles, evidence_handles)
        self.assertFalse(extracted_handles & unrelated_handles)
        self.assertFalse(extracted_handles & excluded_handles)
        self.assertTrue(
            all(
                record.topology_evidence is None
                for record in snapshot.records
                if record.handle in unrelated_handles | excluded_handles
            )
        )

        assessment = build_audit(
            snapshot,
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=self.now,
            topology_profile=self.profile,
        )["topology_assessment"]
        serialized = canonical_json_bytes(assessment).decode("utf-8")
        for private_text in (
            "DO-NOT-RETAIN-PRIVATE-TEXT",
            "HIDDEN-CONTROLLED",
            "TRANSPARENT-CONTROLLED",
            "PAPERSPACE-PRIVATE-TEXT",
            "BLOCK-PRIVATE-TEXT",
        ):
            self.assertNotIn(private_text, serialized)

    def test_controlled_role_cap_stops_before_topology_extraction(self) -> None:
        """An over-cap controlled drawing fails before bbox/vertex/text work."""

        dxf = self.root / "over-cap-before-extraction.dxf"
        create_topology_dxf(dxf)
        with (
            mock.patch(
                "liang_pingfa_review.topology_profile.MAX_ROLE_ENTITIES",
                1,
            ),
            mock.patch.object(
                snapshots,
                "_topology_evidence",
                side_effect=AssertionError("topology extraction ran before cap"),
            ) as extract,
        ):
            with self.assertRaises(PipelineError) as raised:
                self.snapshot_topology(dxf)
        self.assertEqual(raised.exception.code, ErrorCode.TOPOLOGY_LIMIT_EXCEEDED)
        extract.assert_not_called()

    def test_consistent_geometry_emits_strict_read_only_v2(self) -> None:
        audit = self.audit_topology()
        self.assertEqual(audit["schema_version"], "liang-pingfa/audit/v2")
        assessment = audit["topology_assessment"]  # type: ignore[index]
        self.assertEqual(assessment["policy"], "beam-plan-in-situ/v1")
        self.assertEqual(assessment["authorization"], "topology-never-authorizes-edits")
        self.assertEqual(len(assessment["chains"]), 1)
        self.assertEqual(
            self.topology_statuses(audit),
            [
                ("span_lower_annotation", "一致"),
                ("support_upper_annotation", "一致"),
            ],
        )
        manifest = {
            item["handle"]: item
            for item in audit["inventory"]["entity_manifest"]  # type: ignore[index]
        }
        target_handles = {
            target["handle"] for target in audit["audited_targets"]  # type: ignore[index]
        }
        for trace in assessment["traces"]:
            self.assertIn(trace["entity_handle"], manifest)
            self.assertEqual(
                trace["identity_fingerprint"],
                manifest[trace["entity_handle"]]["identity_fingerprint"],
            )
            self.assertEqual(
                trace["content_fingerprint"],
                manifest[trace["entity_handle"]]["content_fingerprint"],
            )
            self.assertNotIn(trace["entity_handle"], target_handles)
        validate_artifact("audit", audit)

    def test_soft_incomplete_topology_binds_every_controlled_candidate(self) -> None:
        """No published incomplete topology may leave a candidate unreported."""

        def remove_all(document, query: str) -> None:
            modelspace = document.modelspace()
            for entity in list(modelspace.query(query)):
                modelspace.delete_entity(entity)

        cases = {
            "one-edge": lambda document: document.modelspace().delete_entity(
                list(document.modelspace().query('LINE[layer=="BEAM"]'))[1]
            ),
            "zero-axes": lambda document: document.modelspace().add_line(
                (0, 40),
                (200, 40),
                dxfattribs={"layer": "BEAM"},
            ),
            "missing-id": lambda document: remove_all(
                document,
                'TEXT[layer=="BEAM_ID"]',
            ),
            "no-support": lambda document: remove_all(
                document,
                'LWPOLYLINE[layer=="SUPPORT"]',
            ),
            "no-span": lambda document: [
                document.modelspace().delete_entity(entity)
                for entity in list(document.modelspace().query('LWPOLYLINE[layer=="SUPPORT"]'))[1:]
            ],
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                audit, expected_categories = self.audit_incomplete_topology(name, mutate)
                self.assert_incomplete_topology_covers_every_candidate(
                    audit,
                    expected_categories,
                )

        annotations_only = self.root / "incomplete-annotations-only.dxf"
        document = ezdxf.new("R2018")
        document.ezdxf_metadata().discard("CREATED_BY_EZDXF")
        modelspace = document.modelspace()
        for layer in ("UPPER", "LOWER"):
            document.layers.new(layer)
        upper = modelspace.add_text(
            "U1",
            dxfattribs={"layer": "UPPER", "height": 4, "insert": (0, 0)},
        )
        lower = modelspace.add_text(
            "L1",
            dxfattribs={"layer": "LOWER", "height": 4, "insert": (20, 0)},
        )
        document.saveas(annotations_only)
        audit = build_audit(
            self.snapshot_topology(annotations_only),
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=self.now,
            topology_profile=self.profile,
        )
        self.assert_incomplete_topology_covers_every_candidate(
            audit,
            {
                str(upper.dxf.handle).upper(): "support_upper_annotation",
                str(lower.dxf.handle).upper(): "span_lower_annotation",
            },
        )

        def add_unsupported_edge(document) -> None:
            document.modelspace().add_lwpolyline(
                [(20, 40, 0, 0, 0.5), (80, 40, 0, 0, 0)],
                dxfattribs={"layer": "BEAM"},
                format="xyseb",
            )

        unsupported, expected_categories = self.audit_incomplete_topology(
            "unsupported-controlled-geometry",
            add_unsupported_edge,
        )
        self.assert_incomplete_topology_covers_every_candidate(
            unsupported,
            expected_categories,
        )

    def test_misplaced_annotations_are_read_only_suspicions(self) -> None:
        expected_unique = {
            "wrong-side-upper": "support_upper_annotation",
            "lower-outside-midspan": "span_lower_annotation",
            "crossing-beam": "span_lower_annotation",
        }
        for variant, category in expected_unique.items():
            with self.subTest(variant=variant):
                audit = self.audit_topology(variant=variant)
                statuses = self.topology_statuses(audit)
                self.assertIn((category, "疑似不一致"), statuses)
                assessment = audit["topology_assessment"]  # type: ignore[index]
                finding = next(
                    item
                    for item in assessment["findings"]
                    if item["category"] == category
                    and item["status"] == "疑似不一致"
                )
                trace = next(
                    item
                    for item in assessment["traces"]
                    if item["trace_id"] == finding["trace_ids"][0]
                )
                self.assertEqual(
                    trace["role"],
                    (
                        "support_upper_annotations"
                        if category == "support_upper_annotation"
                        else "span_lower_annotations"
                    ),
                )
                self.assertIsNotNone(trace["chain_id"])
                self.assertIsNotNone(trace["target_provenance_id"])
                self.assertEqual(
                    trace["support_id"] is not None,
                    category == "support_upper_annotation",
                )
                self.assertEqual(
                    trace["span_id"] is not None,
                    category == "span_lower_annotation",
                )
        # A label crossing a support or entering another span has more than
        # one exact interval owner.  It remains insufficient rather than
        # inheriting a nearest or arbitrarily selected target.
        for variant in ("crossing-support", "entering-next-span"):
            with self.subTest(variant=variant):
                self.assertIn(
                    ("span_lower_annotation", "证据不足"),
                    self.topology_statuses(self.audit_topology(variant=variant)),
                )
        # Without a provable physical upper/lower orientation, a label that
        # could belong to either parallel chain also stays insufficient.
        self.assertIn(
            ("span_lower_annotation", "证据不足"),
            self.topology_statuses(
                self.audit_topology(variant="neighboring-crossing-beam")
            ),
        )

    def test_shared_label_and_ambiguous_cases_are_not_nearest_bound(self) -> None:
        shared = self.topology_statuses(
            self.audit_topology(variant="shared-upper-right")
        )
        self.assertIn(("support_upper_annotation", "一致"), shared)
        for variant in ("repeated-text", "overlap-blocked"):
            with self.subTest(variant=variant):
                statuses = self.topology_statuses(self.audit_topology(variant=variant))
                self.assertIn(("span_lower_annotation", "证据不足"), statuses)

    def test_leader_conflicts_are_insufficient_not_illegal_placement(self) -> None:
        """A valid conflicting leader is ownership ambiguity, not a placement tie."""

        matching = self.topology_statuses(
            self.audit_topology(variant="leader-matches-geometry")
        )
        self.assertIn(("span_lower_annotation", "一致"), matching)
        for variant, category in (
            ("leader-conflicts-other-span", "span_lower_annotation"),
            ("leader-conflicts-other-support", "support_upper_annotation"),
            ("ambiguous-leader-targets", "span_lower_annotation"),
        ):
            with self.subTest(variant=variant):
                statuses = self.topology_statuses(self.audit_topology(variant=variant))
                self.assertIn((category, "证据不足"), statuses)
                self.assertNotIn((category, "疑似不一致"), statuses)

    def test_unpaired_beam_geometry_blocks_only_affected_annotations(self) -> None:
        """Unpaired configured edges remain private blockers, never ignored."""

        intersecting = self.topology_statuses(
            self.audit_topology(variant="unpaired-intersecting-edge")
        )
        self.assertIn(("span_lower_annotation", "证据不足"), intersecting)
        self.assertIn(("support_upper_annotation", "一致"), intersecting)

        disjoint = self.topology_statuses(
            self.audit_topology(variant="unpaired-nearby-disjoint-edge")
        )
        self.assertIn(("span_lower_annotation", "一致"), disjoint)
        self.assertIn(("support_upper_annotation", "一致"), disjoint)
        self.assertIn(("topology", "证据不足"), disjoint)

        ambiguous = self.topology_statuses(
            self.audit_topology(variant="ambiguous-extra-parallel-edge")
        )
        self.assertIn(("support_upper_annotation", "证据不足"), ambiguous)
        self.assertIn(("span_lower_annotation", "证据不足"), ambiguous)
        self.assertNotIn(("support_upper_annotation", "一致"), ambiguous)
        self.assertNotIn(("span_lower_annotation", "一致"), ambiguous)

        crossing = self.topology_statuses(
            self.audit_topology(variant="unpaired-crossing-edge")
        )
        self.assertIn(("support_upper_annotation", "证据不足"), crossing)
        self.assertIn(("span_lower_annotation", "证据不足"), crossing)

    def test_chain_width_admission_never_averages_sections(self) -> None:
        """Only one scale-tolerant cross-section can admit a multi-axis chain."""

        uniform = self.audit_topology(variant="uniform-width-chain")
        self.assertEqual(len(uniform["topology_assessment"]["chains"]), 1)  # type: ignore[index]
        self.assertEqual(
            self.topology_statuses(uniform),
            [
                ("span_lower_annotation", "一致"),
                ("support_upper_annotation", "一致"),
            ],
        )

        # The upper glyph crosses the local 32-unit section edge.  The
        # historical mean of 20 and 32 could classify it outside a 26-unit
        # beam; admission must instead fail closed for the whole chain.
        for variant in ("variable-width-chain", "width-tolerance-boundary"):
            with self.subTest(variant=variant):
                audit = self.audit_topology(variant=variant)
                self.assertEqual(audit["topology_assessment"]["chains"], [])  # type: ignore[index]
                statuses = self.topology_statuses(audit)
                self.assertNotIn(("support_upper_annotation", "一致"), statuses)
                self.assertNotIn(("span_lower_annotation", "一致"), statuses)
                self.assertIn(("support_upper_annotation", "证据不足"), statuses)
                self.assertIn(("span_lower_annotation", "证据不足"), statuses)

    def test_paired_axes_outside_admitted_chains_remain_private_blockers(self) -> None:
        """No-ID, conflicting, and unsupported paired axes cannot disappear."""

        crossing = self.topology_statuses(
            self.audit_topology(variant="paired-no-id-crossing-axis")
        )
        self.assertIn(("support_upper_annotation", "证据不足"), crossing)
        self.assertNotIn(("support_upper_annotation", "一致"), crossing)
        self.assertIn(("span_lower_annotation", "一致"), crossing)
        crossing_audit = self.audit_topology(variant="paired-no-id-crossing-axis")
        serialized = canonical_json_bytes(crossing_audit).decode("utf-8")
        # The pair's private orphan state, geometry, and source-layer names
        # must never escape through the read-only result.
        self.assertNotIn("orphan", serialized)
        self.assertNotIn("coordinates", serialized)
        self.assertNotIn("BEAM", serialized)

        # A private blocker must be geometrically relevant, not globally
        # poison otherwise independent candidates.
        disjoint = self.topology_statuses(
            self.audit_topology(variant="paired-no-id-disjoint-axis")
        )
        self.assertIn(("support_upper_annotation", "一致"), disjoint)
        self.assertIn(("span_lower_annotation", "一致"), disjoint)

        conflicting = self.topology_statuses(
            self.audit_topology(variant="conflicting-id-orphan-axis")
        )
        self.assertNotIn(("support_upper_annotation", "一致"), conflicting)
        self.assertIn(("support_upper_annotation", "证据不足"), conflicting)

        unsupported = self.topology_statuses(
            self.audit_topology(variant="ambiguous-chain-orphan-axis")
        )
        self.assertNotIn(("support_upper_annotation", "一致"), unsupported)
        self.assertIn(("support_upper_annotation", "证据不足"), unsupported)

    def test_generated_unresolved_sources_have_exact_trace_coverage(self) -> None:
        """Every generated orphan or ambiguity trace has one safe conclusion."""

        for variant, required_count, chainless_beam_count in (
            ("paired-no-id-crossing-axis", 3, 2),
            ("paired-no-id-disjoint-axis", 2, 2),
            ("conflicting-id-orphan-axis", 9, 0),
            ("ambiguous-chain-orphan-axis", 6, 2),
            ("leader-disjoint-unbound-support", 1, 0),
        ):
            with self.subTest(variant=variant):
                self.assert_required_trace_coverage(
                    self.audit_topology(variant=variant),
                    required_count=required_count,
                    chainless_beam_count=chainless_beam_count,
                )

    def test_resigned_required_trace_coverage_is_exact(self) -> None:
        """Re-signing cannot omit, redirect, or invent ambiguity evidence."""

        audit = self.audit_topology(variant="paired-no-id-crossing-axis")
        assessment = audit["topology_assessment"]  # type: ignore[index]
        required_trace = next(
            trace
            for trace in assessment["traces"]
            if trace["role"] == "beam_edges" and trace["chain_id"] is None
        )
        finding = next(
            item
            for item in assessment["findings"]
            if item["trace_ids"] == [required_trace["trace_id"]]
        )

        missing = copy.deepcopy(audit)
        missing["topology_assessment"]["findings"].remove(  # type: ignore[index]
            next(
                item
                for item in missing["topology_assessment"]["findings"]  # type: ignore[index]
                if item["trace_ids"] == [required_trace["trace_id"]]
            )
        )
        self.assert_resigned_forgery_is_rejected(missing)

        changed_status = copy.deepcopy(audit)
        changed = next(
            item
            for item in changed_status["topology_assessment"]["findings"]  # type: ignore[index]
            if item["trace_ids"] == [required_trace["trace_id"]]
        )
        changed["status"] = "一致"
        changed["finding_id"] = derive_topology_finding_id(
            required_trace["trace_id"],
            changed["status"],
            required_trace["role"],
            required_trace["chain_id"],
            required_trace["support_id"],
            required_trace["span_id"],
        )
        self.assert_resigned_forgery_is_rejected(changed_status)

        swapped = copy.deepcopy(audit)
        admitted = next(
            trace
            for trace in swapped["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["role"] == "beam_edges" and trace["chain_id"] is not None
        )
        redirected = next(
            item
            for item in swapped["topology_assessment"]["findings"]  # type: ignore[index]
            if item["trace_ids"] == [required_trace["trace_id"]]
        )
        redirected["trace_ids"] = [admitted["trace_id"]]
        redirected["finding_id"] = derive_topology_finding_id(
            admitted["trace_id"],
            redirected["status"],
            admitted["role"],
            admitted["chain_id"],
            admitted["support_id"],
            admitted["span_id"],
        )
        self.assert_resigned_forgery_is_rejected(swapped)

        orphan_trace = copy.deepcopy(audit)
        duplicate_trace = copy.deepcopy(required_trace)
        orphan_trace["topology_assessment"]["traces"].append(duplicate_trace)  # type: ignore[index]
        self.assert_resigned_forgery_is_rejected(orphan_trace)

        orphan_finding = copy.deepcopy(audit)
        invented = copy.deepcopy(finding)
        invented_trace_id = f"trace-{'f' * 24}"
        invented["trace_ids"] = [invented_trace_id]
        invented["finding_id"] = derive_topology_finding_id(
            invented_trace_id,
            invented["status"],
            required_trace["role"],
            None,
            None,
            None,
        )
        orphan_finding["topology_assessment"]["findings"].append(invented)  # type: ignore[index]
        self.assert_resigned_forgery_is_rejected(orphan_finding)

    def test_global_relation_budget_bounds_50_by_50_by_50_geometry(self) -> None:
        """All exact leader/geometry checks share one early stable budget."""

        original = topology_profile._segments_intersect
        counts: list[int] = []
        for shuffle in (None, 7):
            with self.subTest(shuffle=shuffle), mock.patch(
                "liang_pingfa_review.topology_profile.MAX_CHAIN_RELATIONS",
                512,
            ), mock.patch(
                "liang_pingfa_review.topology_profile._segments_intersect",
                wraps=original,
            ) as exact:
                with self.assertRaises(PipelineError) as raised:
                    self.audit_topology(
                        variant="relation-budget-50x50x50",
                        shuffle_seed=shuffle,
                    )
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.TOPOLOGY_LIMIT_EXCEEDED,
                )
                # Each charged high-level segment predicate can inspect at
                # most four rectangle edges, so this proves a bounded exact
                # call count rather than relying on elapsed wall time.
                self.assertLessEqual(exact.call_count, 512 * 4)
                counts.append(exact.call_count)
        self.assertGreater(counts[0], 0)
        self.assertEqual(counts[0], counts[1])

    def test_topology_serialization_has_no_token_dictionary_oracle(self) -> None:
        """No topology field may reproduce a deterministic token-only hash."""

        audit = self.audit_topology()
        topology = audit["topology_assessment"]  # type: ignore[index]
        traces = topology["traces"]  # type: ignore[index]
        self.assertTrue(
            all(
                isinstance(trace["token_equality_established"], bool)
                and "parsed_value_fingerprint" not in trace
                for trace in traces
            )
        )

        candidate_tokens = ("B1", "B2", "U1", "U2", "L1", "L123456789")
        dictionary_hashes = {
            canonical_sha256({"opaque_token": token})
            for token in candidate_tokens
        } | {
            hashlib.sha256(token.encode("utf-8")).hexdigest()
            for token in candidate_tokens
        }

        def strings(value: object) -> set[str]:
            if isinstance(value, str):
                return {value}
            if isinstance(value, list):
                return set().union(*(strings(item) for item in value))
            if isinstance(value, dict):
                return set().union(
                    *(strings(item) for item in value.values())
                )
            return set()

        self.assertFalse(strings(topology) & dictionary_hashes)

        # Changing all text values changes existing manifest references, as
        # intended, but cannot change opaque chain IDs that derive only from
        # geometry/entity evidence rather than a raw-token hash.
        baseline_dxf = self.root / "token-baseline.dxf"
        changed_dxf = self.root / "token-changed.dxf"
        create_topology_dxf(baseline_dxf)
        document = ezdxf.readfile(baseline_dxf)
        for entity in document.modelspace().query("TEXT"):
            if entity.dxf.layer == "BEAM_ID":
                entity.dxf.text = "B9"
            elif entity.dxf.layer == "UPPER":
                entity.dxf.text = "U9"
            elif entity.dxf.layer == "LOWER":
                entity.dxf.text = "L9"
        document.saveas(changed_dxf)
        baseline = build_audit(
            self.snapshot_topology(baseline_dxf),
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=self.now,
            topology_profile=self.profile,
        )
        changed = build_audit(
            self.snapshot_topology(changed_dxf),
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=self.now,
            topology_profile=self.profile,
        )
        self.assertEqual(
            [
                chain["chain_id"]
                for chain in baseline["topology_assessment"]["chains"]  # type: ignore[index]
            ],
            [
                chain["chain_id"]
                for chain in changed["topology_assessment"]["chains"]  # type: ignore[index]
            ],
        )

    def test_nonplanar_or_nonfinite_lwpolyline_forms_are_not_evidence(self) -> None:
        def polyline() -> object:
            document = ezdxf.new("R2018")
            return document.modelspace().add_lwpolyline(
                [(0, 0, 0, 0, 0), (10, 0, 0, 0, 0)],
                format="xyseb",
            )

        def vertex_width(entity: object, value: float) -> None:
            entity.set_points(  # type: ignore[attr-defined]
                [(0, 0, value, 0, 0), (10, 0, 0, value, 0)],
                format="xyseb",
            )

        cases = (
            ("const-width", lambda entity: setattr(entity.dxf, "const_width", 1.0)),
            ("thickness", lambda entity: setattr(entity.dxf, "thickness", 1.0)),
            ("vertex-width", lambda entity: vertex_width(entity, 1.0)),
            (
                "bulge",
                lambda entity: entity.set_points(  # type: ignore[attr-defined]
                    [(0, 0, 0, 0, 0.5), (10, 0, 0, 0, 0)],
                    format="xyseb",
                ),
            ),
            (
                "ocs",
                lambda entity: setattr(entity.dxf, "extrusion", (1.0, 0.0, 0.0)),
            ),
            (
                "nan-const-width",
                lambda entity: setattr(entity.dxf, "const_width", float("nan")),
            ),
            (
                "infinite-thickness",
                lambda entity: setattr(entity.dxf, "thickness", float("inf")),
            ),
            (
                "nan-vertex-width",
                lambda entity: vertex_width(entity, float("nan")),
            ),
            (
                "combined",
                lambda entity: (
                    setattr(entity.dxf, "const_width", 1.0),
                    setattr(entity.dxf, "thickness", float("inf")),
                    setattr(entity.dxf, "elevation", float("nan")),
                    vertex_width(entity, 1.0),
                ),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                entity = polyline()
                mutate(entity)
                self.assertFalse(extract_topology_evidence(entity, "AC1032").finite)

        # Elevation is the primitive's canonical work plane, not an implicit
        # requirement that source geometry be drawn at WCS Z=0.
        elevated = polyline()
        elevated.dxf.elevation = 100.0
        elevated_evidence = extract_topology_evidence(elevated, "AC1032")
        self.assertTrue(elevated_evidence.finite)
        self.assertEqual(elevated_evidence.plane_elevation, 100.0)
        self.assertTrue(
            all(point[2] == 100.0 for point in elevated_evidence.vertices)
        )

        def audit_with_invalid_support(evidence: object) -> dict[str, object]:
            dxf = self.root / "nonfinite-support.dxf"
            create_topology_dxf(dxf)
            snapshot = self.snapshot_topology(dxf)
            replaced = False
            records = []
            for record in snapshot.records:
                if not replaced and record.layer_name == "SUPPORT":
                    records.append(replace(record, topology_evidence=evidence))
                    replaced = True
                else:
                    records.append(record)
            self.assertTrue(replaced)
            return build_audit(
                replace(snapshot, records=tuple(records)),
                describe_source(self.source),
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
                topology_profile=self.profile,
            )

        # A raw nonfinite form cannot be serialized into a trusted fixture,
        # so inject its extractor result into an otherwise immutable runtime
        # snapshot.  This exercises the same evidence-to-assessment boundary.
        for name, mutate in (
            ("nan-const-width", cases[5][1]),
            ("infinite-thickness", cases[6][1]),
            ("combined", cases[8][1]),
        ):
            with self.subTest(downstream=name):
                entity = polyline()
                mutate(entity)
                audit = audit_with_invalid_support(
                    extract_topology_evidence(entity, "AC1032")
                )
                self.assertEqual(audit["topology_assessment"]["chains"], [])  # type: ignore[index]
                self.assertFalse(
                    any(
                        status == "一致"
                        for _category, status in self.topology_statuses(audit)
                    )
                )

        # Generated finite forms verify the rejection reaches all role paths:
        # beam edge/support failures produce no chain, and a malformed leader
        # cannot authorize a legal annotation binding.
        for variant in (
            "polyline-const-width",
            "polyline-thickness",
            "polyline-combination",
        ):
            with self.subTest(variant=variant):
                audit = self.audit_topology(variant=variant)
                self.assertEqual(audit["topology_assessment"]["chains"], [])  # type: ignore[index]
                self.assertFalse(
                    any(
                        status == "一致"
                        for _category, status in self.topology_statuses(audit)
                    )
                )
        leader_statuses = self.topology_statuses(
            self.audit_topology(variant="polyline-vertex-width")
        )
        self.assertNotIn(("support_upper_annotation", "一致"), leader_statuses)
        self.assertNotIn(("span_lower_annotation", "一致"), leader_statuses)

    def test_text_height_is_required_for_semantic_topology_evidence(self) -> None:
        """Degenerate controlled TEXT blocks semantic binding without vanishing."""

        positions = {
            "BEAM_ID": (40, 10),
            "UPPER": (50, 30),
            "LOWER": (45, -30),
        }

        def evidence_with_height(
            height: float,
            insert: tuple[float, float] = (50, 30),
        ) -> object:
            document = ezdxf.new("R2018")
            entity = document.modelspace().add_text(
                "X1",
                dxfattribs={"insert": insert},
            )
            # ezdxf's public setter normalizes nonpositive TEXT heights to
            # its rendering default.  A raw malformed DXF tag reaches the
            # extractor without that setter, so inject it at the namespace
            # boundary to exercise the real fail-closed predicate.
            entity.dxf.__dict__["height"] = height
            return extract_topology_evidence(entity, "AC1032")

        def audit_with_height(layer: str, height: float) -> dict[str, object]:
            dxf = self.root / f"text-height-{layer}-{height!r}.dxf"
            create_topology_dxf(dxf)
            snapshot = self.snapshot_topology(dxf)
            evidence = evidence_with_height(height, positions[layer])
            replaced = False
            records = []
            for record in snapshot.records:
                if not replaced and record.layer_name == layer:
                    records.append(replace(record, topology_evidence=evidence))
                    replaced = True
                else:
                    records.append(record)
            self.assertTrue(replaced)
            return build_audit(
                replace(snapshot, records=tuple(records)),
                describe_source(self.source),
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
                topology_profile=self.profile,
            )

        degenerate_heights = (
            ("zero", 0.0),
            ("negative", -1.0),
            ("nan", float("nan")),
            ("infinity", float("inf")),
            ("sub-tolerance", topology_profile._PLANE_TOLERANCE / 2.0),
            ("tolerance-boundary", topology_profile._PLANE_TOLERANCE),
        )
        for name, height in degenerate_heights:
            with self.subTest(kind="extract", name=name):
                evidence = evidence_with_height(height)
                self.assertFalse(evidence.finite)  # type: ignore[union-attr]
                # The finite anchor remains private overlap evidence, but no
                # zero/invalid-height glyph bounds are invented.
                self.assertIsNone(evidence.text_bounds)  # type: ignore[union-attr]
                self.assertEqual(evidence.plane_elevation, 0.0)  # type: ignore[union-attr]

            with self.subTest(kind="beam-id", name=name):
                audit = audit_with_height("BEAM_ID", height)
                self.assertEqual(audit["topology_assessment"]["chains"], [])  # type: ignore[index]
                self.assertIn(("topology", "证据不足"), self.topology_statuses(audit))

            for layer, category in (
                ("UPPER", "support_upper_annotation"),
                ("LOWER", "span_lower_annotation"),
            ):
                with self.subTest(kind=layer, name=name):
                    statuses = self.topology_statuses(audit_with_height(layer, height))
                    affected = [
                        status
                        for finding_category, status in statuses
                        if finding_category == category
                    ]
                    self.assertIn("证据不足", affected)
                    self.assertNotIn("一致", affected)
                    self.assertNotIn("疑似不一致", affected)

        # The policy is strict at the tolerance boundary but preserves every
        # finite height above it; no positive value is rounded down or
        # defaulted during semantic evidence capture.
        positive_height = max(topology_profile._PLANE_TOLERANCE * 2.0, 1e-5)
        self.assertTrue(evidence_with_height(positive_height).finite)  # type: ignore[union-attr]
        for layer in ("BEAM_ID", "UPPER", "LOWER"):
            with self.subTest(kind="positive-boundary", layer=layer):
                audit = audit_with_height(layer, positive_height)
                self.assertEqual(len(audit["topology_assessment"]["chains"]), 1)  # type: ignore[index]
                self.assertEqual(
                    self.topology_statuses(audit),
                    [
                        ("span_lower_annotation", "一致"),
                        ("support_upper_annotation", "一致"),
                    ],
                )

    def test_rendered_text_bounds_require_resolvable_finite_area(self) -> None:
        """Collapsed renderer boxes remain ambiguity evidence, never semantics."""

        def evidence_with_bounds(
            minimum: tuple[float, float, float],
            maximum: tuple[float, float, float],
        ) -> object:
            document = ezdxf.new("R2018")
            entity = document.modelspace().add_text(
                "X1",
                dxfattribs={"height": 4.0, "insert": (50, 30)},
            )
            extents = mock.Mock(
                has_data=True,
                extmin=minimum,
                extmax=maximum,
            )
            with mock.patch.object(topology_profile.bbox, "extents", return_value=extents):
                return extract_topology_evidence(entity, "AC1032")

        # The mocked boxes cover renderer collapse independently from TEXT
        # height parsing.  ULP-sized spans at large coordinates are not a
        # reliable proof of an area even when they compare nonzero in Python.
        large_coordinate = 1.0e10
        cases = {
            "zero-width": ((50.0, 30.0, 0.0), (50.0, 34.0, 0.0)),
            "zero-height": ((50.0, 30.0, 0.0), (54.0, 30.0, 0.0)),
            "sub-tolerance": (
                (50.0, 30.0, 0.0),
                (
                    50.0 + topology_profile._PLANE_TOLERANCE / 2.0,
                    30.0 + topology_profile._PLANE_TOLERANCE / 2.0,
                    0.0,
                ),
            ),
            "nan": ((float("nan"), 30.0, 0.0), (54.0, 34.0, 0.0)),
            "infinity": ((50.0, 30.0, 0.0), (float("inf"), 34.0, 0.0)),
            "reversed": ((54.0, 34.0, 0.0), (50.0, 30.0, 0.0)),
            "one-ulp": (
                (large_coordinate, large_coordinate, 0.0),
                (
                    large_coordinate + math.ulp(large_coordinate),
                    large_coordinate + math.ulp(large_coordinate),
                    0.0,
                ),
            ),
        }
        for name, (minimum, maximum) in cases.items():
            with self.subTest(name=name):
                evidence = evidence_with_bounds(minimum, maximum)
                self.assertFalse(evidence.finite)  # type: ignore[union-attr]
                self.assertIsNone(evidence.text_bounds)  # type: ignore[union-attr]
                # The insertion plane survives solely as private overlap
                # provenance; no degenerate Aabb is fabricated.
                self.assertEqual(evidence.plane_elevation, 0.0)  # type: ignore[union-attr]

        positive = evidence_with_bounds((50.0, 30.0, 0.0), (54.0, 34.0, 0.0))
        self.assertTrue(positive.finite)  # type: ignore[union-attr]
        self.assertIsNotNone(positive.text_bounds)  # type: ignore[union-attr]

        def audit_with_evidence(layer: str, evidence: object) -> dict[str, object]:
            dxf = self.root / f"rendered-bounds-{layer}.dxf"
            create_topology_dxf(dxf)
            snapshot = self.snapshot_topology(dxf)
            replaced = False
            records = []
            for record in snapshot.records:
                if not replaced and record.layer_name == layer:
                    records.append(replace(record, topology_evidence=evidence))
                    replaced = True
                else:
                    records.append(record)
            self.assertTrue(replaced)
            return build_audit(
                replace(snapshot, records=tuple(records)),
                describe_source(self.source),
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
                topology_profile=self.profile,
            )

        # A visible unbounded role blocks every potentially co-planar
        # controlled TEXT.  It cannot produce IDs/chains or either semantic
        # annotation conclusion.
        collapsed = evidence_with_bounds(*cases["zero-width"])
        for layer, category in (
            ("BEAM_ID", "topology"),
            ("UPPER", "support_upper_annotation"),
            ("LOWER", "span_lower_annotation"),
        ):
            with self.subTest(layer=layer):
                audit = audit_with_evidence(layer, collapsed)
                self.assertEqual(audit["topology_assessment"]["chains"], [])  # type: ignore[index]
                statuses = self.topology_statuses(audit)
                self.assertIn((category, "证据不足"), statuses)
                self.assertNotIn((category, "一致"), statuses)
                self.assertNotIn((category, "疑似不一致"), statuses)

    def test_large_translation_collapsed_bounds_fail_closed(self) -> None:
        """A 1e10 WCS translation must not turn collapsed text into topology."""

        collapsed = self.audit_topology(translation=(1.0e10, 1.0e10))
        self.assertEqual(collapsed["topology_assessment"]["chains"], [])  # type: ignore[index]
        statuses = self.topology_statuses(collapsed)
        self.assertIn(("topology", "证据不足"), statuses)
        self.assertNotIn(("support_upper_annotation", "一致"), statuses)
        self.assertNotIn(("span_lower_annotation", "一致"), statuses)
        self.assertNotIn(("support_upper_annotation", "疑似不一致"), statuses)
        self.assertNotIn(("span_lower_annotation", "疑似不一致"), statuses)

        # Translation preserves findings while extents remain numerically
        # resolvable; this guards against treating all nonzero WCS origins as
        # unreliable.
        self.assertEqual(
            self.topology_statuses(
                self.audit_topology(translation=(1.0e6, -1.0e6))
            ),
            self.topology_statuses(self.audit_topology()),
        )

    def test_numeric_policy_rejects_overflow_prone_geometry_stably(self) -> None:
        """Finite extreme WCS and derived failures never become INTERNAL_ERROR."""

        for name, kwargs in (
            # The reported seventh-review diagonal: finite source coordinates
            # whose subtraction/projection would otherwise overflow.
            ("extreme-diagonal", {"translation": (1.3e308, -1.3e308)}),
            # Both a very large origin and a very large model scale exceed
            # the fixed WCS coordinate policy rather than being clamped.
            (
                "huge-offset-scale",
                {"translation": (9.0e99, -9.0e99), "scale": 2.0e98},
            ),
        ):
            with self.subTest(name=name):
                with self.assertRaises(PipelineError) as raised:
                    self.audit_topology(**kwargs)
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.TOPOLOGY_LIMIT_EXCEEDED,
                )

        # The public CLI must retain the stable topology code rather than
        # translating an admitted arithmetic failure to INTERNAL_ERROR.
        events: list[dict[str, str]] = []
        with mock.patch(
            "liang_pingfa_review.cli.bound_audit_dwg",
            side_effect=PipelineError(ErrorCode.TOPOLOGY_LIMIT_EXCEEDED),
        ), mock.patch(
            "liang_pingfa_review.cli._runner",
            return_value=mock.sentinel.topology_test_runner,
        ), mock.patch(
            "liang_pingfa_review.cli._emit",
            side_effect=lambda event, **_kwargs: events.append(event),
        ):
            self.assertEqual(
                cli.main(
                    [
                        "audit",
                        "--input",
                        str(self.source),
                        "--audit-out",
                        str(self.root / "numeric-audit.json"),
                        "--report-out",
                        str(self.root / "numeric-audit.md"),
                        "--topology-profile",
                        str(self.profile_path),
                    ]
                ),
                1,
            )
        self.assertEqual(
            events,
            [{"status": "error", "code": "TOPOLOGY_LIMIT_EXCEEDED"}],
        )

        # A rotated/scaled fixture is deliberately close to the input cap
        # while still inside it.  It exercises finite projections, interval
        # endpoints, corridors, grid normalization, and expanded bounds.
        near_limit = self.audit_topology(
            orientation_degrees=31.0,
            translation=(7.0e99, -7.0e99),
            scale=1.0e97,
        )
        self.assertEqual(
            self.topology_statuses(near_limit),
            self.topology_statuses(self.audit_topology()),
        )

        for name, polygon in (
            ("nan-derived", ((float("nan"), 0.0, 0.0),) * 4),
            ("infinite-derived", ((float("inf"), 0.0, 0.0),) * 4),
        ):
            with self.subTest(name=name), mock.patch(
                "liang_pingfa_review.topology_profile._projected_rectangle_polygon",
                return_value=polygon,
            ):
                with self.assertRaises(PipelineError) as raised:
                    self.audit_topology()
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.TOPOLOGY_LIMIT_EXCEEDED,
                )

    def test_numeric_input_and_derived_caps_have_fixed_headroom(self) -> None:
        """Source cap boundaries differ from every private derived boundary."""

        self.assertGreaterEqual(
            topology_profile.NUMERIC_PRODUCT_HEADROOM_FACTOR,
            1.0e100,
        )

        def line_evidence_at(x: float) -> object:
            document = ezdxf.new("R2018")
            entity = document.modelspace().add_line((x, 0, 0), (x, 1, 0))
            return extract_topology_evidence(entity, "AC1032")

        exact = line_evidence_at(MAX_INPUT_COORDINATE)
        just_inside = line_evidence_at(math.nextafter(MAX_INPUT_COORDINATE, 0.0))
        just_outside = line_evidence_at(
            math.nextafter(MAX_INPUT_COORDINATE, math.inf)
        )
        self.assertTrue(exact.finite)  # type: ignore[union-attr]
        self.assertTrue(just_inside.finite)  # type: ignore[union-attr]
        self.assertTrue(just_outside.numeric_limit_exceeded)  # type: ignore[union-attr]

        # Private AABBs may use the larger derived envelope, but no expansion
        # may silently wrap, clamp, or exceed it.
        private_bounds = Aabb(
            (0.0, 0.0, 0.0),
            (MAX_DERIVED_SCALAR / 2.0, 1.0, 0.0),
        )
        self.assertEqual(
            private_bounds.expanded(MAX_DERIVED_SCALAR / 2.0).maximum[0],
            MAX_DERIVED_SCALAR,
        )
        with self.assertRaises(PipelineError) as raised:
            private_bounds.expanded(MAX_DERIVED_SCALAR)
        self.assertEqual(raised.exception.code, ErrorCode.TOPOLOGY_LIMIT_EXCEEDED)

        # Replacing one controlled edge with a just-outside source coordinate
        # reaches the stable hard numeric limit before any partial assessment.
        source_cap_dxf = self.root / "source-cap.dxf"
        create_topology_dxf(source_cap_dxf)
        snapshot = self.snapshot_topology(source_cap_dxf)
        records = []
        replaced = False
        for record in snapshot.records:
            if not replaced and record.layer_name == "BEAM":
                records.append(
                    replace(record, topology_evidence=just_outside)
                )
                replaced = True
            else:
                records.append(record)
        self.assertTrue(replaced)
        with self.assertRaises(PipelineError) as raised:
            build_audit(
                replace(snapshot, records=tuple(records)),
                describe_source(self.source),
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
                topology_profile=self.profile,
            )
        self.assertEqual(raised.exception.code, ErrorCode.TOPOLOGY_LIMIT_EXCEEDED)

    def test_plane_tolerance_projects_private_analysis_geometry(self) -> None:
        """Near-planar source geometry is flattened privately, never mutated."""

        baseline = self.topology_statuses(self.audit_topology())
        for residual in (
            0.0,
            5.0e-7,
            topology_profile._PLANE_TOLERANCE,
        ):
            with self.subTest(residual=residual):
                self.assertEqual(
                    self.topology_statuses(
                        self.audit_on_work_plane(
                            plane=100.0,
                            line_residual=residual,
                        )
                    ),
                    baseline,
                )

        document = ezdxf.new("R2018")
        line = document.modelspace().add_line(
            (1.0, 2.0, 100.0),
            (3.0, 4.0, 100.0 + 5.0e-7),
        )
        original_end = tuple(line.dxf.end)
        evidence = extract_topology_evidence(line, "AC1032")
        self.assertTrue(evidence.finite)
        self.assertEqual(evidence.plane_elevation, 100.0)
        self.assertEqual(
            evidence.vertices,
            ((1.0, 2.0, 100.0), (3.0, 4.0, 100.0 + 5.0e-7)),
        )
        self.assertEqual((evidence.plane_min, evidence.plane_max), (100.0, 100.0 + 5.0e-7))
        self.assertEqual(tuple(line.dxf.end), original_end)

        text = document.modelspace().add_text(
            "X1",
            dxfattribs={"height": 4.0, "insert": (10.0, 20.0, 100.0)},
        )
        text.set_placement(
            (10.0, 20.0, 100.0 + 5.0e-7),
            align=TextEntityAlignment.CENTER,
        )
        # Preserve a distinct source insertion and alignment anchor so the
        # extractor, rather than ezdxf's placement setter, proves the
        # residual projection rule.
        text.dxf.insert = (10.0, 20.0, 100.0)
        text_evidence = extract_topology_evidence(text, "AC1032")
        self.assertTrue(text_evidence.finite)
        self.assertEqual(text_evidence.plane_elevation, 100.0)
        self.assertEqual(text_evidence.text_bounds.plane, 100.0)  # type: ignore[union-attr]

        # A residual beyond the inclusive tolerance and two incompatible
        # primitive planes remain ordinary insufficient evidence, not a
        # numeric hard-abort or an arbitrary plane selection.
        outside = self.audit_on_work_plane(
            plane=100.0,
            line_residual=topology_profile._PLANE_TOLERANCE * 1.1,
        )
        self.assertEqual(outside["topology_assessment"]["chains"], [])  # type: ignore[index]
        self.assertIn(("topology", "证据不足"), self.topology_statuses(outside))
        mixed = self.audit_on_work_plane(plane=100.0, mixed_plane=True)
        self.assertEqual(mixed["topology_assessment"]["chains"], [])  # type: ignore[index]
        self.assertIn(("topology", "证据不足"), self.topology_statuses(mixed))

    def test_binding_plane_envelope_cannot_chain_through_baselines(self) -> None:
        """Every target uses one global original-elevation envelope."""

        def audit_with_planes(
            *,
            support_z: float = 0.0,
            annotation_z: float = 0.0,
            leader_z: float | None = None,
        ) -> dict[str, object]:
            dxf = self.root / (
                f"plane-envelope-{support_z!r}-{annotation_z!r}-{leader_z!r}.dxf"
            )
            create_topology_dxf(
                dxf,
                variant="leader-matches-geometry" if leader_z is not None else "consistent",
            )
            document = ezdxf.readfile(dxf)
            for entity in document.modelspace():
                if entity.dxftype() == "LWPOLYLINE" and entity.dxf.layer == "SUPPORT":
                    entity.dxf.elevation = support_z
                elif entity.dxftype() == "TEXT" and entity.dxf.layer in {"UPPER", "LOWER"}:
                    insert = entity.dxf.insert
                    entity.dxf.insert = (insert.x, insert.y, annotation_z)
                elif (
                    leader_z is not None
                    and entity.dxftype() == "LINE"
                    and entity.dxf.layer == "LEADER"
                ):
                    start, end = entity.dxf.start, entity.dxf.end
                    entity.dxf.start = (start.x, start.y, leader_z)
                    entity.dxf.end = (end.x, end.y, leader_z)
            document.saveas(dxf)
            return build_audit(
                self.snapshot_topology(dxf),
                describe_source(self.source),
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
                topology_profile=self.profile,
            )

        chained = audit_with_planes(
            support_z=-0.9e-6,
            annotation_z=0.9e-6,
        )
        chained_statuses = self.topology_statuses(chained)
        self.assertIn(("support_upper_annotation", "证据不足"), chained_statuses)
        self.assertIn(("span_lower_annotation", "证据不足"), chained_statuses)
        self.assertNotIn(("support_upper_annotation", "一致"), chained_statuses)
        self.assertNotIn(("span_lower_annotation", "疑似不一致"), chained_statuses)

        exact = self.topology_statuses(audit_with_planes(annotation_z=1.0e-6))
        self.assertIn(("support_upper_annotation", "一致"), exact)
        self.assertIn(("span_lower_annotation", "一致"), exact)

        just_over = self.topology_statuses(audit_with_planes(annotation_z=1.1e-6))
        self.assertIn(("support_upper_annotation", "证据不足"), just_over)
        self.assertIn(("span_lower_annotation", "证据不足"), just_over)
        self.assertNotIn(("support_upper_annotation", "疑似不一致"), just_over)

        mixed_leader = self.topology_statuses(audit_with_planes(leader_z=1.1e-6))
        self.assertIn(("span_lower_annotation", "证据不足"), mixed_leader)
        self.assertNotIn(("span_lower_annotation", "疑似不一致"), mixed_leader)

    def test_lwpolyline_evidence_is_bounded_before_iteration(self) -> None:
        """Oversized paths retain count provenance but never materialize vertices."""

        class Dxf:
            def __init__(self, count: int) -> None:
                self.count = count

            def get(self, name: str, default: object = None) -> object:
                return getattr(self, name, default)

        class OversizedPolyline:
            def __init__(self, count: int) -> None:
                self.dxf = Dxf(count)
                self.closed = False
                self.get_points = mock.Mock(
                    side_effect=AssertionError("oversized vertices were iterated")
                )

            def dxftype(self) -> str:
                return "LWPOLYLINE"

        for count in (20_000, 100_000):
            with self.subTest(count=count):
                entity = OversizedPolyline(count)
                evidence = extract_topology_evidence(entity, "AC1032")
                self.assertTrue(evidence.vertex_limit_exceeded)
                self.assertEqual(evidence.vertex_count, count)
                self.assertEqual(evidence.vertices, ())
                entity.get_points.assert_not_called()

        document = ezdxf.new("R2018")
        modelspace = document.modelspace()
        edge = modelspace.add_lwpolyline([(0, 0), (100, 0)], close=False)
        support = modelspace.add_lwpolyline(
            [(0, 0), (10, 0), (10, 10), (0, 10)],
            close=True,
        )
        self.assertTrue(extract_topology_evidence(edge, "AC1032").finite)
        self.assertTrue(extract_topology_evidence(support, "AC1032").finite)
        self.assertEqual(
            extract_topology_evidence(
                modelspace.add_lwpolyline([(0, 0), (1, 0), (1, 1)], close=False),
                "AC1032",
            ).vertex_count,
            3,
        )
        over_cap = extract_topology_evidence(
            modelspace.add_lwpolyline(
                [(0, 0), (1, 0), (2, 0), (2, 1), (0, 1)], close=True
            ),
            "AC1032",
        )
        self.assertTrue(over_cap.vertex_limit_exceeded)
        self.assertFalse(over_cap.finite)

        # Role consumers accept only their exact fixed shapes.  A three-point
        # path is not an edge/leader and a five-point support is rejected
        # before its vertices can become a partial topology artifact.
        for name, layer, vertices, closed in (
            ("three-point-edge", "BEAM", [(220, 0), (240, 0), (240, 5)], False),
            ("three-point-leader", "LEADER", [(45, -30), (45, -10), (46, 0)], False),
            (
                "five-point-support",
                "SUPPORT",
                [(220, -10), (240, -10), (245, 0), (240, 10), (220, 10)],
                True,
            ),
        ):
            with self.subTest(role_shape=name):
                dxf = self.root / f"{name}.dxf"
                create_topology_dxf(dxf)
                document = ezdxf.readfile(dxf)
                document.modelspace().add_lwpolyline(
                    vertices,
                    close=closed,
                    dxfattribs={"layer": layer},
                )
                document.saveas(dxf)
                statuses = self.topology_statuses(
                    build_audit(
                        self.snapshot_topology(dxf),
                        describe_source(self.source),
                        oda_version=SUPPORTED_ODA_VERSION,
                        now=self.now,
                        topology_profile=self.profile,
                    )
                )
                self.assertIn(("topology", "证据不足"), statuses)
                self.assertNotIn(("topology", "一致"), statuses)

    def test_overlap_inventory_precedes_text_alignment_eligibility(self) -> None:
        for variant in (
            "overlap-center",
            "overlap-right",
            "overlap-unsupported-alignment",
            "unbounded-unsupported-alignment",
        ):
            with self.subTest(variant=variant):
                statuses = self.topology_statuses(self.audit_topology(variant=variant))
                upper_statuses = [
                    status
                    for category, status in statuses
                    if category == "support_upper_annotation"
                ]
                self.assertGreaterEqual(len(upper_statuses), 2)
                self.assertEqual(set(upper_statuses), {"证据不足"})

        # MIDDLE_CENTER is not role-eligible, but its reliable WCS bounds
        # prove it is disjoint, so it does not poison the distant UPPER label.
        distant = self.topology_statuses(
            self.audit_topology(variant="distant-unsupported-alignment")
        )
        self.assertIn(("support_upper_annotation", "一致"), distant)
        self.assertIn(("support_upper_annotation", "证据不足"), distant)

    def test_visible_malformed_annotation_remains_topology_evidence(self) -> None:
        """Displayable controlled TEXT remains an insufficient overlap input."""

        audit = self.audit_topology(variant="visible-malformed-annotation")
        upper_statuses = [
            status
            for category, status in self.topology_statuses(audit)
            if category == "support_upper_annotation"
        ]
        self.assertEqual(upper_statuses, ["证据不足", "证据不足"])
        malformed_handles = {
            record.handle
            for record in self.snapshot_topology(
                self.root / "visible-malformed-annotation-0.0-1.0-False-None.dxf"
            ).records
            if record.layout == "modelspace"
            and record.visible
            and record.layer_name == "UPPER"
            and record.topology_evidence is not None
            and record.topology_evidence.text_alignment != "left"
        }
        trace_handles = {
            trace["entity_handle"]
            for trace in audit["topology_assessment"]["traces"]  # type: ignore[index]
        }
        self.assertTrue(malformed_handles)
        self.assertTrue(malformed_handles <= trace_handles)

    def test_nonsemantic_roles_are_preserved_without_topology_evidence(self) -> None:
        """Hidden, transparent, non-Modelspace roles cannot affect topology."""

        role_layers = {
            "beam_edges": "BEAM",
            "beam_ids": "BEAM_ID",
            "generic_supports": "SUPPORT",
            "support_upper_annotations": "UPPER",
            "span_lower_annotations": "LOWER",
            "leaders": "LEADER",
        }
        profile_payload = topology_profile_payload()
        profile_layers = {
            role: list(names)
            for role, names in profile_payload["layers"].items()  # type: ignore[index,union-attr]
        }
        for role, layer in role_layers.items():
            profile_layers[role].extend((f"OFF_{layer}", f"FROZEN_{layer}"))
        excluded_profile_path = self.root / "excluded-roles-profile.json"
        excluded_profile_path.write_text(
            json.dumps(
                topology_profile_payload(layers=profile_layers),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        excluded_profile = load_topology_profile(excluded_profile_path)

        dxf = self.root / "ignored-role-records.dxf"
        create_topology_dxf(dxf, variant="ignored-role-records")
        snapshot = self.snapshot_topology(dxf, profile=excluded_profile)
        semantic_records = [
            record
            for record in snapshot.records
            if excluded_profile.roles.role_for(record.layer_name.casefold()) is not None
            and record.layout == "modelspace"
            and record.visible
        ]
        ignored_records = [
            record
            for record in snapshot.records
            if excluded_profile.roles.role_for(record.layer_name.casefold()) is not None
            and record not in semantic_records
        ]
        expected_roles = {
            excluded_profile.roles.role_for(layer.casefold())
            for layer in role_layers.values()
        }
        self.assertNotIn(None, expected_roles)
        self.assertEqual(len(semantic_records), 8)
        self.assertEqual(len(ignored_records), 36)

        conditions = {
            "hidden": [
                record
                for record in ignored_records
                if record.layout == "modelspace"
                and not record.entity_visible
                and record.layer_name in role_layers.values()
            ],
            "transparent": [
                record
                for record in ignored_records
                if record.layout == "modelspace"
                and record.entity_visible
                and record.entity_transparency is not None
            ],
            "off-layer": [
                record
                for record in ignored_records
                if record.layer_name.startswith("OFF_")
            ],
            "frozen-layer": [
                record
                for record in ignored_records
                if record.layer_name.startswith("FROZEN_")
            ],
            "paperspace": [
                record for record in ignored_records if record.layout == "paperspace"
            ],
            "block": [record for record in ignored_records if record.layout == "block"],
        }
        for condition, records in conditions.items():
            with self.subTest(condition=condition):
                self.assertEqual(len(records), len(role_layers))
                self.assertEqual(
                    {
                        excluded_profile.roles.role_for(record.layer_name.casefold())
                        for record in records
                    },
                    expected_roles,
                )

        audit = build_audit(
            snapshot,
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=self.now,
            topology_profile=excluded_profile,
        )
        self.assertEqual(
            self.topology_statuses(audit),
            self.topology_statuses(self.audit_topology()),
        )
        self.assertEqual(len(audit["topology_assessment"]["chains"]), 1)  # type: ignore[index]
        manifest_handles = {
            item["handle"]
            for item in audit["inventory"]["entity_manifest"]  # type: ignore[index]
        }
        ignored_handles = {record.handle for record in ignored_records}
        trace_handles = {
            trace["entity_handle"]
            for trace in audit["topology_assessment"]["traces"]  # type: ignore[index]
        }
        self.assertTrue(ignored_handles <= manifest_handles)
        self.assertFalse(ignored_handles & trace_handles)
        assessment = canonical_json_bytes(audit["topology_assessment"])  # type: ignore[index]
        for handle in ignored_handles:
            self.assertNotIn(
                f'"entity_handle":"{handle}"'.encode("utf-8"),
                assessment,
            )

        # The excluded records are not charged to the semantic-role cap.
        with mock.patch(
            "liang_pingfa_review.topology_profile.MAX_ROLE_ENTITIES",
            len(semantic_records),
        ):
            capped_audit = build_audit(
                snapshot,
                describe_source(self.source),
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
                topology_profile=excluded_profile,
            )
        self.assertEqual(
            self.topology_ids_and_statuses(capped_audit),
            self.topology_ids_and_statuses(audit),
        )

    def test_support_adjacent_zones_and_vertex_order_are_invariant(self) -> None:
        for variant in ("consistent", "shared-upper-right"):
            with self.subTest(variant=variant):
                self.assertIn(
                    ("support_upper_annotation", "一致"),
                    self.topology_statuses(self.audit_topology(variant=variant)),
                )
        for variant in ("upper-inside-shared-support", "wrong-side-upper"):
            with self.subTest(variant=variant):
                self.assertIn(
                    ("support_upper_annotation", "疑似不一致"),
                    self.topology_statuses(self.audit_topology(variant=variant)),
                )
        competing = self.topology_statuses(
            self.audit_topology(variant="competing-shared-upper")
        )
        self.assertEqual(
            [
                status
                for category, status in competing
                if category == "support_upper_annotation"
            ],
            ["证据不足", "证据不足"],
        )

        baseline_statuses = self.topology_statuses(self.audit_topology())
        for start, reverse in ((1, False), (2, True), (3, True)):
            with self.subTest(start=start, reverse=reverse):
                observed = self.audit_topology(
                    support_vertex_start=start,
                    reverse_support_winding=reverse,
                )
                # Support IDs are now deliberately bound to each source
                # entity trace, whose manifest identity changes with this
                # representation mutation.  The conservative conclusions
                # remain invariant.
                self.assertEqual(self.topology_statuses(observed), baseline_statuses)

    def test_zone_faces_require_a_strict_full_bounds_margin(self) -> None:
        """Every legal zone face rejects touching and one-epsilon bounds."""

        def audit_with_bounds(
            layer: str,
            minimum: tuple[float, float, float],
            maximum: tuple[float, float, float],
        ) -> dict[str, object]:
            dxf = self.root / f"strict-zone-{layer}-{minimum[0]}-{minimum[1]}.dxf"
            create_topology_dxf(dxf)
            snapshot = self.snapshot_topology(dxf)
            replaced = False
            records = []
            for record in snapshot.records:
                if record.layer_name == layer and not replaced:
                    evidence = record.topology_evidence
                    self.assertIsNotNone(evidence)
                    records.append(
                        replace(
                            record,
                            topology_evidence=replace(
                                evidence,
                                text_bounds=topology_profile.Aabb(minimum, maximum),
                                finite=True,
                            ),
                        )
                    )
                    replaced = True
                else:
                    records.append(record)
            self.assertTrue(replaced)
            return build_audit(
                replace(snapshot, records=tuple(records)),
                describe_source(self.source),
                oda_version=SUPPORTED_ODA_VERSION,
                now=self.now,
                topology_profile=self.profile,
            )

        epsilon = topology_profile._EPSILON
        # All lower bounds are in the left span's middle zone and below the
        # beam unless the named face deliberately removes that proof.
        insufficient_cases = (
            ("LOWER", (30.0, -34.0, 0.0), (34.0, -30.0, 0.0), "midspan"),
            (
                "LOWER",
                (30.0 + epsilon / 2.0, -34.0, 0.0),
                (34.0 + epsilon / 2.0, -30.0, 0.0),
                "midspan-within",
            ),
            ("LOWER", (40.0, -4.0, 0.0), (44.0, 0.0, 0.0), "lower-beam-face"),
            (
                "LOWER",
                (40.0, -4.0 - epsilon / 2.0, 0.0),
                (44.0, -epsilon / 2.0, 0.0),
                "lower-beam-face-within",
            ),
            ("LOWER", (40.0, -80.0, 0.0), (44.0, -76.0, 0.0), "lower-outer-zone"),
            # This upper label ends exactly on the first span's support/span
            # face.  It is not a legal alternate support assignment.
            ("UPPER", (86.0, 30.0, 0.0), (90.0, 34.0, 0.0), "support-span-face"),
            # Upper r_low=10 is the beam exterior face; r_high=90 is the
            # fixed outer annotation-zone face.
            ("UPPER", (50.0, 20.0, 0.0), (54.0, 24.0, 0.0), "upper-beam-face"),
            ("UPPER", (50.0, 96.0, 0.0), (54.0, 100.0, 0.0), "upper-outer-zone"),
        )
        for layer, minimum, maximum, face in insufficient_cases:
            with self.subTest(face=face):
                statuses = self.topology_statuses(
                    audit_with_bounds(layer, minimum, maximum)
                )
                category = (
                    "support_upper_annotation"
                    if layer == "UPPER"
                    else "span_lower_annotation"
                )
                self.assertIn((category, "证据不足"), statuses)
                self.assertNotIn((category, "一致"), statuses)

        # More than one epsilon from each relevant face is legal.  These
        # brackets prove the fixed policy is neither touching-permissive nor
        # a broad exclusion band.
        for layer, minimum, maximum, category in (
            (
                "LOWER",
                (30.0 + 2.0 * epsilon, -34.0, 0.0),
                (34.0 + 2.0 * epsilon, -30.0, 0.0),
                "span_lower_annotation",
            ),
            (
                "LOWER",
                (40.0, -4.0 - 2.0 * epsilon, 0.0),
                (44.0, -2.0 * epsilon, 0.0),
                "span_lower_annotation",
            ),
            (
                "UPPER",
                (86.0, 30.0, 0.0),
                (90.0 - 2.0 * epsilon, 34.0, 0.0),
                "support_upper_annotation",
            ),
        ):
            with self.subTest(layer=layer, minimum=minimum):
                self.assertIn(
                    (category, "一致"),
                    self.topology_statuses(audit_with_bounds(layer, minimum, maximum)),
                )

    def test_shared_relation_budget_fails_closed_and_sparse_chain_survives(self) -> None:
        original = topology_profile._axes_join_through_support
        with mock.patch(
            "liang_pingfa_review.topology_profile._axes_join_through_support",
            wraps=original,
        ) as predicate:
            with self.assertRaises(PipelineError) as raised:
                self.audit_topology(variant="chain-relation-overload")
        self.assertEqual(raised.exception.code, ErrorCode.TOPOLOGY_LIMIT_EXCEEDED)
        # Edge pairing now shares this same budget, so overload may fail
        # before chain joining.  In either case no exact join can exceed it.
        self.assertLessEqual(predicate.call_count, MAX_CHAIN_RELATIONS)

        sparse = self.audit_topology()
        self.assertEqual(len(sparse["topology_assessment"]["chains"]), 1)  # type: ignore[index]
        self.assertEqual(
            self.topology_statuses(sparse),
            [
                ("span_lower_annotation", "一致"),
                ("support_upper_annotation", "一致"),
            ],
        )

    def test_structural_counterexamples_do_not_synthesize_supports(self) -> None:
        collinear = self.audit_topology(variant="collinear-different-ids")
        # Shared source supports cannot be assigned to two canonical
        # chain-specific support registries.
        self.assertEqual(len(collinear["topology_assessment"]["chains"]), 0)  # type: ignore[index]
        secondary = self.audit_topology(variant="secondary-intersection")
        self.assertEqual(len(secondary["topology_assessment"]["chains"]), 1)  # type: ignore[index]
        unsupported = self.audit_topology(variant="unsupported-edge")
        self.assertIn(("topology", "证据不足"), self.topology_statuses(unsupported))

    def test_orientation_transform_reverse_and_order_are_metamorphic(self) -> None:
        baseline = self.topology_statuses(self.audit_topology())
        for angle, translation, scale, reverse, shuffle in (
            (90.0, (700.0, -300.0), 3.0, True, 4),
            (37.0, (-900.0, 1200.0), 0.5, True, 9),
            (180.0, (1.0, 2.0), 2.0, False, 2),
        ):
            with self.subTest(angle=angle, scale=scale, shuffle=shuffle):
                observed = self.topology_statuses(
                    self.audit_topology(
                        orientation_degrees=angle,
                        translation=translation,
                        scale=scale,
                        reversed_edges=reverse,
                        shuffle_seed=shuffle,
                    )
                )
                self.assertEqual(observed, baseline)

    def test_topology_findings_cannot_be_forged_actionable(self) -> None:
        forged = copy.deepcopy(self.audit_topology())
        forged["topology_assessment"]["findings"][0]["actionability"] = True  # type: ignore[index]
        forged = attach_integrity(forged)
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("audit", forged)
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)

    def test_resigned_traces_require_eligible_public_manifest_evidence(self) -> None:
        """A valid signature cannot reuse paperspace/hidden/transparent records."""

        audit = self.audit_topology(variant="ignored-role-records")
        manifest = audit["inventory"]["entity_manifest"]  # type: ignore[index]
        beam_trace = next(
            trace
            for trace in audit["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["role"] == "beam_edges"
        )
        candidates = {
            "paperspace": next(item for item in manifest if item["layout"] == "paperspace"),
            "hidden": next(
                item
                for item in manifest
                if item["layout"] == "modelspace" and item["entity_visible"] is False
            ),
            "transparent": next(
                item
                for item in manifest
                if item["layout"] == "modelspace"
                and item["entity_visible"] is True
                and item["entity_transparency"] is not None
            ),
        }
        for case, entity in candidates.items():
            with self.subTest(case=case):
                forged = copy.deepcopy(audit)
                trace = next(
                    item
                    for item in forged["topology_assessment"]["traces"]  # type: ignore[index]
                    if item["trace_id"] == beam_trace["trace_id"]
                )
                trace.update(
                    {
                        "entity_handle": entity["handle"],
                        "identity_fingerprint": entity["identity_fingerprint"],
                        "content_fingerprint": entity["content_fingerprint"],
                    }
                )
                self.assert_resigned_forgery_is_rejected(forged)

    def test_resigned_traces_reject_contradictory_role_tuples(self) -> None:
        """Role labels cannot convert opaque IDs into incompatible bindings."""

        audit = self.audit_topology()
        assessment = audit["topology_assessment"]  # type: ignore[index]
        upper = next(
            trace
            for trace in assessment["traces"]
            if trace["role"] == "support_upper_annotations"
        )
        lower = next(
            trace
            for trace in assessment["traces"]
            if trace["role"] == "span_lower_annotations"
        )
        beam = next(
            trace for trace in assessment["traces"] if trace["role"] == "beam_edges"
        )
        span_id = next(
            chain["spans"][0]["span_id"]
            for chain in assessment["chains"]
            if chain["chain_id"] == upper["chain_id"]
        )

        cases: dict[str, dict[str, object]] = {
            "role-mismatch": {
                "trace_id": beam["trace_id"],
                "role": "support_upper_annotations",
            },
            "both-support-and-span": {
                "trace_id": upper["trace_id"],
                "span_id": span_id,
            },
            "missing-required-support": {
                "trace_id": upper["trace_id"],
                "support_id": None,
            },
            "support-span-swap": {
                "trace_id": upper["trace_id"],
                "support_id": None,
                "span_id": span_id,
            },
            "unknown-orphan-id": {
                "trace_id": lower["trace_id"],
                "span_id": f"span-{'f' * 24}",
            },
        }
        for case, mutation in cases.items():
            with self.subTest(case=case):
                forged = copy.deepcopy(audit)
                trace = next(
                    item
                    for item in forged["topology_assessment"]["traces"]  # type: ignore[index]
                    if item["trace_id"] == mutation["trace_id"]
                )
                trace.update(
                    {key: value for key, value in mutation.items() if key != "trace_id"}
                )
                self.assert_resigned_forgery_is_rejected(forged)

    def test_resigned_traces_reject_registry_duplicates_and_wrong_ownership(self) -> None:
        """The opaque registry proves ownership; valid-looking IDs are insufficient."""

        audit = self.audit_topology()
        assessment = audit["topology_assessment"]  # type: ignore[index]
        upper = next(
            trace
            for trace in assessment["traces"]
            if trace["role"] == "support_upper_annotations"
        )

        duplicate = copy.deepcopy(audit)
        duplicate_chain = duplicate["topology_assessment"]["chains"][0]  # type: ignore[index]
        duplicate_chain["supports"][1]["support_id"] = duplicate_chain["supports"][0]["support_id"]
        self.assert_resigned_forgery_is_rejected(duplicate)

        wrong_owner = copy.deepcopy(audit)
        additional_chain_id = f"chain-{'e' * 24}"
        wrong_owner["topology_assessment"]["chains"].append(  # type: ignore[index]
            {
                "chain_id": additional_chain_id,
                "supports": [
                    {
                        "support_id": f"support-{'e' * 23}1",
                        "support_geometry_trace_id": f"trace-{'e' * 23}1",
                    },
                    {
                        "support_id": f"support-{'e' * 23}2",
                        "support_geometry_trace_id": f"trace-{'e' * 23}2",
                    },
                ],
                "spans": [
                    {
                        "span_id": f"span-{'e' * 23}3",
                        "left_support_id": f"support-{'e' * 23}1",
                        "right_support_id": f"support-{'e' * 23}2",
                    }
                ],
            }
        )
        forged_upper = next(
            trace
            for trace in wrong_owner["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["trace_id"] == upper["trace_id"]
        )
        forged_upper["chain_id"] = additional_chain_id
        self.assert_resigned_forgery_is_rejected(wrong_owner)

    def test_resigned_support_and_span_provenance_cannot_be_rebound(self) -> None:
        """Every registry target is pinned to canonical source provenance."""

        audit = self.audit_topology()
        assessment = audit["topology_assessment"]  # type: ignore[index]
        chain = next(
            item for item in assessment["chains"] if len(item["supports"]) >= 3
        )
        supports = chain["supports"]
        spans = chain["spans"]

        def refresh_finding(forged: dict[str, object], trace: dict[str, object]) -> None:
            finding = next(
                item
                for item in forged["topology_assessment"]["findings"]  # type: ignore[index]
                if item["trace_ids"] == [trace["trace_id"]]
            )
            finding["finding_id"] = derive_topology_finding_id(
                trace["trace_id"],
                finding["status"],
                trace["role"],
                trace["chain_id"],
                trace["support_id"],
                trace["span_id"],
            )

        swapped_support_ids = copy.deepcopy(audit)
        registry = swapped_support_ids["topology_assessment"]["chains"][0]["supports"]  # type: ignore[index]
        registry[0]["support_id"], registry[1]["support_id"] = (
            registry[1]["support_id"],
            registry[0]["support_id"],
        )
        self.assert_resigned_forgery_is_rejected(swapped_support_ids)

        swapped_support_traces = copy.deepcopy(audit)
        registry = swapped_support_traces["topology_assessment"]["chains"][0]["supports"]  # type: ignore[index]
        registry[0]["support_geometry_trace_id"], registry[1]["support_geometry_trace_id"] = (
            registry[1]["support_geometry_trace_id"],
            registry[0]["support_geometry_trace_id"],
        )
        self.assert_resigned_forgery_is_rejected(swapped_support_traces)

        removed_trace = copy.deepcopy(audit)
        removed_trace_id = supports[0]["support_geometry_trace_id"]
        removed_trace["topology_assessment"]["traces"] = [  # type: ignore[index]
            trace
            for trace in removed_trace["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["trace_id"] != removed_trace_id
        ]
        self.assert_resigned_forgery_is_rejected(removed_trace)

        duplicate_trace = copy.deepcopy(audit)
        support_trace = next(
            trace
            for trace in duplicate_trace["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["trace_id"] == supports[0]["support_geometry_trace_id"]
        )
        duplicate_trace["topology_assessment"]["traces"].append(copy.deepcopy(support_trace))  # type: ignore[index]
        self.assert_resigned_forgery_is_rejected(duplicate_trace)

        orphan_registry = copy.deepcopy(audit)
        orphan_registry["topology_assessment"]["chains"][0]["supports"][0][  # type: ignore[index]
            "support_geometry_trace_id"
        ] = f"trace-{'f' * 24}"
        self.assert_resigned_forgery_is_rejected(orphan_registry)

        swapped_span_endpoints = copy.deepcopy(audit)
        span = swapped_span_endpoints["topology_assessment"]["chains"][0]["spans"][0]  # type: ignore[index]
        span["left_support_id"], span["right_support_id"] = (
            span["right_support_id"],
            span["left_support_id"],
        )
        self.assert_resigned_forgery_is_rejected(swapped_span_endpoints)

        forged_span_id = copy.deepcopy(audit)
        forged_span_id["topology_assessment"]["chains"][0]["spans"][0]["span_id"] = (  # type: ignore[index]
            f"span-{'f' * 24}"
        )
        self.assert_resigned_forgery_is_rejected(forged_span_id)

        rebound_upper = copy.deepcopy(audit)
        upper = next(
            trace
            for trace in rebound_upper["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["role"] == "support_upper_annotations"
            and trace["chain_id"] == chain["chain_id"]
        )
        upper["support_id"] = next(
            support["support_id"]
            for support in supports
            if support["support_id"] != upper["support_id"]
        )
        refresh_finding(rebound_upper, upper)
        self.assert_resigned_forgery_is_rejected(rebound_upper)

        rebound_lower = copy.deepcopy(audit)
        lower = next(
            trace
            for trace in rebound_lower["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["role"] == "span_lower_annotations"
            and trace["chain_id"] == chain["chain_id"]
        )
        lower["span_id"] = next(
            span["span_id"] for span in spans if span["span_id"] != lower["span_id"]
        )
        refresh_finding(rebound_lower, lower)
        self.assert_resigned_forgery_is_rejected(rebound_lower)

        cross_chain = copy.deepcopy(audit)
        # A span cannot import a support owned by another chain.  The
        # registry has no fallback that would reinterpret this opaque ID.
        cross_chain["topology_assessment"]["chains"][0]["spans"][0][  # type: ignore[index]
            "right_support_id"
        ] = f"support-{'e' * 24}"
        self.assert_resigned_forgery_is_rejected(cross_chain)

    def test_resigned_findings_require_compatible_non_orphan_trace_ids(self) -> None:
        """Findings cannot cite an unrelated role or a nonexistent trace."""

        audit = self.audit_topology()
        assessment = audit["topology_assessment"]  # type: ignore[index]
        upper_finding = next(
            finding
            for finding in assessment["findings"]
            if finding["category"] == "support_upper_annotation"
        )
        lower_trace = next(
            trace
            for trace in assessment["traces"]
            if trace["role"] == "span_lower_annotations"
        )
        for case, trace_ids in {
            "incompatible-role": [lower_trace["trace_id"]],
            "orphan-trace": [f"trace-{'f' * 24}"],
        }.items():
            with self.subTest(case=case):
                forged = copy.deepcopy(audit)
                finding = next(
                    item
                    for item in forged["topology_assessment"]["findings"]  # type: ignore[index]
                    if item["finding_id"] == upper_finding["finding_id"]
                )
                finding["trace_ids"] = trace_ids
                self.assert_resigned_forgery_is_rejected(forged)

    def test_resigned_annotation_findings_require_status_bound_provenance(self) -> None:
        """Positive annotation statuses need one concrete canonical target trace."""

        def finding_for(
            assessment: dict[str, object],
            category: str,
        ) -> dict[str, object]:
            return next(
                item
                for item in assessment["findings"]  # type: ignore[index]
                if item["category"] == category
            )

        def trace_for(
            assessment: dict[str, object],
            role: str,
        ) -> dict[str, object]:
            return next(
                item
                for item in assessment["traces"]  # type: ignore[index]
                if item["role"] == role
            )

        def refresh_finding(
            finding: dict[str, object],
            trace: dict[str, object],
        ) -> None:
            finding["trace_ids"] = [trace["trace_id"]]
            finding["finding_id"] = derive_topology_finding_id(
                trace["trace_id"],
                finding["status"],
                trace["role"],
                trace["chain_id"],
                trace["support_id"],
                trace["span_id"],
            )

        def set_positive_presentation(
            finding: dict[str, object],
            status: str,
        ) -> None:
            finding.update(
                {
                    "status": status,
                    "visible_evidence": (
                        "已建立唯一拓扑位置"
                        if status == "一致"
                        else "位置与已建立拓扑不相容"
                    ),
                    "reasoning": (
                        "角色、文本边界和唯一拓扑目标一致"
                        if status == "一致"
                        else "角色、文本边界或引出线与唯一拓扑目标不相容"
                    ),
                    "unreadable_parts": "无",
                    "next_step": "保持只读结论",
                }
            )

        # An ambiguity trace is intentionally targetless.  Re-signing it as
        # either a legal or uniquely illegal placement must not make it
        # concrete evidence.
        for status in ("一致", "疑似不一致"):
            with self.subTest(case=f"ambiguity-to-{status}"):
                forged = copy.deepcopy(
                    self.audit_topology(variant="ambiguous-leader-targets")
                )
                assessment = forged["topology_assessment"]  # type: ignore[index]
                finding = finding_for(assessment, "span_lower_annotation")
                ambiguity = trace_for(assessment, "ambiguity")
                set_positive_presentation(finding, status)
                refresh_finding(finding, ambiguity)
                self.assert_resigned_forgery_is_rejected(forged)

        # Removing every target field from an otherwise positive concrete
        # trace cannot be papered over by a freshly derived finding ID.
        stripped = copy.deepcopy(self.audit_topology())
        assessment = stripped["topology_assessment"]  # type: ignore[index]
        finding = finding_for(assessment, "support_upper_annotation")
        trace = trace_for(assessment, "support_upper_annotations")
        trace.update(
            {
                "chain_id": None,
                "support_id": None,
                "span_id": None,
                "target_provenance_id": None,
            }
        )
        refresh_finding(finding, trace)
        self.assert_resigned_forgery_is_rejected(stripped)

        # A fully re-derived lower trace remains incompatible with an upper
        # conclusion even when its target provenance is otherwise canonical.
        mismatched = copy.deepcopy(self.audit_topology())
        assessment = mismatched["topology_assessment"]  # type: ignore[index]
        finding = finding_for(assessment, "support_upper_annotation")
        trace = trace_for(assessment, "support_upper_annotations")
        span_id = next(
            chain["spans"][0]["span_id"]
            for chain in assessment["chains"]
            if chain["chain_id"] == trace["chain_id"]
        )
        trace.update(
            {
                "role": "span_lower_annotations",
                "support_id": None,
                "span_id": span_id,
                "trace_id": derive_trace_id(
                    trace["identity_fingerprint"],
                    trace["content_fingerprint"],
                    "span_lower_annotations",
                ),
            }
        )
        trace["target_provenance_id"] = derive_annotation_target_provenance_id(
            trace["trace_id"],
            trace["chain_id"],
            None,
            span_id,
        )
        refresh_finding(finding, trace)
        self.assert_resigned_forgery_is_rejected(mismatched)

        # Likewise, a concrete positive trace cannot be relabelled ambiguity
        # after clearing its ownership and re-deriving every public ID.
        ambiguity = copy.deepcopy(self.audit_topology())
        assessment = ambiguity["topology_assessment"]  # type: ignore[index]
        finding = finding_for(assessment, "span_lower_annotation")
        trace = trace_for(assessment, "span_lower_annotations")
        trace.update(
            {
                "role": "ambiguity",
                "chain_id": None,
                "support_id": None,
                "span_id": None,
                "target_provenance_id": None,
                "trace_id": derive_trace_id(
                    trace["identity_fingerprint"],
                    trace["content_fingerprint"],
                    "ambiguity",
                ),
            }
        )
        refresh_finding(finding, trace)
        self.assert_resigned_forgery_is_rejected(ambiguity)

    def test_resigned_topology_provenance_ids_and_entity_types_are_exact(self) -> None:
        """Re-signing cannot exchange provenance, role types, or finding links."""

        audit = self.audit_topology(variant="repeated-text")
        assessment = audit["topology_assessment"]  # type: ignore[index]
        manifest = audit["inventory"]["entity_manifest"]  # type: ignore[index]
        beam = next(trace for trace in assessment["traces"] if trace["role"] == "beam_edges")
        upper = next(
            trace
            for trace in assessment["traces"]
            if trace["role"] == "support_upper_annotations"
        )
        text_entity = next(item for item in manifest if item["entity_type"] == "TEXT")
        line_entity = next(item for item in manifest if item["entity_type"] == "LINE")

        for case, trace_id, entity, role in (
            ("text-as-beam-edge", beam["trace_id"], text_entity, "beam_edges"),
            ("line-as-annotation", upper["trace_id"], line_entity, "support_upper_annotations"),
        ):
            with self.subTest(case=case):
                forged = copy.deepcopy(audit)
                trace = next(
                    item
                    for item in forged["topology_assessment"]["traces"]  # type: ignore[index]
                    if item["trace_id"] == trace_id
                )
                trace.update(
                    {
                        "entity_handle": entity["handle"],
                        "identity_fingerprint": entity["identity_fingerprint"],
                        "content_fingerprint": entity["content_fingerprint"],
                        "role": role,
                        "trace_id": derive_trace_id(
                            entity["identity_fingerprint"],
                            entity["content_fingerprint"],
                            role,
                        ),
                    }
                )
                self.assert_resigned_forgery_is_rejected(forged)

        arbitrary = copy.deepcopy(audit)
        arbitrary["topology_assessment"]["traces"][0]["trace_id"] = f"trace-{'a' * 24}"  # type: ignore[index]
        self.assert_resigned_forgery_is_rejected(arbitrary)

        trace_swap = copy.deepcopy(audit)
        first_trace, second_trace = trace_swap["topology_assessment"]["traces"][:2]  # type: ignore[index]
        first_trace["trace_id"], second_trace["trace_id"] = (
            second_trace["trace_id"],
            first_trace["trace_id"],
        )
        self.assert_resigned_forgery_is_rejected(trace_swap)

        ambiguity = copy.deepcopy(audit)
        ambiguity_trace = next(
            trace
            for trace in ambiguity["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["role"] == "ambiguity"
        )
        chain = ambiguity["topology_assessment"]["chains"][0]  # type: ignore[index]
        ambiguity_trace["chain_id"] = chain["chain_id"]
        ambiguity_trace["support_id"] = chain["supports"][0]["support_id"]
        self.assert_resigned_forgery_is_rejected(ambiguity)

        missing_owner = copy.deepcopy(audit)
        support_trace = next(
            trace
            for trace in missing_owner["topology_assessment"]["traces"]  # type: ignore[index]
            if trace["role"] == "support_geometry"
            and trace["support_id"] is not None
        )
        support_trace["chain_id"] = None
        self.assert_resigned_forgery_is_rejected(missing_owner)

        multi_chain = self.audit_topology(variant="collinear-different-ids")
        self.assertEqual(multi_chain["topology_assessment"]["chains"], [])  # type: ignore[index]
        self.assertFalse(
            any(
                trace["role"] == "support_geometry"
                for trace in multi_chain["topology_assessment"]["traces"]  # type: ignore[index]
            )
        )

        same_role_findings = [
            finding
            for finding in assessment["findings"]
            if finding["category"] == "span_lower_annotation"
        ]
        self.assertGreaterEqual(len(same_role_findings), 2)
        swapped = copy.deepcopy(audit)
        swapped_findings = [
            finding
            for finding in swapped["topology_assessment"]["findings"]  # type: ignore[index]
            if finding["category"] == "span_lower_annotation"
        ]
        swapped_findings[0]["trace_ids"], swapped_findings[1]["trace_ids"] = (
            swapped_findings[1]["trace_ids"],
            swapped_findings[0]["trace_ids"],
        )
        self.assert_resigned_forgery_is_rejected(swapped)

        swapped_ids = copy.deepcopy(audit)
        findings = swapped_ids["topology_assessment"]["findings"]  # type: ignore[index]
        findings[0]["finding_id"], findings[1]["finding_id"] = (
            findings[1]["finding_id"],
            findings[0]["finding_id"],
        )
        self.assert_resigned_forgery_is_rejected(swapped_ids)

    def test_unbound_supports_block_leader_evidence_without_nearest_fallback(self) -> None:
        """Disconnected configured support geometry remains private blocking evidence."""

        for variant in (
            "leader-crosses-unbound-support",
            "leader-touches-unbound-support",
            "leader-near-unbound-support",
            "leader-overlapping-unbound-supports",
        ):
            with self.subTest(variant=variant):
                statuses = self.topology_statuses(self.audit_topology(variant=variant))
                self.assertIn(("span_lower_annotation", "证据不足"), statuses)
                self.assertNotIn(("span_lower_annotation", "一致"), statuses)

        disjoint = self.topology_statuses(
            self.audit_topology(variant="leader-disjoint-unbound-support")
        )
        self.assertIn(("span_lower_annotation", "一致"), disjoint)

        annotation_overlap = self.topology_statuses(
            self.audit_topology(variant="annotation-overlaps-unbound-support")
        )
        self.assertIn(("span_lower_annotation", "证据不足"), annotation_overlap)
        self.assertNotIn(("span_lower_annotation", "一致"), annotation_overlap)

    def test_topology_schema_rejects_raw_or_unknown_metadata(self) -> None:
        forged = copy.deepcopy(self.audit_topology())
        forged["topology_assessment"]["traces"][0]["coordinates"] = [1, 2]  # type: ignore[index]
        forged = attach_integrity(forged)
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("audit", forged)
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)
        forged = copy.deepcopy(self.audit_topology())
        forged["topology_assessment"]["traces"][0][  # type: ignore[index]
            "parsed_value_fingerprint"
        ] = "0" * 64
        forged = attach_integrity(forged)
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("audit", forged)
        self.assertEqual(raised.exception.code, ErrorCode.AUDIT_SCHEMA_INVALID)
        with self.assertRaises(Exception):
            schema_for("audit", "liang-pingfa/audit/v9")

    def test_private_source_text_and_layer_names_are_not_serialized(self) -> None:
        audit = self.audit_topology()
        encoded = canonical_json_bytes(audit).decode("utf-8")
        for private_value in ("BEAM", "BEAM_ID", "SUPPORT", "UPPER", "LOWER", "U1", "L1"):
            self.assertNotIn(private_value, encoded)

    def test_fixed_cap_fails_closed_without_partial_assessment(self) -> None:
        with mock.patch(
            "liang_pingfa_review.topology_profile.MAX_ROLE_ENTITIES",
            1,
        ):
            with self.assertRaises(PipelineError) as raised:
                self.audit_topology()
        self.assertEqual(raised.exception.code, ErrorCode.TOPOLOGY_LIMIT_EXCEEDED)
        self.assertGreater(MAX_ROLE_ENTITIES, 1)

    def test_phase_two_projection_ignores_topology_and_keeps_overlay_targets(self) -> None:
        dxf = self.root / "overlay-v2.dxf"
        create_synthetic_dxf(dxf)
        snapshot = snapshot_dxf(dxf)
        now = datetime.now(timezone.utc)
        base = build_audit(
            snapshot,
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=now,
        )
        version_two = build_audit(
            snapshot,
            describe_source(self.source),
            oda_version=SUPPORTED_ODA_VERSION,
            now=now,
            topology_profile=self.profile,
        )
        self.assertEqual(
            audit_semantic_projection(base),
            audit_semantic_projection(version_two),
        )
        plan = generate_edit_plan(version_two, now=now)
        self.assertTrue(plan["operations"])
        self.assertTrue(
            all(
                operation["kind"] == "delete_auxiliary_overlay_text"
                for operation in plan["operations"]
            )
        )
        self.assertTrue(
            all(
                target["profile"] == "auxiliary-overlay-text-delete/v1"
                for target in version_two["audited_targets"]
            )
        )
        edited = self.root / "overlay-v2-edited.dxf"
        delete_audited_text_in_synthetic_dxf(
            dxf,
            edited,
            version_two,
            plan,
        )
        self.assertEqual(
            assert_postconditions(
                snapshot,
                snapshot_dxf(edited),
                version_two,
                plan,
            ),
            plan["expected_after"],
        )

    def test_only_audit_cli_exposes_profile_option(self) -> None:
        parser = build_parser()
        audit = parser.parse_args(
            [
                "audit",
                "--input",
                "input.dwg",
                "--audit-out",
                "audit.json",
                "--report-out",
                "audit.md",
                "--topology-profile",
                "profile.json",
            ]
        )
        self.assertEqual(str(audit.topology_profile), "profile.json")
        plan = parser.parse_args(
            [
                "plan",
                "--audit",
                "audit.json",
                "--plan-out",
                "plan.json",
                "--review-out",
                "review.md",
            ]
        )
        self.assertFalse(hasattr(plan, "topology_profile"))

    def test_invalid_profile_uses_only_redacted_stable_cli_error(self) -> None:
        invalid = self.root / "invalid-profile.json"
        invalid.write_text("{}", encoding="utf-8")
        events: list[dict[str, str]] = []
        with mock.patch(
            "liang_pingfa_review.cli._emit",
            side_effect=lambda event, **_kwargs: events.append(event),
        ):
            self.assertEqual(
                cli.main(
                    [
                        "audit",
                        "--input",
                        str(self.source),
                        "--audit-out",
                        str(self.root / "audit.json"),
                        "--report-out",
                        str(self.root / "audit.md"),
                        "--topology-profile",
                        str(invalid),
                    ]
                ),
                1,
            )
        self.assertEqual(
            events,
            [{"status": "error", "code": "TOPOLOGY_PROFILE_INVALID"}],
        )


if __name__ == "__main__":
    unittest.main()
