"""Portable strict-frame tests plus Windows local named-pipe proof."""

from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import liang_pingfa_review.native_bridge as native_bridge_module
import liang_pingfa_review.canonical as canonical_module
import liang_pingfa_review.native_contracts as native_contracts_module
import liang_pingfa_review.native_protocol as native_protocol_module
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.native_contracts import validate_native_contract
from liang_pingfa_review.native_bridge import (
    ComponentDacl,
    ComponentDaclAce,
    NativeInstallationLeases,
    NativeBridgeClient,
    NativeBridgeHandshakeClient,
    NativeBridgeHandshakeContext,
    NativeSessionClockReading,
    NativePipeClosed,
    ProcessIdentity,
    WindowsNamedPipe,
    _read_component_dacl,
    consume_native_bridge_bootstrap,
    consume_native_session,
    prepare_native_session,
    validate_component_dacl,
    validate_pipe_name,
    write_private_native_session_descriptor,
)
from liang_pingfa_review.ownership import (
    FileIdentity,
    OwnedPathBinding,
    OwnershipCleanupError,
    OwnershipLostError,
    acquire_lexical_directory_chain,
    current_user_sid,
    platform_backend,
)
from liang_pingfa_review.canonical import (
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
    format_utc,
    strict_json_loads,
)
from liang_pingfa_review.native_protocol import (
    NativeProtocolError,
    PIPE_IO_CHUNK_BYTES,
    PROTOCOL_VERSION,
    decode_frame,
    derive_challenge_response,
    encode_frame,
    read_exact,
    read_frame,
    response_limit_for_method,
    write_all,
)
from tests.support.mock_native_bridge import (
    ScriptedPipe,
    _GeneratedAdapterSessionGate,
    _handshake_response,
    health_response,
)
from tests.support.synthetic_native import config, digest, entity, geometry, session


class _GeneratedReusablePipe:
    """A generated in-memory transport that can service independent frames."""

    def __init__(self, handler, *, server_pid: int = 1234) -> None:
        self._handler = handler
        self._server_pid = server_pid
        self._request = bytearray()
        self._response = bytearray()
        self.write_timeouts: list[float] = []
        self.read_timeouts: list[float] = []
        self.frame_count = 0
        self.closed = False

    @property
    def server_pid(self) -> int:
        return self._server_pid

    def write(self, payload: bytes, timeout: float) -> int:
        self.write_timeouts.append(timeout)
        self._request.extend(payload)
        if len(self._request) >= 4:
            length = struct.unpack(">I", self._request[:4])[0]
            if len(self._request) == length + 4:
                request = decode_frame(bytes(self._request), maximum=64 * 1024)
                self._request.clear()
                self.frame_count += 1
                self._response.extend(
                    encode_frame(
                        self._handler(request),
                        maximum=response_limit_for_method(request["method"]),
                        opaque_string_rules=(
                            native_contracts_module.opaque_embedded_json_rules(
                                "response"
                            )
                        ),
                    )
                )
        return len(payload)

    def read(self, maximum: int, timeout: float) -> bytes:
        self.read_timeouts.append(timeout)
        result = bytes(self._response[:maximum])
        del self._response[: len(result)]
        return result

    def pending_bytes(self) -> int:
        return len(self._response)

    def close(self) -> None:
        self.closed = True


class _GeneratedRawResponsePipe(_GeneratedReusablePipe):
    """Generated peer that can return malformed bytes without our encoder."""

    def __init__(self, raw_response: bytes, *, server_pid: int = 1234) -> None:
        super().__init__(lambda _request: {}, server_pid=server_pid)
        self._raw_response = raw_response

    def write(self, payload: bytes, timeout: float) -> int:
        self.write_timeouts.append(timeout)
        self._request.extend(payload)
        if len(self._request) >= 4:
            length = struct.unpack(">I", self._request[:4])[0]
            if len(self._request) == length + 4:
                # Validate only the client request; the response deliberately
                # bypasses encode_frame() to model an untrusted peer.
                decode_frame(bytes(self._request), maximum=64 * 1024)
                self._request.clear()
                self.frame_count += 1
                self._response.extend(self._raw_response)
        return len(payload)


class _GeneratedBlockingPipe(_GeneratedReusablePipe):
    """Generated race double that pauses one response after its frame write."""

    def __init__(self, handler, *, server_pid: int = 1234) -> None:
        super().__init__(handler, server_pid=server_pid)
        self.read_started = threading.Event()
        self.release_read = threading.Event()

    def read(self, maximum: int, timeout: float) -> bytes:
        self.read_started.set()
        if not self.release_read.wait(timeout=5):
            raise TimeoutError("generated barrier timed out")
        return super().read(maximum, timeout)


class _GeneratedClock:
    """Deterministic paired wall/monotonic clock for session-bound RPC tests."""

    def __init__(self, current: datetime, *, monotonic: float = 1_000.0) -> None:
        self.current = current
        self.value = monotonic

    def utc_now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.value

    def session_clock(self) -> NativeSessionClockReading:
        return NativeSessionClockReading(
            clock=native_contracts_module.NATIVE_SESSION_MONOTONIC_CLOCK,
            boot_id="a" * 32,
            uptime_milliseconds=int(self.value * 1000),
        )

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        self.value += seconds


class _GeneratedClockedPipe(_GeneratedReusablePipe):
    """Generated transport that advances one injected clock at named I/O points."""

    def __init__(
        self,
        handler,
        *,
        clock: _GeneratedClock,
        write_delay: float = 0.0,
        read_delays: tuple[float, ...] = (),
        server_pid: int = 1234,
    ) -> None:
        super().__init__(handler, server_pid=server_pid)
        self._clock = clock
        self._write_delay = write_delay
        self._read_delays = iter(read_delays)

    def write(self, payload: bytes, timeout: float) -> int:
        if self._write_delay:
            self._clock.advance(self._write_delay)
            self._write_delay = 0.0
        return super().write(payload, timeout)

    def read(self, maximum: int, timeout: float) -> bytes:
        try:
            delay = next(self._read_delays)
        except StopIteration:
            delay = 0.0
        if delay:
            self._clock.advance(delay)
        return super().read(maximum, timeout)


class _GeneratedOwnedSecretFile:
    """Generated ownership handle used to test descriptor cleanup without NTFS."""

    def __init__(
        self,
        path: Path,
        *,
        payload: bytes = b"",
        fail_capture_after_rename: bool = False,
    ) -> None:
        self.path = path
        self.payload = payload
        self._identity = FileIdentity("generated", 7, 11, 13)
        self._fail_capture_after_rename = fail_capture_after_rename
        self.renamed = False
        self.delete_requested = False
        self.closed = False

    def capture_binding(self) -> OwnedPathBinding:
        if self.renamed and self._fail_capture_after_rename:
            raise OwnershipLostError("generated post-rename binding failure")
        return OwnedPathBinding(
            path=self.path,
            identity=self._identity,
            byte_size=len(self.payload),
            sha256=sha256(self.payload).hexdigest(),
            is_directory=False,
        )

    def final_path(self) -> Path:
        return self.path

    def rename_no_replace(self, destination: Path) -> None:
        self.path = destination
        self.renamed = True

    def read_chunks(self, _chunk_size: int = 1024 * 1024):
        yield self.payload

    def read_prefix(self, length: int) -> bytes:
        return self.payload[:length]

    def write_bytes(self, payload: bytes) -> None:
        self.payload = payload

    def request_delete(self) -> None:
        self.delete_requested = True

    def close(self) -> None:
        self.closed = True


class _GeneratedSessionBackend:
    """Generated backend with a retained parent and one owned secret file."""

    def __init__(
        self,
        parent: Path,
        *,
        existing: _GeneratedOwnedSecretFile | None = None,
        replacement_survives_cleanup: bool = False,
    ) -> None:
        self.parent = parent
        self.existing = existing
        self.created: _GeneratedOwnedSecretFile | None = None
        self.replacement_survives_cleanup = replacement_survives_cleanup
        self.public_create_calls = 0
        self.private_create_calls = 0

    def create_new_file(self, path: Path) -> _GeneratedOwnedSecretFile:
        self.public_create_calls += 1
        self.created = _GeneratedOwnedSecretFile(path)
        return self.created

    def create_private_file(self, path: Path) -> _GeneratedOwnedSecretFile:
        self.private_create_calls += 1
        self.created = _GeneratedOwnedSecretFile(path)
        return self.created

    def open_existing_file(
        self,
        _path: Path,
        *,
        for_delete: bool,
    ) -> _GeneratedOwnedSecretFile:
        del for_delete
        assert self.existing is not None
        return self.existing

    def path_matches_binding(self, path: Path, binding: OwnedPathBinding) -> bool:
        owned = self.existing or self.created
        return path == binding.path and not (
            self.replacement_survives_cleanup
            and owned is not None
            and owned.delete_requested
        )

    def path_exists(self, _path: Path) -> bool:
        owned = self.existing or self.created
        return (
            self.replacement_survives_cleanup
            and owned is not None
            and owned.delete_requested
        ) or (
            owned is not None and not owned.delete_requested
        )

    def validate_private_artifact_ancestry(self, _path: Path) -> None:
        """Generated backend models an already-validated private parent."""

    def verify_private_staging_file(self, _path: Path) -> None:
        """Generated backend models retained-handle private DACL readback."""


class _GeneratedDirectoryChain:
    """Minimal generated no-follow chain expected by native session helpers."""

    def __init__(self, parent: Path) -> None:
        self.path = parent
        self.components = (SimpleNamespace(owned=object()),)
        self.closed = False

    def require_binding(self) -> None:
        return

    def close(self) -> None:
        self.closed = True


