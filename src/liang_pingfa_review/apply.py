"""Phase-two, audit-bound temporary DXF mutation and DWG publication."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
import os
from pathlib import Path
from typing import Any, Iterator, Protocol

import ezdxf

from .atomic_output import (
    OutputTargetLease,
    StagedOutputLease,
    acquire_new_dwg_output_target,
    acquire_staged_output_lease,
    publish_no_replace,
)
from .audit import build_audit
from .canonical import (
    acquire_source_lease,
    canonical_sha256,
    describe_leased_source,
    describe_owned_source,
    source_lease_matches,
)
from .contracts import (
    audit_semantic_projection,
    require_fresh_audit,
    validate_artifact,
)
from .errors import ErrorCode, PipelineError
from .oda import SUPPORTED_ODA_VERSION, staged_dwg_to_dxf, staged_dxf_to_dwg
from .ownership import (
    OwnedPath,
    OwnedPathBinding,
    OwnershipCleanupError,
    OwnershipError,
    SourcePathLease,
    WindowsFileOwnershipBackend,
)
from .plan import validate_plan_against_audit
from .snapshots import (
    Snapshot,
    _record,
    open_preflighted_dxf,
    snapshot_document,
    snapshot_dxf,
)
from .temporary import PrivateWorkspace
from .verify import (
    assert_postconditions,
    _verification_artifact_matches_owned_output,
    build_verification,
    output_binding_for_description as verify_output_binding,
)


class Converter(Protocol):
    """The narrow staged-converter interface used by phase two."""

    version: str

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        """Convert one private staged input."""


class ApplyResult(dict[str, Any]):
    """Verification evidence plus the exact handle-bound published output.

    The mapping portion remains the public verification artifact shape. The
    private attribute is intentionally not serialized: local-regression
    cleanup needs the precise binding captured under the retained publication
    handle, while callers must never treat it as reusable authorization data.
    """

    def __init__(
        self,
        verification: Mapping[str, Any],
        *,
        published_output_binding: OwnedPathBinding | None,
    ) -> None:
        super().__init__(verification)
        self.published_output_binding = published_output_binding


@contextmanager
def _source_read_lease(
    source_path: Path,
    *,
    backend: WindowsFileOwnershipBackend | None = None,
) -> Iterator[SourcePathLease]:
    """Hold one source's no-follow ancestors and immutable file bytes.

    The source lease is the only authority for phase-two source binding.  It
    is acquired before audit comparison, conversion, mutation, publication,
    and rollback, and never falls back to a separately resolved pathname.
    """

    try:
        lease = acquire_source_lease(source_path, backend=backend)
    except PipelineError as error:
        if error.code == ErrorCode.INVALID_ARGUMENT:
            raise
        raise PipelineError(
            ErrorCode.SOURCE_LEASE_UNAVAILABLE,
            "exclusive source read lease is unavailable",
        ) from error
    try:
        yield lease
    finally:
        try:
            lease.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.SOURCE_LEASE_UNAVAILABLE,
                "exclusive source read lease cannot be released",
            ) from error


def _require_source_binding(
    source_lease: SourcePathLease,
    expected: Mapping[str, Any],
) -> None:
    """Revalidate all audited source binding fields at the commit boundary."""

    if not source_lease_matches(source_lease, expected):
        raise PipelineError(
            ErrorCode.SOURCE_CHANGED_DURING_RUN,
            "source changed at publication commit boundary",
        )


def _require_windows_backend() -> WindowsFileOwnershipBackend:
    """Build the mandatory retained-handle backend for public DWG mutation."""

    if os.name != "nt":
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "real DWG apply requires Windows handle semantics",
        )
    try:
        return WindowsFileOwnershipBackend()
    except OwnershipCleanupError as error:
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "Windows handle-safe publication is unavailable",
        ) from error


def _require_toolchain(audit: Mapping[str, Any], converter: Converter) -> None:
    if (
        converter.version != SUPPORTED_ODA_VERSION
        or audit["toolchain"]["oda_file_converter"]["version"] != converter.version
        or ezdxf.__version__ != "1.4.4"
        or audit["toolchain"]["ezdxf"]["version"] != ezdxf.__version__
    ):
        raise PipelineError(ErrorCode.TOOL_VERSION_MISMATCH, "toolchain differs from audit")


def _validate_targets_before_mutation(snapshot: Snapshot, plan: Mapping[str, Any]) -> None:
    """Prove exact planned entities are still direct, safe Modelspace TEXT."""

    records = snapshot.records_by_handle
    handles: list[str] = []
    for operation in plan["operations"]:
        target = operation["target"]
        handle = target["handle"]
        handles.append(handle)
        record = records.get(handle)
        if record is None:
            raise PipelineError(ErrorCode.MISSING_HANDLE, "planned handle is absent")
        if record.entity_type != "TEXT" or record.layout != "modelspace":
            raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "planned entity is unsafe")
        if (
            record.identity_fingerprint != target["expected_before_fingerprint"]
            or record.content_fingerprint
            != target["expected_before_content_fingerprint"]
        ):
            raise PipelineError(ErrorCode.CHANGED_ENTITY, "planned entity fingerprint changed")
    if len(handles) != len(set(handles)):
        raise PipelineError(ErrorCode.DUPLICATE_TARGET, "duplicate mutation target")


def _assert_live_safe_text(entity: Any, modelspace: Any) -> None:
    if (
        entity.dxftype() != "TEXT"
        or str(entity.dxf.get("owner", "")) != str(modelspace.layout_key)
        or getattr(entity, "xdata", None) is not None
        or getattr(entity, "appdata", None) is not None
        or bool(getattr(entity, "has_extension_dict", False))
        or getattr(entity, "proxy_graphic", None)
    ):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "unsafe live mutation target")


def _live_target_entity(
    document: Any,
    modelspace: Any,
    target: Mapping[str, Any],
) -> Any:
    """Load and fingerprint the exact in-memory entity immediately before deletion."""

    handle = target["handle"]
    entity = document.entitydb.get(handle)
    if entity is None:
        raise PipelineError(ErrorCode.MISSING_HANDLE, "live target is absent")
    _assert_live_safe_text(entity, modelspace)
    record = _record(
        entity,
        layout="modelspace",
        sequence_index=0,
        container_name=str(modelspace.name),
        dxfversion=document.dxfversion,
    )
    if (
        record.identity_fingerprint != target["expected_before_fingerprint"]
        or record.content_fingerprint != target["expected_before_content_fingerprint"]
    ):
        raise PipelineError(ErrorCode.CHANGED_ENTITY, "live target fingerprint changed")
    return entity


class _StagedDwgLeaseRegistrar:
    """Register generated DWGs through no-write leases, never path reopening."""

    def __init__(
        self,
        workspace: PrivateWorkspace,
        backend: WindowsFileOwnershipBackend,
    ) -> None:
        self._workspace = workspace
        self._backend = backend
        self._leases: dict[Path, StagedOutputLease] = {}

    def __call__(self, candidate: Path) -> Path:
        """Lease and cleanup-register every converter candidate before return."""

        # ``acquire_staged_output_lease`` performs the only resolve/open
        # sequence. Do not call describe_source(), hash, or inspect headers by
        # pathname before this retained no-write/delete handle exists.
        candidate_key = os.path.normcase(os.path.abspath(os.fspath(candidate)))
        for existing_path, existing in self._leases.items():
            if (
                candidate.name == existing_path.name
                or os.path.normcase(os.fspath(existing_path)) == candidate_key
            ):
                existing.require_binding()
                return existing.path
        lease = acquire_staged_output_lease(candidate, backend=self._backend)
        try:
            self._workspace.track_opened_file(lease.owned)
        except BaseException:
            lease.close()
            raise
        self._leases[lease.path] = lease
        return lease.path

    def lease_for(self, candidate: Path) -> StagedOutputLease:
        """Return the exact retained lease for the converter's selected output."""

        for path, lease in self._leases.items():
            if path == candidate:
                lease.require_binding()
                return lease
        raise PipelineError(
            ErrorCode.RE_AUDIT_MISMATCH,
            "generated staged output was not leased",
        )

    def abort(self) -> None:
        """Release leases before workspace cleanup handles a conversion failure."""

        failure: PipelineError | None = None
        for lease in reversed(tuple(self._leases.values())):
            try:
                lease.close()
            except PipelineError as error:
                if failure is None:
                    failure = error
        self._leases.clear()
        if failure is not None:
            raise failure


