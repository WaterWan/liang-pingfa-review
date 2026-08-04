"""Generated-only regressions for cleanup-gated native publication."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from liang_pingfa_review import native_apply as native_apply_module
from liang_pingfa_review.atomic_output import (
    ArtifactPublication,
    OutputTargetLeaseSet,
    PublicationTransaction,
    acquire_new_output_target_leases,
    publish_artifacts,
    stage_publication_transaction,
)
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.ownership import (
    DestinationExistsError,
    OwnershipCleanupError,
    OwnershipLostError,
)
from liang_pingfa_review.temporary import PrivateWorkspace
from tests.support.owned_files import TestOwnershipBackend


class NativePublicationTransactionTests(unittest.TestCase):
    """Exercise only generated files; no CAD input or host is accessed."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backend = TestOwnershipBackend()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_private_dwg(self, workspace: PrivateWorkspace) -> Path:
        private_dwg = workspace.path / "generated-private.dwg"
        opened = workspace.create_owned_file(private_dwg)
        opened.write_bytes(b"AC1032generated-private-output")
        workspace.seal_owned_file(opened)
        return private_dwg

    def _targets(self) -> tuple[Path, Path, OutputTargetLeaseSet]:
        output = self.root / "published-output.dwg"
        verification = self.root / "published-verification.json"
        targets = acquire_new_output_target_leases(
            (output, verification),
            backend=self.backend,
        )
        return output, verification, targets

    @staticmethod
    def _stage(
        private_dwg: Path,
        targets: OutputTargetLeaseSet,
    ) -> PublicationTransaction:
        transaction = stage_publication_transaction(
            private_dwg,
            targets.targets[0],
        )
        transaction.stage_artifact(
            targets.targets[1],
            b'{"passed":true,"generated":true}',
        )
        return transaction

    def _assert_no_owned_public_stage(self) -> None:
        self.assertEqual(list(self.root.glob(".liang-pingfa-publish-*.tmp")), [])
        self.assertEqual(list(self.root.glob(".liang-pingfa-artifact-*.tmp")), [])

    def test_stages_before_workspace_cleanup_then_commits_exact_pair(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        try:
            with PrivateWorkspace(
                prefix="native-publish-success-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                private_dwg = self._create_private_dwg(workspace)
                workspace.require_exact_inventory()
                transaction = self._stage(private_dwg, targets)
                # Final names remain absent while the private workspace exists.
                self.assertFalse(output.exists())
                self.assertFalse(verification.exists())
                self.assertTrue(
                    list(self.root.glob(".liang-pingfa-publish-*.tmp"))
                )
                self.assertTrue(
                    list(self.root.glob(".liang-pingfa-artifact-*.tmp"))
                )

            assert transaction is not None
            transaction.commit()
            transaction.finalize()
            self.assertEqual(
                output.read_bytes(),
                b"AC1032generated-private-output",
            )
            self.assertEqual(
                verification.read_bytes(),
                b'{"passed":true,"generated":true}',
            )
            # Both public-parent staging files were created through the
            # private API, then restored to their ordinary output policy only
            # during the final no-replace transition.
            self.assertEqual(len(self.backend.private_file_creates), 2)
            self.assertEqual(len(self.backend.secured_private_files), 2)
            self.assertTrue(
                all(
                    path.name.startswith(".liang-pingfa-")
                    and path.suffix == ".tmp"
                    for path in self.backend.restored_public_output_files
                )
            )
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                transaction.abort()
            targets.close()

    def test_unknown_and_expected_name_sidecars_block_staging_and_publish(self) -> None:
        for sidecar_name in ("generated-console-sidecar.err", "native-console-result.json"):
            with self.subTest(sidecar_name=sidecar_name):
                output, verification, targets = self._targets()
                transaction: PublicationTransaction | None = None
                workspace_path: Path | None = None
                try:
                    with self.assertRaises(PipelineError) as raised:
                        with PrivateWorkspace(
                            prefix="native-publish-sidecar-",
                            directory=self.root,
                            backend=self.backend,
                        ) as workspace:
                            workspace_path = workspace.path
                            self._create_private_dwg(workspace)
                            (workspace.path / sidecar_name).write_bytes(
                                b"foreign-generated-sidecar"
                            )
                            workspace.require_exact_inventory()
                    self.assertEqual(
                        raised.exception.code,
                        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                    )
                    self.assertFalse(output.exists())
                    self.assertFalse(verification.exists())
                    self._assert_no_owned_public_stage()
                    assert workspace_path is not None
                    self.assertTrue((workspace_path / sidecar_name).exists())
                finally:
                    if transaction is not None:
                        transaction.abort()
                    targets.close()
                    if workspace_path is not None and workspace_path.exists():
                        shutil.rmtree(workspace_path)

    def test_private_cleanup_failure_after_staging_aborts_only_owned_public_temps(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        workspace_path: Path | None = None
        try:
            with mock.patch(
                "liang_pingfa_review.temporary.dispose_owned_binding",
                side_effect=OwnershipCleanupError("generated locked workspace file"),
            ):
                with self.assertRaises(PipelineError) as raised:
                    with PrivateWorkspace(
                        prefix="native-publish-locked-",
                        directory=self.root,
                        backend=self.backend,
                    ) as workspace:
                        workspace_path = workspace.path
                        transaction = self._stage(
                            self._create_private_dwg(workspace),
                            targets,
                        )
                        self.assertFalse(output.exists())
                        self.assertFalse(verification.exists())
            self.assertEqual(
                raised.exception.code,
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            )
            assert transaction is not None
            transaction.abort(raised.exception)
            self.assertFalse(output.exists())
            self.assertFalse(verification.exists())
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                transaction.abort()
            targets.close()
            if workspace_path is not None and workspace_path.exists():
                shutil.rmtree(workspace_path)

    def test_cleanup_exception_after_staging_never_exposes_final_paths(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        workspace_path: Path | None = None
        try:
            with mock.patch.object(
                PrivateWorkspace,
                "_recover",
                side_effect=PipelineError(
                    ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                    "generated cleanup exception",
                ),
            ):
                with self.assertRaises(PipelineError) as raised:
                    with PrivateWorkspace(
                        prefix="native-publish-cleanup-exception-",
                        directory=self.root,
                        backend=self.backend,
                    ) as workspace:
                        workspace_path = workspace.path
                        transaction = self._stage(
                            self._create_private_dwg(workspace),
                            targets,
                        )
            self.assertEqual(
                raised.exception.code,
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            )
            assert transaction is not None
            transaction.abort(raised.exception)
            self.assertFalse(output.exists())
            self.assertFalse(verification.exists())
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                transaction.abort()
            targets.close()
            if workspace_path is not None and workspace_path.exists():
                shutil.rmtree(workspace_path)

    def test_first_final_commit_failure_removes_both_hidden_stages(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        try:
            with PrivateWorkspace(
                prefix="native-publish-first-commit-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                transaction = self._stage(self._create_private_dwg(workspace), targets)
            assert transaction is not None
            with (
                mock.patch.object(
                    transaction.output.owned,
                    "rename_no_replace",
                    side_effect=OSError("generated first commit failure"),
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                transaction.commit()
            self.assertEqual(raised.exception.code, ErrorCode.ATOMIC_PUBLISH_FAILED)
            self.assertFalse(output.exists())
            self.assertFalse(verification.exists())
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                transaction.abort()
            targets.close()

    def test_second_final_commit_failure_rolls_back_output_without_passed_artifact(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        try:
            with PrivateWorkspace(
                prefix="native-publish-second-commit-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                transaction = self._stage(self._create_private_dwg(workspace), targets)
            assert transaction is not None
            assert transaction.artifact is not None
            with (
                mock.patch.object(
                    transaction.artifact.owned,
                    "rename_no_replace",
                    side_effect=DestinationExistsError("generated second commit failure"),
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                transaction.commit()
            self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_EXISTS)
            self.assertFalse(output.exists())
            self.assertFalse(verification.exists())
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                transaction.abort()
            targets.close()

    def test_destination_race_between_commits_preserves_foreign_file_and_rolls_back_output(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        calls = 0

        def source_binding() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                verification.write_bytes(b"foreign-verification")

        try:
            with PrivateWorkspace(
                prefix="native-publish-race-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                transaction = self._stage(self._create_private_dwg(workspace), targets)
            assert transaction is not None
            with self.assertRaises(PipelineError) as raised:
                transaction.commit(source_binding=source_binding)
            self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_EXISTS)
            self.assertFalse(output.exists())
            self.assertEqual(verification.read_bytes(), b"foreign-verification")
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                transaction.abort()
            targets.close()

    def test_rollback_failure_never_leaves_the_passed_verification_artifact(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        try:
            with PrivateWorkspace(
                prefix="native-publish-rollback-failure-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                transaction = self._stage(self._create_private_dwg(workspace), targets)
            assert transaction is not None
            assert transaction.artifact is not None
            with (
                mock.patch.object(
                    transaction.artifact.owned,
                    "rename_no_replace",
                    side_effect=DestinationExistsError("generated second commit failure"),
                ),
                mock.patch(
                    "liang_pingfa_review.atomic_output._rollback_published_artifact",
                    side_effect=PipelineError(
                        ErrorCode.PUBLICATION_ROLLBACK_FAILURE,
                        "generated rollback failure",
                    ),
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                transaction.commit()
            self.assertEqual(
                raised.exception.code,
                ErrorCode.PUBLICATION_ROLLBACK_FAILURE,
            )
            # The first output may require recovery, but the verification
            # final was never committed, so no orphaned passed evidence exists.
            self.assertFalse(verification.exists())
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                try:
                    transaction.output.owned.close()
                except OSError:
                    pass
            targets.close()
            if output.exists():
                output.unlink()

    def test_private_dacl_application_failure_removes_hidden_public_stage(self) -> None:
        output, _verification, targets = self._targets()
        self.backend.fail_private_file_dacl = True
        try:
            with PrivateWorkspace(
                prefix="native-publish-private-dacl-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                with self.assertRaises(PipelineError) as raised:
                    stage_publication_transaction(
                        self._create_private_dwg(workspace),
                        targets.targets[0],
                    )
            self.assertEqual(raised.exception.code, ErrorCode.ATOMIC_PUBLISH_FAILED)
            self.assertFalse(output.exists())
            self._assert_no_owned_public_stage()
        finally:
            targets.close()

    def test_mixed_artifact_pair_keeps_json_private_and_markdown_public(self) -> None:
        """The private final never receives the captured public parent DACL."""

        machine = self.root / "native-audit.json"
        report = self.root / "native-audit.md"
        published = publish_artifacts(
            (
                ArtifactPublication(
                    path=machine,
                    payload=b'{"record_cardinality":"explicit_private"}\n',
                    private=True,
                ),
                ArtifactPublication(
                    path=report,
                    payload=b"# redacted native audit\n",
                    private=False,
                ),
            ),
            backend=self.backend,
        )
        self.assertEqual([artifact.path for artifact in published], [machine, report])
        self.assertEqual(machine.read_bytes(), b'{"record_cardinality":"explicit_private"}\n')
        self.assertEqual(report.read_bytes(), b"# redacted native audit\n")
        # One private artifact is verified before/after its no-replace rename;
        # only the Markdown temporary receives a restored public policy.
        self.assertGreaterEqual(len(self.backend.verified_private_files), 3)
        self.assertEqual(len(self.backend.restored_public_output_files), 1)
        self.assertTrue(
            self.backend.restored_public_output_files[0].name.startswith(
                ".liang-pingfa-artifact-"
            )
        )

    def test_private_json_acl_verify_failure_rolls_back_mixed_pair(self) -> None:
        """A failure after the JSON rename still removes both owned finals."""

        machine = self.root / "native-plan.json"
        report = self.root / "native-plan.md"
        calls = 0
        original_verify = __import__(
            "liang_pingfa_review.atomic_output",
            fromlist=["verify_private_staging_file"],
        ).verify_private_staging_file

        def fail_after_final(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OwnershipCleanupError("generated final private DACL failure")
            original_verify(*args, **kwargs)

        with (
            mock.patch(
                "liang_pingfa_review.atomic_output.verify_private_staging_file",
                side_effect=fail_after_final,
            ),
            self.assertRaises(PipelineError) as raised,
        ):
            publish_artifacts(
                (
                    ArtifactPublication(machine, b'{"private":true}', private=True),
                    ArtifactPublication(report, b"# redacted", private=False),
                ),
                backend=self.backend,
            )
        self.assertEqual(raised.exception.code, ErrorCode.ATOMIC_PUBLISH_FAILED)
        self.assertFalse(machine.exists())
        self.assertFalse(report.exists())
        self._assert_no_owned_public_stage()

    def test_final_acl_transition_failure_after_first_rename_rolls_back_pair(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        transitions = 0

        def fail_second_transition(*_args: object, **_kwargs: object) -> None:
            nonlocal transitions
            transitions += 1
            if transitions == 2:
                raise OwnershipCleanupError("generated final ACL failure")

        try:
            with PrivateWorkspace(
                prefix="native-publish-final-acl-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                transaction = self._stage(self._create_private_dwg(workspace), targets)
            assert transaction is not None
            with (
                mock.patch(
                    "liang_pingfa_review.atomic_output.apply_public_output_acl_policy",
                    side_effect=fail_second_transition,
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                transaction.commit()
            self.assertEqual(raised.exception.code, ErrorCode.ATOMIC_PUBLISH_FAILED)
            self.assertFalse(output.exists())
            self.assertFalse(verification.exists())
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                transaction.abort()
            targets.close()

    def test_post_rename_close_failures_roll_back_before_and_after_close(self) -> None:
        """No failing cleanup may leave a passed verification public."""

        class CloseProbe:
            def __init__(self, *, fail_after_close: bool) -> None:
                self.fail_after_close = fail_after_close
                self.closed = False

            def close(self) -> None:
                if self.fail_after_close:
                    self.closed = True
                raise OSError("generated close failure")

        for label, argument, after_close in (
            ("source", "source_lease", False),
            ("source-after-close", "source_lease", True),
            ("output-target", "output_targets", False),
            ("output-target-after-close", "output_targets", True),
            ("component", "component_leases", False),
            ("component-after-close", "component_leases", True),
        ):
            with self.subTest(label=label):
                output, verification, targets = self._targets()
                transaction: PublicationTransaction | None = None
                probe = CloseProbe(fail_after_close=after_close)
                resources: dict[str, object] = {
                    "client": None,
                    "source_lease": None,
                    "output_targets": None,
                    "component_leases": None,
                }
                resources[argument] = probe
                try:
                    with PrivateWorkspace(
                        prefix="native-publish-post-close-",
                        directory=self.root,
                        backend=self.backend,
                    ) as workspace:
                        transaction = self._stage(
                            self._create_private_dwg(workspace),
                            targets,
                        )
                    assert transaction is not None
                    transaction.commit()
                    failure = native_apply_module._close_post_rename_resources(
                        client=resources["client"],  # type: ignore[arg-type]
                        source_lease=resources["source_lease"],
                        output_targets=resources["output_targets"],  # type: ignore[arg-type]
                        component_leases=resources["component_leases"],  # type: ignore[arg-type]
                    )
                    assert failure is not None
                    self.assertEqual(
                        failure.code,
                        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                    )
                    transaction.abort(failure)
                    self.assertFalse(output.exists())
                    self.assertFalse(verification.exists())
                    self._assert_no_owned_public_stage()
                    self.assertEqual(probe.closed, after_close)
                finally:
                    if transaction is not None:
                        transaction.abort()
                    targets.close()

    def test_final_binding_failure_rolls_back_both_committed_artifacts(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        try:
            with PrivateWorkspace(
                prefix="native-publish-final-binding-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                transaction = self._stage(self._create_private_dwg(workspace), targets)
            assert transaction is not None
            transaction.commit()
            assert transaction._published_artifact is not None
            with (
                mock.patch.object(
                    transaction._published_artifact.owned,
                    "capture_binding",
                    side_effect=OwnershipLostError("generated final binding drift"),
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                transaction.finalize()
            self.assertEqual(raised.exception.code, ErrorCode.ATOMIC_PUBLISH_FAILED)
            self.assertFalse(output.exists())
            self.assertFalse(verification.exists())
            self._assert_no_owned_public_stage()
        finally:
            if transaction is not None:
                transaction.abort()
            targets.close()

    def test_unproven_post_rename_rollback_is_fatal_without_passed_artifact(self) -> None:
        output, verification, targets = self._targets()
        transaction: PublicationTransaction | None = None
        try:
            with PrivateWorkspace(
                prefix="native-publish-fatal-rollback-",
                directory=self.root,
                backend=self.backend,
            ) as workspace:
                transaction = self._stage(self._create_private_dwg(workspace), targets)
            assert transaction is not None
            transaction.commit()
            assert transaction._published_output is not None

            from liang_pingfa_review import atomic_output as atomic_output_module

            original = atomic_output_module._rollback_published_artifact

            def rollback_artifact_then_fail_output(artifact, failure):
                if artifact.path == output:
                    raise PipelineError(
                        ErrorCode.PUBLICATION_ROLLBACK_FAILURE,
                        "generated fatal recovery failure",
                    )
                return original(artifact, failure)

            with (
                mock.patch(
                    "liang_pingfa_review.atomic_output._rollback_published_artifact",
                    side_effect=rollback_artifact_then_fail_output,
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                transaction.abort(
                    PipelineError(
                        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                        "generated post-rename cleanup failure",
                    )
                )
            self.assertEqual(
                raised.exception.code,
                ErrorCode.PUBLICATION_ROLLBACK_FAILURE,
            )
            # The output may require fatal operator recovery, but the passed
            # verification artifact is always rolled back first.
            self.assertFalse(verification.exists())
        finally:
            if transaction is not None and output.exists():
                try:
                    transaction.output.owned.close()
                except OSError:
                    pass
            targets.close()
            if output.exists():
                output.unlink()


if __name__ == "__main__":
    unittest.main()
