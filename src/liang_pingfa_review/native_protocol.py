"""Strict, SDK-free framing for the optional local native bridge.

The bridge deliberately has a tiny read-only RPC surface.  This module keeps
the pure contract and framing implementation separate from the Windows pipe
transport in :mod:`native_bridge`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import re
import secrets
import struct
import time
from typing import Any, Final

from .canonical import (
    CanonicalJsonError,
    OpaqueJsonStringRules,
    OpaqueJsonStringError,
    canonical_json_bytes,
    strict_json_loads,
)
from .errors import ErrorCode, PipelineError


PROTOCOL_VERSION: Final = "liang-pingfa/native-bridge/v1"
PROTOCOL_MAJOR: Final = 1
PROTOCOL_MINOR: Final = 0
CHALLENGE_RESPONSE_DERIVATION_VERSION: Final = (
    "liang-pingfa/native-bridge/challenge-response/v1"
)
_SESSION_ID_PATTERN: Final = re.compile(r"^native-session-[a-f0-9]{32}$")
_NONCE_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
ALLOWED_METHODS: Final = frozenset(
    {
        "health",
        "get_session",
        "get_current_document",
        "export_inventory",
        "export_exact_geometry",
    }
)

MAX_REQUEST_BYTES: Final = 64 * 1024
MAX_CONTROL_RESPONSE_BYTES: Final = 256 * 1024
# Native Core Console write results share the control-response ceiling.  Keep
# this protocol value here rather than duplicating a private reader constant:
# manifests are admitted only when their complete canonical success envelope
# leaves the fixed safety margin below this hard cap.
MAX_NATIVE_CONSOLE_RESULT_BYTES: Final = MAX_CONTROL_RESPONSE_BYTES
MAX_NATIVE_CONSOLE_RESULT_HEADROOM_BYTES: Final = 16 * 1024
MAX_NATIVE_CONSOLE_RESULT_CANONICAL_BYTES: Final = (
    MAX_NATIVE_CONSOLE_RESULT_BYTES - MAX_NATIVE_CONSOLE_RESULT_HEADROOM_BYTES
)
# A full active-v2 result contains one fixed-width status record per requested
# operation. 1,024 records are comfortably below the canonical result budget
# while still covering the validated 623-operation release scenario.
MAX_NATIVE_OPERATION_COUNT: Final = 1_024
# Inventory is a fixed two-digest object, not a geometry surrogate.  Its
# narrow wire ceiling and the geometry envelope below are intentionally sized
# for the v1 semantic caps rather than transport convenience.
MAX_INVENTORY_RESPONSE_BYTES: Final = 256 * 1024
MAX_GEOMETRY_RESPONSE_BYTES: Final = 32 * 1024 * 1024
# Every transport request is bounded independently of the remaining frame
# length.  This prevents a short-reading peer from making the Windows pipe
# layer repeatedly allocate buffers approaching an entire 64 MiB geometry
# frame.  Keep this separate from protocol maxima: it is an I/O allocation
# bound, not a new wire-format limit.
PIPE_IO_CHUNK_BYTES: Final = 64 * 1024

# These are protocol hard maxima, never implicit operational defaults. The
# adapter configuration supplies every actual deadline and is validated before
# a client can connect or write a frame.
CONNECT_TIMEOUT_SECONDS: Final = 5.0
METHOD_TIMEOUT_SECONDS: Final = {
    "health": 3.0,
    "get_session": 3.0,
    "get_current_document": 5.0,
    "export_inventory": 30.0,
    "export_exact_geometry": 60.0,
}
METHOD_TIMEOUT_CONFIG_KEYS: Final = {
    "health": "health_ms",
    "get_session": "session_ms",
    "get_current_document": "document_ms",
    "export_inventory": "inventory_ms",
    "export_exact_geometry": "geometry_ms",
}
CONSOLE_TIMEOUT_SECONDS: Final = {
    "write_console_seconds": 120.0,
    "readback_console_seconds": 60.0,
}


class NativeProtocolError(ValueError):
    """A malformed frame, envelope, or stream state."""


class NativeOpaqueEmbeddedJsonError(NativeProtocolError):
    """A schema-authorized opaque carrier exceeded its pre-NFC boundary."""


_DeadlineCheck = Callable[[str], None]


def _check_deadline(
    deadline_check: _DeadlineCheck | None,
    stage: str,
) -> None:
    """Run an optional caller-owned absolute-deadline checkpoint."""

    if deadline_check is not None:
        deadline_check(stage)


def new_request_id() -> str:
    """Return a CSPRNG request identifier with the wire-format width."""

    return secrets.token_hex(16)


def new_nonce() -> str:
    """Return an unpadded base64url nonce containing 256 bits of entropy."""

    return secrets.token_urlsafe(32)


def derive_challenge_response(
    client_nonce: str,
    challenge: str,
    bridge_nonce: str,
    *,
    session_id: str,
    protocol_version: str = PROTOCOL_VERSION,
) -> str:
    """Return the v1 response for one exact, validated handshake transcript.

    Every field is ASCII and preceded by its unsigned big-endian byte length.
    This domain-separated, ordered encoding cannot make distinct field
    sequences ambiguous through string concatenation.  ``session_id`` is
    included because it is already a required ``get_session`` request
    parameter; this prevents a response from being replayed into a renewed
    descriptor with otherwise repeated nonces.
    """

    if protocol_version != PROTOCOL_VERSION:
        raise NativeProtocolError("unsupported challenge-response derivation")
    fields = (
        protocol_version,
        CHALLENGE_RESPONSE_DERIVATION_VERSION,
        session_id,
        client_nonce,
        challenge,
        bridge_nonce,
    )
    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise NativeProtocolError("invalid session identifier for handshake")
    if any(not _NONCE_PATTERN.fullmatch(value) for value in fields[3:]):
        raise NativeProtocolError("invalid nonce or challenge for handshake")
    encoded_fields = tuple(value.encode("ascii") for value in fields)
    transcript = b"".join(
        struct.pack(">I", len(value)) + value for value in encoded_fields
    )
    return sha256(transcript).hexdigest()


def response_limit_for_method(method: str) -> int:
    """Return the single permitted frame bound for a request method."""

    if method == "export_inventory":
        return MAX_INVENTORY_RESPONSE_BYTES
    if method == "export_exact_geometry":
        return MAX_GEOMETRY_RESPONSE_BYTES
    if method in ALLOWED_METHODS:
        return MAX_CONTROL_RESPONSE_BYTES
    raise NativeProtocolError("method is not allowlisted")


def _require_no_control_characters(value: str) -> None:
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise NativeProtocolError("control character in protocol string")


def _validate_envelope_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeProtocolError("frame root is not an object")
    result = dict(value)
    if result.get("protocol_version") != PROTOCOL_VERSION:
        raise NativeProtocolError("protocol version mismatch")
    request_id = result.get("id")
    if (
        not isinstance(request_id, str)
        or len(request_id) != 32
        or any(character not in "0123456789abcdef" for character in request_id)
    ):
        raise NativeProtocolError("invalid request identifier")
    _require_no_control_characters(request_id)
    return result


def validate_request_envelope(value: Any) -> dict[str, Any]:
    """Apply transport-level invariants before the schema-level validator."""

    result = _validate_envelope_value(value)
    method = result.get("method")
    if method not in ALLOWED_METHODS:
        raise NativeProtocolError("method is not allowlisted")
    if set(result) != {"protocol_version", "id", "method", "params"}:
        raise NativeProtocolError("request fields are not exact")
    if not isinstance(result["params"], Mapping):
        raise NativeProtocolError("request params are not an object")
    return result


def validate_response_envelope(
    value: Any,
    request_id: str,
    *,
    deadline_check: _DeadlineCheck | None = None,
) -> dict[str, Any]:
    """Require one exact response for the current in-flight request."""

    _check_deadline(deadline_check, "response envelope validation")
    result = _validate_envelope_value(value)
    _check_deadline(deadline_check, "response identifier validation")
    if result.get("id") != request_id:
        raise NativeProtocolError("response identifier mismatch")
    fields = set(result)
    if "result" in result and "error" in result:
        raise NativeProtocolError("response has result and error")
    if "result" not in result and "error" not in result:
        raise NativeProtocolError("response has neither result nor error")
    expected = (
        {"protocol_version", "id", "result"}
        if "result" in result
        else {"protocol_version", "id", "error"}
    )
    if fields != expected:
        raise NativeProtocolError("response fields are not exact")
    payload = result.get("result", result.get("error"))
    if not isinstance(payload, Mapping):
        raise NativeProtocolError("response payload is not an object")
    _check_deadline(deadline_check, "response envelope validation")
    return result


def encode_frame(
    value: Mapping[str, Any],
    *,
    maximum: int,
    opaque_string_rules: OpaqueJsonStringRules | None = None,
) -> bytes:
    """Create a single big-endian strict JSON frame.

    Response callers supply only their schema's explicit opaque-carrier
    paths.  Requests retain the default strict-NFC behavior for every scalar.
    """

    try:
        payload = canonical_json_bytes(
            value,
            opaque_string_rules=opaque_string_rules,
        )
    except (CanonicalJsonError, RecursionError) as error:
        raise NativeProtocolError("frame JSON is not canonical") from error
    if not payload or len(payload) > maximum:
        raise NativeProtocolError("frame exceeds method limit")
    return struct.pack(">I", len(payload)) + payload


def decode_payload(
    payload: bytes,
    *,
    maximum: int,
    deadline_check: _DeadlineCheck | None = None,
    opaque_string_rules: OpaqueJsonStringRules | None = None,
) -> dict[str, Any]:
    """Decode one UTF-8, duplicate-key-free JSON payload.

    ``opaque_string_rules`` is intentionally caller supplied rather than
    inferred from field names, so a nested attacker-controlled
    ``geometry_json`` or ``inventory_json`` remains strict NFC.
    """

    _check_deadline(deadline_check, "response payload validation")
    if not payload or len(payload) > maximum:
        raise NativeProtocolError("frame payload exceeds method limit")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise NativeProtocolError("UTF-8 BOM is forbidden")
    try:
        _check_deadline(deadline_check, "UTF-8 decoding")
        text = payload.decode("utf-8", errors="strict")
        _check_deadline(deadline_check, "duplicate-key JSON decoding")
        decoded = strict_json_loads(
            text,
            deadline_check=deadline_check,
            opaque_string_rules=opaque_string_rules,
        )
    except OpaqueJsonStringError as error:
        raise NativeOpaqueEmbeddedJsonError(
            "opaque embedded JSON exceeds its frame contract"
        ) from error
    except (UnicodeDecodeError, CanonicalJsonError, RecursionError) as error:
        raise NativeProtocolError("frame is not strict UTF-8 JSON") from error
    _check_deadline(deadline_check, "duplicate-key JSON decoding")
    if not isinstance(decoded, dict):
        raise NativeProtocolError("frame root is not an object")
    return decoded


def decode_frame(
    frame: bytes,
    *,
    maximum: int,
    deadline_check: _DeadlineCheck | None = None,
    opaque_string_rules: OpaqueJsonStringRules | None = None,
) -> dict[str, Any]:
    """Decode an in-memory frame; primarily used by platform-neutral test doubles."""

    if len(frame) < 4:
        raise NativeProtocolError("truncated frame header")
    expected_length = struct.unpack(">I", frame[:4])[0]
    if expected_length == 0 or expected_length > maximum:
        raise NativeProtocolError("frame header has invalid length")
    if len(frame) != 4 + expected_length:
        raise NativeProtocolError("frame contains trailing or missing bytes")
    return decode_payload(
        frame[4:],
        maximum=maximum,
        deadline_check=deadline_check,
        opaque_string_rules=opaque_string_rules,
    )


def read_exact(
    reader: Callable[[int, float], bytes],
    length: int,
    *,
    deadline: float,
) -> bytes:
    """Read exactly ``length`` bytes using deadline-aware short-read loops."""

    if length < 0:
        raise NativeProtocolError("negative exact-read length")
    # Allocate the completed payload once.  Individual transport buffers are
    # capped below, so a short-reading 64 MiB frame cannot turn into repeated
    # near-frame allocations or repeated full-payload joins.
    payload = bytearray(length)
    offset = 0
    while offset < length:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise TimeoutError("native bridge read timed out")
        remaining = length - offset
        requested = min(remaining, PIPE_IO_CHUNK_BYTES)
        chunk = reader(requested, timeout)
        if not isinstance(chunk, bytes) or not chunk:
            raise NativeProtocolError("native bridge closed during frame")
        if len(chunk) > requested:
            raise NativeProtocolError("native bridge returned excess bytes")
        payload[offset : offset + len(chunk)] = chunk
        offset += len(chunk)
    return bytes(payload)


def write_all(
    writer: Callable[[bytes, float], int],
    payload: bytes,
    *,
    deadline: float,
) -> None:
    """Write all bytes using deadline-aware short-write loops."""

    offset = 0
    while offset < len(payload):
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise TimeoutError("native bridge write timed out")
        chunk_end = min(len(payload), offset + PIPE_IO_CHUNK_BYTES)
        chunk = payload[offset:chunk_end]
        written = writer(chunk, timeout)
        if not isinstance(written, int) or written <= 0:
            raise NativeProtocolError("native bridge short write")
        if written > len(chunk):
            raise NativeProtocolError("native bridge over-reported write")
        offset += written


def read_frame(
    reader: Callable[[int, float], bytes],
    *,
    maximum: int,
    deadline: float,
    deadline_check: _DeadlineCheck | None = None,
    opaque_string_rules: OpaqueJsonStringRules | None = None,
) -> dict[str, Any]:
    """Read one framed payload with no implicit retries or buffering."""

    _check_deadline(deadline_check, "response frame header")
    header = read_exact(reader, 4, deadline=deadline)
    length = struct.unpack(">I", header)[0]
    if length == 0 or length > maximum:
        raise NativeProtocolError("frame length is outside method bound")
    _check_deadline(deadline_check, "response frame body")
    return decode_payload(
        read_exact(reader, length, deadline=deadline),
        maximum=maximum,
        deadline_check=deadline_check,
        opaque_string_rules=opaque_string_rules,
    )


@dataclass(frozen=True)
class RpcRequest:
    """A fully bound request without a write-capable escape hatch."""

    request_id: str
    method: str
    params: dict[str, Any]

    def envelope(self) -> dict[str, Any]:
        envelope = {
            "protocol_version": PROTOCOL_VERSION,
            "id": self.request_id,
            "method": self.method,
            "params": self.params,
        }
        return validate_request_envelope(envelope)


def protocol_error(error: BaseException) -> PipelineError:
    """Map transport details to the only public error category."""

    if isinstance(error, TimeoutError):
        return PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "native RPC timeout")
    # RecursionError is intentionally indistinguishable from every other
    # malformed transport payload. The caller invalidates the session first.
    return PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "native RPC rejected")
