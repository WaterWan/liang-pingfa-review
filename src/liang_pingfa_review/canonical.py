"""Canonical JSON, source identity, and integrity primitives.

All public artifacts use these primitives so their integrity checks are
deterministic across supported Python versions and locales.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, TypeAlias
import unicodedata

from .errors import ErrorCode, PipelineError
from .atomic_output import OutputParentLease, publish_new_artifacts
from .ownership import (
    FileOwnershipBackend,
    OwnedPath,
    OwnedPathBinding,
    OwnershipError,
    SourcePathLease,
    acquire_source_path_lease,
    binding_matches_path,
    dispose_live_owned_path,
    dispose_owned_binding,
    platform_backend,
)


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

SUPPORTED_DWG_VERSIONS: dict[str, str] = {"AC1032": "AC1032/R2018"}


class CanonicalJsonError(ValueError):
    """Raised when a value cannot be represented by the strict JSON profile."""


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalize_json_value(value: Any) -> JsonValue:
    """Normalize a strict JSON value, rejecting lossy or ambiguous inputs."""

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalJsonError("non-finite number")
        return 0.0 if value == 0 else value
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise CanonicalJsonError("object key is not a string")
            key = _nfc(raw_key)
            if key in normalized:
                raise CanonicalJsonError("duplicate normalized object key")
            normalized[key] = normalize_json_value(raw_value)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [normalize_json_value(item) for item in value]
    raise CanonicalJsonError(f"unsupported JSON value type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a value with deterministic UTF-8 JSON serialization."""

    normalized = normalize_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the lowercase SHA-256 of canonical JSON."""

    return sha256(canonical_json_bytes(value)).hexdigest()


def strict_json_loads(text: str) -> JsonValue:
    """Load JSON while rejecting duplicate keys and non-standard constants."""

    def reject_constant(value: str) -> None:
        raise CanonicalJsonError(f"non-standard JSON constant: {value}")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        raw_keys: set[str] = set()
        normalized_keys: set[str] = set()
        for raw_key, value in pairs:
            if raw_key in raw_keys:
                raise CanonicalJsonError("duplicate object key")
            raw_keys.add(raw_key)
            key = _nfc(raw_key)
            if key != raw_key:
                raise CanonicalJsonError("object key is not NFC")
            if key in normalized_keys:
                raise CanonicalJsonError("duplicate normalized object key")
            normalized_keys.add(key)
            result[key] = value
        return result

    try:
        loaded = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, CanonicalJsonError):
            raise
        raise CanonicalJsonError("invalid JSON") from error

    def require_nfc(value: Any) -> None:
        if isinstance(value, str):
            if value != _nfc(value):
                raise CanonicalJsonError("string is not NFC")
        elif isinstance(value, Mapping):
            for key, item in value.items():
                require_nfc(key)
                require_nfc(item)
        elif isinstance(value, list):
            for item in value:
                require_nfc(item)

    require_nfc(loaded)
    return normalize_json_value(loaded)


def load_json_file(path: Path) -> JsonValue:
    """Read UTF-8 strict JSON from a local artifact."""

    try:
        return strict_json_loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CanonicalJsonError("unable to read JSON artifact") from error


def integrity_payload(artifact: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Return a normalized artifact body excluding the self-integrity field."""

    payload = dict(artifact)
    payload.pop("integrity", None)
    normalized = normalize_json_value(payload)
    if not isinstance(normalized, dict):
        raise CanonicalJsonError("artifact must be an object")
    return normalized


