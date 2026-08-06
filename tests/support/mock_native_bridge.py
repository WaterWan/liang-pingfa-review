"""Generated test-only bridge doubles; never packaged with the application."""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from ctypes import wintypes
from hashlib import sha256
import os
import struct
import sys
import time
from typing import Any

from liang_pingfa_review.native_protocol import (
    PIPE_IO_CHUNK_BYTES,
    PROTOCOL_VERSION,
    decode_frame,
    derive_challenge_response,
    encode_frame,
    response_limit_for_method,
)
from liang_pingfa_review.native_contracts import opaque_embedded_json_rules


class ScriptedPipe:
    """An in-memory short-read/short-write transport for protocol tests."""

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        server_pid: int = 1234,
        chunk_size: int = 3,
        extra_frame: bool = False,
    ) -> None:
        self._handler = handler
        self._server_pid = server_pid
        self._chunk_size = chunk_size
        self._written = bytearray()
        self._read = bytearray()
        self._extra_frame = extra_frame
        self.closed = False

    @property
    def server_pid(self) -> int:
        return self._server_pid

    def write(self, payload: bytes, _timeout: float) -> int:
        amount = min(len(payload), self._chunk_size)
        self._written.extend(payload[:amount])
        if len(self._written) >= 4:
            expected = struct.unpack(">I", self._written[:4])[0]
            if len(self._written) == expected + 4:
                request = decode_frame(bytes(self._written), maximum=64 * 1024)
                response = self._handler(request)
                self._read.extend(
                    encode_frame(
                        response,
                        maximum=response_limit_for_method(request["method"]),
                        opaque_string_rules=opaque_embedded_json_rules("response"),
                    )
                )
                if self._extra_frame:
                    self._read.extend(
                        encode_frame(
                            response,
                            maximum=response_limit_for_method(request["method"]),
                            opaque_string_rules=opaque_embedded_json_rules(
                                "response"
                            ),
                        )
                    )
        return amount

    def read(self, maximum: int, _timeout: float) -> bytes:
        amount = min(maximum, self._chunk_size, len(self._read))
        result = bytes(self._read[:amount])
        del self._read[:amount]
        return result

    def pending_bytes(self) -> int:
        return len(self._read)

    def close(self) -> None:
        self.closed = True


def health_response(request: dict[str, Any]) -> dict[str, Any]:
    """Return a schema-valid generated health response."""

    return {
        "protocol_version": PROTOCOL_VERSION,
        "id": request["id"],
        "result": {
            "kind": "health",
            "protocol_major": 1,
            "protocol_minor": 0,
            "adapter": {
                "id": "test-adapter",
                "profile": "test-profile",
                "version": "1.0.0",
            },
            "plugin": {
                "id": "test-plugin",
                "version": "1.0.0",
                "fingerprint": "a" * 64,
            },
            "host": {
                "product": "external-host",
                "release": "1.0",
                "runtime": "test-runtime",
                "mode": "full_host",
            },
            "capabilities": ["read.inventory/v1", "read.exact_geometry/v1"],
        },
    }


def _handshake_document() -> dict[str, Any]:
    """Return a generated saved-document identity matching synthetic config."""

    digest = lambda seed: sha256(seed.encode("utf-8")).hexdigest()
    return {
        "saved": True,
        "path_fingerprint": digest("path"),
        "file_identity_fingerprint": digest("identity"),
        "sha256": digest("source"),
        "byte_size": 128,
        "dwg_header_signature": "AC1032",
        "database_instance_fingerprint": digest("database"),
        "revision_fingerprint": digest("revision"),
    }


def _handshake_identity() -> dict[str, Any]:
    return {
        "adapter": {
            "id": "test-adapter",
            "profile": "test-profile",
            "version": "1.0.0",
        },
        "plugin": {
            "id": "test-plugin",
            "version": "1.0.0",
            "fingerprint": sha256(b"readback-plugin").hexdigest(),
        },
        "host": {
            "product": "external-host",
            "release": "1.0",
            "runtime": "test-runtime",
            "mode": "full_host",
        },
        "capabilities": ["read.inventory/v1", "read.exact_geometry/v1"],
    }


class _GeneratedAdapterSessionGate:
    """Model the adapter's client-owned session binding state machine.

    The bootstrap advertisement intentionally has no session identifier.
    A generated Python client proposes one in its first ``health`` request;
    this server binds that exact value and rejects a later mismatch rather
    than independently minting a competing identifier.
    """

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._descriptor_issued = False

    def require_request(self, request: dict[str, Any]) -> None:
        method = request["method"]
        proposed = request["params"]["session_id"]
        if self._session_id is None:
            if method != "health":
                raise ValueError("adapter bridge requires health before binding")
            self._session_id = proposed
            return
        if proposed != self._session_id:
            raise ValueError("adapter bridge session identifier changed")
        if method == "get_session":
            if self._descriptor_issued:
                raise ValueError("adapter bridge handshake was duplicated")
            self._descriptor_issued = True
            return
        if method != "health" and not self._descriptor_issued:
            raise ValueError("adapter bridge handshake is incomplete")


