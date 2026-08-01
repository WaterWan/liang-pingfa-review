"""Bounded ODA staging, generated integration, and doctor contract tests."""

from __future__ import annotations

from types import SimpleNamespace
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from liang_pingfa_review import cli
from liang_pingfa_review.audit import audit_dwg
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.oda import OdaRunner, discover_oda, staged_dwg_to_dxf, staged_dxf_to_dwg
from liang_pingfa_review.ownership import PrivateStagingCapability
from liang_pingfa_review.plan import generate_edit_plan
from liang_pingfa_review.raw_dxf import MAX_DXF_BYTES
from liang_pingfa_review.temporary import PrivateWorkspace
from liang_pingfa_review.verify import verify_dwg
from liang_pingfa_review.apply import apply_dwg
from tests.support.owned_files import (
    TestOwnedPath,
    TestOwnershipBackend,
    install_non_windows_test_ownership,
)
from tests.support.synthetic_dxf import (
    FakeOdaConverter,
    create_fake_dwg,
    create_synthetic_dxf,
)


def _install_portable_backend(test_case: unittest.TestCase) -> None:
    """Inject the test-only backend on Ubuntu while preserving Windows probes."""

    install_non_windows_test_ownership(test_case)


class OdaDiscoveryAndPrivateRootsTests(unittest.TestCase):
    """Require random exact filters and two separate DACL-protected roots."""

    def setUp(self) -> None:
        _install_portable_backend(self)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.program_root = self.root / "programs"
        self.program_root.mkdir()
        self.executable = self.root / "ODAFileConverter.exe"
        self.executable.write_bytes(b"generated test placeholder")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _environment(self) -> dict[str, str]:
        return {
            "ProgramFiles": str(self.program_root),
            "ProgramFiles(x86)": str(self.program_root),
        }

    def test_discovery_missing_and_ambiguous_fail_closed(self) -> None:
        with self.assertRaises(PipelineError) as raised:
            discover_oda(environment=self._environment(), which=lambda _name: None)
        self.assertEqual(raised.exception.code, ErrorCode.ODA_NOT_FOUND)

        second = self.root / "ODAFileConverter"
        second.write_bytes(b"generated test placeholder")

        def finder(name: str) -> str | None:
            return str(self.executable if name.endswith(".exe") else second)

        with self.assertRaises(PipelineError) as raised:
            discover_oda(environment=self._environment(), which=finder)
        self.assertEqual(raised.exception.code, ErrorCode.ODA_DISCOVERY_AMBIGUOUS)

    def test_command_uses_random_leaf_empty_output_and_exact_filter(self) -> None:
        """ODA receives one random source leaf, recurse=0, audit=1."""

        runner = OdaRunner(self.executable, "27.1.0", timeout_seconds=7)
        source = self.root / "source.dwg"
        fixture = self.root / "fixture.dxf"
        source.write_bytes(b"AC1032generated-source")
        create_synthetic_dxf(fixture)
        command_leaves: list[str] = []

        def completed(
            command: list[str],
            **kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(command[3:7], ["ACAD2018", "DXF", "0", "1"])
            leaf = command[-1]
            self.assertRegex(leaf, r"\Asource-[0-9a-f]{32}[.]dwg\Z")
            self.assertEqual(list(Path(command[2]).iterdir()), [])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["timeout"], 7)
            command_leaves.append(leaf)
            shutil.copyfile(
                fixture,
                Path(command[2]) / f"{Path(leaf).stem}.dxf",
            )
            return subprocess.CompletedProcess(command, 0)

        with PrivateWorkspace(
            prefix="liang-pingfa-oda-command-",
            directory=self.root,
        ) as workspace:
            with mock.patch("liang_pingfa_review.oda.subprocess.run", side_effect=completed):
                output = staged_dwg_to_dxf(
                    source,
                    workspace,
                    runner,
                    stage_name="command",
                )
            self.assertEqual(output.suffix.casefold(), ".dxf")
            roots = sorted(workspace.path.glob("oda-*"))
            self.assertEqual(len(roots), 2)
            self.assertTrue(
                all(re.fullmatch(r"oda-[0-9a-f]{48}", root.name) for root in roots)
            )
        self.assertEqual(len(command_leaves), 2)
        self.assertNotEqual(*command_leaves)

    def test_dual_runs_use_independent_private_roots(self) -> None:
        source = self.root / "source.dwg"
        fixture = self.root / "fixture.dxf"
        create_fake_dwg(source)
        create_synthetic_dxf(fixture)
        converter = FakeOdaConverter(fixture)

        with PrivateWorkspace(
            prefix="liang-pingfa-oda-dual-",
            directory=self.root,
        ) as workspace:
            output = staged_dwg_to_dxf(
                source,
                workspace,
                converter,  # type: ignore[arg-type]
                stage_name="dual",
            )
            self.assertTrue(output.is_file())
            self.assertEqual(len(converter.output_directories), 2)
            self.assertNotEqual(
                converter.output_directories[0].parent,
                converter.output_directories[1].parent,
            )
            self.assertGreaterEqual(len(workspace._retained_files), 1)