def attach_integrity(artifact: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Return a copy with a canonical self-integrity SHA-256 field."""

    result = integrity_payload(artifact)
    result["integrity"] = {
        "algorithm": "SHA-256",
        "sha256": canonical_sha256(result),
    }
    return result


def verify_integrity(artifact: Mapping[str, Any]) -> bool:
    """Verify an artifact's self-integrity field without accepting defaults."""

    integrity = artifact.get("integrity")
    if not isinstance(integrity, Mapping):
        return False
    if integrity.get("algorithm") != "SHA-256":
        return False
    actual = integrity.get("sha256")
    if not isinstance(actual, str):
        return False
    return actual == canonical_sha256(integrity_payload(artifact))


def write_new_text(path: Path, text: str) -> None:
    """Atomically create one UTF-8 public artifact through an owned temporary."""

    write_new_artifacts(((path, text.encode("utf-8")),))


@dataclass
class CreatedFileBinding:
    """Identity/content binding for one exclusive-create artifact.

    Verification retains ``opened`` until its final output-binding check, so
    failure cleanup deletes the exact created handle rather than resolving the
    artifact pathname again. Other artifact writers close their handle after
    confirming their own no-replace publication.
    """

    path: Path
    owned_binding: OwnedPathBinding
    backend: FileOwnershipBackend
    opened: OwnedPath | None = None

    @property
    def byte_size(self) -> int:
        """Expose the exact artifact byte size for existing callers."""

        assert self.owned_binding.byte_size is not None
        return self.owned_binding.byte_size

    @property
    def sha256(self) -> str:
        """Expose the exact artifact content digest for existing callers."""

        assert self.owned_binding.sha256 is not None
        return self.owned_binding.sha256


def created_file_matches(binding: CreatedFileBinding) -> bool:
    """Return whether the artifact still names the recorded owned bytes."""

    try:
        if binding.opened is not None:
            return (
                binding.opened.capture_binding().same_identity_and_content(
                    binding.owned_binding
                )
                and binding.backend.path_matches_binding(
                    binding.path,
                    binding.owned_binding,
                )
            )
        return binding_matches_path(binding.owned_binding, binding.backend)
    except (OSError, OwnershipError):
        return False


def write_new_artifacts(
    artifacts: Sequence[tuple[Path, bytes]],
    *,
    backend: FileOwnershipBackend | None = None,
    retain_handles: bool = False,
    existing_parents: Sequence[OutputParentLease] = (),
) -> list[CreatedFileBinding]:
    """Publish one or more byte artifacts as a no-replace transaction.

    The lower-level publication helper stages every payload in a retained,
    fsynced temporary first.  It rolls back previously committed finals if a
    later commit fails, which makes paired JSON/Markdown CLI artifacts all-or-
    nothing rather than two unrelated exclusive file creates.
    """

    selected_backend = backend or platform_backend(require_windows=True)
    published = publish_new_artifacts(
        artifacts,
        backend=selected_backend,
        retain_handles=retain_handles,
        existing_parents=existing_parents,
    )
    return [
        CreatedFileBinding(
            path=artifact.path,
            owned_binding=artifact.binding,
            backend=artifact.backend,
            opened=artifact.owned if retain_handles else None,
        )
        for artifact in published
    ]


def write_new_canonical_json(
    path: Path,
    artifact: Mapping[str, Any],
    *,
    backend: FileOwnershipBackend | None = None,
    retain_handle: bool = False,
    existing_parents: Sequence[OutputParentLease] = (),
) -> CreatedFileBinding:
    """Atomically create canonical JSON once through an owned temporary."""

    payload = canonical_json_bytes(artifact) + b"\n"
    bindings = write_new_artifacts(
        ((path, payload),),
        backend=backend,
        retain_handles=retain_handle,
        existing_parents=existing_parents,
    )
    return bindings[0]


def remove_created_file(binding: CreatedFileBinding) -> None:
    """Remove only this writer's exact artifact identity."""

    if binding.opened is not None:
        opened = binding.opened
        binding.opened = None
        dispose_live_owned_path(opened, binding.owned_binding, binding.backend)
        return
    dispose_owned_binding(binding.owned_binding, binding.backend)


def close_created_file(binding: CreatedFileBinding) -> None:
    """Release a retained artifact handle after a successful final check."""

    if binding.opened is None:
        return
    opened = binding.opened
    binding.opened = None
    opened.close()


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


def format_utc(value: datetime) -> str:
    """Format a UTC timestamp in the artifact's RFC 3339 profile."""

    if value.tzinfo is None:
        raise CanonicalJsonError("timestamp must be timezone-aware")
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """Parse the strict RFC 3339 UTC timestamp used by artifacts."""

    if not value.endswith("Z"):
        raise CanonicalJsonError("timestamp is not UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CanonicalJsonError("invalid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise CanonicalJsonError("timestamp is not UTC")
    return parsed.astimezone(UTC)


def _hash_text(value: str) -> str:
    return sha256(_nfc(value).encode("utf-8")).hexdigest()


def dwg_header_signature(path: Path) -> str:
    """Read a header through a short-lived no-follow source lease."""

    return describe_source(path).dwg_header_signature


@dataclass(frozen=True)
class SourceDescription:
    """Privacy-safe source binding values for an audit artifact."""

    sha256: str
    byte_size: int
    path_fingerprint: str
    file_identity_fingerprint: str
    dwg_header_signature: str
    version_mapping: str

    def to_artifact(self) -> dict[str, JsonValue]:
        return {
            "format": "DWG",
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "path_fingerprint": self.path_fingerprint,
            "file_identity_fingerprint": self.file_identity_fingerprint,
            "dwg_header_signature": self.dwg_header_signature,
            "version_mapping": self.version_mapping,
        }


def acquire_source_lease(
    path: Path,
    *,
    backend: FileOwnershipBackend | None = None,
) -> SourcePathLease:
    """Bind a local DWG source before any metadata or byte operation.

    The retained chain is opened from the lexical drive root to the source
    parent without following reparse points.  The source itself is then held
    read-only with write/delete sharing denied.  Callers that perform a
    multi-step operation retain the returned lease until publication or
    rollback has completed.
    """

    try:
        selected_backend = backend or platform_backend(require_windows=True)
        return acquire_source_path_lease(path, selected_backend)
    except PipelineError:
        raise
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.INVALID_ARGUMENT,
            "source cannot be opened through a no-follow lexical lease",
        ) from error


def describe_leased_source(lease: SourcePathLease) -> SourceDescription:
    """Describe a DWG only from one already-retained source identity."""

    try:
        lease.require_binding()
        binding = lease.binding
        signature = lease.read_prefix(6).decode("ascii", errors="ignore")
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.SOURCE_CHANGED_DURING_RUN,
            "source lease cannot provide stable bytes",
        ) from error
    if (
        binding.is_directory
        or binding.sha256 is None
        or binding.byte_size is None
    ):
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "source is not a regular file")
    if signature not in SUPPORTED_DWG_VERSIONS:
        raise PipelineError(ErrorCode.UNSUPPORTED_VERSION, "unsupported DWG header")
    return SourceDescription(
        sha256=binding.sha256,
        byte_size=binding.byte_size,
        path_fingerprint=_hash_text(str(lease.path)),
        file_identity_fingerprint=binding.file_identity_fingerprint,
        dwg_header_signature=signature,
        version_mapping=SUPPORTED_DWG_VERSIONS[signature],
    )


