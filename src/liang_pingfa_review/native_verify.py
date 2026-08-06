"""Exact post-save native readback verification and evidence construction."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .canonical import attach_integrity, canonical_sha256, format_utc, utc_now
from .errors import ErrorCode, PipelineError
from .native_contracts import (
    _embedded_geometry,
    native_container_sequences,
    native_execution_stable_host_binding_digest,
    native_artifact_integrity,
    native_marker_fingerprint,
    PRIVATE_RECORD_CARDINALITY,
    require_active_native_contract,
    translated_geometry_bits,
    validate_native_contract,
)
from .native_audit import native_source_from_lease
from .native_manifest import require_final_output_binding
from .ownership import acquire_source_path_lease, platform_backend


def _precondition_export(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return _embedded_geometry(
        manifest["preconditions_geometry_json"],
        error=ErrorCode.NATIVE_MANIFEST_INVALID,
    )


def _entity_projection(entity: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(entity)
    result.pop("geometry_fingerprint", None)
    result.pop("opaque_state_digest", None)
    return result


def _container_key(entity: Mapping[str, Any]) -> tuple[str, str, str, tuple[str, ...]]:
    space = entity["space"]
    return (
        space["kind"],
        space["layout_handle"] or "",
        space["block_handle"] or "",
        tuple(entity["block_path"]),
    )


def _group_by_container(
    entities: list[Mapping[str, Any]],
) -> dict[tuple[str, str, str, tuple[str, ...]], list[Mapping[str, Any]]]:
    grouped: dict[tuple[str, str, str, tuple[str, ...]], list[Mapping[str, Any]]] = {}
    for entity in entities:
        grouped.setdefault(_container_key(entity), []).append(entity)
    return grouped


def _require_equal_except_geometry(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    fields = (
        "handle",
        "native_type",
        "owner_handle",
        "space",
        "block_path",
        "sequence_index",
        "layer",
        "text",
        "style",
        "height",
        "rotation",
        "overlay_evidence",
    )
    if any(before[field] != after[field] for field in fields):
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "unplanned DBTEXT state change")


def _require_document_tables_unchanged(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    for key in (
        "table_state_digest",
        "layout_state_digest",
        "block_state_digest",
        "document_state_digest",
        "marker_layer_fingerprint",
        "marker_style_fingerprint",
    ):
        if before["document"][key] != after["document"][key]:
            raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "unplanned table/layout/document change")


def _require_owners_unchanged(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    """Protect the complete ordered owner record list, including unused owners."""

    if before["owners"] != after["owners"]:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "protected owner state changed")


def _verify_translate(
    operation: Mapping[str, Any],
    before_by_target: Mapping[str, Mapping[str, Any]],
    after_by_handle: Mapping[str, Mapping[str, Any]],
) -> None:
    before = before_by_target.get(operation["target_id"])
    if before is None:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "translate target lacks precondition")
    after = after_by_handle.get(before["handle"])
    if after is None:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "translated target is absent")
    if before["native_type"] != "DBTEXT":
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "translate target is not DBTEXT")
    _require_equal_except_geometry(before, after)
    try:
        expected_after = translated_geometry_bits(before, operation["delta"])
    except (KeyError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_READBACK_INVALID,
            "translation is not representable for precondition geometry",
        ) from error
    if operation["expected_after"] != expected_after:
        raise PipelineError(
            ErrorCode.NATIVE_READBACK_INVALID,
            "manifest translation transition differs",
        )
    if (
        after["position"] != expected_after["position"]
        or after["bounds"] != expected_after["bounds"]
        or after["segments"] != expected_after["segments"]
    ):
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "translated DBTEXT geometry differs")


def _verify_delete(
    operation: Mapping[str, Any],
    before_by_target: Mapping[str, Mapping[str, Any]],
    after_by_handle: Mapping[str, Mapping[str, Any]],
) -> str:
    before = before_by_target.get(operation["target_id"])
    if before is None:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "delete target lacks precondition")
    if before["handle"] in after_by_handle:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "deleted target remains")
    return before["handle"]


def _marker_matches_operation(
    operation: Mapping[str, Any],
    marker: Mapping[str, Any],
) -> bool:
    if (
        marker["native_type"] != "DBTEXT"
        or marker["owner_handle"] != operation["owner_handle"]
        or marker["space"] != operation["space"]
        or marker["block_path"] != operation["block_path"]
        or marker["sequence_index"] != operation["sequence_index"]
        or marker["text"] != operation["marker_text"]
        or marker["layer"] != operation["layer"]
        or marker["style"] != operation["style"]
        or marker["height"] != operation["height"]
        or marker["rotation"] != operation["rotation"]
        or marker["overlay_evidence"] != operation["overlay_evidence"]
        or marker["position"] != operation["position"]
        or marker["bounds"]["minimum"] != operation["position"]
        or marker["bounds"]["maximum"] != operation["position"]
        or marker["segments"]
    ):
        return False
    actual_fingerprint = native_marker_fingerprint(
        {
            "block_path": marker["block_path"],
            "height": marker["height"],
            "kind": "create_review_marker",
            "layer": marker["layer"],
            "marker_text": marker["text"],
            "overlay_evidence": marker["overlay_evidence"],
            "owner_handle": marker["owner_handle"],
            "position": marker["position"],
            "rotation": marker["rotation"],
            "sequence_index": marker["sequence_index"],
            "space": marker["space"],
            "style": marker["style"],
        }
    )
    return actual_fingerprint == operation["marker_fingerprint"]


def _match_markers(
    operations: list[Mapping[str, Any]],
    additions: list[Mapping[str, Any]],
) -> dict[str, str]:
    """Match every declared marker exactly once without global-addition shortcuts."""

    marker_operations = [
        operation
        for operation in operations
        if operation["kind"] == "create_review_marker"
    ]
    if len(additions) != len(marker_operations):
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "marker cardinality differs")
    remaining = list(additions)
    matched: dict[str, str] = {}
    for operation in marker_operations:
        candidates = [
            marker
            for marker in remaining
            if _marker_matches_operation(operation, marker)
        ]
        if len(candidates) != 1:
            raise PipelineError(
                ErrorCode.NATIVE_READBACK_INVALID,
                "marker does not match exactly one manifest operation",
            )
        marker = candidates[0]
        remaining.remove(marker)
        matched[operation["operation_id"]] = marker["handle"]
    if remaining:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "unmatched marker addition")
    return matched


def _operation_postcondition_digest(
    operation: Mapping[str, Any],
    marker_handle: str | None,
) -> str:
    """Mirror the core's v2 operation-result digest construction."""

    if marker_handle is None:
        return canonical_sha256(operation)
    return canonical_sha256(
        {
            "operation": dict(operation),
            "marker_handle": marker_handle,
        }
    )


