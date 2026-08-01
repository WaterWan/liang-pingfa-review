"""Strict artifact schema and semantic contract validation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .canonical import (
    CanonicalJsonError,
    canonical_sha256,
    load_json_file,
    parse_utc,
    utc_now,
    verify_integrity,
)
from .errors import ErrorCode, PipelineError


ArtifactKind = Literal["audit", "plan", "verification"]
_SCHEMA_FILES: dict[ArtifactKind, str] = {
    "audit": "audit-v1.schema.json",
    "plan": "edit-plan-v1.schema.json",
    "verification": "verification-v1.schema.json",
}


def _artifact_error(kind: ArtifactKind) -> ErrorCode:
    return {
        "audit": ErrorCode.AUDIT_SCHEMA_INVALID,
        "plan": ErrorCode.PLAN_SCHEMA_INVALID,
        "verification": ErrorCode.VERIFICATION_SCHEMA_INVALID,
    }[kind]


def schema_for(kind: ArtifactKind) -> dict[str, Any]:
    """Load a packaged Draft 2020-12 schema."""

    filename = _SCHEMA_FILES[kind]
    try:
        text = (
            resources.files("liang_pingfa_review.schemas")
            .joinpath(filename)
            .read_text(encoding="utf-8")
        )
    except (OSError, ModuleNotFoundError) as error:
        raise PipelineError(ErrorCode.INTERNAL_ERROR, "packaged schema unavailable") from error
    try:
        import json

        schema = json.loads(text)
        Draft202012Validator.check_schema(schema)
    except (ValueError, SchemaError) as error:
        raise PipelineError(ErrorCode.INTERNAL_ERROR, "invalid packaged schema") from error
    return cast(dict[str, Any], schema)


def _validate_schema(kind: ArtifactKind, artifact: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(schema_for(kind))
    errors = sorted(validator.iter_errors(artifact), key=lambda item: list(item.path))
    if errors:
        raise PipelineError(_artifact_error(kind), "JSON Schema validation failed")


def _require_mapping(value: Any, kind: ArtifactKind) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PipelineError(_artifact_error(kind), "artifact root is not an object")
    return dict(value)


def _ensure_unique(values: list[str], kind: ArtifactKind) -> None:
    if len(values) != len(set(values)):
        raise PipelineError(_artifact_error(kind), "duplicate artifact identifiers")


def _ordered_count_records(counter: Counter[str], key: str) -> list[dict[str, Any]]:
    return [{key: item, "count": counter[item]} for item in sorted(counter)]


def ordered_entity_sequence_digest(manifest: list[Mapping[str, Any]]) -> str:
    """Digest each layout/block stream in exact draw order.

    Public entity manifests stay handle-sorted for deterministic artifact
    review, so the source order is carried by immutable per-container sequence
    indices.  The digest deliberately excludes those indices: removing an
    audited entity may compact live collection positions, while every
    non-target's relative order must remain identical.
    """

    grouped: dict[tuple[str, str], list[tuple[int, Mapping[str, Any]]]] = {}
    for item in manifest:
        layout = item["layout"]
        container = item["container_fingerprint"]
        sequence_index = item["sequence_index"]
        if (
            not isinstance(layout, str)
            or not isinstance(container, str)
            or not isinstance(sequence_index, int)
            or isinstance(sequence_index, bool)
            or sequence_index < 0
        ):
            raise ValueError("invalid entity sequence")
        grouped.setdefault((layout, container), []).append((sequence_index, item))

    containers: list[dict[str, Any]] = []
    for (layout, container), entries in sorted(grouped.items()):
        entries.sort(key=lambda item: item[0])
        indices = [index for index, _ in entries]
        if len(indices) != len(set(indices)):
            raise ValueError("duplicate entity sequence index")
        containers.append(
            {
                "layout": layout,
                "container_fingerprint": container,
                "entities": [
                    {
                        "handle": entry["handle"],
                        "entity_type": entry["entity_type"],
                        "identity_fingerprint": entry["identity_fingerprint"],
                        "content_fingerprint": entry["content_fingerprint"],
                    }
                    for _, entry in entries
                ],
            }
        )
    return canonical_sha256({"containers": containers})


def state_from_manifest(
    manifest: list[Mapping[str, Any]],
    *,
    paired_right_panel_digest: str,
    bounds_fingerprint: str,
    bounds_has_data: bool,
    layer_manifest_digest: str,
    table_style_manifest_digest: str,
    header_manifest_digest: str,
    raw_header_manifest_digest: str,
    objects_manifest_digest: str,
    classes_manifest_digest: str,
    raw_classes_manifest_digest: str,
    raw_classes_multiset_digest: str,
    raw_classes_record_count: int,
    acdsdata_manifest_digest: str,
    raw_section_structure_digest: str,
) -> dict[str, Any]:
    """Derive preservation state from entities and complete document manifests."""

    entity_counts = Counter(str(item["entity_type"]) for item in manifest)
    layer_counts = Counter(str(item["layer_fingerprint"]) for item in manifest)
    layout_counts = Counter(str(item["layout"]) for item in manifest)
    normalized_manifest = [dict(item) for item in manifest]
    entity_order_manifest_digest = ordered_entity_sequence_digest(normalized_manifest)
    # Sequence indices reconstruct draw order from the handle-sorted artifact
    # manifest, but a deletion naturally compacts a live layout collection.
    # Bind relative order through the digest above rather than treating those
    # transient indices as independently preserved DXF content.
    state_manifest = [
        {
            key: value
            for key, value in item.items()
            if key != "sequence_index"
        }
        for item in normalized_manifest
    ]
    document_manifest = {
        "entities": state_manifest,
        "entity_order_manifest_digest": entity_order_manifest_digest,
        "layer_manifest_digest": layer_manifest_digest,
        "table_style_manifest_digest": table_style_manifest_digest,
        "header_manifest_digest": header_manifest_digest,
        "raw_header_manifest_digest": raw_header_manifest_digest,
        "objects_manifest_digest": objects_manifest_digest,
        "classes_manifest_digest": classes_manifest_digest,
        "raw_classes_manifest_digest": raw_classes_manifest_digest,
        "raw_classes_multiset_digest": raw_classes_multiset_digest,
        "raw_classes_record_count": raw_classes_record_count,
        "acdsdata_manifest_digest": acdsdata_manifest_digest,
        "raw_section_structure_digest": raw_section_structure_digest,
    }
    return {
        "full_manifest_digest": canonical_sha256(document_manifest),
        "content_multiset_digest": canonical_sha256(
            sorted(str(item["content_fingerprint"]) for item in normalized_manifest)
        ),
        "entity_order_manifest_digest": entity_order_manifest_digest,
        "non_target_manifest_digest": canonical_sha256(document_manifest),
        "paired_right_panel_digest": paired_right_panel_digest,
        "bounds_fingerprint": bounds_fingerprint,
        "bounds_has_data": bounds_has_data,
        "layer_manifest_digest": layer_manifest_digest,
        "table_style_manifest_digest": table_style_manifest_digest,
        "header_manifest_digest": header_manifest_digest,
        "raw_header_manifest_digest": raw_header_manifest_digest,
        "objects_manifest_digest": objects_manifest_digest,
        "classes_manifest_digest": classes_manifest_digest,
        "raw_classes_manifest_digest": raw_classes_manifest_digest,
        "raw_classes_multiset_digest": raw_classes_multiset_digest,
        "raw_classes_record_count": raw_classes_record_count,
        "acdsdata_manifest_digest": acdsdata_manifest_digest,
        "raw_section_structure_digest": raw_section_structure_digest,
        "entity_type_counts": _ordered_count_records(entity_counts, "entity_type"),
        "layer_counts": _ordered_count_records(layer_counts, "layer_fingerprint"),
        "layout_counts": _ordered_count_records(layout_counts, "layout"),
    }


def _validate_audit_semantics(artifact: dict[str, Any]) -> None:
    inventory = cast(dict[str, Any], artifact["inventory"])
    manifest = cast(list[dict[str, Any]], inventory["entity_manifest"])
    if manifest != sorted(
        manifest,
        key=lambda item: (
            item["layout"],
            item["container_fingerprint"],
            int(item["handle"], 16),
        ),
    ):
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "manifest is not sorted")
    _ensure_unique([item["handle"] for item in manifest], "audit")
    manifest_by_handle = {item["handle"]: item for item in manifest}

    expected_pre_state = state_from_manifest(
        manifest,
        paired_right_panel_digest=artifact["fingerprints"]["paired_right_panel_digest"],
        bounds_fingerprint=artifact["fingerprints"]["bounds_fingerprint"],
        bounds_has_data=artifact["fingerprints"]["bounds_has_data"],
        layer_manifest_digest=inventory["layer_manifest_digest"],
        table_style_manifest_digest=inventory["table_style_manifest_digest"],
        header_manifest_digest=inventory["header_manifest_digest"],
        raw_header_manifest_digest=inventory["raw_header_manifest_digest"],
        objects_manifest_digest=inventory["objects_manifest_digest"],
        classes_manifest_digest=inventory["classes_manifest_digest"],
        raw_classes_manifest_digest=inventory["raw_classes_manifest_digest"],
        raw_classes_multiset_digest=inventory["raw_classes_multiset_digest"],
        raw_classes_record_count=inventory["raw_classes_record_count"],
        acdsdata_manifest_digest=inventory["acdsdata_manifest_digest"],
        raw_section_structure_digest=inventory["raw_section_structure_digest"],
    )
    if (
        inventory["entity_type_counts"] != expected_pre_state["entity_type_counts"]
        or inventory["layer_counts"] != expected_pre_state["layer_counts"]
        or inventory["layout_counts"] != expected_pre_state["layout_counts"]
        or inventory["entity_order_manifest_digest"]
        != expected_pre_state["entity_order_manifest_digest"]
        or artifact["fingerprints"]["full_manifest_digest"]
        != expected_pre_state["full_manifest_digest"]
        or artifact["fingerprints"]["content_multiset_digest"]
        != expected_pre_state["content_multiset_digest"]
        or artifact["fingerprints"]["entity_order_manifest_digest"]
        != expected_pre_state["entity_order_manifest_digest"]
    ):
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "inventory digest mismatch")

    findings = cast(list[dict[str, Any]], artifact["findings"])
    _ensure_unique([item["finding_id"] for item in findings], "audit")
    findings_by_id = {item["finding_id"]: item for item in findings}
    targets = cast(list[dict[str, Any]], artifact["audited_targets"])
    _ensure_unique([item["target_id"] for item in targets], "audit")
    _ensure_unique([item["handle"] for item in targets], "audit")

    for finding in findings:
        actionable = finding["actionability"]
        target_id = finding["target_id"]
        if actionable and (
            finding["status"] != "疑似不一致" or not isinstance(target_id, str)
        ):
            raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "invalid finding actionability")
        if not actionable and target_id is not None:
            raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "non-actionable finding has target")

    target_handles: set[str] = set()
    for target in targets:
        finding = findings_by_id.get(target["finding_id"])
        manifest_entity = manifest_by_handle.get(target["handle"])
        if finding is None or manifest_entity is None:
            raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "orphan audit target")
        if (
            not finding["actionability"]
            or finding["status"] != "疑似不一致"
            or finding["target_id"] != target["target_id"]
            or manifest_entity["entity_type"] != "TEXT"
            or manifest_entity["layout"] != "modelspace"
            or manifest_entity["identity_fingerprint"] != target["identity_fingerprint"]
            or manifest_entity["content_fingerprint"] != target["content_fingerprint"]
        ):
            raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "target does not match finding")
        target_handles.add(target["handle"])

    for finding in findings:
        if finding["actionability"] and finding["target_id"] not in {
            target["target_id"] for target in targets
        }:
            raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "actionable finding is unbound")

    non_target_manifest = [
        entity for entity in manifest if entity["handle"] not in target_handles
    ]
    expected_non_target_state = state_from_manifest(
        non_target_manifest,
        paired_right_panel_digest=artifact["fingerprints"]["paired_right_panel_digest"],
        bounds_fingerprint=artifact["fingerprints"]["bounds_fingerprint"],
        bounds_has_data=artifact["fingerprints"]["bounds_has_data"],
        layer_manifest_digest=inventory["layer_manifest_digest"],
        table_style_manifest_digest=inventory["table_style_manifest_digest"],
        header_manifest_digest=inventory["header_manifest_digest"],
        raw_header_manifest_digest=inventory["raw_header_manifest_digest"],
        objects_manifest_digest=inventory["objects_manifest_digest"],
        classes_manifest_digest=inventory["classes_manifest_digest"],
        raw_classes_manifest_digest=inventory["raw_classes_manifest_digest"],
        raw_classes_multiset_digest=inventory["raw_classes_multiset_digest"],
        raw_classes_record_count=inventory["raw_classes_record_count"],
        acdsdata_manifest_digest=inventory["acdsdata_manifest_digest"],
        raw_section_structure_digest=inventory["raw_section_structure_digest"],
    )
    if (
        artifact["fingerprints"]["non_target_manifest_digest"]
        != expected_non_target_state["full_manifest_digest"]
    ):
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "non-target digest mismatch")

    created = parse_utc(artifact["created_at"])
    expires = parse_utc(artifact["expires_at"])
    if expires != created + timedelta(hours=24):
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "invalid audit expiration")


def _validate_plan_semantics(artifact: dict[str, Any]) -> None:
    operations = cast(list[dict[str, Any]], artifact["operations"])
    handles = [item["target"]["handle"] for item in operations]
    if len(handles) != len(set(handles)):
        raise PipelineError(ErrorCode.DUPLICATE_TARGET, "duplicate plan target")
    _ensure_unique([item["operation_id"] for item in operations], "plan")
    if artifact["expected_after"]["full_manifest_digest"] != artifact["expected_after"][
        "non_target_manifest_digest"
    ]:
        raise PipelineError(ErrorCode.PLAN_SCHEMA_INVALID, "inconsistent expected state")


def _validate_verification_semantics(artifact: dict[str, Any]) -> None:
    operations = cast(list[dict[str, Any]], artifact["operation_results"])
    _ensure_unique([item["operation_id"] for item in operations], "verification")
    if artifact["expected_after"] != artifact["actual_after"]:
        raise PipelineError(ErrorCode.VERIFICATION_SCHEMA_INVALID, "failed verification state")
    output = cast(dict[str, Any], artifact["output"])
    binding = cast(dict[str, Any], artifact["output_binding"])
    if any(
        output[key] != binding[key]
        for key in ("format", "dwg_header_signature", "version_mapping")
    ):
        raise PipelineError(
            ErrorCode.VERIFICATION_SCHEMA_INVALID,
            "verification output binding disagrees with output metadata",
        )
    try:
        parse_utc(cast(str, binding["verified_at"]))
    except (CanonicalJsonError, KeyError, TypeError) as error:
        raise PipelineError(
            ErrorCode.VERIFICATION_SCHEMA_INVALID,
            "verification output binding time is invalid",
        ) from error


def validate_artifact(kind: ArtifactKind, artifact: Any) -> dict[str, Any]:
    """Validate syntax, self-integrity, schema, and strict semantic invariants."""

    normalized = _require_mapping(artifact, kind)
    try:
        if not verify_integrity(normalized):
            raise PipelineError(_artifact_error(kind), "artifact integrity mismatch")
    except CanonicalJsonError as error:
        raise PipelineError(_artifact_error(kind), "non-canonical artifact") from error
    _validate_schema(kind, normalized)
    try:
        if kind == "audit":
            _validate_audit_semantics(normalized)
        elif kind == "plan":
            _validate_plan_semantics(normalized)
        else:
            _validate_verification_semantics(normalized)
    except (CanonicalJsonError, KeyError, TypeError, ValueError) as error:
        raise PipelineError(_artifact_error(kind), "artifact semantic validation failed") from error
    return normalized


def load_artifact(kind: ArtifactKind, path: Path) -> dict[str, Any]:
    """Load a duplicate-key-safe JSON artifact and validate it completely."""

    try:
        loaded = load_json_file(path)
    except CanonicalJsonError as error:
        raise PipelineError(_artifact_error(kind), "artifact JSON is invalid") from error
    return validate_artifact(kind, loaded)


def require_fresh_audit(audit: Mapping[str, Any], now: datetime | None = None) -> None:
    """Require an audit to remain inside its fixed 24-hour validity interval."""

    try:
        created_at = parse_utc(cast(str, audit["created_at"]))
        expires_at = parse_utc(cast(str, audit["expires_at"]))
    except (CanonicalJsonError, KeyError, TypeError) as error:
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "invalid audit expiry") from error
    current = now or utc_now()
    if current.tzinfo is None:
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "naive current time")
    if current < created_at or current >= expires_at:
        raise PipelineError(ErrorCode.STALE_AUDIT, "audit has expired")


def audit_semantic_projection(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Return the source-derived audit fields that must survive a fresh audit."""

    return {
        key: audit[key]
        for key in (
            "schema_version",
            "scope",
            "source",
            "toolchain",
            "conversion",
            "inventory",
            "fingerprints",
            "findings",
            "audited_targets",
        )
    }