class NativeProtocolTests(unittest.TestCase):
    """Exercise every framing rule without a proprietary bridge."""

    @staticmethod
    def _raw_geometry_response_payload(request_id: str, carrier: str) -> bytes:
        """Encode a hostile outer carrier without invoking our canonicalizer."""

        return json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "id": request_id,
                "result": {
                    "kind": "geometry",
                    "geometry_json": carrier,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _raw_inventory_response_payload(request_id: str, carrier: str) -> bytes:
        """Encode a raw inventory carrier without normalizing it first."""

        return json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "id": request_id,
                "result": {
                    "kind": "inventory",
                    "inventory_json": carrier,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def test_handshake_validation_binds_response_to_session_transcript(self) -> None:
        descriptor = session()
        configured = config()
        health = {
            "kind": "health",
            "protocol_major": 1,
            "protocol_minor": 0,
            "adapter": descriptor["adapter"],
            "plugin": descriptor["plugin"],
            "host": descriptor["host"],
            "capabilities": descriptor["capabilities"],
        }
        handshake = {
            "kind": "session",
            "bridge_nonce": descriptor["bridge_nonce"],
            "challenge_response": descriptor["challenge_response"],
            "adapter": descriptor["adapter"],
            "plugin": descriptor["plugin"],
            "host": descriptor["host"],
            "capabilities": descriptor["capabilities"],
            "current_document": descriptor["current_document"],
        }
        native_bridge_module._require_bridge_identity(
            health,
            handshake,
            configured,
            descriptor["session_id"],
            descriptor["client_nonce"],
            descriptor["challenge"],
        )
        for name, session_id, response in (
            ("mismatched-response", descriptor["session_id"], "0" * 64),
            (
                "session-replay",
                "native-session-" + "d" * 32,
                descriptor["challenge_response"],
            ),
        ):
            with self.subTest(case=name):
                forged = dict(handshake)
                forged["challenge_response"] = response
                with self.assertRaises(PipelineError) as raised:
                    native_bridge_module._require_bridge_identity(
                        health,
                        forged,
                        configured,
                        session_id,
                        descriptor["client_nonce"],
                        descriptor["challenge"],
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)

    def test_frame_rejects_trailing_duplicate_and_non_finite_json(self) -> None:
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "id": "a" * 32,
            "method": "health",
            "params": {"session_id": "native-session-" + "a" * 32},
        }
        frame = encode_frame(payload, maximum=64 * 1024)
        self.assertEqual(decode_frame(frame, maximum=64 * 1024), payload)
        with self.assertRaises(NativeProtocolError):
            decode_frame(frame + b"x", maximum=64 * 1024)
        duplicate = b'{"a":1,"a":2}'
        malformed = len(duplicate).to_bytes(4, "big") + duplicate
        with self.assertRaises(NativeProtocolError):
            decode_frame(malformed, maximum=64 * 1024)
        non_finite = b'{"value":NaN}'
        with self.assertRaises(NativeProtocolError):
            decode_frame(len(non_finite).to_bytes(4, "big") + non_finite, maximum=64 * 1024)

    def test_exact_short_read_and_write_loops(self) -> None:
        payload = b"abcdef"
        cursor = [0]

        def reader(maximum: int, _timeout: float) -> bytes:
            start = cursor[0]
            cursor[0] += min(2, maximum)
            return payload[start : cursor[0]]

        encoded = encode_frame(
            {
                "protocol_version": PROTOCOL_VERSION,
                "id": "b" * 32,
                "method": "health",
                "params": {"session_id": "native-session-" + "b" * 32},
            },
            maximum=64 * 1024,
        )
        cursor[0] = 0
        body = bytearray(encoded)

        def framed_reader(maximum: int, _timeout: float) -> bytes:
            result = bytes(body[: min(2, maximum)])
            del body[: len(result)]
            return result

        result = read_frame(
            framed_reader,
            maximum=64 * 1024,
            deadline=__import__("time").monotonic() + 1,
        )
        self.assertEqual(result["method"], "health")
        written = bytearray()

        def writer(chunk: bytes, _timeout: float) -> int:
            written.extend(chunk[:2])
            return min(2, len(chunk))

        write_all(writer, payload, deadline=__import__("time").monotonic() + 1)
        self.assertEqual(bytes(written), payload)

    def test_large_short_reads_use_fixed_bounded_transport_requests(self) -> None:
        """A 64 MiB frame may never request a shrinking full remainder buffer."""

        frame_size = 64 * 1024 * 1024
        offset = 0
        requested_capacities: list[int] = []

        def reader(maximum: int, _timeout: float) -> bytes:
            nonlocal offset
            requested_capacities.append(maximum)
            amount = min(maximum, frame_size - offset)
            offset += amount
            return b"x" * amount

        result = read_exact(
            reader,
            frame_size,
            deadline=time.monotonic() + 10,
        )
        self.assertEqual(len(result), frame_size)
        self.assertEqual(result[:1], b"x")
        self.assertEqual(result[-1:], b"x")
        self.assertLessEqual(len(requested_capacities), 1024)
        self.assertTrue(
            all(0 < requested <= PIPE_IO_CHUNK_BYTES for requested in requested_capacities)
        )
        # The recorder stands in for the allocation requested by ReadFile.
        # With fixed chunks it is one frame (plus at most a final short
        # chunk), rather than the old quadratic sum of all remainders.
        self.assertLessEqual(
            sum(requested_capacities),
            frame_size + PIPE_IO_CHUNK_BYTES,
        )

    def test_one_byte_and_zero_byte_partial_reads_fail_or_complete_exactly(self) -> None:
        payload = b"one-byte-generated-frame"
        offset = 0

        def one_byte_reader(maximum: int, _timeout: float) -> bytes:
            nonlocal offset
            self.assertLessEqual(maximum, PIPE_IO_CHUNK_BYTES)
            if offset == len(payload):
                return b""
            result = payload[offset : offset + 1]
            offset += 1
            return result

        self.assertEqual(
            read_exact(
                one_byte_reader,
                len(payload),
                deadline=time.monotonic() + 1,
            ),
            payload,
        )
        with self.assertRaises(NativeProtocolError):
            read_exact(
                lambda _maximum, _timeout: b"",
                1,
                deadline=time.monotonic() + 1,
            )

    def test_later_read_timeout_and_cancellation_keep_one_absolute_deadline(self) -> None:
        calls = 0

        def delayed_reader(_maximum: int, _timeout: float) -> bytes:
            nonlocal calls
            calls += 1
            return b"x"

        # The first byte arrives before the deadline.  The second loop must
        # observe the original deadline rather than starting a fresh timeout.
        with mock.patch(
            "liang_pingfa_review.native_protocol.time.monotonic",
            side_effect=(0.0, 1.1),
        ):
            with self.assertRaises(TimeoutError):
                read_exact(delayed_reader, 2, deadline=1.0)
        self.assertEqual(calls, 1)

        cancellation_calls = 0

        def cancelled_reader(_maximum: int, _timeout: float) -> bytes:
            nonlocal cancellation_calls
            cancellation_calls += 1
            if cancellation_calls == 1:
                return b"x"
            raise TimeoutError("generated overlapped cancellation")

        with self.assertRaisesRegex(TimeoutError, "cancellation"):
            read_exact(
                cancelled_reader,
                2,
                deadline=time.monotonic() + 1,
            )
        self.assertEqual(cancellation_calls, 2)

    def test_write_all_uses_the_same_fixed_chunk_bound(self) -> None:
        payload = b"w" * (PIPE_IO_CHUNK_BYTES * 2 + 7)
        requested: list[int] = []
        received = bytearray()

        def writer(chunk: bytes, _timeout: float) -> int:
            requested.append(len(chunk))
            received.extend(chunk)
            return len(chunk)

        write_all(writer, payload, deadline=time.monotonic() + 1)
        self.assertEqual(bytes(received), payload)
        self.assertTrue(
            all(0 < length <= PIPE_IO_CHUNK_BYTES for length in requested)
        )

    @staticmethod
    def _direct_write_pipe() -> WindowsNamedPipe:
        """Build a transport shell whose Win32 transfer is mocked per test."""

        pipe = object.__new__(WindowsNamedPipe)
        pipe._closed = False
        pipe._handle = 1
        pipe._io_lock = threading.RLock()
        return pipe

    def test_direct_write_accepts_exact_chunk_and_never_allocates_larger_buffer(self) -> None:
        pipe = self._direct_write_pipe()
        payload = b"x" * PIPE_IO_CHUNK_BYTES
        allocations: list[tuple[object, int]] = []

        def allocate(value: object, length: int) -> object:
            allocations.append((value, length))
            return object()

        with (
            mock.patch(
                "liang_pingfa_review.native_bridge.ctypes.create_string_buffer",
                side_effect=allocate,
            ),
            mock.patch.object(
                pipe,
                "_transfer",
                return_value=(len(payload), SimpleNamespace()),
            ) as transfer,
        ):
            self.assertEqual(pipe.write(payload, 1.0), len(payload))
        self.assertEqual([length for _value, length in allocations], [PIPE_IO_CHUNK_BYTES])
        self.assertEqual(transfer.call_args.args[2], PIPE_IO_CHUNK_BYTES)

    def test_direct_write_rejects_limit_plus_one_and_huge_inputs_before_allocation(self) -> None:
        pipe = self._direct_write_pipe()
        for payload in (
            b"x" * (PIPE_IO_CHUNK_BYTES + 1),
            b"x" * (PIPE_IO_CHUNK_BYTES * 128),
        ):
            with self.subTest(size=len(payload)):
                with (
                    mock.patch(
                        "liang_pingfa_review.native_bridge.ctypes.create_string_buffer"
                    ) as allocate,
                    mock.patch.object(pipe, "_transfer") as transfer,
                    self.assertRaises(NativePipeClosed),
                ):
                    pipe.write(payload, 1.0)
                allocate.assert_not_called()
                transfer.assert_not_called()

    def test_direct_write_starts_deadline_before_allocating(self) -> None:
        pipe = self._direct_write_pipe()
        with (
            mock.patch(
                "liang_pingfa_review.native_bridge.time.monotonic",
                side_effect=(0.0, 1.1),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.ctypes.create_string_buffer"
            ) as allocate,
            self.assertRaises(TimeoutError),
        ):
            pipe.write(b"x", 1.0)
        allocate.assert_not_called()

    def test_write_all_handles_partial_writes_and_later_chunk_timeout(self) -> None:
        payload = b"w" * (PIPE_IO_CHUNK_BYTES + 11)
        received = bytearray()
        lengths: list[int] = []

        def partial_writer(chunk: bytes, _timeout: float) -> int:
            lengths.append(len(chunk))
            amount = min(3, len(chunk))
            received.extend(chunk[:amount])
            return amount

        write_all(
            partial_writer,
            payload,
            deadline=time.monotonic() + 5,
        )
        self.assertEqual(bytes(received), payload)
        self.assertTrue(all(length <= PIPE_IO_CHUNK_BYTES for length in lengths))

        calls = 0

        def writer_then_late_timeout(chunk: bytes, _timeout: float) -> int:
            nonlocal calls
            calls += 1
            return len(chunk)

        with (
            mock.patch(
                "liang_pingfa_review.native_protocol.time.monotonic",
                side_effect=(0.0, 1.1),
            ),
            self.assertRaises(TimeoutError),
        ):
            write_all(
                writer_then_late_timeout,
                b"w" * (PIPE_IO_CHUNK_BYTES + 1),
                deadline=1.0,
            )
        self.assertEqual(calls, 1)

    def test_pipe_name_rejects_remote_control_and_predictable_values(self) -> None:
        self.assertEqual(
            validate_pipe_name(r"\\.\pipe\liang-pingfa-native-a1b2c3d4e5f6g7h8"),
            r"\\.\pipe\liang-pingfa-native-a1b2c3d4e5f6g7h8",
        )
        prefix = chr(92) * 2 + "." + chr(92) + "pipe" + chr(92)
        for token in (
            "ABCDEFG0" * 4,
            "Ab0Cd1Ef2Gh3Ij4Kl5Mn6Op7Qr8St9Uv",
        ):
            candidate = prefix + "liang-pingfa-native-" + token
            with self.subTest(accepted_token=token):
                self.assertEqual(candidate, validate_pipe_name(candidate))
        for candidate in (
            chr(92) * 2 + "server" + chr(92) + "pipe" + chr(92) + "liang-pingfa-native-a1b2c3d4e5f6g7h8",
            prefix + "other-a1b2c3d4e5f6g7h8",
            prefix + "liang-pingfa-native-a1b2c3d4e5f6g7h8\n",
            r"\\.\pipe\liang-pingfa-native-aaaaaaaaaaaaaaaa",
            prefix + "liang-pingfa-native-" + ("A" * 32),
            prefix + "liang-pingfa-native-" + ("0" * 32),
            prefix + "liang-pingfa-native-" + (("AA0" * 10) + "AA"),
            prefix + "liang-pingfa-native-" + (("ABCDEF0" * 4) + "ABCD"),
            prefix + "liang-pingfa-native-" + (("ABCDEFG0" * 3) + "ABCDEFG-"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(PipelineError) as raised:
                    validate_pipe_name(candidate)
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PIPE_INVALID)

    def test_unsolicited_second_frame_invalidates_client(self) -> None:
        descriptor = session()
        process = ProcessIdentity(
            pid=descriptor["pid"],
            windows_session_id=descriptor["windows_session_id"],
            creation_time_100ns=int(descriptor["process"]["creation_time_100ns"]),
            instance_fingerprint=descriptor["process"]["instance_fingerprint"],
            executable_fingerprint=descriptor["process"]["executable_fingerprint"],
        )
        pipe = ScriptedPipe(
            health_response,
            server_pid=descriptor["pid"],
            extra_frame=True,
        )
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=process,
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)
            with self.assertRaises(PipelineError) as raised:
                client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_geometry_client_uses_the_shared_exact_session_binding_gate(self) -> None:
        """A source-equal export from another session cannot leave the client."""

        descriptor = session()
        exact_export = geometry(session_value=descriptor)

        def geometry_response(export: dict):
            def handler(request: dict) -> dict:
                return {
                    "protocol_version": PROTOCOL_VERSION,
                    "id": request["id"],
                    "result": {
                        "kind": "geometry",
                        "geometry_json": canonical_json_bytes(export).decode("utf-8"),
                    },
                }

            return handler

        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process_for(descriptor),
        ):
            exact_pipe = _GeneratedReusablePipe(
                geometry_response(exact_export),
                server_pid=descriptor["pid"],
            )
            exact_client = NativeBridgeClient(
                descriptor,
                config=config(),
                transport=exact_pipe,
            )
            self.assertEqual(exact_client.export_exact_geometry(), exact_export)
            self.assertFalse(exact_client.invalid)

            other_session = deepcopy(descriptor)
            other_session["session_id"] = "native-session-" + "d" * 32
            other_session["pid"] += 1
            other_session["process"]["instance_fingerprint"] = digest(
                "other-session-process"
            )
            other_session["challenge_response"] = derive_challenge_response(
                other_session["client_nonce"],
                other_session["challenge"],
                other_session["bridge_nonce"],
                session_id=other_session["session_id"],
            )
            other_session = attach_integrity(other_session)
            other_export = geometry(session_value=other_session)
            other_pipe = _GeneratedReusablePipe(
                geometry_response(other_export),
                server_pid=descriptor["pid"],
            )
            other_client = NativeBridgeClient(
                descriptor,
                config=config(),
                transport=other_pipe,
            )
            with self.assertRaises(PipelineError) as raised:
                other_client.export_exact_geometry()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CAPABILITY_MISMATCH)
        self.assertTrue(other_client.invalid)
        self.assertTrue(other_pipe.closed)

    def test_geometry_response_enforces_multibyte_byte_cap_before_inner_parse(self) -> None:
        descriptor = session()
        raw_geometry = "中" * (
            native_contracts_module.MAX_NATIVE_GEOMETRY_JSON_BYTES
            // len("中".encode("utf-8"))
            + 1
        )

        request_id = "a" * 32
        payload = self._raw_geometry_response_payload(request_id, raw_geometry)
        pipe = _GeneratedRawResponsePipe(
            struct.pack(">I", len(payload)) + payload,
            server_pid=descriptor["pid"],
        )
        with (
            mock.patch(
                "liang_pingfa_review.native_bridge.inspect_process",
                return_value=self._process_for(descriptor),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.new_request_id",
                return_value=request_id,
            ),
            mock.patch.object(native_contracts_module, "strict_native_json") as parser,
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)
            with self.assertRaises(PipelineError) as raised:
                client.export_exact_geometry()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)
        parser.assert_not_called()
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_opaque_geometry_carrier_accepts_exact_byte_cap_and_rejects_cap_plus_one(
        self,
    ) -> None:
        """The outer frame keeps its exact carrier without NFC-normalizing it."""

        carrier = "\u0344" * (
            native_contracts_module.MAX_NATIVE_GEOMETRY_JSON_BYTES
            // len("\u0344".encode("utf-8"))
        )
        rules = native_contracts_module.opaque_embedded_json_rules("response")
        decoded = native_protocol_module.decode_payload(
            self._raw_geometry_response_payload("a" * 32, carrier),
            maximum=native_protocol_module.MAX_GEOMETRY_RESPONSE_BYTES,
            opaque_string_rules=rules,
        )
        self.assertEqual(decoded["result"]["geometry_json"], carrier)
        with self.assertRaises(NativeProtocolError):
            native_protocol_module.decode_payload(
                self._raw_geometry_response_payload("a" * 32, carrier + "\u0344"),
                maximum=native_protocol_module.MAX_GEOMETRY_RESPONSE_BYTES,
                opaque_string_rules=rules,
            )

    def test_opaque_inventory_carrier_accepts_exact_byte_cap_and_rejects_cap_plus_one(
        self,
    ) -> None:
        carrier = "中" * (
            native_contracts_module.MAX_NATIVE_INVENTORY_JSON_BYTES
            // len("中".encode("utf-8"))
        )
        rules = native_contracts_module.opaque_embedded_json_rules("response")
        decoded = native_protocol_module.decode_payload(
            self._raw_inventory_response_payload("a" * 32, carrier),
            maximum=native_protocol_module.MAX_INVENTORY_RESPONSE_BYTES,
            opaque_string_rules=rules,
        )
        self.assertEqual(decoded["result"]["inventory_json"], carrier)
        with self.assertRaises(NativeProtocolError):
            native_protocol_module.decode_payload(
                self._raw_inventory_response_payload("a" * 32, carrier + "中"),
                maximum=native_protocol_module.MAX_INVENTORY_RESPONSE_BYTES,
                opaque_string_rules=rules,
            )

    def test_pathological_outer_geometry_never_reaches_nfc(self) -> None:
        """A frame-valid 16 MiB combining scalar fails under the RPC budget."""

        descriptor = session()
        carrier = "\u0344" * (
            native_contracts_module.MAX_NATIVE_GEOMETRY_JSON_BYTES
            // len("\u0344".encode("utf-8"))
        )
        request_id = "a" * 32
        payload = self._raw_geometry_response_payload(request_id, carrier)
        pipe = _GeneratedRawResponsePipe(
            struct.pack(">I", len(payload)) + payload,
            server_pid=descriptor["pid"],
        )
        normalizer_lengths: list[int] = []
        original_normalize = canonical_module.unicodedata.normalize

        def bounded_normalize(form: str, value: str) -> str:
            normalizer_lengths.append(len(value))
            if len(value) > canonical_module.MAX_JSON_STRING_CODEPOINTS:
                raise AssertionError("unbounded opaque carrier reached NFC")
            return original_normalize(form, value)

        with (
            mock.patch(
                "liang_pingfa_review.native_bridge.inspect_process",
                return_value=self._process_for(descriptor),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.new_request_id",
                return_value=request_id,
            ),
            mock.patch.object(
                canonical_module.unicodedata,
                "normalize",
                side_effect=bounded_normalize,
            ),
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)
            with self.assertRaises(PipelineError) as raised:
                client.export_exact_geometry()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_GEOMETRY_INVALID)
        self.assertNotIn(len(carrier), normalizer_lengths)
        self.assertTrue(
            all(
                length <= canonical_module.MAX_JSON_STRING_CODEPOINTS
                for length in normalizer_lengths
            )
        )
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_valid_unicode_geometry_carrier_preserves_inner_text(self) -> None:
        """Inner JSON remains strict/canonical for CJK, astral, escapes, and Hangul."""

        descriptor = session()
        text = '中文 \U0001F600 [] {} "quoted" 가'
        export = geometry(
            [entity("10", text=text)],
            session_value=descriptor,
        )

        def response(request: dict[str, Any]) -> dict[str, Any]:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "id": request["id"],
                "result": {
                    "kind": "geometry",
                    "geometry_json": canonical_json_bytes(export).decode("utf-8"),
                },
            }

        pipe = _GeneratedReusablePipe(response, server_pid=descriptor["pid"])
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process_for(descriptor),
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)
            received = client.export_exact_geometry()
        self.assertEqual(received["entities"][0]["text"], text)
        self.assertEqual(received, export)

    def test_deep_raw_response_is_a_terminal_protocol_error_and_releases_lock(self) -> None:
        """A 1500-level peer response cannot escape as RecursionError."""

        descriptor = session()
        raw_json = b'{"result":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"
        raw_frame = struct.pack(">I", len(raw_json)) + raw_json
        pipe = _GeneratedRawResponsePipe(raw_frame, server_pid=descriptor["pid"])
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process_for(descriptor),
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)
            with self.assertRaises(PipelineError) as raised:
                client.health()
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
            self.assertTrue(client.invalid)
            self.assertTrue(pipe.closed)
            self.assertFalse(client._lifecycle_lock.locked())
            with self.assertRaises(PipelineError) as later:
                client.health()
        self.assertEqual(later.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertEqual(pipe.frame_count, 1)

    def test_late_response_schema_recursion_invalidates_client(self) -> None:
        """A downstream validator recursion maps to the stable protocol code."""

        descriptor = session()
        pipe = _GeneratedReusablePipe(health_response, server_pid=descriptor["pid"])
        original_validate_schema = native_contracts_module._validate_schema

        def recurse_only_for_response(
            kind: str,
            artifact: object,
            **kwargs: object,
        ) -> None:
            if kind == "response" and isinstance(artifact, dict) and "result" in artifact:
                raise RecursionError("synthetic late response schema recursion")
            original_validate_schema(kind, artifact, **kwargs)

        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process_for(descriptor),
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)
            with mock.patch.object(
                native_contracts_module,
                "_validate_schema",
                side_effect=recurse_only_for_response,
            ):
                with self.assertRaises(PipelineError) as raised:
                    client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)
        self.assertFalse(client._lifecycle_lock.locked())

    @staticmethod
    def _process_for(descriptor: dict) -> ProcessIdentity:
        return ProcessIdentity(
            pid=descriptor["pid"],
            windows_session_id=descriptor["windows_session_id"],
            creation_time_100ns=int(descriptor["process"]["creation_time_100ns"]),
            instance_fingerprint=descriptor["process"]["instance_fingerprint"],
            executable_fingerprint=descriptor["process"]["executable_fingerprint"],
        )

    @staticmethod
    def _expiring_session(clock: _GeneratedClock, seconds: float) -> dict:
        descriptor = session()
        descriptor["created_at"] = format_utc(clock.current - timedelta(seconds=1))
        descriptor["expires_at"] = format_utc(
            clock.current + timedelta(seconds=seconds)
        )
        expires_milliseconds = int((clock.value + seconds) * 1000)
        descriptor["monotonic_clock"] = native_contracts_module.NATIVE_SESSION_MONOTONIC_CLOCK
        descriptor["monotonic_boot_id"] = "a" * 32
        # Match the persisted whole-second wall interval.  The generated
        # client tests then vary only the remaining lifetime, rather than
        # constructing a descriptor with two unrelated signed deadlines.
        descriptor["monotonic_issued"] = str(int((clock.value - 1) * 1000))
        descriptor["monotonic_expires"] = str(expires_milliseconds)
        return attach_integrity(descriptor)

    def _clocked_client(
        self,
        *,
        clock: _GeneratedClock,
        descriptor: dict,
        pipe: _GeneratedReusablePipe,
        configured: dict | None = None,
    ) -> NativeBridgeClient:
        patches = (
            mock.patch(
                "liang_pingfa_review.native_bridge.inspect_process",
                return_value=self._process_for(descriptor),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.utc_now",
                side_effect=clock.utc_now,
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.time.monotonic",
                side_effect=clock.monotonic,
            ),
        )
        stack = ExitStack()
        for patcher in patches:
            stack.enter_context(patcher)
        self.addCleanup(stack.close)
        return NativeBridgeClient(
            descriptor,
            config=configured or config(),
            transport=pipe,
            session_clock=clock.session_clock,
        )

    def test_rpc_deadline_is_capped_by_session_and_expires_during_io(self) -> None:
        """A fake clock proves every transport stage honors signed expiry."""

        cases = (
            (
                "write",
                lambda clock, descriptor: _GeneratedClockedPipe(
                    health_response,
                    clock=clock,
                    write_delay=1.1,
                    server_pid=descriptor["pid"],
                ),
            ),
            (
                "read-prefix",
                lambda clock, descriptor: _GeneratedClockedPipe(
                    health_response,
                    clock=clock,
                    read_delays=(1.1,),
                    server_pid=descriptor["pid"],
                ),
            ),
            (
                "read-body",
                lambda clock, descriptor: _GeneratedClockedPipe(
                    health_response,
                    clock=clock,
                    # The first four-byte prefix arrives before expiry; the
                    # body cannot start after this delayed first read.
                    read_delays=(0.6, 0.6),
                    server_pid=descriptor["pid"],
                ),
            ),
        )
        for name, make_pipe in cases:
            with self.subTest(stage=name):
                clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
                descriptor = self._expiring_session(clock, 1)
                pipe = make_pipe(clock, descriptor)
                client = self._clocked_client(
                    clock=clock,
                    descriptor=descriptor,
                    pipe=pipe,
                )
                with self.assertRaises(PipelineError) as raised:
                    client.health()
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
                self.assertTrue(client.invalid)
                self.assertTrue(pipe.closed)

    def test_rpc_expiry_during_response_validation_never_returns_result(self) -> None:
        clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        descriptor = self._expiring_session(clock, 1)
        pipe = _GeneratedClockedPipe(
            health_response,
            clock=clock,
            server_pid=descriptor["pid"],
        )
        client = self._clocked_client(
            clock=clock,
            descriptor=descriptor,
            pipe=pipe,
        )
        original_validate = native_bridge_module.validate_native_contract

        def delayed_validate(kind: str, value: object, **kwargs: object) -> dict:
            checked = original_validate(kind, value, **kwargs)
            if kind == "response":
                clock.advance(1.1)
            return checked

        with mock.patch(
            "liang_pingfa_review.native_bridge.validate_native_contract",
            side_effect=delayed_validate,
        ):
            with self.assertRaises(PipelineError) as raised:
                client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_method_deadline_covers_generic_response_validation_and_errors(self) -> None:
        """A short method budget cannot be spent after pipe I/O completes."""

        for name, handler in (
            ("result", health_response),
            (
                "error",
                lambda request: {
                    "protocol_version": PROTOCOL_VERSION,
                    "id": request["id"],
                    "error": {"code": "INTERNAL_ERROR"},
                },
            ),
        ):
            with self.subTest(response=name):
                clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
                descriptor = self._expiring_session(clock, 10)
                configured = config()
                configured["timeouts"]["health_ms"] = 100
                pipe = _GeneratedClockedPipe(
                    handler,
                    clock=clock,
                    server_pid=descriptor["pid"],
                )
                client = self._clocked_client(
                    clock=clock,
                    descriptor=descriptor,
                    pipe=pipe,
                    configured=configured,
                )
                original_validate = native_bridge_module.validate_native_contract

                def delayed_validate(kind: str, value: object, **kwargs: object) -> dict:
                    checked = original_validate(kind, value, **kwargs)
                    if kind == "response":
                        clock.advance(0.25)
                    return checked

                with mock.patch(
                    "liang_pingfa_review.native_bridge.validate_native_contract",
                    side_effect=delayed_validate,
                ):
                    with self.assertRaises(PipelineError) as raised:
                        client.health()
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
                self.assertTrue(client.invalid)
                self.assertTrue(pipe.closed)

    def test_method_deadline_covers_decode_and_method_specific_validation(self) -> None:
        """Injected fake-clock work cannot leak a late inventory/geometry result."""

        clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        descriptor = self._expiring_session(clock, 10)
        configured = config()
        configured["timeouts"]["inventory_ms"] = 1000
        inventory_response = lambda request: {
            "protocol_version": PROTOCOL_VERSION,
            "id": request["id"],
            "result": {
                "kind": "inventory",
                "inventory_json": canonical_json_bytes(
                    {
                        "schema_version": "liang-pingfa/native-inventory-export/v2",
                        "document_revision_fingerprint": descriptor[
                            "current_document"
                        ]["revision_fingerprint"],
                        "inventory_digest": digest("inventory"),
                    }
                ).decode("utf-8"),
            },
        }
        pipe = _GeneratedClockedPipe(
            inventory_response,
            clock=clock,
            server_pid=descriptor["pid"],
        )
        client = self._clocked_client(
            clock=clock,
            descriptor=descriptor,
            pipe=pipe,
            configured=configured,
        )
        original_loads = native_protocol_module.strict_json_loads

        def delayed_loads(text: str) -> object:
            parsed = original_loads(text)
            clock.advance(1.1)
            return parsed

        with mock.patch.object(
            native_protocol_module,
            "strict_json_loads",
            side_effect=delayed_loads,
        ):
            with self.assertRaises(PipelineError) as raised:
                client.export_inventory()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertTrue(client.invalid)

        geometry_clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        geometry_descriptor = self._expiring_session(geometry_clock, 10)
        geometry_config = config()
        geometry_config["timeouts"]["geometry_ms"] = 1000
        export = geometry(session_id=geometry_descriptor["session_id"])

        def geometry_response(request: dict) -> dict:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "id": request["id"],
                "result": {
                    "kind": "geometry",
                    "geometry_json": canonical_json_bytes(export).decode("utf-8"),
                },
            }

        geometry_pipe = _GeneratedClockedPipe(
            geometry_response,
            clock=geometry_clock,
            server_pid=geometry_descriptor["pid"],
        )
        geometry_client = self._clocked_client(
            clock=geometry_clock,
            descriptor=geometry_descriptor,
            pipe=geometry_pipe,
            configured=geometry_config,
        )
        original_semantics = native_contracts_module._validate_geometry_semantics

        def delayed_semantics(artifact: dict, **kwargs: object) -> None:
            original_semantics(artifact, **kwargs)
            geometry_clock.advance(1.1)

        with mock.patch.object(
            native_contracts_module,
            "_validate_geometry_semantics",
            side_effect=delayed_semantics,
        ):
            with self.assertRaises(PipelineError) as raised:
                geometry_client.export_exact_geometry()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertTrue(geometry_client.invalid)

    def test_inner_nfc_deadline_before_normalization_invalidates_session(self) -> None:
        """Expiry at the mandatory pre-NFC check must prevent the native call."""

        clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        descriptor = self._expiring_session(clock, 10)
        configured = config()
        configured["timeouts"]["geometry_ms"] = 1000
        text = "é"
        export = geometry([entity("10", text=text)], session_value=descriptor)
        request_id = "a" * 32
        payload = self._raw_geometry_response_payload(
            request_id,
            canonical_json_bytes(export).decode("utf-8"),
        )
        pipe = _GeneratedRawResponsePipe(
            struct.pack(">I", len(payload)) + payload,
            server_pid=descriptor["pid"],
        )
        client = self._clocked_client(
            clock=clock,
            descriptor=descriptor,
            pipe=pipe,
            configured=configured,
        )
        original_checkpoint = canonical_module._check_deadline
        original_normalize = canonical_module.unicodedata.normalize
        expired = False

        def expire_before_normalization(checker, stage: str) -> None:
            nonlocal expired
            if (
                checker is not None
                and stage == "JSON NFC validation"
                and not expired
            ):
                expired = True
                clock.advance(1.1)
            original_checkpoint(checker, stage)

        def fail_if_inner_text_normalizes(form: str, value: str) -> str:
            if value == text:
                raise AssertionError("deadline did not run before inner NFC")
            return original_normalize(form, value)

        with (
            mock.patch(
                "liang_pingfa_review.native_bridge.new_request_id",
                return_value=request_id,
            ),
            mock.patch.object(
                canonical_module,
                "_check_deadline",
                side_effect=expire_before_normalization,
            ),
            mock.patch.object(
                canonical_module.unicodedata,
                "normalize",
                side_effect=fail_if_inner_text_normalizes,
            ),
        ):
            with self.assertRaises(PipelineError) as raised:
                client.export_exact_geometry()
        self.assertTrue(expired)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_inner_nfc_deadline_after_normalization_invalidates_session(self) -> None:
        """Expiry after the native call must be observed before parsing continues."""

        clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        descriptor = self._expiring_session(clock, 10)
        configured = config()
        configured["timeouts"]["geometry_ms"] = 1000
        text = "é"
        export = geometry([entity("10", text=text)], session_value=descriptor)
        request_id = "a" * 32
        payload = self._raw_geometry_response_payload(
            request_id,
            canonical_json_bytes(export).decode("utf-8"),
        )
        pipe = _GeneratedRawResponsePipe(
            struct.pack(">I", len(payload)) + payload,
            server_pid=descriptor["pid"],
        )
        client = self._clocked_client(
            clock=clock,
            descriptor=descriptor,
            pipe=pipe,
            configured=configured,
        )
        original_normalize = canonical_module.unicodedata.normalize
        normalized_inner_text = False

        def expire_after_normalization(form: str, value: str) -> str:
            nonlocal normalized_inner_text
            normalized = original_normalize(form, value)
            if value == text and not normalized_inner_text:
                normalized_inner_text = True
                clock.advance(1.1)
            return normalized

        with (
            mock.patch(
                "liang_pingfa_review.native_bridge.new_request_id",
                return_value=request_id,
            ),
            mock.patch.object(
                canonical_module.unicodedata,
                "normalize",
                side_effect=expire_after_normalization,
            ),
        ):
            with self.assertRaises(PipelineError) as raised:
                client.export_exact_geometry()
        self.assertTrue(normalized_inner_text)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_large_geometry_uses_bounded_deadline_checkpoints(self) -> None:
        """Deadline probes scale by bounded intervals, not by nested re-scans."""

        records = [
            entity(f"{index + 1:04X}", sequence_index=index)
            for index in range(512)
        ]
        export = geometry(records)
        checks: list[str] = []

        validate_native_contract = native_contracts_module.validate_native_contract
        with mock.patch(
            "liang_pingfa_review.native_contracts._check_deadline",
            side_effect=lambda _check, stage: checks.append(stage),
        ):
            self.assertEqual(
                validate_native_contract(
                    "geometry",
                    export,
                    deadline_check=lambda _stage: None,
                )["entities"],
                records,
            )
        entity_checks = [
            stage for stage in checks if stage == "geometry entity semantic validation"
        ]
        self.assertEqual(len(entity_checks), 8)
        # Canonical and Draft 2020-12 passes now each probe bounded work;
        # ensure they remain linear rather than recursively rescanning records.
        self.assertGreater(len(checks), len(entity_checks))
        self.assertLess(len(checks), len(records) * 16)

    def test_geometry_deadline_checkpoint_bounds_cover_minimum_and_cap(self) -> None:
        """Every geometry size stays interruptible with bounded Unicode work."""

        def checkpoint_count(record_count: int) -> int:
            records = [
                entity(f"{index + 1:04X}", sequence_index=index)
                for index in range(record_count)
            ]
            export = geometry(records)
            checks: list[str] = []
            native_contracts_module.validate_native_contract(
                "geometry",
                export,
                deadline_check=checks.append,
            )
            return len(checks)

        minimum_checks = checkpoint_count(1)
        maximum_records = native_contracts_module.MAX_NATIVE_GEOMETRY_ENTITIES
        maximum_checks = checkpoint_count(maximum_records)

        # Major stages make a one-record export observable. Mandatory
        # before/after checks now bracket each actual non-ASCII NFC call, so
        # retain a conservative linear ceiling rather than the old
        # no-per-scalar threshold.
        self.assertGreaterEqual(minimum_checks, 16)
        self.assertLess(minimum_checks, 128)
        self.assertGreaterEqual(maximum_checks, maximum_records)
        self.assertLess(maximum_checks, maximum_records * 20)

    def test_fake_clock_interrupts_every_large_validation_stage(self) -> None:
        """Valid large values must observe the original deadline mid-traversal."""

        records = [
            entity(f"{index + 1:04X}", sequence_index=index)
            for index in range(80)
        ]
        entities_export = geometry(records)

        segmented = entity("10", native_type="LWPOLYLINE")
        segmented["segments"] = [
            deepcopy(segmented["segments"][0]) for _ in range(80)
        ]
        projection = dict(segmented)
        projection.pop("geometry_fingerprint")
        projection.pop("opaque_state_digest")
        segmented["geometry_fingerprint"] = canonical_sha256({"geometry": projection})
        segmented["opaque_state_digest"] = canonical_sha256(
            {"opaque_state": projection}
        )
        segments_export = geometry([segmented])

        unique_export = deepcopy(geometry())
        unique_export["owners"] = ["AA"] + [
            f"{index + 0x100:04X}" for index in range(80)
        ]
        unique_export = attach_integrity(unique_export)

        def assert_timeout(
            stage_fragment: str,
            operation,
            *,
            checkpoint_hits: int = 2,
        ) -> None:
            clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
            timing = native_bridge_module.RequestTiming(
                deadline=clock.value + 1,
                method_deadline=clock.value + 1,
                session_deadline=clock.value + 10,
            )
            seen: list[str] = []
            hits = 0

            def checkpoint(stage: str) -> None:
                nonlocal hits
                seen.append(stage)
                if stage_fragment in stage:
                    hits += 1
                    if hits >= checkpoint_hits:
                        clock.advance(1.1)
                native_bridge_module.require_request_deadline(timing, stage)

            with mock.patch(
                "liang_pingfa_review.native_bridge.time.monotonic",
                side_effect=clock.monotonic,
            ):
                with self.assertRaises(PipelineError) as raised:
                    operation(checkpoint)
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
            self.assertTrue(
                any(stage_fragment in stage for stage in seen),
                f"missing checkpoint for {stage_fragment}",
            )

        assert_timeout(
            "JSON text nesting scan",
            lambda checkpoint: strict_json_loads(
                "[" + ",".join("0" for _ in range(8_000)) + "]",
                deadline_check=checkpoint,
            ),
        )
        assert_timeout(
            "JSON NFC validation",
            lambda checkpoint: strict_json_loads(
                '{"value":"' + "é" * 9_000 + '"}',
                deadline_check=checkpoint,
            ),
        )
        assert_timeout(
            "geometry JSON Schema entities items",
            lambda checkpoint: native_contracts_module.validate_native_contract(
                "geometry",
                entities_export,
                deadline_check=checkpoint,
            ),
        )
        assert_timeout(
            "geometry JSON Schema segments items",
            lambda checkpoint: native_contracts_module.validate_native_contract(
                "geometry",
                segments_export,
                deadline_check=checkpoint,
            ),
        )
        assert_timeout(
            "geometry JSON Schema uniqueItems",
            lambda checkpoint: native_contracts_module.validate_native_contract(
                "geometry",
                unique_export,
                deadline_check=checkpoint,
            ),
        )
        assert_timeout(
            "geometry fingerprint validation",
            lambda checkpoint: native_contracts_module.validate_native_contract(
                "geometry",
                entities_export,
                deadline_check=checkpoint,
            ),
        )

    def test_nested_deadline_expiry_closes_client_and_releases_single_flight_lock(self) -> None:
        """A schema checkpoint timeout is terminal even after frame I/O succeeds."""

        clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        descriptor = self._expiring_session(clock, 10)
        configured = config()
        configured["timeouts"]["geometry_ms"] = 1000
        export = geometry(
            [
                entity(f"{index + 1:04X}", sequence_index=index)
                for index in range(32)
            ],
            session_value=descriptor,
        )

        def response(request: dict) -> dict:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "id": request["id"],
                "result": {
                    "kind": "geometry",
                    "geometry_json": canonical_json_bytes(export).decode("utf-8"),
                },
            }

        pipe = _GeneratedClockedPipe(
            response,
            clock=clock,
            server_pid=descriptor["pid"],
        )
        client = self._clocked_client(
            clock=clock,
            descriptor=descriptor,
            pipe=pipe,
            configured=configured,
        )
        original_checkpoint = native_contracts_module._check_deadline

        def expire_in_entity_schema(checker, stage: str) -> None:
            if stage == "geometry JSON Schema entities items":
                clock.advance(1.1)
            original_checkpoint(checker, stage)

        with mock.patch.object(
            native_contracts_module,
            "_check_deadline",
            side_effect=expire_in_entity_schema,
        ):
            with self.assertRaises(PipelineError) as raised:
                client.export_exact_geometry()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)
        self.assertFalse(client._lifecycle_lock.locked())

    def test_rpc_rejects_exact_expiry_and_allows_just_before_expiry(self) -> None:
        exact_clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        exact = self._expiring_session(exact_clock, 0)
        exact_pipe = _GeneratedClockedPipe(
            health_response,
            clock=exact_clock,
            server_pid=exact["pid"],
        )
        with self.assertRaises(PipelineError) as raised:
            self._clocked_client(
                clock=exact_clock,
                descriptor=exact,
                pipe=exact_pipe,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertEqual(exact_pipe.frame_count, 0)

        near_clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        near = self._expiring_session(near_clock, 1)
        near_pipe = _GeneratedClockedPipe(
            health_response,
            clock=near_clock,
            read_delays=(0.2, 0.2),
            server_pid=near["pid"],
        )
        near_client = self._clocked_client(
            clock=near_clock,
            descriptor=near,
            pipe=near_pipe,
        )
        self.assertEqual(near_client.health()["kind"], "health")
        self.assertFalse(near_client.invalid)

    def test_wall_clock_rollback_invalidates_connected_session_without_a_frame(self) -> None:
        """A rollback before creation is terminal even when monotonic time remains."""

        clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        descriptor = self._expiring_session(clock, 60)
        descriptor["created_at"] = format_utc(clock.current - timedelta(seconds=10))
        descriptor = attach_integrity(descriptor)
        pipe = _GeneratedClockedPipe(
            health_response,
            clock=clock,
            server_pid=descriptor["pid"],
        )
        client = self._clocked_client(
            clock=clock,
            descriptor=descriptor,
            pipe=pipe,
        )
        self.assertEqual(client.health()["kind"], "health")
        frames_before_rollback = pipe.frame_count
        clock.current -= timedelta(seconds=20)
        with self.assertRaises(PipelineError) as raised:
            client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertEqual(pipe.frame_count, frames_before_rollback)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_monotonic_session_deadline_survives_non_future_wall_clock_rollback(self) -> None:
        """Rollback within the wall interval cannot extend the captured deadline."""

        clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        descriptor = self._expiring_session(clock, 100)
        descriptor["created_at"] = format_utc(clock.current - timedelta(seconds=100))
        descriptor = attach_integrity(descriptor)
        pipe = _GeneratedClockedPipe(
            health_response,
            clock=clock,
            server_pid=descriptor["pid"],
        )
        client = self._clocked_client(
            clock=clock,
            descriptor=descriptor,
            pipe=pipe,
        )
        self.assertEqual(client.health()["kind"], "health")
        frames_before_expiry = pipe.frame_count
        clock.current -= timedelta(seconds=50)
        clock.value += 101
        with self.assertRaises(PipelineError) as raised:
            client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
        self.assertEqual(pipe.frame_count, frames_before_expiry)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_persisted_uptime_rejects_reset_domain_and_exact_expiry(self) -> None:
        """Descriptor lifetime is bound to a boot domain, not a local origin."""

        def client_for(
            clock: _GeneratedClock,
            descriptor: dict[str, Any],
        ) -> tuple[NativeBridgeClient, _GeneratedReusablePipe]:
            pipe = _GeneratedClockedPipe(
                health_response,
                clock=clock,
                server_pid=descriptor["pid"],
            )
            return (
                self._clocked_client(
                    clock=clock,
                    descriptor=descriptor,
                    pipe=pipe,
                ),
                pipe,
            )

        just_before_clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        just_before = self._expiring_session(just_before_clock, 1)
        just_before_client, just_before_pipe = client_for(
            just_before_clock,
            just_before,
        )
        self.assertEqual(just_before_client.health()["kind"], "health")
        frames_before_expiry = just_before_pipe.frame_count
        just_before_clock.advance(1)
        with self.assertRaises(PipelineError) as raised:
            just_before_client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
        self.assertEqual(just_before_pipe.frame_count, frames_before_expiry)

        exact_clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        exact = self._expiring_session(exact_clock, 0)
        # Keep UTC live so this case proves the exact persisted uptime
        # boundary, rather than merely the pre-existing UTC expiry gate.
        exact["expires_at"] = format_utc(
            exact_clock.current + timedelta(seconds=60)
        )
        exact = attach_integrity(exact)
        exact_pipe = _GeneratedClockedPipe(
            health_response,
            clock=exact_clock,
            server_pid=exact["pid"],
        )
        with self.assertRaises(PipelineError) as raised:
            self._clocked_client(
                clock=exact_clock,
                descriptor=exact,
                pipe=exact_pipe,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
        self.assertEqual(exact_pipe.frame_count, 0)

        reset_clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        reset = self._expiring_session(reset_clock, 60)
        reset_clock.value -= 301
        reset_pipe = _GeneratedClockedPipe(
            health_response,
            clock=reset_clock,
            server_pid=reset["pid"],
        )
        with self.assertRaises(PipelineError) as raised:
            self._clocked_client(
                clock=reset_clock,
                descriptor=reset,
                pipe=reset_pipe,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertEqual(reset_pipe.frame_count, 0)

        domain_clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        domain = self._expiring_session(domain_clock, 60)
        domain["monotonic_boot_id"] = "b" * 32
        domain = attach_integrity(domain)
        domain_pipe = _GeneratedClockedPipe(
            health_response,
            clock=domain_clock,
            server_pid=domain["pid"],
        )
        with self.assertRaises(PipelineError) as raised:
            self._clocked_client(
                clock=domain_clock,
                descriptor=domain,
                pipe=domain_pipe,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertEqual(domain_pipe.frame_count, 0)

    def test_long_method_timeout_cannot_extend_session_deadline(self) -> None:
        clock = _GeneratedClock(datetime(2030, 1, 1, tzinfo=UTC))
        descriptor = self._expiring_session(clock, 1)
        configured = config()
        configured["timeouts"]["health_ms"] = 3000
        pipe = _GeneratedClockedPipe(
            health_response,
            clock=clock,
            server_pid=descriptor["pid"],
        )
        client = self._clocked_client(
            clock=clock,
            descriptor=descriptor,
            pipe=pipe,
            configured=configured,
        )
        self.assertEqual(client.health()["kind"], "health")
        self.assertTrue(pipe.write_timeouts)
        self.assertLessEqual(pipe.write_timeouts[0], 1.0)
        self.assertLessEqual(pipe.read_timeouts[0], 1.0)

    def test_post_connect_process_binding_rejects_pid_reuse_and_drift(self) -> None:
        """The connected pipe PID must still name the prepared full instance."""

        descriptor = session()
        prepared = self._process_for(descriptor)
        drift_cases = (
            (
                "pid-reuse",
                ProcessIdentity(
                    pid=prepared.pid,
                    windows_session_id=prepared.windows_session_id,
                    creation_time_100ns=prepared.creation_time_100ns + 1,
                    instance_fingerprint=digest("reused-process"),
                    executable_fingerprint=prepared.executable_fingerprint,
                ),
            ),
            (
                "executable-drift",
                ProcessIdentity(
                    pid=prepared.pid,
                    windows_session_id=prepared.windows_session_id,
                    creation_time_100ns=prepared.creation_time_100ns,
                    instance_fingerprint=prepared.instance_fingerprint,
                    executable_fingerprint=digest("different-host-image"),
                ),
            ),
            (
                "windows-session-drift",
                ProcessIdentity(
                    pid=prepared.pid,
                    windows_session_id=prepared.windows_session_id + 1,
                    creation_time_100ns=prepared.creation_time_100ns,
                    instance_fingerprint=prepared.instance_fingerprint,
                    executable_fingerprint=prepared.executable_fingerprint,
                ),
            ),
        )
        for name, connected in drift_cases:
            with self.subTest(case=name):
                pipe = _GeneratedReusablePipe(
                    health_response,
                    server_pid=descriptor["pid"],
                )
                with mock.patch(
                    "liang_pingfa_review.native_bridge.inspect_process",
                    side_effect=(prepared, prepared, connected),
                ):
                    client = NativeBridgeClient(
                        descriptor,
                        config=config(),
                        transport=pipe,
                    )
                    with self.assertRaises(PipelineError) as raised:
                        client.connect()
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
                self.assertEqual(pipe.frame_count, 0)
                self.assertTrue(pipe.closed)

    def test_process_exit_after_connect_prevents_first_rpc_frame(self) -> None:
        descriptor = session()
        stable = self._process_for(descriptor)
        pipe = _GeneratedReusablePipe(
            health_response,
            server_pid=descriptor["pid"],
        )
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            side_effect=(
                stable,
                stable,
                stable,
                stable,
                PipelineError(
                    ErrorCode.NATIVE_SESSION_INVALID,
                    "generated process exit",
                ),
            ),
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)
            client.connect()
            with self.assertRaises(PipelineError) as raised:
                client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertEqual(pipe.frame_count, 0)
        self.assertTrue(pipe.closed)

    def test_stable_post_connect_process_binds_and_allows_rpc(self) -> None:
        descriptor = session()
        stable = self._process_for(descriptor)
        pipe = _GeneratedReusablePipe(
            health_response,
            server_pid=descriptor["pid"],
        )
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=stable,
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)
            client.connect()
            self.assertEqual(client.bound_process_identity(), stable)
            self.assertEqual(client.health()["kind"], "health")
        self.assertEqual(pipe.frame_count, 1)
        self.assertFalse(client.invalid)

    def test_rpc_single_flight_rejects_race_before_second_frame_write(self) -> None:
        descriptor = session()
        pipe = _GeneratedBlockingPipe(health_response, server_pid=descriptor["pid"])
        first_errors: list[BaseException] = []
        second_errors: list[PipelineError] = []
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process_for(descriptor),
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=pipe)

            def first_call() -> None:
                try:
                    client.health()
                except BaseException as error:
                    first_errors.append(error)

            def second_call() -> None:
                try:
                    client.health()
                except PipelineError as error:
                    second_errors.append(error)

            first = threading.Thread(target=first_call)
            first.start()
            self.assertTrue(pipe.read_started.wait(timeout=5))
            second = threading.Thread(target=second_call)
            second.start()
            second.join(timeout=5)
            pipe.release_read.set()
            first.join(timeout=5)

        self.assertEqual(pipe.frame_count, 1)
        self.assertEqual(len(second_errors), 1)
        self.assertEqual(second_errors[0].code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertEqual(len(first_errors), 1)
        self.assertIsInstance(first_errors[0], PipelineError)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_rpc_guard_releases_after_transport_failure_and_sequential_calls_work(self) -> None:
        descriptor = session()

        class DisconnectingPipe(_GeneratedReusablePipe):
            def read(self, maximum: int, timeout: float) -> bytes:
                self.read_timeouts.append(timeout)
                return b""

        disconnected = DisconnectingPipe(health_response, server_pid=descriptor["pid"])
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process_for(descriptor),
        ):
            client = NativeBridgeClient(
                descriptor,
                config=config(),
                transport=disconnected,
            )
            with self.assertRaises(PipelineError) as raised:
                client.health()
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
            # A released guard must not deadlock a later caller; it observes
            # the permanent invalidation rather than writing another frame.
            with self.assertRaises(PipelineError) as later:
                client.health()
            self.assertEqual(later.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertEqual(disconnected.frame_count, 1)

        reusable = _GeneratedReusablePipe(health_response, server_pid=descriptor["pid"])
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process_for(descriptor),
        ):
            client = NativeBridgeClient(descriptor, config=config(), transport=reusable)
            self.assertEqual(client.health()["kind"], "health")
            self.assertEqual(client.health()["kind"], "health")
        self.assertEqual(reusable.frame_count, 2)
        self.assertFalse(client.invalid)

    def test_each_rpc_uses_its_configured_bounded_deadline(self) -> None:
        descriptor = session()
        configured = config()
        configured["timeouts"].update(
            {
                "health_ms": 1100,
                "session_ms": 1200,
                "document_ms": 1300,
                "inventory_ms": 1400,
                "geometry_ms": 1500,
            }
        )
        def method_response(request: dict) -> dict:
            result: dict
            if request["method"] == "health":
                return health_response(request)
            if request["method"] == "get_session":
                result = {
                    "kind": "session",
                    "bridge_nonce": descriptor["bridge_nonce"],
                    "challenge_response": descriptor["challenge_response"],
                    "adapter": descriptor["adapter"],
                    "plugin": descriptor["plugin"],
                    "host": descriptor["host"],
                    "capabilities": descriptor["capabilities"],
                    "current_document": descriptor["current_document"],
                }
            elif request["method"] == "get_current_document":
                result = {
                    "kind": "document",
                    "current_document": descriptor["current_document"],
                }
            elif request["method"] == "export_inventory":
                result = {"kind": "inventory", "inventory_json": "{}"}
            else:
                result = {"kind": "geometry", "geometry_json": "{}"}
            return {
                "protocol_version": PROTOCOL_VERSION,
                "id": request["id"],
                "result": result,
            }

        pipe = _GeneratedReusablePipe(method_response, server_pid=descriptor["pid"])
        parameters = {
            "health": {"session_id": descriptor["session_id"]},
            "get_session": {
                "session_id": descriptor["session_id"],
                "client_nonce": descriptor["client_nonce"],
                "challenge": descriptor["challenge"],
            },
            "get_current_document": {"session_id": descriptor["session_id"]},
            "export_inventory": {
                "session_id": descriptor["session_id"],
                "expected_document_revision": descriptor["current_document"][
                    "revision_fingerprint"
                ],
            },
            "export_exact_geometry": {
                "session_id": descriptor["session_id"],
                "expected_document_revision": descriptor["current_document"][
                    "revision_fingerprint"
                ],
            },
        }
        expected_seconds = {
            "health": 1.1,
            "get_session": 1.2,
            "get_current_document": 1.3,
            "export_inventory": 1.4,
            "export_exact_geometry": 1.5,
        }
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process_for(descriptor),
        ):
            client = NativeBridgeClient(descriptor, config=configured, transport=pipe)
            cursor = 0
            for method, expected in expected_seconds.items():
                client.request(method, parameters[method])
                observed = pipe.write_timeouts[cursor]
                self.assertLessEqual(observed, expected)
                self.assertGreater(observed, expected - 0.25)
                self.assertGreater(pipe.read_timeouts[cursor], 0)
                cursor += 1

    def test_invalid_timeout_configurations_fail_before_connection(self) -> None:
        for field, value in (
            ("pipe_connect_ms", 0),
            ("health_ms", -1),
            ("session_ms", 10**100),
            ("document_ms", 5001),
            ("inventory_ms", 30001),
            ("geometry_ms", 60001),
            ("write_console_seconds", 121),
            ("readback_console_seconds", 61),
        ):
            with self.subTest(field=field, value=value):
                configured = config()
                configured["timeouts"][field] = value
                with self.assertRaises(PipelineError) as raised:
                    NativeBridgeClient(session(), config=configured)
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

    def test_revision_preservation_policy_requires_explicit_plugin_capability(self) -> None:
        configured = config()
        configured["write_revision_transition"] = "preserved_by_plugin_capability"
        with self.assertRaises(PipelineError) as raised:
            NativeBridgeClient(session(), config=configured)
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)


