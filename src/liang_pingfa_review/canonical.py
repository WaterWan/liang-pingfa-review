"""Canonical JSON, source identity, and integrity primitives.

All public artifacts use these primitives so their integrity checks are
deterministic across supported Python versions and locales.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Final, TypeAlias, cast
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
_DeadlineCheck: TypeAlias = Callable[[str], None]

SUPPORTED_DWG_VERSIONS: dict[str, str] = {"AC1032": "AC1032/R2018"}
# This is deliberately one project-wide policy rather than a parser-specific
# default. It bounds every artifact, configuration, session, and RPC payload
# before any recursive parser, normalizer, serializer, or schema validator
# receives it. Packaged schemas currently remain far below this 128-level cap.
MAX_JSON_NESTING_DEPTH: Final[int] = 128
# RPC callers supply an absolute-deadline callback.  These fixed intervals
# keep every iterative native-response pass interruptible without making
# ordinary artifact loads pay callback overhead.
_CANONICAL_NODE_CHECK_INTERVAL: Final[int] = 64
_CANONICAL_TEXT_CHECK_INTERVAL: Final[int] = 16 * 1024
_CANONICAL_NFC_MINIMUM_CHECK_CHARACTERS: Final[int] = 4096
_CANONICAL_SERIALIZATION_CHECK_INTERVAL: Final[int] = 16 * 1024
_CANONICAL_HASH_CHECK_INTERVAL: Final[int] = 64 * 1024


class CanonicalJsonError(ValueError):
    """Raised when a value cannot be represented by the strict JSON profile."""


class DeadlineCheckpointSampler:
    """Sample one absolute-deadline callback at a fixed unit interval.

    Traversals call :meth:`visit` for each bounded node and call
    :meth:`advance` for text that has already been split into bounded chunks.
    The sampler never creates or changes a deadline; it only avoids calling
    the caller-owned expensive monotonic-clock callback for every tiny unit.
    Callers retain mandatory entry and exit checks around major stages.
    """

    def __init__(
        self,
        deadline_check: _DeadlineCheck | None,
        *,
        interval: int,
        checkpoint: Callable[[_DeadlineCheck | None, str], None] | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("deadline checkpoint interval must be positive")
        self._deadline_check = deadline_check
        self._checkpoint = _check_deadline if checkpoint is None else checkpoint
        self._interval = interval
        self._units_until_check = interval

    def visit(self, stage: str, *, force: bool = False) -> None:
        """Record one visited node and checkpoint at the fixed interval."""

        if force or self._units_until_check == self._interval:
            self._checkpoint(self._deadline_check, stage)
        self._units_until_check -= 1
        if self._units_until_check == 0:
            self._units_until_check = self._interval

    def advance(self, stage: str, units: int) -> None:
        """Record already-bounded text work, checking every ``interval`` units."""

        if units < 0:
            raise ValueError("deadline checkpoint units must not be negative")
        while units >= self._units_until_check:
            units -= self._units_until_check
            self._checkpoint(self._deadline_check, stage)
            self._units_until_check = self._interval
        self._units_until_check -= units


def _check_deadline(
    deadline_check: _DeadlineCheck | None,
    stage: str,
) -> None:
    """Run an optional caller-owned absolute-deadline checkpoint."""

    if deadline_check is not None:
        deadline_check(stage)


def _nfc(
    value: str,
    *,
    deadline_check: _DeadlineCheck | None = None,
    stage: str = "JSON NFC normalization",
) -> str:
    """Normalize one string after bounded progress probes.

    ``unicodedata.normalize`` is the authority for Unicode normalization.  A
    cheap bounded scan before it makes large RPC strings observable to the
    request timer while retaining byte-for-byte behavior for non-RPC callers.
    Native geometry's individual text fields are schema-bounded to 4096
    characters; the scan also covers outer embedded JSON strings.
    """

    # Short fields are covered by the enclosing iterative node traversal.
    # A longer scalar receives mandatory entry/exit checks; its maximum
    # uninterrupted Unicode normalization work remains below the 16 KiB text
    # interval, without probing every schema-bounded string.
    if (
        deadline_check is not None
        and len(value) > _CANONICAL_NFC_MINIMUM_CHECK_CHARACTERS
    ):
        _check_deadline(deadline_check, stage)
        _check_deadline(deadline_check, stage)
    return unicodedata.normalize("NFC", value)


def _is_json_sequence(value: Any) -> bool:
    """Return whether a value is a JSON-array candidate, excluding strings."""

    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def validate_json_nesting(
    value: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    """Iteratively reject over-deep or cyclic Python JSON-like values.

    This defense-in-depth guard intentionally examines mappings, lists, and
    tuples before recursive consumers such as JSON Schema can process them.
    Repeated references are allowed, but a reference back into the active
    container path is a recursive Python object and cannot be canonical JSON.
    """

    active_containers: set[int] = set()
    stack: list[tuple[bool, Any, int]] = [(False, value, 0)]
    nodes = 0
    try:
        while stack:
            if (
                nodes
                and nodes % _CANONICAL_NODE_CHECK_INTERVAL == 0
            ):
                _check_deadline(
                    deadline_check,
                    "JSON object construction post-walk",
                )
            nodes += 1
            leaving, current, depth = stack.pop()
            if leaving:
                active_containers.remove(cast(int, current))
                continue
            if not isinstance(current, Mapping) and not _is_json_sequence(current):
                continue

            next_depth = depth + 1
            if next_depth > MAX_JSON_NESTING_DEPTH:
                raise CanonicalJsonError("JSON nesting exceeds the fixed limit")
            identity = id(current)
            if identity in active_containers:
                raise CanonicalJsonError("recursive JSON value")
            active_containers.add(identity)
            stack.append((True, identity, next_depth))

            if isinstance(current, Mapping):
                for key, item in current.items():
                    # Keys are normally strings, but inspect both sides of a
                    # hostile Mapping before normalization rejects bad keys.
                    stack.append((False, item, next_depth))
                    stack.append((False, key, next_depth))
            else:
                for item in current:
                    stack.append((False, item, next_depth))
    except RecursionError as error:
        raise CanonicalJsonError("JSON value recursion is unsupported") from error


def _validate_json_text_nesting(
    text: str,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    """Bound JSON delimiter nesting without invoking a recursive decoder.

    The scan is intentionally syntax-light: ``json.loads`` remains the
    authority for malformed input. It only recognizes JSON strings and their
    escapes so brackets or braces inside quoted text never affect the cap.
    """

    if not isinstance(text, str):
        raise CanonicalJsonError("JSON text is not a string")
    depth = 0
    in_string = False
    escaped = False
    for offset, character in enumerate(text):
        if offset % _CANONICAL_TEXT_CHECK_INTERVAL == 0:
            _check_deadline(deadline_check, "JSON text nesting scan")
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            depth += 1
            if depth > MAX_JSON_NESTING_DEPTH:
                raise CanonicalJsonError("JSON nesting exceeds the fixed limit")
        elif character in "}]":
            # A negative depth is malformed JSON, which the strict parser
            # below reports without trying to repair it.
            depth -= 1
    _check_deadline(deadline_check, "JSON text nesting scan")


def validate_json_canonical_form(
    value: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> None:
    """Reject non-NFC, duplicate-key, and non-finite JSON values iteratively.

    This is deliberately separate from normalization so strict RPC decoding
    can prove canonical input before schema validation.  It is also shared by
    native contract validation, avoiding a second divergent traversal for
    Unicode, finite-number, and normalized-key checks.
    """

    validate_json_nesting(value, deadline_check=deadline_check)
    stack = [value]
    nodes = 0
    finite_numbers = 0
    try:
        while stack:
            if (
                nodes
                and nodes % _CANONICAL_NODE_CHECK_INTERVAL == 0
            ):
                _check_deadline(deadline_check, "JSON canonical post-walk")
            nodes += 1
            current = stack.pop()
            if isinstance(current, str):
                if current != _nfc(
                    current,
                    deadline_check=deadline_check,
                    stage="JSON NFC validation",
                ):
                    raise CanonicalJsonError("string is not NFC")
            elif isinstance(current, Mapping):
                normalized: set[str] = set()
                for index, (key, item) in enumerate(current.items()):
                    if (
                        index
                        and index % _CANONICAL_NODE_CHECK_INTERVAL == 0
                    ):
                        _check_deadline(
                            deadline_check,
                            "JSON duplicate-key validation",
                        )
                    if not isinstance(key, str):
                        raise CanonicalJsonError("object key is not a string")
                    normalized_key = _nfc(
                        key,
                        deadline_check=deadline_check,
                        stage="JSON NFC validation",
                    )
                    if normalized_key != key:
                        raise CanonicalJsonError("object key is not NFC")
                    if normalized_key in normalized:
                        raise CanonicalJsonError(
                            "duplicate normalized object key"
                        )
                    normalized.add(normalized_key)
                    stack.append(item)
            elif _is_json_sequence(current):
                for index, item in enumerate(current):
                    if (
                        index
                        and index % _CANONICAL_NODE_CHECK_INTERVAL == 0
                    ):
                        _check_deadline(
                            deadline_check,
                            "JSON canonical post-walk",
                        )
                    stack.append(item)
            elif isinstance(current, float):
                if (
                    finite_numbers
                    and finite_numbers % _CANONICAL_NODE_CHECK_INTERVAL == 0
                ):
                    _check_deadline(
                        deadline_check,
                        "JSON finite-number validation",
                    )
                finite_numbers += 1
                if not math.isfinite(current):
                    raise CanonicalJsonError("non-finite number")
    except RecursionError as error:
        raise CanonicalJsonError("JSON value recursion is unsupported") from error


def normalize_json_value(
    value: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
    _nesting_already_checked: bool = False,
) -> JsonValue:
    """Normalize a strict JSON value, rejecting lossy or ambiguous inputs."""

    if not _nesting_already_checked:
        validate_json_nesting(value, deadline_check=deadline_check)
    pending = object()
    result: list[Any] = [pending]
    # Entries carry a source value and its destination container/slot. The
    # explicit stack avoids a second recursive path after depth validation.
    stack: list[tuple[Any, dict[str, Any] | list[Any] | None, str | int | None]] = [
        (value, None, None)
    ]
    nodes = 0
    finite_numbers = 0

    def assign(
        destination: dict[str, Any] | list[Any] | None,
        slot: str | int | None,
        normalized: JsonValue,
    ) -> None:
        if destination is None:
            result[0] = normalized
        else:
            assert slot is not None
            destination[slot] = normalized

    try:
        while stack:
            if (
                nodes
                and nodes % _CANONICAL_NODE_CHECK_INTERVAL == 0
            ):
                _check_deadline(deadline_check, "JSON normalization")
            nodes += 1
            current, destination, slot = stack.pop()
            if current is None or isinstance(current, bool):
                assign(destination, slot, current)
            elif isinstance(current, int):
                assign(destination, slot, current)
            elif isinstance(current, float):
                if (
                    finite_numbers
                    and finite_numbers % _CANONICAL_NODE_CHECK_INTERVAL == 0
                ):
                    _check_deadline(
                        deadline_check,
                        "JSON finite-number validation",
                    )
                finite_numbers += 1
                if not math.isfinite(current):
                    raise CanonicalJsonError("non-finite number")
                assign(destination, slot, 0.0 if current == 0 else current)
            elif isinstance(current, str):
                assign(
                    destination,
                    slot,
                    _nfc(
                        current,
                        deadline_check=deadline_check,
                        stage="JSON NFC normalization",
                    ),
                )
            elif isinstance(current, Mapping):
                normalized_mapping: dict[str, Any] = {}
                assign(destination, slot, normalized_mapping)
                entries: list[tuple[str, Any]] = []
                for index, (raw_key, raw_value) in enumerate(current.items()):
                    if (
                        index
                        and index % _CANONICAL_NODE_CHECK_INTERVAL == 0
                    ):
                        _check_deadline(
                            deadline_check,
                            "JSON duplicate-key normalization",
                        )
                    if not isinstance(raw_key, str):
                        raise CanonicalJsonError("object key is not a string")
                    key = _nfc(
                        raw_key,
                        deadline_check=deadline_check,
                        stage="JSON NFC normalization",
                    )
                    if key in normalized_mapping:
                        raise CanonicalJsonError("duplicate normalized object key")
                    # Reserve the key before traversing its value so NFC
                    # collisions cannot be obscured by traversal order.
                    normalized_mapping[key] = pending
                    entries.append((key, raw_value))
                for key, raw_value in reversed(entries):
                    stack.append((raw_value, normalized_mapping, key))
            elif _is_json_sequence(current):
                # Decoded JSON arrays are lists.  Do not duplicate an
                # attacker-controlled large list merely to reverse it.
                items = current if isinstance(current, list) else list(current)
                normalized_sequence: list[Any] = [pending] * len(items)
                assign(destination, slot, normalized_sequence)
                for index in range(len(items) - 1, -1, -1):
                    if (
                        index
                        and index % _CANONICAL_NODE_CHECK_INTERVAL == 0
                    ):
                        _check_deadline(
                            deadline_check,
                            "JSON sequence normalization",
                        )
                    stack.append((items[index], normalized_sequence, index))
            else:
                raise CanonicalJsonError(
                    f"unsupported JSON value type: {type(current).__name__}"
                )
    except RecursionError as error:
        raise CanonicalJsonError("JSON value recursion is unsupported") from error
    if result[0] is pending:
        raise CanonicalJsonError("JSON value normalization failed")
    return cast(JsonValue, result[0])


def canonical_json_bytes(
    value: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> bytes:
    """Encode a value with deterministic UTF-8 JSON serialization."""

    try:
        normalized = normalize_json_value(value, deadline_check=deadline_check)
        if deadline_check is None:
            return json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        encoded: list[str] = []
        sampler = DeadlineCheckpointSampler(
            deadline_check,
            interval=_CANONICAL_SERIALIZATION_CHECK_INTERVAL,
        )
        for chunk in encoder.iterencode(normalized):
            encoded.append(chunk)
            sampler.advance("JSON canonical serialization", len(chunk))
        return "".join(encoded).encode("utf-8")
    except RecursionError as error:
        raise CanonicalJsonError("JSON serialization recursion is unsupported") from error


def canonical_sha256(
    value: Any,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> str:
    """Return the lowercase SHA-256 of canonical JSON."""

    payload = canonical_json_bytes(value, deadline_check=deadline_check)
    if deadline_check is None:
        return sha256(payload).hexdigest()
    digest = sha256()
    for offset in range(0, len(payload), _CANONICAL_HASH_CHECK_INTERVAL):
        if offset:
            _check_deadline(deadline_check, "JSON canonical hashing")
        digest.update(payload[offset : offset + _CANONICAL_HASH_CHECK_INTERVAL])
    return digest.hexdigest()


def load_bounded_json(
    text: str,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> Any:
    """Decode JSON through the shared depth and duplicate-key boundary.

    This lower-level parser deliberately does not normalize Unicode. A narrow
    profile loader may have its own documented normalization semantics, but it
    still receives the same iterative pre-parse and post-parse nesting cap.
    Canonical artifacts should use :func:`strict_json_loads`.
    """

    def reject_constant(value: str) -> None:
        raise CanonicalJsonError(f"non-standard JSON constant: {value}")

    pair_count = 0

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal pair_count
        result: dict[str, Any] = {}
        for raw_key, value in pairs:
            if (
                pair_count
                and pair_count % _CANONICAL_NODE_CHECK_INTERVAL == 0
            ):
                _check_deadline(deadline_check, "JSON duplicate-key validation")
            pair_count += 1
            if raw_key in result:
                raise CanonicalJsonError("duplicate object key")
            result[raw_key] = value
        return result

    try:
        _validate_json_text_nesting(text, deadline_check=deadline_check)
        _check_deadline(deadline_check, "JSON object construction")
        loaded = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        if isinstance(error, CanonicalJsonError):
            raise
        raise CanonicalJsonError("invalid JSON") from error
    try:
        validate_json_nesting(loaded, deadline_check=deadline_check)
    except RecursionError as error:
        raise CanonicalJsonError("JSON validation recursion is unsupported") from error
    return loaded


def strict_json_loads(
    text: str,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> JsonValue:
    """Load canonical JSON while rejecting duplicate keys and non-NFC strings."""

    try:
        loaded = load_bounded_json(text, deadline_check=deadline_check)
        validate_json_canonical_form(loaded, deadline_check=deadline_check)
        return normalize_json_value(
            loaded,
            deadline_check=deadline_check,
            _nesting_already_checked=True,
        )
    except RecursionError as error:
        raise CanonicalJsonError("JSON validation recursion is unsupported") from error


def load_json_file(path: Path) -> JsonValue:
    """Read UTF-8 strict JSON from a local artifact."""

    try:
        return strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise CanonicalJsonError("unable to read JSON artifact") from error


def integrity_payload(
    artifact: Mapping[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> dict[str, JsonValue]:
    """Return a normalized artifact body excluding the self-integrity field."""

    payload = dict(artifact)
    payload.pop("integrity", None)
    normalized = normalize_json_value(payload, deadline_check=deadline_check)
    if not isinstance(normalized, dict):
        raise CanonicalJsonError("artifact must be an object")
    return normalized


def attach_integrity(
    artifact: Mapping[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> dict[str, JsonValue]:
    """Return a copy with a canonical self-integrity SHA-256 field."""

    result = integrity_payload(artifact, deadline_check=deadline_check)
    result["integrity"] = {
        "algorithm": "SHA-256",
        "sha256": canonical_sha256(result, deadline_check=deadline_check),
    }
    return result


def verify_integrity(
    artifact: Mapping[str, Any],
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> bool:
    """Verify an artifact's self-integrity field without accepting defaults."""

    integrity = artifact.get("integrity")
    if not isinstance(integrity, Mapping):
        return False
    if integrity.get("algorithm") != "SHA-256":
        return False
    actual = integrity.get("sha256")
    if not isinstance(actual, str):
        return False
    return actual == canonical_sha256(
        integrity_payload(artifact, deadline_check=deadline_check),
        deadline_check=deadline_check,
    )


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
    """Return whether the artifact still names the recorded owned bytes.

    A retained artifact may have been privately staged with zero sharing, so
    its exact handle is the only safe authority before final release.
    """

    try:
        if binding.opened is not None:
            expected_path = os.path.normcase(os.path.normpath(os.fspath(binding.path)))
            handle_paths = {
                os.path.normcase(
                    os.path.normpath(os.fspath(binding.opened.final_path()))
                ),
                os.path.normcase(
                    os.path.normpath(os.fspath(binding.opened.path))
                ),
            }
            return (
                binding.opened.capture_binding().same_identity_and_content(
                    binding.owned_binding
                )
                and expected_path in handle_paths
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
    the held handle.  An exclusive private-publication handle deliberately
    denies pathname reopen, so its retained backend spelling is accepted as
    the no-reopen alias fallback for a final handle path.
    """

    try:
        final_path = opened.final_path()
        binding = opened.capture_binding()
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.OUTPUT_CHANGED_DURING_VERIFY,
            "output binding handle is unavailable",
        ) from error
    expected_path = os.path.normcase(os.path.normpath(os.fspath(path)))
    handle_paths = {
        os.path.normcase(os.path.normpath(os.fspath(final_path))),
        os.path.normcase(os.path.normpath(os.fspath(opened.path))),
    }
    if (
        binding.is_directory
        or binding.sha256 is None
        or binding.byte_size is None
        or expected_path not in handle_paths
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