def _require_protected_order(
    before_entities: list[Mapping[str, Any]],
    after_entities: list[Mapping[str, Any]],
    *,
    before_containers: list[Mapping[str, Any]],
    after_containers: list[Mapping[str, Any]],
    after_document: Mapping[str, Any],
    after_owners: list[str],
    delete_handles: set[str],
    marker_handles_by_operation: Mapping[str, str],
    operations: list[Mapping[str, Any]],
) -> None:
    """Preserve every existing container/index; markers only append as declared.

    Delete policy is intentionally gap-preserving: the removed record vanishes
    but every non-target retains its original sequence index and relative
    order.  A host that renumbers container records is therefore rejected.
    """

    before_physical = {
        _container_key(container): container for container in before_containers
    }
    after_physical = {
        _container_key(container): container for container in after_containers
    }
    if set(before_physical) != set(after_physical):
        raise PipelineError(
            ErrorCode.NATIVE_READBACK_INVALID,
            "physical container set changed",
        )

    marker_count_by_container: dict[
        tuple[str, str, str, tuple[str, ...]], int
    ] = {}
    for operation in operations:
        if operation["kind"] != "create_review_marker":
            continue
        key = _container_key(
            {
                "space": operation["space"],
                "block_path": operation["block_path"],
            }
        )
        marker_count_by_container[key] = marker_count_by_container.get(key, 0) + 1
    for key, before_container in before_physical.items():
        after_container = after_physical[key]
        expected_count = (
            int(before_container["physical_slot_count"])
            + marker_count_by_container.get(key, 0)
        )
        if (
            before_container["owner_handle"] != after_container["owner_handle"]
            or int(after_container["physical_slot_count"]) != expected_count
        ):
            raise PipelineError(
                ErrorCode.NATIVE_READBACK_INVALID,
                "physical container slot count drift",
            )

    before_by_handle = {entity["handle"]: entity for entity in before_entities}
    after_by_handle = {entity["handle"]: entity for entity in after_entities}
    for handle, before in before_by_handle.items():
        if handle in delete_handles:
            continue
        after = after_by_handle.get(handle)
        if (
            after is None
            or after["sequence_index"] != before["sequence_index"]
            or _container_key(after) != _container_key(before)
        ):
            raise PipelineError(
                ErrorCode.NATIVE_READBACK_INVALID,
                "existing entity sequence or container changed",
            )
    for entity in after_entities:
        physical = after_physical.get(_container_key(entity))
        if (
            physical is None
            or physical["owner_handle"] != entity["owner_handle"]
            or int(entity["sequence_index"])
            >= int(physical["physical_slot_count"])
        ):
            raise PipelineError(
                ErrorCode.NATIVE_READBACK_INVALID,
                "active entity physical index/gap drift",
            )

    before_groups = _group_by_container(before_entities)
    after_groups = _group_by_container(after_entities)
    marker_operations = [
        operation
        for operation in operations
        if operation["kind"] == "create_review_marker"
    ]
    marker_by_container: dict[
        tuple[str, str, str, tuple[str, ...]], list[str]
    ] = {}
    for operation in marker_operations:
        handle = marker_handles_by_operation[operation["operation_id"]]
        marker_by_container.setdefault(
            _container_key(
                {
                    "space": operation["space"],
                    "block_path": operation["block_path"],
                }
            ),
            [],
        ).append(handle)

    expected_by_container: dict[
        tuple[str, str, str, tuple[str, ...]], list[str]
    ] = {}
    for container in set(before_groups) | set(marker_by_container):
        expected_by_container[container] = [
            entity["handle"]
            for entity in before_groups.get(container, [])
            if entity["handle"] not in delete_handles
        ] + marker_by_container.get(container, [])
    expected_containers = {
        container
        for container, handles in expected_by_container.items()
        if handles
    }
    if set(after_groups) != expected_containers:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "container set changed")
    for container in expected_containers:
        expected_handles = expected_by_container[container]
        observed_handles = [
            entity["handle"] for entity in after_groups.get(container, [])
        ]
        if observed_handles != expected_handles:
            raise PipelineError(
                ErrorCode.NATIVE_READBACK_INVALID,
                "container entity order changed",
            )

    # The geometry schema independently recomputes this digest.  Rechecking
    # it here ensures a transition cannot bypass the container-order binding
    # even when a forged export recomputes unrelated document fields.
    sequences = native_container_sequences(after_entities, after_containers)
    if canonical_sha256(sequences) != after_document["container_order_digest"]:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "container order digest drift")
    if canonical_sha256(
        {
            "container_sequences": sequences,
            "document_state_digest": after_document["document_state_digest"],
            "owners": after_owners,
        }
    ) != after_document["protected_order_digest"]:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "protected order digest drift")


