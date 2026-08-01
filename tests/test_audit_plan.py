"""Read-only snapshot, overlay evidence, and deterministic plan tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ezdxf
from ezdxf.entities.dxfclass import DXFClass
from ezdxf.enums import TextEntityAlignment
from ezdxf.sections.acdsdata import new_acds_data_section

from liang_pingfa_review import snapshots as snapshots_module
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.plan import generate_edit_plan
from liang_pingfa_review.raw_dxf import (
    assert_normalized_records_match,
    preflight_ascii_dxf,
)
from liang_pingfa_review.reports import render_audit_report
from liang_pingfa_review.snapshots import (
    VOLATILE_HEADER_VARIABLES,
    VOLATILE_OBJECT_TAG_ALLOWLIST,
    snapshot_document,
    snapshot_dxf,
)
from tests.support.synthetic_dxf import (
    build_synthetic_audit as audit_dxf_for_testing,
    create_fake_dwg,
    create_synthetic_dxf,
)
from tests.support.owned_files import install_non_windows_test_ownership


def _append_before_raw_eof(path: Path, payload: bytes) -> None:
    """Append a synthetic section before ezdxf's exact ASCII EOF marker."""

    raw = path.read_bytes()
    eof = b"  0\r\nEOF\r\n"
    index = raw.rfind(eof)
    if index < 0:
        raise AssertionError("synthetic DXF has no EOF marker")
    path.write_bytes(raw[:index] + payload + raw[index:])


