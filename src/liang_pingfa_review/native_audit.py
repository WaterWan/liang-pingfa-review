"""Read-only, redacted native audit construction.

Exact geometry is accepted only in memory/private staging.  The durable audit
contains opaque IDs and fingerprints, never text, coordinates, layer names,
block paths, handles, pipe names, or raw bridge responses.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from .canonical import (
    acquire_source_lease,
    attach_integrity,
    format_utc,
    normalize_nfc_text,
    utc_now,
)
from .errors import ErrorCode, PipelineError
from .native_bridge import NativeBridgeClient
from .native_contracts import (
    AUTOCAD_ADAPTER_ID,
    canonical_geometry_json_bytes,
    derive_native_target_id,
    geometry_adapter_binding,
    geometry_document_binding,
    native_artifact_integrity,
    native_execution_stable_host_binding_digest,
    native_host_binding,
    native_marker_policy_binding,
    PRIVATE_RECORD_CARDINALITY,
    require_active_native_contract,
    require_geometry_export_matches_session,
    validate_native_contract,
)


_DWG_HEADER = re.compile(r"^AC[0-9A-Z]{4}$")


def native_source_from_lease(lease: Any) -> dict[str, Any]:
    """Describe a leased native DWG without inheriting the ODA version profile."""

    try:
        lease.require_binding()
        binding = lease.binding
        header = lease.read_prefix(6).decode("ascii", errors="strict")
    except Exception as error:
        raise PipelineError(
            ErrorCode.SOURCE_CHANGED_DURING_RUN,
            "native source lease is not stable",
        ) from error
    if (
        binding.is_directory
        or binding.sha256 is None
        or binding.byte_size is None
        or _DWG_HEADER.fullmatch(header) is None
    ):
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "native input is not a DWG")
    return {
        "format": "DWG",
        "sha256": binding.sha256,
        "byte_size": binding.byte_size,
        "path_fingerprint": sha256(
            normalize_nfc_text(str(lease.path)).encode("utf-8")
        ).hexdigest(),
        "file_identity_fingerprint": binding.file_identity_fingerprint,
        "dwg_header_signature": header,
    }


def _eligible_profiles(
    entity: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[str]:
    """Return the fixed no-command mutation profiles proven by raw evidence."""

    direct_modelspace = (
        entity["native_type"] == "DBTEXT"
        and entity["space"]["kind"] == "modelspace"
        and not entity["block_path"]
    )
    eligible: list[str] = []
    configured_profiles = config["operation_profiles"]
    capabilities = set(config["required_capabilities"])
    adapter_requires_capability = config["adapter"]["id"] == AUTOCAD_ADAPTER_ID
    if (
        direct_modelspace
        and configured_profiles["translate_dbtext/v1"] is True
        and (
            not adapter_requires_capability
            or "translate_dbtext/v1" in capabilities
        )
    ):
        eligible.append("translate_dbtext/v1")
        evidence = entity["overlay_evidence"]
        if (
            configured_profiles["delete_auxiliary_overlay_text/v1"] is True
            and (
                not adapter_requires_capability
                or "delete_auxiliary_overlay_text/v1" in capabilities
            )
            and
            isinstance(entity["layer"], str)
            and entity["layer"].casefold() in {"temp", "textarea"}
            and evidence["unique_content"] is True
            and evidence["left_panel"] is True
            and evidence["corresponding_right_absent"] is True
            and evidence["visible_interference"] is True
            and evidence["unsupported_data"] is False
        ):
            eligible.append("delete_auxiliary_overlay_text/v1")
    return eligible


def build_native_audit(
    export: Mapping[str, Any],
    session: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    expected_source: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a 15-minute redacted native audit from one exact raw export."""

    # Direct callers hand us a decoded mapping rather than bridge text. Count
    # its sole canonical raw representation before validating/copying it so
    # this entry point cannot bypass the geometry UTF-8 byte ceiling.
    canonical_geometry_json_bytes(
        export,
        error=ErrorCode.NATIVE_GEOMETRY_INVALID,
    )
    checked_config = require_active_native_contract("config", config)
    checked_export, checked_session = require_geometry_export_matches_session(
        export,
        session,
        expected_source=expected_source,
    )
    source = checked_export["source"]
    checked_native_host_binding = native_host_binding(checked_session, checked_config)
    marker_policy_binding = native_marker_policy_binding(checked_config)
    records: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for entity in checked_export["entities"]:
        target_id = derive_native_target_id(entity)
        profiles = _eligible_profiles(entity, checked_config)
        record = {
            "target_id": target_id,
            "native_type": entity["native_type"],
            "before_geometry_fingerprint": entity["geometry_fingerprint"],
            "opaque_state_digest": entity["opaque_state_digest"],
            "eligible_profiles": profiles,
        }
        records.append(record)
        for profile in profiles:
            findings.append(
                {
                    "finding_id": "native-finding-"
                    + sha256(
                        f"{target_id}:{profile}".encode("ascii")
                    ).hexdigest()[:24],
                    "status": "actionable",
                    "actionability": True,
                    "target_id": target_id,
                    "profile": profile,
                }
            )
    current = now or utc_now()
    artifact = {
        "schema_version": "liang-pingfa/native-audit/v2",
        "config_schema_version": checked_config["schema_version"],
        "session_schema_version": checked_session["schema_version"],
        "geometry_schema_version": checked_export["schema_version"],
        "audit_id": "native-audit-" + sha256(
            (
                checked_export["document"]["complete_geometry_digest"]
                + checked_export["binding"]["document_binding_digest"]
                + format_utc(current)
            ).encode("ascii")
        ).hexdigest()[:32],
        "created_at": format_utc(current),
        "expires_at": format_utc(current + timedelta(minutes=15)),
        "scope": "native-representation-and-readability-only",
        "source": source,
        "adapter_binding": geometry_adapter_binding(checked_export),
        "host_executable_fingerprint": checked_export["binding"]["process"][
            "executable_fingerprint"
        ],
        "native_host_binding": checked_native_host_binding,
        "stable_host_binding_digest": native_execution_stable_host_binding_digest(
            checked_export,
            marker_policy_binding,
        ),
        # These are taken from the exact tuple the shared gate validated,
        # rather than recomputed from independently accepted inputs.
        "session_binding_digest": checked_export["binding"]["session_binding_digest"],
        "geometry_document_binding_digest": checked_export["binding"][
            "document_binding_digest"
        ],
        "document_binding": geometry_document_binding(checked_export),
        "protected_state_digest": checked_export["document"]["protected_state_digest"],
        "marker_prerequisites": {
            # Full-host bridge and Core Console snapshots are deliberately
            # policy-independent. The exact configured policy is bound here;
            # Core preflight validates its layer/style resources against the
            # private copy's complete table map before any marker mutation.
            "layer_fingerprint": marker_policy_binding["layer_fingerprint"],
            "style_fingerprint": marker_policy_binding["style_fingerprint"],
        },
        "marker_policy_binding": marker_policy_binding,
        "records": sorted(records, key=lambda item: item["target_id"]),
        "findings": sorted(findings, key=lambda item: item["finding_id"]),
        # This private machine-readable artifact enumerates opaque records and
        # findings, so its local cardinality is necessarily visible.
        "record_cardinality": PRIVATE_RECORD_CARDINALITY,
    }
    return validate_native_contract("audit", attach_integrity(artifact))