def verify_native_transition(
    manifest: Mapping[str, Any],
    after_export: Mapping[str, Any],
    *,
    console_result: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compare raw before/exported-after geometry against the exact allowlist."""

    checked_manifest = require_active_native_contract("manifest", manifest)
    before = _precondition_export(checked_manifest)
    after = require_active_native_contract("geometry", after_export)
    require_final_output_binding(
        checked_manifest,
        after["source"],
        error_code=ErrorCode.NATIVE_READBACK_INVALID,
    )
    if (
        native_execution_stable_host_binding_digest(
            before,
            checked_manifest["marker_policy_binding"],
        )
        != checked_manifest["stable_host_binding_digest"]
        or native_execution_stable_host_binding_digest(
            after,
            checked_manifest["marker_policy_binding"],
        )
        != checked_manifest["stable_host_binding_digest"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_READBACK_INVALID,
            "readback stable host/profile/capability binding differs",
        )
    _require_document_tables_unchanged(before, after)
    _require_owners_unchanged(before, after)
    before_entities = list(before["entities"])
    after_entities = list(after["entities"])
    before_by_handle = {entity["handle"]: entity for entity in before_entities}
    after_by_handle = {entity["handle"]: entity for entity in after_entities}
    before_by_target = {
        __import__(
            "liang_pingfa_review.native_contracts", fromlist=["derive_native_target_id"]
        ).derive_native_target_id(entity): entity
        for entity in before_entities
    }
    delete_handles: set[str] = set()
    results: list[dict[str, Any]] = []
    operations = list(checked_manifest["operations"])
    if len({operation["operation_id"] for operation in operations}) != len(operations):
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "duplicate manifest operation")
    target_ids = [
        operation["target_id"]
        for operation in operations
        if operation["kind"] != "create_review_marker"
    ]
    if len(target_ids) != len(set(target_ids)):
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "duplicate manifest target")
    for operation in operations:
        kind = operation["kind"]
        if kind == "translate_dbtext":
            _verify_translate(operation, before_by_target, after_by_handle)
        elif kind == "delete_auxiliary_overlay_text":
            delete_handles.add(_verify_delete(operation, before_by_target, after_by_handle))
        elif kind != "create_review_marker":
            raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "unknown manifest operation")
        results.append(
            {
                "operation_id": operation["operation_id"],
                "kind": kind,
                "verified": True,
            }
        )

    additions = [
        entity for entity in after_entities if entity["handle"] not in before_by_handle
    ]
    marker_handles_by_operation = _match_markers(operations, additions)
    if console_result is not None:
        checked_result = require_active_native_contract(
            "console_result",
            console_result,
        )
        results_by_operation = {
            item["operation_id"]: item
            for item in checked_result["operation_results"]
        }
        for operation in operations:
            if operation["kind"] != "create_review_marker":
                continue
            receipt = results_by_operation.get(operation["operation_id"])
            if (
                receipt is None
                or receipt["marker_handle"]
                != marker_handles_by_operation[operation["operation_id"]]
            ):
                raise PipelineError(
                    ErrorCode.NATIVE_READBACK_INVALID,
                    "marker append receipt differs from exact readback",
                )
        for result in results:
            if result["kind"] == "create_review_marker":
                result["marker_handle"] = marker_handles_by_operation[
                    result["operation_id"]
                ]
            else:
                result["marker_handle"] = None
    else:
        for result in results:
            result["marker_handle"] = (
                marker_handles_by_operation[result["operation_id"]]
                if result["kind"] == "create_review_marker"
                else None
            )
    marker_handles = set(marker_handles_by_operation.values())
    expected_handles = set(before_by_handle) - delete_handles | marker_handles
    if set(after_by_handle) != expected_handles:
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "unplanned entity add/delete")
    _require_protected_order(
        before_entities,
        after_entities,
        before_containers=list(before["containers"]),
        after_containers=list(after["containers"]),
        after_document=after["document"],
        after_owners=after["owners"],
        delete_handles=delete_handles,
        marker_handles_by_operation=marker_handles_by_operation,
        operations=operations,
    )
    translated_handles = {
        before_by_target[operation["target_id"]]["handle"]
        for operation in operations
        if operation["kind"] == "translate_dbtext"
    }
    for handle, before_entity in before_by_handle.items():
        if handle in delete_handles or handle in translated_handles:
            continue
        after_entity = after_by_handle[handle]
        if _entity_projection(before_entity) != _entity_projection(after_entity):
            raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "non-target geometry changed")
    return results


def validate_console_result(
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Validate the external transaction claim without treating it as proof."""

    checked_manifest = require_active_native_contract("manifest", manifest)
    checked = require_active_native_contract("console_result", result)
    final = checked["final_document_binding"]
    prewrite = checked_manifest["expected_prewrite_revision"]
    transition_policy = checked_manifest["environment"]["write_revision_transition"]
    if (
        checked["run_id"] != run_id
        or checked["manifest_id"] != checked_manifest["manifest_id"]
        or checked["manifest_integrity_sha256"]
        != native_artifact_integrity(checked_manifest)
        or checked["manifest_schema_version"] != checked_manifest["schema_version"]
        or checked["nonce"] != checked_manifest["nonce"]
        or checked["final_revision_fingerprint"] != final["revision_fingerprint"]
        or checked["transaction"]
        != {"preflight": "passed", "outcome": "committed", "rollback": "not_required"}
    ):
        raise PipelineError(ErrorCode.NATIVE_CONSOLE_RESULT_INVALID, "console transaction claim differs")
    require_final_output_binding(
        checked_manifest,
        final["output_copy_binding"],
        error_code=ErrorCode.NATIVE_CONSOLE_RESULT_INVALID,
    )
    if transition_policy == "save_reopen_changes_revision":
        transition_valid = (
            checked["final_revision_transition"] == "save_reopen_changed"
        )
    elif transition_policy == "preserved_by_plugin_capability":
        transition_valid = (
            checked["final_revision_transition"] == "preserved_by_plugin_capability"
        )
    else:
        transition_valid = False
    if not transition_valid:
        raise PipelineError(
            ErrorCode.NATIVE_CONSOLE_RESULT_INVALID,
            "console final revision transition differs",
        )
    expected_operations = {
        operation["operation_id"]: operation
        for operation in checked_manifest["operations"]
    }
    observed = {
        item["operation_id"]: item
        for item in checked["operation_results"]
    }
    if set(observed) != set(expected_operations):
        raise PipelineError(ErrorCode.NATIVE_CONSOLE_RESULT_INVALID, "console operation result differs")
    for operation_id, operation in expected_operations.items():
        item = observed[operation_id]
        marker_handle = item["marker_handle"]
        is_marker = operation["kind"] == "create_review_marker"
        if (
            item["status"] != "applied"
            or (is_marker and not isinstance(marker_handle, str))
            or (not is_marker and marker_handle is not None)
            or item["postcondition_digest"]
            != _operation_postcondition_digest(
                operation,
                marker_handle,
            )
        ):
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_RESULT_INVALID,
                "console operation result differs",
            )
    return checked