class NativeBridgeHandshakeTests(unittest.TestCase):
    """Exercise the separate non-persisted health/session preparation path."""

    @staticmethod
    def _process(descriptor: dict[str, Any]) -> ProcessIdentity:
        return ProcessIdentity(
            pid=descriptor["pid"],
            windows_session_id=descriptor["windows_session_id"],
            creation_time_100ns=int(descriptor["process"]["creation_time_100ns"]),
            instance_fingerprint=descriptor["process"]["instance_fingerprint"],
            executable_fingerprint=descriptor["process"]["executable_fingerprint"],
        )

    def _context(
        self,
        descriptor: dict[str, Any],
        *,
        created_at: datetime | None = None,
        mode: str = "read_only",
        expires_at: datetime | None = None,
    ) -> NativeBridgeHandshakeContext:
        return NativeBridgeHandshakeContext(
            prepared_process=self._process(descriptor),
            pipe_name=descriptor["pipe_name"],
            protocol_version=PROTOCOL_VERSION,
            session_id=descriptor["session_id"],
            client_nonce=descriptor["client_nonce"],
            challenge=descriptor["challenge"],
            mode=mode,
            created_at=created_at or datetime.now(UTC),
            monotonic_clock=descriptor["monotonic_clock"],
            monotonic_boot_id=descriptor["monotonic_boot_id"],
            monotonic_issued=descriptor["monotonic_issued"],
            monotonic_expires=descriptor["monotonic_expires"],
            expires_at=expires_at,
        )

    def test_generated_adapter_gate_binds_the_first_client_health_id(self) -> None:
        """The real-pipe double mirrors adapter-owned binding, not an ID mint."""

        selected = session()
        alternate = "native-session-" + "f" * 32
        gate = _GeneratedAdapterSessionGate()
        health = {
            "protocol_version": PROTOCOL_VERSION,
            "id": "1" * 32,
            "method": "health",
            "params": {"session_id": selected["session_id"]},
        }
        self.assertEqual(
            _handshake_response(health, session_gate=gate)["result"]["kind"],
            "health",
        )
        handshake = {
            "protocol_version": PROTOCOL_VERSION,
            "id": "2" * 32,
            "method": "get_session",
            "params": {
                "session_id": selected["session_id"],
                "client_nonce": selected["client_nonce"],
                "challenge": selected["challenge"],
            },
        }
        self.assertEqual(
            _handshake_response(handshake, session_gate=gate)["result"]["kind"],
            "session",
        )
        mismatch = {
            "protocol_version": PROTOCOL_VERSION,
            "id": "3" * 32,
            "method": "health",
            "params": {"session_id": alternate},
        }
        with self.assertRaises(ValueError):
            _handshake_response(mismatch, session_gate=gate)

    @staticmethod
    def _clocked_descriptor(
        clock: _GeneratedClock,
        *,
        created_at: datetime | None = None,
        lifetime_milliseconds: int = (
            native_contracts_module.MAX_NATIVE_SESSION_LIFETIME_MILLISECONDS
        ),
    ) -> dict[str, Any]:
        """Build a signed private descriptor from one fake boot clock."""

        created = created_at if created_at is not None else clock.current
        issued = int(clock.value * 1000)
        lifetime = timedelta(milliseconds=lifetime_milliseconds)
        descriptor = session()
        descriptor["created_at"] = format_utc(created)
        descriptor["expires_at"] = format_utc(
            created + lifetime
        )
        descriptor["monotonic_clock"] = native_contracts_module.NATIVE_SESSION_MONOTONIC_CLOCK
        descriptor["monotonic_boot_id"] = "a" * 32
        descriptor["monotonic_issued"] = str(issued)
        descriptor["monotonic_expires"] = str(
            issued + lifetime_milliseconds
        )
        return attach_integrity(descriptor)

    @staticmethod
    def _bridge_result(
        request: dict[str, Any],
        descriptor: dict[str, Any],
        *,
        session_capabilities: list[str] | None = None,
        omit_bridge_nonce: bool = False,
        invalid_challenge: bool = False,
    ) -> dict[str, Any]:
        if request["method"] == "health":
            result: dict[str, Any] = {
                "kind": "health",
                "protocol_major": 1,
                "protocol_minor": 0,
                "adapter": descriptor["adapter"],
                "plugin": descriptor["plugin"],
                "host": descriptor["host"],
                "capabilities": descriptor["capabilities"],
            }
        elif request["method"] == "get_session":
            parameters = request["params"]
            bridge_nonce = descriptor["bridge_nonce"]
            result = {
                "kind": "session",
                "bridge_nonce": bridge_nonce,
                "challenge_response": (
                    "0" * 64
                    if invalid_challenge
                    else derive_challenge_response(
                        parameters["client_nonce"],
                        parameters["challenge"],
                        bridge_nonce,
                        session_id=parameters["session_id"],
                    )
                ),
                "adapter": descriptor["adapter"],
                "plugin": descriptor["plugin"],
                "host": descriptor["host"],
                "capabilities": session_capabilities
                or descriptor["capabilities"],
                "current_document": descriptor["current_document"],
            }
            if omit_bridge_nonce:
                result.pop("bridge_nonce")
        else:
            raise AssertionError(f"unexpected pre-handshake method: {request['method']}")
        return {
            "protocol_version": PROTOCOL_VERSION,
            "id": request["id"],
            "result": result,
        }

    def _client(
        self,
        *,
        descriptor: dict[str, Any] | None = None,
        server_pid: int | None = None,
        created_at: datetime | None = None,
        mode: str = "read_only",
        clock: _GeneratedClock | None = None,
        expires_at: datetime | None = None,
        session_capabilities: list[str] | None = None,
        omit_bridge_nonce: bool = False,
        invalid_challenge: bool = False,
    ) -> tuple[NativeBridgeHandshakeClient, _GeneratedReusablePipe, list[str], dict[str, Any]]:
        selected = descriptor or session()
        methods: list[str] = []

        def handler(request: dict[str, Any]) -> dict[str, Any]:
            methods.append(request["method"])
            return self._bridge_result(
                request,
                selected,
                session_capabilities=session_capabilities,
                omit_bridge_nonce=omit_bridge_nonce,
                invalid_challenge=invalid_challenge,
            )

        pipe = _GeneratedReusablePipe(
            handler,
            server_pid=server_pid if server_pid is not None else selected["pid"],
        )
        return (
            NativeBridgeHandshakeClient(
                self._context(
                    selected,
                    created_at=(
                        created_at
                        if created_at is not None
                        else (clock.current if clock is not None else None)
                    ),
                    mode=mode,
                    expires_at=expires_at,
                ),
                config=config(),
                transport=pipe,
                session_clock=(
                    clock.session_clock if clock is not None else None
                ),
            ),
            pipe,
            methods,
            selected,
        )

    def test_constructs_a_complete_valid_descriptor_after_ordered_handshake(self) -> None:
        client, pipe, methods, descriptor = self._client()
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process(descriptor),
        ):
            completed = client.complete_session_descriptor()
        self.assertEqual(methods, ["health", "get_session"])
        self.assertEqual(completed["schema_version"], "liang-pingfa/native-bridge-session/v2")
        self.assertEqual(
            completed["config_schema_version"],
            "liang-pingfa/native-adapter-config/v2",
        )
        self.assertEqual(completed["mode"], "read_only")
        self.assertEqual(completed["current_document"], descriptor["current_document"])
        self.assertEqual(validate_native_contract("session", completed), completed)
        self.assertFalse(client.invalid)
        self.assertFalse(pipe.closed)

    def test_prepare_captures_one_boot_uptime_binding_before_setup_work(self) -> None:
        """Preparation owns the sole issuance tick, before any slow setup."""

        created = datetime(2030, 1, 1, tzinfo=UTC)
        reading = NativeSessionClockReading(
            clock=native_contracts_module.NATIVE_SESSION_MONOTONIC_CLOCK,
            boot_id="a" * 32,
            uptime_milliseconds=1_000_000,
        )
        descriptor = session()
        process = self._process(descriptor)
        events: list[str] = []
        contexts: list[NativeBridgeHandshakeContext] = []

        class CapturingHandshake:
            def __init__(
                self,
                context: NativeBridgeHandshakeContext,
                *,
                config: dict[str, Any],
                session_clock: Any,
                component_leases: Any,
            ) -> None:
                self.context = context
                contexts.append(context)
                self.config = config
                self.session_clock = session_clock
                events.append("client")

            def complete_session_descriptor(self) -> dict[str, Any]:
                events.append("complete")
                return {"generated": True}

            def close(self) -> None:
                events.append("close")

        def clock_reader() -> NativeSessionClockReading:
            events.append("clock")
            return reading

        with (
            mock.patch("liang_pingfa_review.native_bridge._require_windows"),
            mock.patch(
                "liang_pingfa_review.native_bridge.utc_now",
                side_effect=lambda: (events.append("utc") or created),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.validate_pipe_name",
                side_effect=lambda _value: events.append("pipe"),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.require_active_native_contract",
                side_effect=lambda _kind, value: (
                    events.append("config") or value
                ),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.acquire_native_installation_leases",
                side_effect=lambda _config: SimpleNamespace(
                    require_bindings=lambda: events.append("installation"),
                    close=lambda: events.append("installation-close"),
                ),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.inspect_process",
                side_effect=lambda _pid: (events.append("process") or process),
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.NativeBridgeHandshakeClient",
                CapturingHandshake,
            ),
        ):
            result = prepare_native_session(
                pid=descriptor["pid"],
                pipe_name=descriptor["pipe_name"],
                config=config(),
                session_clock=clock_reader,
            )
        self.assertEqual(result, {"generated": True})
        self.assertEqual(events[:4], ["config", "utc", "clock", "pipe"])
        self.assertEqual(len(contexts), 1)
        context = contexts[0]
        self.assertEqual(context.created_at, created)
        self.assertEqual(context.monotonic_clock, reading.clock)
        self.assertEqual(context.monotonic_boot_id, reading.boot_id)
        self.assertEqual(context.monotonic_issued, "1000000")
        self.assertEqual(context.monotonic_expires, "1300000")

    def test_preparation_uptime_budget_survives_delay_and_wall_rollback(self) -> None:
        """A 299-second delay leaves at most one second in a new client."""

        origin = datetime(2030, 1, 1, tzinfo=UTC)
        clock = _GeneratedClock(origin)
        descriptor = self._clocked_descriptor(
            clock,
            lifetime_milliseconds=120_000,
        )
        context = self._context(
            descriptor,
            created_at=origin,
            expires_at=origin + timedelta(minutes=2),
        )
        # A short bootstrap-derived session was long-running, then the wall
        # clock rolled back while staying inside the strict UTC interval. The
        # original uptime deadline, rather than a renewed five-minute window,
        # still limits its later RPC.
        clock.advance(119)
        clock.current -= timedelta(seconds=118)
        methods: list[str] = []

        def handshake_response(request: dict[str, Any]) -> dict[str, Any]:
            methods.append(request["method"])
            return self._bridge_result(request, descriptor)

        handshake_pipe = _GeneratedReusablePipe(
            handshake_response,
            server_pid=descriptor["pid"],
        )
        process = self._process(descriptor)
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge.utc_now",
                    side_effect=clock.utc_now,
                )
            )
            stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge.time.monotonic",
                    side_effect=clock.monotonic,
                )
            )
            stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge.inspect_process",
                    return_value=process,
                )
            )
            handshake = NativeBridgeHandshakeClient(
                context,
                config=config(),
                transport=handshake_pipe,
                session_clock=clock.session_clock,
            )
            completed = handshake.complete_session_descriptor()
            self.assertEqual(methods, ["health", "get_session"])
            self.assertEqual(
                completed["monotonic_issued"],
                descriptor["monotonic_issued"],
            )
            self.assertEqual(
                completed["monotonic_expires"],
                descriptor["monotonic_expires"],
            )
            post_handshake_pipe = _GeneratedReusablePipe(
                health_response,
                server_pid=descriptor["pid"],
            )
            completed_client = NativeBridgeClient(
                completed,
                config=config(),
                transport=post_handshake_pipe,
                session_clock=clock.session_clock,
            )
            self.assertLessEqual(
                completed_client._session_deadline - clock.value,
                1.0,
            )
            clock.advance(1)
            with self.assertRaises(PipelineError) as raised:
                completed_client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
        self.assertEqual(post_handshake_pipe.frame_count, 0)

    def test_handshake_after_original_uptime_expiry_publishes_no_descriptor(self) -> None:
        """A short bootstrap budget is never renewed after session RPC."""

        clock = _GeneratedClock(datetime.now(UTC))
        descriptor = self._clocked_descriptor(
            clock,
            lifetime_milliseconds=120_000,
        )
        client, pipe, methods, _selected = self._client(
            descriptor=descriptor,
            clock=clock,
            expires_at=clock.current + timedelta(minutes=2),
        )
        original_get_session = client.get_session

        def delayed_get_session() -> dict[str, Any]:
            result = original_get_session()
            clock.advance(121)
            return result

        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process(descriptor),
        ), mock.patch.object(client, "get_session", side_effect=delayed_get_session):
            with self.assertRaises(PipelineError) as raised:
                client.complete_session_descriptor()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
        self.assertEqual(methods, ["health", "get_session"])
        self.assertEqual(pipe.frame_count, 2)
        self.assertTrue(client.invalid)

    def test_prehandshake_expiry_writes_no_pipe_frame(self) -> None:
        """The original uptime boundary is checked before health can write."""

        clock = _GeneratedClock(datetime.now(UTC))
        descriptor = self._clocked_descriptor(clock)
        client, pipe, methods, _selected = self._client(
            descriptor=descriptor,
            clock=clock,
        )
        clock.advance(301)
        # Keep strict UTC live to prove that the pre-frame rejection comes
        # from the original same-boot deadline rather than wall expiry.
        clock.current -= timedelta(seconds=300)
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process(descriptor),
        ):
            with self.assertRaises(PipelineError) as raised:
                client.health()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
        self.assertEqual(methods, [])
        self.assertEqual(pipe.frame_count, 0)

    def test_reordered_or_early_methods_write_no_frame(self) -> None:
        for label, invoke in (
            ("session-first", lambda client: client.get_session()),
            (
                "generic-session-first",
                lambda client: client.request("get_session", {}),
            ),
            ("early-document", lambda client: client.get_current_document()),
            ("early-inventory", lambda client: client.export_inventory()),
            ("early-geometry", lambda client: client.export_exact_geometry()),
        ):
            with self.subTest(case=label):
                client, pipe, methods, _descriptor = self._client()
                with self.assertRaises(PipelineError) as raised:
                    invoke(client)
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
                self.assertEqual(methods, [])
                self.assertEqual(pipe.frame_count, 0)
                self.assertTrue(client.invalid)
                self.assertTrue(pipe.closed)

    def test_future_context_is_rejected_before_pipe_connect_or_frame_write(self) -> None:
        """Even a one-second future context may not reach the pipe transport."""

        now = datetime(2030, 1, 1, tzinfo=UTC)
        descriptor = session()
        for label, created_at in (
            ("one-second", now + timedelta(seconds=1)),
            ("thirty-days", now + timedelta(days=30)),
            ("exactly-expired", now - timedelta(minutes=5)),
        ):
            with self.subTest(case=label):
                pipe = _GeneratedReusablePipe(
                    lambda request: self._bridge_result(request, descriptor),
                    server_pid=descriptor["pid"],
                )
                with (
                    mock.patch(
                        "liang_pingfa_review.native_bridge.utc_now",
                        return_value=now,
                    ),
                    self.assertRaises(PipelineError) as raised,
                ):
                    NativeBridgeHandshakeClient(
                        self._context(descriptor, created_at=created_at),
                        config=config(),
                        transport=pipe,
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
                self.assertEqual(pipe.frame_count, 0)
                self.assertFalse(pipe.closed)

    def test_session_response_before_health_is_a_terminal_order_error(self) -> None:
        descriptor = session()
        methods: list[str] = []

        def session_first(request: dict[str, Any]) -> dict[str, Any]:
            methods.append(request["method"])
            return self._bridge_result(
                {
                    **request,
                    "method": "get_session",
                    "params": {
                        "session_id": descriptor["session_id"],
                        "client_nonce": descriptor["client_nonce"],
                        "challenge": descriptor["challenge"],
                    },
                },
                descriptor,
            )

        pipe = _GeneratedReusablePipe(session_first, server_pid=descriptor["pid"])
        client = NativeBridgeHandshakeClient(
            self._context(descriptor),
            config=config(),
            transport=pipe,
        )
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process(descriptor),
        ):
            with self.assertRaises(PipelineError) as raised:
                client.complete_session_descriptor()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_PROTOCOL_INVALID)
        self.assertEqual(methods, ["health"])
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_handshake_uses_configured_health_and_session_deadlines(self) -> None:
        descriptor = session()
        configured = config()
        configured["timeouts"].update({"health_ms": 1100, "session_ms": 1200})
        methods: list[str] = []

        def handler(request: dict[str, Any]) -> dict[str, Any]:
            methods.append(request["method"])
            return self._bridge_result(request, descriptor)

        pipe = _GeneratedReusablePipe(handler, server_pid=descriptor["pid"])
        client = NativeBridgeHandshakeClient(
            self._context(descriptor),
            config=configured,
            transport=pipe,
        )
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process(descriptor),
        ):
            client.complete_session_descriptor()
        self.assertEqual(methods, ["health", "get_session"])
        self.assertEqual(len(pipe.write_timeouts), 2)
        self.assertLessEqual(pipe.write_timeouts[0], 1.1)
        self.assertLessEqual(pipe.write_timeouts[1], 1.2)
        self.assertGreater(pipe.read_timeouts[0], 0)
        self.assertGreater(pipe.read_timeouts[1], 0)

    def test_invalid_transcript_missing_nonce_pid_expiry_and_capability_drift_fail_closed(self) -> None:
        cases = (
            (
                "invalid-challenge",
                {"invalid_challenge": True},
                ErrorCode.NATIVE_SESSION_INVALID,
            ),
            (
                "missing-bridge-nonce",
                {"omit_bridge_nonce": True},
                ErrorCode.NATIVE_PROTOCOL_INVALID,
            ),
            (
                "wrong-pid",
                {"server_pid": 4321},
                ErrorCode.NATIVE_PIPE_INVALID,
            ),
            (
                "capability-drift",
                {
                    "session_capabilities": [
                        "read.inventory/v1",
                        "read.exact_geometry/v1",
                        "read.metadata/v1",
                    ]
                },
                ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            ),
        )
        for label, options, expected_code in cases:
            with self.subTest(case=label):
                client, pipe, _methods, descriptor = self._client(**options)
                with mock.patch(
                    "liang_pingfa_review.native_bridge.inspect_process",
                    return_value=self._process(descriptor),
                ):
                    with self.assertRaises(PipelineError) as raised:
                        client.complete_session_descriptor()
                self.assertEqual(raised.exception.code, expected_code)
                self.assertTrue(client.invalid)
                self.assertTrue(pipe.closed)

    def test_bridge_rejects_runtime_package_fingerprint_drift(self) -> None:
        """Health/session plugin identity is the complete package fingerprint."""

        descriptor = session()
        descriptor["plugin"]["fingerprint"] = "f" * 64
        descriptor = attach_integrity(descriptor)
        client, pipe, _methods, _selected = self._client(descriptor=descriptor)
        with mock.patch(
            "liang_pingfa_review.native_bridge.inspect_process",
            return_value=self._process(descriptor),
        ):
            with self.assertRaises(PipelineError) as raised:
                client.complete_session_descriptor()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CAPABILITY_MISMATCH)
        self.assertTrue(client.invalid)
        self.assertTrue(pipe.closed)

    def test_rejects_non_read_only_preparation_before_connecting(self) -> None:
        descriptor = session()
        with self.assertRaises(PipelineError) as raised:
            NativeBridgeHandshakeClient(
                self._context(descriptor, mode="read_write"),
                config=config(),
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)


