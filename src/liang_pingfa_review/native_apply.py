"""Copy-only native apply orchestration with mandatory independent readback."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Any

from .atomic_output import (
    PublicationTransaction,
    OutputTargetLeaseSet,
    StagedOutputExpectation,
    StagedOutputLease,
    acquire_new_output_target_leases,
    stage_publication_transaction,
)
from .canonical import canonical_json_bytes, normalize_nfc_text
from .core_console import run_core_console
from .errors import ErrorCode, PipelineError
from .native_audit import native_source_from_lease, require_fresh_native_audit
from .native_bridge import (
    NativeBridgeClient,
    NativeInstallationLeases,
    acquire_native_installation_leases,
)
from .native_contracts import require_active_native_contract
from .native_manifest import (
    build_native_manifest,
    require_final_output_binding,
    require_fresh_native_manifest,
    write_private_manifest,
)
from .native_plan import validate_native_plan_against_audit
from .native_verify import (
    build_native_verification,
    geometry_from_console_export,
    require_published_output_binding,
    validate_console_result,
    verify_native_transition,
)
from .ownership import (
    FileOwnershipBackend,
    OwnedPath,
    OwnedPathBinding,
    OwnershipCleanupError,
    OwnershipError,
    acquire_source_path_lease,
    platform_backend,
)
from .temporary import PrivateWorkspace


_DWG_HEADER = re.compile(r"^AC[0-9A-Z]{4}$")


@dataclass(frozen=True)
class NativeApplyResult:
    """Only redacted evidence and output identity leave native apply."""

    verification: dict[str, Any]


def _native_output_description(
    path: Path,
    opened: OwnedPath,
    binding: OwnedPathBinding,
    backend: FileOwnershipBackend,
    *,
    final_name_visible: bool = True,
) -> dict[str, Any]:
    """Describe a retained output temporary or just-published final safely."""

    try:
        current = opened.capture_binding()
        current_path = opened.final_path()
        header = opened.read_prefix(6).decode("ascii", errors="strict")
    except Exception as error:
        raise PipelineError(ErrorCode.OUTPUT_CHANGED_DURING_VERIFY, "native output handle unavailable") from error
    expected_path = path if final_name_visible else binding.path
    same_handle_path = os.path.normcase(os.path.normpath(os.fspath(current_path))) == (
        os.path.normcase(os.path.normpath(os.fspath(expected_path)))
    )
    if (
        current.is_directory
        or not current.same_identity_and_content(binding)
        or not same_handle_path
        or current.sha256 is None
        or current.byte_size is None
        or _DWG_HEADER.fullmatch(header) is None
    ):
        raise PipelineError(ErrorCode.OUTPUT_CHANGED_DURING_VERIFY, "native output binding drift")
    return {
        "format": "DWG",
        "sha256": current.sha256,
        "byte_size": current.byte_size,
        "path_fingerprint": sha256(
            normalize_nfc_text(str(path)).encode("utf-8")
        ).hexdigest(),
        "file_identity_fingerprint": current.file_identity_fingerprint,
        "dwg_header_signature": header,
    }


def _validate_apply_targets(
    source_path: Path,
    output_path: Path,
    verification_path: Path,
) -> OutputTargetLeaseSet:
    if source_path.suffix.casefold() != ".dwg" or output_path.suffix.casefold() != ".dwg":
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "native apply requires DWG input/output")
    if verification_path.suffix.casefold() != ".json":
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "native verification requires JSON output")
    targets = acquire_new_output_target_leases((output_path, verification_path))
    try:
        normalized_source = os.path.normcase(os.path.abspath(os.fspath(source_path)))
        destinations = [
            os.path.normcase(os.path.abspath(os.fspath(target.destination)))
            for target in targets.targets
        ]
        if normalized_source in destinations or len(set(destinations)) != len(destinations):
            raise PipelineError(ErrorCode.INVALID_ARGUMENT, "native output aliases source/artifact")
        return targets
    except BaseException:
        targets.close()
        raise


def _copy_source_to_private_dwg(
    source_lease: Any,
    workspace: PrivateWorkspace,
) -> tuple[Path, dict[str, Any]]:
    """Copy bytes only from the retained source handle into a private owned file."""

    destination = workspace.path / "native-source-copy.dwg"
    opened = workspace.create_owned_file(destination)
    try:
        opened.write_chunks(source_lease.read_chunks())
        binding = opened.capture_binding()
        final_path = opened.final_path()
        header = opened.read_prefix(6).decode("ascii", errors="strict")
        workspace.seal_owned_file(opened)
    except BaseException:
        try:
            workspace.discard_owned_file(opened)
        except BaseException:
            pass
        raise
    if (
        binding.sha256 is None
        or binding.byte_size is None
        or _DWG_HEADER.fullmatch(header) is None
    ):
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_INVALID, "private source copy is unbound")
    return destination, {
        "sha256": binding.sha256,
        "byte_size": binding.byte_size,
        "path_fingerprint": sha256(
            normalize_nfc_text(str(final_path)).encode("utf-8")
        ).hexdigest(),
        "file_identity_fingerprint": binding.file_identity_fingerprint,
        "dwg_header_signature": header,
    }


def _close_post_rename_resources(
    *,
    client: NativeBridgeClient | None,
    source_lease: Any,
    output_targets: OutputTargetLeaseSet | None,
    component_leases: NativeInstallationLeases | None,
) -> PipelineError | None:
    """Run every failure-prone cleanup while publication handles can roll back.

    A successful pair of no-replace renames is not yet a successful
    ``native-apply``.  Source, output-parent, component, and client leases
    are released before final publication handles are finalized, so any
    failure can still remove both final artifacts by retained identity.
    """

    failure: PipelineError | None = None

    def close_resource(resource: Any, label: str) -> None:
        nonlocal failure
        if resource is None:
            return
        try:
            resource.close()
        except PipelineError as error:
            if failure is None:
                failure = error
        except (OSError, OwnershipError, OwnershipCleanupError) as error:
            if failure is None:
                failure = PipelineError(
                    ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                    f"native apply {label} cleanup failed",
                )
                failure.__cause__ = error

    # Continue after a first failure: every caller-owned handle has either
    # been released or has had its close attempted before rollback proceeds.
    close_resource(client, "bridge")
    close_resource(source_lease, "source lease")
    close_resource(output_targets, "output target lease")
    close_resource(component_leases, "component lease")
    return failure


def native_apply(
    source_path: Path,
    session: Mapping[str, Any],
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
    intent: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    confirm_plan: str,
    output_path: Path,
    verification_path: Path,
) -> NativeApplyResult:
    """Perform the fixed copy-only native flow; no ODA path is ever invoked."""

    # Version gates precede the platform gate so a v1 artifact never receives
    # an accidental execution-looking failure mode on non-Windows hosts.
    checked_config = require_active_native_contract("config", config)
    checked_audit = require_fresh_native_audit(audit)
    checked_intent = require_active_native_contract("intent", intent)
    checked_plan = validate_native_plan_against_audit(
        checked_audit,
        checked_intent,
        plan,
        checked_config,
    )
    if confirm_plan != checked_plan["plan_id"]:
        raise PipelineError(ErrorCode.NATIVE_OPERATION_INVALID, "native plan confirmation differs")
    if os.name != "nt":
        raise PipelineError(ErrorCode.WINDOWS_PLATFORM_REQUIRED, "native apply is Windows-only")
    output_targets = _validate_apply_targets(
        source_path, output_path, verification_path
    )
    source_lease = None
    publication: PublicationTransaction | None = None
    verification: dict[str, Any] | None = None
    client: NativeBridgeClient | None = None
    component_leases: NativeInstallationLeases | None = None
    completed = False
    try:
        component_leases = acquire_native_installation_leases(checked_config)
        backend = platform_backend(require_windows=True)
        source_lease = acquire_source_path_lease(source_path, backend)
        source = native_source_from_lease(source_lease)
        if source != checked_audit["source"]:
            raise PipelineError(ErrorCode.NATIVE_DOCUMENT_CHANGED, "native source differs from audit")
        with PrivateWorkspace(prefix="liang-pingfa-native-apply-") as workspace:
            private_dwg, private_copy = _copy_source_to_private_dwg(source_lease, workspace)
            checked_session = require_active_native_contract("session", session)
            if checked_session["process"]["executable_fingerprint"] == "unavailable":
                raise PipelineError(
                    ErrorCode.NATIVE_CAPABILITY_MISMATCH,
                    "native apply requires a host executable fingerprint",
                )
            client = NativeBridgeClient(checked_session, config=checked_config)
            fresh_export = client.export_exact_geometry()
            manifest = build_native_manifest(
                checked_audit,
                checked_plan,
                checked_intent,
                fresh_export,
                checked_session,
                checked_config,
                private_source_copy=private_copy,
                output_path=output_targets.targets[0].destination,
                private_output_path=private_dwg,
                private_workspace_root=workspace.path,
            )
            manifest_path = write_private_manifest(
                workspace,
                workspace.path / "native-manifest.json",
                manifest,
            )
            require_fresh_native_manifest(manifest)
            # Preflight the exact retained private input before the console
            # starts. A source copy that was replaced or altered after the
            # manifest was sealed cannot become an implicit write target.
            prewrite_dwg = workspace.open_validated_external_file_read_lease(
                private_dwg,
                allow_replacement=False,
            )
            try:
                _, prewrite_source = _private_dwg_source_from_lease(
                    workspace,
                    private_dwg,
                    prewrite_dwg,
                )
                if (
                    prewrite_source
                    != manifest["expected_prewrite_output_copy_binding"]
                ):
                    raise PipelineError(
                        ErrorCode.NATIVE_DOCUMENT_CHANGED,
                        "private prewrite DWG differs from manifest binding",
                    )
            finally:
                prewrite_dwg.close()
            write_outcome = run_core_console(
                workspace=workspace,
                private_dwg=private_dwg,
                manifest_path=manifest_path,
                config=checked_config,
                mode="write",
                component_leases=component_leases,
            )
            saved_dwg = None
            saved_dwg_retained = False
            try:
                # Write mode is the single explicit contract that may replace
                # the prepared copy. Before adopting that replacement, hold a
                # no-write/no-delete lease and prove owner/DACL, identity,
                # size, header, and the Core Console result binding.
                saved_dwg = workspace.open_validated_external_file_read_lease(
                    private_dwg,
                    allow_replacement=(
                        manifest["final_output_constraints"][
                            "file_identity_transition_policy"
                        ]
                        == "replacement_allowed"
                    ),
                    allow_content_change=True,
                )
                saved_binding, saved_source = _private_dwg_source_from_lease(
                    workspace,
                    private_dwg,
                    saved_dwg,
                )
                require_final_output_binding(
                    manifest,
                    saved_source,
                    private_output_path=private_dwg,
                    private_workspace_root=workspace.path,
                    error_code=ErrorCode.NATIVE_READBACK_INVALID,
                )
                write_result = validate_console_result(
                    manifest,
                    write_outcome.artifact,
                    run_id=write_outcome.run_id,
                )
                if (
                    write_result["final_document_binding"]["output_copy_binding"]
                    != saved_source
                ):
                    raise PipelineError(
                        ErrorCode.NATIVE_CONSOLE_RESULT_INVALID,
                        "console result does not bind saved private output",
                    )
                workspace.adopt_external_file(
                    private_dwg,
                    opened=saved_dwg,
                    allow_replacement=(
                        manifest["final_output_constraints"][
                            "file_identity_transition_policy"
                        ]
                        == "replacement_allowed"
                    ),
                    allow_content_change=True,
                    expected_binding=saved_binding,
                )
                # Keep this exact validated handle through the independent
                # readback, its evidence validation, and the public staging
                # stream. A second process can still read it but cannot write,
                # delete, or replace it while this lease remains live.
                workspace.retain_opened_file(saved_dwg)
                saved_dwg_retained = True

                # A separate process and fixed readback command are mandatory.
                require_fresh_native_manifest(manifest)
                readback_outcome = run_core_console(
                    workspace=workspace,
                    private_dwg=private_dwg,
                    manifest_path=manifest_path,
                    config=checked_config,
                    mode="readback",
                    component_leases=component_leases,
                )
                after_export = geometry_from_console_export(
                    manifest,
                    readback_outcome.artifact,
                    run_id=readback_outcome.run_id,
                    result=write_result,
                )
                verify_native_transition(
                    manifest,
                    after_export,
                    console_result=write_result,
                )
                saved_binding, saved_source = _require_readback_matches_private_copy(
                    workspace,
                    private_dwg,
                    saved_dwg,
                    after_export,
                    manifest=manifest,
                    private_workspace_root=workspace.path,
                    expected_final_document_binding=write_result[
                        "final_document_binding"
                    ],
                    expected_binding=saved_binding,
                )
                component_leases.require_bindings()
                _require_native_source_unchanged(source_lease, checked_audit["source"])

                # Phase 2: no unregistered converter sidecar, reparse point, or
                # replacement may remain before public staging begins.  The
                # context manager repeats this proof while deleting its private
                # children, because a hostile/external process can still race the
                # interval before ``__exit__``.
                workspace.require_exact_inventory()

                # Revalidate immediately before streaming. The staged-output
                # lease reuses the same held DWG handle rather than reopening
                # its filename and thereby recreating a save/publication race.
                saved_binding, saved_source = _private_dwg_source_from_lease(
                    workspace,
                    private_dwg,
                    saved_dwg,
                    expected_binding=saved_binding,
                )
                require_final_output_binding(
                    manifest,
                    saved_source,
                    private_output_path=private_dwg,
                    private_workspace_root=workspace.path,
                    error_code=ErrorCode.NATIVE_READBACK_INVALID,
                )
                if (
                    saved_source
                    != write_result["final_document_binding"]["output_copy_binding"]
                ):
                    raise PipelineError(
                        ErrorCode.NATIVE_READBACK_INVALID,
                        "private output drifted after readback",
                    )
                publication = stage_publication_transaction(
                    _staged_private_dwg_lease(
                        workspace,
                        saved_dwg,
                        saved_binding,
                        saved_source,
                    ),
                    output_targets.targets[0],
                )
                output_description = _native_output_description(
                    output_targets.targets[0].destination,
                    publication.output.owned,
                    publication.output.binding,
                    output_targets.targets[0].backend,
                    final_name_visible=False,
                )
                verification = build_native_verification(
                    manifest,
                    after_export,
                    output_description,
                    result=write_result,
                    console_export=readback_outcome.artifact,
                )
                publication.stage_artifact(
                    output_targets.targets[1],
                    canonical_json_bytes(verification),
                    private=True,
                )
            finally:
                if saved_dwg is not None and not saved_dwg_retained:
                    try:
                        saved_dwg.close()
                    except (OSError, OwnershipError) as close_error:
                        raise PipelineError(
                            ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                            "private saved DWG lease cleanup failed",
                        ) from close_error

        # Phase 4 has completed only after ``PrivateWorkspace.__exit__``
        # deleted every exact private child.  No public final path exists
        # before this point.
        if publication is None or verification is None:
            raise PipelineError(
                ErrorCode.NATIVE_VERIFICATION_INVALID,
                "native publication staging was incomplete",
            )

        # Phase 5: make the pair visible through no-replace renames.  The
        # source lease remains live through commit and any retained-handle
        # rollback performed by the transaction.
        published_output, _published_verification = publication.commit(
            source_binding=lambda: _require_native_source_unchanged(
                source_lease,
                checked_audit["source"],
            ),
        )
        # The verification JSON was prepared while the invisible staged
        # output handle was retained. Rebind after both no-replace renames so
        # the artifact demonstrably names the actual public DWG bytes, not
        # merely a planned destination.
        published_description = _native_output_description(
            published_output.path,
            published_output.owned,
            published_output.binding,
            published_output.backend,
            final_name_visible=True,
        )
        require_published_output_binding(verification, published_description)
        post_rename_failure = _close_post_rename_resources(
            client=client,
            source_lease=source_lease,
            output_targets=output_targets,
            component_leases=component_leases,
        )
        # Every close has been attempted.  Do not let ``finally`` retry a
        # partly consumed handle after a close that reported late failure.
        client = None
        source_lease = None
        output_targets = None
        component_leases = None
        if post_rename_failure is not None:
            try:
                publication.abort(post_rename_failure)
            except PipelineError as rollback_failure:
                # PUBLICATION_ROLLBACK_FAILURE is a dedicated fatal recovery
                # outcome; no verification result can escape this branch.
                raise rollback_failure from post_rename_failure
            raise post_rename_failure
        publication.finalize()
        completed = True
        return NativeApplyResult(verification=verification)
    except BaseException as error:
        if publication is not None:
            try:
                publication.abort(error)
            except PipelineError as cleanup_error:
                raise cleanup_error from error
        raise
    finally:
        cleanup_failure: BaseException | None = None
        if client is not None:
            try:
                client.close()
            except BaseException as error:
                cleanup_failure = error
        if source_lease is not None:
            try:
                source_lease.close()
            except BaseException as error:
                if cleanup_failure is None:
                    cleanup_failure = error
        if output_targets is not None:
            try:
                output_targets.close()
            except BaseException as error:
                if cleanup_failure is None:
                    cleanup_failure = error
        if component_leases is not None:
            try:
                component_leases.close()
            except BaseException as error:
                if cleanup_failure is None:
                    cleanup_failure = error
        if cleanup_failure is not None and not completed:
            if isinstance(cleanup_failure, PipelineError):
                raise cleanup_failure
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "native apply cleanup did not retain component trust",
            ) from cleanup_failure


def _require_native_source_unchanged(
    source_lease: Any,
    expected: Mapping[str, Any],
) -> None:
    """Rebind the original source through its retained handle before publish."""

    if source_lease is None or native_source_from_lease(source_lease) != dict(expected):
        raise PipelineError(ErrorCode.SOURCE_CHANGED_DURING_RUN, "native source changed")


def _private_dwg_source_from_lease(
    workspace: PrivateWorkspace,
    private_dwg: Path,
    opened: OwnedPath,
    *,
    expected_binding: OwnedPathBinding | None = None,
) -> tuple[OwnedPathBinding, dict[str, Any]]:
    """Describe a private saved DWG only through its retained validated lease."""

    try:
        binding = workspace.validate_retained_private_file(
            private_dwg,
            opened,
            expected_binding=expected_binding,
        )
        final_path = opened.final_path()
        header = opened.read_prefix(6).decode("ascii", errors="strict")
        # Capture again after the header read; owner/DACL and content must
        # still bind the exact same file before any result or public copy can
        # rely on this description.
        binding = workspace.validate_retained_private_file(
            private_dwg,
            opened,
            expected_binding=binding,
        )
        if (
            binding.is_directory
            or binding.sha256 is None
            or binding.byte_size is None
            or _DWG_HEADER.fullmatch(header) is None
        ):
            raise PipelineError(
                ErrorCode.NATIVE_READBACK_INVALID,
                "private saved output is not a bound DWG",
            )
        return binding, {
            "format": "DWG",
            "sha256": binding.sha256,
            "byte_size": binding.byte_size,
            "path_fingerprint": sha256(
                normalize_nfc_text(str(final_path)).encode("utf-8")
            ).hexdigest(),
            "file_identity_fingerprint": binding.file_identity_fingerprint,
            "dwg_header_signature": header,
        }
    except PipelineError:
        raise
    except Exception as error:
        raise PipelineError(
            ErrorCode.NATIVE_READBACK_INVALID,
            "private output cannot be bound",
        ) from error


def _staged_private_dwg_lease(
    workspace: PrivateWorkspace,
    opened: OwnedPath,
    binding: OwnedPathBinding,
    source: Mapping[str, Any],
) -> StagedOutputLease:
    """Project a validated workspace lease into the publication source lease."""

    try:
        return StagedOutputLease(
            path=opened.final_path(),
            owned=opened,
            binding=binding,
            expectation=StagedOutputExpectation(
                sha256=source["sha256"],
                byte_size=source["byte_size"],
                file_identity_fingerprint=source["file_identity_fingerprint"],
                dwg_header_signature=source["dwg_header_signature"],
            ),
            backend=workspace.backend,
        )
    except (KeyError, TypeError, OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_READBACK_INVALID,
            "private output publication lease is invalid",
        ) from error


def _require_readback_matches_private_copy(
    workspace: PrivateWorkspace,
    private_dwg: Path,
    opened: OwnedPath,
    export: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    private_workspace_root: Path,
    expected_final_document_binding: Mapping[str, Any],
    expected_binding: OwnedPathBinding,
) -> tuple[OwnedPathBinding, dict[str, Any]]:
    """Bind readback evidence to the retained actual saved copy before publish."""

    binding, expected_source = _private_dwg_source_from_lease(
        workspace,
        private_dwg,
        opened,
        expected_binding=expected_binding,
    )
    require_final_output_binding(
        manifest,
        expected_source,
        private_output_path=private_dwg,
        private_workspace_root=private_workspace_root,
        error_code=ErrorCode.NATIVE_READBACK_INVALID,
    )
    if (
        export["source"] != expected_source
        or expected_final_document_binding["output_copy_binding"] != expected_source
        or export["document"]["revision_fingerprint"]
        != expected_final_document_binding["revision_fingerprint"]
        or export["document"]["database_instance_fingerprint"]
        != expected_final_document_binding["database_instance_fingerprint"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_READBACK_INVALID,
            "readback export does not bind private output",
        )
    return binding, expected_source
