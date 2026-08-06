"""Private, immutable one-use manifest construction for native writes."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import os
from pathlib import Path
import secrets
from typing import Any

from .canonical import (
    canonical_sha256,
    format_utc,
    normalize_nfc_text,
    parse_utc,
    utc_now,
)
from .errors import ErrorCode, PipelineError
from .native_audit import native_audit_binding, require_fresh_native_audit
from .native_contracts import (
    attach_native_integrity,
    canonical_native_contract_bytes,
    canonical_geometry_json_bytes,
    derive_native_target_id,
    derive_native_marker_text,
    geometry_adapter_binding,
    geometry_document_binding,
    geometry_document_binding_digest,
    native_artifact_integrity,
    native_execution_stable_host_binding_digest,
    native_host_binding,
    native_marker_fingerprint,
    native_marker_policy_binding,
    PRIVATE_RECORD_CARDINALITY,
    prewrite_semantic_projection,
    prewrite_semantic_projection_digest,
    prewrite_revision_binding,
    require_active_native_contract,
    require_geometry_export_matches_session,
    translated_geometry_bits,
    validate_native_contract,
)
from .native_plan import validate_native_plan_against_audit
from .temporary import PrivateWorkspace


MAX_NATIVE_FINAL_OUTPUT_BYTES = 512 * 1024 * 1024


def _path_fingerprint(path: Path) -> str:
    """Hash a lexical public target spelling without resolving a user path."""

    return canonical_sha256(
        {"output_path": os.path.normcase(os.path.abspath(os.fspath(path)))}
    )


def _private_path_fingerprint(path: Path) -> str:
    """Fingerprint a private path exactly as its retained file lease does."""

    return sha256(normalize_nfc_text(str(path)).encode("utf-8")).hexdigest()


def _private_root_fingerprint(path: Path) -> str:
    """Fingerprint an authorized private workspace root without storing it."""

    return _private_path_fingerprint(path)


def _lexical_path(path: Path) -> Path:
    """Return an absolute lexical path without resolving a reparse point."""

    return Path(os.path.abspath(os.fspath(path)))


def _same_volume(left: Path, right: Path) -> bool:
    """Compare only lexical Windows volume roots; callers hold real handles."""

    return os.path.normcase(os.path.splitdrive(os.fspath(left))[0]) == os.path.normcase(
        os.path.splitdrive(os.fspath(right))[0]
    )


def _private_output_source_binding(
    private_source_copy: Mapping[str, Any],
    *,
    prewrite_source: Mapping[str, Any],
    private_output_path: Path,
) -> dict[str, Any]:
    """Return the exact pre-write private-copy source binding.

    A post-save DWG does not exist yet and therefore cannot be represented
    here.  This projection binds the file that the console must open before
    it is permitted to mutate anything.
    """

    try:
        binding = {
            "format": "DWG",
            "sha256": private_source_copy["sha256"],
            "byte_size": private_source_copy["byte_size"],
            "path_fingerprint": private_source_copy["path_fingerprint"],
            "file_identity_fingerprint": private_source_copy[
                "file_identity_fingerprint"
            ],
            "dwg_header_signature": private_source_copy["dwg_header_signature"],
        }
    except (KeyError, TypeError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "private output copy is not bound",
        ) from error
    expected_path_fingerprint = _private_path_fingerprint(
        _lexical_path(private_output_path)
    )
    if binding["path_fingerprint"] != expected_path_fingerprint:
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "private output copy path is not exact",
        )
    if binding == dict(prewrite_source):
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "private output copy must differ from original source",
        )
    return binding


def _final_output_constraints(
    prewrite_output_binding: Mapping[str, Any],
    *,
    private_output_path: Path,
    private_workspace_root: Path,
) -> dict[str, Any]:
    """Build constraints for an output whose bytes are unknowable pre-save."""

    private_path = _lexical_path(private_output_path)
    private_root = _lexical_path(private_workspace_root)
    try:
        private_path.relative_to(private_root)
    except ValueError as error:
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "private output is outside its workspace root",
        ) from error
    if not _same_volume(private_path, private_root):
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "private output does not share its workspace volume",
        )
    return {
        "authorized_private_path_fingerprint": prewrite_output_binding[
            "path_fingerprint"
        ],
        "authorized_private_root_fingerprint": _private_root_fingerprint(private_root),
        "require_same_volume_as_prewrite": True,
        "require_within_private_root": True,
        "required_dwg_header_signature": prewrite_output_binding[
            "dwg_header_signature"
        ],
        # The ACxxxx signature is the version token exposed by the safe
        # source-binding contract. Keep it explicit so adapters cannot claim
        # header validation while saving another DWG version.
        "required_dwg_version": prewrite_output_binding["dwg_header_signature"],
        "max_byte_size": MAX_NATIVE_FINAL_OUTPUT_BYTES,
        # SaveAs implementations are allowed to atomically replace the
        # private file, but the replacement remains constrained to this exact
        # lexical private destination and retained-handle revalidation.
        "file_identity_transition_policy": "replacement_allowed",
    }


def require_final_output_binding(
    manifest: Mapping[str, Any],
    actual_binding: Mapping[str, Any],
    *,
    private_output_path: Path | None = None,
    private_workspace_root: Path | None = None,
    error_code: ErrorCode = ErrorCode.NATIVE_READBACK_INVALID,
) -> dict[str, Any]:
    """Require an actual post-save binding to satisfy v2 constraints.

    ``actual_binding`` is intentionally supplied by a retained file handle or
    a console/readback envelope after save.  The helper never derives a final
    hash, byte size, identity, or revision from the prewrite input.
    """

    try:
        checked_manifest = require_active_native_contract("manifest", manifest)
    except PipelineError as error:
        if error.code == ErrorCode.NATIVE_LEGACY_ARTIFACT_READ_ONLY:
            raise
        raise PipelineError(error_code, "native manifest is invalid") from error
    try:
        actual = dict(actual_binding)
        prewrite = checked_manifest["expected_prewrite_output_copy_binding"]
        constraints = checked_manifest["final_output_constraints"]
        if (
            actual["format"] != "DWG"
            or actual["path_fingerprint"]
            != constraints["authorized_private_path_fingerprint"]
            or actual["dwg_header_signature"]
            != constraints["required_dwg_header_signature"]
            or actual["dwg_header_signature"] != constraints["required_dwg_version"]
            or not isinstance(actual["byte_size"], int)
            or actual["byte_size"] < 6
            or actual["byte_size"] > constraints["max_byte_size"]
            or not isinstance(actual["sha256"], str)
            or not isinstance(actual["file_identity_fingerprint"], str)
        ):
            raise ValueError("actual output violates constrained binding")
        if (
            constraints["file_identity_transition_policy"] == "same_identity_required"
            and actual["file_identity_fingerprint"]
            != prewrite["file_identity_fingerprint"]
        ):
            raise ValueError("private output identity replacement is forbidden")
        # Every currently allowlisted operation changes bytes. Revisions are
        # checked by result/readback validators; this gate rejects the former
        # generated-copy loophole before public staging.
        if checked_manifest["operations"] and actual["sha256"] == prewrite["sha256"]:
            raise ValueError("mutating manifest produced unchanged DWG bytes")
        if private_output_path is not None:
            actual_path = _lexical_path(private_output_path)
            if _private_path_fingerprint(actual_path) != actual["path_fingerprint"]:
                raise ValueError("actual output path fingerprint differs")
            if private_workspace_root is None:
                raise ValueError("private root is required with private output path")
            private_root = _lexical_path(private_workspace_root)
            if (
                _private_root_fingerprint(private_root)
                != constraints["authorized_private_root_fingerprint"]
            ):
                raise ValueError("private workspace root differs")
            try:
                actual_path.relative_to(private_root)
            except ValueError as error:
                raise ValueError("actual output escaped private root") from error
            if (
                constraints["require_same_volume_as_prewrite"]
                and not _same_volume(actual_path, private_root)
            ):
                raise ValueError("actual output crossed private volume")
    except (KeyError, TypeError, ValueError) as error:
        raise PipelineError(error_code, "actual final output binding is invalid") from error
    return actual


def _private_prewrite_export(
    fresh_export: Mapping[str, Any],
    prewrite_output_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Retarget source binding without changing portable document semantics."""

    bridge_projection = prewrite_semantic_projection(fresh_export)
    bridge_projection_digest = prewrite_semantic_projection_digest(fresh_export)
    private_export = deepcopy(dict(fresh_export))
    # This is the only intentional source-to-private-copy rewrite. Host
    # database/revision fields remain bridge evidence in the embedded geometry
    # and are excluded from the portable projection checked by Core Console.
    private_export["source"] = dict(prewrite_output_binding)
    private_export["binding"]["document_binding_digest"] = geometry_document_binding_digest(
        private_export
    )
    if (
        prewrite_semantic_projection(private_export) != bridge_projection
        or prewrite_semantic_projection_digest(private_export)
        != bridge_projection_digest
    ):
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "private source retarget changed portable prewrite state",
        )
    return validate_native_contract(
        "geometry",
        attach_native_integrity("geometry", private_export),
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
        or native_execution_stable_host_binding_digest(
            checked_export,
            native_marker_policy_binding(config),
        )
        != audit["stable_host_binding_digest"]
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
    direct_containers = [
        container
        for container in export["containers"]
        if (
            container["space"]["kind"] == defaults["space_kind"]
            and container["block_path"] == defaults["block_path"]
        )
    ]
    if marker_count < 1:
        return []
    if len(direct_containers) != 1:
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "marker direct Modelspace container is ambiguous",
        )
    direct = direct_containers[0]
    owner_handle = direct["owner_handle"]
    layout_handle = direct["space"]["layout_handle"]
    block_handle = direct["space"]["block_handle"]
    if block_handle is not None or defaults["space_kind"] != "modelspace":
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "marker container is not direct Modelspace",
        )
    if owner_handle not in export["owners"]:
        raise PipelineError(
            ErrorCode.NATIVE_MANIFEST_INVALID,
            "marker owner is not a pre-existing declared Modelspace owner",
        )
    # Slot reservations are based on the immutable erased-inclusive physical
    # extent, not on the greatest active entity index. This remains correct
    # when the final physical slots are erased or all active records precede
    # an internal/trailing gap.
    first_index = int(direct["physical_slot_count"])
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
    private_output_path: Path | None = None,
    private_workspace_root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a private manifest only after exact fresh state equality."""

    checked_audit = require_fresh_native_audit(audit, now=now)
    checked_intent = require_active_native_contract("intent", intent)
    checked_config = require_active_native_contract("config", config)
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
    stable_host_binding_digest = native_execution_stable_host_binding_digest(
        checked_export,
        current_marker_policy,
    )
    _require_config_matches_export(checked_config, checked_export)
    if (
        checked_audit["host_executable_fingerprint"] == "unavailable"
        or checked_session["process"]["executable_fingerprint"] == "unavailable"
        or checked_plan["native_host_binding"] != checked_audit["native_host_binding"]
        or checked_plan["stable_host_binding_digest"]
        != checked_audit["stable_host_binding_digest"]
        or stable_host_binding_digest
        != checked_audit["stable_host_binding_digest"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "native manifest requires an audited compatible host",
        )
    source = checked_audit["source"]
    selected_private_output_path = private_output_path or output_path
    selected_private_workspace_root = (
        private_workspace_root or selected_private_output_path.parent
    )
    if (
        private_source_copy.get("sha256") != source["sha256"]
        or private_source_copy.get("byte_size") != source["byte_size"]
        or not isinstance(private_source_copy.get("file_identity_fingerprint"), str)
    ):
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_INVALID, "private source copy is not bound")
    normalized_private_source_copy = dict(private_source_copy)
    # Source-free core fixtures do not own a real workspace, so they may omit
    # path/header metadata. Production passes the retained copy's exact values
    # and the comparison below rejects a substituted private destination.
    normalized_private_source_copy.setdefault(
        "path_fingerprint",
        _private_path_fingerprint(_lexical_path(selected_private_output_path)),
    )
    normalized_private_source_copy.setdefault(
        "dwg_header_signature",
        checked_export["source"]["dwg_header_signature"],
    )
    expected_prewrite_output_copy_binding = _private_output_source_binding(
        normalized_private_source_copy,
        prewrite_source=source,
        private_output_path=selected_private_output_path,
    )
    final_output_constraints = _final_output_constraints(
        expected_prewrite_output_copy_binding,
        private_output_path=selected_private_output_path,
        private_workspace_root=selected_private_workspace_root,
    )
    prewrite_export = _private_prewrite_export(
        checked_export,
        expected_prewrite_output_copy_binding,
    )
    plan_operations = {
        operation["operation_id"]: operation for operation in checked_plan["operations"]
    }
    sorted_intent_operations = sorted(
        checked_intent["operations"], key=lambda item: item["operation_id"]
    )
    marker_destinations = iter(
        _marker_destinations(
            prewrite_export,
            marker_policy=checked_audit["marker_policy_binding"],
            marker_count=sum(
                operation["kind"] == "create_review_marker"
                for operation in sorted_intent_operations
            ),
        )
    )
    private_operations: list[dict[str, Any]] = []
    fresh_by_target = {
        derive_native_target_id(entity): entity for entity in prewrite_export["entities"]
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
        prewrite_export,
        error=ErrorCode.NATIVE_MANIFEST_INVALID,
    ).decode("utf-8")
    artifact = {
        "schema_version": "liang-pingfa/native-edit-manifest/v2",
        "manifest_id": "native-manifest-" + secrets.token_hex(16),
        "created_at": format_utc(current),
        "expires_at": format_utc(manifest_expires),
        "consumed": False,
        "nonce": secrets.token_urlsafe(32),
        "audit_binding": {
            **native_audit_binding(checked_audit),
            "audit_schema_version": checked_audit["schema_version"],
        },
        "plan_binding": {
            "plan_id": checked_plan["plan_id"],
            "plan_integrity_sha256": native_artifact_integrity(checked_plan),
            "plan_schema_version": checked_plan["schema_version"],
        },
        "intent_binding": {
            "intent_id": checked_intent["intent_id"],
            "intent_integrity_sha256": native_artifact_integrity(checked_intent),
            "intent_schema_version": checked_intent["schema_version"],
        },
        "native_host_binding": checked_audit["native_host_binding"],
        "stable_host_binding_digest": stable_host_binding_digest,
        "marker_policy_binding": checked_audit["marker_policy_binding"],
        "session_renewal": {
            "audited_session_binding": checked_audit["session_binding_digest"],
            "fresh_session_binding": checked_export["binding"][
                "session_binding_digest"
            ],
            "audited_session_schema_version": checked_audit[
                "session_schema_version"
            ],
            "fresh_session_schema_version": checked_session["schema_version"],
            "native_host_binding": checked_audit["native_host_binding"],
            "expires_at": checked_session["expires_at"],
        },
        "source": source,
        "expected_prewrite_output_copy_binding": expected_prewrite_output_copy_binding,
        "final_output_constraints": final_output_constraints,
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
            prewrite_export,
            native_host_binding_value=checked_audit["native_host_binding"],
            stable_host_binding_digest=stable_host_binding_digest,
            audited_semantic_state_digest=native_artifact_integrity(checked_audit),
        ),
        "preconditions_geometry_json": raw_geometry,
        "preconditions_geometry_sha256": canonical_sha256(prewrite_export),
        "operations": private_operations,
        "record_cardinality": PRIVATE_RECORD_CARDINALITY,
    }
    return validate_native_contract(
        "manifest",
        attach_native_integrity("manifest", artifact),
    )


def write_private_manifest(
    workspace: PrivateWorkspace,
    path: Path,
    manifest: Mapping[str, Any],
) -> Path:
    """Write an immutable manifest only through a workspace-owned handle."""

    checked = require_active_native_contract("manifest", manifest)
    opened = workspace.create_owned_file(path)
    try:
        opened.write_bytes(
            canonical_native_contract_bytes("manifest", checked) + b"\n"
        )
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

    checked = require_active_native_contract("manifest", manifest)
    current = now or utc_now()
    try:
        created = parse_utc(checked["created_at"])
        expires = parse_utc(checked["expires_at"])
    except Exception as error:
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_INVALID, "manifest time invalid") from error
    if current < created or current >= expires:
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_REPLAY, "manifest expired")
    return checked
