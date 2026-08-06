"""Strict contracts for the isolated optional native bridge lane.

Nothing in this module imports an AutoCAD, TSSD, ODA, or other proprietary
assembly.  It validates only project-owned JSON contracts and deliberately
treats bridge/plugin self-attestations as corruption checks rather than
authentication against a hostile local account.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from importlib import resources
import json
import math
import os
from pathlib import Path
import re
import struct
from typing import Any, Final, Literal, TypeVar, cast
from jsonschema import Draft202012Validator, validators
from jsonschema._utils import equal, unbool
from jsonschema.exceptions import SchemaError, ValidationError

from .canonical import (
    CanonicalJsonError,
    DeadlineCheckpointSampler,
    MAX_JSON_STRING_CODEPOINTS,
    OpaqueJsonStringRules,
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
    format_utc,
    normalize_json_value,
    parse_utc,
    strict_json_loads,
    utc_now,
    validate_json_canonical_form,
    validate_json_nesting,
    validate_json_string_limits,
    verify_integrity,
)
from .errors import ErrorCode, PipelineError
from .ownership import (
    FileOwnershipBackend,
    OwnedPath,
    OwnershipError,
    acquire_lexical_directory_chain,
    current_user_sid,
    lexical_absolute_path,
    platform_backend,
    verify_private_staging_file,
)
from .native_protocol import (
    CONNECT_TIMEOUT_SECONDS,
    CONSOLE_TIMEOUT_SECONDS,
    MAX_NATIVE_CONSOLE_RESULT_BYTES,
    MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES,
    MAX_NATIVE_OPERATION_COUNT,
    METHOD_TIMEOUT_CONFIG_KEYS,
    METHOD_TIMEOUT_SECONDS,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    PROTOCOL_VERSION,
    NativeProtocolError,
    derive_challenge_response,
)


NativeArtifactKind = Literal[
    "session",
    "geometry",
    "audit",
    "intent",
    "plan",
    "manifest",
    "console_result",
    "console_export",
    "verification",
]
NativeSchemaKind = Literal[
    "config",
    "request",
    "response",
    "inventory",
    *NativeArtifactKind,
]
_PrivateReadResult = TypeVar("_PrivateReadResult")
_DeadlineCheck = Callable[[str], None]
MAX_NATIVE_SESSION_LIFETIME: Final[timedelta] = timedelta(minutes=5)
# A private descriptor records Windows' cross-process GetTickCount64 value in
# milliseconds, serialized as strict decimal text so it remains exact in every
# JSON implementation (including those without unsigned 64-bit numbers).
NATIVE_SESSION_MONOTONIC_CLOCK: Final = "windows-gettickcount64-ms/v1"
MAX_NATIVE_SESSION_UPTIME_MILLISECONDS: Final = (1 << 64) - 1
MAX_NATIVE_SESSION_LIFETIME_MILLISECONDS: Final = int(
    MAX_NATIVE_SESSION_LIFETIME.total_seconds() * 1000
)
_NATIVE_SESSION_UPTIME_PATTERN: Final = re.compile(r"^(?:0|[1-9][0-9]{0,19})$")
_NATIVE_SESSION_BOOT_ID_PATTERN: Final = re.compile(r"^[a-f0-9]{32}$")

_SCHEMA_FILES: dict[NativeSchemaKind, str] = {
    # The wire protocol is genuinely frozen at v1.  Its envelopes do not
    # acquire session-lifetime or write-authorization fields.
    "request": "native-bridge-request-v1.schema.json",
    "response": "native-bridge-response-v1.schema.json",
    # Every active persisted/native-workflow artifact uses an explicit v2
    # namespace.  v1 files are retained only by _SUPPORTED_SCHEMA_FILES for
    # legacy validation, reporting, and deliberately narrow migration.
    "config": "native-adapter-config-v2.schema.json",
    "inventory": "native-inventory-export-v2.schema.json",
    "session": "native-bridge-session-v2.schema.json",
    "geometry": "native-geometry-export-v2.schema.json",
    "audit": "native-audit-v2.schema.json",
    "intent": "native-edit-intent-v2.schema.json",
    "plan": "native-edit-plan-v2.schema.json",
    "manifest": "native-edit-manifest-v2.schema.json",
    "console_result": "native-console-result-v2.schema.json",
    "console_export": "native-console-export-v2.schema.json",
    "verification": "native-verification-v2.schema.json",
}

_ACTIVE_SCHEMA_VERSIONS: Final[dict[NativeSchemaKind, str]] = {
    "config": "liang-pingfa/native-adapter-config/v2",
    "request": "liang-pingfa/native-bridge/v1",
    "response": "liang-pingfa/native-bridge/v1",
    "inventory": "liang-pingfa/native-inventory-export/v2",
    "session": "liang-pingfa/native-bridge-session/v2",
    "geometry": "liang-pingfa/native-geometry-export/v2",
    "audit": "liang-pingfa/native-audit/v2",
    "intent": "liang-pingfa/native-edit-intent/v2",
    "plan": "liang-pingfa/native-edit-plan/v2",
    "manifest": "liang-pingfa/native-edit-manifest/v2",
    "console_result": "liang-pingfa/native-console-result/v2",
    "console_export": "liang-pingfa/native-console-export/v2",
    "verification": "liang-pingfa/native-verification/v2",
}

_VERSION_FIELD_BY_KIND: Final[dict[NativeSchemaKind, str]] = {
    "request": "protocol_version",
    "response": "protocol_version",
}

_SUPPORTED_SCHEMA_FILES: Final[dict[NativeSchemaKind, dict[str, str]]] = {
    "config": {
        "liang-pingfa/native-adapter-config/v1": "native-adapter-config-v1.schema.json",
        "liang-pingfa/native-adapter-config/v2": "native-adapter-config-v2.schema.json",
    },
    "request": {
        "liang-pingfa/native-bridge/v1": "native-bridge-request-v1.schema.json",
    },
    "response": {
        "liang-pingfa/native-bridge/v1": "native-bridge-response-v1.schema.json",
    },
    "inventory": {
        "liang-pingfa/native-inventory-export/v2": "native-inventory-export-v2.schema.json",
    },
    "session": {
        "liang-pingfa/native-bridge-session/v1": "native-bridge-session-v1.schema.json",
        "liang-pingfa/native-bridge-session/v2": "native-bridge-session-v2.schema.json",
    },
    "geometry": {
        "liang-pingfa/native-geometry-export/v1": "native-geometry-export-v1.schema.json",
        "liang-pingfa/native-geometry-export/v2": "native-geometry-export-v2.schema.json",
    },
    "audit": {
        "liang-pingfa/native-audit/v1": "native-audit-v1.schema.json",
        "liang-pingfa/native-audit/v2": "native-audit-v2.schema.json",
    },
    "intent": {
        "liang-pingfa/native-edit-intent/v1": "native-edit-intent-v1.schema.json",
        "liang-pingfa/native-edit-intent/v2": "native-edit-intent-v2.schema.json",
    },
    "plan": {
        "liang-pingfa/native-edit-plan/v1": "native-edit-plan-v1.schema.json",
        "liang-pingfa/native-edit-plan/v2": "native-edit-plan-v2.schema.json",
    },
    "manifest": {
        "liang-pingfa/native-edit-manifest/v1": "native-edit-manifest-v1.schema.json",
        "liang-pingfa/native-edit-manifest/v2": "native-edit-manifest-v2.schema.json",
    },
    "console_result": {
        "liang-pingfa/native-console-result/v1": "native-console-result-v1.schema.json",
        "liang-pingfa/native-console-result/v2": "native-console-result-v2.schema.json",
    },
    "console_export": {
        "liang-pingfa/native-console-export/v1": "native-console-export-v1.schema.json",
        "liang-pingfa/native-console-export/v2": "native-console-export-v2.schema.json",
    },
    "verification": {
        "liang-pingfa/native-verification/v1": "native-verification-v1.schema.json",
        "liang-pingfa/native-verification/v2": "native-verification-v2.schema.json",
    },
}
_ARTIFACT_ERRORS: dict[NativeArtifactKind, ErrorCode] = {
    "session": ErrorCode.NATIVE_SESSION_INVALID,
    "geometry": ErrorCode.NATIVE_GEOMETRY_INVALID,
    "audit": ErrorCode.NATIVE_AUDIT_SCHEMA_INVALID,
    "intent": ErrorCode.NATIVE_INTENT_SCHEMA_INVALID,
    "plan": ErrorCode.NATIVE_PLAN_SCHEMA_INVALID,
    "manifest": ErrorCode.NATIVE_MANIFEST_INVALID,
    "console_result": ErrorCode.NATIVE_CONSOLE_RESULT_INVALID,
    "console_export": ErrorCode.NATIVE_READBACK_INVALID,
    "verification": ErrorCode.NATIVE_VERIFICATION_INVALID,
}
# The original 100k/250k envelope allowed valid responses to outlive the
# fixed 60-second RPC budget on ordinary CI hardware.  Geometry's published
# v1 bounds remain readable; active v2 preserves these conservative geometry
# bounds while reducing mutable operation cardinality to fit result transport.
MAX_NATIVE_GEOMETRY_ENTITIES: Final = 2_000
MAX_NATIVE_GEOMETRY_SEGMENTS: Final = 10_000
MAX_NATIVE_GEOMETRY_CONTAINERS: Final = MAX_NATIVE_GEOMETRY_ENTITIES + 1
MAX_NATIVE_GEOMETRY_SEQUENCE_INDEX: Final = 1_000_000
# A count is an extent, rather than an index: the maximum legal active index
# requires one additional erased-inclusive physical slot.
MAX_NATIVE_PHYSICAL_SLOT_COUNT: Final = MAX_NATIVE_GEOMETRY_SEQUENCE_INDEX + 1
# This is a UTF-8 *byte* ceiling, not a JSON Schema ``maxLength`` ceiling.
# Schemas retain their code-point bound as a secondary structural constraint,
# while every raw/embedded geometry boundary calls the bounded helper below.
MAX_NATIVE_GEOMETRY_JSON_BYTES: Final = 16 * 1024 * 1024
MAX_NATIVE_INVENTORY_JSON_BYTES: Final = 64 * 1024
# The geometry schema's largest legitimate field is DBTEXT ``text`` at
# 4,096 Unicode code points.  Apply that tighter limit before any inner
# geometry scalar reaches NFC, rather than relying on schema validation after
# normalization.
MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS: Final = 4_096
# Retain the earlier internal spellings for callers outside this module while
# making the contract-specific names authoritative.
MAX_NATIVE_ENTITIES: Final = MAX_NATIVE_GEOMETRY_ENTITIES
MAX_NATIVE_SEGMENTS: Final = MAX_NATIVE_GEOMETRY_SEGMENTS
MAX_TRANSLATION = 1_000_000.0
PRIVATE_RECORD_CARDINALITY: Final = "explicit_private"
AUTOCAD_ADAPTER_ID: Final = "liang-pingfa-autocad-adapter"
_OPERATION_PROFILE_CAPABILITIES: Final[dict[str, str]] = {
    "translate_dbtext/v1": "translate_dbtext/v1",
    "delete_auxiliary_overlay_text/v1": "delete_auxiliary_overlay_text/v1",
    "create_review_marker/v1": "create_review_marker/v1",
}
MAX_NATIVE_LEGACY_OPERATION_COUNT: Final = 2_000
_SCHEMA_CHECKPOINT_INTERVAL: Final = 64
_GEOMETRY_UTF8_SCAN_CHARACTERS: Final = 16 * 1024
NATIVE_OPAQUE_EMBEDDED_JSON_RULES: Final[
    Mapping[NativeSchemaKind, OpaqueJsonStringRules]
] = {
    # These are complete path tuples from the schema root, not name-based
    # exemptions. A nested attacker-controlled ``geometry_json`` stays NFC.
    "response": {
        ("result", "geometry_json"): MAX_NATIVE_GEOMETRY_JSON_BYTES,
        ("result", "inventory_json"): MAX_NATIVE_INVENTORY_JSON_BYTES,
    },
    "manifest": {
        ("preconditions_geometry_json",): MAX_NATIVE_GEOMETRY_JSON_BYTES,
    },
    "console_export": {
        ("geometry_json",): MAX_NATIVE_GEOMETRY_JSON_BYTES,
    },
}
_PRIVATE_PERSISTED_KINDS: Final[frozenset[NativeSchemaKind]] = frozenset(
    {
        "config",
        "session",
        "geometry",
        "audit",
        "intent",
        "plan",
        "manifest",
        "console_result",
        "console_export",
        "verification",
    }
)


def canonical_console_result_bytes(result: Mapping[str, Any]) -> bytes:
    """Return the exact bytes consumed by the bounded Console result reader.

    The outer result has no opaque carrier path, so ordinary canonical JSON is
    both the integrity representation and the byte budget representation.
    Keeping this helper here lets builders, validators, and tests use the
    same calculation instead of inferring a safe operation count.
    """

    return canonical_json_bytes(dict(result))


def require_console_result_transport_budget(result: Mapping[str, Any]) -> None:
    """Reject a result whose canonical bytes consume the reserved headroom.

    The external reader remains capped at ``MAX_NATIVE_CONSOLE_RESULT_BYTES``.
    Valid success and failure envelopes must stay below the smaller canonical
    budget so logging, a terminal LF, and transport framing cannot make a
    schema-valid result unreadable.
    """

    try:
        length = len(canonical_console_result_bytes(result))
    except (CanonicalJsonError, TypeError, ValueError) as error:
        raise ValueError("native console result cannot be canonicalized") from error
    if length > MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES:
        raise ValueError(
            "native console result exceeds the fixed transport budget "
            f"({length}>{MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES}; "
            f"hard cap {MAX_NATIVE_CONSOLE_RESULT_BYTES})"
        )


def opaque_embedded_json_rules(kind: NativeSchemaKind) -> OpaqueJsonStringRules:
    """Return the one explicit opaque-carrier allowlist for a native context."""

    return NATIVE_OPAQUE_EMBEDDED_JSON_RULES.get(kind, {})


def _maximum_string_codepoints_for(kind: NativeSchemaKind) -> int:
    """Return the schema-derived pre-NFC scalar bound for this context."""

    return (
        MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS
        if kind == "geometry"
        else MAX_JSON_STRING_CODEPOINTS
    )


def _require_embedded_json_utf8_bytes(
    value: Any,
    *,
    maximum_bytes: int,
    label: str,
    error: ErrorCode,
    deadline_check: _DeadlineCheck | None = None,
) -> str:
    """Bound one raw JSON carrier without normalizing or fully re-encoding it."""

    if not isinstance(value, str):
        raise PipelineError(error, f"native {label} JSON is invalid")
    byte_count = 0
    try:
        for offset in range(0, len(value), _GEOMETRY_UTF8_SCAN_CHARACTERS):
            _check_deadline(deadline_check, f"{label} UTF-8 byte limit")
            byte_count += len(
                value[offset : offset + _GEOMETRY_UTF8_SCAN_CHARACTERS].encode(
                    "utf-8",
                    errors="strict",
                )
            )
            if byte_count > maximum_bytes:
                raise PipelineError(
                    error,
                    f"native {label} JSON exceeds the fixed UTF-8 byte limit",
                )
    except UnicodeEncodeError as exc:
        raise PipelineError(error, f"native {label} JSON is invalid") from exc
    _check_deadline(deadline_check, f"{label} UTF-8 byte limit")
    return value


def require_geometry_json_utf8_bytes(
    value: Any,
    *,
    error: ErrorCode,
    deadline_check: _DeadlineCheck | None = None,
) -> str:
    """Return raw geometry JSON only when it fits the fixed UTF-8 byte cap.

    Encoding a hostile ``str`` in one call would allocate a second object as
    large as the input before a caller could reject it.  Scan bounded slices
    instead and stop at the first byte beyond the fixed v1 limit.  Callers
    receive only stable, redacted errors; raw geometry is never interpolated.
    """

    return _require_embedded_json_utf8_bytes(
        value,
        maximum_bytes=MAX_NATIVE_GEOMETRY_JSON_BYTES,
        label="geometry",
        error=error,
        deadline_check=deadline_check,
    )


def require_inventory_json_utf8_bytes(
    value: Any,
    *,
    error: ErrorCode,
    deadline_check: _DeadlineCheck | None = None,
) -> str:
    """Return raw inventory JSON only below its fixed UTF-8 carrier cap."""

    return _require_embedded_json_utf8_bytes(
        value,
        maximum_bytes=MAX_NATIVE_INVENTORY_JSON_BYTES,
        label="inventory",
        error=error,
        deadline_check=deadline_check,
    )


def require_geometry_json_payload_bytes(
    payload: bytes,
    *,
    error: ErrorCode,
) -> bytes:
    """Reject an oversized persisted raw geometry payload before UTF-8 decode.

    A canonical private artifact may have exactly one terminal LF.  It is not
    part of the geometry JSON byte budget; every other byte is counted before
    decoding or normalizing the payload.
    """

    if not isinstance(payload, bytes):
        raise PipelineError(error, "native geometry JSON is invalid")
    raw_length = len(payload) - int(payload.endswith(b"\n"))
    if raw_length > MAX_NATIVE_GEOMETRY_JSON_BYTES:
        raise PipelineError(
            error,
            "native geometry JSON exceeds the fixed UTF-8 byte limit",
        )
    return payload


def canonical_geometry_json_bytes(
    geometry: Mapping[str, Any],
    *,
    error: ErrorCode,
    deadline_check: _DeadlineCheck | None = None,
) -> bytes:
    """Serialize direct geometry once with a bounded UTF-8 output stream."""

    try:
        normalized = normalize_json_value(
            geometry,
            deadline_check=deadline_check,
            maximum_string_codepoints=MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS,
        )
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        chunks: list[bytes] = []
        byte_count = 0
        sampler = DeadlineCheckpointSampler(
            deadline_check,
            interval=_GEOMETRY_UTF8_SCAN_CHARACTERS,
            checkpoint=_check_deadline,
        )
        for text_chunk in encoder.iterencode(normalized):
            for offset in range(
                0,
                len(text_chunk),
                _GEOMETRY_UTF8_SCAN_CHARACTERS,
            ):
                bounded_text = text_chunk[
                    offset : offset + _GEOMETRY_UTF8_SCAN_CHARACTERS
                ]
                chunk = bounded_text.encode("utf-8", errors="strict")
                byte_count += len(chunk)
                if byte_count > MAX_NATIVE_GEOMETRY_JSON_BYTES:
                    raise PipelineError(
                        error,
                        "native geometry JSON exceeds the fixed UTF-8 byte limit",
                    )
                chunks.append(chunk)
                sampler.advance(
                    "geometry canonical serialization",
                    len(bounded_text),
                )
        return b"".join(chunks)
    except (
        CanonicalJsonError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
        raise PipelineError(error, "native geometry JSON is invalid") from exc


def geometry_text_matches_canonical_bytes(
    text: str,
    parsed: Mapping[str, Any],
    *,
    error: ErrorCode,
    deadline_check: _DeadlineCheck | None = None,
) -> bool:
    """Compare canonical bytes without allocating a second full raw encoding."""

    canonical = canonical_geometry_json_bytes(
        parsed,
        error=error,
        deadline_check=deadline_check,
    )
    offset = 0
    try:
        for start in range(0, len(text), _GEOMETRY_UTF8_SCAN_CHARACTERS):
            _check_deadline(deadline_check, "geometry canonical byte comparison")
            chunk = text[start : start + _GEOMETRY_UTF8_SCAN_CHARACTERS].encode(
                "utf-8",
                errors="strict",
            )
            if canonical[offset : offset + len(chunk)] != chunk:
                return False
            offset += len(chunk)
    except UnicodeEncodeError:
        return False
    _check_deadline(deadline_check, "geometry canonical byte comparison")
    return offset == len(canonical)


def canonical_native_contract_bytes(
    kind: NativeSchemaKind,
    artifact: Mapping[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> bytes:
    """Serialize one native contract while preserving its exact carriers."""

    return canonical_json_bytes(
        artifact,
        deadline_check=deadline_check,
        opaque_string_rules=opaque_embedded_json_rules(kind),
        maximum_string_codepoints=_maximum_string_codepoints_for(kind),
    )


def attach_native_integrity(
    kind: NativeSchemaKind,
    artifact: Mapping[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> dict[str, Any]:
    """Hash one native contract with exact opaque-carrier bytes."""

    return dict(
        attach_integrity(
            artifact,
            deadline_check=deadline_check,
            opaque_string_rules=opaque_embedded_json_rules(kind),
            maximum_string_codepoints=_maximum_string_codepoints_for(kind),
        )
    )


def _error_for(kind: NativeSchemaKind) -> ErrorCode:
    if kind == "config":
        return ErrorCode.NATIVE_CONFIG_INVALID
    if kind in {"request", "response", "inventory"}:
        return ErrorCode.NATIVE_PROTOCOL_INVALID
    return _ARTIFACT_ERRORS[kind]


def schema_for_native(
    kind: NativeSchemaKind,
    *,
    schema_version: str | None = None,
) -> dict[str, Any]:
    """Load one packaged Draft 2020-12 schema without filesystem discovery."""

    try:
        filename = _SCHEMA_FILES[kind]
        if schema_version is not None:
            filename = _SUPPORTED_SCHEMA_FILES[kind][schema_version]
        text = (
            resources.files("liang_pingfa_review.schemas")
            .joinpath(filename)
            .read_text(encoding="utf-8")
        )
        schema = strict_json_loads(text)
        Draft202012Validator.check_schema(schema)
    except (
        CanonicalJsonError,
        KeyError,
        OSError,
        ModuleNotFoundError,
        RecursionError,
        ValueError,
        SchemaError,
    ) as error:
        raise PipelineError(
            ErrorCode.INTERNAL_ERROR, "native packaged schema is unavailable"
        ) from error
    if not isinstance(schema, dict):
        raise PipelineError(ErrorCode.INTERNAL_ERROR, "native schema is not an object")
    return schema


def native_contract_schema_version(
    kind: NativeSchemaKind,
    artifact: Mapping[str, Any],
) -> str:
    """Return the version token that selects one exact packaged schema.

    Native wire envelopes deliberately retain their frozen v1
    ``protocol_version`` field.  Every persisted artifact uses
    ``schema_version``.  Keeping this selection in one place prevents a
    caller from validating a v1 object with an active v2 schema merely
    because both have similar field names.
    """

    field = _VERSION_FIELD_BY_KIND.get(kind, "schema_version")
    version = artifact.get(field)
    if not isinstance(version, str):
        raise PipelineError(_error_for(kind), "native schema namespace is missing")
    return version


def is_active_native_contract(
    kind: NativeSchemaKind,
    artifact: Mapping[str, Any],
) -> bool:
    """Return whether an already decoded artifact is in the active namespace."""

    return native_contract_schema_version(kind, artifact) == _ACTIVE_SCHEMA_VERSIONS[
        kind
    ]


def require_active_native_contract(
    kind: NativeSchemaKind,
    artifact: Mapping[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate an active v2 artifact or reject a frozen v1 read artifact.

    This is deliberately separate from :func:`validate_native_contract`.
    Legacy callers need to inspect and report v1 evidence, while every
    execution boundary must fail with one stable, non-reinterpreting code.
    """

    checked = validate_native_contract(
        kind,
        artifact,
        deadline_check=deadline_check,
        now=now,
    )
    if not is_active_native_contract(kind, checked):
        raise PipelineError(
            ErrorCode.NATIVE_LEGACY_ARTIFACT_READ_ONLY,
            "published native v1 artifacts require a fresh v2 audit/session",
        )
    return checked