def require_fresh_native_audit(
    audit: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Require a current integrity-checked native audit before planning/writing."""

    checked = require_active_native_contract("audit", audit)
    current = now or utc_now()
    try:
        created = __import__(
            "liang_pingfa_review.canonical", fromlist=["parse_utc"]
        ).parse_utc(checked["created_at"])
        expires = __import__(
            "liang_pingfa_review.canonical", fromlist=["parse_utc"]
        ).parse_utc(checked["expires_at"])
    except Exception as error:
        raise PipelineError(ErrorCode.NATIVE_AUDIT_SCHEMA_INVALID, "native audit time invalid") from error
    if current < created or current >= expires:
        raise PipelineError(ErrorCode.NATIVE_SESSION_EXPIRED, "native audit expired")
    return checked


@contextmanager
def bound_native_audit(
    source_path: Path,
    session: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield an audit while the original input's no-follow lease remains held."""

    source_lease = acquire_source_lease(source_path)
    client: NativeBridgeClient | None = None
    try:
        source = native_source_from_lease(source_lease)
        checked_session = require_active_native_contract("session", session)
        client = NativeBridgeClient(checked_session, config=config)
        export = client.export_exact_geometry()
        audit = build_native_audit(
            export,
            checked_session,
            config,
            expected_source=source,
            now=now,
        )
        yield audit
        source_lease.require_binding()
    finally:
        if client is not None:
            client.close()
        source_lease.close()


def native_audit(
    source_path: Path,
    session: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a native audit without retaining raw geometry beyond the call."""

    with bound_native_audit(source_path, session, config, now=now) as artifact:
        return artifact


def native_audit_binding(audit: Mapping[str, Any]) -> dict[str, str]:
    """Return exactly the public-safe audit identity used by later artifacts."""

    checked = validate_native_contract("audit", audit)
    return {
        "audit_id": checked["audit_id"],
        "audit_integrity_sha256": native_artifact_integrity(checked),
    }
