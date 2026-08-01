"""Deterministic phase-one edit-plan generation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .canonical import attach_integrity, canonical_sha256
from .contracts import require_fresh_audit, state_from_manifest, validate_artifact
from .errors import ErrorCode, PipelineError


RATIONALE = "删除已审计的辅助覆盖文字，不改变设计表达内容。"


def expected_after_from_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the exact permitted post-mutation state from audit targets."""

    manifest = audit["inventory"]["entity_manifest"]
    target_handles = {target["handle"] for target in audit["audited_targets"]}
    remaining = [entry for entry in manifest if entry["handle"] not in target_handles]
    fingerprints = audit["fingerprints"]
    expected = state_from_manifest(
        remaining,
        paired_right_panel_digest=fingerprints["paired_right_panel_digest"],
        bounds_fingerprint=fingerprints["bounds_fingerprint"],
        bounds_has_data=fingerprints["bounds_has_data"],
        layer_manifest_digest=audit["inventory"]["layer_manifest_digest"],
        table_style_manifest_digest=audit["inventory"][
            "table_style_manifest_digest"
        ],
        header_manifest_digest=audit["inventory"]["header_manifest_digest"],
        raw_header_manifest_digest=audit["inventory"][
            "raw_header_manifest_digest"
        ],
        objects_manifest_digest=audit["inventory"]["objects_manifest_digest"],
        classes_manifest_digest=audit["inventory"]["classes_manifest_digest"],
        raw_classes_manifest_digest=audit["inventory"][
            "raw_classes_manifest_digest"
        ],
        raw_classes_multiset_digest=audit["inventory"][
            "raw_classes_multiset_digest"
        ],
        raw_classes_record_count=audit["inventory"][
            "raw_classes_record_count"
        ],
        acdsdata_manifest_digest=audit["inventory"]["acdsdata_manifest_digest"],
        raw_section_structure_digest=audit["inventory"][
            "raw_section_structure_digest"
        ],
    )
    return expected


def generate_edit_plan(
    audit: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Generate the only valid plan shape solely from an integrity-checked audit."""

    checked_audit = validate_artifact("audit", audit)
    require_fresh_audit(checked_audit, now)
    targets = checked_audit["audited_targets"]
    if not targets:
        raise PipelineError(ErrorCode.NO_ACTIONABLE_FINDINGS, "audit has no safe targets")
    findings = {finding["finding_id"]: finding for finding in checked_audit["findings"]}
    operations: list[dict[str, Any]] = []
    for target in sorted(targets, key=lambda item: int(item["handle"], 16)):
        finding = findings[target["finding_id"]]
        operation_id = (
            "operation-"
            + canonical_sha256(
                {
                    "target_id": target["target_id"],
                    "identity": target["identity_fingerprint"],
                }
            )[:24]
        )
        operations.append(
            {
                "operation_id": operation_id,
                "kind": "delete_auxiliary_overlay_text",
                "finding_id": target["finding_id"],
                "rationale": RATIONALE,
                "source_topics": finding["source_topics"],
                "target": {
                    "handle": target["handle"],
                    "entity_type": "TEXT",
                    "layout": "modelspace",
                    "expected_before_fingerprint": target["identity_fingerprint"],
                    "expected_before_content_fingerprint": target[
                        "content_fingerprint"
                    ],
                },
                "expected_postcondition": {
                    "target_absent": True,
                    "content_fingerprint_delta": -1,
                    "no_added_entities": True,
                    "non_target_manifest_preserved": True,
                    "paired_right_panel_preserved": True,
                    "re_audit_overlay_condition_cleared": True,
                },
            }
        )
    expected_after = expected_after_from_audit(checked_audit)
    binding = {
        "audit_id": checked_audit["audit_id"],
        "audit_integrity_sha256": checked_audit["integrity"]["sha256"],
        "source_sha256": checked_audit["source"]["sha256"],
        "source_path_fingerprint": checked_audit["source"]["path_fingerprint"],
        "source_file_identity_fingerprint": checked_audit["source"][
            "file_identity_fingerprint"
        ],
    }
    plan_id = "plan-" + canonical_sha256(
        {
            "audit_integrity": binding["audit_integrity_sha256"],
            "operations": operations,
            "expected_after": expected_after,
        }
    )[:32]
    artifact = {
        "schema_version": "liang-pingfa/edit-plan/v1",
        "plan_id": plan_id,
        "created_at": checked_audit["created_at"],
        "audit_binding": binding,
        "operation_profile": "auxiliary-overlay-text-delete/v1",
        "operations": operations,
        "expected_after": expected_after,
    }
    return validate_artifact("plan", attach_integrity(artifact))


def validate_plan_against_audit(
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reject hand-written, stale, or semantically forged plans."""

    checked_audit = validate_artifact("audit", audit)
    require_fresh_audit(checked_audit, now)
    checked_plan = validate_artifact("plan", plan)
    try:
        expected = generate_edit_plan(checked_audit, now=now)
    except PipelineError:
        raise
    if canonical_sha256(expected) != canonical_sha256(checked_plan):
        raise PipelineError(ErrorCode.PLAN_AUDIT_MISMATCH, "plan is not generated from audit")
    return checked_plan
