"""Phase-two fail-closed mutation, output, and re-audit tests."""

from __future__ import annotations

import copy
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
import importlib
import inspect
import os
from pathlib import Path
import pkgutil
import shutil
import tempfile
import unittest
from unittest import mock

import ezdxf
from ezdxf.entities.dxfclass import DXFClass
from ezdxf.sections.acdsdata import new_acds_data_section

import liang_pingfa_review
from liang_pingfa_review import atomic_output
from liang_pingfa_review import apply as apply_module
from liang_pingfa_review import verify as verify_module
from liang_pingfa_review.apply import (
    _source_read_lease,
    _validate_targets_before_mutation,
    apply_dwg,
)
from liang_pingfa_review.audit import audit_dwg
from liang_pingfa_review.canonical import (
    attach_integrity,
    close_created_file,
    utc_now,
)
from liang_pingfa_review.contracts import validate_artifact
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.ownership import (
    OwnershipCleanupError,
    WindowsFileOwnershipBackend,
    WindowsOwnedPath,
)
from liang_pingfa_review.plan import generate_edit_plan
from liang_pingfa_review.reports import render_plan_review
from liang_pingfa_review.snapshots import snapshot_dxf
from liang_pingfa_review.temporary import PrivateWorkspace
from liang_pingfa_review.verify import (
    assert_postconditions,
    verification_artifact_matches_output,
    verify_dwg,
)
from tests.support.synthetic_dxf import (
    FakeOdaConverter,
    build_synthetic_audit as audit_dxf_for_testing,
    create_fake_dwg,
    create_synthetic_dxf,
    delete_audited_text_in_synthetic_dxf,
    save_document_to_existing_default_stream,
)


class CorruptRoundtripConverter(FakeOdaConverter):
    """A fake converter that proves final re-audit rejects changed output."""

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        destination = super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )
        source = next(path for path in input_directory.iterdir() if path.is_file())
        if output_type == "DXF" and source.read_bytes().startswith(
            b"AC1032" + self._MARKER
        ):
            document = ezdxf.readfile(destination)
            document.modelspace().add_line((2, 2), (3, 3))
            save_document_to_existing_default_stream(document, destination)
        return destination


class HeaderChangingRoundtripConverter(FakeOdaConverter):
    """A fake converter that changes a raw-bound HEADER representation value."""

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        destination = super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )
        source = next(path for path in input_directory.iterdir() if path.is_file())
        if output_type == "DXF" and source.read_bytes().startswith(
            b"AC1032" + self._MARKER
        ):
            document = ezdxf.readfile(destination)
            document.header["$LTSCALE"] = float(document.header["$LTSCALE"]) + 1.0
            save_document_to_existing_default_stream(document, destination)
        return destination


class GroupXdataChangingRoundtripConverter(FakeOdaConverter):
    """A fake converter that changes global GROUP metadata after mutation."""

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        destination = super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )
        source = next(path for path in input_directory.iterdir() if path.is_file())
        if output_type == "DXF" and source.read_bytes().startswith(
            b"AC1032" + self._MARKER
        ):
            document = ezdxf.readfile(destination)
            group = document.groups.get("SYNTHETIC_GROUP")
            group.set_xdata("SYNTHETIC_APP", [(1000, "changed-metadata")])
            save_document_to_existing_default_stream(document, destination)
        return destination


class TableChangingRoundtripConverter(FakeOdaConverter):
    """A fake converter that changes protected table state after mutation."""

    def __init__(self, initial_dxf: Path, change: object) -> None:
        super().__init__(initial_dxf)
        self.change = change

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        destination = super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )
        source = next(path for path in input_directory.iterdir() if path.is_file())
        if output_type == "DXF" and source.read_bytes().startswith(
            b"AC1032" + self._MARKER
        ):
            document = ezdxf.readfile(destination)
            self.change(document)  # type: ignore[operator]
            save_document_to_existing_default_stream(document, destination)
        return destination


class ClassesChangingRoundtripConverter(FakeOdaConverter):
    """A fake converter that changes a registered CLASS field after mutation."""

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        destination = super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )
        source = next(path for path in input_directory.iterdir() if path.is_file())
        if output_type == "DXF" and source.read_bytes().startswith(
            b"AC1032" + self._MARKER
        ):
            document = ezdxf.readfile(destination)
            dxf_class = document.classes.get("SYNTHETIC_CLASS")
            dxf_class.dxf.flags = int(dxf_class.dxf.flags) + 1
            save_document_to_existing_default_stream(document, destination)
        return destination


class LayoutBlockHeaderChangingRoundtripConverter(FakeOdaConverter):
    """A fake converter that changes layout-backed BLOCK header metadata."""

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        destination = super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )
        source = next(path for path in input_directory.iterdir() if path.is_file())
        if output_type == "DXF" and source.read_bytes().startswith(
            b"AC1032" + self._MARKER
        ):
            document = ezdxf.readfile(destination)
            header = document.blocks.get("*Model_Space").block
            header.dxf.base_point = (11, 12, 13)
            save_document_to_existing_default_stream(document, destination)
        return destination


class EntityOrderChangingRoundtripConverter(FakeOdaConverter):
    """A fake converter that swaps preserved modelspace draw-order entries."""

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        destination = super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )
        source = next(path for path in input_directory.iterdir() if path.is_file())
        if output_type == "DXF" and source.read_bytes().startswith(
            b"AC1032" + self._MARKER
        ):
            document = ezdxf.readfile(destination)
            modelspace = document.modelspace()
            entities = [
                next(
                    entity
                    for entity in modelspace
                    if entity.dxftype() == entity_type
                )
                for entity_type in ("LINE", "TEXT", "HATCH")
            ]
            for entity in entities:
                modelspace.unlink_entity(entity)
            for entity in reversed(entities):
                modelspace.add_entity(entity)
            save_document_to_existing_default_stream(document, destination)
        return destination


class OutputChangingVerifyConverter(FakeOdaConverter):
    """Replace the public output only after its private DXF copy is staged."""

    def __init__(self, initial_dxf: Path, output_to_replace: Path) -> None:
        super().__init__(initial_dxf)
        self.output_to_replace = output_to_replace
        self.write_error: OSError | None = None

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        destination = super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )
        source = next(path for path in input_directory.iterdir() if path.is_file())
        if output_type == "DXF" and source.read_bytes().startswith(
            b"AC1032" + self._MARKER
        ):
            try:
                self.output_to_replace.write_bytes(b"AC1032replaced-after-staging")
            except OSError as error:
                self.write_error = error
        return destination