class OdaInventoryAndFailureTests(unittest.TestCase):
    """Exercise accidental/stale/path/subprocess failure gates."""

    def setUp(self) -> None:
        _install_portable_backend(self)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.executable = self.root / "ODAFileConverter.exe"
        self.executable.write_bytes(b"generated test placeholder")
        self.runner = OdaRunner(self.executable, "27.1.0", timeout_seconds=3)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _direct_roots(self, name: str) -> tuple[Path, Path]:
        input_directory = self.root / f"{name}-input"
        output_directory = self.root / f"{name}-output"
        input_directory.mkdir()
        output_directory.mkdir()
        (input_directory / "source.dwg").write_bytes(b"AC1032generated-source")
        return input_directory, output_directory

    def _run_direct(
        self,
        name: str,
        side_effect: object,
    ) -> PipelineError:
        input_directory, output_directory = self._direct_roots(name)
        run_patch = (
            mock.patch(
                "liang_pingfa_review.oda.subprocess.run",
                return_value=side_effect,
            )
            if isinstance(side_effect, subprocess.CompletedProcess)
            else mock.patch(
                "liang_pingfa_review.oda.subprocess.run",
                side_effect=side_effect,
            )
        )
        with (run_patch, self.assertRaises(PipelineError) as raised):
            self.runner.convert(
                input_directory,
                output_directory,
                "DXF",
                register_output=lambda candidate: candidate,
            )
        return raised.exception

    def test_accidental_same_name_and_stale_entries_block_launch(self) -> None:
        """An output root must be empty before a subprocess can start."""

        for name, entries in (
            ("same-name", ("source.dxf",)),
            ("stale-leftover", ("old.dxf",)),
        ):
            with self.subTest(name=name):
                input_directory, output_directory = self._direct_roots(name)
                for entry in entries:
                    (output_directory / entry).write_bytes(b"stale")
                with (
                    mock.patch("liang_pingfa_review.oda.subprocess.run") as launched,
                    self.assertRaises(PipelineError) as raised,
                ):
                    self.runner.convert(
                        input_directory,
                        output_directory,
                        "DXF",
                        register_output=lambda candidate: candidate,
                    )
                self.assertEqual(raised.exception.code, ErrorCode.ODA_OUTPUT_INCOMPATIBLE)
                self.assertFalse(launched.called)

    def test_timeout_nonzero_and_missing_output_fail_closed(self) -> None:
        cases: tuple[tuple[str, object, ErrorCode], ...] = (
            (
                "timeout",
                subprocess.TimeoutExpired(["oda"], 3),
                ErrorCode.ODA_TIMEOUT,
            ),
            (
                "nonzero",
                subprocess.CompletedProcess([], 1),
                ErrorCode.CONVERSION_FAILURE,
            ),
            (
                "missing",
                subprocess.CompletedProcess([], 0),
                ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
            ),
        )
        for name, side_effect, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(self._run_direct(name, side_effect).code, expected)

    def test_multiple_output_and_sidecar_are_rejected_before_adoption(self) -> None:
        for name, extra in (("multiple", "second.dxf"), ("sidecar", "source.dxf.err")):
            with self.subTest(name=name):
                def produces(
                    command: list[str],
                    **kwargs: object,
                ) -> subprocess.CompletedProcess[bytes]:
                    del kwargs
                    output = Path(command[2])
                    candidate = output / f"{Path(command[-1]).stem}.dxf"
                    create_synthetic_dxf(candidate)
                    (output / extra).write_bytes(b"unrelated generated sidecar")
                    return subprocess.CompletedProcess(command, 0)

                error = self._run_direct(name, produces)
                self.assertEqual(error.code, ErrorCode.ODA_OUTPUT_INCOMPATIBLE)

    def test_wrong_name_and_source_change_fail_closed(self) -> None:
        def wrong_name(
            command: list[str],
            **kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            del kwargs
            create_synthetic_dxf(Path(command[2]) / "wrong-name.dxf")
            return subprocess.CompletedProcess(command, 0)

        self.assertEqual(
            self._run_direct("wrong-name", wrong_name).code,
            ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
        )

        input_directory, output_directory = self._direct_roots("source-changed")

        def changes_source(
            command: list[str],
            **kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            del kwargs
            create_synthetic_dxf(Path(command[2]) / f"{Path(command[-1]).stem}.dxf")
            (Path(command[1]) / command[-1]).write_bytes(b"AC1032changed-source")
            return subprocess.CompletedProcess(command, 0)

        # The test backend deliberately permits the write; production read
        # leases normally deny it, and the post-run inventory is the fallback.
        with (
            mock.patch(
                "liang_pingfa_review.oda._converter_backend",
                return_value=TestOwnershipBackend(),
            ),
            mock.patch("liang_pingfa_review.oda.subprocess.run", side_effect=changes_source),
            self.assertRaises(PipelineError) as raised,
        ):
            self.runner.convert(
                input_directory,
                output_directory,
                "DXF",
                register_output=lambda candidate: candidate,
            )
        self.assertEqual(raised.exception.code, ErrorCode.CONVERSION_FAILURE)

    def test_timeout_cleanup_removes_owned_private_staging(self) -> None:
        source = self.root / "timeout-source.dwg"
        source.write_bytes(b"AC1032generated-source")
        workspace_path: Path | None = None
        with self.assertRaises(PipelineError) as raised:
            with PrivateWorkspace(
                prefix="liang-pingfa-oda-timeout-",
                directory=self.root,
            ) as workspace:
                workspace_path = workspace.path
                with mock.patch(
                    "liang_pingfa_review.oda.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["oda"], 3),
                ):
                    staged_dwg_to_dxf(source, workspace, self.runner)
        self.assertEqual(raised.exception.code, ErrorCode.ODA_TIMEOUT)
        assert workspace_path is not None
        self.assertFalse(workspace_path.exists())

    def test_oversized_dxf_rejects_during_bounded_lease_read_and_cleans_up(self) -> None:
        """A 65 MiB converter candidate stops at the first over-limit byte."""

        source = self.root / "oversized-source.dwg"
        source.write_bytes(b"AC1032generated-source")
        candidate: Path | None = None
        workspace_path: Path | None = None
        observed_reads: list[int] = []
        backend = TestOwnershipBackend()
        owned_path_type = TestOwnedPath
        original_read_chunks = owned_path_type.read_chunks
        test_case = self

        class OversizedConverter:
            def convert(
                self,
                input_directory: Path,
                output_directory: Path,
                output_type: str,
                *,
                register_output: object,
            ) -> Path:
                del register_output
                test_case.assertEqual(output_type, "DXF")
                staged_input = next(input_directory.iterdir())
                produced = output_directory / f"{staged_input.stem}.dxf"
                block = b"0" * (1024 * 1024)
                with produced.open("wb") as destination:
                    for _ in range(65):
                        destination.write(block)
                nonlocal candidate
                candidate = produced
                return produced

        converter = OversizedConverter()

        def instrumented_chunks(
            opened: object,
            chunk_size: int = 1024 * 1024,
        ) -> object:
            if (
                candidate is None
                or Path(os.path.abspath(os.fspath(opened.path)))
                != Path(os.path.abspath(os.fspath(candidate)))
            ):
                yield from original_read_chunks(opened, chunk_size)
                return
            self.assertEqual(chunk_size, 1024 * 1024)
            size = 0
            for chunk in original_read_chunks(opened, chunk_size):
                size += len(chunk)
                observed_reads.append(len(chunk))
                yield chunk
                if size > MAX_DXF_BYTES:
                    self.fail("bounded DXF reader consumed a chunk after overflow")

        with (
            mock.patch(
                "liang_pingfa_review.oda._converter_backend",
                return_value=backend,
            ),
            mock.patch.object(
                owned_path_type,
                "read_chunks",
                side_effect=instrumented_chunks,
                autospec=True,
            ),
            self.assertRaises(PipelineError) as raised,
        ):
            with PrivateWorkspace(
                prefix="liang-pingfa-oda-oversized-",
                directory=self.root,
                backend=backend,
            ) as workspace:
                workspace_path = workspace.path
                staged_dwg_to_dxf(
                    source,
                    workspace,
                    converter,  # type: ignore[arg-type]
                    stage_name="oversized",
                )
        self.assertEqual(len(observed_reads), 65)
        self.assertEqual(sum(observed_reads), MAX_DXF_BYTES + 1024 * 1024)
        self.assertEqual(raised.exception.code, ErrorCode.UNSAFE_ENTITY_TYPE)
        assert candidate is not None
        assert workspace_path is not None
        self.assertFalse(candidate.exists())
        self.assertFalse(workspace_path.exists())


class OdaDualProofTests(unittest.TestCase):
    """Dual agreement is necessary but never sufficient for expected state."""

    def setUp(self) -> None:
        _install_portable_backend(self)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_disagreeing_valid_outputs_are_rejected(self) -> None:
        fixture = self.root / "fixture.dxf"
        source = self.root / "source.dwg"
        create_synthetic_dxf(fixture)
        create_fake_dwg(source)

        class DisagreeingConverter(FakeOdaConverter):
            def __init__(self, initial_dxf: Path) -> None:
                super().__init__(initial_dxf)
                self.run_count = 0

            def convert(
                self,
                input_directory: Path,
                output_directory: Path,
                output_type: str,
                *,
                register_output: object,
            ) -> Path:
                del input_directory, register_output
                self.run_count += 1
                destination = output_directory / (
                    f"{next(self.initial_dxf.parent.glob('source.dwg'), self.initial_dxf).stem}"
                    f".{output_type.lower()}"
                )
                # The staging source has a random name, so derive the exact
                # ODA output leaf from it rather than relying on a fixed path.
                staged_source = next(output_directory.parent.joinpath("input").iterdir())
                destination = output_directory / f"{staged_source.stem}.{output_type.lower()}"
                create_synthetic_dxf(
                    destination,
                    variant="actionable" if self.run_count == 1 else "ambiguous",
                )
                return destination

        converter = DisagreeingConverter(fixture)
        with self.assertRaises(PipelineError) as raised:
            with PrivateWorkspace(
                prefix="liang-pingfa-oda-disagree-",
                directory=self.root,
            ) as workspace:
                staged_dwg_to_dxf(
                    source,
                    workspace,
                    converter,  # type: ignore[arg-type]
                    stage_name="disagree",
                )
        self.assertEqual(raised.exception.code, ErrorCode.ODA_OUTPUT_INCOMPATIBLE)
        self.assertEqual(converter.run_count, 2)

    def test_identical_wrong_outputs_need_expected_state_proof(self) -> None:
        source = self.root / "edited.dxf"
        create_synthetic_dxf(source)
        converter = FakeOdaConverter(source)

        def reject_identical_but_wrong(_roundtrip: Path) -> None:
            raise PipelineError(
                ErrorCode.RE_AUDIT_MISMATCH,
                "synthetic expected-state proof rejected candidate",
            )

        with self.assertRaises(PipelineError) as raised:
            with PrivateWorkspace(
                prefix="liang-pingfa-oda-expected-state-",
                directory=self.root,
            ) as workspace:
                staged_dxf_to_dwg(
                    source,
                    workspace,
                    converter,  # type: ignore[arg-type]
                    stage_name="expected-state",
                    expected_state_proof=reject_identical_but_wrong,
                )
        self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)

    def test_expected_state_proof_runs_for_each_dual_dwg_candidate(self) -> None:
        source = self.root / "edited-proof.dxf"
        create_synthetic_dxf(source)
        converter = FakeOdaConverter(source)
        proven: list[Path] = []

        with PrivateWorkspace(
            prefix="liang-pingfa-oda-two-proofs-",
            directory=self.root,
        ) as workspace:
            output = staged_dxf_to_dwg(
                source,
                workspace,
                converter,  # type: ignore[arg-type]
                stage_name="two-proofs",
                expected_state_proof=proven.append,
            )
            self.assertEqual(output.suffix.casefold(), ".dwg")
        self.assertEqual(len(proven), 2)
        self.assertTrue(all(path.suffix.casefold() == ".dxf" for path in proven))


class PrivateStagingCapabilityTests(unittest.TestCase):
    """DACL and NTFS capability failures must stop before any ODA launch."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_private_workspace_rejects_missing_ntfs_or_dacl_capability(self) -> None:
        for name, failure in (
            ("non-ntfs", "fail_private_ancestry"),
            ("dacl", "fail_private_dacl"),
        ):
            with self.subTest(name=name):
                backend = TestOwnershipBackend()
                setattr(backend, failure, True)
                with self.assertRaises(PipelineError) as raised:
                    with PrivateWorkspace(
                        prefix=f"liang-pingfa-{name}-",
                        directory=self.root,
                        backend=backend,
                    ):
                        self.fail("private workspace should not open")
                self.assertEqual(raised.exception.code, ErrorCode.CONVERSION_FAILURE)

    def test_private_oda_root_is_independently_secured(self) -> None:
        backend = TestOwnershipBackend()
        with PrivateWorkspace(
            prefix="liang-pingfa-private-root-",
            directory=self.root,
            backend=backend,
        ) as workspace:
            root = workspace.create_private_oda_root(workspace / "oda-random")
            self.assertEqual(root.parent, workspace.path)
            self.assertIn(root, backend.secured_private_directories)
        self.assertGreaterEqual(len(backend.private_ancestry_checks), 1)


@unittest.skipUnless(
    os.name == "nt" and os.environ.get("LIANG_PINGFA_RUN_GENERATED_ODA") == "1",
    "generated real ODA workflow is an explicit local Windows qualification",
)
class GeneratedOdaIntegrationTests(unittest.TestCase):
    """Run installed ODA only on runtime-generated R2018 data."""

    def setUp(self) -> None:
        try:
            self.runner = OdaRunner.discover()
        except PipelineError as error:
            self.skipTest(f"installed ODA is unavailable: {error.code.value}")
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_generated_dual_directions_and_full_audit_plan_apply_verify(self) -> None:
        """This is a narrow supported-profile qualification, never user data."""

        seed_dxf = self.root / "generated-seed.dxf"
        source_dwg = self.root / "generated-source.dwg"
        corrected_dwg = self.root / "generated-corrected.dwg"
        create_synthetic_dxf(seed_dxf)

        with PrivateWorkspace(
            prefix="liang-pingfa-generated-oda-seed-",
            directory=self.root,
        ) as workspace:
            staged_dwg = staged_dxf_to_dwg(
                seed_dxf,
                workspace,
                self.runner,
                stage_name="generated-seed",
            )
            # The source is generated in this test only; no fixture, user
            # path, hash, drawing, or converter output persists afterward.
            shutil.copyfile(staged_dwg, source_dwg)

        audit = audit_dwg(source_dwg, self.runner)
        plan = generate_edit_plan(audit)
        result = apply_dwg(
            source_dwg,
            audit,
            plan,
            plan["plan_id"],
            corrected_dwg,
            self.runner,
        )
        verification = verify_dwg(corrected_dwg, audit, plan, self.runner)
        self.assertEqual(len(plan["operations"]), 1)
        self.assertTrue(result["passed"])
        self.assertTrue(verification["passed"])


class CliDoctorAndWorkflowTests(unittest.TestCase):
    """Doctor emits only redacted capability states and CLI remains DWG-only."""

    def setUp(self) -> None:
        _install_portable_backend(self)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_doctor_reports_version_windows_ntfs_and_dacl_without_details(self) -> None:
        events: list[dict[str, str]] = []
        with (
            mock.patch(
                "liang_pingfa_review.cli.private_staging_capability",
                return_value=PrivateStagingCapability(True, True, True),
            ),
            mock.patch(
                "liang_pingfa_review.cli._runner",
                return_value=SimpleNamespace(version="27.1.0"),
            ),
            mock.patch(
                "liang_pingfa_review.cli._emit",
                side_effect=lambda event, **_kwargs: events.append(event),
            ),
        ):
            self.assertEqual(cli.main(["doctor"]), 0)
        self.assertEqual(
            events,
            [
                {
                    "status": "ok",
                    "command": "doctor",
                    "support_profile": "r2018-ac1032-dxf-exposable-overlay-text",
                    "support_profile_readiness": "ready",
                    "per_file_compatibility": "audit_required",
                    "oda_version": "27.1.0",
                    "windows": "ready",
                    "ntfs_private_staging": "supported",
                    "dacl": "verified",
                }
            ],
        )

    def test_doctor_reports_unavailable_private_staging_without_details(self) -> None:
        events: list[dict[str, str]] = []
        with (
            mock.patch(
                "liang_pingfa_review.cli.private_staging_capability",
                return_value=PrivateStagingCapability(True, False, False),
            ),
            mock.patch(
                "liang_pingfa_review.cli._runner",
                return_value=SimpleNamespace(version="27.1.0"),
            ),
            mock.patch(
                "liang_pingfa_review.cli._emit",
                side_effect=lambda event, **_kwargs: events.append(event),
            ),
        ):
            self.assertEqual(cli.main(["doctor"]), 0)
        self.assertEqual(
            events,
            [
                {
                    "status": "not_ready",
                    "command": "doctor",
                    "support_profile": "r2018-ac1032-dxf-exposable-overlay-text",
                    "support_profile_readiness": "not_ready",
                    "per_file_compatibility": "audit_required",
                    "oda_version": "27.1.0",
                    "windows": "ready",
                    "ntfs_private_staging": "unsupported",
                    "dacl": "unsupported",
                }
            ],
        )

    def test_cli_help_separates_profile_prerequisites_from_file_compatibility(self) -> None:
        parser = cli.build_parser()
        self.assertIn(
            "R2018/AC1032 DXF-exposable profile only",
            " ".join(parser.format_help().split()),
        )
        doctor_help = next(
            action.choices["doctor"].format_help()
            for action in parser._actions
            if "doctor" in (getattr(action, "choices", None) or {})
        )
        self.assertIn(
            "does not assess a drawing",
            " ".join(doctor_help.split()),
        )

    def test_cli_audit_and_plan_use_generated_private_converter_data(self) -> None:
        source = self.root / "source.dwg"
        fixture = self.root / "fixture.dxf"
        output = self.root / "output"
        output.mkdir()
        create_fake_dwg(source)
        create_synthetic_dxf(fixture)

        with mock.patch(
            "liang_pingfa_review.cli._runner",
            return_value=FakeOdaConverter(fixture),
        ):
            self.assertEqual(
                cli.main(
                    [
                        "audit",
                        "--input",
                        str(source),
                        "--audit-out",
                        str(output / "audit.json"),
                        "--report-out",
                        str(output / "audit.md"),
                    ]
                ),
                0,
            )
            self.assertEqual(
                cli.main(
                    [
                        "plan",
                        "--audit",
                        str(output / "audit.json"),
                        "--plan-out",
                        str(output / "plan.json"),
                        "--review-out",
                        str(output / "plan.md"),
                    ]
                ),
                0,
            )
        self.assertTrue((output / "audit.json").is_file())
        self.assertTrue((output / "plan.json").is_file())


if __name__ == "__main__":
    unittest.main()
