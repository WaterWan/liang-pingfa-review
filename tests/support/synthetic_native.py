"""Generated, source-free native bridge contract fixtures for public tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from liang_pingfa_review.canonical import attach_integrity, canonical_sha256, format_utc
from liang_pingfa_review.native_contracts import (
    bits_from_float,
    geometry_document_binding_digest,
    MAX_NATIVE_GEOMETRY_JSON_BYTES,
    MAX_NATIVE_SESSION_LIFETIME_MILLISECONDS,
    native_geometry_host_binding_digest,
    native_session_binding_digest,
    validate_native_contract,
)
from liang_pingfa_review.native_bridge import read_native_session_clock
from liang_pingfa_review.native_protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    PROTOCOL_VERSION,
    derive_challenge_response,
)


def digest(seed: str) -> str:
    return sha256(seed.encode("utf-8")).hexdigest()


def source() -> dict[str, Any]:
    return {
        "format": "DWG",
        "sha256": digest("source"),
        "byte_size": 128,
        "path_fingerprint": digest("path"),
        "file_identity_fingerprint": digest("identity"),
        "dwg_header_signature": "AC1032",
    }


def adapter() -> dict[str, str]:
    return {"id": "test-adapter", "profile": "test-profile", "version": "1.0.0"}


def plugin() -> dict[str, str]:
    return {
        "id": "test-plugin",
        "version": "1.0.0",
        "fingerprint": digest("readback-plugin"),
    }


def config() -> dict[str, Any]:
    return {
        "schema_version": "liang-pingfa/native-adapter-config/v2",
        "adapter": adapter(),
        "protocol": {"major": 1, "minor": 0},
        "required_capabilities": [
            "read.inventory/v1",
            "read.exact_geometry/v1",
        ],
        "core_console": {"path": "generated-core.exe", "sha256": digest("core")},
        "plugins": {
            "write": {
                "id": "test-plugin",
                "version": "1.0.0",
                "path": "generated-write.dll",
                "sha256": digest("write-plugin"),
                "command": "LPF_NATIVE_EXECUTE_MANIFEST",
            },
            "readback": {
                "id": "test-plugin",
                "version": "1.0.0",
                "path": "generated-readback.dll",
                "sha256": digest("readback-plugin"),
                "command": "LPF_NATIVE_EXPORT_MANIFEST",
            },
        },
        "host_compatibility": {
            "host_family": "external-host",
            "minimum_version": "1.0",
            "maximum_version": "9.9",
            "host_product": "external-host",
            "host_release": "1.0",
            "host_runtime": "test-runtime",
            "audit_host_mode": "full_host",
            "core_console_mode": "core_console",
        },
        "timeouts": {
            "pipe_connect_ms": 5000,
            "health_ms": 3000,
            "session_ms": 3000,
            "document_ms": 5000,
            "inventory_ms": 30000,
            "geometry_ms": 60000,
            "write_console_seconds": 120,
            "readback_console_seconds": 60,
        },
        "geometry_limits": {
            "max_entities": 2000,
            "max_segments": 10000,
            "max_geometry_json_bytes": MAX_NATIVE_GEOMETRY_JSON_BYTES,
            "max_inventory_json_bytes": 64 * 1024,
        },
        "write_revision_transition": "save_reopen_changes_revision",
        "operation_profiles": {
            "translate_dbtext/v1": True,
            "delete_auxiliary_overlay_text/v1": True,
            "create_review_marker/v1": False,
        },
        "marker_policy": {
            "policy_version": "marker-policy/v1",
            "profile": "create_review_marker/v1",
            "enabled": False,
            "plugin_capability": False,
            "layer": "REVIEW",
            "style": "STANDARD",
            "layer_fingerprint": digest("marker-layer"),
            "style_fingerprint": digest("marker-style"),
            "height_bits": bits_from_float(2.5),
            "rotation_bits": bits_from_float(0.0),
            "text_prefix": "LPF-REVIEW-",
            "text_derivation_version": "operation-id-suffix/v1",
            "geometry_defaults": {
                "space_kind": "modelspace",
                "block_path": [],
                "overlay_evidence": {
                    "unique_content": False,
                    "left_panel": False,
                    "corresponding_right_absent": False,
                    "visible_interference": False,
                    "unsupported_data": True,
                },
            },
        },
    }


def _vector(x: float, y: float, z: float = 0.0) -> list[str]:
    return [bits_from_float(item) for item in (x, y, z)]


def entity(
    handle: str,
    *,
    native_type: str = "DBTEXT",
    sequence_index: int = 0,
    layer: str | None = "TEMP",
    text: str | None = "generated",
    style: str | None = "STANDARD",
    position: tuple[float, float, float] = (1.0, 2.0, 0.0),
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]]
    | None = None,
    height: float = 2.5,
    block_path: list[str] | None = None,
    space_kind: str = "modelspace",
    owner_handle: str = "AA",
    evidence: dict[str, bool] | None = None,
) -> dict[str, Any]:
    if bounds is None:
        bounds = (position, position)
    if native_type == "LINE":
        text = None
        style = None
        segments = [{"start": _vector(*position), "end": _vector(position[0] + 1, position[1], position[2])}]
    elif native_type == "LWPOLYLINE":
        text = None
        style = None
        segments = [{"start": _vector(*position), "end": _vector(position[0] + 1, position[1], position[2])}]
    else:
        segments = []
    if native_type == "OPAQUE":
        text = None
        style = None
        layer = None
    space = {
        "kind": space_kind,
        "layout_handle": "BB" if space_kind != "block" else None,
        "block_handle": "CC" if space_kind == "block" else None,
    }
    result = {
        "handle": handle,
        "native_type": native_type,
        "owner_handle": owner_handle,
        "space": space,
        "block_path": block_path or [],
        "sequence_index": sequence_index,
        "layer": layer,
        "text": text,
        "style": style,
        "height": bits_from_float(height),
        "rotation": bits_from_float(0.0),
        "position": _vector(*position),
        "bounds": {
            "minimum": _vector(*bounds[0]),
            "maximum": _vector(*bounds[1]),
        },
        "segments": segments,
        "overlay_evidence": evidence
        or {
            "unique_content": True,
            "left_panel": True,
            "corresponding_right_absent": True,
            "visible_interference": True,
            "unsupported_data": False,
        },
    }
    projection = dict(result)
    result["geometry_fingerprint"] = canonical_sha256({"geometry": projection})
    result["opaque_state_digest"] = canonical_sha256({"opaque_state": projection})
    return result


def _container(entity_value: dict[str, Any]) -> tuple[Any, ...]:
    space = entity_value["space"]
    return (
        space["kind"],
        space["layout_handle"] or "",
        space["block_handle"] or "",
        tuple(entity_value["block_path"]),
    )


def _projection(entity_value: dict[str, Any]) -> dict[str, Any]:
    result = dict(entity_value)
    result.pop("geometry_fingerprint")
    result.pop("opaque_state_digest")
    return result


def geometry(
    entities: list[dict[str, Any]] | None = None,
    *,
    owners: list[str] | None = None,
    source_value: dict[str, Any] | None = None,
    session_value: dict[str, Any] | None = None,
    session_id: str = "native-session-0123456789abcdef0123456789abcdef",
    capabilities: list[str] | None = None,
    database_instance: str | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    selected_source = source_value or source()
    if session_value is None:
        selected_adapter = adapter()
        selected_plugin = plugin()
        selected_host = {
            "product": "external-host",
            "release": "1.0",
            "runtime": "test-runtime",
            "mode": "full_host",
        }
        selected_capabilities = capabilities or [
            "read.inventory/v1",
            "read.exact_geometry/v1",
        ]
        selected_process = {
            "pid": 1234,
            "windows_session_id": 1,
            "instance_fingerprint": digest("process"),
            "creation_time_100ns": "123456789",
            "executable_fingerprint": digest("host-executable"),
        }
        selected_database = database_instance or digest("database")
        selected_revision = revision or digest("revision")
        binding_session: dict[str, Any] = {
            "session_id": session_id,
            "adapter": selected_adapter,
            "plugin": selected_plugin,
            "host": selected_host,
            "capabilities": selected_capabilities,
            "process": {
                key: selected_process[key]
                for key in (
                    "instance_fingerprint",
                    "creation_time_100ns",
                    "executable_fingerprint",
                )
            },
            "current_document": {
                "saved": True,
                "path_fingerprint": selected_source["path_fingerprint"],
                "file_identity_fingerprint": selected_source[
                    "file_identity_fingerprint"
                ],
                "sha256": selected_source["sha256"],
                "byte_size": selected_source["byte_size"],
                "dwg_header_signature": selected_source["dwg_header_signature"],
                "database_instance_fingerprint": selected_database,
                "revision_fingerprint": selected_revision,
            },
        }
    else:
        binding_session = session_value
        session_id = binding_session["session_id"]
        selected_adapter = binding_session["adapter"]
        selected_plugin = binding_session["plugin"]
        selected_host = binding_session["host"]
        selected_capabilities = binding_session["capabilities"]
        selected_process = {
            "pid": binding_session["pid"],
            "windows_session_id": binding_session["windows_session_id"],
            **binding_session["process"],
        }
        selected_database = (
            database_instance
            or binding_session["current_document"]["database_instance_fingerprint"]
        )
        selected_revision = (
            revision or binding_session["current_document"]["revision_fingerprint"]
        )
    records = sorted(
        [entity("10")] if entities is None else entities,
        key=lambda item: (*_container(item), item["sequence_index"]),
    )
    selected_owners = ["AA"] if owners is None else list(owners)
    order = [
        {
            "container": _container(item),
            "sequence_index": item["sequence_index"],
            "handle": item["handle"],
            "geometry_fingerprint": item["geometry_fingerprint"],
            "opaque_state_digest": item["opaque_state_digest"],
        }
        for item in records
    ]
    container_order = []
    for container in sorted({_container(item) for item in records}):
        container_order.append(
            {
                "container": container,
                "entities": [
                    {
                        "geometry_fingerprint": item["geometry_fingerprint"],
                        "handle": item["handle"],
                        "opaque_state_digest": item["opaque_state_digest"],
                        "sequence_index": item["sequence_index"],
                    }
                    for item in records
                    if _container(item) == container
                ],
            }
        )
    document_state = {
        "table_state_digest": digest("tables"),
        "layout_state_digest": digest("layouts"),
        "block_state_digest": digest("blocks"),
        "marker_layer_fingerprint": digest("marker-layer"),
        "marker_style_fingerprint": digest("marker-style"),
    }
    document_state_digest = canonical_sha256(document_state)
    document = {
        "database_instance_fingerprint": selected_database,
        "revision_fingerprint": selected_revision,
        "ordered_entity_digest": canonical_sha256(order),
        "container_order_digest": canonical_sha256(container_order),
        "complete_geometry_digest": canonical_sha256([_projection(item) for item in records]),
        "protected_state_digest": canonical_sha256(
            {
                "document_state_digest": document_state_digest,
                "owners": selected_owners,
                "opaque_state_digests": [item["opaque_state_digest"] for item in records],
            }
        ),
        "protected_order_digest": canonical_sha256(
            {
                "container_sequences": container_order,
                "document_state_digest": document_state_digest,
                "owners": selected_owners,
            }
        ),
        **document_state,
        "document_state_digest": document_state_digest,
    }
    artifact = {
        "schema_version": "liang-pingfa/native-geometry-export/v2",
        "source": selected_source,
        "binding": {
            "session_id": session_id,
            "session_schema_version": "liang-pingfa/native-bridge-session/v2",
            "protocol_version": PROTOCOL_VERSION,
            "protocol_major": PROTOCOL_MAJOR,
            "protocol_minor": PROTOCOL_MINOR,
            "host": selected_host,
            "process": selected_process,
            "adapter": selected_adapter,
            "plugin": selected_plugin,
            "capabilities": selected_capabilities,
        },
        "document": document,
        "owners": selected_owners,
        "entities": records,
    }
    artifact["binding"]["session_binding_digest"] = native_session_binding_digest(
        binding_session
    )
    artifact["binding"][
        "stable_host_binding_digest"
    ] = native_geometry_host_binding_digest(binding_session)
    artifact["binding"][
        "document_binding_digest"
    ] = geometry_document_binding_digest(artifact)
    return validate_native_contract("geometry", attach_integrity(artifact))


def session(
    *,
    source_value: dict[str, Any] | None = None,
    database_instance: str | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    current = source_value or source()
    now = datetime.now(UTC)
    clock = read_native_session_clock()
    artifact = {
        "schema_version": "liang-pingfa/native-bridge-session/v2",
        "config_schema_version": "liang-pingfa/native-adapter-config/v2",
        "session_id": "native-session-0123456789abcdef0123456789abcdef",
        "created_at": format_utc(now),
        "expires_at": format_utc(now + timedelta(minutes=5)),
        "monotonic_clock": clock.clock,
        "monotonic_boot_id": clock.boot_id,
        "monotonic_issued": str(clock.uptime_milliseconds),
        "monotonic_expires": str(
            clock.uptime_milliseconds + MAX_NATIVE_SESSION_LIFETIME_MILLISECONDS
        ),
        "mode": "read_only",
        "pid": 1234,
        "windows_session_id": 1,
        "process": {
            "instance_fingerprint": digest("process"),
            "creation_time_100ns": "123456789",
            "executable_fingerprint": digest("host-executable"),
        },
        "pipe_name": r"\\.\pipe\liang-pingfa-native-a1b2c3d4e5f6g7h8",
        "client_nonce": "a" * 43,
        "challenge": "b" * 43,
        "bridge_nonce": "c" * 43,
        "challenge_response": derive_challenge_response(
            "a" * 43,
            "b" * 43,
            "c" * 43,
            session_id="native-session-0123456789abcdef0123456789abcdef",
        ),
        "adapter": adapter(),
        "plugin": plugin(),
        "host": {
            "product": "external-host",
            "release": "1.0",
            "runtime": "test-runtime",
            "mode": "full_host",
        },
        "current_document": {
            "saved": True,
            "path_fingerprint": current["path_fingerprint"],
            "file_identity_fingerprint": current["file_identity_fingerprint"],
            "sha256": current["sha256"],
            "byte_size": current["byte_size"],
            "dwg_header_signature": current["dwg_header_signature"],
            "database_instance_fingerprint": database_instance or digest("database"),
            "revision_fingerprint": revision or digest("revision"),
        },
        "capabilities": [
            "read.inventory/v1",
            "read.exact_geometry/v1",
        ],
    }
    return validate_native_contract("session", attach_integrity(artifact))


def intent(
    audit: dict[str, Any],
    *,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    artifact = {
        "schema_version": "liang-pingfa/native-edit-intent/v2",
        "intent_id": "native-intent-" + digest("intent")[:32],
        "created_at": format_utc(now),
        "audit_binding": {
            "audit_id": audit["audit_id"],
            "audit_integrity_sha256": audit["integrity"]["sha256"],
            "audit_schema_version": audit["schema_version"],
        },
        "operations": operations,
    }
    return validate_native_contract("intent", attach_integrity(artifact))