def _append_to_raw_header(path: Path, payload: bytes) -> None:
    """Append one adversarial tag sequence inside the generated HEADER section."""

    raw = path.read_bytes()
    header_start = raw.find(b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n")
    if header_start < 0:
        raise AssertionError("synthetic DXF has no HEADER section")
    header_end = raw.find(b"  0\r\nENDSEC\r\n", header_start)
    if header_end < 0:
        raise AssertionError("synthetic DXF has no terminated HEADER section")
    path.write_bytes(raw[:header_end] + payload + raw[header_end:])


def _classes_record_bounds(raw: bytes) -> tuple[int, int]:
    """Locate the first generated CLASS record without committing a fixture."""

    start = raw.find(b"  0\r\nCLASS\r\n")
    if start < 0:
        raise AssertionError("synthetic DXF has no CLASS record")
    next_record = raw.find(b"  0\r\nCLASS\r\n", start + 1)
    section_end = raw.find(b"  0\r\nENDSEC\r\n", start)
    candidates = [index for index in (next_record, section_end) if index >= 0]
    if not candidates:
        raise AssertionError("synthetic CLASS record is unterminated")
    return start, min(candidates)


def _raw_record_bounds(raw: bytes, record_type: bytes) -> tuple[int, int]:
    """Locate one generated modeled record without committing a DXF fixture."""

    marker = b"  0\r\n" + record_type + b"\r\n"
    start = raw.find(marker)
    if start < 0:
        raise AssertionError("synthetic DXF record is absent")
    end = raw.find(b"  0\r\n", start + len(marker))
    if end < 0:
        raise AssertionError("synthetic DXF record is unterminated")
    return start, end


class AuditAndPlanTests(unittest.TestCase):
    """Exercise all fixed finding statuses without committed CAD fixtures."""

    def setUp(self) -> None:
        install_non_windows_test_ownership(self)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source.dwg"
        create_fake_dwg(self.source)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def audit_variant(self, variant: str) -> dict[str, object]:
        dxf = self.root / f"{variant}.dxf"
        create_synthetic_dxf(dxf, variant=variant)
        return audit_dxf_for_testing(dxf, self.source)

    def assert_variant_has_no_plan(self, variant: str) -> None:
        """Assert a synthetic counterexample cannot become a mutation plan."""

        audit = self.audit_variant(variant)
        self.assertEqual(audit["audited_targets"], [])
        self.assertTrue(
            all(finding["status"] == "证据不足" for finding in audit["findings"])
        )
        with self.assertRaises(PipelineError) as raised:
            generate_edit_plan(audit)
        self.assertEqual(raised.exception.code, ErrorCode.NO_ACTIONABLE_FINDINGS)

    def test_actionable_left_overlay_has_clean_right_counterpart(self) -> None:
        audit = self.audit_variant("actionable")
        statuses = {finding["status"] for finding in audit["findings"]}
        self.assertIn("疑似不一致", statuses)
        self.assertIn("一致", statuses)
        self.assertEqual(len(audit["audited_targets"]), 1)
        target = audit["audited_targets"][0]
        self.assertEqual(target["entity_type"], "TEXT")
        self.assertEqual(target["layout"], "modelspace")
        self.assertNotIn("text", target)
        self.assertNotIn("coordinates", target)
        self.assertRegex(
            audit["inventory"]["entity_order_manifest_digest"],
            r"^[a-f0-9]{64}$",
        )
        self.assertRegex(
            audit["fingerprints"]["entity_order_manifest_digest"],
            r"^[a-f0-9]{64}$",
        )

        plan = generate_edit_plan(audit)
        self.assertEqual(len(plan["operations"]), 1)
        self.assertEqual(plan["operations"][0]["kind"], "delete_auxiliary_overlay_text")
        self.assertNotIn("layer", plan["operations"][0]["target"])
        self.assertRegex(
            plan["expected_after"]["entity_order_manifest_digest"],
            r"^[a-f0-9]{64}$",
        )

    def test_ambiguous_overlay_is_evidence_insufficient_and_non_actionable(self) -> None:
        audit = self.audit_variant("ambiguous")
        self.assertEqual([finding["status"] for finding in audit["findings"]], ["证据不足"])
        self.assertEqual(audit["audited_targets"], [])
        with self.assertRaises(PipelineError) as raised:
            generate_edit_plan(audit)
        self.assertEqual(raised.exception.code, ErrorCode.NO_ACTIONABLE_FINDINGS)

    def test_duplicate_content_fingerprint_is_non_actionable(self) -> None:
        audit = self.audit_variant("duplicate")
        self.assertEqual(audit["audited_targets"], [])
        self.assertTrue(
            all(finding["status"] == "证据不足" for finding in audit["findings"])
        )

    def test_candidate_on_an_off_layer_is_not_actionable(self) -> None:
        self.assert_variant_has_no_plan("candidate-layer-off")

    def test_candidate_on_a_frozen_layer_is_not_actionable(self) -> None:
        self.assert_variant_has_no_plan("candidate-layer-frozen")

    def test_candidate_on_a_new_viewport_frozen_layer_is_not_actionable(self) -> None:
        self.assert_variant_has_no_plan("candidate-layer-viewport-frozen")

    def test_candidate_on_an_unsupported_flag_layer_is_not_actionable(self) -> None:
        self.assert_variant_has_no_plan("candidate-layer-unsupported-flag")

    def test_invisible_text_flag_is_not_actionable(self) -> None:
        self.assert_variant_has_no_plan("candidate-invisible")

    def test_hidden_interference_line_is_not_evidence(self) -> None:
        self.assert_variant_has_no_plan("hidden-evidence")

    def test_transparent_visual_evidence_is_not_actionable(self) -> None:
        """Opacity is required for every candidate, line, and supporting layer."""

        for variant in (
            "candidate-transparent",
            "interference-transparent",
            "candidate-layer-transparent",
        ):
            with self.subTest(variant=variant):
                self.assert_variant_has_no_plan(variant)

    def test_non_coplanar_candidate_and_evidence_are_not_actionable(self) -> None:
        """XY overlap at Z=100 cannot borrow frame/interference evidence at Z=0."""

        self.assert_variant_has_no_plan("noncoplanar")

    def test_coplanar_candidate_and_evidence_remain_actionable(self) -> None:
        audit = self.audit_variant("coplanar")
        self.assertEqual(len(audit["audited_targets"]), 1)
        plan = generate_edit_plan(audit)
        self.assertEqual(len(plan["operations"]), 1)

    def test_non_default_text_ocs_extrusion_is_not_actionable(self) -> None:
        dxf = self.root / "unsupported-text-ocs.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        candidate = next(
            entity
            for entity in document.modelspace()
            if entity.dxftype() == "TEXT"
            and entity.dxf.layer.casefold() == "temp"
        )
        candidate.dxf.extrusion = (0, 1, 0)
        document.saveas(dxf)

        audit = audit_dxf_for_testing(dxf, self.source)
        self.assertEqual(audit["audited_targets"], [])
        with self.assertRaises(PipelineError) as raised:
            generate_edit_plan(audit)
        self.assertEqual(raised.exception.code, ErrorCode.NO_ACTIONABLE_FINDINGS)

    def test_unsupported_right_panel_counterparts_block_left_deletion(self) -> None:
        """Unsupported right-side overlay evidence is ambiguity, never absence."""

        cases = (
            (
                "non-default-extrusion",
                lambda entity: setattr(entity.dxf, "extrusion", (0, 1, 0)),
            ),
            (
                "noncoplanar-z",
                lambda entity: setattr(entity.dxf, "insert", (245, 25, 100)),
            ),
            (
                "unsupported-rotation",
                lambda entity: setattr(entity.dxf, "rotation", 15),
            ),
            (
                "unsupported-alignment",
                lambda entity: entity.set_placement(
                    (245, 25), align=TextEntityAlignment.CENTER
                ),
            ),
            (
                "ambiguous-region-membership",
                lambda entity: (
                    setattr(entity.dxf, "rotation", 15),
                    setattr(entity.dxf, "insert", (200, 25)),
                ),
            ),
        )
        for name, configure in cases:
            with self.subTest(name=name):
                dxf = self.root / f"right-{name}.dxf"
                create_synthetic_dxf(dxf)
                document = ezdxf.readfile(dxf)
                right_overlay = document.modelspace().add_text(
                    "right-overlay",
                    dxfattribs={
                        "layer": "textarea",
                        "height": 5,
                        "insert": (245, 25),
                    },
                )
                configure(right_overlay)
                document.saveas(dxf)

                audit = audit_dxf_for_testing(dxf, self.source)
                self.assertEqual(audit["audited_targets"], [])
                self.assertTrue(
                    all(
                        finding["status"] == "证据不足"
                        for finding in audit["findings"]
                    )
                )
                with self.assertRaises(PipelineError) as raised:
                    generate_edit_plan(audit)
                self.assertEqual(
                    raised.exception.code, ErrorCode.NO_ACTIONABLE_FINDINGS
                )

    def test_boundary_overlapping_unsupported_right_text_blocks_left_deletion(
        self,
    ) -> None:
        """An outside anchor cannot prove rotated unsupported text is irrelevant."""

        dxf = self.root / "right-boundary-overlap.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        right_overlay = document.modelspace().add_text(
            "right-overlay",
            dxfattribs={
                "layer": "textarea",
                "height": 5,
                "insert": (199, 25),
                "rotation": 15,
            },
        )
        document.saveas(dxf)

        audit = audit_dxf_for_testing(dxf, self.source)
        self.assertEqual(audit["audited_targets"], [])
        self.assertTrue(
            all(finding["status"] == "证据不足" for finding in audit["findings"])
        )
        with self.assertRaises(PipelineError) as raised:
            generate_edit_plan(audit)
        self.assertEqual(raised.exception.code, ErrorCode.NO_ACTIONABLE_FINDINGS)

    def test_supported_disjoint_right_text_preserves_clean_right_conclusion(
        self,
    ) -> None:
        """Supported bounds can conservatively exclude a nearby right-panel match."""

        dxf = self.root / "right-supported-disjoint.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        document.modelspace().add_text(
            "x",
            dxfattribs={
                "layer": "textarea",
                "height": 5,
                "insert": (190, 25),
            },
        )
        document.saveas(dxf)

        audit = audit_dxf_for_testing(dxf, self.source)
        self.assertEqual(len(audit["audited_targets"]), 1)
        plan = generate_edit_plan(audit)
        self.assertEqual(len(plan["operations"]), 1)

    def test_layer_visibility_changes_preservation_state(self) -> None:
        dxf = self.root / "layer-visibility.dxf"
        create_synthetic_dxf(dxf)
        before = snapshot_dxf(dxf)

        document = ezdxf.readfile(dxf)
        document.layers.get("TEMP").off()
        document.saveas(dxf)
        after = snapshot_dxf(dxf)

        self.assertNotEqual(
            before.layer_manifest_digest,
            after.layer_manifest_digest,
        )
        self.assertNotEqual(
            before.preservation_state(paired_right_panel_digest="0" * 64),
            after.preservation_state(paired_right_panel_digest="0" * 64),
        )

    def test_entity_and_layer_transparency_bind_preservation_state(self) -> None:
        """Transparency changes are visible preservation changes, not metadata."""

        dxf = self.root / "transparency-preservation.dxf"
        create_synthetic_dxf(dxf)
        before = snapshot_dxf(dxf)
        before_state = before.preservation_state(
            paired_right_panel_digest="0" * 64
        )

        document = ezdxf.readfile(dxf)
        candidate = next(
            entity
            for entity in document.modelspace()
            if entity.dxftype() == "TEXT"
            and entity.dxf.layer.casefold() == "temp"
        )
        candidate.transparency = 0.5
        candidate_handle = candidate.dxf.handle
        document.saveas(dxf)
        entity_changed = snapshot_dxf(dxf)
        entity_record = entity_changed.records_by_handle[candidate_handle]
        self.assertIsNotNone(entity_record.entity_transparency)
        self.assertEqual(entity_record.layer_transparency, 0.0)
        self.assertNotEqual(
            before_state,
            entity_changed.preservation_state(paired_right_panel_digest="0" * 64),
        )

        document = ezdxf.readfile(dxf)
        document.layers.get("TEMP").transparency = 0.5
        document.saveas(dxf)
        layer_changed = snapshot_dxf(dxf)
        layer_record = layer_changed.records_by_handle[candidate_handle]
        self.assertGreater(layer_record.layer_transparency, 0.0)
        self.assertNotEqual(
            entity_changed.preservation_state(paired_right_panel_digest="0" * 64),
            layer_changed.preservation_state(paired_right_panel_digest="0" * 64),
        )

    def test_classes_section_binds_all_serializable_class_fields(self) -> None:
        """A registered class's serialized flags cannot escape preservation."""

        dxf = self.root / "classes-preservation.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        document.classes.register(
            DXFClass.new(
                doc=document,
                dxfattribs={
                    "name": "SYNTHETIC_CLASS",
                    "cpp_class_name": "SyntheticCppClass",
                    "app_name": "SyntheticApp",
                    "flags": 17,
                    "instance_count": 23,
                    "was_a_proxy": 1,
                    "is_an_entity": 1,
                },
            )
        )
        document.saveas(dxf)
        before = snapshot_dxf(dxf)

        document = ezdxf.readfile(dxf)
        registered = document.classes.get("SYNTHETIC_CLASS")
        registered.dxf.flags = int(registered.dxf.flags) + 1
        document.saveas(dxf)
        after = snapshot_dxf(dxf)

        self.assertNotEqual(
            before.classes_manifest_digest, after.classes_manifest_digest
        )
        self.assertNotEqual(
            before.preservation_state(paired_right_panel_digest="0" * 64),
            after.preservation_state(paired_right_panel_digest="0" * 64),
        )
        audit = audit_dxf_for_testing(dxf, self.source)
        self.assertEqual(
            audit["inventory"]["classes_manifest_digest"],
            after.classes_manifest_digest,
        )
        self.assertNotIn("SYNTHETIC_CLASS", str(audit))

    def test_unsupported_classes_content_fails_closed(self) -> None:
        """A non-serializable class entry is rejected instead of omitted."""

        dxf = self.root / "unsupported-classes.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        document.classes.classes[("unsupported", "unsupported")] = object()
        with self.assertRaises(PipelineError) as raised:
            snapshot_document(document)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_raw_preflight_rejects_unknown_sections_and_malformed_structure(
        self,
    ) -> None:
        """Raw top-level structure is rejected before ezdxf can discard it."""

        thumbnail = self.root / "thumbnail.dxf"
        create_synthetic_dxf(thumbnail)
        _append_before_raw_eof(
            thumbnail,
            b"  0\r\nSECTION\r\n  2\r\nTHUMBNAILIMAGE\r\n  0\r\nENDSEC\r\n",
        )
        with mock.patch("liang_pingfa_review.snapshots.ezdxf.read") as readfile:
            with self.assertRaises(PipelineError) as raised:
                snapshot_dxf(thumbnail)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)
        readfile.assert_not_called()

        malformed = self.root / "malformed-lines.dxf"
        malformed.write_bytes(b"0\n")
        with self.assertRaises(PipelineError) as raised:
            preflight_ascii_dxf(malformed)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

        trailing = self.root / "trailing-data.dxf"
        create_synthetic_dxf(trailing)
        trailing.write_bytes(trailing.read_bytes() + b"999\r\ntrailing\r\n")
        with self.assertRaises(PipelineError) as raised:
            preflight_ascii_dxf(trailing)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_leased_dxf_bytes_survive_preflight_and_parser_aba_attempts(
        self,
    ) -> None:
        """Preflight, parser, congruence, and snapshot consume one byte image."""

        for phase in ("after-preflight", "after-parse"):
            with self.subTest(phase=phase):
                dxf = self.root / f"leased-{phase}.dxf"
                replacement = self.root / f"replacement-{phase}.dxf"
                create_synthetic_dxf(dxf, variant="actionable")
                create_synthetic_dxf(replacement, variant="ambiguous")
                expected = snapshot_dxf(dxf)
                original_preflight = snapshots_module.preflight_ascii_dxf_bytes
                original_read = ezdxf.read
                attempted: list[OSError | None] = []

                def attempt_replacement() -> None:
                    try:
                        replacement.replace(dxf)
                    except OSError as error:
                        attempted.append(error)
                    else:
                        attempted.append(None)

                def preflight_then_replace(raw: bytes) -> object:
                    checked = original_preflight(raw)
                    if not attempted:
                        attempt_replacement()
                    return checked

                def read_then_replace(stream: object) -> object:
                    document = original_read(stream)  # type: ignore[arg-type]
                    if not attempted:
                        attempt_replacement()
                    return document

                patch_target = (
                    "liang_pingfa_review.snapshots.preflight_ascii_dxf_bytes"
                    if phase == "after-preflight"
                    else "liang_pingfa_review.snapshots.ezdxf.read"
                )
                replacement_hook = (
                    preflight_then_replace
                    if phase == "after-preflight"
                    else read_then_replace
                )
                try:
                    with mock.patch(patch_target, side_effect=replacement_hook):
                        snapshot = snapshot_dxf(dxf)
                except PipelineError as raised:
                    self.assertEqual(
                        attempted,
                        [None],
                        "only a successful replacement may fail leased loading",
                    )
                    self.assertEqual(
                        raised.code,
                        ErrorCode.SOURCE_CHANGED_DURING_RUN,
                    )
                else:
                    self.assertEqual(len(attempted), 1)
                    self.assertIsInstance(attempted[0], OSError)
                    self.assertEqual(snapshot, expected)
                finally:
                    # A successful synthetic swap is intentionally preserved
                    # through the assertion above; restore only this generated
                    # fixture for the enclosing TemporaryDirectory cleanup.
                    if replacement.exists():
                        replacement.unlink()
                    if dxf.exists():
                        dxf.unlink()

    def test_raw_congruence_rejects_an_unexpected_normalized_record(self) -> None:
        """The explicit normalization allowlist cannot hide an extra record."""

        dxf = self.root / "extra-normalized-record.dxf"
        create_synthetic_dxf(dxf)
        source = preflight_ascii_dxf(dxf)
        normalized = replace(
            source,
            modeled_records=source.modeled_records + (source.modeled_records[0],),
        )
        with self.assertRaises(PipelineError) as raised:
            assert_normalized_records_match(source, normalized)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_raw_classes_reject_unknown_tags_and_duplicate_records_before_load(
        self,
    ) -> None:
        """No raw CLASS construct may be normalized away by ezdxf."""

        for name, mutate in (
            (
                "unknown-class-tag",
                lambda raw, _start, end, _record: raw[:end]
                + b"999\r\nunsupported\r\n"
                + raw[end:],
            ),
            (
                "duplicate-class",
                lambda raw, _start, end, record: raw[:end] + record + raw[end:],
            ),
        ):
            with self.subTest(name=name):
                dxf = self.root / f"{name}.dxf"
                create_synthetic_dxf(dxf)
                raw = dxf.read_bytes()
                start, end = _classes_record_bounds(raw)
                dxf.write_bytes(mutate(raw, start, end, raw[start:end]))
                with mock.patch(
                    "liang_pingfa_review.snapshots.ezdxf.read"
                ) as readfile:
                    with self.assertRaises(PipelineError) as raised:
                        snapshot_dxf(dxf)
                self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)
                readfile.assert_not_called()

    def test_raw_modeled_records_reject_lossy_extensions_before_loading(
        self,
    ) -> None:
        """No entity subclass, XDATA, app-data, or comment reaches ezdxf first."""

        cases = (
            ("unknown-subclass", b"100\r\nOpaqueSubclass\r\n"),
            (
                "vendor-xdata",
                b"1001\r\nOpaqueVendor\r\n1000\r\nopaque-payload\r\n",
            ),
            ("application-data", b"102\r\n{OPAQUE_APP\r\n102\r\n}\r\n"),
            ("extension-dictionary", b"360\r\nF00D\r\n"),
            ("discarded-comment", b"999\r\ndiscarded\r\n"),
        )
        for name, payload in cases:
            with self.subTest(name=name):
                dxf = self.root / f"raw-{name}.dxf"
                create_synthetic_dxf(dxf)
                raw = dxf.read_bytes()
                start, end = _raw_record_bounds(raw, b"LINE")
                dxf.write_bytes(raw[:end] + payload + raw[end:])
                with mock.patch(
                    "liang_pingfa_review.snapshots.ezdxf.read"
                ) as readfile:
                    with self.assertRaises(PipelineError) as raised:
                        snapshot_dxf(dxf)
                self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)
                readfile.assert_not_called()

    def test_raw_class_flags_and_normalization_loss_bind_preservation(self) -> None:
        """Raw declarations survive ordering-independent matching and detection."""

        flags = self.root / "raw-class-flags.dxf"
        create_synthetic_dxf(flags)
        before = snapshot_dxf(flags)
        raw = flags.read_bytes()
        start, end = _classes_record_bounds(raw)
        record = raw[start:end]
        marker = b"280\r\n0\r\n"
        self.assertIn(marker, record)
        flags.write_bytes(
            raw[:start] + record.replace(marker, b"280\r\n1\r\n", 1) + raw[end:]
        )
        after = snapshot_dxf(flags)
        self.assertNotEqual(
            before.classes_manifest_digest, after.classes_manifest_digest
        )
        self.assertNotEqual(
            before.raw_classes_manifest_digest,
            after.raw_classes_manifest_digest,
        )
        self.assertNotEqual(
            before.preservation_state(paired_right_panel_digest="0" * 64),
            after.preservation_state(paired_right_panel_digest="0" * 64),
        )

        lossy = self.root / "raw-class-normalization-loss.dxf"
        create_synthetic_dxf(lossy)
        raw = lossy.read_bytes()
        start, end = _classes_record_bounds(raw)
        record = raw[start:end]
        marker = b" 90\r\n0\r\n"
        self.assertIn(marker, record)
        lossy.write_bytes(
            raw[:start] + record.replace(marker, b" 90\r\n00\r\n", 1) + raw[end:]
        )
        wire_before = preflight_ascii_dxf(lossy)
        before_normalization = snapshot_dxf(lossy)
        normalized = ezdxf.readfile(lossy)
        normalized.saveas(lossy)
        wire_after = preflight_ascii_dxf(lossy)
        after_normalization = snapshot_dxf(lossy)
        self.assertNotEqual(
            wire_before.classes_wire_manifest_digest,
            wire_after.classes_wire_manifest_digest,
        )
        self.assertEqual(
            before_normalization.raw_classes_manifest_digest,
            after_normalization.raw_classes_manifest_digest,
        )
        self.assertEqual(
            before_normalization.preservation_state(
                paired_right_panel_digest="0" * 64
            ),
            after_normalization.preservation_state(
                paired_right_panel_digest="0" * 64
            ),
        )

    def test_in_memory_documents_use_the_same_raw_acdsdata_preflight(self) -> None:
        """Synthetic documents cannot bypass production raw-section semantics."""

        document = ezdxf.new("R2018")
        document.modelspace().add_line((0, 0), (1, 1))
        document.acdsdata = new_acds_data_section(document)
        document.acdsdata.new_acis_data("SYNTHETIC_ACDS_HANDLE", b"payload")
        with self.assertRaises(PipelineError) as raised:
            snapshot_document(document)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_nonempty_acdsdata_fails_closed_before_model_loading(self) -> None:
        """Initial release rejects ACDSDATA records instead of dropping them."""

        dxf = self.root / "nonempty-acdsdata.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        document.acdsdata = new_acds_data_section(document)
        document.acdsdata.new_acis_data(
            "SYNTHETIC_ACDS_HANDLE", b"\x00synthetic-acdsdata\xff"
        )
        document.saveas(dxf)
        with mock.patch("liang_pingfa_review.snapshots.ezdxf.read") as readfile:
            with self.assertRaises(PipelineError) as raised:
                snapshot_dxf(dxf)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)
        readfile.assert_not_called()

    def test_only_canonical_empty_acdsdata_is_supported(self) -> None:
        """Empty ACDSDATA has one raw three-tag representation, nothing else."""

        canonical = self.root / "canonical-empty-acdsdata.dxf"
        create_synthetic_dxf(canonical)
        _append_before_raw_eof(
            canonical,
            b"  0\r\nSECTION\r\n  2\r\nACDSDATA\r\n  0\r\nENDSEC\r\n",
        )
        snapshot = snapshot_dxf(canonical)
        self.assertRegex(snapshot.acdsdata_manifest_digest, r"^[a-f0-9]{64}$")

        noncanonical = self.root / "noncanonical-empty-acdsdata.dxf"
        create_synthetic_dxf(noncanonical)
        _append_before_raw_eof(
            noncanonical,
            b"  0\r\nSECTION\r\n  2\r\nACDSDATA\r\n 70\r\n0\r\n  0\r\nENDSEC\r\n",
        )
        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(noncanonical)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_malformed_acdsdata_content_fails_closed(self) -> None:
        """ACDSDATA objects outside ezdxf's serializable model are rejected."""

        dxf = self.root / "unsupported-acdsdata.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        document.acdsdata = new_acds_data_section(document)
        document.acdsdata.new_acis_data("SYNTHETIC_ACDS_HANDLE", b"payload")
        document.acdsdata.entities.append(object())
        with self.assertRaises(PipelineError) as raised:
            snapshot_document(document)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_ucs_view_and_vport_records_are_preserved_completely(self) -> None:
        """Named table record geometry/properties must affect preservation state."""

        cases = (
            (
                "ucs",
                lambda document: document.ucs.add(
                    "SYNTHETIC_UCS",
                    dxfattribs={"origin": (1, 2, 3)},
                ),
                lambda document: setattr(
                    document.ucs.get("SYNTHETIC_UCS").dxf,
                    "origin",
                    (4, 5, 6),
                ),
            ),
            (
                "view",
                lambda document: document.views.add(
                    "SYNTHETIC_VIEW",
                    dxfattribs={"height": 12.0, "width": 24.0},
                ),
                lambda document: setattr(
                    document.views.get("SYNTHETIC_VIEW").dxf, "height", 18.0
                ),
            ),
            (
                "vport",
                lambda document: document.viewports.add(
                    "SYNTHETIC_VPORT",
                    dxfattribs={"height": 12.0, "center": (2, 3)},
                ),
                lambda document: setattr(
                    document.viewports.get("SYNTHETIC_VPORT")[0].dxf,
                    "center",
                    (4, 5),
                ),
            ),
        )
        for name, create, change in cases:
            with self.subTest(name=name):
                dxf = self.root / f"{name}-preservation.dxf"
                create_synthetic_dxf(dxf)
                document = ezdxf.readfile(dxf)
                create(document)
                document.saveas(dxf)
                before = snapshot_dxf(dxf)

                document = ezdxf.readfile(dxf)
                change(document)
                document.saveas(dxf)
                after = snapshot_dxf(dxf)

                self.assertNotEqual(
                    before.table_style_manifest_digest,
                    after.table_style_manifest_digest,
                )
                self.assertNotEqual(
                    before.preservation_state(
                        paired_right_panel_digest="0" * 64
                    ),
                    after.preservation_state(
                        paired_right_panel_digest="0" * 64
                    ),
                )

    def test_unsupported_table_record_metadata_fails_closed(self) -> None:
        """Table XDATA is rejected rather than omitted from preservation."""

        dxf = self.root / "ucs-xdata.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        document.appids.new("TABLEAPP")
        ucs = document.ucs.add("SYNTHETIC_UCS")
        ucs.set_xdata("TABLEAPP", [(1000, "metadata")])
        document.saveas(dxf)

        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(dxf)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_unknown_table_name_is_rejected_before_ezdxf_loading(self) -> None:
        """An R2018 ``TABLE/CUSTOM`` record cannot trigger a parser traceback."""

        dxf = self.root / "unknown-table.dxf"
        create_synthetic_dxf(dxf)
        raw = dxf.read_bytes()
        supported_header = b"  0\r\nTABLE\r\n  2\r\nLAYER\r\n"
        self.assertIn(supported_header, raw)
        dxf.write_bytes(
            raw.replace(
                supported_header,
                b"  0\r\nTABLE\r\n  2\r\nCUSTOM\r\n",
                1,
            )
        )

        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(dxf)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_loader_keyerror_is_translated_at_snapshot_boundary(self) -> None:
        """Implementation-specific table loader details remain redacted."""

        dxf = self.root / "loader-keyerror.dxf"
        create_synthetic_dxf(dxf)
        with mock.patch(
            "liang_pingfa_review.snapshots.ezdxf.read",
            side_effect=KeyError("synthetic-private-table-name"),
        ):
            with self.assertRaises(PipelineError) as raised:
                snapshot_dxf(dxf)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_header_manifest_binds_ltscale_but_allows_only_volatile_updates(self) -> None:
        dxf = self.root / "header-manifest.dxf"
        create_synthetic_dxf(dxf)
        before = snapshot_dxf(dxf)

        document = ezdxf.readfile(dxf)
        document.header["$LTSCALE"] = float(document.header["$LTSCALE"]) + 1.0
        document.saveas(dxf)
        changed_representation = snapshot_dxf(dxf)
        self.assertNotEqual(
            before.header_manifest_digest,
            changed_representation.header_manifest_digest,
        )
        self.assertNotEqual(
            before.raw_header_manifest_digest,
            changed_representation.raw_header_manifest_digest,
        )
        self.assertNotEqual(
            before.preservation_state(paired_right_panel_digest="0" * 64),
            changed_representation.preservation_state(
                paired_right_panel_digest="0" * 64
            ),
        )

        document = ezdxf.readfile(dxf)
        document.header["$TDUPDATE"] = float(document.header["$TDUPDATE"]) + 1.0
        document.header["$TDCREATE"] = float(document.header["$TDCREATE"]) + 1.0
        document.saveas(dxf)
        changed_volatile = snapshot_dxf(dxf)
        self.assertIn("$TDUPDATE", VOLATILE_HEADER_VARIABLES)
        self.assertIn("$TDCREATE", VOLATILE_HEADER_VARIABLES)
        self.assertIn("$ACADMAINTVER", VOLATILE_HEADER_VARIABLES)
        self.assertEqual(
            changed_representation.header_manifest_digest,
            changed_volatile.header_manifest_digest,
        )
        self.assertEqual(
            changed_representation.raw_header_manifest_digest,
            changed_volatile.raw_header_manifest_digest,
        )

    def test_raw_header_comments_are_rejected_before_snapshot_or_plan(self) -> None:
        """HEADER comments cannot vanish through ezdxf serialization."""

        for suffix in (b"first-private-comment", b"second-private-comment"):
            with self.subTest(suffix=suffix):
                dxf = self.root / f"header-comment-{suffix.decode('ascii')}.dxf"
                create_synthetic_dxf(dxf)
                _append_to_raw_header(dxf, b"999\r\n" + suffix + b"\r\n")
                with self.assertRaises(PipelineError) as raised:
                    snapshot_dxf(dxf)
                self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_unknown_or_malformed_raw_header_records_are_rejected(self) -> None:
        """No unknown variable or tag can reach the permissive HEADER loader."""

        cases = {
            "unknown-variable": b"  9\r\n$UNMODELED_HEADER\r\n  1\r\nprivate\r\n",
            "unexpected-tag": b"  9\r\n$LTSCALE\r\n 41\r\n1.0\r\n",
            "duplicate-variable": b"  9\r\n$LTSCALE\r\n 40\r\n1.0\r\n",
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                dxf = self.root / f"header-{name}.dxf"
                create_synthetic_dxf(dxf)
                _append_to_raw_header(dxf, payload)
                with self.assertRaises(PipelineError) as raised:
                    snapshot_dxf(dxf)
                self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_clean_r2018_objects_section_is_supported_and_fingerprinted(self) -> None:
        dxf = self.root / "normal-r2018-objects.dxf"
        document = ezdxf.new("R2018")
        document.modelspace().add_line((0, 0), (1, 0))
        document.saveas(dxf)

        snapshot = snapshot_dxf(dxf)
        self.assertRegex(snapshot.objects_manifest_digest, r"^[a-f0-9]{64}$")
        self.assertRegex(snapshot.header_manifest_digest, r"^[a-f0-9]{64}$")

    def test_only_ezdxf_writer_marker_is_object_volatility_allowlisted(self) -> None:
        dxf = self.root / "object-volatility.dxf"
        create_synthetic_dxf(dxf)
        before = snapshot_dxf(dxf)

        document = ezdxf.readfile(dxf)
        document.saveas(dxf)
        after = snapshot_dxf(dxf)

        self.assertIn(
            ("EZDXF_META", "WRITTEN_BY_EZDXF", 1),
            VOLATILE_OBJECT_TAG_ALLOWLIST,
        )
        self.assertEqual(before.objects_manifest_digest, after.objects_manifest_digest)

    def test_human_audit_report_is_actionability_not_cardinality_based(self) -> None:
        one_finding = {
            "findings": [
                {
                    "actionability": True,
                    "finding_id": "synthetic-finding-one",
                    "handle": "synthetic-handle-one",
                    "text": "synthetic-private-text-one",
                    "coordinates": [1, 2, 3],
                    "source_path": "synthetic-private-path-one",
                    "source_hash": "synthetic-private-hash-one",
                }
            ]
        }
        multiple_findings = {
            "findings": one_finding["findings"]
            + [
                {
                    "actionability": True,
                    "finding_id": "synthetic-finding-two",
                    "handle": "synthetic-handle-two",
                    "text": "synthetic-private-text-two",
                    "coordinates": [4, 5, 6],
                    "source_path": "synthetic-private-path-two",
                    "source_hash": "synthetic-private-hash-two",
                }
            ]
        }

        one_report = render_audit_report(one_finding)
        multiple_report = render_audit_report(multiple_findings)
        self.assertEqual(one_report, multiple_report)
        for private_value in (
            "synthetic-finding-one",
            "synthetic-finding-two",
            "synthetic-handle-one",
            "synthetic-handle-two",
            "synthetic-private-text-one",
            "synthetic-private-text-two",
            "synthetic-private-path-one",
            "synthetic-private-path-two",
            "synthetic-private-hash-one",
            "synthetic-private-hash-two",
        ):
            self.assertNotIn(private_value, one_report)

    def test_disconnected_translated_lines_cannot_authorize_gap_text(self) -> None:
        """Loose translated segments must not create a panel-sized envelope."""

        dxf = self.root / "disconnected-lines.dxf"
        document = ezdxf.new("R2018")
        modelspace = document.modelspace()
        document.layers.new("TEMP")
        for offset_x in (0, 200):
            for start, end in (
                ((0, 0), (10, 0)),
                ((90, 100), (100, 100)),
                ((0, 0), (0, 10)),
                ((100, 90), (100, 100)),
            ):
                modelspace.add_line(
                    (start[0] + offset_x, start[1]),
                    (end[0] + offset_x, end[1]),
                )
        # This line genuinely intersects the candidate, but no connected closed
        # frame contains the candidate. The old envelope merge made it actionable.
        modelspace.add_line((40, 50), (70, 50))
        modelspace.add_text(
            "synthetic-gap-marker",
            dxfattribs={"layer": "TEMP", "height": 5, "insert": (45, 50)},
        )
        document.saveas(dxf)

        audit = audit_dxf_for_testing(dxf, self.source)
        self.assertEqual(audit["audited_targets"], [])
        self.assertTrue(
            all(finding["status"] == "证据不足" for finding in audit["findings"])
        )
        with self.assertRaises(PipelineError) as raised:
            generate_edit_plan(audit)
        self.assertEqual(raised.exception.code, ErrorCode.NO_ACTIONABLE_FINDINGS)

    def test_unsupported_overlay_text_alignment_or_rotation_fails_closed(self) -> None:
        """Only the supported text geometry can provide interference evidence."""

        for name, configure in (
            (
                "centered",
                lambda entity: entity.set_placement(
                    (45, 25), align=TextEntityAlignment.CENTER
                ),
            ),
            ("rotated", lambda entity: setattr(entity.dxf, "rotation", 15)),
        ):
            with self.subTest(name=name):
                dxf = self.root / f"{name}.dxf"
                create_synthetic_dxf(dxf)
                document = ezdxf.readfile(dxf)
                candidate = next(
                    entity
                    for entity in document.modelspace()
                    if entity.dxftype() == "TEXT"
                    and entity.dxf.layer.casefold() == "temp"
                )
                configure(candidate)
                document.saveas(dxf)

                audit = audit_dxf_for_testing(dxf, self.source)
                self.assertEqual(audit["audited_targets"], [])
                self.assertEqual(
                    [finding["status"] for finding in audit["findings"]],
                    ["证据不足"],
                )

    def test_no_overlay_has_no_actionable_findings(self) -> None:
        audit = self.audit_variant("clean")
        self.assertEqual(audit["audited_targets"], [])
        with self.assertRaises(PipelineError) as raised:
            generate_edit_plan(audit)
        self.assertEqual(raised.exception.code, ErrorCode.NO_ACTIONABLE_FINDINGS)

    def test_unknown_entity_and_extension_data_fail_closed(self) -> None:
        dxf = self.root / "unsupported.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        document.modelspace().add_circle((10, 10), 2)
        document.saveas(dxf)
        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(dxf)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

        dxf_with_xdata = self.root / "xdata.dxf"
        create_synthetic_dxf(dxf_with_xdata)
        document = ezdxf.readfile(dxf_with_xdata)
        document.appids.new("TESTAPP")
        entity = next(item for item in document.modelspace() if item.dxftype() == "TEXT")
        entity.set_xdata("TESTAPP", [(1000, "metadata")])
        document.saveas(dxf_with_xdata)
        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(dxf_with_xdata)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

        dxf_with_extension_dictionary = self.root / "extension-dictionary.dxf"
        create_synthetic_dxf(dxf_with_extension_dictionary)
        document = ezdxf.readfile(dxf_with_extension_dictionary)
        entity = next(item for item in document.modelspace() if item.dxftype() == "TEXT")
        entity.new_extension_dict()
        document.saveas(dxf_with_extension_dictionary)
        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(dxf_with_extension_dictionary)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_referenced_anonymous_blocks_are_validated_and_preserved(self) -> None:
        """Anonymous reusable blocks cannot hide unsupported data or mutations."""

        circle_dxf = self.root / "anonymous-circle.dxf"
        create_synthetic_dxf(circle_dxf)
        document = ezdxf.readfile(circle_dxf)
        anonymous = document.blocks.new("*U101")
        anonymous.add_circle((0, 0), 1)
        document.modelspace().add_blockref(anonymous.name, (10, 10))
        document.saveas(circle_dxf)
        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(circle_dxf)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

        xdata_dxf = self.root / "anonymous-xdata.dxf"
        create_synthetic_dxf(xdata_dxf)
        document = ezdxf.readfile(xdata_dxf)
        document.appids.new("TESTAPP")
        anonymous = document.blocks.new("*D101")
        contained_line = anonymous.add_line((0, 0), (1, 0))
        contained_line.set_xdata("TESTAPP", [(1000, "metadata")])
        document.modelspace().add_blockref(anonymous.name, (10, 10))
        document.saveas(xdata_dxf)
        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(xdata_dxf)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

        supported_dxf = self.root / "anonymous-supported.dxf"
        create_synthetic_dxf(supported_dxf)
        document = ezdxf.readfile(supported_dxf)
        anonymous = document.blocks.new("*U102")
        contained_line = anonymous.add_line((0, 0), (5, 0))
        document.modelspace().add_blockref(anonymous.name, (10, 10))
        document.saveas(supported_dxf)

        before = snapshot_dxf(supported_dxf)
        self.assertIn(
            contained_line.dxf.handle,
            {
                record.handle
                for record in before.records
                if record.layout == "block"
            },
        )
        before_state = before.preservation_state(
            paired_right_panel_digest="0" * 64
        )

        document = ezdxf.readfile(supported_dxf)
        document.entitydb[contained_line.dxf.handle].dxf.end = (8, 0)
        document.saveas(supported_dxf)
        after = snapshot_dxf(supported_dxf)
        after_state = after.preservation_state(
            paired_right_panel_digest="0" * 64
        )
        self.assertNotEqual(before.table_style_manifest_digest, after.table_style_manifest_digest)
        self.assertNotEqual(before_state, after_state)

    def test_entity_draw_order_is_preserved_for_layout_and_block_streams(self) -> None:
        """Entity handles are not a substitute for modelspace/block draw order."""

        dxf = self.root / "entity-order.dxf"
        create_synthetic_dxf(dxf)
        document = ezdxf.readfile(dxf)
        modelspace = document.modelspace()
        layout_line = modelspace.add_line((320, 0), (330, 0))
        layout_text = modelspace.add_text(
            "order",
            dxfattribs={"height": 2, "insert": (320, 5)},
        )
        layout_hatch = modelspace.add_hatch(color=1)
        layout_hatch.paths.add_polyline_path(
            [(320, 10), (330, 10), (330, 20), (320, 20)],
            is_closed=True,
        )
        block = document.blocks.new("ORDER_STREAM")
        block_line = block.add_line((0, 0), (5, 0))
        block_text = block.add_text(
            "order",
            dxfattribs={"height": 1, "insert": (0, 1)},
        )
        block_hatch = block.add_hatch(color=1)
        block_hatch.paths.add_polyline_path(
            [(0, 2), (5, 2), (5, 5), (0, 5)],
            is_closed=True,
        )
        modelspace.add_blockref(block.name, (340, 0))
        layout_handles = (
            layout_line.dxf.handle,
            layout_text.dxf.handle,
            layout_hatch.dxf.handle,
        )
        block_handles = (
            block_line.dxf.handle,
            block_text.dxf.handle,
            block_hatch.dxf.handle,
        )
        document.saveas(dxf)

        before = snapshot_dxf(dxf)

        def ordered_stream(snapshot: object, handles: tuple[str, ...]) -> list[tuple[str, str, str]]:
            records = [
                record
                for record in snapshot.records  # type: ignore[attr-defined]
                if record.handle in handles
            ]
            return [
                (
                    record.handle,
                    record.identity_fingerprint,
                    record.content_fingerprint,
                )
                for record in sorted(records, key=lambda record: record.sequence_index)
            ]

        layout_before = ordered_stream(before, layout_handles)
        block_before = ordered_stream(before, block_handles)

        document = ezdxf.readfile(dxf)
        modelspace = document.modelspace()
        for handle in layout_handles:
            modelspace.unlink_entity(document.entitydb[handle])
        for handle in reversed(layout_handles):
            modelspace.add_entity(document.entitydb[handle])
        block = document.blocks.get("ORDER_STREAM")
        for handle in block_handles:
            block.unlink_entity(document.entitydb[handle])
        for handle in reversed(block_handles):
            block.add_entity(document.entitydb[handle])
        document.saveas(dxf)

        after = snapshot_dxf(dxf)
        layout_after = ordered_stream(after, layout_handles)
        block_after = ordered_stream(after, block_handles)
        self.assertEqual(set(layout_before), set(layout_after))
        self.assertEqual(set(block_before), set(block_after))
        self.assertEqual(layout_after, list(reversed(layout_before)))
        self.assertEqual(block_after, list(reversed(block_before)))
        self.assertNotEqual(
            before.inventory()["entity_order_manifest_digest"],
            after.inventory()["entity_order_manifest_digest"],
        )
        self.assertNotEqual(
            before.preservation_state(paired_right_panel_digest="0" * 64),
            after.preservation_state(paired_right_panel_digest="0" * 64),
        )

    def test_layout_block_headers_bind_base_point_and_supported_tags(self) -> None:
        """*Model_Space metadata is not omitted with layout entity content."""

        dxf = self.root / "layout-block-metadata.dxf"
        create_synthetic_dxf(dxf)
        before = snapshot_dxf(dxf)
        before_state = before.preservation_state(
            paired_right_panel_digest="0" * 64
        )

        document = ezdxf.readfile(dxf)
        header = document.blocks.get("*Model_Space").block
        header.dxf.base_point = (11, 12, 13)
        document.saveas(dxf)
        base_point_changed = snapshot_dxf(dxf)
        self.assertNotEqual(
            before.table_style_manifest_digest,
            base_point_changed.table_style_manifest_digest,
        )
        self.assertNotEqual(
            before_state,
            base_point_changed.preservation_state(
                paired_right_panel_digest="0" * 64
            ),
        )

        document = ezdxf.readfile(dxf)
        header = document.blocks.get("*Model_Space").block
        header.dxf.flags = int(header.dxf.flags) | 4
        document.saveas(dxf)
        header_tag_changed = snapshot_dxf(dxf)
        self.assertNotEqual(
            base_point_changed.table_style_manifest_digest,
            header_tag_changed.table_style_manifest_digest,
        )

        xref = self.root / "block-xref-metadata.dxf"
        create_synthetic_dxf(xref)
        document = ezdxf.readfile(xref)
        block = document.blocks.new(
            "SYNTHETIC_XREF",
            base_point=(1, 2, 3),
            dxfattribs={"flags": 4, "xref_path": "synthetic-a.dwg"},
        )
        document.saveas(xref)
        xref_before = snapshot_dxf(xref)
        document = ezdxf.readfile(xref)
        document.blocks.get(block.name).block.dxf.xref_path = "synthetic-b.dwg"
        document.saveas(xref)
        xref_after = snapshot_dxf(xref)
        self.assertNotEqual(
            xref_before.table_style_manifest_digest,
            xref_after.table_style_manifest_digest,
        )

        unsupported = self.root / "block-header-xdata.dxf"
        create_synthetic_dxf(unsupported)
        document = ezdxf.readfile(unsupported)
        document.appids.new("BLOCKAPP")
        document.blocks.get("*Model_Space").block.set_xdata(
            "BLOCKAPP", [(1000, "metadata")]
        )
        document.saveas(unsupported)
        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(unsupported)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_unsupported_dwg_and_dxf_versions_fail_closed(self) -> None:
        unsupported_source = self.root / "unsupported.dwg"
        unsupported_source.write_bytes(b"AC1027legacy")
        dxf = self.root / "supported.dxf"
        create_synthetic_dxf(dxf)
        with self.assertRaises(PipelineError) as raised:
            audit_dxf_for_testing(dxf, unsupported_source)
        self.assertEqual(raised.exception.code, ErrorCode.UNSUPPORTED_VERSION)

        legacy_dxf = self.root / "legacy.dxf"
        document = ezdxf.new("R2000")
        document.modelspace().add_line((0, 0), (1, 1))
        document.saveas(legacy_dxf)
        with self.assertRaises(PipelineError) as raised:
            snapshot_dxf(legacy_dxf)
        self.assertEqual(raised.exception.code, ErrorCode.UNSUPPORTED_VERSION)


if __name__ == "__main__":
    unittest.main()