class NativeComponentAclTests(unittest.TestCase):
    """Exercise interpreted component ACL gates without reading local installs."""

    _trusted = frozenset(
        {
            "S-1-5-21-100",
            "S-1-5-18",
            "S-1-5-32-544",
        }
    )
    _trustedinstaller = (
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )

    @staticmethod
    def _acl(*aces: ComponentDaclAce) -> ComponentDacl:
        return ComponentDacl(owner_sid="S-1-5-21-100", aces=aces)

    def test_component_acl_rejects_untrusted_write_and_accepts_trusted_readonly_model(self) -> None:
        allow = lambda sid, mask, inherited=False: ComponentDaclAce(
            "allow", sid, mask, inherited
        )
        validate_component_dacl(
            self._acl(
                allow("S-1-5-21-100", 0x40000000),
                allow("S-1-5-18", 0x10000000),
                allow("S-1-5-32-544", 0x40000000, inherited=True),
                allow("S-1-1-0", 0x00000001),
                ComponentDaclAce("deny", "S-1-5-32-546", 0x40000000, True),
            ),
            is_directory=False,
            trusted_sids=self._trusted,
        )
        for sid, mask, directory in (
            ("S-1-1-0", 0x40000000, False),
            ("S-1-5-11", 0x00000002, False),
            ("S-1-5-32-545", 0x00000040, True),
            ("S-1-5-32-546", 0x00010000, True),
            ("S-1-5-21-999", 0x00080000, False),
        ):
            with self.subTest(sid=sid, mask=mask):
                with self.assertRaises(PipelineError) as raised:
                    validate_component_dacl(
                        self._acl(allow(sid, mask, inherited=True)),
                        is_directory=directory,
                        trusted_sids=self._trusted,
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

    def test_directory_rights_are_not_file_append_rights(self) -> None:
        """Map generic rights first and honor current-object ACE flags."""

        allow = lambda sid, mask, *, flags=0, inherited=False: ComponentDaclAce(
            "allow",
            sid,
            mask,
            inherited,
            flags,
        )
        authenticated_users = "S-1-5-11"
        users = "S-1-5-32-545"

        # These directory rights create only new children. They cannot
        # overwrite the retained existing component and must not be treated
        # as FILE_APPEND_DATA/file-write authority.
        for label, mask in (
            ("add-subdirectory", 0x00000004),
            ("add-file", 0x00000002),
        ):
            with self.subTest(label=label):
                validate_component_dacl(
                    self._acl(allow(authenticated_users, mask)),
                    is_directory=True,
                    trusted_sids=self._trusted,
                )

        # The generic mapping includes object EA/attribute mutation, so
        # GENERIC_WRITE remains unsafe even though its low bits include the
        # safe child-add aliases above.
        for label, mask in (
            ("delete-child", 0x00000040),
            ("directory-attributes", 0x00000100),
            ("generic-write", 0x40000000),
            ("maximum-allowed", 0x02000000),
        ):
            with self.subTest(label=label):
                with self.assertRaises(PipelineError) as raised:
                    validate_component_dacl(
                        self._acl(allow(users, mask)),
                        is_directory=True,
                        trusted_sids=self._trusted,
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

        for label, mask in (
            ("write-data", 0x00000002),
            ("append-data", 0x00000004),
            ("write-attributes", 0x00000100),
            ("delete", 0x00010000),
        ):
            with self.subTest(label=label):
                with self.assertRaises(PipelineError) as raised:
                    validate_component_dacl(
                        self._acl(allow(users, mask)),
                        is_directory=False,
                        trusted_sids=self._trusted,
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

        # An inherit-only writer does not apply to the currently retained
        # parent. An inherited writer without INHERIT_ONLY fully applies and
        # must fail just like an explicit writer.
        validate_component_dacl(
            self._acl(allow(users, 0x40000000, flags=0x08)),
            is_directory=True,
            trusted_sids=self._trusted,
        )
        with self.assertRaises(PipelineError) as raised:
            validate_component_dacl(
                self._acl(allow(users, 0x40000000, flags=0x10)),
                is_directory=True,
                trusted_sids=self._trusted,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

    def test_component_acl_fails_closed_on_owner_ace_reader_or_hash_drift(self) -> None:
        with self.assertRaises(PipelineError):
            validate_component_dacl(
                ComponentDacl(owner_sid="S-1-5-21-999", aces=()),
                is_directory=False,
                trusted_sids=self._trusted,
            )
        with self.assertRaises(PipelineError):
            validate_component_dacl(
                self._acl(ComponentDaclAce("unknown", "S-1-5-21-100", 0, False)),
                is_directory=False,
                trusted_sids=self._trusted,
            )
        with (
            mock.patch("liang_pingfa_review.native_bridge._require_windows"),
            mock.patch(
                "liang_pingfa_review.native_bridge.ctypes.WinDLL",
                side_effect=OSError("generated"),
            ),
        ):
            with self.assertRaises(OwnershipCleanupError):
                _read_component_dacl(SimpleNamespace(handle=1))

        identity = FileIdentity("test", 1, 2, 3)
        original = OwnedPathBinding(
            path=Path("generated.dll"),
            identity=identity,
            byte_size=1,
            sha256="a" * 64,
            is_directory=False,
        )
        drifted = OwnedPathBinding(
            path=Path("generated.dll"),
            identity=identity,
            byte_size=1,
            sha256="b" * 64,
            is_directory=False,
        )

        class Opened:
            def capture_binding(self) -> OwnedPathBinding:
                return drifted

        class Lease:
            binding = original
            owned = Opened()
            chain = SimpleNamespace(components=())

            def require_binding(self) -> None:
                pass

            def close(self) -> None:
                pass

        leases = NativeInstallationLeases(
            leases={"core_console": Lease()},
            expected_hashes={"core_console": "a" * 64},
            acl_reader=lambda _opened: self._acl(),
            trusted_sids=self._trusted,
        )
        with self.assertRaises(PipelineError) as raised:
            leases.require_bindings()
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

    def test_trustedinstaller_owner_accepts_only_safe_program_files_chain(self) -> None:
        """The exact Modules Installer owner is narrow, not a write bypass."""

        allow = lambda sid, mask, inherited=False: ComponentDaclAce(
            "allow", sid, mask, inherited
        )
        safe = ComponentDacl(
            owner_sid=self._trustedinstaller,
            aces=(
                allow("S-1-5-18", 0x10000000, inherited=True),
                allow("S-1-5-32-544", 0x10000000, inherited=True),
                # Program Files commonly grants public read access inherited
                # from its ancestor; readonly access is intentionally safe.
                allow("S-1-1-0", 0x00000001, inherited=True),
            ),
        )
        # Exercise a Program Files-style root → vendor → component chain,
        # plus the executable itself, with the exact trusted owner.
        for is_directory in (True, True, True, False):
            with self.subTest(is_directory=is_directory):
                validate_component_dacl(
                    safe,
                    is_directory=is_directory,
                    trusted_sids=self._trusted,
                )

        unsafe_aces = (
            (
                "everyone-write",
                allow("S-1-1-0", 0x00000002, False),
                False,
            ),
            (
                "users-write",
                allow("S-1-5-32-545", 0x00000002, False),
                False,
            ),
            (
                "inherited-users-delete-child",
                allow("S-1-5-32-545", 0x00000040, True),
                True,
            ),
        )
        for name, ace, is_directory in unsafe_aces:
            with self.subTest(name=name):
                with self.assertRaises(PipelineError) as raised:
                    validate_component_dacl(
                        ComponentDacl(
                            owner_sid=self._trustedinstaller,
                            aces=(ace,),
                        ),
                        is_directory=is_directory,
                        trusted_sids=self._trusted,
                    )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

        with self.assertRaises(PipelineError) as raised:
            validate_component_dacl(
                ComponentDacl(
                    owner_sid="S-1-5-80-123-456-789-1011-1213",
                    aces=(),
                ),
                is_directory=False,
                trusted_sids=self._trusted,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

    @unittest.skipUnless(os.name == "nt", "requires a local Windows DACL")
    def test_normal_windows_root_and_program_files_dacls_are_accepted(self) -> None:
        """Read only generated host metadata; never inspect installed files."""

        system_drive = os.environ.get("SystemDrive", "C:")
        paths = [Path(system_drive + "\\")]
        program_files = Path(
            os.environ.get("ProgramFiles", system_drive + "\\Program Files")
        )
        if program_files.is_dir():
            paths.append(program_files)
        backend = platform_backend(require_windows=True)
        trusted = frozenset(
            {
                current_user_sid(),
                "S-1-5-18",
                "S-1-5-32-544",
            }
        )
        for path in paths:
            with self.subTest(path=path):
                chain = acquire_lexical_directory_chain(path, backend)
                try:
                    for component in chain.components:
                        validate_component_dacl(
                            _read_component_dacl(component.owned),
                            is_directory=True,
                            trusted_sids=trusted,
                            allow_trustedinstaller_owner=True,
                        )
                finally:
                    chain.close()


class NativeSessionDescriptorTests(unittest.TestCase):
    """Exercise private descriptor and post-claim cleanup with generated mocks."""

    _trusted = frozenset({"S-1-5-21-100", "S-1-5-18", "S-1-5-32-544"})

    def setUp(self) -> None:
        self.parent = Path("/generated/private-session-root")
        self.path = self.parent / "session.json"

    def _writer_patches(self, backend: _GeneratedSessionBackend):
        chain = _GeneratedDirectoryChain(self.parent)
        return (
            mock.patch("liang_pingfa_review.native_bridge._require_windows"),
            mock.patch(
                "liang_pingfa_review.native_bridge.lexical_absolute_path",
                return_value=self.path,
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.acquire_lexical_directory_chain",
                return_value=chain,
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.validate_private_staging_ancestry"
            ),
        )

    def _consume_patches(
        self,
        backend: _GeneratedSessionBackend,
    ):
        chain = _GeneratedDirectoryChain(self.parent)
        return (
            mock.patch("liang_pingfa_review.native_bridge._require_windows"),
            mock.patch(
                "liang_pingfa_review.native_bridge.lexical_absolute_path",
                return_value=self.path,
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.platform_backend",
                return_value=backend,
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.acquire_lexical_directory_chain",
                return_value=chain,
            ),
        )

    def test_private_descriptor_uses_exclusive_dacl_file_api_without_secret_event(self) -> None:
        backend = _GeneratedSessionBackend(self.parent)
        dacl = ComponentDacl(
            owner_sid="S-1-5-21-100",
            aces=(
                ComponentDaclAce("allow", "S-1-5-21-100", 0x10000000, False),
                ComponentDaclAce("allow", "S-1-5-18", 0x10000000, False),
            ),
        )
        descriptor = session()
        with ExitStack() as stack:
            for patcher in self._writer_patches(backend):
                stack.enter_context(patcher)
            secure = stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge.secure_private_staging_file",
                )
            )
            result = write_private_native_session_descriptor(
                self.path,
                descriptor,
                backend=backend,
                acl_reader=lambda _opened: dacl,
                trusted_parent_sids=self._trusted,
            )
        assert backend.created is not None
        self.assertEqual(result, self.path)
        self.assertEqual(secure.call_count, 2)
        self.assertEqual(backend.private_create_calls, 1)
        self.assertEqual(backend.public_create_calls, 0)
        self.assertTrue(backend.created.closed)
        self.assertFalse(backend.created.delete_requested)
        self.assertEqual(
            backend.created.payload,
            canonical_json_bytes(descriptor) + b"\n",
        )
        self.assertNotIn(session()["pipe_name"], str(result))
        self.assertNotIn(session()["client_nonce"], str(result))

    def test_private_descriptor_rejects_broad_and_inherited_parent_writers(self) -> None:
        for ace in (
            ComponentDaclAce("allow", "S-1-1-0", 0x40000000, False),
            ComponentDaclAce("allow", "S-1-5-32-545", 0x00000040, True),
        ):
            with self.subTest(ace=ace):
                backend = _GeneratedSessionBackend(self.parent)
                dacl = ComponentDacl(owner_sid="S-1-5-21-100", aces=(ace,))
                with ExitStack() as stack:
                    for patcher in self._writer_patches(backend):
                        stack.enter_context(patcher)
                    with self.assertRaises(PipelineError) as raised:
                        write_private_native_session_descriptor(
                            self.path,
                            session(),
                            backend=backend,
                            acl_reader=lambda _opened: dacl,
                            trusted_parent_sids=self._trusted,
                        )
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)
                self.assertIsNone(backend.created)

    def test_private_descriptor_dacl_failures_delete_the_held_secret(self) -> None:
        dacl = ComponentDacl(owner_sid="S-1-5-21-100", aces=())
        for name, side_effect in (
            ("apply", OwnershipCleanupError("generated apply failure")),
            (
                "query",
                [None, OwnershipCleanupError("generated query failure")],
            ),
        ):
            with self.subTest(name=name):
                backend = _GeneratedSessionBackend(self.parent)
                with ExitStack() as stack:
                    for patcher in self._writer_patches(backend):
                        stack.enter_context(patcher)
                    stack.enter_context(
                        mock.patch(
                            "liang_pingfa_review.native_bridge.secure_private_staging_file",
                            side_effect=side_effect,
                        )
                    )
                    with self.assertRaises(PipelineError) as raised:
                        write_private_native_session_descriptor(
                            self.path,
                            session(),
                            backend=backend,
                            acl_reader=lambda _opened: dacl,
                            trusted_parent_sids=self._trusted,
                        )
                assert backend.created is not None
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
                self.assertTrue(backend.created.delete_requested)
                self.assertTrue(backend.created.closed)
                self.assertNotIn(session()["pipe_name"], str(raised.exception))
                self.assertNotIn(session()["bridge_nonce"], str(raised.exception))

    def test_descriptor_publication_rejects_original_uptime_expiry(self) -> None:
        """Private file creation cannot publish a descriptor after expiry."""

        backend = _GeneratedSessionBackend(self.parent)
        descriptor = session()
        descriptor["monotonic_clock"] = native_contracts_module.NATIVE_SESSION_MONOTONIC_CLOCK
        descriptor["monotonic_boot_id"] = "a" * 32
        descriptor["monotonic_issued"] = "1000000"
        descriptor["monotonic_expires"] = "1300000"
        descriptor = attach_integrity(descriptor)
        expired_clock = _GeneratedClock(datetime.now(UTC), monotonic=1_300.0)
        with ExitStack() as stack:
            for patcher in self._writer_patches(backend):
                stack.enter_context(patcher)
            with self.assertRaises(PipelineError) as raised:
                write_private_native_session_descriptor(
                    self.path,
                    descriptor,
                    backend=backend,
                    trusted_parent_sids=self._trusted,
                    session_clock=expired_clock.session_clock,
                )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
        self.assertIsNone(backend.created)

    def test_post_rename_cleanup_handles_binding_parse_operation_and_replacement_failures(self) -> None:
        descriptor = canonical_json_bytes(session()) + b"\n"
        cases = (
            (
                "binding",
                _GeneratedOwnedSecretFile(
                    self.path,
                    payload=descriptor,
                    fail_capture_after_rename=True,
                ),
                "pipeline",
                False,
            ),
            (
                "parse",
                _GeneratedOwnedSecretFile(self.path, payload=b"{}"),
                "pipeline",
                False,
            ),
            (
                "operation",
                _GeneratedOwnedSecretFile(self.path, payload=descriptor),
                "operation",
                False,
            ),
            (
                "replacement",
                _GeneratedOwnedSecretFile(self.path, payload=descriptor),
                "normal",
                True,
            ),
            (
                "normal",
                _GeneratedOwnedSecretFile(self.path, payload=descriptor),
                "normal",
                False,
            ),
        )
        for name, opened, mode, replacement in cases:
            with self.subTest(name=name):
                backend = _GeneratedSessionBackend(
                    self.parent,
                    existing=opened,
                    replacement_survives_cleanup=replacement,
                )
                with ExitStack() as stack:
                    for patcher in self._consume_patches(backend):
                        stack.enter_context(patcher)
                    if mode == "operation":
                        with self.assertRaisesRegex(RuntimeError, "generated operation"):
                            with consume_native_session(self.path):
                                raise RuntimeError("generated operation")
                    elif mode == "pipeline":
                        with self.assertRaises(PipelineError) as raised:
                            with consume_native_session(self.path):
                                pass
                        self.assertEqual(
                            raised.exception.code,
                            ErrorCode.NATIVE_SESSION_INVALID,
                        )
                    elif replacement:
                        with self.assertRaises(PipelineError) as raised:
                            with consume_native_session(self.path):
                                pass
                        self.assertEqual(
                            raised.exception.code,
                            ErrorCode.NATIVE_SESSION_INVALID,
                        )
                    else:
                        with consume_native_session(self.path) as consumed:
                            self.assertEqual(consumed["session_id"], session()["session_id"])
                self.assertTrue(opened.renamed)
                self.assertTrue(opened.delete_requested)
                self.assertTrue(opened.closed)
                self.assertEqual(
                    backend.path_exists(opened.path),
                    replacement,
                )

    def test_claim_rejects_re_signed_future_descriptor_after_atomic_claim(self) -> None:
        """Integrity cannot make a future descriptor usable after persistence."""

        future = session()
        created = datetime.now(UTC) + timedelta(days=30)
        future["created_at"] = format_utc(created)
        future["expires_at"] = format_utc(created + timedelta(minutes=5))
        future = attach_integrity(future)
        opened = _GeneratedOwnedSecretFile(
            self.path,
            payload=canonical_json_bytes(future) + b"\n",
        )
        backend = _GeneratedSessionBackend(self.parent, existing=opened)
        with ExitStack() as stack:
            for patcher in self._consume_patches(backend):
                stack.enter_context(patcher)
            with self.assertRaises(PipelineError) as raised:
                with consume_native_session(self.path):
                    self.fail("future descriptor reached a consumer")
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertTrue(opened.renamed)
        self.assertTrue(opened.delete_requested)
        self.assertTrue(opened.closed)

    def test_claimed_descriptor_in_another_process_keeps_original_uptime_remaining(self) -> None:
        """A same-boot consumer receives remaining time, never a new window."""

        issuer_clock = _GeneratedClock(datetime.now(UTC), monotonic=1_000.0)
        consumer_clock = _GeneratedClock(datetime.now(UTC), monotonic=1_299.0)
        descriptor = session()
        descriptor["monotonic_clock"] = native_contracts_module.NATIVE_SESSION_MONOTONIC_CLOCK
        descriptor["monotonic_boot_id"] = "a" * 32
        descriptor["monotonic_issued"] = "1000000"
        descriptor["monotonic_expires"] = "1300000"
        descriptor = attach_integrity(descriptor)
        self.assertEqual(
            issuer_clock.session_clock().boot_id,
            consumer_clock.session_clock().boot_id,
        )
        opened = _GeneratedOwnedSecretFile(
            self.path,
            payload=canonical_json_bytes(descriptor) + b"\n",
        )
        backend = _GeneratedSessionBackend(self.parent, existing=opened)
        with ExitStack() as stack:
            for patcher in self._consume_patches(backend):
                stack.enter_context(patcher)
            with consume_native_session(
                self.path,
                session_clock=consumer_clock.session_clock,
            ) as consumed:
                with mock.patch(
                    "liang_pingfa_review.native_bridge.time.monotonic",
                    side_effect=consumer_clock.monotonic,
                ):
                    client = NativeBridgeClient(
                        consumed,
                        config=config(),
                        transport=_GeneratedReusablePipe(
                            health_response,
                            server_pid=consumed["pid"],
                        ),
                        session_clock=consumer_clock.session_clock,
                    )
                    self.assertLessEqual(
                        client._session_deadline - consumer_clock.value,
                        1.0,
                    )
        self.assertTrue(opened.delete_requested)
        self.assertTrue(opened.closed)

    def test_claimed_descriptor_basename_is_ascii_casefolded_only(self) -> None:
        """NTFS casing cannot turn a consumed descriptor back into a candidate."""

        canonical = (
            ".liang-pingfa-native-session-claimed-" + "a" * 64 + ".json"
        )
        stale_variants = (
            canonical.upper(),
            ".LiAnG-PiNgFa-NaTiVe-SeSsIoN-ClAiMeD-"
            + "Ab" * 32
            + ".JsOn",
        )
        for name in stale_variants:
            with self.subTest(stale=name):
                candidate = self.parent / name
                with (
                    mock.patch("liang_pingfa_review.native_bridge._require_windows"),
                    mock.patch(
                        "liang_pingfa_review.native_bridge.lexical_absolute_path",
                        return_value=candidate,
                    ),
                    self.assertRaises(PipelineError) as raised,
                ):
                    with consume_native_session(candidate):
                        pass
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.NATIVE_SESSION_INVALID,
                )

                backend = _GeneratedSessionBackend(self.parent)
                with (
                    mock.patch("liang_pingfa_review.native_bridge._require_windows"),
                    mock.patch(
                        "liang_pingfa_review.native_bridge.lexical_absolute_path",
                        return_value=candidate,
                    ),
                    self.assertRaises(PipelineError) as write_raised,
                ):
                    write_private_native_session_descriptor(
                        candidate,
                        session(),
                        backend=backend,
                        trusted_parent_sids=self._trusted,
                    )
                self.assertEqual(
                    write_raised.exception.code,
                    ErrorCode.NATIVE_SESSION_INVALID,
                )
                self.assertIsNone(backend.created)

        # A normal unclaimed name and a Unicode lookalike are not stale
        # descriptors. The latter must not be transformed into an ASCII
        # claimed name by Unicode regex case matching.
        for name in (
            "normal-session.json",
            ".l\u0131ang-pingfa-native-session-claimed-" + "a" * 64 + ".json",
        ):
            with self.subTest(unclaimed=name):
                self.path = self.parent / name
                opened = _GeneratedOwnedSecretFile(
                    self.path,
                    payload=canonical_json_bytes(session()) + b"\n",
                )
                backend = _GeneratedSessionBackend(self.parent, existing=opened)
                with ExitStack() as stack:
                    for patcher in self._consume_patches(backend):
                        stack.enter_context(patcher)
                    with consume_native_session(self.path) as consumed:
                        self.assertEqual(consumed["session_id"], session()["session_id"])
                self.assertTrue(opened.renamed)
                self.assertEqual(opened.path.name, opened.path.name.casefold())
                self.assertTrue(
                    opened.path.name.startswith(
                        ".liang-pingfa-native-session-claimed-"
                    )
                )


class NativeBootstrapDescriptorTests(unittest.TestCase):
    """Exercise the distinct one-use C# bootstrap claim namespace."""

    def setUp(self) -> None:
        self.parent = Path("/generated/private-bootstrap-root")
        self.path = self.parent / "bootstrap.json"

    def _consume_patches(self, backend: _GeneratedSessionBackend):
        chain = _GeneratedDirectoryChain(self.parent)
        return (
            mock.patch("liang_pingfa_review.native_bridge._require_windows"),
            mock.patch(
                "liang_pingfa_review.native_bridge.lexical_absolute_path",
                return_value=self.path,
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.platform_backend",
                return_value=backend,
            ),
            mock.patch(
                "liang_pingfa_review.native_bridge.acquire_lexical_directory_chain",
                return_value=chain,
            ),
        )

    @staticmethod
    def _bootstrap(
        *,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict:
        issued = (issued_at or datetime.now(UTC)).replace(microsecond=0)
        expires = (expires_at or issued + timedelta(minutes=2)).replace(
            microsecond=0
        )
        return attach_integrity(
            {
                "schema_version": "liang-pingfa/native-bridge-bootstrap/v1",
                "protocol": {
                    "version": "liang-pingfa/native-bridge/v1",
                    "major": 1,
                    "minor": 0,
                },
                "pid": 1234,
                "pipe": r"\\.\pipe\liang-pingfa-native-a1b2c3d4e5f6g7h8",
                "mode": "read_only",
                "adapter": {
                    "id": "liang-pingfa-autocad-adapter",
                    "profile": "autocad2025",
                    "version": "2.0.0",
                },
                "plugin": {
                    "id": "liang-pingfa-autocad-plugin",
                    "version": "2.0.0",
                    "fingerprint": "a" * 64,
                },
                "host": {
                    "product": "autocad",
                    "release": "2025",
                    "runtime": "net8",
                    "mode": "full_host",
                },
                "process": {
                    "windows_session_id": 1,
                    "creation_time_100ns": "123456789",
                    "instance_fingerprint": "b" * 64,
                    "executable_fingerprint": "c" * 64,
                },
                "capabilities": [
                    "create_review_marker/v1",
                    "read.exact_geometry/v1",
                    "read.inventory/v1",
                    "translate_dbtext/v1",
                ],
                "bootstrap": {
                    "nonce": "d" * 43,
                    "config_sha256": "e" * 64,
                    "issued_at": format_utc(issued),
                    "expires_at": format_utc(expires),
                },
            }
        )

    def test_claim_is_one_use_canonical_and_private(self) -> None:
        bootstrap = self._bootstrap()
        opened = _GeneratedOwnedSecretFile(
            self.path,
            payload=canonical_json_bytes(bootstrap),
        )
        backend = _GeneratedSessionBackend(self.parent, existing=opened)
        with ExitStack() as stack:
            for patcher in self._consume_patches(backend):
                stack.enter_context(patcher)
            with consume_native_bridge_bootstrap(self.path) as claimed:
                self.assertEqual(claimed, bootstrap)
        self.assertTrue(opened.renamed)
        self.assertTrue(opened.delete_requested)
        self.assertTrue(opened.closed)
        self.assertFalse(backend.path_exists(opened.path))
        self.assertTrue(
            opened.path.name.startswith(".liang-pingfa-native-bootstrap-claimed-")
        )

    def test_concrete_bootstrap_rejects_forged_tssd_profile(self) -> None:
        forged = self._bootstrap()
        forged["adapter"]["profile"] = "tssd2025"
        with self.assertRaises(PipelineError):
            validate_native_contract("bootstrap", attach_integrity(forged))

    def test_concrete_bootstrap_rejects_mismatched_autocad_host_identity(self) -> None:
        forged = self._bootstrap()
        forged["host"]["release"] = "2024"
        forged["host"]["runtime"] = "net48"
        with self.assertRaises(PipelineError):
            validate_native_contract("bootstrap", attach_integrity(forged))

    def test_replay_stale_and_expired_bootstraps_fail_before_pipe_use(self) -> None:
        cases = (
            (
                "expired",
                self._bootstrap(
                    issued_at=datetime.now(UTC) - timedelta(minutes=3),
                    expires_at=datetime.now(UTC) - timedelta(minutes=1),
                ),
                self.path,
            ),
            (
                "stale-claimed-name",
                self._bootstrap(),
                self.parent
                / (".liang-pingfa-native-bootstrap-claimed-" + "a" * 64 + ".json"),
            ),
        )
        for label, bootstrap, candidate in cases:
            with self.subTest(label=label):
                opened = _GeneratedOwnedSecretFile(
                    candidate,
                    payload=canonical_json_bytes(bootstrap),
                )
                backend = _GeneratedSessionBackend(self.parent, existing=opened)
                self.path = candidate
                with ExitStack() as stack:
                    for patcher in self._consume_patches(backend):
                        stack.enter_context(patcher)
                    with self.assertRaises(PipelineError) as raised:
                        with consume_native_bridge_bootstrap(candidate):
                            self.fail("invalid bootstrap reached a consumer")
                self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
                if label == "expired":
                    self.assertTrue(opened.renamed)
                    self.assertTrue(opened.delete_requested)
                else:
                    self.assertFalse(opened.renamed)
        self.path = self.parent / "bootstrap.json"

    def test_oversized_bootstrap_fails_before_hash_parse_or_pipe_use(self) -> None:
        opened = _GeneratedOwnedSecretFile(
            self.path,
            payload=b"x" * (native_bridge_module._BOOTSTRAP_MAX_BYTES + 1),
        )
        backend = _GeneratedSessionBackend(self.parent, existing=opened)
        with ExitStack() as stack:
            for patcher in self._consume_patches(backend):
                stack.enter_context(patcher)
            with self.assertRaises(PipelineError) as raised:
                with consume_native_bridge_bootstrap(self.path):
                    self.fail("oversized bootstrap reached a consumer")
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertFalse(opened.renamed)
        self.assertTrue(opened.closed)


@unittest.skipUnless(os.name == "nt", "Windows named-pipe APIs are Windows-only")
class WindowsNamedPipeTests(unittest.TestCase):
    """Exercise production overlapped I/O against generated local pipes only."""

    @staticmethod
    def _pipe_name() -> str:
        token = secrets.token_hex(16)
        return (
            chr(92) * 2
            + "."
            + chr(92)
            + "pipe"
            + chr(92)
            + f"liang-pingfa-native-a1b2c3d4{token}"
        )

    @staticmethod
    def _environment() -> tuple[Path, dict[str, str]]:
        root = Path(__file__).parent.parent
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(root / "src"), str(root), environment.get("PYTHONPATH", "")]
        )
        return root, environment

    def _start_server(
        self,
        pipe_name: str,
        scenario: str | None = None,
        *,
        payload_size: int = 0,
    ) -> subprocess.Popen[str]:
        root, environment = self._environment()
        arguments = [sys.executable, "-m", "tests.support.mock_native_bridge", pipe_name]
        if scenario is not None:
            arguments.append(scenario)
            if payload_size:
                arguments.append(str(payload_size))
        process = subprocess.Popen(
            arguments,
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(process.stdout.readline().strip(), "READY")
        return process

    def _assert_server_clean(self, process: subprocess.Popen[str]) -> None:
        process.wait(timeout=10)
        stderr = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        if process.returncode not in (0, None):
            self.fail(stderr)

    def test_connects_to_explicit_local_server_pid_and_frames(self) -> None:
        pipe_name = self._pipe_name()
        process = self._start_server(pipe_name)
        try:
            started = time.monotonic()
            transport = WindowsNamedPipe.connect(pipe_name, timeout_seconds=5)
            try:
                self.assertEqual(transport.server_pid, process.pid)
                request = {
                    "protocol_version": PROTOCOL_VERSION,
                    "id": "c" * 32,
                    "method": "health",
                    "params": {"session_id": "native-session-" + "c" * 32},
                }
                frame = encode_frame(request, maximum=64 * 1024)
                write_all(
                    transport.write,
                    frame,
                    deadline=__import__("time").monotonic() + 5,
                )
                response = read_frame(
                    transport.read,
                    maximum=256 * 1024,
                    deadline=__import__("time").monotonic() + 5,
                )
                self.assertEqual(response["id"], request["id"])
            finally:
                transport.close()
            # Generous CI bound: this verifies that normal overlapped framing
            # does not inherit a cancellation or polling delay.
            self.assertLess(time.monotonic() - started, 3.0)
        finally:
            self._assert_server_clean(process)

    def test_prepare_real_generated_pipe_then_consume_descriptor(self) -> None:
        """Exercise actual prepare, descriptor DACL/claim, and post-claim RPCs."""

        pipe_name = self._pipe_name()
        process = self._start_server(pipe_name, "handshake-sequence")
        try:
            with tempfile.TemporaryDirectory() as temporary:
                descriptor_path = Path(temporary) / "prepared-native-session.json"
                # The generated pipe is a real process and actual Windows
                # named pipe. Component installation is deliberately outside
                # this source-free protocol test and is independently tested
                # by the retained-installation/DACL suite.
                component_leases = mock.Mock()
                with mock.patch(
                    "liang_pingfa_review.native_bridge.acquire_native_installation_leases",
                    return_value=component_leases,
                ):
                    descriptor = prepare_native_session(
                        pid=process.pid,
                        pipe_name=pipe_name,
                        config=config(),
                    )
                self.assertEqual(
                    descriptor["pid"],
                    process.pid,
                )
                self.assertEqual(
                    descriptor["current_document"]["saved"],
                    True,
                )
                write_private_native_session_descriptor(descriptor_path, descriptor)
                # The generated server creates its second pipe instance after
                # the preparation client closes the first one.
                time.sleep(0.1)
                with consume_native_session(descriptor_path) as consumed:
                    client = NativeBridgeClient(
                        consumed,
                        config=config(),
                        component_leases=component_leases,
                    )
                    try:
                        self.assertEqual(client.health()["kind"], "health")
                        self.assertEqual(
                            client.get_current_document()["current_document"],
                            consumed["current_document"],
                        )
                    finally:
                        client.close()
                self.assertFalse(descriptor_path.exists())
        finally:
            self._assert_server_clean(process)

    def test_real_delayed_pipe_cancels_at_session_expiry(self) -> None:
        """A real generated pipe cannot return health after its session ends."""

        pipe_name = self._pipe_name()
        process = self._start_server(pipe_name, "delayed-health")
        try:
            origin = datetime.now(UTC).replace(microsecond=0)
            monotonic_origin = time.monotonic()
            descriptor = session()
            identity = ProcessIdentity(
                pid=process.pid,
                windows_session_id=1,
                creation_time_100ns=987654321,
                instance_fingerprint=digest("generated-delayed-pipe-instance"),
                executable_fingerprint=digest("generated-delayed-pipe-image"),
            )
            descriptor["pid"] = identity.pid
            descriptor["windows_session_id"] = identity.windows_session_id
            descriptor["pipe_name"] = pipe_name
            descriptor["process"] = {
                "instance_fingerprint": identity.instance_fingerprint,
                "creation_time_100ns": str(identity.creation_time_100ns),
                "executable_fingerprint": identity.executable_fingerprint,
            }
            descriptor["created_at"] = format_utc(origin - timedelta(seconds=1))
            descriptor["expires_at"] = format_utc(origin + timedelta(seconds=1))
            descriptor = attach_integrity(descriptor)

            def advancing_utc_now() -> datetime:
                return origin + timedelta(
                    seconds=time.monotonic() - monotonic_origin
                )

            with (
                mock.patch(
                    "liang_pingfa_review.native_bridge.inspect_process",
                    return_value=identity,
                ),
                mock.patch(
                    "liang_pingfa_review.native_bridge.utc_now",
                    side_effect=advancing_utc_now,
                ),
            ):
                client = NativeBridgeClient(
                    descriptor,
                    config=config(),
                    component_leases=mock.Mock(),
                )
                with self.assertRaises(PipelineError) as raised:
                    client.health()
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_EXPIRED)
            self.assertTrue(client.invalid)
        finally:
            self._assert_server_clean(process)

    def test_tiny_buffer_full_write_and_partial_read_are_bounded(self) -> None:
        """A tiny generated pipe proves full writes and actual short reads."""

        payload = b"x" * (16 * 1024)
        pipe_name = self._pipe_name()
        writer = self._start_server(
            pipe_name,
            "slow-read",
            payload_size=len(payload),
        )
        try:
            transport = WindowsNamedPipe.connect(pipe_name, timeout_seconds=5)
            try:
                started = time.monotonic()
                write_all(
                    transport.write,
                    payload,
                    deadline=time.monotonic() + 2.0,
                )
                self.assertEqual(transport.read(2, 2.0), b"OK")
                self.assertLess(time.monotonic() - started, 3.0)
            finally:
                transport.close()
        finally:
            self._assert_server_clean(writer)

        pipe_name = self._pipe_name()
        reader = self._start_server(pipe_name, "partial-read")
        try:
            transport = WindowsNamedPipe.connect(pipe_name, timeout_seconds=5)
            try:
                started = time.monotonic()
                chunks = [transport.read(6, 1.0)]
                while sum(map(len, chunks)) < 6:
                    chunks.append(transport.read(6 - sum(map(len, chunks)), 1.0))
                self.assertEqual(b"".join(chunks), b"abcdef")
                self.assertLess(len(chunks[0]), 6)
                self.assertLess(time.monotonic() - started, 2.0)
            finally:
                transport.close()
        finally:
            self._assert_server_clean(reader)

    def test_nonreading_server_cancels_overlapped_write_at_deadline(self) -> None:
        """A server that never reads cannot hold WriteFile beyond its deadline."""

        pipe_name = self._pipe_name()
        process = self._start_server(pipe_name, "no-read")
        try:
            transport = WindowsNamedPipe.connect(pipe_name, timeout_seconds=5)
            started = time.monotonic()
            try:
                with self.assertRaises(TimeoutError):
                    write_all(
                        transport.write,
                        b"x" * (2 * 1024 * 1024),
                        deadline=time.monotonic() + 0.1,
                    )
            finally:
                transport.close()
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertTrue(transport._closed)
            self.assertEqual(transport._handle, 0)
        finally:
            self._assert_server_clean(process)

    def test_delayed_server_and_cancellation_race_do_not_linger(self) -> None:
        """Timeout and completion races both release the one-use transport."""

        for scenario in ("delayed-response", "cancellation-race"):
            with self.subTest(scenario=scenario):
                pipe_name = self._pipe_name()
                process = self._start_server(pipe_name, scenario)
                try:
                    transport = WindowsNamedPipe.connect(pipe_name, timeout_seconds=5)
                    started = time.monotonic()
                    try:
                        try:
                            received = transport.read(1, 0.05)
                        except TimeoutError:
                            received = b""
                        self.assertIn(received, (b"", b"R"))
                    finally:
                        transport.close()
                    self.assertLess(time.monotonic() - started, 2.0)
                    if not received:
                        self.assertTrue(transport._closed)
                finally:
                    self._assert_server_clean(process)

    def test_broken_generated_connection_fails_without_blocking(self) -> None:
        """Broken peer completion is not reclassified as a long read timeout."""

        pipe_name = self._pipe_name()
        process = self._start_server(pipe_name, "broken")
        try:
            transport = WindowsNamedPipe.connect(pipe_name, timeout_seconds=5)
            try:
                started = time.monotonic()
                self.assertEqual(transport.write(b"x", 0.5), 1)
                with self.assertRaises(NativePipeClosed):
                    transport.read(1, 0.5)
                self.assertLess(time.monotonic() - started, 2.0)
            finally:
                transport.close()
        finally:
            self._assert_server_clean(process)


