"""Private, immutable one-use manifest construction for native writes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import os
from pathlib import Path
import secrets
from typing import Any

from .canonical import (
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
    format_utc,
    parse_utc,
    utc_now,
)
from .errors import ErrorCode, PipelineError
from .native_audit import native_audit_binding, require_fresh_native_audit
from .native_contracts import (
    canonical_geometry_json_bytes,
    derive_native_target_id,
    derive_native_marker_text,
    geometry_adapter_binding,
    geometry_document_binding,
    native_artifact_integrity,
    native_host_binding,
    native_marker_fingerprint,
    native_marker_policy_binding,
    PRIVATE_RECORD_CARDINALITY,
    prewrite_revision_binding,
    require_geometry_export_matches_session,
    translated_geometry_bits,
    validate_native_contract,
)
from .native_plan import validate_native_plan_against_audit
from .temporary import PrivateWorkspace


def _path_fingerprint(path: Path) -> str:
    """Hash a lexical public target spelling without resolving a user path."""

    return canonical_sha256(
        {"output_path": os.path.normcase(os.path.abspath(os.fspath(path)))}
    )


def _require_fresh_export_matches_audit(
    audit: Mapping[str, Any],
    export: Mapping[str, Any],
    fresh_session: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind a fresh export before comparing it with the audited state."""

    checked_export, checked_session = require_geometry_export_matches_session(
        export,
        fresh_session,
    )
    if (
        checked_session["process"]["executable_fingerprint"] == "unavailable"
        or audit["host_executable_fingerprint"] == "unavailable"
        or checked_session["process"]["executable_fingerprint"]
        != audit["host_executable_fingerprint"]
        or native_host_binding(checked_session, config) != audit["native_host_binding"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "fresh session host compatibility differs from audit",
        )
    fresh_document = geometry_document_binding(checked_export)
    audited_document = audit["document_binding"]
    # A renewed host may have reopened the same saved DWG into a different
    # database instance with a new ephemeral revision token.  Those values
    # bind this pre-write transaction, but are not audit-era identity gates.
    stable_document_keys = (
        "ordered_entity_digest",
        "container_order_digest",
        "complete_geometry_digest",
        "protected_state_digest",
        "protected_order_digest",
        "table_state_digest",
        "layout_state_digest",
        "block_state_digest",
        "document_state_digest",
    )
    if (
        checked_export["source"] != audit["source"]
        or geometry_adapter_binding(checked_export) != audit["adapter_binding"]
        or any(
            fresh_document[key] != audited_document[key]
            for key in stable_document_keys
        )
        or checked_export["document"]["protected_state_digest"]
        != audit["protected_state_digest"]
    ):
        raise PipelineError(ErrorCode.NATIVE_DOCUMENT_CHANGED, "fresh bridge export differs from audit")
    audited = {
        record["target_id"]: (
            record["before_geometry_fingerprint"],
            record["opaque_state_digest"],
        )
        for record in audit["records"]
    }
    current = {
        derive_native_target_id(entity): (
            entity["geometry_fingerprint"],
            entity["opaque_state_digest"],
        )
        for entity in checked_export["entities"]
    }
    if current != audited:
        raise PipelineError(ErrorCode.NATIVE_DOCUMENT_CHANGED, "fresh geometry differs from audit")
    document = checked_session["current_document"]
    if (
        any(
            document[key] != checked_export["source"][key]
            for key in (
                "sha256",
                "byte_size",
                "path_fingerprint",
                "file_identity_fingerprint",
            )
        )
        or document["database_instance_fingerprint"]
        != checked_export["document"]["database_instance_fingerprint"]
        or document["revision_fingerprint"]
        != checked_export["document"]["revision_fingerprint"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_DOCUMENT_CHANGED,
            "fresh session document differs from export",
        )
    return checked_export, checked_session


def _require_config_matches_export(
    config: Mapping[str, Any],
    export: Mapping[str, Any],
) -> None:
    binding = export["binding"]
    plugin = config["plugins"]["readback"]
    if (
        binding["adapter"] != config["adapter"]
        or binding["plugin"]["id"] != plugin["id"]
        or binding["plugin"]["version"] != plugin["version"]
        or binding["plugin"]["fingerprint"] != plugin["sha256"]
        or not set(config["required_capabilities"]).issubset(binding["capabilities"])
    ):
        raise PipelineError(ErrorCode.NATIVE_CAPABILITY_MISMATCH, "fresh bridge capability drift")


def _private_operation(
    intent_operation: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    marker_destination: Mapping[str, Any] | None = None,
    translation_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kind = intent_operation["kind"]
    if kind == "translate_dbtext":
        if translation_target is None:
            raise PipelineError(
                ErrorCode.NATIVE_MANIFEST_INVALID,
                "translation target was not present in fresh geometry",
            )
        try:
            expected_after = translated_geometry_bits(
                translation_target, intent_operation["delta"]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PipelineError(
                ErrorCode.NATIVE_MANIFEST_INVALID,
                "translation is not representable for fresh geometry",
            ) from error
        return {
            "operation_id": intent_operation["operation_id"],
            "kind": kind,
            "target_id": intent_operation["target_id"],
            "delta": intent_operation["delta"],
            "expected_after": expected_after,
        }
    if kind == "delete_auxiliary_overlay_text":
        return {
            "operation_id": intent_operation["operation_id"],
            "kind": kind,
            "target_id": intent_operation["target_id"],
        }
    if kind == "create_review_marker":
        if marker_destination is None:
            raise PipelineError(
                ErrorCode.NATIVE_MANIFEST_INVALID,
                "marker destination was not audited",
            )
        operation_id = intent_operation["operation_id"]
        marker = native_marker_policy_binding(config)
        defaults = marker["geometry_defaults"]
        if marker_destination["space"]["kind"] != defaults["space_kind"]:
            raise PipelineError(
                ErrorCode.NATIVE_MANIFEST_INVALID,
                "marker destination profile differs",
            )
        operation = {
            "operation_id": operation_id,
            "kind": kind,
            "owner_handle": marker_destination["owner_handle"],
            "space": marker_destination["space"],
            "block_path": list(defaults["block_path"]),
            "sequence_index": marker_destination["sequence_index"],
            "position": intent_operation["position"],
            "marker_text": derive_native_marker_text(operation_id, marker),
            "layer": marker["layer"],
            "style": marker["style"],
            "height": marker["height_bits"],
            "rotation": marker["rotation_bits"],
            "overlay_evidence": dict(defaults["overlay_evidence"]),
        }
        operation["marker_fingerprint"] = native_marker_fingerprint(operation)
        return operation
    raise PipelineError(ErrorCode.NATIVE_OPERATION_INVALID, "unknown native intent operation")


def _marker_destinations(
    export: Mapping[str, Any],
    *,
    marker_policy: Mapping[str, Any],
    marker_count: int,
) -> list[dict[str, Any]]:
    """Reserve deterministic append slots in the one audited direct Modelspace."""

    defaults = marker_policy["geometry_defaults"]
    direct = [
        entity
        for entity in export["entities"]
        if (
            entity["space"]["kind"] == defaults["space_kind"]
            and entity["block_path"] == defaults["block_path"]
        )
    ]
    containers = {
        (
            entity["owner_handle"],
            entity["space"]["layout_handle"],
            entity["space"]["block_handle"],
        )
        for entity in direct
    }
    if marker_count < 1:
        return []
    if len(containers) != 1:
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "marker direct Modelspace container is ambiguous",
        )
    owner_handle, layout_handle, block_handle = next(iter(containers))
    if block_handle is not None or defaults["space_kind"] != "modelspace":
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "marker container is not direct Modelspace",
        )
    first_index = max(int(entity["sequence_index"]) for entity in direct) + 1
    return [
        {
            "owner_handle": owner_handle,
            "space": {
                "kind": "modelspace",
                "layout_handle": layout_handle,
                "block_handle": None,
            },
            "sequence_index": first_index + offset,
        }
        for offset in range(marker_count)
    ]


def build_native_manifest(
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
    intent: Mapping[str, Any],
    fresh_export: Mapping[str, Any],
    fresh_session: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    private_source_copy: Mapping[str, Any],
    output_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a private manifest only after exact fresh state equality."""

    checked_audit = require_fresh_native_audit(audit, now=now)
    checked_intent = validate_native_contract("intent", intent)
    checked_config = validate_native_contract("config", config)
    checked_plan = validate_native_plan_against_audit(
        checked_audit,
        checked_intent,
        plan,
        checked_config,
        now=now,
    )
    current_marker_policy = native_marker_policy_binding(checked_config)
    if (
        checked_audit["marker_policy_binding"] != current_marker_policy
        or checked_plan["marker_policy_binding"]
        != checked_audit["marker_policy_binding"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "native marker policy differs from audit/plan",
        )
    checked_export, checked_session = _require_fresh_export_matches_audit(
        checked_audit,
        fresh_export,
        fresh_session,
        checked_config,
    )
    _require_config_matches_export(checked_config, checked_export)
    if (
        checked_audit["host_executable_fingerprint"] == "unavailable"
        or checked_session["process"]["executable_fingerprint"] == "unavailable"
        or checked_plan["native_host_binding"] != checked_audit["native_host_binding"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "native manifest requires an audited compatible host",
        )
    source = checked_audit["source"]
    if (
        private_source_copy.get("sha256") != source["sha256"]
        or private_source_copy.get("byte_size") != source["byte_size"]
        or not isinstance(private_source_copy.get("file_identity_fingerprint"), str)
    ):
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_INVALID, "private source copy is not bound")
    plan_operations = {
        operation["operation_id"]: operation for operation in checked_plan["operations"]
    }
    sorted_intent_operations = sorted(
        checked_intent["operations"], key=lambda item: item["operation_id"]
    )
    marker_destinations = iter(
        _marker_destinations(
            checked_export,
            marker_policy=checked_audit["marker_policy_binding"],
            marker_count=sum(
                operation["kind"] == "create_review_marker"
                for operation in sorted_intent_operations
            ),
        )
    )
    private_operations: list[dict[str, Any]] = []
    fresh_by_target = {
        derive_native_target_id(entity): entity for entity in checked_export["entities"]
    }
    for intent_operation in sorted_intent_operations:
        operation_id = intent_operation["operation_id"]
        plan_operation = plan_operations.get(operation_id)
        if (
            plan_operation is None
            or plan_operation["kind"] != intent_operation["kind"]
            or plan_operation["allowed_delta_digest"] != canonical_sha256(intent_operation)
        ):
            raise PipelineError(ErrorCode.NATIVE_ARTIFACT_MISMATCH, "manifest plan/intent drift")
        private_operations.append(
            _private_operation(
                intent_operation,
                checked_config,
                translation_target=(
                    fresh_by_target.get(intent_operation["target_id"])
                    if intent_operation["kind"] == "translate_dbtext"
                    else None
                ),
                marker_destination=(
                    next(marker_destinations)
                    if intent_operation["kind"] == "create_review_marker"
                    else None
                ),
            )
        )
    if set(plan_operations) != {item["operation_id"] for item in private_operations}:
        raise PipelineError(ErrorCode.NATIVE_ARTIFACT_MISMATCH, "manifest operation set differs")
    current = now or utc_now()
    try:
        session_expires = parse_utc(checked_session["expires_at"])
    except (TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_INVALID,
            "fresh native session expiry is invalid",
        ) from error
    if session_expires <= current:
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_EXPIRED,
            "fresh native session expired before manifest",
        )
    manifest_expires = min(current + timedelta(minutes=5), session_expires)
    raw_geometry = canonical_geometry_json_bytes(
        checked_export,
        error=ErrorCode.NATIVE_MANIFEST_INVALID,
    ).decode("utf-8")
    artifact = {
        "schema_version": "liang-pingfa/native-edit-manifest/v1",
        "manifest_id": "native-manifest-" + secrets.token_hex(16),
        "created_at": format_utc(current),
        "expires_at": format_utc(manifest_expires),
        "consumed": False,
        "nonce": secrets.token_urlsafe(32),
        "audit_binding": native_audit_binding(checked_audit),
        "plan_binding": {
            "plan_id": checked_plan["plan_id"],
            "plan_integrity_sha256": native_artifact_integrity(checked_plan),
        },
        "intent_binding": {
            "intent_id": checked_intent["intent_id"],
            "intent_integrity_sha256": native_artifact_integrity(checked_intent),
        },
        "native_host_binding": checked_audit["native_host_binding"],
        "marker_policy_binding": checked_audit["marker_policy_binding"],
        "session_renewal": {
            "audited_session_binding": checked_audit["session_binding_digest"],
            "fresh_session_binding": checked_export["binding"][
                "session_binding_digest"
            ],
            "native_host_binding": checked_audit["native_host_binding"],
            "expires_at": checked_session["expires_at"],
        },
        "source": source,
        "private_source_copy": dict(private_source_copy),
        "output_target_path_fingerprint": _path_fingerprint(output_path),
        "environment": {
            "core_console_fingerprint": checked_config["core_console"]["sha256"],
            "write_plugin_fingerprint": checked_config["plugins"]["write"]["sha256"],
            "readback_plugin_fingerprint": checked_config["plugins"]["readback"][
                "sha256"
            ],
            "write_command": checked_config["plugins"]["write"]["command"],
            "readback_command": checked_config["plugins"]["readback"]["command"],
            "write_revision_transition": checked_config["write_revision_transition"],
            "protocol_major": 1,
            "protocol_minor": 0,
            "capabilities_digest": canonical_sha256(checked_export["binding"]["capabilities"]),
        },
        "expected_prewrite_revision": prewrite_revision_binding(
            checked_export,
            native_host_binding_value=checked_audit["native_host_binding"],
            audited_semantic_state_digest=native_artifact_integrity(checked_audit),
        ),
        "preconditions_geometry_json": raw_geometry,
        "preconditions_geometry_sha256": canonical_sha256(checked_export),
        "operations": private_operations,
        "record_cardinality": PRIVATE_RECORD_CARDINALITY,
    }
    return validate_native_contract("manifest", attach_integrity(artifact))


def write_private_manifest(
    workspace: PrivateWorkspace,
    path: Path,
    manifest: Mapping[str, Any],
) -> Path:
    """Write an immutable manifest only through a workspace-owned handle."""

    checked = validate_native_contract("manifest", manifest)
    opened = workspace.create_owned_file(path)
    try:
        opened.write_bytes(canonical_json_bytes(checked) + b"\n")
        return workspace.seal_owned_file(opened)
    except BaseException:
        try:
            workspace.discard_owned_file(opened)
        except BaseException:
            pass
        raise


def require_fresh_native_manifest(
    manifest: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reject an expired or externally altered one-use manifest before launch."""

    checked = validate_native_contract("manifest", manifest)
    current = now or utc_now()
    try:
        created = parse_utc(checked["created_at"])
        expires = parse_utc(checked["expires_at"])
    except Exception as error:
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_INVALID, "manifest time invalid") from error
    if current < created or current >= expires:
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_REPLAY, "manifest expired")
    return checked
