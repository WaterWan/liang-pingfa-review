"""Deterministic planning for the isolated native operation allowlist."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .canonical import attach_integrity, canonical_sha256
from .errors import ErrorCode, PipelineError
from .native_audit import native_audit_binding, require_fresh_native_audit
from .native_contracts import (
    native_artifact_integrity,
    native_marker_policy_binding,
    PRIVATE_RECORD_CARDINALITY,
    require_active_native_contract,
    validate_native_contract,
)


_PROFILE_FOR_KIND = {
    "translate_dbtext": "translate_dbtext/v1",
    "delete_auxiliary_overlay_text": "delete_auxiliary_overlay_text/v1",
    "create_review_marker": "create_review_marker/v1",
}
_POSTCONDITION_FOR_KIND = {
    "translate_dbtext": "translated-exactly",
    "delete_auxiliary_overlay_text": "target-absent",
    "create_review_marker": "one-derived-marker",
}


def _plan_adapter_binding(audit: Mapping[str, Any]) -> dict[str, Any]:
    # Carry the complete audited adapter/plugin/version/protocol tuple, not a
    # compatibility subset.  ``audit_binding`` integrity already protects it,
    # but this explicit projection keeps plans independently reviewable.
    result = dict(audit["adapter_binding"])
    result["host_executable_fingerprint"] = audit["host_executable_fingerprint"]
    return result


def _audit_target_for_operation(
    audit: Mapping[str, Any],
    operation: Mapping[str, Any],
    profile: str,
) -> Mapping[str, Any]:
    target_id = operation.get("target_id")
    records = {record["target_id"]: record for record in audit["records"]}
    record = records.get(target_id)
    if record is None or profile not in record["eligible_profiles"]:
        raise PipelineError(ErrorCode.NATIVE_OPERATION_INVALID, "native target is not audited")
    if not any(
        finding["actionability"] is True
        and finding["target_id"] == target_id
        and finding["profile"] == profile
        for finding in audit["findings"]
    ):
        raise PipelineError(ErrorCode.NATIVE_OPERATION_INVALID, "native target lacks finding")
    return record


def _require_marker_capability(
    audit: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    marker = native_marker_policy_binding(config)
    audited_marker = audit["marker_policy_binding"]
    prerequisites = audit["marker_prerequisites"]
    if (
        audited_marker != marker
        or marker["enabled"] is not True
        or marker["plugin_capability"] is not True
        or marker["profile_enabled"] is not True
        or prerequisites["layer_fingerprint"] is None
        or prerequisites["style_fingerprint"] is None
        or prerequisites["layer_fingerprint"] != marker["layer_fingerprint"]
        or prerequisites["style_fingerprint"] != marker["style_fingerprint"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_OPERATION_INVALID,
            "marker profile is not explicitly capability-gated and audited",
        )


def generate_native_plan(
    audit: Mapping[str, Any],
    intent: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    now: Any = None,
) -> dict[str, Any]:
    """Generate the only acceptable redacted plan for a private native intent."""

    checked_audit = require_fresh_native_audit(audit, now=now)
    checked_intent = require_active_native_contract("intent", intent)
    checked_config = require_active_native_contract("config", config)
    marker_policy_binding = native_marker_policy_binding(checked_config)
    if checked_audit["marker_policy_binding"] != marker_policy_binding:
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "native marker policy differs from audit",
        )
    if checked_audit["host_executable_fingerprint"] == "unavailable":
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "native write planning requires a host executable fingerprint",
        )
    expected_audit_binding = {
        **native_audit_binding(checked_audit),
        "audit_schema_version": checked_audit["schema_version"],
    }
    if checked_intent["audit_binding"] != expected_audit_binding:
        raise PipelineError(ErrorCode.NATIVE_ARTIFACT_MISMATCH, "native intent audit binding differs")
    operations: list[dict[str, Any]] = []
    for intent_operation in sorted(
        checked_intent["operations"], key=lambda item: item["operation_id"]
    ):
        kind = intent_operation["kind"]
        profile = _PROFILE_FOR_KIND.get(kind)
        if profile is None or checked_config["operation_profiles"][profile] is not True:
            raise PipelineError(ErrorCode.NATIVE_OPERATION_INVALID, "native profile is disabled")
        if kind == "create_review_marker":
            _require_marker_capability(checked_audit, checked_config)
            target_id = None
            before_geometry = None
            before_opaque = None
        else:
            record = _audit_target_for_operation(
                checked_audit, intent_operation, profile
            )
            target_id = record["target_id"]
            before_geometry = record["before_geometry_fingerprint"]
            before_opaque = record["opaque_state_digest"]
        operations.append(
            {
                "operation_id": intent_operation["operation_id"],
                "kind": kind,
                "profile": profile,
                "target_id": target_id,
                "expected_before_geometry_fingerprint": before_geometry,
                "expected_before_opaque_state_digest": before_opaque,
                # The plan ID binds this exact bit-string delta digest and
                # the exact before fingerprint.  Their deterministic
                # transition is materialized and integrity-bound in the
                # fresh pre-launch manifest.
                "allowed_delta_digest": canonical_sha256(intent_operation),
                "postcondition": _POSTCONDITION_FOR_KIND[kind],
            }
        )
    profiles = sorted({operation["profile"] for operation in operations})
    source = checked_audit["source"]
    source_binding = {
        "sha256": source["sha256"],
        "path_fingerprint": source["path_fingerprint"],
        "file_identity_fingerprint": source["file_identity_fingerprint"],
    }
    common = {
        "audit_binding": {
            **native_audit_binding(checked_audit),
            "audit_schema_version": checked_audit["schema_version"],
        },
        "intent_sha256": native_artifact_integrity(checked_intent),
        "intent_schema_version": checked_intent["schema_version"],
        "source_binding": source_binding,
        "adapter_binding": _plan_adapter_binding(checked_audit),
        "native_host_binding": checked_audit["native_host_binding"],
        "stable_host_binding_digest": checked_audit[
            "stable_host_binding_digest"
        ],
        "marker_policy_binding": checked_audit["marker_policy_binding"],
        # The audit integrity already binds this field, but carrying the
        # validated geometry/document digest explicitly prevents later plan
        # consumers from treating a source-only match as sufficient.
        "geometry_document_binding_digest": checked_audit[
            "geometry_document_binding_digest"
        ],
        "protected_state_digest": checked_audit["protected_state_digest"],
        "operation_profiles": profiles,
        "operations": operations,
        # Operations are intentionally enumerated in this private artifact.
        "record_cardinality": PRIVATE_RECORD_CARDINALITY,
    }
    plan_id = "native-plan-" + canonical_sha256(common)[:32]
    artifact = {
        "schema_version": "liang-pingfa/native-edit-plan/v2",
        "plan_id": plan_id,
        # Determinism means a plan is time-independent while the audit itself
        # remains inside its short fixed validity interval.
        "created_at": checked_audit["created_at"],
        **common,
    }
    return validate_native_contract("plan", attach_integrity(artifact))


def validate_native_plan_against_audit(
    audit: Mapping[str, Any],
    intent: Mapping[str, Any],
    plan: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    now: Any = None,
) -> dict[str, Any]:
    """Reject stale, hand-authored, wrong-intent, or profile-drifted plans."""

    expected = generate_native_plan(audit, intent, config, now=now)
    checked = require_active_native_contract("plan", plan)
    if canonical_sha256(expected) != canonical_sha256(checked):
        raise PipelineError(ErrorCode.NATIVE_ARTIFACT_MISMATCH, "native plan is not deterministic")
    return checked