@unittest.skipUnless(os.name == "nt", "one-use descriptor handles are Windows-only")
class NativeSessionConsumptionTests(unittest.TestCase):
    """Prove a private descriptor cannot be used twice after a completed call."""

    def test_consumes_canonical_session_file_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session.json"
            descriptor = session()
            write_private_native_session_descriptor(path, descriptor)
            with consume_native_session(path) as consumed:
                self.assertEqual(consumed["session_id"], descriptor["session_id"])
            self.assertFalse(path.exists())
            with self.assertRaises(PipelineError) as raised:
                with consume_native_session(path):
                    pass
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)

    def test_atomic_claim_rejects_second_consumer_and_cleans_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session.json"
            descriptor = session()
            write_private_native_session_descriptor(path, descriptor)
            claimed = threading.Event()
            release = threading.Event()
            first_errors: list[BaseException] = []
            second_errors: list[PipelineError] = []

            def first_consumer() -> None:
                try:
                    with consume_native_session(path):
                        claimed.set()
                        release.wait(timeout=5)
                except BaseException as error:
                    first_errors.append(error)

            def second_consumer() -> None:
                claimed.wait(timeout=5)
                try:
                    with consume_native_session(path):
                        self.fail("second consumer reached claimed session")
                except PipelineError as error:
                    second_errors.append(error)

            first = threading.Thread(target=first_consumer)
            second = threading.Thread(target=second_consumer)
            first.start()
            second.start()
            self.assertTrue(claimed.wait(timeout=5))
            second.join(timeout=5)
            release.set()
            first.join(timeout=5)
            self.assertFalse(first_errors)
            self.assertEqual(len(second_errors), 1)
            self.assertEqual(second_errors[0].code, ErrorCode.NATIVE_SESSION_INVALID)
            self.assertFalse(path.exists())

            crashing = Path(temporary) / "crashing-session.json"
            write_private_native_session_descriptor(crashing, descriptor)
            with self.assertRaisesRegex(RuntimeError, "generated"):
                with consume_native_session(crashing):
                    raise RuntimeError("generated")
            self.assertFalse(crashing.exists())

            stale = Path(temporary) / (
                ".liang-pingfa-native-session-claimed-" + "a" * 64 + ".json"
            )
            stale.write_bytes(canonical_json_bytes(descriptor) + b"\n")
            with self.assertRaises(PipelineError) as raised:
                with consume_native_session(stale):
                    pass
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
            self.assertTrue(stale.exists())


if __name__ == "__main__":
    unittest.main()
