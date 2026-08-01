"""Native no-follow source binding races using generated drawing placeholders."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from liang_pingfa_review import oda as oda_module
from liang_pingfa_review import apply as apply_module
from liang_pingfa_review.apply import apply_dwg
from liang_pingfa_review.audit import audit_dwg
from liang_pingfa_review.errors import PipelineError
from liang_pingfa_review.plan import generate_edit_plan
from tests.support.synthetic_dxf import (
    FakeOdaConverter,
    create_synthetic_dxf,
)


class _RecordingConverter(FakeOdaConverter):
    """Record only generated placeholder bytes that entered private staging."""

    def __init__(self, initial_dxf: Path) -> None:
        super().__init__(initial_dxf)
        self.staged_inputs: list[bytes] = []

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        source = next(path for path in input_directory.iterdir() if path.is_file())
        self.staged_inputs.append(source.read_bytes())
        return super().convert(
            input_directory,
            output_directory,
            output_type,
            register_output=register_output,
        )


@unittest.skipUnless(os.name == "nt", "Windows source-handle semantics")
class SourceBindingNativeTests(unittest.TestCase):
    """A source A lease must never authorize conversion of pathname source B."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.normal = self.root / "normal-source"
        self.external = self.root / "external-source"
        self.moved = self.root / "moved-source"
        self.normal.mkdir()
        self.external.mkdir()
        self.source = self.normal / "source.dwg"
        self.source_a = b"AC1032generated-source-A"
        self.source_b = b"AC1032generated-source-B"
        self.source.write_bytes(self.source_a)
        (self.external / "source.dwg").write_bytes(self.source_b)
        self.fixture = self.root / "fixture.dxf"
        create_synthetic_dxf(self.fixture)

    def tearDown(self) -> None:
        self._restore_normal_source()
        self.temporary_directory.cleanup()

    def _create_junction(self, link: Path, target: Path) -> bool:
        return (
            subprocess.Popen(
                [
                    os.environ.get("ComSpec", "cmd.exe"),
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(link),
                    str(target),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).wait()
            == 0
        )

    def _restore_normal_source(self) -> None:
        is_junction = getattr(os.path, "isjunction", lambda _path: False)
        if os.path.lexists(self.normal) and (
            self.normal.is_symlink() or is_junction(self.normal)
        ):
            self.normal.rmdir()
        if self.moved.exists():
            self.moved.replace(self.normal)

    def test_audit_rejects_a_source_ancestor_junction_before_conversion(self) -> None:
        junction = self.root / "source-junction"
        if not self._create_junction(junction, self.external):
            self.skipTest("junction creation lacks permission")
        converter = _RecordingConverter(self.fixture)
        try:
            with self.assertRaises(PipelineError):
                audit_dwg(junction / "source.dwg", converter)
            self.assertEqual(converter.staged_inputs, [])
        finally:
            if os.path.lexists(junction):
                junction.rmdir()

    def test_source_unsupported_lexical_forms_fail_before_conversion(self) -> None:
        """UNC/device/ADS spellings never reach a source open or converter."""

        converter = _RecordingConverter(self.fixture)
        separator = chr(92)
        for source in (
            Path(
                separator * 2
                + "server"
                + separator
                + "share"
                + separator
                + "source.dwg"
            ),
            Path(
                separator * 2
                + "?"
                + separator
                + "C"
                + chr(58)
                + separator
                + "generated"
                + separator
                + "source.dwg"
            ),
            Path(str(self.source) + ":alternate-stream"),
        ):
            with self.subTest(source=str(source)):
                with self.assertRaises(PipelineError):
                    audit_dwg(source, converter)
        self.assertEqual(converter.staged_inputs, [])

    def test_source_file_symlink_is_rejected_before_conversion(self) -> None:
        """A direct reparse leaf cannot bypass the retained source chain."""

        link = self.root / "source-link.dwg"
        try:
            os.symlink(self.source, link)
        except OSError:
            self.skipTest("symbolic-link creation lacks permission")
        converter = _RecordingConverter(self.fixture)
        try:
            with self.assertRaises(PipelineError):
                audit_dwg(link, converter)
            self.assertEqual(converter.staged_inputs, [])
        finally:
            if os.path.lexists(link):
                link.unlink()

    def _swap_parent_to_junction(self) -> OSError | None:
        """Try an ABA redirect only after the source chain/file lease exists."""

        try:
            self.normal.replace(self.moved)
            if not self._create_junction(self.normal, self.external):
                raise OSError("junction creation failed")
        except OSError as error:
            return error
        return None

    def test_audit_source_junction_swap_never_converts_replacement_bytes(self) -> None:
        converter = _RecordingConverter(self.fixture)
        original_copy = oda_module._copy_single_staged_input
        attempts: list[OSError | None] = []

        def copy_after_swap(*args: object, **kwargs: object) -> None:
            attempts.append(self._swap_parent_to_junction())
            original_copy(*args, **kwargs)  # type: ignore[arg-type]

        with mock.patch(
            "liang_pingfa_review.oda._copy_single_staged_input",
            side_effect=copy_after_swap,
        ):
            try:
                audit_dwg(self.source, converter)
            except PipelineError:
                self.assertEqual(converter.staged_inputs, [])
            else:
                self.assertEqual(
                    converter.staged_inputs,
                    [self.source_a, self.source_a],
                )
        self.assertIn(len(attempts), {1, 2})
        self.assertNotIn(self.source_b, converter.staged_inputs)

    def test_apply_source_junction_swap_never_converts_replacement_bytes(self) -> None:
        initial_converter = _RecordingConverter(self.fixture)
        audit = audit_dwg(self.source, initial_converter)
        plan = generate_edit_plan(audit)
        converter = _RecordingConverter(self.fixture)
        output = self.root / "corrected.dwg"
        original_copy = oda_module._copy_single_staged_input
        attempts: list[OSError | None] = []

        def copy_after_swap(*args: object, **kwargs: object) -> None:
            # The first phase-two staging copy is the held public source;
            # later copies are workspace-owned DXF/DWG intermediates.
            if not attempts:
                attempts.append(self._swap_parent_to_junction())
            original_copy(*args, **kwargs)  # type: ignore[arg-type]

        with mock.patch(
            "liang_pingfa_review.oda._copy_single_staged_input",
            side_effect=copy_after_swap,
        ):
            try:
                apply_dwg(
                    self.source,
                    audit,
                    plan,
                    plan["plan_id"],
                    output,
                    converter,
                )
            except PipelineError:
                self.assertFalse(output.exists())
                self.assertEqual(converter.staged_inputs, [])
            else:
                self.assertGreaterEqual(len(converter.staged_inputs), 1)
                self.assertEqual(converter.staged_inputs[0], self.source_a)
        self.assertEqual(len(attempts), 1)
        self.assertNotIn(self.source_b, converter.staged_inputs)

    def test_apply_retains_source_file_through_publication(self) -> None:
        """The source read lease stays live until no-replace publication finishes."""

        audit = audit_dwg(self.source, _RecordingConverter(self.fixture))
        plan = generate_edit_plan(audit)
        output = self.root / "published.dwg"
        attempts: list[OSError | None] = []
        original_publish = apply_module.publish_no_replace

        def publish_while_source_is_leased(*args: object, **kwargs: object) -> object:
            def attempt_write() -> None:
                try:
                    self.source.write_bytes(self.source_b)
                except OSError as error:
                    attempts.append(error)
                else:
                    attempts.append(None)

            kwargs["before_commit"] = attempt_write
            return original_publish(*args, **kwargs)

        with mock.patch(
            "liang_pingfa_review.apply.publish_no_replace",
            side_effect=publish_while_source_is_leased,
        ):
            apply_dwg(
                self.source,
                audit,
                plan,
                plan["plan_id"],
                output,
                _RecordingConverter(self.fixture),
            )
        self.assertTrue(output.exists())
        self.assertEqual(len(attempts), 1)
        self.assertIsInstance(attempts[0], OSError)
        self.assertEqual(self.source.read_bytes(), self.source_a)


if __name__ == "__main__":
    unittest.main()