def geometry_from_console_export(
    manifest: Mapping[str, Any],
    console_export: Mapping[str, Any],
    *,
    run_id: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one fresh readback export emitted by a separate console run."""

    checked_manifest = require_active_native_contract("manifest", manifest)
    checked_result = require_active_native_contract("console_result", result)
    checked = require_active_native_contract("console_export", console_export)
    if (
        checked["run_id"] != run_id
        or checked["manifest_id"] != checked_manifest["manifest_id"]
        or checked["manifest_integrity_sha256"]
        != native_artifact_integrity(checked_manifest)
        or checked["manifest_schema_version"] != checked_manifest["schema_version"]
        or checked["console_result_integrity_sha256"]
        != native_artifact_integrity(checked_result)
        or checked["console_result_schema_version"] != checked_result["schema_version"]
        or checked["nonce"] != checked_manifest["nonce"]
        or checked["final_revision_fingerprint"]
        != checked_result["final_revision_fingerprint"]
        or checked["final_document_binding"]
        != checked_result["final_document_binding"]
    ):
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "readback binding differs")
    geometry = _embedded_geometry(
        checked["geometry_json"],
        error=ErrorCode.NATIVE_READBACK_INVALID,
    )
    if checked["geometry_sha256"] != canonical_sha256(geometry):
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "readback geometry digest differs")
    if (
        geometry["document"]["revision_fingerprint"]
        != checked_result["final_revision_fingerprint"]
        or geometry["document"]["revision_fingerprint"]
        != checked["final_revision_fingerprint"]
        or geometry["document"]["database_instance_fingerprint"]
        != checked_result["final_document_binding"]["database_instance_fingerprint"]
        or geometry["source"]
        != checked_result["final_document_binding"]["output_copy_binding"]
        or native_execution_stable_host_binding_digest(
            geometry,
            checked_manifest["marker_policy_binding"],
        )
        != checked_manifest["stable_host_binding_digest"]
    ):
        raise PipelineError(ErrorCode.NATIVE_READBACK_INVALID, "readback geometry revision differs")
    require_final_output_binding(
        checked_manifest,
        geometry["source"],
        error_code=ErrorCode.NATIVE_READBACK_INVALID,
    )
    return geometry


def native_output_binding(description: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Project an exact held output description into redacted evidence fields."""

    current = now or utc_now()
    return {
        "format": description["format"],
        "sha256": description["sha256"],
        "byte_size": description["byte_size"],
        "path_fingerprint": description["path_fingerprint"],
        "file_identity_fingerprint": description["file_identity_fingerprint"],
        "dwg_header_signature": description["dwg_header_signature"],
        "verified_at": format_utc(current),
    }


def build_native_verification(
    manifest: Mapping[str, Any],
    after_export: Mapping[str, Any],
    output_description: Mapping[str, Any],
    *,
    result: Mapping[str, Any],
    console_export: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build evidence only after exact readback passes; it never authorizes edits."""

    checked_manifest = require_active_native_contract("manifest", manifest)
    checked_after = require_active_native_contract("geometry", after_export)
    checked_result = require_active_native_contract("console_result", result)
    checked_export = require_active_native_contract("console_export", console_export)
    if (
        checked_export["manifest_id"] != checked_manifest["manifest_id"]
        or checked_export["manifest_integrity_sha256"]
        != native_artifact_integrity(checked_manifest)
        or checked_export["console_result_integrity_sha256"]
        != native_artifact_integrity(checked_result)
    ):
        raise PipelineError(
            ErrorCode.NATIVE_VERIFICATION_INVALID,
            "verification input version bindings differ",
        )
    results = verify_native_transition(
        checked_manifest,
        checked_after,
        console_result=checked_result,
    )
    try:
        if any(
            output_description[key] != checked_after["source"][key]
            for key in ("format", "sha256", "byte_size", "dwg_header_signature")
        ):
            raise ValueError("public staging bytes differ from verified private output")
    except (KeyError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_VERIFICATION_INVALID,
            "verification output does not bind readback bytes",
        ) from error
    current = now or utc_now()
    before = _precondition_export(checked_manifest)
    artifact = {
        "schema_version": "liang-pingfa/native-verification/v2",
        "verification_id": "native-verification-"
        + canonical_sha256(
            {
                "after": checked_after["document"]["complete_geometry_digest"],
                "manifest": checked_manifest["manifest_id"],
                "output": output_description["sha256"],
            }
        )[:32],
        "created_at": format_utc(current),
        "audit_binding": checked_manifest["audit_binding"],
        "plan_binding": checked_manifest["plan_binding"],
        "manifest_binding": {
            "manifest_id": checked_manifest["manifest_id"],
            "manifest_integrity_sha256": native_artifact_integrity(checked_manifest),
            "manifest_schema_version": checked_manifest["schema_version"],
        },
        "console_result_binding": {
            "run_id": checked_result["run_id"],
            "result_integrity_sha256": native_artifact_integrity(checked_result),
            "result_schema_version": checked_result["schema_version"],
        },
        "console_export_binding": {
            "run_id": checked_export["run_id"],
            "export_integrity_sha256": native_artifact_integrity(checked_export),
            "export_schema_version": checked_export["schema_version"],
        },
        "output_binding": native_output_binding(output_description, now=current),
        "transition_digest": canonical_sha256(
            {
                "after": checked_after["document"]["complete_geometry_digest"],
                "before": before["document"]["complete_geometry_digest"],
                "operations": results,
            }
        ),
        "operation_results": results,
        "record_cardinality": PRIVATE_RECORD_CARDINALITY,
        "passed": True,
        "non_claims": {
            "evidence_only": True,
            "external_transaction_claim_unproven": True,
            "real_host_integration_unproven": True,
        },
    }
    return validate_native_contract("verification", attach_integrity(artifact))


def require_published_output_binding(
    verification: Mapping[str, Any],
    output_description: Mapping[str, Any],
) -> dict[str, Any]:
    """Require the committed public DWG to equal the staged evidence binding."""

    checked = require_active_native_contract("verification", verification)
    expected = checked["output_binding"]
    try:
        if any(
            output_description[key] != expected[key]
            for key in (
                "format",
                "sha256",
                "byte_size",
                "path_fingerprint",
                "file_identity_fingerprint",
                "dwg_header_signature",
            )
        ):
            raise ValueError("published output differs from verification evidence")
    except (KeyError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_VERIFICATION_INVALID,
            "published native output binding differs",
        ) from error
    return checked


def verify_native_published_output(
    output_path: Path,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Recheck that evidence still names the exact currently held output bytes.

    This command intentionally does not authorize another edit and cannot turn
    old verification evidence into a future manifest.  The exact before/after
    geometry proof is produced during ``native-apply`` before publication.
    """

    checked = require_active_native_contract("verification", verification)
    if __import__("os").name != "nt":
        raise PipelineError(ErrorCode.WINDOWS_PLATFORM_REQUIRED, "native verification is Windows-only")
    backend = platform_backend(require_windows=True)
    lease = acquire_source_path_lease(output_path, backend)
    try:
        current = native_source_from_lease(lease)
        expected = checked["output_binding"]
        if any(
            current[key] != expected[key]
            for key in (
                "format",
                "sha256",
                "byte_size",
                "path_fingerprint",
                "file_identity_fingerprint",
                "dwg_header_signature",
            )
        ):
            raise PipelineError(ErrorCode.NATIVE_VERIFICATION_INVALID, "native evidence no longer binds output")
        return checked
    finally:
        lease.close()