class ProductionBoundaryTests(unittest.TestCase):
    """Production entry points expose no synthetic mutation escape hatch."""

    def test_apply_and_verify_signatures_expose_only_production_inputs(self) -> None:
        self.assertEqual(
            set(inspect.signature(apply_dwg).parameters),
            {
                "source_path",
                "audit",
                "plan",
                "confirm_plan",
                "output_path",
                "converter",
                "dry_run",
            },
        )
        self.assertEqual(
            set(inspect.signature(verify_dwg).parameters),
            {
                "output_path",
                "audit",
                "plan",
                "converter",
                "verification_output_path",
            },
        )
        self.assertFalse(
            hasattr(apply_module, "delete_audited_text_in_dxf_for_testing")
        )
        self.assertFalse(
            any(
                module.name.casefold().startswith("test")
                for module in pkgutil.iter_modules(liang_pingfa_review.__path__)
            )
        )

    def test_non_windows_production_calls_fail_closed_without_an_override(self) -> None:
        with mock.patch("liang_pingfa_review.apply.os.name", "posix"):
            with self.assertRaises(PipelineError) as raised:
                apply_dwg(
                    Path("synthetic-source.dwg"),
                    {},
                    {},
                    "synthetic-plan",
                    Path("synthetic-output.dwg"),
                    object(),  # type: ignore[arg-type]
                )
        self.assertEqual(raised.exception.code, ErrorCode.WINDOWS_PLATFORM_REQUIRED)

        with mock.patch("liang_pingfa_review.verify.os.name", "posix"):
            with self.assertRaises(PipelineError) as raised:
                verify_dwg(
                    Path("synthetic-output.dwg"),
                    {},
                    {},
                    object(),  # type: ignore[arg-type]
                )
        self.assertEqual(raised.exception.code, ErrorCode.WINDOWS_PLATFORM_REQUIRED)

    def test_unsupported_backend_and_callback_kwargs_cannot_bypass_production(self) -> None:
        arguments = (
            Path("synthetic-source.dwg"),
            {},
            {},
            "synthetic-plan",
            Path("synthetic-output.dwg"),
            object(),
        )
        with self.assertRaises(TypeError):
            apply_dwg(*arguments, ownership_backend=object())  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            apply_dwg(*arguments, before_commit=lambda: None)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            verify_dwg(
                Path("synthetic-output.dwg"),
                {},
                {},
                object(),  # type: ignore[arg-type]
                ownership_backend=object(),  # type: ignore[call-arg]
            )
        with self.assertRaises(TypeError):
            verify_dwg(
                Path("synthetic-output.dwg"),
                {},
                {},
                object(),  # type: ignore[arg-type]
                before_artifact=lambda: None,  # type: ignore[call-arg]
            )

    def test_installed_package_has_no_test_audit_or_portable_backend_surface(self) -> None:
        """Synthetic DXF paths and local backend doubles belong only in tests."""

        audit_module = importlib.import_module("liang_pingfa_review.audit")
        apply_names = set(vars(apply_module))
        self.assertFalse(
            any("for_testing" in name.casefold() for name in vars(audit_module))
        )
        self.assertFalse(
            any("dxf" in name.casefold() for name in vars(audit_module) if name.startswith("audit_"))
        )
        self.assertFalse(
            any("for_testing" in name.casefold() for name in apply_names)
        )
        self.assertFalse(
            any("delete" in name.casefold() and "dxf" in name.casefold() for name in apply_names)
        )

        for module_info in pkgutil.walk_packages(
            liang_pingfa_review.__path__,
            prefix="liang_pingfa_review.",
        ):
            module = importlib.import_module(module_info.name)
            exported_names = set(vars(module))
            self.assertFalse(
                any("for_testing" in name.casefold() for name in exported_names),
                module_info.name,
            )
            self.assertFalse(
                any("portable" in name.casefold() for name in exported_names),
                module_info.name,
            )
            module_path = getattr(module, "__file__", None)
            if module_path is not None and module_path.endswith(".py"):
                text = Path(module_path).read_text(encoding="utf-8")
                self.assertNotIn("for_testing", text.casefold(), module_info.name)
                self.assertNotIn("portable", text.casefold(), module_info.name)