def _require_mapping(value: Any, kind: NativeSchemaKind) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PipelineError(_error_for(kind), "native contract root is not an object")
    return dict(value)


def _require_nfc(
    value: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
    opaque_string_rules: OpaqueJsonStringRules | None = None,
    maximum_string_codepoints: int = MAX_JSON_STRING_CODEPOINTS,
) -> None:
    """Use the canonical module's bounded NFC/key/finite-number traversal."""

    try:
        validate_json_canonical_form(
            value,
            deadline_check=deadline_check,
            opaque_string_rules=opaque_string_rules,
            maximum_string_codepoints=maximum_string_codepoints,
        )
    except CanonicalJsonError as error:
        raise ValueError("native contract is non-canonical") from error


def _check_deadline(
    deadline_check: _DeadlineCheck | None,
    stage: str,
) -> None:
    """Run an optional caller-owned absolute-deadline checkpoint."""

    if deadline_check is not None:
        deadline_check(stage)


def _deadline_aware_validator(
    kind: NativeSchemaKind,
    schema: Mapping[str, Any],
    *,
    deadline_check: _DeadlineCheck | None,
) -> Draft202012Validator:
    """Build a strict Draft 2020-12 validator with bounded traversal probes.

    Native geometry uses large arrays, so checking only before and after
    ``iter_errors`` would allow a syntactically valid response to overrun the
    original RPC deadline.  Every standard keyword remains delegated to the
    pinned Draft 2020-12 implementation; only array iteration and
    ``uniqueItems`` are reimplemented to retain the same semantics while
    exposing bounded checkpoints.
    """

    if deadline_check is None:
        return Draft202012Validator(schema)

    sampler = DeadlineCheckpointSampler(
        deadline_check,
        interval=_SCHEMA_CHECKPOINT_INTERVAL,
        checkpoint=_check_deadline,
    )

    def checkpoint(stage: str, *, force: bool = False) -> None:
        sampler.visit(f"{kind} JSON Schema {stage}", force=force)

    def deadline_items(
        validator: Draft202012Validator,
        items: Any,
        instance: Any,
        schema_value: Mapping[str, Any],
    ) -> Any:
        item_ref = (
            items.get("$ref")
            if isinstance(items, Mapping)
            else None
        )
        item_stage = (
            "entities items"
            if item_ref == "#/$defs/entity"
            else "segments items"
            if item_ref == "#/$defs/segment"
            else "items"
        )
        if not validator.is_type(instance, "array"):
            checkpoint(item_stage)
            return
        prefix = len(schema_value.get("prefixItems", []))
        total = len(instance)
        # Small nested arrays are sampled by the shared schema counter, while
        # long arrays force an initial and each subsequent bounded checkpoint.
        # This avoids a callback for every one-item field in every entity.
        # Entity arrays are the dominant nested geometry cost and their first
        # deadline probe must not depend on how many unrelated schema fields
        # precede them. In particular, adding v2 physical-container records
        # must not shift a small entity array past a sampler boundary and let
        # it bypass the request's first geometry checkpoint.
        checkpoint(
            item_stage,
            force=(
                total >= _SCHEMA_CHECKPOINT_INTERVAL
                or (item_stage == "entities items" and total > 0)
            ),
        )
        extra = total - prefix
        if extra <= 0:
            return
        if items is False:
            # The generic error text is deliberately not surfaced by native
            # callers; avoid building a full attacker-controlled slice merely
            # to interpolate it into the message.
            for index in range(prefix, total):
                checkpoint(
                    item_stage,
                    force=(index + 1) % _SCHEMA_CHECKPOINT_INTERVAL == 0,
                )
            yield ValidationError(
                f"Expected at most {prefix} item(s), found {extra} extra"
            )
            return
        for index in range(prefix, total):
            checkpoint(
                item_stage,
                force=(index + 1) % _SCHEMA_CHECKPOINT_INTERVAL == 0,
            )
            yield from validator.descend(
                instance=instance[index],
                schema=items,
                path=index,
            )

    def deadline_unique_items(
        validator: Draft202012Validator,
        unique_items: Any,
        instance: Any,
        _schema_value: Mapping[str, Any],
    ) -> Any:
        checkpoint("uniqueItems", force=True)
        if not unique_items or not validator.is_type(instance, "array"):
            return
        try:
            sortable: list[Any] = []
            for index, item in enumerate(instance):
                checkpoint(
                    "uniqueItems",
                    force=index % _SCHEMA_CHECKPOINT_INTERVAL == 0,
                )
                sortable.append(unbool(item))
            sortable.sort()
            previous: Any | None = None
            have_previous = False
            for index, item in enumerate(sortable):
                checkpoint(
                    "uniqueItems",
                    force=index % _SCHEMA_CHECKPOINT_INTERVAL == 0,
                )
                if have_previous and equal(previous, item):
                    yield ValidationError("array has non-unique elements")
                    return
                previous = item
                have_previous = True
        except (NotImplementedError, TypeError):
            seen: list[Any] = []
            for item_index, item in enumerate(instance):
                checkpoint(
                    "uniqueItems",
                    force=item_index % _SCHEMA_CHECKPOINT_INTERVAL == 0,
                )
                normalized = unbool(item)
                for prior_index, prior in enumerate(seen):
                    checkpoint(
                        "uniqueItems",
                        force=prior_index % _SCHEMA_CHECKPOINT_INTERVAL == 0,
                    )
                    if equal(prior, normalized):
                        yield ValidationError("array has non-unique elements")
                        return
                seen.append(normalized)

    wrapped: dict[str, Any] = {}
    for keyword, keyword_validator in Draft202012Validator.VALIDATORS.items():
        if keyword in {"items", "uniqueItems"}:
            continue

        def checked_keyword(
            validator: Draft202012Validator,
            value: Any,
            instance: Any,
            schema_value: Mapping[str, Any],
            *,
            _keyword: str = keyword,
            _validator: Any = keyword_validator,
        ) -> Any:
            checkpoint(_keyword)
            yield from (_validator(validator, value, instance, schema_value) or ())

        wrapped[keyword] = checked_keyword
    wrapped["items"] = deadline_items
    wrapped["uniqueItems"] = deadline_unique_items
    deadline_validator = validators.extend(
        Draft202012Validator,
        validators=wrapped,
    )
    checkpoint("validation", force=True)
    return deadline_validator(schema)


