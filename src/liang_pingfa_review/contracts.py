"""Strict artifact schema and semantic contract validation."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

from ezdxf.colors import float2transparency
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .canonical import (
    CanonicalJsonError,
    canonical_sha256,
    load_json_file,
    parse_utc,
    strict_json_loads,
    utc_now,
    validate_json_nesting,
    verify_integrity,
)
from .errors import ErrorCode, PipelineError
from .topology_ids import (
    derive_annotation_target_provenance_id,
    derive_chain_id,
    derive_span_id,
    derive_support_id,
    derive_topology_finding_id,
    derive_trace_id,
    entity_provenance,
)


ArtifactKind = Literal["audit", "plan", "verification"]
_SCHEMA_FILES: dict[ArtifactKind, str] = {
    "audit": "audit-v1.schema.json",
    "plan": "edit-plan-v1.schema.json",
    "verification": "verification-v1.schema.json",
}
_AUDIT_SCHEMA_FILES = {
    "liang-pingfa/audit/v1": "audit-v1.schema.json",
    "liang-pingfa/audit/v2": "audit-v2.schema.json",
}
_DEFAULT_ENTITY_TRANSPARENCY = float2transparency(0.0)
_SUPPORT_TRACE_ROLES = frozenset({"support_geometry"})
_CHAIN_ONLY_TRACE_ROLES = frozenset({"beam_edges", "beam_ids"})
_ROLE_ENTITY_TYPES: dict[str, frozenset[str]] = {
    "beam_edges": frozenset({"LINE", "LWPOLYLINE"}),
    "beam_ids": frozenset({"TEXT"}),
    "support_geometry": frozenset({"LWPOLYLINE"}),
    "support_upper_annotations": frozenset({"TEXT"}),
    "span_lower_annotations": frozenset({"TEXT"}),
    "leaders": frozenset({"LINE", "LWPOLYLINE"}),
    # A rendered ambiguity can originate from any configured role, but never
    # from an unrelated manifest type and never retains target ownership.
    "ambiguity": frozenset({"TEXT", "LINE", "LWPOLYLINE"}),
}
# A topology conclusion is meaningful only with this exact presentation and
# evidence role.  In particular, a trace that was conservatively rendered as
# ambiguity never establishes a legal or uniquely illegal annotation target.
_TOPOLOGY_FINDING_PRESENTATION: dict[
    tuple[str, str], dict[str, object]
] = {
    (
        "support_upper_annotation",
        "一致",
    ): {
        "object_position": "支座上部原位注写",
        "field": "原位注写语义位置",
        "visible_evidence": "已建立唯一拓扑位置",
        "reasoning": "角色、文本边界和唯一拓扑目标一致",
        "source_topics": ["平面与截面表达、集中与原位作用域"],
        "unreadable_parts": "无",
        "next_step": "保持只读结论",
    },
    (
        "span_lower_annotation",
        "一致",
    ): {
        "object_position": "跨中下部原位注写",
        "field": "原位注写语义位置",
        "visible_evidence": "已建立唯一拓扑位置",
        "reasoning": "角色、文本边界和唯一拓扑目标一致",
        "source_topics": ["平面与截面表达、集中与原位作用域"],
        "unreadable_parts": "无",
        "next_step": "保持只读结论",
    },
    (
        "support_upper_annotation",
        "疑似不一致",
    ): {
        "object_position": "支座上部原位注写",
        "field": "原位注写语义位置",
        "visible_evidence": "位置与已建立拓扑不相容",
        "reasoning": "角色、文本边界或引出线与唯一拓扑目标不相容",
        "source_topics": ["平面与截面表达、集中与原位作用域"],
        "unreadable_parts": "无",
        "next_step": "保持只读结论",
    },
    (
        "span_lower_annotation",
        "疑似不一致",
    ): {
        "object_position": "跨中下部原位注写",
        "field": "原位注写语义位置",
        "visible_evidence": "位置与已建立拓扑不相容",
        "reasoning": "角色、文本边界或引出线与唯一拓扑目标不相容",
        "source_topics": ["平面与截面表达、集中与原位作用域"],
        "unreadable_parts": "无",
        "next_step": "保持只读结论",
    },
    (
        "support_upper_annotation",
        "证据不足",
    ): {
        "object_position": "支座上部原位注写",
        "field": "原位注写语义位置",
        "visible_evidence": "拓扑或标注证据不足",
        "reasoning": "不以图层、距离或最近对象推断拓扑归属",
        "source_topics": ["平面与截面表达、集中与原位作用域"],
        "unreadable_parts": "拓扑或标注位置证据",
        "next_step": "补充完整可读视图后重新审计",
    },
    (
        "span_lower_annotation",
        "证据不足",
    ): {
        "object_position": "跨中下部原位注写",
        "field": "原位注写语义位置",
        "visible_evidence": "拓扑或标注证据不足",
        "reasoning": "不以图层、距离或最近对象推断拓扑归属",
        "source_topics": ["平面与截面表达、集中与原位作用域"],
        "unreadable_parts": "拓扑或标注位置证据",
        "next_step": "补充完整可读视图后重新审计",
    },
    (
        "topology",
        "证据不足",
    ): {
        "object_position": "梁图拓扑",
        "field": "原位注写语义位置",
        "visible_evidence": "拓扑或标注证据不足",
        "reasoning": "不以图层、距离或最近对象推断拓扑归属",
        "source_topics": ["平面与截面表达、集中与原位作用域"],
        "unreadable_parts": "拓扑或标注位置证据",
        "next_step": "补充完整可读视图后重新审计",
    },
}
_TOPOLOGY_FINDING_ROLE_MATRIX: dict[tuple[str, str], frozenset[str]] = {
    ("support_upper_annotation", "一致"): frozenset(
        {"support_upper_annotations"}
    ),
    ("span_lower_annotation", "一致"): frozenset({"span_lower_annotations"}),
    ("support_upper_annotation", "疑似不一致"): frozenset(
        {"support_upper_annotations"}
    ),
    ("span_lower_annotation", "疑似不一致"): frozenset(
        {"span_lower_annotations"}
    ),
    ("support_upper_annotation", "证据不足"): frozenset(
        {"support_upper_annotations", "ambiguity"}
    ),
    ("span_lower_annotation", "证据不足"): frozenset(
        {"span_lower_annotations", "ambiguity"}
    ),
    # A topology-level insufficiency can be caused by any controlled source
    # role, but it never carries a positive or uniquely illegal conclusion.
    ("topology", "证据不足"): frozenset(_ROLE_ENTITY_TYPES),
}


def _topology_manifest_eligible(manifest: Mapping[str, Any]) -> bool:
    """Require public, direct Modelspace display evidence for every trace.

    Trace fingerprints are binding evidence, not permission to reuse a hidden,
    paperspace, block, switched-off, frozen, or translucent manifest record.
    The manifest deliberately exposes only layout and display-state values;
    it never needs raw layer names, coordinates, or source text for this gate.
    """

    return bool(
        manifest["layout"] == "modelspace"
        and manifest["entity_visible"] is True
        and manifest["layer_visible"] is True
        and manifest["entity_transparency"] in (None, _DEFAULT_ENTITY_TRANSPARENCY)
        and manifest["layer_transparency"] == 0.0
    )


def _artifact_error(kind: ArtifactKind) -> ErrorCode:
    return {
        "audit": ErrorCode.AUDIT_SCHEMA_INVALID,
        "plan": ErrorCode.PLAN_SCHEMA_INVALID,
        "verification": ErrorCode.VERIFICATION_SCHEMA_INVALID,
    }[kind]


def schema_for(
    kind: ArtifactKind,
    schema_version: str | None = None,
) -> dict[str, Any]:
    """Load a packaged Draft 2020-12 schema."""

    if kind == "audit":
        filename = _AUDIT_SCHEMA_FILES.get(
            schema_version or "liang-pingfa/audit/v1",
            "",
        )
        if not filename:
            raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "unknown audit version")
    else:
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
        schema = strict_json_loads(text)
        Draft202012Validator.check_schema(schema)
    except (CanonicalJsonError, RecursionError, ValueError, SchemaError) as error:
        raise PipelineError(ErrorCode.INTERNAL_ERROR, "invalid packaged schema") from error
    return cast(dict[str, Any], schema)


def _validate_schema(kind: ArtifactKind, artifact: Mapping[str, Any]) -> None:
    schema_version = artifact.get("schema_version") if kind == "audit" else None
    if kind == "audit" and not isinstance(schema_version, str):
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "audit schema version missing")
    try:
        # Do this before jsonschema's recursive descent even for values that
        # originated from an in-process API rather than strict_json_loads().
        validate_json_nesting(artifact)
        validator = Draft202012Validator(
            schema_for(kind, cast(str | None, schema_version))
        )
        errors = sorted(
            validator.iter_errors(artifact),
            key=lambda item: list(item.path),
        )
    except (CanonicalJsonError, RecursionError) as error:
        raise PipelineError(
            _artifact_error(kind),
            "JSON Schema validation failed",
        ) from error
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
        if target["profile"] != "auxiliary-overlay-text-delete/v1":
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "audit target is outside overlay mutation profile",
            )
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
    if artifact["schema_version"] == "liang-pingfa/audit/v2":
        _validate_topology_semantics(
            artifact,
            manifest_by_handle=manifest_by_handle,
            target_handles=target_handles,
            finding_ids=set(findings_by_id),
        )


def _validate_topology_semantics(
    artifact: Mapping[str, Any],
    *,
    manifest_by_handle: Mapping[str, Mapping[str, Any]],
    target_handles: set[str],
    finding_ids: set[str],
) -> None:
    """Enforce v2's permanent topology-to-mutation firewall."""

    assessment = cast(Mapping[str, Any], artifact["topology_assessment"])
    findings = cast(list[Mapping[str, Any]], assessment["findings"])
    traces = cast(list[Mapping[str, Any]], assessment["traces"])
    chains = cast(list[Mapping[str, Any]], assessment["chains"])
    topology_finding_ids = [cast(str, finding["finding_id"]) for finding in findings]
    if len(topology_finding_ids) != len(set(topology_finding_ids)):
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "duplicate topology finding")
    if set(topology_finding_ids) & finding_ids:
        raise PipelineError(
            ErrorCode.AUDIT_SCHEMA_INVALID, "topology finding collides with overlay"
        )
    for finding in findings:
        if (
            finding["actionability"] is not False
            or finding["target_id"] is not None
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology finding attempted authorization",
            )

    chain_ids = [cast(str, chain["chain_id"]) for chain in chains]
    if len(chain_ids) != len(set(chain_ids)):
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "duplicate chain")
    trace_ids = [cast(str, trace["trace_id"]) for trace in traces]
    handles = [cast(str, trace["entity_handle"]) for trace in traces]
    if len(trace_ids) != len(set(trace_ids)) or len(handles) != len(set(handles)):
        raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "duplicate topology trace")
    chain_id_set = set(chain_ids)
    traces_by_id = {cast(str, trace["trace_id"]): trace for trace in traces}
    for trace in traces:
        handle = cast(str, trace["entity_handle"])
        manifest = manifest_by_handle.get(handle)
        if manifest is None or handle in target_handles:
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID, "topology trace does not resolve"
            )
        if (
            trace["identity_fingerprint"] != manifest["identity_fingerprint"]
            or trace["content_fingerprint"] != manifest["content_fingerprint"]
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID, "topology trace fingerprint mismatch"
            )
        if not _topology_manifest_eligible(manifest):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology trace is not eligible Modelspace evidence",
            )
        role = cast(str, trace["role"])
        if manifest["entity_type"] not in _ROLE_ENTITY_TYPES.get(role, frozenset()):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology trace has incompatible manifest entity type",
            )
        if trace["trace_id"] != derive_trace_id(
            cast(str, trace["identity_fingerprint"]),
            cast(str, trace["content_fingerprint"]),
            role,
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology trace identifier is not derived from provenance",
            )
        # V2 discloses only the result of a local token-equality gate.  The
        # schema excludes token values, equality classes, and token-only
        # fingerprints; retain an explicit runtime check for hand-built maps.
        if not isinstance(trace["token_equality_established"], bool):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "invalid topology token equality relation",
            )
        chain_id = trace["chain_id"]
        support_id = trace["support_id"]
        span_id = trace["span_id"]
        if support_id is not None and span_id is not None:
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology trace binds support and span together",
            )
        if role not in {"support_upper_annotations", "span_lower_annotations"} and (
            trace["target_provenance_id"] is not None
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "non-annotation trace has target provenance",
            )
        if chain_id is not None and chain_id not in chain_id_set:
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID, "unknown topology chain target"
            )
        if role in _CHAIN_ONLY_TRACE_ROLES:
            if support_id is not None or span_id is not None:
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "beam trace has non-chain target",
                )
            if role == "beam_ids" and chain_id is None:
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "beam ID trace lacks admitted chain",
                )
        elif role in _SUPPORT_TRACE_ROLES:
            if span_id is not None or ((chain_id is None) != (support_id is None)):
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "support trace has incomplete owner binding",
                )
        elif role == "support_upper_annotations":
            if chain_id is None or support_id is None or span_id is not None:
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "support annotation has invalid target tuple",
                )
            if trace["target_provenance_id"] != derive_annotation_target_provenance_id(
                cast(str, trace["trace_id"]),
                cast(str, chain_id),
                cast(str, support_id),
                None,
            ):
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "support annotation target provenance mismatch",
                )
        elif role == "span_lower_annotations":
            if chain_id is None or support_id is not None or span_id is None:
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "span annotation has invalid target tuple",
                )
            if trace["target_provenance_id"] != derive_annotation_target_provenance_id(
                cast(str, trace["trace_id"]),
                cast(str, chain_id),
                None,
                cast(str, span_id),
            ):
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "span annotation target provenance mismatch",
                )
        elif role == "ambiguity":
            if chain_id is not None or support_id is not None or span_id is not None:
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "ambiguity trace has an unproven target",
                )
        elif role == "leaders":
            if chain_id is not None or support_id is not None or span_id is not None:
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "leader trace has an unproven target",
                )
        else:
            raise PipelineError(ErrorCode.AUDIT_SCHEMA_INVALID, "unknown trace role")

    support_to_chain: dict[str, str] = {}
    span_to_chain: dict[str, str] = {}
    support_trace_to_id: dict[str, str] = {}
    for chain in chains:
        chain_id = cast(str, chain["chain_id"])
        support_registry = cast(list[Mapping[str, str]], chain["supports"])
        span_registry = cast(list[Mapping[str, str]], chain["spans"])
        support_ids = [
            cast(str, support["support_id"]) for support in support_registry
        ]
        span_ids = [cast(str, span["span_id"]) for span in span_registry]
        support_trace_ids = [
            cast(str, support["support_geometry_trace_id"])
            for support in support_registry
        ]
        if (
            len(support_ids) != len(set(support_ids))
            or len(span_ids) != len(set(span_ids))
            or len(support_trace_ids) != len(set(support_trace_ids))
            or len(support_ids) < 2
            or len(span_ids) != len(support_ids) - 1
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID, "invalid topology chain registry"
            )
        beam_entities = [
            entity_provenance(
                cast(str, trace["identity_fingerprint"]),
                cast(str, trace["content_fingerprint"]),
            )
            for trace in traces
            if trace["role"] == "beam_edges" and trace["chain_id"] == chain_id
        ]
        if not beam_entities or chain_id != derive_chain_id(beam_entities):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology chain identifier is not derived from member provenance",
            )
        for support in support_registry:
            support_id = cast(str, support["support_id"])
            support_trace_id = cast(str, support["support_geometry_trace_id"])
            trace = traces_by_id.get(support_trace_id)
            if (
                not support_id.startswith("support-")
                or support_id in support_to_chain
                or support_trace_id in support_trace_to_id
                or trace is None
                or trace["role"] != "support_geometry"
                or trace["chain_id"] != chain_id
                or trace["support_id"] != support_id
                or trace["span_id"] is not None
            ):
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "invalid topology support registry provenance",
                )
            if support_id != derive_support_id(
                chain_id,
                support_trace_id,
                cast(str, trace["identity_fingerprint"]),
                cast(str, trace["content_fingerprint"]),
            ):
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "topology support identifier is not derived from trace provenance",
                )
            support_to_chain[support_id] = chain_id
            support_trace_to_id[support_trace_id] = support_id
        for index, span in enumerate(span_registry):
            span_id = cast(str, span["span_id"])
            left_support_id = cast(str, span["left_support_id"])
            right_support_id = cast(str, span["right_support_id"])
            if (
                not span_id.startswith("span-")
                or span_id in span_to_chain
                or left_support_id == right_support_id
                or left_support_id != support_ids[index]
                or right_support_id != support_ids[index + 1]
            ):
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID, "invalid topology span registry"
                )
            if span_id != derive_span_id(
                chain_id,
                left_support_id,
                right_support_id,
            ):
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "topology span identifier is not derived from adjacent supports",
                )
            span_to_chain[span_id] = chain_id

    registered_support_trace_ids = set(support_trace_to_id)
    observed_support_trace_ids = {
        cast(str, trace["trace_id"])
        for trace in traces
        if trace["role"] == "support_geometry"
    }
    if observed_support_trace_ids != registered_support_trace_ids:
        raise PipelineError(
            ErrorCode.AUDIT_SCHEMA_INVALID,
            "orphan or missing canonical support geometry trace",
        )

    for trace in traces:
        chain_id = cast(str | None, trace["chain_id"])
        support_id = cast(str | None, trace["support_id"])
        span_id = cast(str | None, trace["span_id"])
        if support_id is not None and (
            chain_id is None
            or support_to_chain.get(support_id) != chain_id
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID, "unknown topology support target"
            )
        if span_id is not None and (
            chain_id is None or span_to_chain.get(span_id) != chain_id
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID, "unknown topology span target"
            )
        if trace["role"] == "support_geometry" and (
            support_trace_to_id.get(cast(str, trace["trace_id"])) != support_id
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "support geometry trace is not its registered support",
            )

    for finding in findings:
        referenced_trace_ids = cast(list[str], finding["trace_ids"])
        if (
            len(referenced_trace_ids) != 1
            or any(trace_id not in traces_by_id for trace_id in referenced_trace_ids)
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID, "topology finding has orphan trace"
            )
        category = cast(str, finding["category"])
        status = cast(str, finding["status"])
        canonical_presentation = _TOPOLOGY_FINDING_PRESENTATION.get(
            (category, status)
        )
        expected_roles = _TOPOLOGY_FINDING_ROLE_MATRIX.get((category, status))
        if canonical_presentation is None or expected_roles is None:
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology finding has unsupported status category",
            )
        if any(
            finding[field] != expected
            for field, expected in canonical_presentation.items()
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology finding presentation is not canonical",
            )
        trace = traces_by_id[referenced_trace_ids[0]]
        role = cast(str, trace["role"])
        if role not in expected_roles:
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology finding has status-incompatible trace role",
            )
        if status != "证据不足":
            # The role matrix deliberately excludes ambiguity here.  These
            # checks make the full target tuple explicit at the finding
            # boundary as well as in the trace registry validation above.
            chain_id = cast(str | None, trace["chain_id"])
            support_id = cast(str | None, trace["support_id"])
            span_id = cast(str | None, trace["span_id"])
            if chain_id is None:
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "positive topology finding lacks canonical target provenance",
                )
            expected_target_provenance = derive_annotation_target_provenance_id(
                referenced_trace_ids[0],
                chain_id,
                support_id,
                span_id,
            )
            if (
                trace["target_provenance_id"] != expected_target_provenance
            ):
                raise PipelineError(
                    ErrorCode.AUDIT_SCHEMA_INVALID,
                    "positive topology finding lacks canonical target provenance",
                )
        if finding["finding_id"] != derive_topology_finding_id(
            referenced_trace_ids[0],
            status,
            role,
            cast(str | None, trace["chain_id"]),
            cast(str | None, trace["support_id"]),
            cast(str | None, trace["span_id"]),
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "topology finding identifier is not derived from its trace",
            )

    # Ambiguity is a rendered declaration that no owner tuple was proven.  A
    # chainless beam edge is equivalently unresolved even when it preserves
    # the concrete beam role.  Both must have exactly one non-actionable,
    # canonical insufficiency conclusion; otherwise a signed audit could
    # omit, duplicate, or repoint its uncertainty evidence.
    required_trace_ids = {
        trace_id
        for trace_id, trace in traces_by_id.items()
        if trace["role"] == "ambiguity"
        or (
            trace["role"] == "beam_edges"
            and trace["chain_id"] is None
        )
    }
    findings_by_trace: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for finding in findings:
        findings_by_trace[cast(list[str], finding["trace_ids"])[0]].append(finding)
    for trace_id in required_trace_ids:
        coverage = findings_by_trace.get(trace_id, [])
        if len(coverage) != 1:
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "required topology trace has missing or duplicate coverage",
            )
        finding = coverage[0]
        if (
            finding["status"] != "证据不足"
            or finding["actionability"] is not False
            or finding["target_id"] is not None
        ):
            raise PipelineError(
                ErrorCode.AUDIT_SCHEMA_INVALID,
                "required topology trace has incompatible coverage",
            )


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
        validate_json_nesting(normalized)
        if not verify_integrity(normalized):
            raise PipelineError(_artifact_error(kind), "artifact integrity mismatch")
    except (CanonicalJsonError, RecursionError) as error:
        raise PipelineError(_artifact_error(kind), "non-canonical artifact") from error
    _validate_schema(kind, normalized)
    try:
        if kind == "audit":
            _validate_audit_semantics(normalized)
        elif kind == "plan":
            _validate_plan_semantics(normalized)
        else:
            _validate_verification_semantics(normalized)
    except (CanonicalJsonError, KeyError, RecursionError, TypeError, ValueError) as error:
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
    """Return only the overlay/base state that phase two may compare.

    Audit/v2 adds read-only topology evidence.  It is intentionally excluded
    here so apply/verify always re-audit the base audit/v1 state and cannot
    turn a topology conclusion into mutation authorization.
    """

    projection = {
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
    if projection["schema_version"] == "liang-pingfa/audit/v2":
        projection["schema_version"] = "liang-pingfa/audit/v1"
    return projection