class PureAuditPlanStateTests(unittest.TestCase):
    """Exercise audit-bound state verification without a public DWG mutation."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.dxf = self.root / "synthetic-before.dxf"
        self.source = self.root / "synthetic-source.dwg"
        create_synthetic_dxf(self.dxf)
        create_fake_dwg(self.source)
        self.audit = audit_dxf_for_testing(self.dxf, self.source)
        self.plan = generate_edit_plan(self.audit)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_validated_synthetic_state_transition_matches_audit_plan(self) -> None:
        after = self.root / "synthetic-after.dxf"
        delete_audited_text_in_synthetic_dxf(
            self.dxf,
            after,
            self.audit,
            self.plan,
        )
        actual = assert_postconditions(
            snapshot_dxf(self.dxf),
            snapshot_dxf(after),
            self.audit,
            self.plan,
        )
        self.assertEqual(actual, self.plan["expected_after"])
        self.assertEqual(
            self.audit["inventory"]["raw_header_manifest_digest"],
            self.plan["expected_after"]["raw_header_manifest_digest"],
        )

    def test_raw_header_state_mismatch_rejects_postconditions(self) -> None:
        """Raw HEADER preservation is part of the audit-derived post-state."""

        after = self.root / "synthetic-after-raw-header.dxf"
        delete_audited_text_in_synthetic_dxf(
            self.dxf,
            after,
            self.audit,
            self.plan,
        )
        changed_raw_header = replace(
            snapshot_dxf(after),
            raw_header_manifest_digest="0" * 64,
        )
        with self.assertRaises(PipelineError) as raised:
            assert_postconditions(
                snapshot_dxf(self.dxf),
                changed_raw_header,
                self.audit,
                self.plan,
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)


@unittest.skipUnless(
    os.name == "nt",
    "Public DWG apply/verify requires retained Windows handles",
)
class ApplyAndVerifyTests(unittest.TestCase):
    """Cover success and every intended phase-two failure gate."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.dxf = self.root / "fixture.dxf"
        self.source = self.root / "source.dwg"
        create_synthetic_dxf(self.dxf)
        create_fake_dwg(self.source)
        self.converter = FakeOdaConverter(self.dxf)
        self.audit = audit_dwg(self.source, self.converter)
        self.plan = generate_edit_plan(self.audit)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def add_supported_group(self) -> None:
        """Add normal global OBJECTS content without using source-derived data."""

        document = ezdxf.readfile(self.dxf)
        document.appids.new("SYNTHETIC_APP")
        group = document.groups.new("SYNTHETIC_GROUP")
        line = next(
            entity for entity in document.modelspace() if entity.dxftype() == "LINE"
        )
        group.set_data([line])
        document.saveas(self.dxf)

    def add_registered_class(self) -> None:
        """Add a complete synthetic CLASS record to the runtime-only fixture."""

        document = ezdxf.readfile(self.dxf)
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
        document.saveas(self.dxf)

    def add_acdsdata_payload(self) -> None:
        """Add an ACDSDATA binary record to the runtime-only fixture."""

        document = ezdxf.readfile(self.dxf)
        document.acdsdata = new_acds_data_section(document)
        document.acdsdata.new_acis_data(
            "SYNTHETIC_ACDS_HANDLE", b"\x00synthetic-acdsdata-byte\xff"
        )
        document.saveas(self.dxf)

    def test_full_audit_plan_apply_verify_flow(self) -> None:
        output = self.root / "corrected.dwg"
        original_bytes = self.source.read_bytes()
        verification = apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            output,
            self.converter,
        )
        self.assertTrue(output.is_file())
        self.assertTrue(verification["passed"])
        self.assertEqual(self.source.read_bytes(), original_bytes)
        output_verification = verify_dwg(output, self.audit, self.plan, self.converter)
        self.assertTrue(output_verification["passed"])
        self.assertEqual(
            {
                key: value
                for key, value in verification["output_binding"].items()
                if key != "verified_at"
            },
            {
                key: value
                for key, value in output_verification["output_binding"].items()
                if key != "verified_at"
            },
        )
        self.assertTrue(
            verification_artifact_matches_output(output_verification, output)
        )

    def test_output_binding_cannot_certify_a_replaced_dwg(self) -> None:
        """Verification evidence must be recomputed for the current DWG bytes."""

        output = self.root / "binding-output.dwg"
        verification = apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            output,
            self.converter,
        )
        self.assertTrue(verification_artifact_matches_output(verification, output))
        replacement = self.root / "replacement.dwg"
        replacement.write_bytes(b"AC1032unrelated-replacement")
        replacement.replace(output)
        self.assertFalse(verification_artifact_matches_output(verification, output))

    def test_verification_output_binding_is_required_and_integrity_bound(self) -> None:
        """A passed artifact cannot omit or silently alter its output evidence."""

        output = self.root / "strict-binding-output.dwg"
        verification = apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            output,
            self.converter,
        )
        missing = copy.deepcopy(verification)
        missing.pop("output_binding")
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("verification", attach_integrity(missing))
        self.assertEqual(
            raised.exception.code,
            ErrorCode.VERIFICATION_SCHEMA_INVALID,
        )
        altered = copy.deepcopy(verification)
        altered["output_binding"]["sha256"] = "0" * 64
        with self.assertRaises(PipelineError) as raised:
            validate_artifact("verification", altered)
        self.assertEqual(
            raised.exception.code,
            ErrorCode.VERIFICATION_SCHEMA_INVALID,
        )

    def test_verification_artifact_creation_never_replaces_existing_file(self) -> None:
        output = self.root / "artifact-no-replace-output.dwg"
        apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            output,
            self.converter,
        )
        artifact = self.root / "existing-verification.json"
        existing = b"other-writer-artifact"
        artifact.write_bytes(existing)
        with self.assertRaises(PipelineError) as raised:
            verify_dwg(
                output,
                self.audit,
                self.plan,
                self.converter,
                verification_output_path=artifact,
            )
            self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_EXISTS)
            self.assertEqual(artifact.read_bytes(), existing)

    def test_windows_source_read_lease_denies_write_access(self) -> None:
        """The production lease is mandatory and rejects a concurrent writer."""

        if os.name != "nt":
            self.skipTest("Windows no-write source handle is platform-specific")
        original = self.source.read_bytes()
        with _source_read_lease(self.source):
            with self.assertRaises(OSError):
                self.source.write_bytes(b"AC1032concurrent-write")
        self.assertEqual(self.source.read_bytes(), original)

    def test_windows_verify_output_lease_denies_write_access(self) -> None:
        """Verification uses the same enforced no-write primitive for output."""

        if os.name != "nt":
            self.skipTest("Windows no-write output handle is platform-specific")
        output = self.root / "lease-protected-output.dwg"
        output.write_bytes(b"AC1032synthetic-output")
        original = output.read_bytes()
        with verify_module._output_read_lease(output):
            with self.assertRaises(OSError):
                output.write_bytes(b"AC1032concurrent-output-write")
        self.assertEqual(output.read_bytes(), original)

    def test_source_lease_acquisition_fails_closed(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows no-write source handle is platform-specific")
        with mock.patch(
            "liang_pingfa_review.apply.acquire_source_lease",
            side_effect=PipelineError(
                ErrorCode.SOURCE_LEASE_UNAVAILABLE, "synthetic lease failure"
            ),
        ):
            with self.assertRaises(PipelineError) as raised:
                apply_dwg(
                    self.source,
                    self.audit,
                    self.plan,
                    self.plan["plan_id"],
                    self.root / "lease-failure.dwg",
                    self.converter,
                )
        self.assertEqual(raised.exception.code, ErrorCode.SOURCE_LEASE_UNAVAILABLE)

    def test_dry_run_publishes_nothing(self) -> None:
        output = self.root / "dry-run.dwg"
        verification = apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            output,
            self.converter,
            dry_run=True,
        )
        self.assertTrue(verification["passed"])
        self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "nt", "Windows staged-DWG lease behavior")
    def test_staged_dwg_aba_attempts_before_during_and_after_roundtrip_fail_closed(
        self,
    ) -> None:
        """The one retained staged lease covers every round-trip race boundary."""

        original_roundtrip = apply_module.staged_dwg_to_dxf

        for phase in ("before", "during", "after"):
            with self.subTest(phase=phase):
                output = self.root / f"aba-{phase}.dwg"
                replacement = self.root / f"replacement-{phase}.dwg"
                replacement.write_bytes(b"AC1032unverified-staged-replacement")
                attempts: list[OSError | None] = []

                def attempt_swap(staged: Path) -> None:
                    try:
                        replacement.replace(staged)
                    except OSError as error:
                        attempts.append(error)
                    else:
                        attempts.append(None)

                class RoundtripSwapConverter(FakeOdaConverter):
                    def __init__(self, initial_dxf: Path) -> None:
                        super().__init__(initial_dxf)
                        self.staged_path: Path | None = None

                    def convert(
                        self,
                        input_directory: Path,
                        output_directory: Path,
                        output_type: str,
                        *,
                        register_output: Callable[[Path], Path],
                    ) -> Path:
                        destination = super().convert(
                            input_directory,
                            output_directory,
                            output_type,
                            register_output=register_output,
                        )
                        source = next(
                            path
                            for path in input_directory.iterdir()
                            if path.is_file()
                        )
                        if (
                            phase == "during"
                            and output_type == "DXF"
                            and source.read_bytes().startswith(
                                b"AC1032" + self._MARKER
                            )
                            and self.staged_path is not None
                        ):
                            attempt_swap(self.staged_path)
                        return destination

                converter = RoundtripSwapConverter(self.dxf)

                def roundtrip_with_swap(
                    source: Path,
                    workspace: PrivateWorkspace,
                    converter_argument: object,
                    *,
                    stage_name: str = "dwg-to-dxf",
                ) -> Path:
                    if stage_name != "roundtrip-output":
                        return original_roundtrip(
                            source,
                            workspace,
                            converter_argument,  # type: ignore[arg-type]
                            stage_name=stage_name,
                        )
                    converter.staged_path = source
                    if phase == "before":
                        attempt_swap(source)
                    result = original_roundtrip(
                        source,
                        workspace,
                        converter_argument,  # type: ignore[arg-type]
                        stage_name=stage_name,
                    )
                    if phase == "after":
                        attempt_swap(source)
                    return result

                with mock.patch(
                    "liang_pingfa_review.apply.staged_dwg_to_dxf",
                    side_effect=roundtrip_with_swap,
                ):
                    try:
                        apply_dwg(
                            self.source,
                            self.audit,
                            self.plan,
                            self.plan["plan_id"],
                            output,
                            converter,
                        )
                    except PipelineError:
                        self.assertFalse(output.exists())
                    else:
                        self.assertTrue(attempts)
                        self.assertTrue(all(error is not None for error in attempts))
                        self.assertTrue(output.exists())

    def test_expired_audit_and_changed_source_fail_before_mutation(self) -> None:
        expired_created_at = utc_now() - timedelta(hours=25)
        expired_audit = audit_dwg(
            self.source,
            self.converter,
            now=expired_created_at,
        )
        expired_plan = generate_edit_plan(
            expired_audit,
            now=expired_created_at + timedelta(seconds=1),
        )
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                expired_audit,
                expired_plan,
                expired_plan["plan_id"],
                self.root / "expired.dwg",
                self.converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.STALE_AUDIT)

        self.source.write_bytes(b"AC1032changed-source")
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                self.audit,
                self.plan,
                self.plan["plan_id"],
                self.root / "changed.dwg",
                self.converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.STALE_AUDIT)

    def test_changed_missing_and_unsafe_targets_fail_closed(self) -> None:
        changed = self.root / "changed-entity.dxf"
        create_synthetic_dxf(changed)
        document = ezdxf.readfile(changed)
        target_handle = self.plan["operations"][0]["target"]["handle"]
        document.entitydb[target_handle].dxf.text = "different"
        document.saveas(changed)
        with self.assertRaises(PipelineError) as raised:
            _validate_targets_before_mutation(snapshot_dxf(changed), self.plan)
        self.assertEqual(raised.exception.code, ErrorCode.CHANGED_ENTITY)

        missing = self.root / "missing-entity.dxf"
        document = ezdxf.readfile(self.dxf)
        missing_entity = document.entitydb[target_handle]
        document.modelspace().delete_entity(missing_entity)
        document.saveas(missing)
        with self.assertRaises(PipelineError) as raised:
            delete_audited_text_in_synthetic_dxf(
                missing,
                self.root / "unused.dxf",
                self.audit,
                self.plan,
            )
        self.assertEqual(raised.exception.code, ErrorCode.MISSING_HANDLE)

        unsafe = copy.deepcopy(self.plan)
        line_handle = next(
            entity.dxf.handle
            for entity in ezdxf.readfile(self.dxf).modelspace()
            if entity.dxftype() == "LINE"
        )
        unsafe["operations"][0]["target"]["handle"] = line_handle
        with self.assertRaises(PipelineError) as raised:
            _validate_targets_before_mutation(snapshot_dxf(self.dxf), unsafe)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)

    def test_live_target_substitution_after_snapshot_is_not_deleted(self) -> None:
        """Deletion must re-fingerprint the same live entity after preflight."""

        before_snapshot = snapshot_dxf(self.dxf)
        _validate_targets_before_mutation(before_snapshot, self.plan)
        changed = self.root / "substituted-live-target.dxf"
        target_handle = self.plan["operations"][0]["target"]["handle"]
        document = ezdxf.readfile(self.dxf)
        # Keep the planned handle but replace its DXF content after the initial
        # snapshot. The old reopen-and-delete path would remove this entity.
        document.entitydb[target_handle].dxf.text = "substituted-synthetic-content"
        document.saveas(changed)

        destination = self.root / "must-not-exist.dxf"
        with self.assertRaises(PipelineError) as raised:
            delete_audited_text_in_synthetic_dxf(
                changed,
                destination,
                self.audit,
                self.plan,
            )
        self.assertEqual(raised.exception.code, ErrorCode.CHANGED_ENTITY)
        self.assertFalse(destination.exists())
        reloaded = ezdxf.readfile(changed)
        self.assertIsNotNone(reloaded.entitydb.get(target_handle))

    def test_output_exists_and_publication_race_never_overwrite(self) -> None:
        existing = self.root / "existing.dwg"
        existing.write_bytes(b"existing")
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                self.audit,
                self.plan,
                self.plan["plan_id"],
                existing,
                self.converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_EXISTS)
        self.assertEqual(existing.read_bytes(), b"existing")

        raced = self.root / "raced.dwg"
        original_publish = apply_module.publish_no_replace

        def publish_with_destination_race(*args: object, **kwargs: object) -> object:
            kwargs["before_commit"] = lambda: raced.write_bytes(b"raced")
            return original_publish(*args, **kwargs)

        with (
            mock.patch(
                "liang_pingfa_review.apply.publish_no_replace",
                side_effect=publish_with_destination_race,
            ),
            self.assertRaises(PipelineError) as raised,
        ):
            apply_dwg(
                self.source,
                self.audit,
                self.plan,
                self.plan["plan_id"],
                raced,
                self.converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_EXISTS)
        self.assertEqual(raced.read_bytes(), b"raced")

    def test_publication_copy_failures_clean_private_temporary_files(self) -> None:
        """Copy and fsync failures leave no partially published private file."""

        if os.name != "nt":
            self.skipTest("Windows retained-handle copy behavior")
        staged = self.root / "staged.dwg"
        staged.write_bytes(b"verified-staged-content")
        original = staged.read_bytes()
        destination = self.root / "output.dwg"
        for failure in ("copy", "fsync"):
            with self.subTest(failure=failure):
                with mock.patch(
                    "liang_pingfa_review.atomic_output.uuid4",
                    return_value=mock.Mock(hex=("a" if failure == "copy" else "b") * 32),
                ):
                    if failure == "copy":
                        failure_context = mock.patch(
                            "liang_pingfa_review.ownership.WindowsOwnedPath.write_chunks",
                            side_effect=OSError("copy failed"),
                        )
                    else:
                        failure_context = mock.patch(
                            "liang_pingfa_review.ownership.os.fsync",
                            side_effect=OSError("fsync failed"),
                        )
                    with failure_context:
                        with self.assertRaises(PipelineError) as raised:
                            atomic_output._copy_for_publication(staged, destination)
                self.assertEqual(raised.exception.code, ErrorCode.ATOMIC_PUBLISH_FAILED)
                self.assertEqual(staged.read_bytes(), original)
                self.assertFalse(destination.exists())
                self.assertEqual(
                    list(self.root.glob(".liang-pingfa-publish-*.tmp")),
                    [],
                )

    def test_partial_owned_dxf_write_is_removed_before_conversion(self) -> None:
        """A serializer failure after its first byte leaves no owned DXF behind."""

        output = self.root / "partial-dxf-write.dwg"
        original_workspace = apply_module.PrivateWorkspace

        def workspace_in_test_root(*args: object, **kwargs: object) -> PrivateWorkspace:
            kwargs["directory"] = self.root
            return original_workspace(*args, **kwargs)  # type: ignore[arg-type]

        def write_one_chunk_then_fail(
            opened: WindowsOwnedPath,
            _writer: object,
        ) -> None:
            opened.write_bytes(b"  0\r\nSECTION\r\n")
            raise OSError("synthetic DXF write failure after first chunk")

        with (
            mock.patch(
                "liang_pingfa_review.apply.PrivateWorkspace",
                side_effect=workspace_in_test_root,
            ),
            mock.patch.object(
                WindowsOwnedPath,
                "write_text",
                side_effect=write_one_chunk_then_fail,
                autospec=True,
            ),
            self.assertRaises(PipelineError) as raised,
        ):
            apply_dwg(
                self.source,
                self.audit,
                self.plan,
                self.plan["plan_id"],
                output,
                self.converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.CONVERSION_FAILURE)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob("liang-pingfa-apply-*")), [])

    def test_publication_temporary_cleanup_uses_its_retained_handle(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows retained-handle cleanup behavior")
        temporary = self.root / f".liang-pingfa-publish-{'c' * 32}.tmp"
        backend = WindowsFileOwnershipBackend()
        opened = backend.create_new_file(temporary)
        opened.write_bytes(b"private-staging")
        binding = opened.capture_binding()
        atomic_output.recover_publication_temporary(
            temporary,
            self.root,
            binding=binding,
            backend=backend,
            opened=opened,
        )
        self.assertFalse(temporary.exists())
        self.assertEqual(list(self.root.glob(".liang-pingfa-publish-*.tmp")), [])

    def test_permanent_publication_cleanup_failure_is_observable(self) -> None:
        staged = self.root / "staged-cleanup-failure.dwg"
        staged.write_bytes(b"verified-staged-content")
        destination = self.root / "cleanup-failure-output.dwg"
        def source_changed() -> None:
            raise PipelineError(
                ErrorCode.SOURCE_CHANGED_DURING_RUN, "synthetic source race"
            )

        def fail_handle_disposal(*args: object, **kwargs: object) -> None:
            opened = args[0] if args else kwargs.get("opened")
            if opened is not None:
                opened.close()  # type: ignore[union-attr]
            raise OwnershipCleanupError("synthetic permanent sharing conflict")

        with mock.patch(
            "liang_pingfa_review.temporary.dispose_live_owned_path",
            side_effect=fail_handle_disposal,
        ):
            with self.assertRaises(PipelineError) as raised:
                atomic_output.publish_no_replace(
                    staged,
                    destination,
                    source_binding=source_changed,
                )
        self.assertEqual(raised.exception.code, ErrorCode.PUBLICATION_CLEANUP_FAILURE)
        self.assertIsInstance(raised.exception.__cause__, PipelineError)
        self.assertEqual(
            raised.exception.__cause__.code, ErrorCode.SOURCE_CHANGED_DURING_RUN
        )
        self.assertFalse(destination.exists())
        leftovers = list(self.root.glob(".liang-pingfa-publish-*.tmp"))
        self.assertEqual(len(leftovers), 1)
        leftovers[0].unlink()

    def test_staging_workspace_cleanup_failure_cannot_report_success(self) -> None:
        workspace: PrivateWorkspace | None = None
        with mock.patch(
            "liang_pingfa_review.temporary.dispose_owned_binding",
            side_effect=OwnershipCleanupError("synthetic permanent sharing conflict"),
        ):
            with self.assertRaises(PipelineError) as raised:
                with PrivateWorkspace(prefix="liang-pingfa-test-cleanup-") as workspace:
                    private = workspace / "private.txt"
                    private.write_text("private", encoding="utf-8")
                    workspace.track_created_file(private)
        self.assertEqual(raised.exception.code, ErrorCode.PUBLICATION_CLEANUP_FAILURE)
        assert workspace is not None
        shutil.rmtree(workspace.path)

    def test_non_windows_publication_requires_explicit_synthetic_backend(self) -> None:
        """No production fallback may silently use weaker POSIX path semantics."""

        staged = self.root / "staged-posix.dwg"
        staged.write_bytes(b"verified-staged-content")
        destination = self.root / "published-posix.dwg"
        with mock.patch("liang_pingfa_review.atomic_output.os.name", "posix"):
            with self.assertRaises(PipelineError) as raised:
                atomic_output.publish_no_replace(staged, destination)
        self.assertEqual(raised.exception.code, ErrorCode.WINDOWS_PLATFORM_REQUIRED)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".liang-pingfa-publish-*.tmp")), [])

    def test_windows_publication_removes_private_name_after_success(self) -> None:
        """The Windows handle rename retains only its intended final output."""

        if os.name != "nt":
            self.skipTest("Windows handle publication is platform-specific")
        staged = self.root / "staged-windows.dwg"
        staged.write_bytes(b"verified-staged-content")
        destination = self.root / "published-windows.dwg"

        atomic_output.publish_no_replace(staged, destination)
        self.assertEqual(destination.read_bytes(), staged.read_bytes())
        self.assertEqual(list(self.root.glob(".liang-pingfa-publish-*.tmp")), [])

    def test_source_binding_callback_runs_after_fsync_and_before_commit(self) -> None:
        staged = self.root / "staged-binding.dwg"
        staged.write_bytes(b"verified-staged-content")
        destination = self.root / "binding-output.dwg"
        events: list[str] = []
        original_fsync = os.fsync

        def record_fsync(file_descriptor: int) -> None:
            events.append("fsync")
            original_fsync(file_descriptor)

        with (
            mock.patch(
                "liang_pingfa_review.ownership.os.fsync", side_effect=record_fsync
            ),
        ):
            atomic_output.publish_no_replace(
                staged,
                destination,
                before_commit=lambda: events.append("before-commit-hook"),
                source_binding=lambda: events.append("source-binding"),
                after_commit=lambda _opened, _binding: events.append("commit"),
            )
        self.assertEqual(
            events,
            ["fsync", "before-commit-hook", "source-binding", "commit"],
        )

    def test_plan_review_has_operation_count_independent_structure(self) -> None:
        """Human review must not disclose private operation details or count."""

        one_operation = {
            "operations": [
                {
                    "operation_id": "synthetic-operation-one",
                    "target": {
                        "handle": "synthetic-handle-one",
                        "text": "synthetic-private-text",
                        "coordinates": [1, 2],
                        "source_metadata": "synthetic-source-metadata",
                    },
                }
            ]
        }
        multiple_operations = {
            "operations": one_operation["operations"]
            + [
                {
                    "operation_id": "synthetic-operation-two",
                    "target": {
                        "handle": "synthetic-handle-two",
                        "text": "another-synthetic-private-text",
                        "coordinates": [3, 4],
                        "source_metadata": "another-synthetic-source-metadata",
                    },
                }
            ]
        }
        one_review = render_plan_review(one_operation)
        multiple_review = render_plan_review(multiple_operations)
        self.assertEqual(one_review, multiple_review)
        self.assertEqual(one_review, render_plan_review())
        for private_value in (
            "synthetic-operation-one",
            "synthetic-operation-two",
            "synthetic-handle-one",
            "synthetic-handle-two",
            "synthetic-private-text",
            "another-synthetic-private-text",
            "synthetic-source-metadata",
            "another-synthetic-source-metadata",
        ):
            self.assertNotIn(private_value, one_review)

    def test_source_change_during_run_and_reaudit_mismatch_fail(self) -> None:
        output = self.root / "source-race.dwg"
        # A real write cannot pass the retained source handle. Replace only
        # the commit-bound recheck to prove publication rolls back when that
        # exact held source binding reports a change after copy/fsync staging.
        original_publish = apply_module.publish_no_replace

        def publish_with_source_race(*args: object, **kwargs: object) -> object:
            kwargs["source_binding"] = lambda: (_ for _ in ()).throw(
                PipelineError(
                    ErrorCode.SOURCE_CHANGED_DURING_RUN,
                    "synthetic held-source binding race",
                )
            )
            return original_publish(*args, **kwargs)

        with (
            mock.patch(
                "liang_pingfa_review.apply.publish_no_replace",
                side_effect=publish_with_source_race,
            ),
        ):
            with self.assertRaises(PipelineError) as raised:
                apply_dwg(
                    self.source,
                    self.audit,
                    self.plan,
                    self.plan["plan_id"],
                    output,
                    self.converter,
                )
        self.assertEqual(raised.exception.code, ErrorCode.SOURCE_CHANGED_DURING_RUN)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".liang-pingfa-publish-*.tmp")), [])

        create_fake_dwg(self.source)
        corrupt = CorruptRoundtripConverter(self.dxf)
        audit = audit_dwg(self.source, corrupt)
        plan = generate_edit_plan(audit)
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                audit,
                plan,
                plan["plan_id"],
                self.root / "corrupt.dwg",
                corrupt,
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)

    def test_forged_duplicate_plan_is_rejected(self) -> None:
        duplicate = copy.deepcopy(self.plan)
        duplicate["operations"].append(copy.deepcopy(duplicate["operations"][0]))
        duplicate = attach_integrity(duplicate)
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                self.audit,
                duplicate,
                duplicate["plan_id"],
                self.root / "duplicate.dwg",
                self.converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.DUPLICATE_TARGET)

    def test_raw_header_ltscale_roundtrip_change_rejects_apply_without_output(self) -> None:
        """A fake round-trip changing bound raw HEADER state cannot publish."""

        changing_converter = HeaderChangingRoundtripConverter(self.dxf)
        rejected_output = self.root / "header-changed.dwg"
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                self.audit,
                self.plan,
                self.plan["plan_id"],
                rejected_output,
                changing_converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
        self.assertFalse(rejected_output.exists())

    def test_ucs_view_and_vport_roundtrip_changes_reject_publication(self) -> None:
        """Protected named table records reject apply with no output."""

        cases = (
            (
                "ucs",
                lambda document: document.ucs.add(
                    "SYNTHETIC_UCS", dxfattribs={"origin": (1, 2, 3)}
                ),
                lambda document: setattr(
                    document.ucs.get("SYNTHETIC_UCS").dxf, "origin", (4, 5, 6)
                ),
            ),
            (
                "view",
                lambda document: document.views.add(
                    "SYNTHETIC_VIEW", dxfattribs={"height": 12.0}
                ),
                lambda document: setattr(
                    document.views.get("SYNTHETIC_VIEW").dxf, "height", 18.0
                ),
            ),
            (
                "vport",
                lambda document: document.viewports.add(
                    "SYNTHETIC_VPORT", dxfattribs={"center": (2, 3)}
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
                document = ezdxf.readfile(self.dxf)
                create(document)
                document.saveas(self.dxf)
                audit = audit_dwg(self.source, self.converter)
                plan = generate_edit_plan(audit)
                rejected_output = self.root / f"{name}-changed.dwg"
                with self.assertRaises(PipelineError) as raised:
                    apply_dwg(
                        self.source,
                        audit,
                        plan,
                        plan["plan_id"],
                        rejected_output,
                        TableChangingRoundtripConverter(self.dxf, change),
                    )
                self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
                self.assertFalse(rejected_output.exists())

    def test_ltscale_roundtrip_change_rejects_verify(self) -> None:
        """Verification independently binds representation-affecting HEADER state."""

        changing_converter = HeaderChangingRoundtripConverter(self.dxf)
        verified_output = self.root / "verified-before-header-change.dwg"
        apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            verified_output,
            self.converter,
        )
        with self.assertRaises(PipelineError) as raised:
            verify_dwg(
                verified_output,
                self.audit,
                self.plan,
                changing_converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)

    def test_group_xdata_is_preserved_and_roundtrip_mutation_is_rejected(self) -> None:
        """GROUP XDATA belongs to full OBJECTS preservation, not an ignored subset."""

        self.add_supported_group()
        before = snapshot_dxf(self.dxf)

        changed = self.root / "group-xdata-changed.dxf"
        document = ezdxf.readfile(self.dxf)
        group = document.groups.get("SYNTHETIC_GROUP")
        group.set_xdata("SYNTHETIC_APP", [(1000, "synthetic-group-xdata")])
        document.saveas(changed)
        after = snapshot_dxf(changed)
        self.assertNotEqual(before.objects_manifest_digest, after.objects_manifest_digest)
        self.assertNotEqual(
            before.preservation_state(paired_right_panel_digest="0" * 64),
            after.preservation_state(paired_right_panel_digest="0" * 64),
        )

        converter = GroupXdataChangingRoundtripConverter(self.dxf)
        audit = audit_dwg(self.source, converter)
        plan = generate_edit_plan(audit)
        rejected_output = self.root / "group-xdata-changed.dwg"
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                audit,
                plan,
                plan["plan_id"],
                rejected_output,
                converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
        self.assertFalse(rejected_output.exists())

    def test_classes_roundtrip_mutation_rejects_apply_and_verify(self) -> None:
        """CLASS changes invalidate the audit-bound expected-after state."""

        self.add_registered_class()
        audit = audit_dwg(self.source, self.converter)
        plan = generate_edit_plan(audit)
        self.assertIn("classes_manifest_digest", audit["inventory"])
        self.assertIn("classes_manifest_digest", plan["expected_after"])

        rejected_output = self.root / "classes-changed.dwg"
        changing_converter = ClassesChangingRoundtripConverter(self.dxf)
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                audit,
                plan,
                plan["plan_id"],
                rejected_output,
                changing_converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
        self.assertFalse(rejected_output.exists())
        self.assertEqual(list(self.root.glob(".liang-pingfa-publish-*.tmp")), [])

        verified_output = self.root / "classes-verified.dwg"
        apply_dwg(
            self.source,
            audit,
            plan,
            plan["plan_id"],
            verified_output,
            self.converter,
        )
        with self.assertRaises(PipelineError) as raised:
            verify_dwg(verified_output, audit, plan, changing_converter)
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)

    def test_layout_block_header_roundtrip_change_rejects_apply(self) -> None:
        """Layout BLOCK base-point metadata is a required preserved invariant."""

        rejected_output = self.root / "layout-block-header-changed.dwg"
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                self.audit,
                self.plan,
                self.plan["plan_id"],
                rejected_output,
                LayoutBlockHeaderChangingRoundtripConverter(self.dxf),
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
        self.assertFalse(rejected_output.exists())

    def test_entity_order_roundtrip_change_rejects_apply_and_verify(self) -> None:
        """Swapping supported records cannot be hidden by a sorted manifest."""

        document = ezdxf.readfile(self.dxf)
        hatch = document.modelspace().add_hatch(color=1)
        hatch.paths.add_polyline_path(
            [(320, 0), (330, 0), (330, 10), (320, 10)],
            is_closed=True,
        )
        document.saveas(self.dxf)
        audit = audit_dwg(self.source, self.converter)
        plan = generate_edit_plan(audit)
        rejected_output = self.root / "entity-order-changed.dwg"
        changing_converter = EntityOrderChangingRoundtripConverter(self.dxf)
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                audit,
                plan,
                plan["plan_id"],
                rejected_output,
                changing_converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
        self.assertFalse(rejected_output.exists())

        verified_output = self.root / "entity-order-verified.dwg"
        apply_dwg(
            self.source,
            audit,
            plan,
            plan["plan_id"],
            verified_output,
            self.converter,
        )
        with self.assertRaises(PipelineError) as raised:
            verify_dwg(verified_output, audit, plan, changing_converter)
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)

    def test_transparency_roundtrip_change_rejects_apply_and_verify(self) -> None:
        """A converter cannot make retained interference geometry transparent."""

        def make_interference_transparent(document: object) -> None:
            modelspace = document.modelspace()  # type: ignore[attr-defined]
            line = next(entity for entity in modelspace if entity.dxftype() == "LINE")
            line.transparency = 0.5

        changing_converter = TableChangingRoundtripConverter(
            self.dxf,
            make_interference_transparent,
        )
        rejected_output = self.root / "transparency-changed.dwg"
        with self.assertRaises(PipelineError) as raised:
            apply_dwg(
                self.source,
                self.audit,
                self.plan,
                self.plan["plan_id"],
                rejected_output,
                changing_converter,
            )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
        self.assertFalse(rejected_output.exists())

        verified_output = self.root / "transparency-verified.dwg"
        apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            verified_output,
            self.converter,
        )
        with self.assertRaises(PipelineError) as raised:
            verify_dwg(verified_output, self.audit, self.plan, changing_converter)
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)

    def test_fixed_width_class_spelling_is_narrow_serialization_volatility(self) -> None:
        """Whitespace-only CLASS integer spelling is not a drawing-state edit."""

        raw = self.dxf.read_bytes()
        start = raw.find(b"  0\r\nCLASS\r\n")
        self.assertGreaterEqual(start, 0)
        end = raw.find(b"  0\r\nCLASS\r\n", start + 1)
        self.assertGreater(end, start)
        record = raw[start:end]
        marker = b" 90\r\n0\r\n"
        self.assertIn(marker, record)
        self.dxf.write_bytes(
            raw[:start] + record.replace(marker, b" 90\r\n00\r\n", 1) + raw[end:]
        )
        audit = audit_dwg(self.source, self.converter)
        plan = generate_edit_plan(audit)
        output = self.root / "raw-class-fixed-width-volatility.dwg"
        result = apply_dwg(
            self.source,
            audit,
            plan,
            plan["plan_id"],
            output,
            self.converter,
        )
        self.assertTrue(result["passed"])
        self.assertTrue(output.exists())

    def test_verify_output_mutation_after_staging_has_no_passed_artifact(self) -> None:
        """The retained output source also blocks a mutation during staging."""

        output = self.root / "verified-output.dwg"
        apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            output,
            self.converter,
        )
        verification_artifact = self.root / "must-not-exist.json"
        changing_converter = OutputChangingVerifyConverter(self.dxf, output)
        # A real Windows lease prevents this write. Replacing only that OS
        # primitive exercises the documented non-Windows advisory final check.
        verification = verify_dwg(
            output,
            self.audit,
            self.plan,
            changing_converter,
            verification_output_path=verification_artifact,
        )
        self.assertTrue(verification["passed"])
        self.assertIsNotNone(changing_converter.write_error)
        self.assertTrue(verification_artifact.exists())
        self.assertEqual(list(self.root.glob(".liang-pingfa-publish-*.tmp")), [])

    def test_verify_normal_failure_removes_owned_artifact(self) -> None:
        """The final output binding failure removes only this call's artifact."""

        output = self.root / "artifact-race-output.dwg"
        apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            output,
            self.converter,
        )
        verification_artifact = self.root / "artifact-race.json"
        original_writer = verify_module.write_new_canonical_json

        def write_then_replace(
            path: Path,
            artifact: object,
            **kwargs: object,
        ) -> object:
            binding = original_writer(path, artifact, **kwargs)  # type: ignore[arg-type]
            output.write_bytes(b"AC1032replaced-after-artifact-write")
            return binding

        with (
            mock.patch(
                "liang_pingfa_review.verify._source_read_lease",
                return_value=nullcontext(),
            ),
            mock.patch(
                "liang_pingfa_review.verify.write_new_canonical_json",
                side_effect=write_then_replace,
            ),
        ):
            with self.assertRaises(PipelineError) as raised:
                verify_dwg(
                    output,
                    self.audit,
                    self.plan,
                    self.converter,
                    verification_output_path=verification_artifact,
                )
        self.assertEqual(
            raised.exception.code, ErrorCode.OUTPUT_CHANGED_DURING_VERIFY
        )
        self.assertFalse(verification_artifact.exists())

    def test_verify_never_removes_a_concurrently_replaced_artifact(self) -> None:
        """Ownership loss keeps a replacement and cannot report verification success."""

        output = self.root / "artifact-ownership-output.dwg"
        apply_dwg(
            self.source,
            self.audit,
            self.plan,
            self.plan["plan_id"],
            output,
            self.converter,
        )
        verification_artifact = self.root / "artifact-ownership.json"
        replacement = b"replacement-owned-by-another-writer"
        original_writer = verify_module.write_new_canonical_json

        def write_then_replace_artifact(
            path: Path,
            artifact: object,
            **kwargs: object,
        ) -> object:
            binding = original_writer(path, artifact, **kwargs)  # type: ignore[arg-type]
            close_created_file(binding)  # type: ignore[arg-type]
            path.unlink()
            path.write_bytes(replacement)
            return binding

        with (
            mock.patch(
                "liang_pingfa_review.verify._source_read_lease",
                return_value=nullcontext(),
            ),
            mock.patch(
                "liang_pingfa_review.verify.write_new_canonical_json",
                side_effect=write_then_replace_artifact,
            ),
        ):
            with self.assertRaises(PipelineError) as raised:
                verify_dwg(
                    output,
                    self.audit,
                    self.plan,
                    self.converter,
                    verification_output_path=verification_artifact,
                )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.VERIFICATION_ARTIFACT_OWNERSHIP_LOST,
        )
        self.assertEqual(verification_artifact.read_bytes(), replacement)

    def test_nonempty_acdsdata_cannot_enter_audit_or_apply(self) -> None:
        """The pipeline no longer claims partial ACDSDATA payload support."""

        self.add_acdsdata_payload()
        with self.assertRaises(PipelineError) as raised:
            audit_dwg(self.source, self.converter)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)
        self.assertEqual(list(self.root.glob("*.dwg")), [self.source])


if __name__ == "__main__":
    unittest.main()