def _validate_schema(
    kind: NativeSchemaKind,
    artifact: Mapping[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    _check_deadline(deadline_check, f"{kind} JSON Schema validation")
    try:
        # Native RPC values can be supplied in-process as well as decoded from
        # a frame, so cap their structure before jsonschema recurses.
        validate_json_nesting(artifact, deadline_check=deadline_check)
        validator = _deadline_aware_validator(
            kind,
            schema_for_native(
                kind,
                schema_version=native_contract_schema_version(kind, artifact),
            ),
            deadline_check=deadline_check,
        )
        # Native callers expose one redacted validation outcome, not a sorted
        # error report.  Stopping on the first error avoids allocating every
        # error from a hostile large invalid response while valid data still
        # traverses every applicable Draft 2020-12 keyword.
        error = next(validator.iter_errors(artifact), None)
    except (CanonicalJsonError, RecursionError) as error:
        raise PipelineError(
            _error_for(kind),
            "native JSON Schema validation failed",
        ) from error
    _check_deadline(deadline_check, f"{kind} JSON Schema validation")
    if error is not None:
        raise PipelineError(_error_for(kind), "native JSON Schema validation failed")


def _integrity_required(kind: NativeSchemaKind) -> bool:
    return kind not in {"config", "request", "response", "inventory"}


def _validate_common(
    kind: NativeSchemaKind,
    artifact: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> dict[str, Any]:
    _check_deadline(deadline_check, f"{kind} response normalization")
    normalized = _require_mapping(artifact, kind)
    opaque_rules = opaque_embedded_json_rules(kind)
    maximum_string_codepoints = _maximum_string_codepoints_for(kind)
    try:
        _check_deadline(deadline_check, f"{kind} canonical validation")
        _require_nfc(
            normalized,
            deadline_check=deadline_check,
            opaque_string_rules=opaque_rules,
            maximum_string_codepoints=maximum_string_codepoints,
        )
        if _integrity_required(kind) and not verify_integrity(
            normalized,
            deadline_check=deadline_check,
            opaque_string_rules=opaque_rules,
            maximum_string_codepoints=maximum_string_codepoints,
        ):
            raise PipelineError(_error_for(kind), "native integrity mismatch")
    except (CanonicalJsonError, RecursionError) as error:
        raise PipelineError(_error_for(kind), "native contract is non-canonical") from error
    except ValueError as error:
        raise PipelineError(_error_for(kind), "native contract is non-canonical") from error
    _check_deadline(deadline_check, f"{kind} canonical validation")
    schema_version = native_contract_schema_version(kind, normalized)
    if schema_version not in _SUPPORTED_SCHEMA_FILES[kind]:
        raise PipelineError(_error_for(kind), "native schema namespace mismatch")
    _validate_schema(kind, normalized, deadline_check=deadline_check)
    return normalized


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _finite_bits(value: str) -> float:
    try:
        number = struct.unpack(">d", bytes.fromhex(value))[0]
    except (ValueError, struct.error) as error:
        raise ValueError("invalid binary64 bits") from error
    if not math.isfinite(number):
        raise ValueError("non-finite binary64")
    # -0 has an observable bit pattern but no useful canonical geometry value.
    if number == 0.0 and value != "0000000000000000":
        raise ValueError("non-canonical zero binary64")
    return number


def bits_from_float(value: float) -> str:
    """Encode one finite, canonical binary64 scalar for private geometry."""

    if not math.isfinite(value):
        raise ValueError("non-finite binary64")
    if value == 0.0:
        value = 0.0
    return struct.pack(">d", value).hex()


def bits_vector(values: list[str] | tuple[str, str, str]) -> tuple[float, float, float]:
    if len(values) != 3:
        raise ValueError("geometry vector has wrong length")
    return tuple(_finite_bits(item) for item in values)  # type: ignore[return-value]


def translate_binary64_bits(original_bits: str, delta_bits: str) -> str:
    """Translate one canonical finite binary64 scalar without silent rounding.

    A zero delta is a bit-preserving identity.  Every nonzero delta must
    produce a distinct finite binary64 result; callers must reject the whole
    operation when this cannot be represented exactly by the host scalar
    format.  This is deliberately the sole arithmetic primitive for native
    DBTEXT translations.
    """

    original = _finite_bits(original_bits)
    delta = _finite_bits(delta_bits)
    if delta == 0.0:
        return original_bits
    translated = original + delta
    if not math.isfinite(translated):
        raise ValueError("translated binary64 is non-finite")
    translated_bits = bits_from_float(translated)
    if translated_bits == original_bits:
        raise ValueError("nonzero translation is not representable")
    return translated_bits


def translated_geometry_bits(
    entity: Mapping[str, Any],
    delta: list[str] | tuple[str, str, str],
) -> dict[str, Any]:
    """Return the exact required geometry transition for one native entity.

    All position, serialized bounds, and serialized segment coordinates use
    :func:`translate_binary64_bits`, so a nonzero axis cannot be certified
    when any operation-critical scalar would be a binary64 no-op.
    """

    if len(delta) != 3:
        raise ValueError("translation vector has wrong length")

    def translate(vector: list[str] | tuple[str, str, str]) -> list[str]:
        if len(vector) != 3:
            raise ValueError("geometry vector has wrong length")
        return [
            translate_binary64_bits(original, difference)
            for original, difference in zip(vector, delta, strict=True)
        ]

    position = translate(cast(list[str], entity["position"]))
    if position == entity["position"]:
        raise ValueError("translation did not change position")
    bounds = cast(Mapping[str, list[str]], entity["bounds"])
    segments = cast(list[Mapping[str, list[str]]], entity["segments"])
    return {
        "position": position,
        "bounds": {
            "minimum": translate(bounds["minimum"]),
            "maximum": translate(bounds["maximum"]),
        },
        "segments": [
            {
                "start": translate(segment["start"]),
                "end": translate(segment["end"]),
            }
            for segment in segments
        ],
    }


def _geometry_projection(entity: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(entity)
    result.pop("geometry_fingerprint", None)
    result.pop("opaque_state_digest", None)
    return result


def _container_key_from_record(
    record: Mapping[str, Any],
) -> tuple[str, str, str, tuple[str, ...]]:
    space = cast(Mapping[str, Any], record["space"])
    return (
        cast(str, space["kind"]),
        cast(str, space["layout_handle"] or ""),
        cast(str, space["block_handle"] or ""),
        tuple(cast(list[str], record["block_path"])),
    )


def _container_key(entity: Mapping[str, Any]) -> tuple[str, str, str, tuple[str, ...]]:
    return _container_key_from_record(entity)


def native_container_sequences(
    entities: list[Mapping[str, Any]],
    containers: list[Mapping[str, Any]] | None = None,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> list[dict[str, Any]]:
    """Project ordered records with explicit v2 physical container extents.

    Legacy v1 callers intentionally retain their historical active-record
    grouping when ``containers`` is omitted. Active v2 callers must supply
    every physical container rather than deriving its extent from active
    sequence indices.
    """

    grouped: dict[tuple[str, str, str, tuple[str, ...]], list[Mapping[str, Any]]] = {}
    for index, entity in enumerate(entities):
        if index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
            _check_deadline(deadline_check, "geometry container projection")
        grouped.setdefault(_container_key(entity), []).append(entity)
    if containers is None:
        ordered_containers: list[tuple[
            tuple[str, str, str, tuple[str, ...]],
            Mapping[str, Any] | None,
        ]] = [
            (container, None) for container in sorted(grouped)
        ]
    else:
        ordered_containers = [
            (_container_key_from_record(container), container)
            for container in sorted(
                containers,
                key=_container_key_from_record,
            )
        ]

    projected: list[dict[str, Any]] = []
    for container_index, (container, container_record) in enumerate(
        ordered_containers
    ):
        if container_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
            _check_deadline(deadline_check, "geometry container projection")
        records = grouped.get(container, [])
        records_projection: list[dict[str, Any]] = []
        for record_index, entity in enumerate(records):
            if record_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
                _check_deadline(deadline_check, "geometry container projection")
            records_projection.append(
                {
                    "geometry_fingerprint": entity["geometry_fingerprint"],
                    "handle": entity["handle"],
                    "opaque_state_digest": entity["opaque_state_digest"],
                    "sequence_index": entity["sequence_index"],
                }
            )
        projection: dict[str, Any] = {
            "container": container,
            "entities": records_projection,
        }
        if container_record is not None:
            projection["owner_handle"] = container_record["owner_handle"]
            projection["physical_slot_count"] = container_record[
                "physical_slot_count"
            ]
        projected.append(projection)
    return projected


def geometry_document_binding(export: Mapping[str, Any]) -> dict[str, Any]:
    """Return the redaction-safe document digest projection from a raw export."""

    document = cast(Mapping[str, Any], export["document"])
    return {
        key: document[key]
        for key in (
            "database_instance_fingerprint",
            "revision_fingerprint",
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
    }


def prewrite_semantic_projection(export: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one source-to-private-copy portable prewrite contract.

    Database instance/revision values and source/session bindings identify the
    host context that produced an export, but a byte-for-byte private DWG copy
    is allowed to receive different values for each.  This projection carries
    only semantic/protected state that must survive that retarget.  Marker
    policy is deliberately not folded into this digest: it is bound and
    checked separately by the manifest stable-host binding, while the
    policy-independent table digest still rejects marker resource drift.
    """

    document = cast(Mapping[str, Any], export["document"])
    return {
        "schema_version": "liang-pingfa/portable-prewrite-projection/v2",
        "ordered_entity_digest": document["ordered_entity_digest"],
        "container_order_digest": document["container_order_digest"],
        "geometry_digest": document["complete_geometry_digest"],
        "protected_semantic_digest": canonical_sha256(
            {
                "owners": list(cast(list[str], export["owners"])),
                "opaque_state_digests": [
                    entity["opaque_state_digest"]
                    for entity in cast(list[Mapping[str, Any]], export["entities"])
                ],
            }
        ),
        "table_state_digest": document["table_state_digest"],
        "layout_state_digest": document["layout_state_digest"],
        "block_state_digest": document["block_state_digest"],
    }


def prewrite_semantic_projection_digest(export: Mapping[str, Any]) -> str:
    """Return the canonical digest of :func:`prewrite_semantic_projection`."""

    return canonical_sha256(prewrite_semantic_projection(export))


def geometry_adapter_binding(export: Mapping[str, Any]) -> dict[str, Any]:
    """Build the stable adapter projection carried by native audit/plan files."""

    binding = cast(Mapping[str, Any], export["binding"])
    adapter = cast(Mapping[str, Any], binding["adapter"])
    plugin = cast(Mapping[str, Any], binding["plugin"])
    return {
        "adapter_id": adapter["id"],
        "adapter_profile": adapter["profile"],
        "adapter_version": adapter["version"],
        "plugin_id": plugin["id"],
        "plugin_version": plugin["version"],
        "plugin_fingerprint": plugin["fingerprint"],
        "protocol_major": binding["protocol_major"],
        "protocol_minor": binding["protocol_minor"],
        "capabilities_digest": canonical_sha256(binding["capabilities"]),
    }


def prewrite_revision_binding(
    export: Mapping[str, Any],
    *,
    native_host_binding_value: str,
    stable_host_binding_digest: str,
    audited_semantic_state_digest: str,
) -> dict[str, Any]:
    """Bind private source bytes and one portable prewrite projection.

    The bridge database/revision values remain explicit evidence of the
    embedded bridge export only. They are never compared to a Core Console
    private-copy database, whose documented host identity may differ after
    opening the copied DWG.
    """

    source = cast(Mapping[str, Any], export["source"])
    document = cast(Mapping[str, Any], export["document"])
    portable = prewrite_semantic_projection(export)
    return {
        "source_binding": dict(source),
        "document_path_fingerprint": source["path_fingerprint"],
        "document_file_identity_fingerprint": source["file_identity_fingerprint"],
        "document_content_sha256": source["sha256"],
        "document_byte_size": source["byte_size"],
        "bridge_document_identity": {
            "database_instance_fingerprint": document["database_instance_fingerprint"],
            "revision_fingerprint": document["revision_fingerprint"],
        },
        "portable_prewrite_projection": portable,
        "portable_prewrite_projection_digest": canonical_sha256(portable),
        "adapter_binding": geometry_adapter_binding(export),
        "native_host_binding": native_host_binding_value,
        "stable_host_binding_digest": stable_host_binding_digest,
        "audited_semantic_state_digest": audited_semantic_state_digest,
    }


def native_session_binding_digest(session: Mapping[str, Any]) -> str:
    """Return a redacted binding for one ephemeral read-only session.

    This intentionally includes the unique session and process-instance
    identity, unlike :func:`native_host_binding`.  It is persisted only as a
    digest so a manifest can prove that a fresh session replaced, rather than
    silently reused, the audit session.
    """

    return canonical_sha256(
        {
            "adapter": session["adapter"],
            "capabilities": session["capabilities"],
            "current_document": session["current_document"],
            "host": session["host"],
            "plugin": session["plugin"],
            "process": session["process"],
            "session_id": session["session_id"],
        }
    )


def _stable_geometry_host_binding_digest(
    *,
    protocol_version: str,
    protocol_major: int,
    protocol_minor: int,
    host: Mapping[str, Any],
    process: Mapping[str, Any],
    adapter: Mapping[str, Any],
    plugin: Mapping[str, Any],
    capabilities: list[str],
) -> str:
    """Digest the export's stable host compatibility identity.

    Session IDs, PIDs, Windows logon sessions, and process-instance tokens are
    intentionally omitted here: they belong in the exact session digest.  The
    executable fingerprint remains because a renewed session must not silently
    switch host binaries beneath the same adapter/plugin profile.
    """

    return canonical_sha256(
        {
            "protocol_version": protocol_version,
            "protocol_major": protocol_major,
            "protocol_minor": protocol_minor,
            "host": dict(host),
            "host_executable_fingerprint": process["executable_fingerprint"],
            "adapter": dict(adapter),
            "plugin": dict(plugin),
            # Capability membership is stable; list ordering is an
            # ephemeral adapter serialization detail. The C# typed context
            # canonicalizes this set before calculating the same digest.
            "capabilities": sorted(capabilities),
        }
    )


def native_geometry_host_binding_digest(session: Mapping[str, Any]) -> str:
    """Return the stable export-host digest expected for one session."""

    return _stable_geometry_host_binding_digest(
        protocol_version=PROTOCOL_VERSION,
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        host=cast(Mapping[str, Any], session["host"]),
        process=cast(Mapping[str, Any], session["process"]),
        adapter=cast(Mapping[str, Any], session["adapter"]),
        plugin=cast(Mapping[str, Any], session["plugin"]),
        capabilities=cast(list[str], session["capabilities"]),
    )


def native_execution_stable_host_binding_digest(
    geometry: Mapping[str, Any],
    marker_policy: Mapping[str, Any],
) -> str:
    """Return the save/readback-stable host projection shared with C#.

    It retains protocol, complete host identity including executable
    fingerprint, adapter/profile/version, plugin identity, capability set,
    and every output-affecting marker-policy field. Session/PID/pipe/nonces,
    database instance, and revision intentionally do not participate.
    """

    binding = cast(Mapping[str, Any], geometry["binding"])
    process = cast(Mapping[str, Any], binding["process"])
    return canonical_sha256(
        {
            "protocol_version": binding["protocol_version"],
            "protocol_major": binding["protocol_major"],
            "protocol_minor": binding["protocol_minor"],
            "host": dict(cast(Mapping[str, Any], binding["host"])),
            "host_executable_fingerprint": process["executable_fingerprint"],
            "adapter": dict(cast(Mapping[str, Any], binding["adapter"])),
            "plugin": dict(cast(Mapping[str, Any], binding["plugin"])),
            "capabilities": sorted(cast(list[str], binding["capabilities"])),
            "marker_policy_binding": dict(marker_policy),
        }
    )


def geometry_document_binding_digest(export: Mapping[str, Any]) -> str:
    """Digest every source and document field carried by a geometry export."""

    binding: dict[str, Any] = {
        "source": dict(cast(Mapping[str, Any], export["source"])),
        "document": dict(cast(Mapping[str, Any], export["document"])),
    }
    # v1's persisted document-binding digest is frozen. Active v2 binds the
    # full erased-inclusive container extent in addition to its document
    # digest fields, so an otherwise self-consistent count drift cannot pass
    # audit/plan/manifest preconditions.
    if export.get("schema_version") == _ACTIVE_SCHEMA_VERSIONS["geometry"]:
        binding["containers"] = [
            dict(container)
            for container in cast(
                list[Mapping[str, Any]],
                export["containers"],
            )
        ]
    return canonical_sha256(binding)


def _export_process_binding(session: Mapping[str, Any]) -> dict[str, Any]:
    """Project the full ephemeral process identity an export must repeat."""

    process = cast(Mapping[str, Any], session["process"])
    return {
        "pid": session["pid"],
        "windows_session_id": session["windows_session_id"],
        "instance_fingerprint": process["instance_fingerprint"],
        "creation_time_100ns": process["creation_time_100ns"],
        "executable_fingerprint": process["executable_fingerprint"],
    }


def require_geometry_export_matches_session(
    geometry: Mapping[str, Any],
    session: Mapping[str, Any],
    expected_source: Mapping[str, Any] | None = None,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate one geometry export against the exact issuing session.

    Source equality alone is deliberately insufficient: a same-byte drawing
    may be exported by another session, process instance, plugin, capability
    set, database, or revision.  This is the single binding gate used by the
    RPC client and direct audit/manifest consumers.
    """

    checked_geometry = require_active_native_contract(
        "geometry",
        geometry,
        deadline_check=deadline_check,
    )
    checked_session = require_active_native_contract(
        "session",
        session,
        deadline_check=deadline_check,
    )
    binding = cast(Mapping[str, Any], checked_geometry["binding"])
    session_binding = native_session_binding_digest(checked_session)
    source = cast(Mapping[str, Any], checked_geometry["source"])
    document = cast(Mapping[str, Any], checked_geometry["document"])
    current_document = cast(Mapping[str, Any], checked_session["current_document"])

    _check_deadline(deadline_check, "geometry/session binding validation")
    if (
        checked_geometry["schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["geometry"]
        or checked_session["schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["session"]
        or binding["protocol_version"] != PROTOCOL_VERSION
        or binding["protocol_major"] != PROTOCOL_MAJOR
        or binding["protocol_minor"] != PROTOCOL_MINOR
        or binding["session_id"] != checked_session["session_id"]
        or binding["session_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["session"]
        or binding["host"] != checked_session["host"]
        or binding["process"] != _export_process_binding(checked_session)
        or binding["adapter"] != checked_session["adapter"]
        or binding["plugin"] != checked_session["plugin"]
        or binding["capabilities"] != checked_session["capabilities"]
        or binding["stable_host_binding_digest"]
        != native_geometry_host_binding_digest(checked_session)
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "geometry export session/host capability binding differs",
        )
    if (
        binding["document_binding_digest"]
        != geometry_document_binding_digest(checked_geometry)
        or binding["session_binding_digest"] != session_binding
        or current_document["saved"] is not True
        or any(
            source[key] != current_document[key]
            for key in (
                "sha256",
                "byte_size",
                "dwg_header_signature",
                "path_fingerprint",
                "file_identity_fingerprint",
            )
        )
        or document["database_instance_fingerprint"]
        != current_document["database_instance_fingerprint"]
        or document["revision_fingerprint"]
        != current_document["revision_fingerprint"]
    ):
        raise PipelineError(
            ErrorCode.NATIVE_DOCUMENT_CHANGED,
            "geometry export document binding differs",
        )
    if expected_source is not None and dict(source) != dict(expected_source):
        raise PipelineError(
            ErrorCode.NATIVE_DOCUMENT_CHANGED,
            "geometry export source differs from expected source",
        )
    _check_deadline(deadline_check, "geometry/session binding validation")
    return checked_geometry, checked_session


def native_marker_policy_binding(config: Mapping[str, Any]) -> dict[str, Any]:
    """Project every output-affecting marker policy value into a stable binding.

    This intentionally includes configured spelling as well as audited
    fingerprints: the external writer emits both layer/style tokens and their
    derived marker geometry.  Any future policy revision must therefore
    change this explicitly versioned, exact object rather than silently
    changing an already-audited plan.
    """

    marker = cast(Mapping[str, Any], config["marker_policy"])
    profile = cast(str, marker["profile"])
    profiles = cast(Mapping[str, bool], config["operation_profiles"])
    defaults = cast(Mapping[str, Any], marker["geometry_defaults"])
    evidence = cast(Mapping[str, Any], defaults["overlay_evidence"])
    return {
        "policy_version": marker["policy_version"],
        "profile": profile,
        "profile_enabled": profiles[profile],
        "enabled": marker["enabled"],
        "plugin_capability": marker["plugin_capability"],
        "layer": marker["layer"],
        "style": marker["style"],
        "layer_fingerprint": marker["layer_fingerprint"],
        "style_fingerprint": marker["style_fingerprint"],
        "height_bits": marker["height_bits"],
        "rotation_bits": marker["rotation_bits"],
        "text_prefix": marker["text_prefix"],
        "text_derivation_version": marker["text_derivation_version"],
        "geometry_defaults": {
            "space_kind": defaults["space_kind"],
            "block_path": list(cast(list[str], defaults["block_path"])),
            "overlay_evidence": {
                "unique_content": evidence["unique_content"],
                "left_panel": evidence["left_panel"],
                "corresponding_right_absent": evidence[
                    "corresponding_right_absent"
                ],
                "visible_interference": evidence["visible_interference"],
                "unsupported_data": evidence["unsupported_data"],
            },
        },
    }


def derive_native_marker_text(
    operation_id: str,
    marker_policy: Mapping[str, Any],
) -> str:
    """Derive the only allowed marker text from an operation and policy version."""

    if (
        marker_policy.get("text_derivation_version") != "operation-id-suffix/v1"
        or marker_policy.get("text_prefix") != "LPF-REVIEW-"
        or not operation_id.startswith("native-operation-")
    ):
        raise ValueError("marker text policy is unsupported")
    return cast(str, marker_policy["text_prefix"]) + operation_id.removeprefix(
        "native-operation-"
    )


def native_host_binding(
    session: Mapping[str, Any],
    config: Mapping[str, Any],
) -> str:
    """Return the stable compatibility identity permitted across sessions.

    PIDs, pipe names, nonces, challenges, and session IDs are deliberately
    absent.  A renewed session may therefore be fresh, but it cannot switch
    host executable, adapter/plugin, capability set, runtime, or configured
    native profile beneath an already-audited plan.
    """

    host_compatibility = cast(Mapping[str, Any], config["host_compatibility"])
    expected_host = {
        "product": host_compatibility["host_product"],
        "release": host_compatibility["host_release"],
        "runtime": host_compatibility["host_runtime"],
        "mode": host_compatibility["audit_host_mode"],
    }
    expected_plugin = {
        "id": config["plugins"]["readback"]["id"],
        "version": config["plugins"]["readback"]["version"],
        "fingerprint": config["plugins"]["readback"]["sha256"],
    }
    capabilities = sorted(cast(list[str], session["capabilities"]))
    if (
        session["adapter"] != config["adapter"]
        or session["plugin"] != expected_plugin
        or session["host"] != expected_host
        or not set(config["required_capabilities"]).issubset(capabilities)
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "native host compatibility identity differs",
        )
    return canonical_sha256(
        {
            "adapter": session["adapter"],
            "capabilities": capabilities,
            "configured_native_profile": {
                "adapter_profile": config["adapter"]["profile"],
                "geometry_limits": config["geometry_limits"],
                "marker_policy": native_marker_policy_binding(config),
                "operation_profiles": config["operation_profiles"],
                "write_revision_transition": config["write_revision_transition"],
            },
            "core_console_fingerprint": config["core_console"]["sha256"],
            "core_console_mode": host_compatibility["core_console_mode"],
            "host": session["host"],
            "host_executable_fingerprint": session["process"][
                "executable_fingerprint"
            ],
            "plugin": session["plugin"],
            "protocol": config["protocol"],
            "required_capabilities": sorted(config["required_capabilities"]),
            "write_plugin": {
                "fingerprint": config["plugins"]["write"]["sha256"],
                "id": config["plugins"]["write"]["id"],
                "version": config["plugins"]["write"]["version"],
            },
        }
    )


def native_marker_fingerprint(operation: Mapping[str, Any]) -> str:
    """Digest the exact, operation-derived marker fields independent of handle."""

    return canonical_sha256(
        {
            key: operation[key]
            for key in (
                "block_path",
                "height",
                "kind",
                "layer",
                "marker_text",
                "overlay_evidence",
                "owner_handle",
                "position",
                "rotation",
                "sequence_index",
                "space",
                "style",
            )
        }
    )


def derive_native_target_id(entity: Mapping[str, Any]) -> str:
    """Derive a durable opaque target identifier without exposing its handle."""

    return "native-target-" + canonical_sha256(
        {
            "geometry_fingerprint": entity["geometry_fingerprint"],
            "opaque_state_digest": entity["opaque_state_digest"],
        }
    )[:24]


def _preflight_geometry_limits(
    artifact: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    """Reject cap+1 geometry before canonical/schema work allocates copies.

    A decoded JSON value is already resident, but this deliberately runs
    before root copying, normalization, integrity hashing, and Draft schema
    traversal.  The full schema remains authoritative for types and all other
    constraints; this is only an early, bounded hard-limit gate.
    """

    if not isinstance(artifact, Mapping):
        return
    entities = artifact.get("entities")
    if not isinstance(entities, list):
        return
    containers = artifact.get("containers")
    if isinstance(containers, list) and len(containers) > MAX_NATIVE_GEOMETRY_CONTAINERS:
        raise PipelineError(
            ErrorCode.NATIVE_GEOMETRY_INVALID,
            "native geometry exceeds the fixed container limit",
        )
    _check_deadline(deadline_check, "geometry limit preflight")
    if len(entities) > MAX_NATIVE_GEOMETRY_ENTITIES:
        raise PipelineError(
            ErrorCode.NATIVE_GEOMETRY_INVALID,
            "native geometry exceeds the fixed entity limit",
        )
    total_segments = 0
    for entity_index, entity in enumerate(entities):
        if entity_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
            _check_deadline(deadline_check, "geometry limit preflight")
        if not isinstance(entity, Mapping):
            continue
        segments = entity.get("segments")
        if not isinstance(segments, list):
            continue
        if len(segments) > MAX_NATIVE_GEOMETRY_SEGMENTS:
            raise PipelineError(
                ErrorCode.NATIVE_GEOMETRY_INVALID,
                "native geometry exceeds the fixed segment limit",
            )
        total_segments += len(segments)
        if total_segments > MAX_NATIVE_GEOMETRY_SEGMENTS:
            raise PipelineError(
                ErrorCode.NATIVE_GEOMETRY_INVALID,
                "native geometry exceeds the fixed segment limit",
            )
    # Direct in-process callers do not have a raw JSON string to gate. Count
    # their canonical representation only after the cheap cardinality guards
    # above, but before schema/integrity work, so they cannot bypass the same
    # raw export budget used by pipe/file boundaries.
    validate_json_string_limits(
        artifact,
        deadline_check=deadline_check,
        maximum_string_codepoints=MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS,
    )
    canonical_geometry_json_bytes(
        artifact,
        error=ErrorCode.NATIVE_GEOMETRY_INVALID,
        deadline_check=deadline_check,
    )


def _validate_geometry_semantics(
    artifact: dict[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    owners: set[str] = set()
    for owner_index, owner in enumerate(cast(list[str], artifact["owners"])):
        if owner_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
            _check_deadline(deadline_check, "geometry owner semantic validation")
        owners.add(owner)
    active_v2 = artifact["schema_version"] == _ACTIVE_SCHEMA_VERSIONS["geometry"]
    physical_containers: list[dict[str, Any]] | None = None
    containers_by_key: dict[
        tuple[str, str, str, tuple[str, ...]], dict[str, Any]
    ] = {}
    if active_v2:
        physical_containers = cast(list[dict[str, Any]], artifact["containers"])
        if (
            len(physical_containers) == 0
            or len(physical_containers) > MAX_NATIVE_GEOMETRY_CONTAINERS
        ):
            raise ValueError("native physical container cardinality is invalid")
        previous_container: tuple[str, str, str, tuple[str, ...]] | None = None
        for container_index, container in enumerate(physical_containers):
            if container_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
                _check_deadline(
                    deadline_check,
                    "geometry physical container semantic validation",
                )
            key = _container_key_from_record(container)
            owner = cast(str, container["owner_handle"])
            count = container["physical_slot_count"]
            if (
                owner not in owners
                or key in containers_by_key
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                or count > MAX_NATIVE_PHYSICAL_SLOT_COUNT
                or (
                    previous_container is not None
                    and key <= previous_container
                )
            ):
                raise ValueError("native physical container is invalid")
            containers_by_key[key] = container
            previous_container = key
    entities = cast(list[dict[str, Any]], artifact["entities"])
    if len(entities) > MAX_NATIVE_GEOMETRY_ENTITIES:
        raise ValueError("too many native entities")
    handles: set[str] = set()
    sequences: set[tuple[tuple[str, str, str, tuple[str, ...]], int]] = set()
    total_segments = 0
    expected_order: list[dict[str, Any]] = []
    for entity_index, entity in enumerate(entities):
        if entity_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
            _check_deadline(deadline_check, "geometry entity semantic validation")
        handle = cast(str, entity["handle"])
        if handle in handles:
            raise ValueError("duplicate native handle")
        handles.add(handle)
        if entity["owner_handle"] not in owners:
            raise ValueError("entity owner is unknown")
        container_key = _container_key(entity)
        if active_v2:
            physical_container = containers_by_key.get(container_key)
            if (
                physical_container is None
                or physical_container["owner_handle"] != entity["owner_handle"]
                or entity["sequence_index"]
                >= physical_container["physical_slot_count"]
            ):
                raise ValueError(
                    "native entity is outside its physical container extent"
                )
        key = (container_key, cast(int, entity["sequence_index"]))
        if key in sequences:
            raise ValueError("duplicate native sequence index")
        sequences.add(key)
        for value in cast(list[str], entity["position"]):
            _finite_bits(value)
        minimum = bits_vector(cast(list[str], entity["bounds"]["minimum"]))
        maximum = bits_vector(cast(list[str], entity["bounds"]["maximum"]))
        if any(left > right for left, right in zip(minimum, maximum, strict=True)):
            raise ValueError("native bounds are inverted")
        _finite_bits(cast(str, entity["rotation"]))
        _finite_bits(cast(str, entity["height"]))
        block_path = cast(list[str], entity["block_path"])
        if len(block_path) != len(set(block_path)):
            raise ValueError("repeated block path handle")
        segments = cast(list[Mapping[str, Any]], entity["segments"])
        total_segments += len(segments)
        if total_segments > MAX_NATIVE_GEOMETRY_SEGMENTS:
            raise ValueError("too many native segments")
        for segment_index, segment in enumerate(segments):
            if segment_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
                _check_deadline(deadline_check, "geometry segment semantic validation")
            bits_vector(cast(list[str], segment["start"]))
            bits_vector(cast(list[str], segment["end"]))
        native_type = entity["native_type"]
        if native_type == "DBTEXT":
            if not isinstance(entity["text"], str) or not isinstance(entity["style"], str):
                raise ValueError("DBTEXT lacks exact text/style")
            if segments:
                raise ValueError("DBTEXT has segments")
        elif native_type == "LINE":
            if entity["text"] is not None or entity["style"] is not None or len(segments) != 1:
                raise ValueError("LINE shape is invalid")
        elif native_type == "LWPOLYLINE":
            if entity["text"] is not None or entity["style"] is not None or not segments:
                raise ValueError("polyline shape is invalid")
        elif native_type == "OPAQUE":
            if entity["text"] is not None or entity["style"] is not None:
                raise ValueError("opaque record leaks unsupported content")
        else:
            raise ValueError("unsupported native record")
        projection = _geometry_projection(entity)
        if entity_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
            _check_deadline(deadline_check, "geometry fingerprint validation")
        if entity["geometry_fingerprint"] != canonical_sha256(
            {"geometry": projection},
            deadline_check=deadline_check,
        ):
            raise ValueError("native geometry fingerprint mismatch")
        if entity["opaque_state_digest"] != canonical_sha256(
            {"opaque_state": projection},
            deadline_check=deadline_check,
        ):
            raise ValueError("native opaque state digest mismatch")
        expected_order.append(entity)
    _check_deadline(deadline_check, "geometry ordering validation")
    sorted_entities = sorted(
        expected_order,
        key=lambda item: (*_container_key(item), int(item["sequence_index"])),
    )
    if entities != sorted_entities:
        raise ValueError("native geometry records are not ordered")
    document = cast(dict[str, Any], artifact["document"])
    order_projection: list[dict[str, Any]] = []
    geometry_projection: list[dict[str, Any]] = []
    opaque_state_digests: list[str] = []
    for entity_index, item in enumerate(entities):
        if entity_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
            _check_deadline(deadline_check, "geometry document projection")
        order_projection.append(
            {
                "container": _container_key(item),
                "sequence_index": item["sequence_index"],
                "handle": item["handle"],
                "geometry_fingerprint": item["geometry_fingerprint"],
                "opaque_state_digest": item["opaque_state_digest"],
            }
        )
        geometry_projection.append(_geometry_projection(item))
        opaque_state_digests.append(cast(str, item["opaque_state_digest"]))
    container_projection = native_container_sequences(
        entities,
        physical_containers,
        deadline_check=deadline_check,
    )
    _check_deadline(deadline_check, "geometry document fingerprint validation")
    expected_order_value: Any = order_projection
    expected_geometry_value: Any = geometry_projection
    if active_v2:
        # The active carrier retains all physical containers, including
        # erased-only ones. These explicit records must be inseparable from
        # ordered/geometry/protected digests rather than reconstructed from
        # the active entities below them.
        expected_order_value = {
            "containers": physical_containers,
            "entities": order_projection,
        }
        expected_geometry_value = {
            "containers": physical_containers,
            "entities": geometry_projection,
        }
    expected_order_digest = canonical_sha256(
        expected_order_value,
        deadline_check=deadline_check,
    )
    expected_container_order_digest = canonical_sha256(
        container_projection,
        deadline_check=deadline_check,
    )
    expected_geometry_digest = canonical_sha256(
        expected_geometry_value,
        deadline_check=deadline_check,
    )
    # A saved/reopened output necessarily receives a new file identity,
    # database instance, and revision binding.  Those are separately bound at
    # each phase and must not make the protected content-state digest drift.
    document_state = {
        "table_state_digest": document["table_state_digest"],
        "layout_state_digest": document["layout_state_digest"],
        "block_state_digest": document["block_state_digest"],
        "marker_layer_fingerprint": document["marker_layer_fingerprint"],
        "marker_style_fingerprint": document["marker_style_fingerprint"],
    }
    expected_document_state = canonical_sha256(
        document_state,
        deadline_check=deadline_check,
    )
    expected_protected = canonical_sha256(
        {
            "document_state_digest": expected_document_state,
            # Owner records are protected host state even when currently
            # unused by an entity. Preserve their complete canonical order.
            "owners": artifact["owners"],
            **(
                {"containers": physical_containers}
                if active_v2
                else {}
            ),
            "opaque_state_digests": opaque_state_digests,
        },
        deadline_check=deadline_check,
    )
    expected_protected_order = canonical_sha256(
        {
            "container_sequences": container_projection,
            "document_state_digest": expected_document_state,
            "owners": artifact["owners"],
        },
        deadline_check=deadline_check,
    )
    if (
        document["ordered_entity_digest"] != expected_order_digest
        or document["container_order_digest"] != expected_container_order_digest
        or document["complete_geometry_digest"] != expected_geometry_digest
        or document["document_state_digest"] != expected_document_state
        or document["protected_state_digest"] != expected_protected
        or document["protected_order_digest"] != expected_protected_order
    ):
        raise ValueError("native document digest mismatch")
    if is_active_native_contract("geometry", artifact):
        portable = prewrite_semantic_projection(artifact)
        if (
            artifact["portable_prewrite_projection"] != portable
            or artifact["portable_prewrite_projection_digest"]
            != canonical_sha256(portable, deadline_check=deadline_check)
            or artifact["portable_prewrite_projection_digest"]
            != canonical_sha256(
                cast(Mapping[str, Any], artifact["portable_prewrite_projection"]),
                deadline_check=deadline_check,
            )
        ):
            raise ValueError("native portable prewrite projection mismatch")
    binding = cast(Mapping[str, Any], artifact["binding"])
    if (
        binding["document_binding_digest"] != geometry_document_binding_digest(artifact)
        or (
            is_active_native_contract("geometry", artifact)
            and binding["session_schema_version"]
            != _ACTIVE_SCHEMA_VERSIONS["session"]
        )
        or binding["stable_host_binding_digest"]
        != _stable_geometry_host_binding_digest(
            protocol_version=cast(str, binding["protocol_version"]),
            protocol_major=cast(int, binding["protocol_major"]),
            protocol_minor=cast(int, binding["protocol_minor"]),
            host=cast(Mapping[str, Any], binding["host"]),
            process=cast(Mapping[str, Any], binding["process"]),
            adapter=cast(Mapping[str, Any], binding["adapter"]),
            plugin=cast(Mapping[str, Any], binding["plugin"]),
            capabilities=cast(list[str], binding["capabilities"]),
        )
    ):
        raise ValueError("native geometry binding digest mismatch")


def validate_native_session_temporal_bounds(
    created_at: str | datetime,
    expires_at: str | datetime,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Validate the one strict temporal policy for native bridge sessions.

    Persisted values must use the schema's whole-second RFC 3339 UTC spelling;
    the non-persisted handshake context supplies equivalent aware UTC
    ``datetime`` values.  There is intentionally no future-clock tolerance:
    accepting one would permit a local clock rollback to extend a session.
    """

    def timestamp(value: str | datetime, *, persisted: bool) -> datetime:
        if isinstance(value, str):
            parsed = parse_utc(value)
            if format_utc(parsed) != value:
                raise ValueError("native session timestamp is not strict RFC3339 UTC")
            return parsed
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
        ):
            raise ValueError("native session timestamp is not aware UTC")
        if persisted:
            raise ValueError("native persisted session timestamp is not RFC3339 UTC")
        return value.astimezone(UTC)

    persisted = isinstance(created_at, str) and isinstance(expires_at, str)
    created = timestamp(created_at, persisted=persisted)
    expires = timestamp(expires_at, persisted=persisted)
    current = utc_now() if now is None else timestamp(now, persisted=False)
    if expires <= created:
        raise ValueError("native session lifetime is not positive")
    if expires - created > MAX_NATIVE_SESSION_LIFETIME:
        raise ValueError("native session lifetime exceeds fixed maximum")
    if current < created:
        raise ValueError("native session creation is in the future")
    if current >= expires:
        raise ValueError("native session has expired")
    return created, expires


def validate_native_session_monotonic_bounds(
    monotonic_clock: Any,
    monotonic_boot_id: Any,
    monotonic_issued: Any,
    monotonic_expires: Any,
) -> tuple[int, int]:
    """Validate the signed same-boot uptime interval in a session descriptor.

    ``GetTickCount64`` is a cross-process Windows uptime source.  Its values
    are persisted as decimal strings rather than JSON numbers so an adapter
    cannot silently lose a high unsigned 64-bit tick through a floating-point
    JSON implementation.  The fixed interval is deliberately exact: a
    descriptor has one five-minute budget that begins at preparation, never a
    new budget at handshake completion or client construction.
    """

    if (
        monotonic_clock != NATIVE_SESSION_MONOTONIC_CLOCK
        or not isinstance(monotonic_boot_id, str)
        or _NATIVE_SESSION_BOOT_ID_PATTERN.fullmatch(monotonic_boot_id) is None
    ):
        raise ValueError("native session monotonic clock domain is invalid")

    def uptime(value: Any) -> int:
        if (
            not isinstance(value, str)
            or _NATIVE_SESSION_UPTIME_PATTERN.fullmatch(value) is None
        ):
            raise ValueError("native session uptime is not strict decimal")
        parsed = int(value)
        if parsed > MAX_NATIVE_SESSION_UPTIME_MILLISECONDS:
            raise ValueError("native session uptime exceeds GetTickCount64")
        return parsed

    issued = uptime(monotonic_issued)
    expires = uptime(monotonic_expires)
    if expires <= issued:
        raise ValueError("native session monotonic lifetime is not positive")
    if (
        issued
        > MAX_NATIVE_SESSION_UPTIME_MILLISECONDS
        - MAX_NATIVE_SESSION_LIFETIME_MILLISECONDS
    ):
        raise ValueError("native session monotonic deadline wraps")
    if expires - issued != MAX_NATIVE_SESSION_LIFETIME_MILLISECONDS:
        raise ValueError("native session monotonic lifetime is not fixed")
    return issued, expires


def _validate_session_semantics(
    artifact: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    active = is_active_native_contract("session", artifact)
    # Frozen descriptors can be read long after their one-use deadline.  For
    # v1 reporting/migration validation prove the original interval shape but
    # deliberately do not reinterpret it as a currently live session.
    created_at = cast(str, artifact["created_at"])
    validate_native_session_temporal_bounds(
        created_at,
        cast(str, artifact["expires_at"]),
        now=now if active else parse_utc(created_at),
    )
    if active:
        if artifact["config_schema_version"] != _ACTIVE_SCHEMA_VERSIONS["config"]:
            raise ValueError("native session config schema differs")
        validate_native_session_monotonic_bounds(
            artifact["monotonic_clock"],
            artifact["monotonic_boot_id"],
            artifact["monotonic_issued"],
            artifact["monotonic_expires"],
        )
    if artifact["mode"] != "read_only":
        raise ValueError("native session is not read-only")
    capabilities = cast(list[str], artifact["capabilities"])
    if any(capability.startswith("write.") for capability in capabilities):
        raise ValueError("read-only session advertises write capability")
    if not {"read.inventory/v1", "read.exact_geometry/v1"}.issubset(capabilities):
        raise ValueError("session lacks required read capabilities")
    host = cast(Mapping[str, Any], artifact["host"])
    if host["mode"] != "full_host":
        raise ValueError("read-only bridge session is not a full host session")
    try:
        expected_response = derive_challenge_response(
            cast(str, artifact["client_nonce"]),
            cast(str, artifact["challenge"]),
            cast(str, artifact["bridge_nonce"]),
            session_id=cast(str, artifact["session_id"]),
        )
    except NativeProtocolError as error:
        raise ValueError("native session handshake transcript is invalid") from error
    if not compare_digest(cast(str, artifact["challenge_response"]), expected_response):
        raise ValueError("native session handshake response mismatches transcript")


def _validate_inventory_semantics(
    artifact: dict[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    """Retain an explicit semantic stage after the fixed inventory schema."""

    _check_deadline(deadline_check, "inventory semantic validation")
    if set(artifact) != {
        "schema_version",
        "document_revision_fingerprint",
        "inventory_digest",
    } or artifact["schema_version"] != _ACTIVE_SCHEMA_VERSIONS["inventory"]:
        raise ValueError("native inventory shape is invalid")


def _validate_audit_semantics(artifact: dict[str, Any]) -> None:
    created = parse_utc(cast(str, artifact["created_at"]))
    expires = parse_utc(cast(str, artifact["expires_at"]))
    if expires != created + timedelta(minutes=15):
        raise ValueError("native audit lifetime is invalid")
    records = cast(list[dict[str, Any]], artifact["records"])
    if artifact["record_cardinality"] != PRIVATE_RECORD_CARDINALITY:
        raise ValueError("native audit cardinality claim is false")
    if is_active_native_contract("audit", artifact) and (
        artifact["config_schema_version"] != _ACTIVE_SCHEMA_VERSIONS["config"]
        or artifact["session_schema_version"] != _ACTIVE_SCHEMA_VERSIONS["session"]
        or artifact["geometry_schema_version"] != _ACTIVE_SCHEMA_VERSIONS["geometry"]
    ):
        raise ValueError("native audit version binding differs")
    if records != sorted(records, key=lambda item: cast(str, item["target_id"])):
        raise ValueError("native audit records are not ordered")
    records_by_id = {record["target_id"]: record for record in records}
    if len(records_by_id) != len(records):
        raise ValueError("duplicate native audit target")
    findings = cast(list[dict[str, Any]], artifact["findings"])
    if len({item["finding_id"] for item in findings}) != len(findings):
        raise ValueError("duplicate native audit finding")
    for finding in findings:
        target_id = finding["target_id"]
        profile = finding["profile"]
        actionable = finding["actionability"]
        if actionable:
            if (
                finding["status"] != "actionable"
                or target_id not in records_by_id
                or profile not in records_by_id[target_id]["eligible_profiles"]
            ):
                raise ValueError("native finding is not eligible")
        elif target_id is not None or profile is not None:
            raise ValueError("read-only native finding leaks target authorization")


def _require_native_operation_count(
    operations: list[Mapping[str, Any]],
    *,
    label: str,
    kind: NativeSchemaKind,
    artifact: Mapping[str, Any],
) -> None:
    """Apply the versioned cardinality bound after schema validation.

    Historical v1 plans remain readable at their originally published
    2,000-operation limit.  Active v2 writes are constrained to 1,024 so a
    complete result always fits the fixed transport budget.
    """

    maximum = (
        MAX_NATIVE_OPERATION_COUNT
        if is_active_native_contract(kind, artifact)
        else MAX_NATIVE_LEGACY_OPERATION_COUNT
    )
    if not 1 <= len(operations) <= maximum:
        raise ValueError(f"{label} operation count exceeds the fixed result budget")


def _validate_intent_semantics(artifact: dict[str, Any]) -> None:
    operations = cast(list[dict[str, Any]], artifact["operations"])
    _require_native_operation_count(
        operations,
        label="native intent",
        kind="intent",
        artifact=artifact,
    )
    operation_ids = [cast(str, item["operation_id"]) for item in operations]
    if is_active_native_contract("intent", artifact) and (
        artifact["audit_binding"]["audit_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["audit"]
    ):
        raise ValueError("native intent audit schema differs")
    if len(operation_ids) != len(set(operation_ids)):
        raise ValueError("duplicate native intent operation")
    targets: set[str] = set()
    for operation in operations:
        kind = operation["kind"]
        if kind == "translate_dbtext":
            delta = bits_vector(cast(list[str], operation["delta"]))
            # Validate the submitted bit strings through the one shared
            # translation primitive even before private geometry is available.
            # Per-scalar representability is then checked against fresh exact
            # geometry during manifest construction.
            for axis_delta in cast(list[str], operation["delta"]):
                translate_binary64_bits("0000000000000000", axis_delta)
            if (
                delta[2] != 0.0
                or (delta[0] == 0.0 and delta[1] == 0.0)
                or any(abs(value) > MAX_TRANSLATION for value in delta[:2])
            ):
                raise ValueError("native translation is outside bounded XY profile")
            target = cast(str, operation["target_id"])
            if target in targets:
                raise ValueError("duplicate native intent target")
            targets.add(target)
        elif kind == "delete_auxiliary_overlay_text":
            target = cast(str, operation["target_id"])
            if target in targets:
                raise ValueError("duplicate native intent target")
            targets.add(target)
        elif kind == "create_review_marker":
            bits_vector(cast(list[str], operation["position"]))
        else:
            raise ValueError("unknown native intent operation")


def _validate_plan_semantics(artifact: dict[str, Any]) -> None:
    operations = cast(list[dict[str, Any]], artifact["operations"])
    _require_native_operation_count(
        operations,
        label="native plan",
        kind="plan",
        artifact=artifact,
    )
    if artifact["record_cardinality"] != PRIVATE_RECORD_CARDINALITY:
        raise ValueError("native plan cardinality claim is false")
    if is_active_native_contract("plan", artifact) and (
        artifact["audit_binding"]["audit_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["audit"]
        or artifact["intent_schema_version"] != _ACTIVE_SCHEMA_VERSIONS["intent"]
    ):
        raise ValueError("native plan version binding differs")
    ids = [cast(str, item["operation_id"]) for item in operations]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate native plan operation")
    targets: set[str] = set()
    profiles = set(cast(list[str], artifact["operation_profiles"]))
    for operation in operations:
        kind = operation["kind"]
        profile = operation["profile"]
        expected = {
            "translate_dbtext": ("translate_dbtext/v1", "translated-exactly"),
            "delete_auxiliary_overlay_text": (
                "delete_auxiliary_overlay_text/v1",
                "target-absent",
            ),
            "create_review_marker": ("create_review_marker/v1", "one-derived-marker"),
        }.get(kind)
        if expected is None or (profile, operation["postcondition"]) != expected:
            raise ValueError("native plan operation profile mismatch")
        if profile not in profiles:
            raise ValueError("native plan profile is unbound")
        target = operation["target_id"]
        if kind == "create_review_marker":
            if target is not None or operation["expected_before_geometry_fingerprint"] is not None:
                raise ValueError("marker plan has a source target")
        else:
            if (
                not isinstance(target, str)
                or target in targets
                or operation["expected_before_geometry_fingerprint"] is None
                or operation["expected_before_opaque_state_digest"] is None
            ):
                raise ValueError("native plan target is invalid")
            targets.add(target)


def _preflight_embedded_json(
    kind: NativeSchemaKind,
    artifact: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    """Apply each exact outer embedded-JSON cap before NFC or schema work."""

    if not isinstance(artifact, Mapping):
        return
    if kind == "response":
        result = artifact.get("result")
        if not isinstance(result, Mapping):
            return
        if "geometry_json" in result:
            require_geometry_json_utf8_bytes(
                result["geometry_json"],
                error=_error_for(kind),
                deadline_check=deadline_check,
            )
        if "inventory_json" in result:
            require_inventory_json_utf8_bytes(
                result["inventory_json"],
                error=_error_for(kind),
                deadline_check=deadline_check,
            )
    elif kind == "manifest" and "preconditions_geometry_json" in artifact:
        require_geometry_json_utf8_bytes(
            artifact["preconditions_geometry_json"],
            error=_error_for(kind),
            deadline_check=deadline_check,
        )
    elif kind == "console_export" and "geometry_json" in artifact:
        require_geometry_json_utf8_bytes(
            artifact["geometry_json"],
            error=_error_for(kind),
            deadline_check=deadline_check,
        )


def _embedded_geometry(
    text: str,
    *,
    error: ErrorCode,
    deadline_check: _DeadlineCheck | None = None,
) -> dict[str, Any]:
    text = require_geometry_json_utf8_bytes(
        text,
        error=error,
        deadline_check=deadline_check,
    )
    try:
        _check_deadline(deadline_check, "embedded geometry JSON decoding")
        parsed = strict_native_json(
            text,
            deadline_check=deadline_check,
            maximum_string_codepoints=MAX_NATIVE_GEOMETRY_STRING_CODEPOINTS,
        )
        _check_deadline(deadline_check, "embedded geometry JSON decoding")
        _check_deadline(deadline_check, "embedded geometry canonical validation")
        if not geometry_text_matches_canonical_bytes(
            text,
            parsed,
            error=error,
            deadline_check=deadline_check,
        ):
            raise ValueError("embedded geometry is not canonical")
        _check_deadline(deadline_check, "embedded geometry canonical validation")
        _check_deadline(deadline_check, "embedded geometry schema validation")
        checked = validate_native_contract(
            "geometry",
            parsed,
            deadline_check=deadline_check,
        )
        _check_deadline(deadline_check, "embedded geometry semantic validation")
        return checked
    except (CanonicalJsonError, RecursionError, ValueError, PipelineError) as exc:
        if isinstance(exc, PipelineError):
            if exc.code in {
                ErrorCode.NATIVE_GEOMETRY_INVALID,
                ErrorCode.NATIVE_PROTOCOL_INVALID,
                ErrorCode.NATIVE_SESSION_EXPIRED,
            }:
                raise
        raise PipelineError(error, "embedded native geometry is invalid") from exc


def _embedded_inventory(
    text: str,
    *,
    error: ErrorCode,
    deadline_check: _DeadlineCheck | None = None,
) -> dict[str, Any]:
    """Parse and validate the fixed inner inventory document independently."""

    text = require_inventory_json_utf8_bytes(
        text,
        error=error,
        deadline_check=deadline_check,
    )
    try:
        _check_deadline(deadline_check, "embedded inventory JSON decoding")
        parsed = strict_native_json(text, deadline_check=deadline_check)
        _check_deadline(deadline_check, "embedded inventory JSON decoding")
        _check_deadline(deadline_check, "embedded inventory canonical validation")
        if canonical_json_bytes(parsed, deadline_check=deadline_check) != text.encode(
            "utf-8",
            errors="strict",
        ):
            raise ValueError("embedded inventory is not canonical")
        _check_deadline(deadline_check, "embedded inventory canonical validation")
        _check_deadline(deadline_check, "embedded inventory schema validation")
        checked = validate_native_contract(
            "inventory",
            parsed,
            deadline_check=deadline_check,
        )
        _check_deadline(deadline_check, "embedded inventory semantic validation")
        return checked
    except (
        CanonicalJsonError,
        RecursionError,
        UnicodeEncodeError,
        ValueError,
        PipelineError,
    ) as exc:
        if isinstance(exc, PipelineError) and exc.code in {
            ErrorCode.NATIVE_PROTOCOL_INVALID,
            ErrorCode.NATIVE_SESSION_EXPIRED,
        }:
            raise
        raise PipelineError(error, "embedded native inventory is invalid") from exc


def _validate_legacy_manifest_semantics(
    artifact: Mapping[str, Any],
    geometry: Mapping[str, Any],
) -> None:
    """Validate only relationships that existed in published v1.

    This intentionally does not project v2 output constraints, stable-host
    digests, or session-clock fields into a v1 record.  Such a projection
    would turn a read path into an unsafe semantic reinterpretation.
    """

    source = cast(Mapping[str, Any], geometry["source"])
    document = cast(Mapping[str, Any], geometry["document"])
    prewrite = cast(Mapping[str, Any], artifact["expected_prewrite_revision"])
    private_copy = cast(Mapping[str, Any], artifact["private_source_copy"])
    if (
        geometry["schema_version"] != "liang-pingfa/native-geometry-export/v1"
        or artifact["source"] != source
        or prewrite["source_binding"] != source
        or prewrite["document_path_fingerprint"] != source["path_fingerprint"]
        or prewrite["document_file_identity_fingerprint"]
        != source["file_identity_fingerprint"]
        or prewrite["document_content_sha256"] != source["sha256"]
        or prewrite["document_byte_size"] != source["byte_size"]
        or prewrite["database_instance_fingerprint"]
        != document["database_instance_fingerprint"]
        or prewrite["revision_fingerprint"] != document["revision_fingerprint"]
        or prewrite["geometry_digest"] != document["complete_geometry_digest"]
        or prewrite["protected_state_digest"] != document["protected_state_digest"]
        or prewrite["protected_order_digest"] != document["protected_order_digest"]
        or prewrite["document_state_digest"] != document["document_state_digest"]
        or prewrite["adapter_binding"] != geometry_adapter_binding(geometry)
        or prewrite["native_host_binding"] != artifact["native_host_binding"]
        or prewrite["audited_semantic_state_digest"]
        != artifact["audit_binding"]["audit_integrity_sha256"]
        or private_copy["sha256"] != source["sha256"]
        or private_copy["byte_size"] != source["byte_size"]
        or not isinstance(private_copy["file_identity_fingerprint"], str)
    ):
        raise ValueError("legacy manifest binding differs")
    renewal = cast(Mapping[str, Any], artifact["session_renewal"])
    if (
        renewal["native_host_binding"] != artifact["native_host_binding"]
        or renewal["audited_session_binding"] == renewal["fresh_session_binding"]
        or parse_utc(cast(str, renewal["expires_at"]))
        < parse_utc(cast(str, artifact["expires_at"]))
    ):
        raise ValueError("legacy manifest session-renewal proof differs")
    operations = cast(list[Mapping[str, Any]], artifact["operations"])
    _require_native_operation_count(
        operations,
        label="legacy native manifest",
        kind="manifest",
        artifact=artifact,
    )
    operation_ids = [operation["operation_id"] for operation in operations]
    if len(operation_ids) != len(set(operation_ids)):
        raise ValueError("duplicate legacy manifest operation")


def _validate_manifest_semantics(
    artifact: dict[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    created = parse_utc(cast(str, artifact["created_at"]))
    expires = parse_utc(cast(str, artifact["expires_at"]))
    if expires <= created or expires - created > timedelta(minutes=5):
        raise ValueError("native manifest lifetime is invalid")
    if artifact["record_cardinality"] != PRIVATE_RECORD_CARDINALITY:
        raise ValueError("native manifest cardinality claim is false")
    geometry = _embedded_geometry(
        cast(str, artifact["preconditions_geometry_json"]),
        error=ErrorCode.NATIVE_MANIFEST_INVALID,
        deadline_check=deadline_check,
    )
    _check_deadline(deadline_check, "manifest embedded geometry validation")
    if artifact["preconditions_geometry_sha256"] != canonical_sha256(
        geometry,
        deadline_check=deadline_check,
    ):
        raise ValueError("manifest geometry digest mismatch")
    is_v2 = is_active_native_contract("manifest", artifact)
    if not is_v2:
        # Frozen v1 manifests are readable historical evidence only. Their
        # source schema has no final-output constraints, stable-host digest,
        # or same-boot session binding, so none can be invented during a
        # legacy parse.
        _validate_legacy_manifest_semantics(artifact, geometry)
        return
    prewrite = cast(Mapping[str, Any], artifact["expected_prewrite_revision"])
    source = cast(Mapping[str, Any], geometry["source"])
    document = cast(Mapping[str, Any], geometry["document"])
    # v2 intentionally binds only the input private copy.  The final file's
    # hash/size/identity do not exist until SaveAs has completed; only a
    # narrowly scoped, integrity-covered constraint set can be authored
    # before execution.
    prewrite_output = cast(
        Mapping[str, Any], artifact["expected_prewrite_output_copy_binding"]
    )
    constraints = cast(Mapping[str, Any], artifact["final_output_constraints"])
    original_source = cast(Mapping[str, Any], artifact["source"])
    if (
        geometry["schema_version"] != _ACTIVE_SCHEMA_VERSIONS["geometry"]
        or artifact["audit_binding"]["audit_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["audit"]
        or artifact["plan_binding"]["plan_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["plan"]
        or artifact["intent_binding"]["intent_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["intent"]
        or artifact["session_renewal"]["audited_session_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["session"]
        or artifact["session_renewal"]["fresh_session_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["session"]
        or source != prewrite_output
        or prewrite["source_binding"] != prewrite_output
        or prewrite["document_path_fingerprint"]
        != prewrite_output["path_fingerprint"]
        or prewrite["document_file_identity_fingerprint"]
        != prewrite_output["file_identity_fingerprint"]
        or prewrite["document_content_sha256"] != prewrite_output["sha256"]
        or prewrite["document_byte_size"] != prewrite_output["byte_size"]
        or original_source["sha256"] != prewrite_output["sha256"]
        or original_source["byte_size"] != prewrite_output["byte_size"]
        or original_source["dwg_header_signature"]
        != prewrite_output["dwg_header_signature"]
        or original_source["path_fingerprint"]
        == prewrite_output["path_fingerprint"]
        or original_source["file_identity_fingerprint"]
        == prewrite_output["file_identity_fingerprint"]
    ):
        raise ValueError("manifest prewrite private-copy binding differs")
    if (
        constraints["authorized_private_path_fingerprint"]
        != prewrite_output["path_fingerprint"]
        or constraints["required_dwg_header_signature"]
        != prewrite_output["dwg_header_signature"]
        or constraints["required_dwg_version"]
        != constraints["required_dwg_header_signature"]
        or constraints["max_byte_size"] < 6
    ):
        raise ValueError("manifest final-output constraints differ from prewrite")
    portable = prewrite_semantic_projection(geometry)
    if (
        prewrite["bridge_document_identity"]
        != {
            "database_instance_fingerprint": document[
                "database_instance_fingerprint"
            ],
            "revision_fingerprint": document["revision_fingerprint"],
        }
        or prewrite["portable_prewrite_projection"] != portable
        or prewrite["portable_prewrite_projection_digest"]
        != canonical_sha256(portable, deadline_check=deadline_check)
        or prewrite["portable_prewrite_projection_digest"]
        != canonical_sha256(
            cast(Mapping[str, Any], prewrite["portable_prewrite_projection"]),
            deadline_check=deadline_check,
        )
        or prewrite["adapter_binding"] != geometry_adapter_binding(geometry)
        or prewrite["native_host_binding"] != artifact["native_host_binding"]
        or prewrite["stable_host_binding_digest"]
        != artifact["stable_host_binding_digest"]
        or artifact["stable_host_binding_digest"]
        != native_execution_stable_host_binding_digest(
            geometry,
            cast(Mapping[str, Any], artifact["marker_policy_binding"]),
        )
        or prewrite["audited_semantic_state_digest"]
        != artifact["audit_binding"]["audit_integrity_sha256"]
    ):
        raise ValueError("manifest pre-write revision mismatches geometry")
    renewal = cast(Mapping[str, Any], artifact["session_renewal"])
    if (
        renewal["native_host_binding"] != artifact["native_host_binding"]
        or renewal["audited_session_binding"] == renewal["fresh_session_binding"]
        or geometry["binding"]["session_binding_digest"]
        != renewal["fresh_session_binding"]
        or parse_utc(cast(str, renewal["expires_at"]))
        < parse_utc(cast(str, artifact["expires_at"]))
    ):
        raise ValueError("manifest session-renewal proof mismatches")
    operation_ids = [operation["operation_id"] for operation in artifact["operations"]]
    _require_native_operation_count(
        cast(list[Mapping[str, Any]], artifact["operations"]),
        label="native manifest",
        kind="manifest",
        artifact=artifact,
    )
    if len(operation_ids) != len(set(operation_ids)):
        raise ValueError("duplicate native manifest operation")
    targets = [
        operation["target_id"]
        for operation in cast(list[dict[str, Any]], artifact["operations"])
        if operation["kind"] != "create_review_marker"
    ]
    if len(targets) != len(set(targets)):
        raise ValueError("duplicate native manifest target")
    marker_policy = cast(Mapping[str, Any], artifact["marker_policy_binding"])
    marker_defaults = cast(Mapping[str, Any], marker_policy["geometry_defaults"])
    by_target = {
        derive_native_target_id(entity): entity
        for entity in cast(list[dict[str, Any]], geometry["entities"])
    }
    # Marker slots are reservations made from the immutable prewrite physical
    # Modelspace extent. They are never recomputed from a prefix where a
    # delete may have removed an active tail while its erased slot remains.
    marker_operations = [
        operation
        for operation in cast(list[dict[str, Any]], artifact["operations"])
        if operation["kind"] == "create_review_marker"
    ]
    marker_sequence_reservations: dict[str, int] = {}
    marker_container: Mapping[str, Any] | None = None
    if marker_operations:
        direct_modelspace = [
            container
            for container in cast(list[Mapping[str, Any]], geometry["containers"])
            if (
                container["space"]["kind"] == "modelspace"
                and container["space"]["block_handle"] is None
                and container["block_path"] == []
            )
        ]
        if len(direct_modelspace) != 1:
            raise ValueError("marker has no direct Modelspace reservation base")
        marker_container = direct_modelspace[0]
        physical_slot_count = cast(
            int,
            marker_container["physical_slot_count"],
        )
        reserved_slots: set[int] = set()
        for marker_offset, operation in enumerate(marker_operations):
            slot = cast(int, operation["sequence_index"])
            if (
                slot != physical_slot_count + marker_offset
                or slot in reserved_slots
            ):
                raise ValueError(
                    "marker sequence reservation differs from prewrite physical Modelspace"
                )
            reserved_slots.add(slot)
            marker_sequence_reservations[
                cast(str, operation["operation_id"])
            ] = slot
    for operation_index, operation in enumerate(
        cast(list[dict[str, Any]], artifact["operations"])
    ):
        if operation_index % _SCHEMA_CHECKPOINT_INTERVAL == 0:
            _check_deadline(deadline_check, "manifest semantic validation")
        if operation["kind"] == "translate_dbtext":
            target = by_target.get(cast(str, operation["target_id"]))
            if target is None or target["native_type"] != "DBTEXT":
                raise ValueError("manifest translation target is unavailable")
            if operation["expected_after"] != translated_geometry_bits(
                target, cast(list[str], operation["delta"])
            ):
                raise ValueError("manifest translation transition differs")
        elif operation["kind"] == "create_review_marker":
            expected = derive_native_marker_text(
                cast(str, operation["operation_id"]),
                marker_policy,
            )
            if (
                operation["marker_text"] != expected
                or operation["marker_fingerprint"]
                != native_marker_fingerprint(operation)
                or operation["owner_handle"] not in geometry["owners"]
                or marker_container is None
                or operation["owner_handle"] != marker_container["owner_handle"]
                or _container_key(operation)
                != _container_key_from_record(marker_container)
                or operation["space"]["kind"] != "modelspace"
                or operation["space"]["kind"] != marker_defaults["space_kind"]
                or operation["block_path"] != marker_defaults["block_path"]
                or operation["layer"] != marker_policy["layer"]
                or operation["style"] != marker_policy["style"]
                or operation["height"] != marker_policy["height_bits"]
                or operation["rotation"] != marker_policy["rotation_bits"]
                or operation["overlay_evidence"]
                != marker_defaults["overlay_evidence"]
                or operation["sequence_index"]
                != marker_sequence_reservations[operation["operation_id"]]
            ):
                raise ValueError("marker text is not operation-derived")


def _validate_console_result_semantics(artifact: dict[str, Any]) -> None:
    _require_native_operation_count(
        cast(list[Mapping[str, Any]], artifact["operation_results"]),
        label="native console result",
        kind="console_result",
        artifact=artifact,
    )
    operation_ids = [item["operation_id"] for item in artifact["operation_results"]]
    if len(operation_ids) != len(set(operation_ids)):
        raise ValueError("duplicate console operation result")
    if is_active_native_contract("console_result", artifact) and (
        artifact["manifest_schema_version"] != _ACTIVE_SCHEMA_VERSIONS["manifest"]
    ):
        raise ValueError("console result manifest schema differs")
    final = cast(Mapping[str, Any], artifact["final_document_binding"])
    if final["revision_fingerprint"] != artifact["final_revision_fingerprint"]:
        raise ValueError("console result final revision binding differs")


def _validate_console_export_semantics(
    artifact: dict[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    geometry = _embedded_geometry(
        cast(str, artifact["geometry_json"]),
        error=ErrorCode.NATIVE_READBACK_INVALID,
        deadline_check=deadline_check,
    )
    _check_deadline(deadline_check, "console export embedded geometry validation")
    if artifact["geometry_sha256"] != canonical_sha256(
        geometry,
        deadline_check=deadline_check,
    ):
        raise ValueError("console export geometry digest mismatch")
    final = cast(Mapping[str, Any], artifact["final_document_binding"])
    if (
        final["revision_fingerprint"] != artifact["final_revision_fingerprint"]
        or final["revision_fingerprint"] != geometry["document"]["revision_fingerprint"]
        or final["database_instance_fingerprint"]
        != geometry["document"]["database_instance_fingerprint"]
        or final["output_copy_binding"] != geometry["source"]
        or (
            is_active_native_contract("console_export", artifact)
            and (
                geometry["schema_version"] != _ACTIVE_SCHEMA_VERSIONS["geometry"]
                or artifact["manifest_schema_version"]
                != _ACTIVE_SCHEMA_VERSIONS["manifest"]
                or artifact["console_result_schema_version"]
                != _ACTIVE_SCHEMA_VERSIONS["console_result"]
            )
        )
    ):
        raise ValueError("console export final document binding differs")


def _validate_verification_semantics(artifact: dict[str, Any]) -> None:
    if artifact["record_cardinality"] != PRIVATE_RECORD_CARDINALITY:
        raise ValueError("native verification cardinality claim is false")
    _require_native_operation_count(
        cast(list[Mapping[str, Any]], artifact["operation_results"]),
        label="native verification",
        kind="verification",
        artifact=artifact,
    )
    operation_ids = [item["operation_id"] for item in artifact["operation_results"]]
    if len(operation_ids) != len(set(operation_ids)):
        raise ValueError("duplicate native verification operation")
    if is_active_native_contract("verification", artifact) and (
        artifact["audit_binding"]["audit_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["audit"]
        or artifact["plan_binding"]["plan_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["plan"]
        or artifact["manifest_binding"]["manifest_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["manifest"]
        or artifact["console_result_binding"]["result_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["console_result"]
        or artifact["console_export_binding"]["export_schema_version"]
        != _ACTIVE_SCHEMA_VERSIONS["console_export"]
    ):
        raise ValueError("native verification version binding differs")


def strict_native_json(
    text: str,
    *,
    deadline_check: _DeadlineCheck | None = None,
    maximum_string_codepoints: int = MAX_JSON_STRING_CODEPOINTS,
) -> dict[str, Any]:
    """Decode strict JSON embedded in a protocol or private workspace file."""

    try:
        _check_deadline(deadline_check, "embedded JSON decoding")
        parsed = load_json_value(
            text,
            deadline_check=deadline_check,
            maximum_string_codepoints=maximum_string_codepoints,
        )
        _check_deadline(deadline_check, "embedded JSON decoding")
    except CanonicalJsonError:
        raise
    if not isinstance(parsed, dict):
        raise CanonicalJsonError("native JSON root is not object")
    return parsed


def load_json_value(
    text: str,
    *,
    deadline_check: _DeadlineCheck | None = None,
    maximum_string_codepoints: int = MAX_JSON_STRING_CODEPOINTS,
) -> Any:
    """Keep the duplicate-key/NFC parser local to this native contract surface."""

    return strict_json_loads(
        text,
        deadline_check=deadline_check,
        maximum_string_codepoints=maximum_string_codepoints,
    )


def load_native_json_value(
    kind: NativeSchemaKind,
    text: str,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> Any:
    """Decode an outer native document with only its exact carrier exceptions."""

    return strict_json_loads(
        text,
        deadline_check=deadline_check,
        opaque_string_rules=opaque_embedded_json_rules(kind),
        maximum_string_codepoints=_maximum_string_codepoints_for(kind),
    )


def validate_native_contract(
    kind: NativeSchemaKind,
    artifact: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a native contract's schema, integrity, and semantic invariants."""

    try:
        if kind == "geometry":
            _preflight_geometry_limits(artifact, deadline_check=deadline_check)
        elif kind in {"response", "manifest", "console_export"}:
            _preflight_embedded_json(
                kind,
                artifact,
                deadline_check=deadline_check,
            )
    except PipelineError:
        raise
    except (CanonicalJsonError, RecursionError, TypeError, ValueError) as error:
        raise PipelineError(
            _error_for(kind),
            "native string preflight failed",
        ) from error
    normalized = _validate_common(kind, artifact, deadline_check=deadline_check)
    try:
        if kind == "config":
            _validate_config_semantics(normalized)
        elif kind == "inventory":
            _validate_inventory_semantics(
                normalized,
                deadline_check=deadline_check,
            )
        elif kind == "session":
            _validate_session_semantics(normalized, now=now)
        elif kind == "geometry":
            _validate_geometry_semantics(normalized, deadline_check=deadline_check)
        elif kind == "audit":
            _validate_audit_semantics(normalized)
        elif kind == "intent":
            _validate_intent_semantics(normalized)
        elif kind == "plan":
            _validate_plan_semantics(normalized)
        elif kind == "manifest":
            _validate_manifest_semantics(
                normalized,
                deadline_check=deadline_check,
            )
        elif kind == "console_result":
            _validate_console_result_semantics(normalized)
            require_console_result_transport_budget(normalized)
        elif kind == "console_export":
            _validate_console_export_semantics(
                normalized,
                deadline_check=deadline_check,
            )
        elif kind == "verification":
            _validate_verification_semantics(normalized)
    except PipelineError:
        raise
    except (
        CanonicalJsonError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
        struct.error,
    ) as error:
        raise PipelineError(_error_for(kind), "native semantic validation failed") from error
    _check_deadline(deadline_check, f"{kind} semantic validation")
    return normalized


def _validate_config_semantics(config: dict[str, Any]) -> None:
    if config["protocol"] != {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR}:
        raise ValueError("unsupported native protocol")
    required = set(cast(list[str], config["required_capabilities"]))
    if not {"read.inventory/v1", "read.exact_geometry/v1"}.issubset(required):
        raise ValueError("native config lacks read capabilities")
    operation_profiles = cast(dict[str, bool], config["operation_profiles"])
    if not operation_profiles["translate_dbtext/v1"]:
        raise ValueError("initial native translation profile must be explicitly enabled")
    if config["adapter"]["id"] == AUTOCAD_ADAPTER_ID:
        for profile, capability in _OPERATION_PROFILE_CAPABILITIES.items():
            if operation_profiles[profile] and capability not in required:
                raise ValueError(
                    "enabled AutoCAD operation lacks advertised plugin capability"
                )
        if operation_profiles["delete_auxiliary_overlay_text/v1"]:
            raise ValueError(
                "the AutoCAD adapter does not support delete_auxiliary_overlay_text"
            )
    plugins = cast(dict[str, dict[str, Any]], config["plugins"])
    if plugins["write"]["command"] != "LPF_NATIVE_EXECUTE_MANIFEST" or plugins[
        "readback"
    ]["command"] != "LPF_NATIVE_EXPORT_MANIFEST":
        raise ValueError("native command is not fixed")
    host_compatibility = cast(dict[str, Any], config["host_compatibility"])
    if (
        host_compatibility["audit_host_mode"] != "full_host"
        or host_compatibility["core_console_mode"] != "core_console"
    ):
        raise ValueError("native host modes are not fixed")
    timeouts = cast(Mapping[str, Any], config["timeouts"])
    bounded_timeouts: dict[str, float] = {
        "pipe_connect_ms": CONNECT_TIMEOUT_SECONDS,
        **{
            field: METHOD_TIMEOUT_SECONDS[method]
            for method, field in METHOD_TIMEOUT_CONFIG_KEYS.items()
        },
        **CONSOLE_TIMEOUT_SECONDS,
    }
    for field, maximum_seconds in bounded_timeouts.items():
        value = timeouts[field]
        multiplier = 1 if field.endswith("_seconds") else 1000
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            or value > int(maximum_seconds * multiplier)
        ):
            raise ValueError("native timeout is outside its hard bound")
    if config["geometry_limits"] != {
        "max_entities": MAX_NATIVE_GEOMETRY_ENTITIES,
        "max_segments": MAX_NATIVE_GEOMETRY_SEGMENTS,
        "max_geometry_json_bytes": MAX_NATIVE_GEOMETRY_JSON_BYTES,
        "max_inventory_json_bytes": MAX_NATIVE_INVENTORY_JSON_BYTES,
    }:
        raise ValueError("native geometry limits are not the fixed v1 bounds")
    if config["write_revision_transition"] not in {
        "save_reopen_changes_revision",
        "preserved_by_plugin_capability",
    }:
        raise ValueError("native write revision transition is unsupported")
    if (
        config["write_revision_transition"] == "preserved_by_plugin_capability"
        and "plugin.revision_preservation/v1" not in required
    ):
        raise ValueError("revision preservation lacks explicit plugin capability")
    for installation in (config["core_console"], plugins["write"], plugins["readback"]):
        path = cast(str, installation["path"])
        if (
            not path
            or '"' in path
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in path)
        ):
            raise ValueError("unsafe native installation path")
    marker_policy = native_marker_policy_binding(config)
    if (
        marker_policy["policy_version"] != "marker-policy/v1"
        or marker_policy["profile"] != "create_review_marker/v1"
        or marker_policy["text_derivation_version"] != "operation-id-suffix/v1"
        or marker_policy["text_prefix"] != "LPF-REVIEW-"
        or marker_policy["geometry_defaults"]["space_kind"] != "modelspace"
        or marker_policy["geometry_defaults"]["block_path"] != []
        or marker_policy["geometry_defaults"]["overlay_evidence"]
        != {
            "unique_content": False,
            "left_panel": False,
            "corresponding_right_absent": False,
            "visible_interference": False,
            "unsupported_data": True,
        }
    ):
        raise ValueError("native marker policy is not fixed")
    _finite_bits(cast(str, marker_policy["height_bits"]))
    _finite_bits(cast(str, marker_policy["rotation_bits"]))


def _private_native_input_error(kind: NativeSchemaKind) -> PipelineError:
    """Build one redacted error without disclosing a local path or DACL."""

    return PipelineError(
        _error_for(kind),
        "private native machine artifact is unavailable",
    )


def _same_private_path(left: Path, right: Path) -> bool:
    """Compare handle-derived paths without resolving any reparse point."""

    return os.path.normcase(os.path.normpath(os.fspath(left))) == os.path.normcase(
        os.path.normpath(os.fspath(right))
    )


def read_private_native_artifact_bytes(
    kind: NativeSchemaKind,
    path: Path,
    *,
    backend: FileOwnershipBackend | None = None,
    acl_reader: Any = None,
    trusted_sids: frozenset[str] | None = None,
    consume: Callable[[bytes], _PrivateReadResult] | None = None,
) -> bytes | _PrivateReadResult:
    """Read a persisted native artifact only through private retained handles.

    The direct final DACL must contain exactly current-user and SYSTEM access;
    a broad output parent is acceptable only while it lacks rights to delete,
    redirect, or replace the retained existing child.  Every lexical ancestor
    is therefore opened no-follow and checked before the file itself is read.
    """

    if kind not in _PRIVATE_PERSISTED_KINDS:
        raise _private_native_input_error(kind)
    chain = None
    opened: OwnedPath | None = None
    try:
        lexical = lexical_absolute_path(path)
        if not lexical.name or lexical.suffix.casefold() != ".json":
            raise OwnershipError("private native artifact name is invalid")
        selected_backend = backend or platform_backend(require_windows=True)
        chain = acquire_lexical_directory_chain(lexical.parent, selected_backend)

        # Avoid an import cycle at module initialization: native_bridge
        # imports this contract module, whereas this path runs only after a
        # caller has explicitly elected to consume persisted native material.
        from .native_bridge import (
            _TRUSTED_ADMINISTRATORS_SID,
            _TRUSTED_SYSTEM_SID,
            _read_component_dacl,
            validate_component_dacl,
        )

        trusted = trusted_sids or frozenset(
            {
                current_user_sid(),
                _TRUSTED_SYSTEM_SID,
                _TRUSTED_ADMINISTRATORS_SID,
            }
        )
        selected_acl_reader = acl_reader or _read_component_dacl
        chain.require_binding()
        for component in chain.components:
            validate_component_dacl(
                selected_acl_reader(component.owned),
                is_directory=True,
                trusted_sids=trusted,
                allow_trustedinstaller_owner=True,
            )
        chain.require_binding()
        opened = selected_backend.open_existing_file_read_lease(lexical)
        binding = opened.capture_binding()
        final_path = opened.final_path()
        if (
            binding.is_directory
            or binding.sha256 is None
            or final_path.name.casefold() != lexical.name.casefold()
            or not _same_private_path(final_path.parent, chain.path)
            or not selected_backend.path_matches_binding(lexical, binding)
            or not selected_backend.path_matches_binding(final_path, binding)
        ):
            raise OwnershipError("private native artifact binding differs")
        # This owner/DACL readback is performed on the already-open final
        # file; it never follows a pathname after opening it. The handle
        # remains live through bounded reading and caller-supplied validation.
        owner_before = verify_private_staging_file(opened, selected_backend)
        payload = b"".join(opened.read_chunks())
        result = consume(payload) if consume is not None else payload
        owner_after = verify_private_staging_file(opened, selected_backend)
        if (
            not opened.capture_binding().same_identity_and_content(binding)
            or not _same_private_path(opened.final_path(), final_path)
            or (
                owner_before is not None
                and owner_after is not None
                and owner_before != owner_after
            )
        ):
            raise OwnershipError("private native artifact changed while read")
        chain.require_binding()
        return result
    except PipelineError as error:
        if error.code == _error_for(kind):
            raise
        raise _private_native_input_error(kind) from error
    except (OSError, OwnershipError) as error:
        raise _private_native_input_error(kind) from error
    finally:
        cleanup_error: BaseException | None = None
        if opened is not None:
            try:
                opened.close()
            except (OSError, OwnershipError) as error:
                cleanup_error = error
        if chain is not None:
            try:
                chain.close()
            except (OSError, OwnershipError) as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            raise _private_native_input_error(kind) from cleanup_error


def load_native_artifact(kind: NativeArtifactKind, path: Path) -> dict[str, Any]:
    """Load a private persisted native artifact with duplicate-key rejection."""

    def consume(payload: bytes) -> dict[str, Any]:
        try:
            if kind == "geometry":
                require_geometry_json_payload_bytes(
                    payload,
                    error=ErrorCode.NATIVE_GEOMETRY_INVALID,
                )
            text = payload.decode("utf-8", errors="strict")
            if kind == "geometry":
                # The payload preflight avoids allocating any decoded text for
                # an oversized file; this preserves the same cap for a
                # terminal-LF private artifact.
                require_geometry_json_utf8_bytes(
                    text[:-1] if text.endswith("\n") else text,
                    error=ErrorCode.NATIVE_GEOMETRY_INVALID,
                )
            loaded = load_native_json_value(kind, text)
        except (CanonicalJsonError, RecursionError, UnicodeDecodeError) as error:
            raise PipelineError(
                _ARTIFACT_ERRORS[kind],
                "native JSON artifact is invalid",
            ) from error
        return validate_native_contract(kind, loaded)

    return cast(
        dict[str, Any],
        read_private_native_artifact_bytes(kind, path, consume=consume),
    )


def load_native_config(path: Path) -> dict[str, Any]:
    """Load an explicit private adapter configuration; never discover one."""

    def consume(payload: bytes) -> dict[str, Any]:
        try:
            loaded = load_native_json_value(
                "config",
                payload.decode("utf-8", errors="strict"),
            )
        except (CanonicalJsonError, RecursionError, UnicodeDecodeError) as error:
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "native config is invalid",
            ) from error
        return require_active_native_contract("config", loaded)

    return cast(
        dict[str, Any],
        read_private_native_artifact_bytes("config", path, consume=consume),
    )


def migrate_native_v1_to_v2(
    kind: NativeSchemaKind,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Explicitly migrate the one v1 shape that preserves all v2 semantics.

    Adapter configuration is declarative and its v2 schema only changes the
    namespace.  Session/audit/plan/manifest/result/readback artifacts are
    intentionally *not* migrated: their published v1 shapes lack mandatory
    same-boot, stable-host, or actual-output bindings.  Inventing any of
    those values would make a legacy read look like fresh authorization.
    """

    checked = validate_native_contract(kind, artifact)
    version = native_contract_schema_version(kind, checked)
    if version == _ACTIVE_SCHEMA_VERSIONS[kind]:
        return checked
    if kind != "config" or version != "liang-pingfa/native-adapter-config/v1":
        raise PipelineError(
            ErrorCode.NATIVE_LEGACY_ARTIFACT_READ_ONLY,
            "legacy native artifact requires a fresh v2 audit/session",
        )
    migrated = dict(checked)
    migrated["schema_version"] = _ACTIVE_SCHEMA_VERSIONS["config"]
    # Config has no integrity carrier. Validate the exact transformed object
    # rather than accepting a caller-supplied partial projection.
    return validate_native_contract("config", migrated)


def native_artifact_integrity(artifact: Mapping[str, Any]) -> str:
    """Return an already-validated artifact's accidental-corruption digest."""

    integrity = artifact.get("integrity")
    if not isinstance(integrity, Mapping) or not isinstance(integrity.get("sha256"), str):
        raise PipelineError(ErrorCode.NATIVE_ARTIFACT_MISMATCH, "native integrity missing")
    return cast(str, integrity["sha256"])


def native_protocol_version() -> str:
    """Expose the immutable protocol identifier for the bridge client."""

    return PROTOCOL_VERSION
