"""Fresh output re-audit and preservation verification."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .atomic_output import (
    OutputTargetLeaseSet,
    acquire_existing_output_target_leases,
    acquire_new_output_target_leases,
)

import ezdxf

from .canonical import (
    CreatedFileBinding,
    attach_integrity,
    close_created_file,
    created_file_matches,
    describe_source,
    describe_owned_source,
    format_utc,
    remove_created_file,
    source_matches,
    utc_now,
    write_new_canonical_json,
)
from .audit import build_audit
from .contracts import require_fresh_audit, validate_artifact
from .errors import ErrorCode, PipelineError
from .oda import SUPPORTED_ODA_VERSION, staged_dwg_to_dxf
from .ownership import (
    OwnedPath,
    OwnershipCleanupError,
    OwnershipError,
    OwnershipLostError,
    WindowsFileOwnershipBackend,
)
from .overlay_profile import assess_auxiliary_overlays
from .plan import validate_plan_against_audit
from .snapshots import Snapshot, snapshot_dxf
from .temporary import PrivateWorkspace


class Converter(Protocol):
    """The minimal isolated converter surface used by verification."""

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


def _source_read_lease(path: Path) -> Any:
    """Obtain apply's shared mandatory Windows no-write lease lazily.

    ``apply`` imports this module for postcondition checks, so resolving the
    shared primitive only when verification runs avoids an import cycle.
    """

    from .apply import _source_read_lease as shared_source_read_lease

    return shared_source_read_lease(path)


@contextmanager
def _output_read_lease(output_path: Path) -> Any:
    """Hold the mandatory Windows no-write lease used by phase-two apply."""

    if os.name != "nt":
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "Windows output handle semantics are required",
        )
    with _source_read_lease(output_path):
        yield


def _require_windows_backend() -> WindowsFileOwnershipBackend:
    """Build the mandatory retained-handle backend for public verification."""

    if os.name != "nt":
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "real DWG verification requires Windows handle semantics",
        )
    try:
        return WindowsFileOwnershipBackend()
    except OwnershipCleanupError as error:
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "Windows handle-safe verification is unavailable",
        ) from error


def _output_binding_projection(binding: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact current-DWG fields, excluding evidence timestamp."""

    return {
        key: binding[key]
        for key in (
            "format",
            "sha256",
            "byte_size",
            "path_fingerprint",
            "file_identity_fingerprint",
            "dwg_header_signature",
            "version_mapping",
        )
    }


def _require_output_binding(output_path: Path, expected: Mapping[str, Any]) -> None:
    """Fail closed if a verified output changed after its initial binding."""

    if not source_matches(output_path, _output_binding_projection(expected)):
        raise PipelineError(
            ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
            "output changed during verification",
        )


def _require_owned_verification_artifact(binding: CreatedFileBinding) -> None:
    """Require the path still to name the exact artifact this call created."""

    if not created_file_matches(binding):
        raise PipelineError(
            ErrorCode.VERIFICATION_ARTIFACT_OWNERSHIP_LOST,
            "verification artifact ownership was lost",
        )


def _remove_owned_verification_artifact(binding: CreatedFileBinding) -> None:
    """Remove only the exact artifact that this verification invocation created."""

    try:
        remove_created_file(binding)
    except OwnershipLostError as error:
        raise PipelineError(
            ErrorCode.VERIFICATION_ARTIFACT_OWNERSHIP_LOST,
            "verification artifact ownership was lost",
        ) from error
    except (OwnershipCleanupError, OSError) as error:
        raise PipelineError(
            ErrorCode.VERIFICATION_ARTIFACT_CLEANUP_FAILURE,
            "verification artifact cleanup failed",
        ) from error


def _require_toolchain(audit: Mapping[str, Any], converter: Converter) -> None:
    if (
        converter.version != SUPPORTED_ODA_VERSION
        or audit["toolchain"]["oda_file_converter"]["version"] != converter.version
        or ezdxf.__version__ != "1.4.4"
        or audit["toolchain"]["ezdxf"]["version"] != ezdxf.__version__
    ):
        raise PipelineError(ErrorCode.TOOL_VERSION_MISMATCH, "toolchain differs from audit")


def _content_counts(snapshot: Snapshot) -> Counter[str]:
    return Counter(record.content_fingerprint for record in snapshot.records)