def _handshake_response(
    request: dict[str, Any],
    *,
    session_gate: _GeneratedAdapterSessionGate | None = None,
) -> dict[str, Any]:
    """Generate only the fixed read-only adapter bridge methods for tests."""

    identity = _handshake_identity()
    method = request["method"]
    if session_gate is not None:
        session_gate.require_request(request)
    if method == "health":
        result: dict[str, Any] = {
            "kind": "health",
            "protocol_major": 1,
            "protocol_minor": 0,
            **identity,
        }
    elif method == "get_session":
        parameters = request["params"]
        bridge_nonce = "c" * 43
        result = {
            "kind": "session",
            "bridge_nonce": bridge_nonce,
            "challenge_response": derive_challenge_response(
                parameters["client_nonce"],
                parameters["challenge"],
                bridge_nonce,
                session_id=parameters["session_id"],
            ),
            **identity,
            "current_document": _handshake_document(),
        }
    elif method == "get_current_document":
        result = {
            "kind": "document",
            "current_document": _handshake_document(),
        }
    else:
        raise ValueError("generated handshake server received an unallowlisted method")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "id": request["id"],
        "result": result,
    }


def _read_exact(handle: int, size: int) -> bytes:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    output = bytearray(size)
    offset = 0
    while offset < size:
        requested = min(size - offset, PIPE_IO_CHUNK_BYTES)
        buffer = ctypes.create_string_buffer(requested)
        received = wintypes.DWORD()
        if not kernel32.ReadFile(
            wintypes.HANDLE(handle),
            buffer,
            requested,
            ctypes.byref(received),
            None,
        ):
            raise OSError("mock pipe read failed")
        if received.value <= 0 or received.value > requested:
            raise OSError("mock pipe short read failed")
        output[offset : offset + received.value] = buffer.raw[: received.value]
        offset += received.value
    return bytes(output)


def _write_all(handle: int, payload: bytes) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + PIPE_IO_CHUNK_BYTES]
        buffer = ctypes.create_string_buffer(chunk, len(chunk))
        written = wintypes.DWORD()
        if not kernel32.WriteFile(
            wintypes.HANDLE(handle),
            buffer,
            len(chunk),
            ctypes.byref(written),
            None,
        ):
            raise OSError("mock pipe write failed")
        if written.value <= 0 or written.value > len(chunk):
            raise OSError("mock pipe short write failed")
        offset += written.value


def _create_named_pipe(
    pipe_name: str,
    *,
    input_buffer_size: int = 65536,
    output_buffer_size: int = 65536,
) -> tuple[Any, int]:
    """Create one generated byte-mode local server without any CAD dependency."""

    if os.name != "nt":
        raise RuntimeError("real named pipe mock is Windows-only")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateNamedPipeW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    create.restype = wintypes.HANDLE
    handle = int(
        create(
            pipe_name,
            0x00000003,
            0x00000000,
            1,
            output_buffer_size,
            input_buffer_size,
            0,
            None,
        )
        or 0
    )
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError("mock pipe create failed")
    return kernel32, handle


def _connect_server(kernel32: Any, handle: int) -> None:
    connected = kernel32.ConnectNamedPipe(wintypes.HANDLE(handle), None)
    if not connected and ctypes.get_last_error() != 535:
        raise OSError("mock pipe connect failed")


def _close_server(kernel32: Any, handle: int) -> None:
    try:
        kernel32.DisconnectNamedPipe(wintypes.HANDLE(handle))
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def serve_once(pipe_name: str) -> None:
    """Serve one real Windows named-pipe health request in a generated process."""

    kernel32, handle = _create_named_pipe(pipe_name)
    try:
        print("READY", flush=True)
        _connect_server(kernel32, handle)
        header = _read_exact(handle, 4)
        length = struct.unpack(">I", header)[0]
        request = decode_frame(header + _read_exact(handle, length), maximum=64 * 1024)
        _write_all(handle, encode_frame(health_response(request), maximum=256 * 1024))
        # Keep the generated server connected until the client receives every
        # byte; DisconnectNamedPipe otherwise discards unread output.
        if not kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
            raise OSError("mock pipe flush failed")
    finally:
        _close_server(kernel32, handle)