def describe_source(path: Path) -> SourceDescription:
    """Create a short-lived exact binding through retained no-follow handles."""

    lease = acquire_source_lease(path)
    try:
        return describe_leased_source(lease)
    finally:
        try:
            lease.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.SOURCE_CHANGED_DURING_RUN,
                "source lease cannot be released",
            ) from error


def source_lease_matches(
    lease: SourcePathLease,
    expected: Mapping[str, Any],
) -> bool:
    """Compare audit binding fields through the still-held source bytes."""

    try:
        return describe_leased_source(lease).to_artifact() == dict(expected)
    except PipelineError:
        return False


def describe_owned_source(
    path: Path,
    opened: OwnedPath,
    backend: FileOwnershipBackend,
) -> SourceDescription:
    """Describe a DWG through a retained no-write/delete ownership handle.

    This is used for a just-published DWG and for an output held under the
    verification lease.  The bytes, size, header, and identity are read from
    the held handle, then the current pathname is required to resolve to that
    same exact object before an artifact can bind it.
    """

    try:
        final_path = opened.final_path()
        binding = opened.capture_binding()
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
            "output binding handle is unavailable",
        ) from error
    if (
        binding.is_directory
        or binding.sha256 is None
        or binding.byte_size is None
        or not backend.path_matches_binding(path, binding)
        or not backend.path_matches_binding(final_path, binding)
    ):
        raise PipelineError(
            ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
            "output binding does not name the held object",
        )
    try:
        signature = opened.read_prefix(6).decode("ascii", errors="ignore")
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
            "output header cannot be read through its held handle",
        ) from error
    if signature not in SUPPORTED_DWG_VERSIONS:
        raise PipelineError(ErrorCode.UNSUPPORTED_VERSION, "unsupported DWG header")
    return SourceDescription(
        sha256=binding.sha256,
        byte_size=binding.byte_size,
        path_fingerprint=_hash_text(str(final_path)),
        file_identity_fingerprint=binding.file_identity_fingerprint,
        dwg_header_signature=signature,
        version_mapping=SUPPORTED_DWG_VERSIONS[signature],
    )


def source_matches(path: Path, expected: Mapping[str, Any]) -> bool:
    """Return whether a source currently matches every audited binding field."""

    try:
        current = describe_source(path).to_artifact()
    except PipelineError:
        return False
    return current == dict(expected)