def assert_postconditions(
    before_snapshot: Snapshot,
    after_snapshot: Snapshot,
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Require a validated audit-plan state transition and nothing else.

    This pure snapshot function never loads or writes a path.  Revalidating
    its artifact inputs keeps direct platform-neutral tests from treating an
    arbitrary mapping as mutation authorization.
    """

    checked_audit = validate_artifact("audit", audit)
    checked_plan = validate_plan_against_audit(checked_audit, plan)
    expected = checked_plan["expected_after"]
    before_records = before_snapshot.records_by_handle
    after_records = after_snapshot.records_by_handle
    target_handles = {
        operation["target"]["handle"]
        for operation in checked_plan["operations"]
    }
    for operation in checked_plan["operations"]:
        target = operation["target"]
        record = before_records.get(target["handle"])
        if record is None:
            raise PipelineError(ErrorCode.MISSING_HANDLE, "planned handle is missing")
        if (
            record.entity_type != "TEXT"
            or record.layout != "modelspace"
        ):
            raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "planned entity is unsafe")
        if (
            record.identity_fingerprint != target["expected_before_fingerprint"]
            or record.content_fingerprint
            != target["expected_before_content_fingerprint"]
        ):
            raise PipelineError(ErrorCode.CHANGED_ENTITY, "planned entity changed")
        if target["handle"] in after_records:
            raise PipelineError(ErrorCode.RE_AUDIT_MISMATCH, "target remained after mutation")

    paired_profile = assess_auxiliary_overlays(after_snapshot)
    if (
        paired_profile.paired_right_panel_digest
        != expected["paired_right_panel_digest"]
    ):
        raise PipelineError(ErrorCode.RE_AUDIT_MISMATCH, "paired right panel changed")
    actual = after_snapshot.preservation_state(
        paired_right_panel_digest=paired_profile.paired_right_panel_digest
    )
    if actual != expected:
        raise PipelineError(ErrorCode.RE_AUDIT_MISMATCH, "non-target preservation mismatch")

    before_content = _content_counts(before_snapshot)
    after_content = _content_counts(after_snapshot)
    for operation in checked_plan["operations"]:
        content = operation["target"]["expected_before_content_fingerprint"]
        if before_content[content] - after_content[content] != 1:
            raise PipelineError(ErrorCode.RE_AUDIT_MISMATCH, "target content delta mismatch")
    if any(assessment.actionable for assessment in paired_profile.assessments):
        raise PipelineError(ErrorCode.RE_AUDIT_MISMATCH, "overlay condition remains actionable")
    if set(before_records) - target_handles != set(after_records):
        raise PipelineError(ErrorCode.RE_AUDIT_MISMATCH, "unexpected entity set change")
    return actual


def build_verification(
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
    actual_after: Mapping[str, Any],
    *,
    output_binding: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a signed evidence artifact after all postconditions pass."""

    current_time = now or utc_now()
    operation_results = [
        {
            "operation_id": operation["operation_id"],
            "target_absent": True,
            "content_fingerprint_delta": -1,
            "no_added_entities": True,
            "non_target_manifest_preserved": True,
            "paired_right_panel_preserved": True,
            "re_audit_overlay_condition_cleared": True,
        }
        for operation in plan["operations"]
    ]
    artifact = {
        "schema_version": "liang-pingfa/verification/v1",
        "verification_id": f"verification-{uuid4().hex}",
        "created_at": format_utc(current_time),
        "audit_binding": {
            "audit_id": audit["audit_id"],
            "audit_integrity_sha256": audit["integrity"]["sha256"],
        },
        "plan_binding": {
            "plan_id": plan["plan_id"],
            "plan_integrity_sha256": plan["integrity"]["sha256"],
        },
        "output": {
            "format": "DWG",
            "dwg_header_signature": "AC1032",
            "version_mapping": "AC1032/R2018",
        },
        # This is evidence of one exact verified DWG, not authorization for a
        # future edit. Consumers must recompute it against their current file.
        "output_binding": dict(output_binding),
        "expected_after": plan["expected_after"],
        "actual_after": dict(actual_after),
        "operation_results": operation_results,
        "passed": True,
    }
    return validate_artifact("verification", attach_integrity(artifact))


def output_binding_for_description(
    description: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the evidence-only current-output binding payload."""

    return {
        **_output_binding_projection(description),
        "verified_at": format_utc(now or utc_now()),
    }


def verification_artifact_matches_output(
    artifact: Mapping[str, Any],
    output_path: Path,
) -> bool:
    """Return whether a verification artifact still describes this exact DWG.

    This helper is intentionally advisory and has no mutation capability. It
    does not turn verification evidence into authorization for any edit.
    """

    try:
        checked = validate_artifact("verification", artifact)
        current = describe_source(output_path).to_artifact()
        return (
            _output_binding_projection(checked["output_binding"])
            == current
        )
    except (PipelineError, OSError, KeyError, TypeError):
        return False


def _verification_artifact_matches_owned_output(
    artifact: Mapping[str, Any],
    output_path: Path,
    opened_output: OwnedPath,
    windows_backend: WindowsFileOwnershipBackend,
) -> bool:
    """Compare evidence through the retained Windows output handle."""

    try:
        checked = validate_artifact("verification", artifact)
        current = describe_owned_source(
            output_path,
            opened_output,
            windows_backend,
        ).to_artifact()
        return _output_binding_projection(checked["output_binding"]) == current
    except (PipelineError, OSError, KeyError, TypeError):
        return False


def verify_dwg(
    output_path: Path,
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
    converter: Converter,
    *,
    verification_output_path: Path | None = None,
) -> dict[str, Any]:
    """Reconvert an output DWG and prove every planned postcondition.

    The optional artifact path exists for the public CLI: its passed artifact
    is written while the output lease remains held, then removed if the final
    binding check detects a race.
    """

    windows_backend = _require_windows_backend()
    checked_audit = validate_artifact("audit", audit)
    require_fresh_audit(checked_audit)
    checked_plan = validate_plan_against_audit(checked_audit, plan)
    _require_toolchain(checked_audit, converter)
    if output_path.suffix.casefold() != ".dwg":
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "verify requires a DWG")
    published_verification: CreatedFileBinding | None = None
    output_targets = acquire_existing_output_target_leases(
        (output_path,),
        backend=windows_backend,
    )
    bound_output_path = output_targets.targets[0].destination
    artifact_targets: OutputTargetLeaseSet | None = None
    if verification_output_path is not None:
        try:
            artifact_targets = acquire_new_output_target_leases(
                (verification_output_path,),
                backend=windows_backend,
                existing_parents=(output_targets.targets[0].parent,),
            )
        except BaseException:
            output_targets.close()
            raise
    try:
        # The lease is acquired before the first hash/path/identity/header
        # binding and remains held through artifact publication and final return.
        with _output_read_lease(bound_output_path):
            output_targets.targets[0].require_existing_destination()
            output_description = describe_source(bound_output_path)
            if output_description.dwg_header_signature != "AC1032":
                raise PipelineError(
                    ErrorCode.UNSUPPORTED_VERSION, "output version mismatch"
                )
            with PrivateWorkspace(prefix="liang-pingfa-verify-") as workspace:
                dxf_path = staged_dwg_to_dxf(
                    bound_output_path,
                    workspace,
                    converter,  # type: ignore[arg-type]
                    stage_name="output-re-audit",
                )
                after_snapshot = snapshot_dxf(dxf_path)
                output_audit = build_audit(
                    after_snapshot,
                    output_description,
                    oda_version=converter.version,
                )
                # The expected pre-state is recoverable from the audit
                # manifest. It is independent of output bytes and therefore
                # cannot authorize mutation.
                pre_snapshot = Snapshot(
                    records=tuple(
                        _record_from_manifest(item)
                        for item in checked_audit["inventory"]["entity_manifest"]
                    ),
                    layer_manifest_digest=checked_audit["inventory"][
                        "layer_manifest_digest"
                    ],
                    table_style_manifest_digest=checked_audit["inventory"][
                        "table_style_manifest_digest"
                    ],
                    header_manifest_digest=checked_audit["inventory"][
                        "header_manifest_digest"
                    ],
                    raw_header_manifest_digest=checked_audit["inventory"][
                        "raw_header_manifest_digest"
                    ],
                    objects_manifest_digest=checked_audit["inventory"][
                        "objects_manifest_digest"
                    ],
                    classes_manifest_digest=checked_audit["inventory"][
                        "classes_manifest_digest"
                    ],
                    raw_classes_manifest_digest=checked_audit["inventory"][
                        "raw_classes_manifest_digest"
                    ],
                    raw_classes_multiset_digest=checked_audit["inventory"][
                        "raw_classes_multiset_digest"
                    ],
                    raw_classes_record_count=checked_audit["inventory"][
                        "raw_classes_record_count"
                    ],
                    acdsdata_manifest_digest=checked_audit["inventory"][
                        "acdsdata_manifest_digest"
                    ],
                    raw_section_structure_digest=checked_audit["inventory"][
                        "raw_section_structure_digest"
                    ],
                    bounds_fingerprint=checked_audit["fingerprints"][
                        "bounds_fingerprint"
                    ],
                    bounds_has_data=checked_audit["fingerprints"]["bounds_has_data"],
                )
                actual = assert_postconditions(
                    pre_snapshot, after_snapshot, checked_audit, checked_plan
                )
                if output_audit["audited_targets"]:
                    raise PipelineError(
                        ErrorCode.RE_AUDIT_MISMATCH, "output remains actionable"
                    )

            output_targets.targets[0].require_existing_destination()
            _require_output_binding(
                bound_output_path,
                output_description.to_artifact(),
            )
            output_binding = output_binding_for_description(
                output_description.to_artifact(),
            )
            verification = build_verification(
                checked_audit,
                checked_plan,
                actual,
                output_binding=output_binding,
            )
            if not verification_artifact_matches_output(
                verification,
                bound_output_path,
            ):
                raise PipelineError(
                    ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
                    "verification evidence does not match current output",
                )
            # This rehash occurs under the no-write/delete lease immediately
            # before artifact publication. It must equal the DWG that was
            # converted into the verified private DXF.
            output_targets.targets[0].require_existing_destination()
            _require_output_binding(bound_output_path, output_binding)
            if verification_output_path is not None:
                assert artifact_targets is not None
                try:
                    created_binding = write_new_canonical_json(
                        artifact_targets.targets[0].destination,
                        verification,
                        backend=windows_backend,
                        retain_handle=True,
                        existing_parents=(
                            artifact_targets.targets[0].parent,
                        ),
                    )
                except FileExistsError as error:
                    raise PipelineError(
                        ErrorCode.OUTPUT_EXISTS,
                        "verification artifact appeared during publication",
                    ) from error
                except (OSError, OwnershipError) as error:
                    raise PipelineError(
                        ErrorCode.VERIFICATION_ARTIFACT_CLEANUP_FAILURE,
                        "verification artifact publication failed",
                    ) from error
                if not isinstance(created_binding, CreatedFileBinding):
                    raise PipelineError(
                        ErrorCode.INTERNAL_ERROR,
                        "verification artifact has no ownership binding",
                    )
                published_verification = created_binding
            # Recheck after publication and immediately before success return.
            output_targets.targets[0].require_existing_destination()
            _require_output_binding(bound_output_path, output_binding)
            if published_verification is not None:
                _require_owned_verification_artifact(published_verification)
                try:
                    close_created_file(published_verification)
                except (OSError, OwnershipError) as error:
                    raise PipelineError(
                        ErrorCode.VERIFICATION_ARTIFACT_CLEANUP_FAILURE,
                        "verification artifact handle cannot be released",
                    ) from error
                published_verification = None
            return verification
    except BaseException as error:
        if published_verification is not None:
            try:
                _remove_owned_verification_artifact(published_verification)
            except PipelineError as cleanup_error:
                raise cleanup_error from error
        raise
    finally:
        try:
            if artifact_targets is not None:
                artifact_targets.close()
        finally:
            output_targets.close()


def _record_from_manifest(item: Mapping[str, Any]) -> Any:
    """Build a geometry-free record only for postcondition fingerprint checks."""

    from .snapshots import EntityRecord

    return EntityRecord(
        handle=item["handle"],
        entity_type=item["entity_type"],
        layout=item["layout"],
        sequence_index=int(item["sequence_index"]),
        container_fingerprint=item["container_fingerprint"],
        owner_fingerprint=item["owner_fingerprint"],
        layer_fingerprint=item["layer_fingerprint"],
        identity_fingerprint=item["identity_fingerprint"],
        content_fingerprint=item["content_fingerprint"],
        layer_name="",
        entity_visible=bool(item["entity_visible"]),
        layer_visible=bool(item["layer_visible"]),
        entity_transparency=item["entity_transparency"],
        layer_transparency=float(item["layer_transparency"]),
        plane_elevation=None,
        anchor=None,
        bounds=None,
    )