def _write_chunks(
    handle: int,
    payload: bytes,
    *,
    chunk_size: int,
    delay_seconds: float,
) -> None:
    for offset in range(0, len(payload), chunk_size):
        _write_all(handle, payload[offset : offset + chunk_size])
        if delay_seconds:
            time.sleep(delay_seconds)


def serve_transport_scenario(
    pipe_name: str,
    scenario: str,
    *,
    payload_size: int = 0,
) -> None:
    """Exercise production transport cancellation with generated local pipes."""

    tiny = scenario in {"no-read", "slow-read", "partial-read"}
    kernel32, handle = _create_named_pipe(
        pipe_name,
        input_buffer_size=1 if tiny else 65536,
        output_buffer_size=1 if tiny else 65536,
    )
    try:
        print("READY", flush=True)
        _connect_server(kernel32, handle)
        if scenario == "no-read":
            # The client must cancel an overlapped WriteFile rather than wait
            # for this intentionally non-reading generated peer.
            time.sleep(1.5)
            return
        if scenario == "broken":
            _read_exact(handle, 1)
            return
        if scenario == "slow-read":
            remaining = payload_size
            while remaining:
                amount = min(64, remaining)
                _read_exact(handle, amount)
                remaining -= amount
                time.sleep(0.001)
            _write_all(handle, b"OK")
            return
        if scenario == "partial-read":
            _write_chunks(
                handle,
                b"abcdef",
                chunk_size=1,
                delay_seconds=0.01,
            )
            return
        if scenario == "delayed-response":
            time.sleep(0.3)
            try:
                _write_all(handle, b"D")
            except OSError:
                pass
            return
        if scenario == "delayed-health":
            header = _read_exact(handle, 4)
            length = struct.unpack(">I", header)[0]
            request = decode_frame(
                header + _read_exact(handle, length),
                maximum=64 * 1024,
            )
            # Deliberately outlive a short client session. A prompt client
            # cancellation leaves this generated server with no proprietary
            # host dependency and no leaked named-pipe handle.
            time.sleep(1.2)
            try:
                _write_all(
                    handle,
                    encode_frame(health_response(request), maximum=256 * 1024),
                )
            except OSError:
                pass
            return
        if scenario == "cancellation-race":
            time.sleep(0.06)
            try:
                _write_all(handle, b"R")
            except OSError:
                # A timely client cancellation is an expected race outcome.
                pass
            return
        raise ValueError("unknown generated pipe scenario")
    finally:
        _close_server(kernel32, handle)


def _serve_handshake_connection(
    pipe_name: str,
    methods: tuple[str, ...],
    *,
    session_gate: _GeneratedAdapterSessionGate,
    announce_ready: bool = False,
) -> None:
    """Serve one generated connection with an exact method sequence."""

    kernel32, handle = _create_named_pipe(pipe_name)
    try:
        if announce_ready:
            print("READY", flush=True)
        _connect_server(kernel32, handle)
        for method in methods:
            header = _read_exact(handle, 4)
            length = struct.unpack(">I", header)[0]
            request = decode_frame(
                header + _read_exact(handle, length),
                maximum=64 * 1024,
            )
            if request["method"] != method:
                raise ValueError("generated handshake method order differs")
            response = _handshake_response(request, session_gate=session_gate)
            _write_all(
                handle,
                encode_frame(
                    response,
                    maximum=response_limit_for_method(method),
                ),
            )
            if not kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
                raise OSError("mock handshake pipe flush failed")
    finally:
        _close_server(kernel32, handle)


def serve_handshake_sequence(pipe_name: str) -> None:
    """Serve adapter-semantic preparation then one consumed descriptor."""

    session_gate = _GeneratedAdapterSessionGate()
    _serve_handshake_connection(
        pipe_name,
        ("health", "get_session"),
        session_gate=session_gate,
        announce_ready=True,
    )
    _serve_handshake_connection(
        pipe_name,
        ("health", "get_current_document"),
        session_gate=session_gate,
    )
    # Keep the actual server process alive through the client's post-response
    # PID-instance recheck; production bridges remain resident after a
    # response rather than exiting at the frame boundary.
    time.sleep(0.5)


if __name__ == "__main__":
    if len(sys.argv) == 2:
        serve_once(sys.argv[1])
    elif len(sys.argv) == 3 and sys.argv[2] == "handshake-sequence":
        serve_handshake_sequence(sys.argv[1])
    elif len(sys.argv) in {3, 4}:
        serve_transport_scenario(
            sys.argv[1],
            sys.argv[2],
            payload_size=int(sys.argv[3]) if len(sys.argv) == 4 else 0,
        )
    else:
        raise SystemExit("usage: mock_native_bridge PIPE [SCENARIO [PAYLOAD_SIZE]]")