def apply_dwg(
    source_path: Path,
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
    confirm_plan: str,
    output_path: Path,
    converter: Converter,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """Run the Windows-only revalidation, mutation, round trip, and publication."""

    windows_backend = _require_windows_backend()
    checked_audit = validate_artifact("audit", audit)
    require_fresh_audit(checked_audit)
    checked_plan = validate_plan_against_audit(checked_audit, plan)
    _require_toolchain(checked_audit, converter)
    if confirm_plan != checked_plan["plan_id"]:
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "plan confirmation does not match")
    with _source_read_lease(source_path, backend=windows_backend) as source_lease:
        output_targets = acquire_new_dwg_output_target(
            source_lease.path,
            output_path,
            backend=windows_backend,
        )
        try:
            if not source_lease_matches(source_lease, checked_audit["source"]):
                raise PipelineError(ErrorCode.STALE_AUDIT, "input differs from audit")
            return _apply_with_bound_output(
                source_lease,
                checked_audit,
                checked_plan,
                output_targets.targets[0],
                converter,
                windows_backend,
                dry_run=dry_run,
            )
        finally:
            output_targets.close()


def _apply_with_bound_output(
    source_lease: SourcePathLease,
    checked_audit: Mapping[str, Any],
    checked_plan: Mapping[str, Any],
    output_target: OutputTargetLease,
    converter: Converter,
    windows_backend: WindowsFileOwnershipBackend,
    *,
    dry_run: bool,
) -> ApplyResult:
    """Perform apply while the caller retains the validated output parent."""

    destination = output_target.destination
    with PrivateWorkspace(prefix="liang-pingfa-apply-") as workspace:
        fresh_dxf = staged_dwg_to_dxf(
            source_lease,
            workspace,
            converter,  # type: ignore[arg-type]
            stage_name="fresh-source",
        )
        # The pre-mutation snapshot and the authorized mutation share one
        # loaded document. Raw preflight, parse, raw congruence, and snapshot
        # construction all happen under one retained DXF byte lease before
        # this document becomes the mutation authority.
        with open_preflighted_dxf(fresh_dxf) as (document, raw_preflight):
            before_snapshot = snapshot_document(document, raw_preflight=raw_preflight)
        fresh_audit = build_audit(
            before_snapshot,
            describe_leased_source(source_lease),
            oda_version=converter.version,
        )
        if canonical_sha256(audit_semantic_projection(fresh_audit)) != canonical_sha256(
            audit_semantic_projection(checked_audit)
        ):
            raise PipelineError(
                ErrorCode.STALE_AUDIT, "fresh audit differs from supplied audit"
            )
        _validate_targets_before_mutation(before_snapshot, checked_plan)
        if not source_lease_matches(source_lease, checked_audit["source"]):
            raise PipelineError(
                ErrorCode.SOURCE_CHANGED_DURING_RUN,
                "source changed before temporary mutation",
            )

        edited_dxf = workspace / "edited.dxf"
        modelspace = document.modelspace()
        live_handles: set[str] = set()
        # Preflight every target before changing the loaded document so a
        # later mismatch cannot leave a partially written DXF.  This mutation
        # remains inside the Windows-only production entry point; no DXF-path
        # mutation helper is packaged.
        for operation in checked_plan["operations"]:
            target = operation["target"]
            handle = target["handle"]
            if handle in live_handles:
                raise PipelineError(
                    ErrorCode.DUPLICATE_TARGET,
                    "duplicate mutation target",
                )
            live_handles.add(handle)
            _live_target_entity(document, modelspace, target)
        for operation in checked_plan["operations"]:
            entity = _live_target_entity(
                document,
                modelspace,
                operation["target"],
            )
            modelspace.delete_entity(entity)
        edited_handle = workspace.create_owned_file(edited_dxf)
        try:
            # Direct serialization into the already-registered retained
            # handle ensures no source-derived DXF byte first lands at an
            # unowned pathname.
            edited_handle.write_text(
                lambda stream: document.write(stream, fmt="asc")
            )
            workspace.seal_owned_file(edited_handle)
        except BaseException as error:
            try:
                workspace.discard_owned_file(edited_handle)
            except PipelineError as cleanup_error:
                raise cleanup_error from error
            if isinstance(error, PipelineError):
                raise
            if isinstance(error, (OSError, IOError, ValueError, ezdxf.DXFError)):
                raise PipelineError(
                    ErrorCode.CONVERSION_FAILURE,
                    "temporary DXF cannot be written",
                ) from error
            raise
        temporary_after_snapshot = snapshot_dxf(edited_dxf)
        assert_postconditions(
            before_snapshot,
            temporary_after_snapshot,
            checked_audit,
            checked_plan,
        )

        staged_registrar = _StagedDwgLeaseRegistrar(workspace, windows_backend)
        try:
            def require_candidate_expected_state(roundtrip_dxf: Path) -> None:
                """Prove each dual DWG candidate is exactly before minus targets."""

                candidate_snapshot = snapshot_dxf(roundtrip_dxf)
                assert_postconditions(
                    before_snapshot,
                    candidate_snapshot,
                    checked_audit,
                    checked_plan,
                )

            staged_dwg = staged_dxf_to_dwg(
                edited_dxf,
                workspace,
                converter,  # type: ignore[arg-type]
                stage_name="edited-to-dwg",
                register_output=staged_registrar,
                expected_state_proof=require_candidate_expected_state,
            )
            staged_output_lease = staged_registrar.lease_for(staged_dwg)
        except BaseException:
            staged_registrar.abort()
            raise
        # This is the first inspection of the generated DWG.  The retained
        # handle is acquired before any pathname hashing, header read, or
        # DWG-to-DXF round trip, closing the staged-file ABA window.
        try:
            staged_output_lease.require_binding()
            output_description = describe_owned_source(
                staged_output_lease.path,
                staged_output_lease.owned,
                windows_backend,
            )
            if output_description.dwg_header_signature != "AC1032":
                raise PipelineError(
                    ErrorCode.UNSUPPORTED_VERSION, "staged output version mismatch"
                )
            # ODA receives the leased pathname only while this no-write/delete
            # handle remains open. If ODA requests incompatible sharing, the
            # conversion fails closed rather than weakening the lease.
            final_dxf = staged_dwg_to_dxf(
                staged_output_lease.path,
                workspace,
                converter,  # type: ignore[arg-type]
                stage_name="roundtrip-output",
            )
            staged_output_lease.require_binding()
            final_snapshot = snapshot_dxf(final_dxf)
            # Constructing a fresh audit drives the profile again; its output
            # is deliberately transient and never an authorization artifact.
            output_audit = build_audit(
                final_snapshot,
                output_description,
                oda_version=converter.version,
            )
            actual_after = assert_postconditions(
                before_snapshot, final_snapshot, checked_audit, checked_plan
            )
            if output_audit["audited_targets"]:
                raise PipelineError(
                    ErrorCode.RE_AUDIT_MISMATCH, "output remains actionable"
                )
            if dry_run:
                # Dry-run evidence binds only the private staged DWG. It is
                # never emitted as an artifact and cannot certify the absent
                # public output path.
                return ApplyResult(
                    build_verification(
                        checked_audit,
                        checked_plan,
                        actual_after,
                        output_binding=verify_output_binding(
                            output_description.to_artifact(),
                        ),
                    ),
                    published_output_binding=None,
                )
            staged_output_lease.require_binding()

            def build_published_verification(
                opened_output: OwnedPath,
                final_binding: OwnedPathBinding,
            ) -> ApplyResult:
                final_description = describe_owned_source(
                    destination,
                    opened_output,
                    windows_backend,
                )
                expected_payload = output_description.to_artifact()
                actual_payload = final_description.to_artifact()
                if any(
                    actual_payload[key] != expected_payload[key]
                    for key in (
                        "format",
                        "sha256",
                        "byte_size",
                        "dwg_header_signature",
                        "version_mapping",
                    )
                ):
                    raise PipelineError(
                        ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
                        "published output differs from verified staged output",
                    )
                if (
                    final_binding.file_identity_fingerprint
                    != final_description.file_identity_fingerprint
                ):
                    raise PipelineError(
                        ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
                        "published output identity differs from held handle",
                    )
                verification = build_verification(
                    checked_audit,
                    checked_plan,
                    actual_after,
                    output_binding=verify_output_binding(
                        final_description.to_artifact(),
                    ),
                )
                if not _verification_artifact_matches_owned_output(
                    verification,
                    destination,
                    opened_output,
                    windows_backend,
                ):
                    raise PipelineError(
                        ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
                        "published verification evidence does not match output",
                    )
                return ApplyResult(
                    verification,
                    published_output_binding=final_binding,
                )

            verification = publish_no_replace(
                staged_output_lease,
                destination,
                source_binding=lambda: _require_source_binding(
                    source_lease, checked_audit["source"]
                ),
                after_commit=build_published_verification,
                backend=windows_backend,
                output_target=output_target,
            )
            if (
                not isinstance(verification, ApplyResult)
                or verification.published_output_binding is None
            ):
                raise PipelineError(
                    ErrorCode.INTERNAL_ERROR,
                    "publication did not return verification evidence",
                )
            return verification
        finally:
            # ``publish_no_replace`` is deliberately allowed to close the
            # lease after its own rollback; this idempotent close covers every
            # pre-publication, dry-run, and round-trip failure before private
            # workspace cleanup tries to remove the staged DWG.
            staged_output_lease.close()
