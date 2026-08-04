"""Windows-only client and capability checks for the optional native bridge.

The project never starts, injects into, enumerates, or selects a CAD host.
An operator must explicitly supply the PID and the random local pipe exposed
by a separately installed read-only bridge.  Same-user/admin hostile-process
attacks are outside the documented trusted-local-session threat model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Protocol, cast

from .canonical import (
    CanonicalJsonError,
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
    format_utc,
    parse_utc,
    utc_now,
)
from .errors import ErrorCode, PipelineError
from .native_contracts import (
    MAX_NATIVE_SESSION_LIFETIME,
    _embedded_geometry,
    _embedded_inventory,
    load_native_config,
    native_host_binding,
    opaque_embedded_json_rules,
    require_geometry_export_matches_session,
    strict_native_json,
    validate_native_contract,
    validate_native_session_temporal_bounds,
)
from .native_protocol import (
    CONNECT_TIMEOUT_SECONDS,
    METHOD_TIMEOUT_CONFIG_KEYS,
    METHOD_TIMEOUT_SECONDS,
    PIPE_IO_CHUNK_BYTES,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    PROTOCOL_VERSION,
    RpcRequest,
    NativeOpaqueEmbeddedJsonError,
    NativeProtocolError,
    derive_challenge_response,
    encode_frame,
    new_nonce,
    new_request_id,
    protocol_error,
    read_frame,
    response_limit_for_method,
    validate_response_envelope,
    write_all,
)
from .ownership import (
    FileOwnershipBackend,
    OwnedPath,
    OwnedPathBinding,
    DestinationExistsError,
    OwnershipCleanupError,
    OwnershipError,
    OwnershipLostError,
    SourcePathLease,
    WindowsFileOwnershipBackend,
    acquire_lexical_directory_chain,
    acquire_source_path_lease,
    current_user_sid,
    dispose_retained_owned_path,
    is_reparse_point,
    lexical_absolute_path,
    platform_backend,
    secure_private_staging_file,
    verify_private_staging_file,
    validate_private_staging_ancestry,
)


_PIPE_PATTERN = re.compile(
    r"^\\\\\.\\pipe\\liang-pingfa-native-[A-Za-z0-9_-]{16,128}$"
)
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_INVALID_PATH_CHARACTERS = frozenset('"<>\x00')
_DEVICE_PATH_PREFIX = chr(92) * 2 + "?" + chr(92)
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_FLAG_OVERLAPPED = 0x40000000
_HANDLE_FLAG_INHERIT = 0x00000001
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_ERROR_BROKEN_PIPE = 109
_ERROR_HANDLE_EOF = 38
_ERROR_IO_PENDING = 997
_ERROR_NO_DATA = 232
_ERROR_NOT_FOUND = 1168
_ERROR_OPERATION_ABORTED = 995
_ERROR_PIPE_NOT_CONNECTED = 233
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
# Cancellation is normally immediate for a local named pipe.  A bounded
# foreground wait keeps a wedged kernel call from holding the RPC owner after
# its deadline; a retained cleanup waiter owns any still-pending event/buffer.
_CANCELLATION_SETTLE_MS = 250
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_SE_FILE_OBJECT = 1
_ACL_SIZE_INFORMATION = 2
_ACCESS_ALLOWED_ACE_TYPE = 0x00
_ACCESS_DENIED_ACE_TYPE = 0x01
_OBJECT_INHERIT_ACE = 0x01
_CONTAINER_INHERIT_ACE = 0x02
_NO_PROPAGATE_INHERIT_ACE = 0x04
_INHERIT_ONLY_ACE = 0x08
_INHERITED_ACE = 0x10
_SUPPORTED_DACL_ACE_FLAGS = (
    _OBJECT_INHERIT_ACE
    | _CONTAINER_INHERIT_ACE
    | _NO_PROPAGATE_INHERIT_ACE
    | _INHERIT_ONLY_ACE
    | _INHERITED_ACE
)
_GENERIC_ALL = 0x10000000
_GENERIC_EXECUTE = 0x20000000
_GENERIC_WRITE = 0x40000000
_GENERIC_READ = 0x80000000
_MAXIMUM_ALLOWED = 0x02000000
_FILE_READ_DATA = 0x00000001
_FILE_WRITE_DATA = 0x00000002
_FILE_APPEND_DATA = 0x00000004
_FILE_READ_EA = 0x00000008
_FILE_WRITE_EA = 0x00000010
_FILE_EXECUTE = 0x00000020
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_WRITE_ATTRIBUTES = 0x00000100
_FILE_DELETE_CHILD = 0x00000040
_DELETE = 0x00010000
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_SYNCHRONIZE = 0x00100000
_STANDARD_RIGHTS_REQUIRED = 0x000F0000
_FILE_ALL_ACCESS = _STANDARD_RIGHTS_REQUIRED | _SYNCHRONIZE | 0x000001FF
_FILE_GENERIC_READ = (
    _READ_CONTROL
    | _SYNCHRONIZE
    | _FILE_READ_DATA
    | _FILE_READ_ATTRIBUTES
    | _FILE_READ_EA
)
_FILE_GENERIC_WRITE = (
    _READ_CONTROL
    | _SYNCHRONIZE
    | _FILE_WRITE_DATA
    | _FILE_APPEND_DATA
    | _FILE_WRITE_EA
    | _FILE_WRITE_ATTRIBUTES
)
_FILE_GENERIC_EXECUTE = (
    _READ_CONTROL | _SYNCHRONIZE | _FILE_READ_ATTRIBUTES | _FILE_EXECUTE
)
_TRUSTED_SYSTEM_SID = "S-1-5-18"
_TRUSTED_ADMINISTRATORS_SID = "S-1-5-32-544"
_CREATOR_OWNER_SID = "S-1-3-0"
_OWNER_RIGHTS_SID = "S-1-3-4"
# Windows Modules Installer (TrustedInstaller) owns and retains full control
# over the normal Program Files component chain on supported installations.
# Only this exact service SID is trusted; arbitrary service SIDs and every
# other broad writer ACE remain rejected.
_TRUSTED_INSTALLER_SID = (
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
)
_CLAIMED_SESSION_NAME = re.compile(
    r"^\.liang-pingfa-native-session-claimed-[a-f0-9]{64}\.json$"
)


def _is_claimed_session_basename(name: str) -> bool:
    """Recognize only ASCII case variants of the canonical claimed basename.

    NTFS treats the one-use claim namespace case-insensitively.  Normalize
    only the basename supplied by the caller: normalizing a whole path could
    accidentally turn a lexical containment check into path resolution.
    ``str.casefold`` is deliberately guarded by ``isascii`` so Unicode
    lookalikes never become aliases for an ASCII private descriptor.
    """

    return (
        isinstance(name, str)
        and name.isascii()
        and _CLAIMED_SESSION_NAME.fullmatch(name.casefold()) is not None
    )


class PipeTransport(Protocol):
    """The minimal exact-byte transport used by the protocol client."""

    @property
    def server_pid(self) -> int:
        """Return the Windows server process ID bound to this pipe."""

    def read(self, maximum: int, timeout: float) -> bytes:
        """Read no more than ``maximum`` bytes before the deadline."""

    def write(self, payload: bytes, timeout: float) -> int:
        """Write one short chunk and return the exact byte count."""

    def pending_bytes(self) -> int:
        """Return immediately available bytes without consuming a frame."""

    def close(self) -> None:
        """Close the transport without a reconnect retry."""


@dataclass(frozen=True)
class ProcessIdentity:
    """Stable process-instance values used to reject PID reuse."""

    pid: int
    windows_session_id: int
    creation_time_100ns: int
    instance_fingerprint: str
    executable_fingerprint: str


@dataclass(frozen=True)
class RequestTiming:
    """One immutable RPC timing budget shared by every response stage."""

    deadline: float
    method_deadline: float
    session_deadline: float


def require_request_deadline(timing: RequestTiming, stage: str) -> None:
    """Reject work which crosses its original method/session deadline.

    ``deadline`` is always the earlier absolute boundary.  Preserve the cause
    of that boundary so callers consistently report session expiry when signed
    session life was limiting, and the stable RPC timeout otherwise.
    """

    if time.monotonic() < timing.deadline:
        return
    if timing.session_deadline <= timing.method_deadline:
        raise PipelineError(ErrorCode.NATIVE_SESSION_EXPIRED, "native session expired")
    raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "native RPC timeout")


class _Overlapped(ctypes.Structure):
    """Win32 OVERLAPPED with pointer-width fields on both supported ABIs."""

    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


@dataclass
class _PendingPipeOperation:
    """Keep all kernel-referenced state alive until one I/O completes."""

    event: int
    overlapped: _Overlapped
    buffer: Any
    event_closed: bool = False


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", ctypes.c_ushort),
    ]


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = [
        ("Header", _AceHeader),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


@dataclass(frozen=True)
class ComponentDaclAce:
    """One interpreted allow/deny ACE kept only in local validation memory."""

    ace_type: str
    sid: str
    mask: int
    inherited: bool
    # ``INHERITED_ACE`` only records where an ACE came from.  In contrast,
    # ``INHERIT_ONLY_ACE`` means that the ACE does not apply to this object.
    # Keep raw ACE flags so the evaluator can preserve that Win32 distinction.
    ace_flags: int = 0


@dataclass(frozen=True)
class ComponentDacl:
    """A fully interpreted component owner/DACL with no published raw data."""

    owner_sid: str
    aces: tuple[ComponentDaclAce, ...]


def _effective_ace_flags(ace: ComponentDaclAce) -> int:
    """Return validated ACE flags while preserving legacy test construction.

    ``inherited`` predates raw flag retention and remains part of the narrow
    test-facing model.  A reader supplies both values; hand-built tests that
    supply only ``inherited=True`` still model an applicable inherited ACE.
    """

    if (
        not isinstance(ace.inherited, bool)
        or not isinstance(ace.ace_flags, int)
        or isinstance(ace.ace_flags, bool)
        or ace.ace_flags < 0
        or ace.ace_flags > 0xFF
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CONFIG_INVALID,
            "component DACL ACE flags cannot be interpreted",
        )
    flags = ace.ace_flags | (_INHERITED_ACE if ace.inherited else 0)
    if flags & ~_SUPPORTED_DACL_ACE_FLAGS:
        raise PipelineError(
            ErrorCode.NATIVE_CONFIG_INVALID,
            "component DACL ACE flags are unsupported",
        )
    return flags


def _component_generic_mapping(*, is_directory: bool) -> dict[int, int]:
    """Return the documented generic mapping for one file-system object.

    The access bits for file data and directory child creation intentionally
    have the same numeric values.  Their *meaning* differs, so callers must
    normalize generic rights before applying the object-kind-specific unsafe
    mask below.
    """

    if is_directory:
        # FILE_WRITE_DATA/FILE_APPEND_DATA mean FILE_ADD_FILE and
        # FILE_ADD_SUBDIRECTORY for a directory.  They alone cannot replace
        # an already-existing retained child or rename the held directory.
        generic_write = (
            _READ_CONTROL
            | _SYNCHRONIZE
            | _FILE_WRITE_DATA  # FILE_ADD_FILE
            | _FILE_APPEND_DATA  # FILE_ADD_SUBDIRECTORY
            | _FILE_WRITE_EA
            | _FILE_WRITE_ATTRIBUTES
        )
        generic_read = _FILE_GENERIC_READ
        generic_execute = _FILE_GENERIC_EXECUTE
    else:
        generic_read = _FILE_GENERIC_READ
        generic_write = _FILE_GENERIC_WRITE
        generic_execute = _FILE_GENERIC_EXECUTE
    return {
        _GENERIC_READ: generic_read,
        _GENERIC_WRITE: generic_write,
        _GENERIC_EXECUTE: generic_execute,
        _GENERIC_ALL: _FILE_ALL_ACCESS,
    }


def _normalize_component_access_mask(mask: int, *, is_directory: bool) -> int:
    """Expand all Win32 GENERIC_* bits before evaluating an ACE.

    An ACE DACL mask is a 32-bit access mask.  ``MapGenericMask`` performs
    the same replacement for the object type; keeping it explicit makes the
    later file-vs-directory decision auditable and avoids treating a raw
    directory ``FILE_ADD_SUBDIRECTORY`` bit as file append authority.
    """

    if (
        not isinstance(mask, int)
        or isinstance(mask, bool)
        or mask < 0
        or mask > 0xFFFFFFFF
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CONFIG_INVALID,
            "component DACL access mask cannot be interpreted",
        )
    normalized = mask
    for generic, expanded in _component_generic_mapping(
        is_directory=is_directory
    ).items():
        if normalized & generic:
            normalized = (normalized & ~generic) | expanded
    return normalized


def _component_unsafe_mask(*, is_directory: bool) -> int:
    """Return rights that can alter/repoint the exact retained component.

    Existing regular files are mutable through data/append/EA/attribute
    rights.  Existing directories are different: FILE_WRITE_DATA and
    FILE_APPEND_DATA mean creating a *new* child and do not by themselves
    authorize replacement of the held existing directory or child.  The
    retained no-delete/no-rename handles provide the complementary race
    boundary; this DACL gate rejects rights that can alter the held directory
    itself, remove children, or rewrite its security ownership.
    """

    if is_directory:
        return (
            _MAXIMUM_ALLOWED
            | _FILE_WRITE_EA
            | _FILE_WRITE_ATTRIBUTES
            | _DELETE
            | _FILE_DELETE_CHILD
            | _WRITE_DAC
            | _WRITE_OWNER
        )
    return (
        _MAXIMUM_ALLOWED
        | _FILE_WRITE_DATA
        | _FILE_APPEND_DATA
        | _FILE_WRITE_EA
        | _FILE_WRITE_ATTRIBUTES
        | _DELETE
        | _WRITE_DAC
        | _WRITE_OWNER
    )


def validate_component_dacl(
    dacl: ComponentDacl,
    *,
    is_directory: bool,
    trusted_sids: frozenset[str] | None = None,
    allow_trustedinstaller_owner: bool = True,
) -> None:
    """Reject writable/replacement rights outside the narrow local trust set.

    The reader retains whether every ACE was explicit or inherited.  Both are
    evaluated: inheritance is not a reason to overlook a broad writer.
    Unsupported ACE forms are rejected by the Win32 reader before this
    function is called.
    """

    trusted = trusted_sids or frozenset(
        {
            current_user_sid(),
            _TRUSTED_SYSTEM_SID,
            _TRUSTED_ADMINISTRATORS_SID,
        }
    )
    trusted_principals = (
        trusted | {_TRUSTED_INSTALLER_SID}
        if allow_trustedinstaller_owner
        else trusted
    )
    if dacl.owner_sid not in trusted_principals:
        raise PipelineError(
            ErrorCode.NATIVE_CONFIG_INVALID,
            "component owner is outside the trusted local session",
        )
    unsafe_mask = _component_unsafe_mask(is_directory=is_directory)
    for ace in dacl.aces:
        if (
            ace.ace_type not in {"allow", "deny"}
            or not isinstance(ace.sid, str)
            or not ace.sid
        ):
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "component DACL ACE cannot be interpreted",
            )
        flags = _effective_ace_flags(ace)
        # An inherit-only ACE exists solely to be inherited by a descendant;
        # it grants no access on the currently evaluated component.  An
        # inherited ACE without this flag is fully applicable and is checked
        # exactly like an explicit ACE.
        if flags & _INHERIT_ONLY_ACE:
            continue
        effective_mask = _normalize_component_access_mask(
            ace.mask,
            is_directory=is_directory,
        )
        # OWNER RIGHTS (and a materialized Creator Owner ACE) is not an
        # arbitrary third-party principal. Windows evaluates it against the
        # object's current owner; it is safe only when that owner already
        # belongs to the exact trusted set above. This is the standard DACL
        # shape for current-user temporary directories.
        ace_principal_is_trusted = (
            ace.sid in trusted_principals
            or (
                ace.sid in {_CREATOR_OWNER_SID, _OWNER_RIGHTS_SID}
                and dacl.owner_sid in trusted_principals
            )
        )
        if (
            ace.ace_type == "allow"
            and effective_mask & unsafe_mask
            and not ace_principal_is_trusted
        ):
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "component is writable by an untrusted principal",
            )


def _sid_text(advapi32: Any, kernel32: Any, sid: ctypes.c_void_p) -> str:
    """Convert one validated SID without publishing its raw value."""

    valid = advapi32.IsValidSid
    valid.argtypes = [ctypes.c_void_p]
    valid.restype = wintypes.BOOL
    convert = advapi32.ConvertSidToStringSidW
    convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    if not sid.value or not valid(sid):
        raise OwnershipCleanupError("component ACL SID is invalid")
    result = ctypes.c_wchar_p()
    if not convert(sid, ctypes.byref(result)):
        raise OwnershipCleanupError("component ACL SID cannot be converted")
    try:
        value = result.value
        if value is None or re.fullmatch(r"S-\d+(?:-\d+)+", value) is None:
            raise OwnershipCleanupError("component ACL SID is malformed")
        return value
    finally:
        if result:
            local_free(ctypes.cast(result, ctypes.c_void_p))


def _read_component_dacl(opened: OwnedPath) -> ComponentDacl:
    """Read owner plus every inherited/explicit allow/deny ACE by open handle."""

    _require_windows()
    handle = getattr(opened, "handle", None)
    if not isinstance(handle, int) or handle in {0, _INVALID_HANDLE_VALUE}:
        raise OwnershipCleanupError("component handle cannot expose its DACL")
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as error:
        raise OwnershipCleanupError("component DACL APIs are unavailable") from error
    get_security = advapi32.GetSecurityInfo
    get_security.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security.restype = wintypes.DWORD
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = get_security(
        wintypes.HANDLE(handle),
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != 0 or not owner.value or not dacl.value or not descriptor.value:
        raise OwnershipCleanupError("component DACL cannot be read")
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    try:
        owner_sid = _sid_text(advapi32, kernel32, owner)
        get_acl_information = advapi32.GetAclInformation
        get_acl_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_int,
        ]
        get_acl_information.restype = wintypes.BOOL
        size = _AclSizeInformation()
        if not get_acl_information(
            dacl,
            ctypes.byref(size),
            ctypes.sizeof(size),
            _ACL_SIZE_INFORMATION,
        ):
            raise OwnershipCleanupError("component DACL cannot be interpreted")
        get_ace = advapi32.GetAce
        get_ace.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        get_ace.restype = wintypes.BOOL
        aces: list[ComponentDaclAce] = []
        for index in range(int(size.AceCount)):
            pointer = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(pointer)) or not pointer.value:
                raise OwnershipCleanupError("component DACL ACE cannot be read")
            header = ctypes.cast(pointer, ctypes.POINTER(_AceHeader)).contents
            if (
                header.AceType not in {_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE}
                or header.AceSize < ctypes.sizeof(_AccessAllowedAce)
            ):
                raise OwnershipCleanupError("component DACL ACE is unsupported")
            ace = ctypes.cast(pointer, ctypes.POINTER(_AccessAllowedAce)).contents
            sid = ctypes.c_void_p(
                pointer.value + _AccessAllowedAce.SidStart.offset
            )
            aces.append(
                ComponentDaclAce(
                    ace_type=(
                        "allow"
                        if header.AceType == _ACCESS_ALLOWED_ACE_TYPE
                        else "deny"
                    ),
                    sid=_sid_text(advapi32, kernel32, sid),
                    mask=int(ace.Mask),
                    inherited=bool(header.AceFlags & _INHERITED_ACE),
                    ace_flags=int(header.AceFlags),
                )
            )
        return ComponentDacl(owner_sid=owner_sid, aces=tuple(aces))
    finally:
        local_free(descriptor)


@dataclass
class NativeInstallationLeases:
    """Retained canonical Core Console/plugin identities through native apply."""

    leases: dict[str, SourcePathLease]
    expected_hashes: dict[str, str]
    acl_reader: Callable[[OwnedPath], ComponentDacl]
    trusted_sids: frozenset[str] | None
    _closed: bool = False

    @property
    def paths(self) -> dict[str, Path]:
        return {name: lease.path for name, lease in self.leases.items()}

    def require_bindings(self) -> None:
        """Recheck identity, content hash, and every held component DACL."""

        if self._closed:
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "component leases were released",
            )
        try:
            for name, lease in self.leases.items():
                lease.require_binding()
                current = lease.owned.capture_binding()
                if (
                    current.is_directory
                    or current.sha256 != self.expected_hashes[name]
                    or current.sha256 != lease.binding.sha256
                ):
                    raise OwnershipLostError("component file identity or hash drifted")
                for component in lease.chain.components:
                    validate_component_dacl(
                        self.acl_reader(component.owned),
                        is_directory=True,
                        trusted_sids=self.trusted_sids,
                        allow_trustedinstaller_owner=True,
                    )
                validate_component_dacl(
                    self.acl_reader(lease.owned),
                    is_directory=False,
                    trusted_sids=self.trusted_sids,
                    allow_trustedinstaller_owner=True,
                )
        except PipelineError:
            raise
        except (OSError, OwnershipError, OwnershipCleanupError) as error:
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "configured component trust binding is unavailable",
            ) from error

    def close(self) -> None:
        """Release all file and lexical-ancestor leases only after cleanup."""

        if self._closed:
            return
        failure: BaseException | None = None
        for lease in reversed(tuple(self.leases.values())):
            try:
                lease.close()
            except (OSError, OwnershipError) as error:
                if failure is None:
                    failure = error
        self._closed = True
        if failure is not None:
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "configured component lease cannot be released",
            ) from failure


def _require_windows() -> None:
    if os.name != "nt":
        raise PipelineError(ErrorCode.WINDOWS_PLATFORM_REQUIRED, "native bridge is Windows-only")


def validate_pipe_name(pipe_name: str) -> str:
    """Accept only a non-predictable project-owned local named-pipe spelling."""

    if (
        not isinstance(pipe_name, str)
        or not _PIPE_PATTERN.fullmatch(pipe_name)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in pipe_name)
        or pipe_name.startswith(_DEVICE_PATH_PREFIX)
        or "/" in pipe_name
    ):
        raise PipelineError(ErrorCode.NATIVE_PIPE_INVALID, "pipe is not local and valid")
    token = pipe_name.rsplit("-", maxsplit=1)[-1]
    if (
        len(set(token)) < 8
        or not any(character.isalpha() for character in token)
        or not any(character.isdigit() for character in token)
    ):
        raise PipelineError(ErrorCode.NATIVE_PIPE_INVALID, "pipe token is predictable")
    return pipe_name


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb", buffering=0) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normal_local_file(path_text: str, suffix: str) -> Path:
    """Validate only lexical component syntax before opening a retained lease."""

    _require_windows()
    if (
        not isinstance(path_text, str)
        or not path_text
        or any(character in _INVALID_PATH_CHARACTERS or ord(character) < 0x20 for character in path_text)
        or not path_text.casefold().endswith(suffix)
        or path_text.startswith("\\\\")
        or path_text.startswith(_DEVICE_PATH_PREFIX)
    ):
        raise PipelineError(ErrorCode.NATIVE_CONFIG_INVALID, "configured file path is unsafe")
    raw = Path(path_text)
    if not raw.is_absolute() or raw.drive == "":
        raise PipelineError(ErrorCode.NATIVE_CONFIG_INVALID, "configured file is not local")
    if any(component in {"", ".", ".."} for component in raw.parts[1:]):
        raise PipelineError(ErrorCode.NATIVE_CONFIG_INVALID, "configured file path is unsafe")
    return raw


def _is_local_ntfs(path: Path) -> bool:
    """Require a normal drive-rooted NTFS volume for configured host files."""

    try:
        root = path.anchor
        if not root or root.startswith("\\\\"):
            return False
        kernel32 = _windows_kernel32()
        filesystem = ctypes.create_unicode_buffer(64)
        volume = ctypes.create_unicode_buffer(260)
        serial = wintypes.DWORD()
        maximum_component = wintypes.DWORD()
        flags = wintypes.DWORD()
        query = kernel32.GetVolumeInformationW
        query.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        query.restype = wintypes.BOOL
        return bool(
            query(
                root,
                volume,
                len(volume),
                ctypes.byref(serial),
                ctypes.byref(maximum_component),
                ctypes.byref(flags),
                filesystem,
                len(filesystem),
            )
            and filesystem.value.casefold() == "ntfs"
        )
    except (AttributeError, OSError):
        return False


def acquire_native_installation_leases(
    config: Mapping[str, Any],
    *,
    backend: FileOwnershipBackend | None = None,
    acl_reader: Callable[[OwnedPath], ComponentDacl] | None = None,
    trusted_sids: frozenset[str] | None = None,
) -> NativeInstallationLeases:
    """Open, ACL-check, hash, and retain every configured native component.

    The leases are the launch authority.  Callers must keep them alive from
    fixed-script creation through process-tree termination, readback
    validation, and private-workspace cleanup; a path-only hash is never
    accepted as an installation proof.
    """

    _require_windows()
    checked = validate_native_contract("config", config)
    selected_backend = backend or platform_backend(require_windows=True)
    candidates = {
        "core_console": (checked["core_console"], ".exe"),
        "write_plugin": (checked["plugins"]["write"], ".dll"),
        "readback_plugin": (checked["plugins"]["readback"], ".dll"),
    }
    leases: dict[str, SourcePathLease] = {}
    try:
        for name, (entry, suffix) in candidates.items():
            lexical = _normal_local_file(cast(str, entry["path"]), suffix)
            lease = acquire_source_path_lease(lexical, selected_backend)
            if not _is_local_ntfs(lease.path):
                lease.close()
                raise PipelineError(
                    ErrorCode.NATIVE_CONFIG_INVALID,
                    "configured component is not on local NTFS",
                )
            leases[name] = lease
        result = NativeInstallationLeases(
            leases=leases,
            expected_hashes={
                name: cast(str, entry["sha256"])
                for name, (entry, _suffix) in candidates.items()
            },
            acl_reader=acl_reader or _read_component_dacl,
            trusted_sids=trusted_sids,
        )
        result.require_bindings()
        return result
    except BaseException:
        for lease in reversed(tuple(leases.values())):
            try:
                lease.close()
            except (OSError, OwnershipError):
                pass
        raise


def validate_native_installation(config: Mapping[str, Any]) -> dict[str, Path]:
    """Probe explicit configured files via held ACL/identity leases, then release."""

    leases = acquire_native_installation_leases(config)
    try:
        return leases.paths
    finally:
        leases.close()


def _windows_kernel32() -> Any:
    _require_windows()
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _close_handle(kernel32: Any, handle: int) -> None:
    if handle and handle != _INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def _process_image_path(kernel32: Any, handle: int) -> Path | None:
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    query = kernel32.QueryFullProcessImageNameW
    query.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query.restype = wintypes.BOOL
    if not query(wintypes.HANDLE(handle), 0, buffer, ctypes.byref(size)):
        return None
    return Path(buffer.value)


def inspect_process(pid: int) -> ProcessIdentity:
    """Open an explicitly selected process and bind PID, session, and creation time."""

    _require_windows()
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise PipelineError(ErrorCode.NATIVE_SESSION_INVALID, "PID is invalid")
    kernel32 = _windows_kernel32()
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    handle = int(open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid) or 0)
    if not handle:
        raise PipelineError(ErrorCode.NATIVE_SESSION_INVALID, "selected PID is unavailable")
    try:
        process_id = wintypes.DWORD()
        session_fn = kernel32.ProcessIdToSessionId
        session_fn.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        session_fn.restype = wintypes.BOOL
        if not session_fn(pid, ctypes.byref(process_id)):
            raise PipelineError(ErrorCode.NATIVE_SESSION_INVALID, "PID has no Windows session")
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        get_times.restype = wintypes.BOOL
        if not get_times(
            wintypes.HANDLE(handle),
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            raise PipelineError(ErrorCode.NATIVE_SESSION_INVALID, "PID creation time unavailable")
        created = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        executable = "unavailable"
        image = _process_image_path(kernel32, handle)
        if image is not None:
            try:
                # The target executable is advisory for read-only preparation.
                # A missing/untrusted image makes later write authorization fail.
                if image.is_file() and not is_reparse_point(image):
                    executable = _hash_file(image)
            except OSError:
                executable = "unavailable"
        instance = canonical_sha256(
            {
                "creation_time_100ns": str(created),
                "pid": pid,
                "windows_session_id": int(process_id.value),
            }
        )
        return ProcessIdentity(
            pid=pid,
            windows_session_id=int(process_id.value),
            creation_time_100ns=created,
            instance_fingerprint=instance,
            executable_fingerprint=executable,
        )
    finally:
        _close_handle(kernel32, handle)


class WindowsNamedPipe:
    """Byte-mode named-pipe client with cancellable overlapped Win32 I/O.

    Each ``read`` and ``write`` owns a fresh event and ``OVERLAPPED`` record.
    The protocol's frame loops supply the remaining part of one absolute RPC
    deadline, so a partial transfer never receives a new full timeout.  A
    timeout retires the outstanding operation, closes this one-use transport,
    and leaves no synchronous Win32 call able to outlive the caller.
    """

    def __init__(self, handle: int, server_pid: int) -> None:
        self._handle = handle
        self._server_pid = server_pid
        self._closed = False
        self._kernel32 = _windows_kernel32()
        self._io_lock = threading.RLock()

    @property
    def server_pid(self) -> int:
        return self._server_pid

    @classmethod
    def connect(cls, pipe_name: str, *, timeout_seconds: float) -> "WindowsNamedPipe":
        """Open one explicit local pipe and prove its server PID before use."""

        _require_windows()
        validate_pipe_name(pipe_name)
        kernel32 = _windows_kernel32()
        wait = kernel32.WaitNamedPipeW
        wait.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        wait.restype = wintypes.BOOL
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > CONNECT_TIMEOUT_SECONDS
        ):
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "native pipe timeout is outside its hard bound",
            )
        timeout_ms = int(timeout_seconds * 1000)
        if timeout_ms < 1:
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "native pipe timeout is outside its hard bound",
            )
        if not wait(pipe_name, timeout_ms):
            raise PipelineError(ErrorCode.NATIVE_PIPE_INVALID, "native pipe is unavailable")
        create = kernel32.CreateFileW
        create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create.restype = wintypes.HANDLE
        handle = int(
            create(
                pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OVERLAPPED,
                None,
            )
            or 0
        )
        if not handle or handle == _INVALID_HANDLE_VALUE:
            raise PipelineError(ErrorCode.NATIVE_PIPE_INVALID, "native pipe cannot open")
        try:
            set_handle_information = kernel32.SetHandleInformation
            set_handle_information.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            set_handle_information.restype = wintypes.BOOL
            if not set_handle_information(
                wintypes.HANDLE(handle),
                _HANDLE_FLAG_INHERIT,
                0,
            ):
                raise PipelineError(
                    ErrorCode.NATIVE_PIPE_INVALID,
                    "native pipe inheritance cannot be disabled",
                )
            server_pid = wintypes.ULONG()
            get_server_pid = kernel32.GetNamedPipeServerProcessId
            get_server_pid.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
            get_server_pid.restype = wintypes.BOOL
            if not get_server_pid(wintypes.HANDLE(handle), ctypes.byref(server_pid)):
                raise PipelineError(ErrorCode.NATIVE_PIPE_INVALID, "native pipe server PID unavailable")
            return cls(handle, int(server_pid.value))
        except BaseException:
            _close_handle(kernel32, handle)
            raise

    def pending_bytes(self) -> int:
        with self._io_lock:
            if self._closed or not self._handle:
                return 0
            available = wintypes.DWORD()
            peek = self._kernel32.PeekNamedPipe
            peek.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.c_void_p,
            ]
            peek.restype = wintypes.BOOL
            if not peek(
                wintypes.HANDLE(self._handle),
                None,
                0,
                None,
                ctypes.byref(available),
                None,
            ):
                return 0
            return int(available.value)

    def _close_handle_locked(self) -> None:
        """Close the non-inheritable client handle exactly once."""

        handle = self._handle
        self._handle = 0
        self._closed = True
        _close_handle(self._kernel32, handle)

    def _close_operation_event(self, operation: _PendingPipeOperation) -> None:
        if not operation.event_closed:
            operation.event_closed = True
            _close_handle(self._kernel32, operation.event)

    def _new_operation(self, buffer: Any) -> _PendingPipeOperation:
        create_event = self._kernel32.CreateEventW
        create_event.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        create_event.restype = wintypes.HANDLE
        event = int(create_event(None, True, False, None) or 0)
        if not event or event == _INVALID_HANDLE_VALUE:
            raise NativePipeClosed("native pipe event cannot open")
        try:
            set_handle_information = self._kernel32.SetHandleInformation
            set_handle_information.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            set_handle_information.restype = wintypes.BOOL
            if not set_handle_information(
                wintypes.HANDLE(event),
                _HANDLE_FLAG_INHERIT,
                0,
            ):
                raise NativePipeClosed("native pipe event inheritance cannot be disabled")
            overlapped = _Overlapped()
            overlapped.hEvent = wintypes.HANDLE(event)
            return _PendingPipeOperation(
                event=event,
                overlapped=overlapped,
                buffer=buffer,
            )
        except BaseException:
            _close_handle(self._kernel32, event)
            raise

    @staticmethod
    def _operation_deadline(timeout: float) -> float:
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise TimeoutError("native pipe I/O timed out")
        return time.monotonic() + float(timeout)

    @staticmethod
    def _wait_milliseconds(deadline: float) -> int:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0
        # Rounding up prevents a sub-millisecond remainder from becoming an
        # unintended zero-length wait while never resetting the deadline.
        return min(0xFFFFFFFE, max(1, math.ceil(remaining * 1000)))

    def _wait_for_event(self, event: int, milliseconds: int) -> int:
        wait = self._kernel32.WaitForSingleObject
        wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait.restype = wintypes.DWORD
        return int(wait(wintypes.HANDLE(event), milliseconds))

    @staticmethod
    def _is_closed_pipe_error(error: int) -> bool:
        return error in {
            _ERROR_BROKEN_PIPE,
            _ERROR_HANDLE_EOF,
            _ERROR_NO_DATA,
            _ERROR_OPERATION_ABORTED,
            _ERROR_PIPE_NOT_CONNECTED,
        }

    def _defer_operation_cleanup(self, operation: _PendingPipeOperation) -> None:
        """Retain kernel-owned state if cancellation completion is delayed.

        Closing the pipe handle has already requested cancellation.  This
        daemon only owns the event/OVERLAPPED/buffer lifetime; it never holds
        the caller or the single-flight lifecycle lock after a timeout.
        """

        kernel32 = self._kernel32

        def await_completion() -> None:
            try:
                wait = kernel32.WaitForSingleObject
                wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                wait.restype = wintypes.DWORD
                wait(wintypes.HANDLE(operation.event), 0xFFFFFFFF)
            finally:
                _close_handle(kernel32, operation.event)
                operation.event_closed = True

        thread = threading.Thread(
            target=await_completion,
            name="liang-pingfa-native-pipe-cancel",
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError:
            # A thread-start failure is extraordinary.  Keep the operation
            # valid rather than freeing a kernel-referenced event/buffer.
            await_completion()

    def _retire_pending_operation(self, operation: _PendingPipeOperation) -> None:
        """Cancel one pending request, then close or retain its event safely."""

        try:
            cancel = self._kernel32.CancelIoEx
            cancel.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
            cancel.restype = wintypes.BOOL
            if not cancel(
                wintypes.HANDLE(self._handle),
                ctypes.byref(operation.overlapped),
            ):
                # ERROR_NOT_FOUND means completion raced cancellation.  The
                # event still remains the authoritative lifetime signal.
                error = ctypes.get_last_error()
                if error not in {
                    _ERROR_NOT_FOUND,
                    _ERROR_OPERATION_ABORTED,
                    _ERROR_PIPE_NOT_CONNECTED,
                    _ERROR_BROKEN_PIPE,
                }:
                    pass
        finally:
            completion = self._wait_for_event(
                operation.event,
                _CANCELLATION_SETTLE_MS,
            )
            # A timeout permanently invalidates the one-use session.  Closing
            # its handle also cancels any race that missed CancelIoEx.
            self._close_handle_locked()
            if completion == _WAIT_OBJECT_0:
                self._close_operation_event(operation)
            else:
                self._defer_operation_cleanup(operation)

    def _completed_transfer(
        self,
        operation: _PendingPipeOperation,
        transferred: wintypes.DWORD,
    ) -> int:
        get_result = self._kernel32.GetOverlappedResult
        get_result.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_Overlapped),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        get_result.restype = wintypes.BOOL
        if not get_result(
            wintypes.HANDLE(self._handle),
            ctypes.byref(operation.overlapped),
            ctypes.byref(transferred),
            False,
        ):
            error = ctypes.get_last_error()
            if self._is_closed_pipe_error(error):
                raise NativePipeClosed("native pipe closed during I/O")
            raise NativePipeClosed("native pipe I/O completion failed")
        return int(transferred.value)

    def _transfer(
        self,
        function_name: str,
        buffer: Any,
        length: int,
        deadline: float,
    ) -> tuple[int, _PendingPipeOperation]:
        """Issue one overlapped transfer and wait only to its absolute deadline."""

        if self._wait_milliseconds(deadline) == 0:
            raise TimeoutError("native pipe I/O timed out")
        operation = self._new_operation(buffer)
        safe_to_close_event = True
        try:
            if self._closed or not self._handle:
                raise NativePipeClosed("native pipe is closed")
            if self._wait_milliseconds(deadline) == 0:
                raise TimeoutError("native pipe I/O timed out")
            transferred = wintypes.DWORD()
            transfer = getattr(self._kernel32, function_name)
            transfer.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(_Overlapped),
            ]
            transfer.restype = wintypes.BOOL
            completed_immediately = bool(
                transfer(
                    wintypes.HANDLE(self._handle),
                    buffer,
                    length,
                    ctypes.byref(transferred),
                    ctypes.byref(operation.overlapped),
                )
            )
            if completed_immediately:
                if time.monotonic() > deadline:
                    self._close_handle_locked()
                    raise TimeoutError("native pipe I/O timed out")
                return int(transferred.value), operation
            error = ctypes.get_last_error()
            if error != _ERROR_IO_PENDING:
                if self._is_closed_pipe_error(error):
                    raise NativePipeClosed("native pipe closed during I/O")
                raise NativePipeClosed("native pipe I/O failed")
            wait_result = self._wait_for_event(
                operation.event,
                self._wait_milliseconds(deadline),
            )
            if wait_result == _WAIT_OBJECT_0:
                return self._completed_transfer(operation, transferred), operation
            if wait_result == _WAIT_TIMEOUT:
                safe_to_close_event = False
                self._retire_pending_operation(operation)
                raise TimeoutError("native pipe I/O timed out")
            safe_to_close_event = False
            self._retire_pending_operation(operation)
            raise NativePipeClosed("native pipe I/O wait failed")
        finally:
            if safe_to_close_event:
                self._close_operation_event(operation)

    def read_until(self, maximum: int, *, deadline: float) -> bytes:
        """Read one bounded chunk without replacing the caller's deadline."""

        if (
            not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or maximum <= 0
        ):
            raise NativePipeClosed("native pipe read bound is invalid")
        if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise TimeoutError("native pipe I/O timed out")
        with self._io_lock:
            if self._closed or not self._handle:
                raise NativePipeClosed("native pipe is closed")
            if self._wait_milliseconds(deadline) == 0:
                raise TimeoutError("native pipe I/O timed out")
            # ``read_exact`` already requests fixed chunks, but cap this
            # lower transport boundary as well so a direct caller cannot
            # recreate remaining-frame-sized ReadFile allocations.
            requested = min(maximum, PIPE_IO_CHUNK_BYTES)
            buffer = ctypes.create_string_buffer(requested)
            received, operation = self._transfer(
                "ReadFile",
                buffer,
                requested,
                deadline,
            )
            if received <= 0:
                raise NativePipeClosed("native pipe closed during read")
            return bytes(operation.buffer.raw[:received])

    def read(self, maximum: int, timeout: float) -> bytes:
        """Read using a direct caller timeout outside protocol-owned RPCs."""

        # The public transport method remains timeout-shaped for test doubles
        # and direct callers. NativeBridgeClient uses ``read_until`` so all
        # chunks of one RPC share one immutable absolute deadline.
        return self.read_until(
            maximum,
            deadline=self._operation_deadline(timeout),
        )

    def write_until(self, payload: bytes, *, deadline: float) -> int:
        """Write exactly one bounded transport chunk.

        ``write_all`` owns framing-level chunking and its absolute deadline.
        A direct caller cannot make one ``WriteFile`` allocation exceed the
        fixed transport maximum.
        """

        if not isinstance(payload, bytes) or not payload:
            return 0
        if len(payload) > PIPE_IO_CHUNK_BYTES:
            raise NativePipeClosed("native pipe write exceeds fixed chunk bound")
        if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise TimeoutError("native pipe I/O timed out")
        with self._io_lock:
            if self._closed or not self._handle:
                return 0
            if self._wait_milliseconds(deadline) == 0:
                raise TimeoutError("native pipe I/O timed out")
            buffer = ctypes.create_string_buffer(payload, len(payload))
            written, _operation = self._transfer(
                "WriteFile",
                buffer,
                len(payload),
                deadline,
            )
            if written <= 0:
                raise NativePipeClosed("native pipe closed during write")
            return written

    def write(self, payload: bytes, timeout: float) -> int:
        """Write using a direct caller timeout outside protocol-owned RPCs."""

        # As with ``read``, keep the protocol's compatibility surface while
        # letting the RPC layer pass its one absolute deadline unchanged.
        return self.write_until(
            payload,
            deadline=self._operation_deadline(timeout),
        )

    def close(self) -> None:
        with self._io_lock:
            if self._closed:
                return
            self._close_handle_locked()


class NativePipeClosed(OSError):
    """Raised internally when a strict pipe transport disconnects."""


def _document_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return dict(left) == dict(right)


class NativeBridgeClient:
    """One non-reconnectable, thread-safe session with one in-flight RPC.

    ``request`` uses a nonblocking lifecycle guard.  A second concurrent
    caller cannot write a frame and marks the session invalid; the owning
    caller observes that invalidation before it can return a successful
    response.  ``close`` serializes on the same guard so it cannot close a
    transport midway through a frame exchange.
    """

    def __init__(
        self,
        session: Mapping[str, Any],
        *,
        config: Mapping[str, Any],
        transport: PipeTransport | None = None,
    ) -> None:
        wall_now = utc_now()
        self._session = validate_native_contract("session", session, now=wall_now)
        try:
            created, expires = validate_native_session_temporal_bounds(
                self._session["created_at"],
                self._session["expires_at"],
                now=wall_now,
            )
        except (CanonicalJsonError, TypeError, ValueError) as error:
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native session temporal bounds are invalid",
            ) from error
        # This deadline is established exactly once after successful wall-clock
        # validation.  Wall-clock rollback cannot mint additional session life.
        self._session_created_at = created
        self._session_expires_at = expires
        self._session_deadline = time.monotonic() + (expires - wall_now).total_seconds()
        self._config = validate_native_contract("config", config)
        # Reject a hand-supplied descriptor that is valid in isolation but is
        # incompatible with the exact configured native host before a pipe can
        # be opened.  Geometry responses then use the same shared binding gate.
        native_host_binding(self._session, self._config)
        self._transport = transport
        # Deliberately non-reentrant: a transport callback cannot recursively
        # issue a second frame on the same thread.
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._invalid = False
        self._request_ids: set[str] = set()
        # This immutable token is captured only after the pipe reports its
        # server PID and that exact PID has been reinspected.  A fresh check
        # before every RPC proves it has not exited, restarted, or drifted.
        self._connected_process: ProcessIdentity | None = None
        self._transport_bound = False
        self._last_response_timing: RequestTiming | None = None

    @property
    def invalid(self) -> bool:
        with self._state_lock:
            return self._invalid

    def _is_invalid(self) -> bool:
        with self._state_lock:
            return self._invalid

    def _mark_invalid_while_busy(self) -> None:
        """Record concurrent misuse without racing the active transport owner."""

        with self._state_lock:
            self._invalid = True

    @staticmethod
    def _bounded_timeout_seconds(
        value: Any,
        *,
        maximum_seconds: float,
        milliseconds: bool,
    ) -> float:
        """Convert an already-schema-checked timeout without silently capping it."""

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            or value > int(maximum_seconds * (1000 if milliseconds else 1))
        ):
            raise PipelineError(
                ErrorCode.NATIVE_CONFIG_INVALID,
                "native timeout is outside its hard bound",
            )
        return value / 1000 if milliseconds else float(value)

    def _connect_timeout_seconds(self) -> float:
        return self._bounded_timeout_seconds(
            self._config["timeouts"]["pipe_connect_ms"],
            maximum_seconds=CONNECT_TIMEOUT_SECONDS,
            milliseconds=True,
        )

    def _method_timeout_seconds(self, method: str) -> float:
        try:
            field = METHOD_TIMEOUT_CONFIG_KEYS[method]
            maximum = METHOD_TIMEOUT_SECONDS[method]
        except KeyError as error:
            raise PipelineError(
                ErrorCode.NATIVE_PROTOCOL_INVALID,
                "native RPC method is not allowlisted",
            ) from error
        return self._bounded_timeout_seconds(
            self._config["timeouts"][field],
            maximum_seconds=maximum,
            milliseconds=True,
        )

    def connect(self) -> None:
        """Connect once and bind the pipe to the full selected process instance."""

        with self._lifecycle_lock:
            try:
                self._require_live_session()
                self._connect_locked()
                self._require_live_session()
            except PipelineError:
                self._invalidate_locked()
                raise

    def _connect_locked(self) -> None:
        """Connect while the lifecycle guard protects identity and transport state."""

        if self._is_invalid():
            raise PipelineError(ErrorCode.NATIVE_SESSION_INVALID, "session was invalidated")
        if self._transport is not None and self._transport_bound:
            return
        expected = inspect_process(cast(int, self._session["pid"]))
        if not self._process_matches(expected):
            self._invalidate_locked()
            raise PipelineError(ErrorCode.NATIVE_SESSION_INVALID, "selected process changed")
        pipe = self._transport
        if pipe is None:
            pipe = WindowsNamedPipe.connect(
                cast(str, self._session["pipe_name"]),
                timeout_seconds=self._connect_timeout_seconds(),
            )
            self._transport = pipe
        try:
            server_pid = pipe.server_pid
            if (
                not isinstance(server_pid, int)
                or isinstance(server_pid, bool)
                or server_pid != expected.pid
            ):
                raise PipelineError(ErrorCode.NATIVE_PIPE_INVALID, "pipe server PID differs")
            # Never trust the numeric PID returned by a connected pipe on its
            # own.  The process can exit and its PID can be reused between the
            # pre-connect inspection and this query.
            connected = inspect_process(server_pid)
            if (
                not self._process_matches(connected)
                or not self._same_process_instance(expected, connected)
            ):
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_INVALID,
                    "pipe server process instance differs",
                )
            self._connected_process = connected
            self._transport_bound = True
        except PipelineError:
            self._invalidate_locked()
            raise
        except (AttributeError, TypeError, ValueError) as error:
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_PIPE_INVALID,
                "pipe server identity is unavailable",
            ) from error

    @staticmethod
    def _same_process_instance(
        expected: ProcessIdentity,
        current: ProcessIdentity,
    ) -> bool:
        """Compare every process value that binds a PID to one host instance."""

        return (
            current.pid == expected.pid
            and current.windows_session_id == expected.windows_session_id
            and current.creation_time_100ns == expected.creation_time_100ns
            and current.instance_fingerprint == expected.instance_fingerprint
            and current.executable_fingerprint == expected.executable_fingerprint
        )

    def _process_matches(self, current: ProcessIdentity) -> bool:
        process = cast(Mapping[str, Any], self._session["process"])
        return (
            current.pid == self._session["pid"]
            and current.windows_session_id == self._session["windows_session_id"]
            and str(current.creation_time_100ns) == process["creation_time_100ns"]
            and current.instance_fingerprint == process["instance_fingerprint"]
            and current.executable_fingerprint == process["executable_fingerprint"]
        )

    def _session_expiry(self) -> Any:
        """Read the signed session expiry once for a timing or liveness check."""

        try:
            return self._session_expires_at
        except (
            CanonicalJsonError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
        ) as error:
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native session expiry invalid",
            ) from error

    def _expire_locked(self) -> None:
        """Retire the transport and report the one stable expiry outcome."""

        self._invalidate_locked()
        raise PipelineError(ErrorCode.NATIVE_SESSION_EXPIRED, "native session expired")

    def _session_has_expired(
        self,
        *,
        session_deadline: float | None = None,
    ) -> bool:
        """Test both clocks so a bounded RPC cannot outlive signed expiry."""

        deadline = self._session_deadline if session_deadline is None else session_deadline
        if time.monotonic() >= deadline:
            return True
        return utc_now() >= self._session_expiry()

    def _require_temporally_live_session(self) -> None:
        """Reapply wall-clock bounds without extending the fixed monotonic life."""

        wall_now = utc_now()
        try:
            validate_native_session_temporal_bounds(
                self._session_created_at,
                self._session_expires_at,
                now=wall_now,
            )
        except (CanonicalJsonError, TypeError, ValueError) as error:
            self._invalidate_locked()
            if wall_now >= self._session_expires_at:
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native session expired",
                ) from error
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native session wall clock is invalid",
            ) from error
        if time.monotonic() >= self._session_deadline:
            self._expire_locked()

    def _rpc_deadline(self, method: str) -> RequestTiming:
        """Cap one configured method timeout by the remaining session life."""

        self._require_temporally_live_session()
        expires = self._session_expiry()
        wall_now = utc_now()
        monotonic_now = time.monotonic()
        if wall_now >= expires:
            self._expire_locked()
        remaining = (expires - wall_now).total_seconds()
        if not math.isfinite(remaining) or remaining <= 0:
            self._expire_locked()
        session_deadline = min(self._session_deadline, monotonic_now + remaining)
        method_deadline = monotonic_now + self._method_timeout_seconds(method)
        deadline = min(method_deadline, session_deadline)
        if deadline <= monotonic_now:
            # The configured timeout cannot rescue a session with no positive
            # remaining lifetime, even when rounding makes the timestamps
            # appear equal.
            self._expire_locked()
        return RequestTiming(
            deadline=deadline,
            method_deadline=method_deadline,
            session_deadline=session_deadline,
        )

    def _deadline_checker(self, timing: RequestTiming) -> Callable[[str], None]:
        """Bind protocol/contract callbacks to this request's one budget."""

        return lambda stage: self._require_request_deadline(timing, stage)

    def _require_request_deadline(self, timing: RequestTiming, stage: str) -> None:
        """Check method/session expiry and keep all timeout failures terminal."""

        try:
            require_request_deadline(timing, stage)
        except PipelineError:
            self._invalidate_locked()
            raise

    def _response_timing(self) -> RequestTiming:
        """Return the timing captured by the response currently being parsed."""

        if self._last_response_timing is None:
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_PROTOCOL_INVALID,
                "native response timing is unavailable",
            )
        return self._last_response_timing

    def _require_live_session(
        self,
        *,
        session_deadline: float | None = None,
    ) -> None:
        if self._is_invalid():
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native session was invalidated",
            )
        self._require_temporally_live_session()
        if self._session_has_expired(session_deadline=session_deadline):
            self._expire_locked()
        current = inspect_process(cast(int, self._session["pid"]))
        if (
            not self._process_matches(current)
            or (
                self._connected_process is not None
                and not self._same_process_instance(
                    self._connected_process,
                    current,
                )
            )
        ):
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native process instance changed",
            )

    def _require_live_after_response(self, timing: RequestTiming, stage: str) -> None:
        """Recheck expiry/identity after a response-specific parse completes."""

        self._require_request_deadline(timing, stage)
        self._require_live_session(
            session_deadline=timing.session_deadline,
        )
        self._require_request_deadline(timing, stage)

    def bound_process_identity(self) -> ProcessIdentity:
        """Return the still-live exact instance bound after pipe connection."""

        with self._lifecycle_lock:
            self._require_live_session()
            if self._connected_process is None or not self._transport_bound:
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_INVALID,
                    "native pipe process was not bound",
                )
            return self._connected_process

    def invalidate(self) -> None:
        """Permanently close after any protocol, identity, or binding failure."""

        with self._lifecycle_lock:
            self._invalidate_locked()

    def _invalidate_locked(self) -> None:
        """Invalidate and close while the caller owns the lifecycle guard."""

        with self._state_lock:
            self._invalid = True
        if self._transport is not None:
            try:
                self._transport.close()
            finally:
                self._transport = None
        self._connected_process = None
        self._transport_bound = False
        self._last_response_timing = None

    def close(self) -> None:
        self.invalidate()

    def _reject_concurrent_rpc(self) -> None:
        """Reject a contender without closing the active owner's transport."""

        self._mark_invalid_while_busy()
        raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "concurrent native RPC")

    def _write_until(self, payload: bytes, *, deadline: float) -> int:
        """Write one protocol chunk against the original RPC deadline."""

        assert self._transport is not None
        writer = getattr(self._transport, "write_until", None)
        if callable(writer):
            return writer(payload, deadline=deadline)
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise TimeoutError("native bridge write timed out")
        return self._transport.write(payload, timeout)

    def _read_until(self, maximum: int, *, deadline: float) -> bytes:
        """Read one protocol chunk against the original RPC deadline."""

        assert self._transport is not None
        reader = getattr(self._transport, "read_until", None)
        if callable(reader):
            return reader(maximum, deadline=deadline)
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise TimeoutError("native bridge read timed out")
        return self._transport.read(maximum, timeout)

    def _request_locked(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Exchange one frame while the caller owns the non-reentrant guard."""

        timing: RequestTiming | None = None
        try:
            if self._is_invalid():
                self._invalidate_locked()
                raise PipelineError(ErrorCode.NATIVE_SESSION_INVALID, "native session was invalidated")
            self._require_live_session()
            self._connect_locked()
            # The post-connect identity has to be rechecked before the first
            # frame too: a server can exit or be replaced immediately after
            # GetNamedPipeServerProcessId succeeds.
            self._require_live_session()
            timing = self._rpc_deadline(method)
            self._last_response_timing = timing
            self._require_request_deadline(timing, "request setup")
            self._require_live_session(session_deadline=timing.session_deadline)
            if self._transport is None:
                self._invalidate_locked()
                raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "native transport unavailable")
            if self._transport.pending_bytes():
                self._invalidate_locked()
                raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "unsolicited native frame")
            request_id = new_request_id()
            if request_id in self._request_ids:
                self._invalidate_locked()
                raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "duplicate native request ID")
            self._request_ids.add(request_id)
            request = RpcRequest(request_id=request_id, method=method, params=dict(params))
            self._require_request_deadline(timing, "request envelope validation")
            envelope = validate_native_contract("request", request.envelope())
            self._require_request_deadline(timing, "request framing")
            write_all(
                lambda payload, _timeout: self._write_until(
                    payload,
                    deadline=timing.deadline,
                ),
                encode_frame(envelope, maximum=64 * 1024),
                deadline=timing.deadline,
            )
            self._require_request_deadline(timing, "response frame decoding")
            response = read_frame(
                lambda maximum, _timeout: self._read_until(
                    maximum,
                    deadline=timing.deadline,
                ),
                maximum=response_limit_for_method(method),
                deadline=timing.deadline,
                deadline_check=self._deadline_checker(timing),
                opaque_string_rules=opaque_embedded_json_rules("response"),
            )
            # The strict frame decoder receives only the response schema's
            # exact opaque-carrier paths. It enforces their UTF-8 caps before
            # outer NFC; nested same-named fields remain ordinary scalars.
            self._require_request_deadline(timing, "generic response schema validation")
            response = validate_native_contract(
                "response",
                response,
                deadline_check=self._deadline_checker(timing),
            )
            response = validate_response_envelope(
                response,
                request_id,
                deadline_check=self._deadline_checker(timing),
            )
            # Frame decoding and schema validation can consume enough time to
            # cross expiry even when I/O itself completed just in time.
            self._require_request_deadline(timing, "generic response validation")
            self._require_live_session(session_deadline=timing.session_deadline)
            self._require_request_deadline(timing, "response stream validation")
            if self._transport.pending_bytes():
                raise NativePipeClosed("unsolicited second native frame")
            if "error" in response:
                self._require_request_deadline(timing, "response error validation")
                code = response["error"]["code"]
                if code == "DOCUMENT_CHANGED":
                    raise PipelineError(ErrorCode.NATIVE_DOCUMENT_CHANGED, "bridge document changed")
                if code == "SESSION_EXPIRED":
                    raise PipelineError(ErrorCode.NATIVE_SESSION_EXPIRED, "bridge session expired")
                raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "bridge returned error")
            expected_kind = {
                "health": "health",
                "get_session": "session",
                "get_current_document": "document",
                "export_inventory": "inventory",
                "export_exact_geometry": "geometry",
            }[method]
            self._require_request_deadline(timing, "method-specific response validation")
            if response["result"].get("kind") != expected_kind:
                raise PipelineError(
                    ErrorCode.NATIVE_PROTOCOL_INVALID,
                    "bridge returned wrong method result",
                )
            # Do not let a concurrently invalidated/expired session return a
            # validated result after all protocol work has completed.
            self._require_request_deadline(timing, "response final validation")
            self._require_live_session(session_deadline=timing.session_deadline)
            self._require_request_deadline(timing, "response final return")
            if self._is_invalid():
                raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "concurrent native RPC")
            return cast(dict[str, Any], response["result"])
        except PipelineError as error:
            if timing is not None:
                self._require_request_deadline(timing, "response failure")
            if (
                error.code != ErrorCode.NATIVE_SESSION_EXPIRED
                and timing is not None
                and self._session_has_expired(
                    session_deadline=timing.session_deadline,
                )
            ):
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native session expired",
                ) from error
            self._invalidate_locked()
            raise
        except NativeOpaqueEmbeddedJsonError as error:
            if (
                timing is not None
                and self._session_has_expired(
                    session_deadline=timing.session_deadline,
                )
            ):
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native session expired",
                ) from error
            self._invalidate_locked()
            raise PipelineError(
                (
                    ErrorCode.NATIVE_GEOMETRY_INVALID
                    if method == "export_exact_geometry"
                    else ErrorCode.NATIVE_PROTOCOL_INVALID
                ),
                "native opaque JSON carrier rejected",
            ) from error
        except (
            NativePipeClosed,
            NativeProtocolError,
            RecursionError,
            TimeoutError,
            OSError,
            ValueError,
        ) as error:
            if (
                timing is not None
                and self._session_has_expired(
                    session_deadline=timing.session_deadline,
                )
            ):
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native session expired",
                ) from error
            self._invalidate_locked()
            raise protocol_error(error) from error

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Send one allowlisted request and reject every stream irregularity."""

        if not self._lifecycle_lock.acquire(blocking=False):
            # Do not close a live handle from the rejected caller: the owner
            # must finish its frame boundary before it can invalidate safely.
            self._reject_concurrent_rpc()
        try:
            return self._request_locked(method, params)
        finally:
            self._lifecycle_lock.release()

    def health(self) -> dict[str, Any]:
        return self.request("health", {"session_id": self._session["session_id"]})

    def get_session(self) -> dict[str, Any]:
        return self.request(
            "get_session",
            {
                "session_id": self._session["session_id"],
                "client_nonce": self._session["client_nonce"],
                "challenge": self._session["challenge"],
            },
        )

    def get_current_document(self) -> dict[str, Any]:
        return self.request(
            "get_current_document",
            {"session_id": self._session["session_id"]},
        )

    def export_inventory(self) -> dict[str, Any]:
        if not self._lifecycle_lock.acquire(blocking=False):
            self._reject_concurrent_rpc()
        try:
            result = self._request_locked(
                "export_inventory",
                {
                    "session_id": self._session["session_id"],
                    "expected_document_revision": self._session["current_document"][
                        "revision_fingerprint"
                    ],
                },
            )
            timing = self._response_timing()
            self._require_request_deadline(timing, "inventory result validation")
            if result.get("kind") != "inventory":
                raise PipelineError(
                    ErrorCode.NATIVE_PROTOCOL_INVALID,
                    "wrong native inventory response",
                )
            try:
                inventory = _embedded_inventory(
                    cast(str, result["inventory_json"]),
                    error=ErrorCode.NATIVE_PROTOCOL_INVALID,
                    deadline_check=self._deadline_checker(timing),
                )
            except (CanonicalJsonError, RecursionError, TypeError) as error:
                raise PipelineError(
                    ErrorCode.NATIVE_PROTOCOL_INVALID,
                    "native inventory JSON invalid",
                ) from error
            self._require_request_deadline(
                timing,
                "inventory binding semantic validation",
            )
            if (
                set(inventory) != {"document_revision_fingerprint", "inventory_digest"}
                or not all(
                    isinstance(inventory[key], str)
                    and _SHA256_PATTERN.fullmatch(inventory[key]) is not None
                    for key in inventory
                )
                or inventory["document_revision_fingerprint"]
                != self._session["current_document"]["revision_fingerprint"]
            ):
                raise PipelineError(
                    ErrorCode.NATIVE_DOCUMENT_CHANGED,
                    "native inventory binding drift",
                )
            self._require_request_deadline(
                timing,
                "inventory binding semantic validation",
            )
            self._require_live_after_response(timing, "inventory final validation")
            self._require_request_deadline(timing, "inventory final return")
            return inventory
        except PipelineError as error:
            timing = self._last_response_timing
            if timing is not None:
                self._require_request_deadline(timing, "inventory response failure")
            if (
                error.code != ErrorCode.NATIVE_SESSION_EXPIRED
                and self._session_has_expired(
                    session_deadline=(
                        timing.session_deadline if timing is not None else None
                    ),
                )
            ):
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native session expired",
                ) from error
            self._invalidate_locked()
            raise
        except (KeyError, RecursionError, TypeError, ValueError) as error:
            timing = self._last_response_timing
            if timing is not None:
                self._require_request_deadline(timing, "inventory response failure")
            if self._session_has_expired(
                session_deadline=(
                    timing.session_deadline if timing is not None else None
                ),
            ):
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native session expired",
                ) from error
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_PROTOCOL_INVALID,
                "native inventory response is invalid",
            ) from error
        finally:
            self._lifecycle_lock.release()

    def export_exact_geometry(self) -> dict[str, Any]:
        if not self._lifecycle_lock.acquire(blocking=False):
            self._reject_concurrent_rpc()
        try:
            result = self._request_locked(
                "export_exact_geometry",
                {
                    "session_id": self._session["session_id"],
                    "expected_document_revision": self._session["current_document"][
                        "revision_fingerprint"
                    ],
                },
            )
            timing = self._response_timing()
            self._require_request_deadline(timing, "geometry result validation")
            if result.get("kind") != "geometry":
                raise PipelineError(
                    ErrorCode.NATIVE_PROTOCOL_INVALID,
                    "wrong native geometry response",
                )
            try:
                decoded_geometry = _embedded_geometry(
                    cast(str, result.get("geometry_json")),
                    error=ErrorCode.NATIVE_GEOMETRY_INVALID,
                    deadline_check=self._deadline_checker(timing),
                )
                self._require_request_deadline(
                    timing,
                    "geometry session binding validation",
                )
                export, _checked_session = require_geometry_export_matches_session(
                    decoded_geometry,
                    self._session,
                    deadline_check=self._deadline_checker(timing),
                )
                self._require_request_deadline(
                    timing,
                    "geometry session binding validation",
                )
            except (CanonicalJsonError, RecursionError) as error:
                raise PipelineError(
                    ErrorCode.NATIVE_GEOMETRY_INVALID,
                    "bridge geometry invalid",
                ) from error
            self._require_live_after_response(timing, "geometry final validation")
            self._require_request_deadline(timing, "geometry final return")
            return export
        except PipelineError as error:
            timing = self._last_response_timing
            if timing is not None:
                self._require_request_deadline(timing, "geometry response failure")
            if (
                error.code != ErrorCode.NATIVE_SESSION_EXPIRED
                and self._session_has_expired(
                    session_deadline=(
                        timing.session_deadline if timing is not None else None
                    ),
                )
            ):
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native session expired",
                ) from error
            self._invalidate_locked()
            raise
        except (KeyError, RecursionError, TypeError, ValueError) as error:
            timing = self._last_response_timing
            if timing is not None:
                self._require_request_deadline(timing, "geometry response failure")
            if self._session_has_expired(
                session_deadline=(
                    timing.session_deadline if timing is not None else None
                ),
            ):
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native session expired",
                ) from error
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_GEOMETRY_INVALID,
                "bridge geometry invalid",
            ) from error
        finally:
            self._lifecycle_lock.release()


def _require_configured_bridge_identity(
    result: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    kind: str,
    require_protocol_version: bool,
) -> None:
    """Validate one response's configured read-only bridge identity."""

    if result.get("kind") != kind:
        raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "native handshake result kind")
    if require_protocol_version and (
        result.get("protocol_major") != PROTOCOL_MAJOR
        or result.get("protocol_minor") != PROTOCOL_MINOR
    ):
        raise PipelineError(ErrorCode.NATIVE_PROTOCOL_INVALID, "native protocol version drift")
    adapter = config["adapter"]
    plugin = config["plugins"]["readback"]
    host_compatibility = config["host_compatibility"]
    host = {
        "product": host_compatibility["host_product"],
        "release": host_compatibility["host_release"],
        "runtime": host_compatibility["host_runtime"],
        "mode": host_compatibility["audit_host_mode"],
    }
    required = set(config["required_capabilities"])
    if (
        result.get("adapter") != adapter
        or result.get("plugin", {}).get("id") != plugin["id"]
        or result.get("plugin", {}).get("version") != plugin["version"]
        or result.get("plugin", {}).get("fingerprint") != plugin["sha256"]
        or result.get("host") != host
        or not required.issubset(set(result.get("capabilities", [])))
    ):
        raise PipelineError(ErrorCode.NATIVE_CAPABILITY_MISMATCH, "native bridge identity drift")


def _require_bridge_identity(
    health: Mapping[str, Any],
    handshake: Mapping[str, Any],
    config: Mapping[str, Any],
    session_id: str,
    client_nonce: str,
    challenge: str,
) -> None:
    _require_configured_bridge_identity(
        health,
        config,
        kind="health",
        require_protocol_version=True,
    )
    _require_configured_bridge_identity(
        handshake,
        config,
        kind="session",
        require_protocol_version=False,
    )
    # A configured capability requirement is deliberately a subset gate: an
    # adapter may expose an additional read-only capability.  It must not,
    # however, drift between the two pre-session responses.  The completed
    # descriptor freezes this exact list and every later geometry response
    # must repeat it byte-for-byte as part of its session binding.
    if any(
        health.get(field) != handshake.get(field)
        for field in ("adapter", "plugin", "host", "capabilities")
    ):
        raise PipelineError(
            ErrorCode.NATIVE_CAPABILITY_MISMATCH,
            "native handshake identity drift",
        )
    bridge_nonce = handshake.get("bridge_nonce")
    response = handshake.get("challenge_response")
    try:
        expected_response = derive_challenge_response(
            client_nonce,
            challenge,
            bridge_nonce,
            session_id=session_id,
        )
    except (NativeProtocolError, TypeError):
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_INVALID,
            "native bridge challenge mismatch",
        ) from None
    if not isinstance(response, str) or not compare_digest(response, expected_response):
        raise PipelineError(ErrorCode.NATIVE_SESSION_INVALID, "native bridge challenge mismatch")


@dataclass(frozen=True)
class NativeBridgeHandshakeContext:
    """Explicit non-persisted state used only to establish a new session.

    This deliberately has no descriptor-shaped placeholder: bridge nonce,
    challenge response, adapter/plugin attestation, capabilities, and the
    saved document are unavailable until the ordered handshake completes.
    """

    prepared_process: ProcessIdentity
    pipe_name: str
    protocol_version: str
    session_id: str
    client_nonce: str
    challenge: str
    mode: str
    created_at: datetime


class NativeBridgeHandshakeClient(NativeBridgeClient):
    """Fail-closed pre-session client restricted to ``health`` then session.

    The regular :class:`NativeBridgeClient` intentionally accepts only a
    fully persisted and integrity-checked descriptor.  This separate client
    shares its single-flight framing, pipe identity, and deadline machinery,
    but never calls that constructor and never creates a fake descriptor to
    get through it.  Its only state is the explicit local preparation context.
    """

    _HANDSHAKE_METHODS = ("health", "get_session")

    def __init__(
        self,
        context: NativeBridgeHandshakeContext,
        *,
        config: Mapping[str, Any],
        transport: PipeTransport | None = None,
    ) -> None:
        self._context = context
        self._config = validate_native_contract("config", config)
        self._validate_context()
        self._transport = transport
        # These fields are the existing protocol client's transport state.
        # There is intentionally no ``_session`` attribute in this class.
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._invalid = False
        self._request_ids: set[str] = set()
        self._connected_process: ProcessIdentity | None = None
        self._transport_bound = False
        self._last_response_timing: RequestTiming | None = None
        self._next_handshake_method = 0
        self._health_result: dict[str, Any] | None = None

    def _validate_context(self) -> None:
        """Reject malformed local preparation state before a pipe is opened."""

        context = self._context
        if (
            context.protocol_version != PROTOCOL_VERSION
            or context.mode != "read_only"
            or not isinstance(context.created_at, datetime)
            or context.created_at.tzinfo is None
            or context.created_at.utcoffset() != timedelta(0)
            or not isinstance(context.prepared_process, ProcessIdentity)
            or not isinstance(context.prepared_process.pid, int)
            or isinstance(context.prepared_process.pid, bool)
            or context.prepared_process.pid <= 0
            or not isinstance(context.prepared_process.windows_session_id, int)
            or isinstance(context.prepared_process.windows_session_id, bool)
            or context.prepared_process.windows_session_id < 0
            or not isinstance(context.prepared_process.creation_time_100ns, int)
            or isinstance(context.prepared_process.creation_time_100ns, bool)
            or context.prepared_process.creation_time_100ns < 0
            or _SHA256_PATTERN.fullmatch(
                context.prepared_process.instance_fingerprint
            )
            is None
            or (
                context.prepared_process.executable_fingerprint != "unavailable"
                and _SHA256_PATTERN.fullmatch(
                    context.prepared_process.executable_fingerprint
                )
                is None
            )
        ):
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native handshake preparation is invalid",
            )
        validate_pipe_name(context.pipe_name)
        try:
            validate_native_session_temporal_bounds(
                context.created_at,
                context.created_at + MAX_NATIVE_SESSION_LIFETIME,
                now=utc_now(),
            )
            derive_challenge_response(
                context.client_nonce,
                context.challenge,
                "x" * 43,
                session_id=context.session_id,
                protocol_version=context.protocol_version,
            )
        except (NativeProtocolError, TypeError, ValueError):
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native handshake preparation is invalid",
            ) from None

    def _process_matches(self, current: ProcessIdentity) -> bool:
        return self._same_process_instance(self._context.prepared_process, current)

    def _session_has_expired(
        self,
        *,
        session_deadline: float | None = None,
    ) -> bool:
        if session_deadline is not None and time.monotonic() >= session_deadline:
            return True
        return utc_now() >= self._context.created_at + MAX_NATIVE_SESSION_LIFETIME

    def _require_temporally_live_context(self) -> None:
        """Reject future/expired preparation before connecting or writing."""

        wall_now = utc_now()
        try:
            validate_native_session_temporal_bounds(
                self._context.created_at,
                self._context.created_at + MAX_NATIVE_SESSION_LIFETIME,
                now=wall_now,
            )
        except (CanonicalJsonError, TypeError, ValueError) as error:
            self._invalidate_locked()
            if wall_now >= self._context.created_at + MAX_NATIVE_SESSION_LIFETIME:
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED,
                    "native handshake expired",
                ) from error
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native handshake preparation is invalid",
            ) from error

    def _rpc_deadline(self, method: str) -> RequestTiming:
        if method not in self._HANDSHAKE_METHODS:
            raise PipelineError(
                ErrorCode.NATIVE_PROTOCOL_INVALID,
                "native pre-handshake method is not allowlisted",
            )
        monotonic_now = time.monotonic()
        method_deadline = monotonic_now + self._method_timeout_seconds(method)
        return RequestTiming(
            deadline=method_deadline,
            method_deadline=method_deadline,
            # ``require_request_deadline`` distinguishes session expiry from
            # a method deadline.  Infinity keeps pre-session failures in the
            # latter stable category without inventing a persisted expiry.
            session_deadline=math.inf,
        )

    def _require_live_session(
        self,
        *,
        session_deadline: float | None = None,
    ) -> None:
        if self._is_invalid():
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native handshake was invalidated",
            )
        self._require_temporally_live_context()
        if self._session_has_expired(session_deadline=session_deadline):
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_PROTOCOL_INVALID,
                "native handshake timed out",
            )
        current = inspect_process(self._context.prepared_process.pid)
        if (
            not self._process_matches(current)
            or (
                self._connected_process is not None
                and not self._same_process_instance(
                    self._connected_process,
                    current,
                )
            )
        ):
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native process instance changed",
            )

    def _connect_locked(self) -> None:
        """Bind the prepared instance without a persisted descriptor."""

        if self._is_invalid():
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "native handshake was invalidated",
            )
        if self._transport is not None and self._transport_bound:
            return
        expected = self._context.prepared_process
        current = inspect_process(expected.pid)
        if not self._same_process_instance(expected, current):
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "selected process changed",
            )
        pipe = self._transport
        if pipe is None:
            pipe = WindowsNamedPipe.connect(
                self._context.pipe_name,
                timeout_seconds=self._connect_timeout_seconds(),
            )
            self._transport = pipe
        try:
            server_pid = pipe.server_pid
            if (
                not isinstance(server_pid, int)
                or isinstance(server_pid, bool)
                or server_pid != expected.pid
            ):
                raise PipelineError(
                    ErrorCode.NATIVE_PIPE_INVALID,
                    "pipe server PID differs",
                )
            connected = inspect_process(server_pid)
            if not self._same_process_instance(expected, connected):
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_INVALID,
                    "pipe server process instance differs",
                )
            self._connected_process = connected
            self._transport_bound = True
        except PipelineError:
            self._invalidate_locked()
            raise
        except (AttributeError, TypeError, ValueError) as error:
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_PIPE_INVALID,
                "pipe server identity is unavailable",
            ) from error

    def _expected_handshake_params(self, method: str) -> dict[str, str]:
        if method == "health":
            return {"session_id": self._context.session_id}
        if method == "get_session":
            return {
                "session_id": self._context.session_id,
                "client_nonce": self._context.client_nonce,
                "challenge": self._context.challenge,
            }
        raise PipelineError(
            ErrorCode.NATIVE_PROTOCOL_INVALID,
            "native pre-handshake method is not allowlisted",
        )

    def _request_ordered(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        validate_result: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Issue the exact next internal pre-session RPC under one flight lock."""

        if not self._lifecycle_lock.acquire(blocking=False):
            self._reject_concurrent_rpc()
        try:
            expected_method = (
                self._HANDSHAKE_METHODS[self._next_handshake_method]
                if self._next_handshake_method < len(self._HANDSHAKE_METHODS)
                else None
            )
            if (
                method != expected_method
                or method not in self._HANDSHAKE_METHODS
                or dict(params) != self._expected_handshake_params(method)
            ):
                self._invalidate_locked()
                raise PipelineError(
                    ErrorCode.NATIVE_PROTOCOL_INVALID,
                    "native pre-handshake method order is invalid",
                )
            result = self._request_locked(method, params)
            if validate_result is not None:
                validate_result(result)
            self._next_handshake_method += 1
            return result
        except PipelineError:
            # Method-specific health/session validation remains inside the
            # same single-flight critical section as framing. A competing
            # caller can therefore never turn a rejected handshake response
            # into a successful return.
            self._invalidate_locked()
            raise
        finally:
            self._lifecycle_lock.release()

    def request(self, _method: str, _params: Mapping[str, Any]) -> dict[str, Any]:
        """Deny a generic RPC surface until a full descriptor exists."""

        self._reject_prehandshake_method()

    def health(self) -> dict[str, Any]:
        def validate_health(result: dict[str, Any]) -> None:
            _require_configured_bridge_identity(
                result,
                self._config,
                kind="health",
                require_protocol_version=True,
            )
            self._health_result = result

        return self._request_ordered(
            "health",
            self._expected_handshake_params("health"),
            validate_result=validate_health,
        )

    def get_session(self) -> dict[str, Any]:
        if self._health_result is None:
            self._reject_prehandshake_order()

        def validate_session(result: dict[str, Any]) -> None:
            assert self._health_result is not None
            _require_bridge_identity(
                self._health_result,
                result,
                self._config,
                self._context.session_id,
                self._context.client_nonce,
                self._context.challenge,
            )

        return self._request_ordered(
            "get_session",
            self._expected_handshake_params("get_session"),
            validate_result=validate_session,
        )

    def _reject_prehandshake_order(self) -> None:
        if not self._lifecycle_lock.acquire(blocking=False):
            self._reject_concurrent_rpc()
        try:
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_PROTOCOL_INVALID,
                "native pre-handshake method order is invalid",
            )
        finally:
            self._lifecycle_lock.release()

    def _reject_prehandshake_method(self) -> None:
        if not self._lifecycle_lock.acquire(blocking=False):
            self._reject_concurrent_rpc()
        try:
            self._invalidate_locked()
            raise PipelineError(
                ErrorCode.NATIVE_PROTOCOL_INVALID,
                "native pre-handshake method is not allowlisted",
            )
        finally:
            self._lifecycle_lock.release()

    def get_current_document(self) -> dict[str, Any]:
        self._reject_prehandshake_method()

    def export_inventory(self) -> dict[str, Any]:
        self._reject_prehandshake_method()

    def export_exact_geometry(self) -> dict[str, Any]:
        self._reject_prehandshake_method()

    def complete_session_descriptor(self) -> dict[str, Any]:
        """Perform the two-message handshake and seal one full descriptor."""

        self.health()
        handshake = self.get_session()
        if not self._lifecycle_lock.acquire(blocking=False):
            self._reject_concurrent_rpc()
        try:
            self._require_live_session()
            if self._connected_process is None or not self._transport_bound:
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_INVALID,
                    "native pipe process was not bound",
                )
            connected = self._connected_process
            expires_at = self._context.created_at + MAX_NATIVE_SESSION_LIFETIME
            publication_now = utc_now()
            try:
                validate_native_session_temporal_bounds(
                    self._context.created_at,
                    expires_at,
                    now=publication_now,
                )
            except (CanonicalJsonError, TypeError, ValueError) as error:
                raise PipelineError(
                    ErrorCode.NATIVE_SESSION_EXPIRED
                    if publication_now >= expires_at
                    else ErrorCode.NATIVE_SESSION_INVALID,
                    "native handshake expired before descriptor publication",
                ) from error
            artifact = {
                "schema_version": "liang-pingfa/native-bridge-session/v1",
                "session_id": self._context.session_id,
                "created_at": format_utc(self._context.created_at),
                "expires_at": format_utc(expires_at),
                "mode": self._context.mode,
                "pid": connected.pid,
                "windows_session_id": connected.windows_session_id,
                "process": {
                    "instance_fingerprint": connected.instance_fingerprint,
                    "creation_time_100ns": str(connected.creation_time_100ns),
                    "executable_fingerprint": connected.executable_fingerprint,
                },
                "pipe_name": self._context.pipe_name,
                "client_nonce": self._context.client_nonce,
                "challenge": self._context.challenge,
                "bridge_nonce": handshake["bridge_nonce"],
                "challenge_response": handshake["challenge_response"],
                "adapter": handshake["adapter"],
                "plugin": handshake["plugin"],
                "host": handshake["host"],
                "current_document": handshake["current_document"],
                "capabilities": handshake["capabilities"],
            }
            completed = validate_native_contract(
                "session",
                attach_integrity(artifact),
                now=publication_now,
            )
            # Re-run the full configured compatibility gate only after the
            # descriptor is complete; no incomplete artifact can reach the
            # strict persisted-session client.
            native_host_binding(completed, self._config)
            return completed
        except PipelineError:
            self._invalidate_locked()
            raise
        finally:
            self._lifecycle_lock.release()


def prepare_native_session(
    *,
    pid: int,
    pipe_name: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach once to an explicit read-only bridge and return a sealed session.

    The returned descriptor remains private in memory. The CLI persists it
    only through :func:`write_private_native_session_descriptor`, which
    establishes the required no-replace and DACL boundary.
    """

    _require_windows()
    validate_pipe_name(pipe_name)
    checked_config = validate_native_contract("config", config)
    validate_native_installation(checked_config)
    identity = inspect_process(pid)
    context = NativeBridgeHandshakeContext(
        prepared_process=identity,
        pipe_name=pipe_name,
        protocol_version=PROTOCOL_VERSION,
        session_id="native-session-" + secrets.token_hex(16),
        client_nonce=new_nonce(),
        challenge=new_nonce(),
        mode="read_only",
        created_at=utc_now(),
    )
    client = NativeBridgeHandshakeClient(context, config=checked_config)
    try:
        return client.complete_session_descriptor()
    finally:
        client.close()


def write_private_native_session_descriptor(
    path: Path,
    session: Mapping[str, Any],
    *,
    backend: FileOwnershipBackend | None = None,
    acl_reader: Callable[[OwnedPath], ComponentDacl] | None = None,
    trusted_parent_sids: frozenset[str] | None = None,
) -> Path:
    """Create one secret-bearing descriptor under a verified private parent.

    Unlike public audit/plan artifacts, this function never routes the
    descriptor through generic artifact publication.  It retains the full
    no-follow ancestor chain and the newly-created file handle while applying
    and reading back the current-user/SYSTEM-only DACL.
    """

    _require_windows()
    checked_session = validate_native_contract("session", session)
    try:
        lexical = lexical_absolute_path(path)
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_INVALID,
            "private session descriptor path is invalid",
        ) from error
    if (
        lexical.suffix.casefold() != ".json"
        or not lexical.name
        or _is_claimed_session_basename(lexical.name)
    ):
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_INVALID,
            "private session descriptor name is invalid",
        )
    selected_backend = backend or platform_backend(require_windows=True)
    selected_acl_reader = acl_reader or _read_component_dacl
    trusted = trusted_parent_sids or frozenset(
        {
            current_user_sid(),
            _TRUSTED_SYSTEM_SID,
            _TRUSTED_ADMINISTRATORS_SID,
        }
    )
    chain = None
    opened: OwnedPath | None = None
    published = False
    try:
        chain = acquire_lexical_directory_chain(lexical.parent, selected_backend)
        validate_private_staging_ancestry(chain.path, selected_backend)
        chain.require_binding()
        for component in chain.components:
            validate_component_dacl(
                selected_acl_reader(component.owned),
                is_directory=True,
                trusted_sids=trusted,
                # A normal Program Files chain can be owned by the Windows
                # Modules Installer while its DACL remains non-writable to
                # untrusted principals. Ownership alone is not a writer
                # bypass; the object-aware DACL evaluation below still gates
                # every retained ancestor.
                allow_trustedinstaller_owner=True,
            )
        private_creator = getattr(selected_backend, "create_private_file", None)
        if not callable(private_creator):
            raise OwnershipCleanupError("private session file API is unavailable")
        # The final retained parent may use a long canonical spelling where a
        # caller supplied a valid 8.3 lexical alias. Create below the held
        # parent spelling rather than requiring textual alias equality.
        creation_path = chain.path / lexical.name
        opened = private_creator(creation_path)
        initial = opened.capture_binding()
        final_path = opened.final_path()
        if (
            initial.is_directory
            or final_path.name.casefold() != lexical.name.casefold()
            or os.path.normcase(os.path.normpath(os.fspath(final_path.parent)))
            != os.path.normcase(os.path.normpath(os.fspath(chain.path)))
        ):
            raise OwnershipLostError("private session descriptor lost its creation binding")
        # The exclusive creation handle prevents a replacement while the
        # Windows security APIs apply and read back the protected DACL.
        secure_private_staging_file(opened, selected_backend)
        chain.require_binding()
        opened.write_bytes(canonical_json_bytes(checked_session) + b"\n")
        # Verify the restrictive descriptor DACL again after serialization and
        # before the retained handle is released to its one-use consumer.
        secure_private_staging_file(opened, selected_backend)
        final = opened.capture_binding()
        final_after_write_path = opened.final_path()
        if (
            final.is_directory
            or not final.same_identity_and_content(
                OwnedPathBinding(
                    path=initial.path,
                    identity=initial.identity,
                    byte_size=final.byte_size,
                    sha256=final.sha256,
                    is_directory=False,
                )
            )
            or os.path.normcase(os.path.normpath(os.fspath(final_after_write_path)))
            != os.path.normcase(os.path.normpath(os.fspath(final_path)))
        ):
            raise OwnershipLostError("private session descriptor binding drifted")
        published = True
        return final_path
    except PipelineError:
        raise
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_INVALID,
            "private session descriptor cannot be created",
        ) from error
    finally:
        cleanup_error: BaseException | None = None
        if opened is not None:
            try:
                if not published:
                    # A just-created descriptor remains owned by this retained
                    # handle even when DACL application/readback failed.
                    opened.request_delete()
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
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "private session descriptor cleanup failed",
            ) from cleanup_error


def native_doctor_status(config_path: Path | None) -> dict[str, str]:
    """Return a cardinality-independent and path-free native readiness event."""

    if os.name != "nt":
        return {
            "status": "not_ready",
            "command": "native-doctor",
            "windows": "unsupported",
            "config": "not_checked",
            "core_console": "not_checked",
            "plugins": "not_checked",
            "protocol": "not_checked",
            "per_file_compatibility": "audit_required",
            "integration_claim": "external-adapter-not-validated",
        }
    if config_path is None:
        return {
            "status": "not_ready",
            "command": "native-doctor",
            "windows": "ready",
            "config": "not_supplied",
            "core_console": "not_checked",
            "plugins": "not_checked",
            "protocol": "not_checked",
            "per_file_compatibility": "audit_required",
            "integration_claim": "external-adapter-not-validated",
        }
    try:
        config = load_native_config(config_path)
        validate_native_installation(config)
    except PipelineError:
        return {
            "status": "not_ready",
            "command": "native-doctor",
            "windows": "ready",
            "config": "invalid_or_unavailable",
            "core_console": "not_ready",
            "plugins": "not_ready",
            "protocol": "not_ready",
            "per_file_compatibility": "audit_required",
            "integration_claim": "external-adapter-not-validated",
        }
    return {
        "status": "ok",
        "command": "native-doctor",
        "windows": "ready",
        "config": "validated",
        "core_console": "fingerprint_validated",
        "plugins": "fingerprints_validated",
        "protocol": "v1-configured",
        "per_file_compatibility": "audit_required",
        "integration_claim": "external-adapter-not-validated",
    }


@contextmanager
def consume_native_session(path: Path) -> Any:
    """Atomically claim, hold, and destroy a private one-use session file.

    A same-directory no-replace rename happens before any descriptor bytes are
    read or any pipe is usable.  The original spelling is never reopened;
    cleanup acts only through the retained claimed-file handle so a later
    replacement is preserved rather than deleted.
    """

    _require_windows()
    try:
        lexical = lexical_absolute_path(path)
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_INVALID,
            "session descriptor path is invalid",
        ) from error
    if (
        not lexical.name
        or lexical.suffix.casefold() != ".json"
        or _is_claimed_session_basename(lexical.name)
    ):
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_INVALID,
            "session descriptor name is invalid or stale",
        )
    backend = platform_backend(require_windows=True)
    chain = None
    opened: OwnedPath | None = None
    original_binding: OwnedPathBinding | None = None
    claimed_binding: OwnedPathBinding | None = None
    claimed_path: Path | None = None
    rename_completed = False
    try:
        chain = acquire_lexical_directory_chain(lexical.parent, backend)
        chain.require_binding()
        if isinstance(backend, WindowsFileOwnershipBackend):
            trusted = frozenset(
                {
                    current_user_sid(),
                    _TRUSTED_SYSTEM_SID,
                    _TRUSTED_ADMINISTRATORS_SID,
                }
            )
            for component in chain.components:
                validate_component_dacl(
                    _read_component_dacl(component.owned),
                    is_directory=True,
                    trusted_sids=trusted,
                    allow_trustedinstaller_owner=True,
                )
            chain.require_binding()
        else:
            # Generated test backends model the same ancestry boundary without
            # treating POSIX permissions as a Windows DACL.
            probe = getattr(backend, "validate_private_artifact_ancestry", None)
            if callable(probe):
                probe(chain.path)
        opened = backend.open_existing_file(lexical, for_delete=True)
        original = opened.capture_binding()
        original_binding = original
        if (
            original.is_directory
            or original.sha256 is None
            or not backend.path_matches_binding(lexical, original)
        ):
            raise OwnershipLostError("session descriptor is not a bound regular file")
        # The one-use claim may only begin after the exact pre-existing final
        # file proves it retained the current-user/SYSTEM-only DACL.  A broad
        # readable JSON is rejected without consuming or renaming it.
        owner_before_claim = verify_private_staging_file(opened, backend)
        for _attempt in range(16):
            candidate = chain.path / (
                ".liang-pingfa-native-session-claimed-"
                + secrets.token_hex(32)
                + ".json"
            )
            try:
                opened.rename_no_replace(candidate)
                claimed_path = candidate
                # Set this immediately after the irreversible rename. A later
                # binding/parse failure must still delete this exact held
                # secret file rather than merely closing its handle.
                rename_completed = True
                break
            except DestinationExistsError:
                continue
        if claimed_path is None:
            raise OwnershipLostError("session descriptor cannot be claimed")
        claimed_binding = opened.capture_binding()
        final_path = opened.final_path()
        if (
            claimed_binding.is_directory
            or not claimed_binding.same_identity_and_content(original)
            or os.path.normcase(os.path.normpath(os.fspath(final_path)))
            != os.path.normcase(os.path.normpath(os.fspath(claimed_path)))
            or not backend.path_matches_binding(claimed_path, claimed_binding)
        ):
            raise OwnershipLostError("claimed session descriptor binding differs")
        owner_after_claim = verify_private_staging_file(opened, backend)
        if (
            owner_before_claim is not None
            and owner_after_claim is not None
            and owner_before_claim != owner_after_claim
        ):
            raise OwnershipLostError("claimed session descriptor owner changed")
        payload = b"".join(opened.read_chunks())
        try:
            session = validate_native_contract(
                "session",
                strict_native_json(payload.decode("utf-8", errors="strict")),
            )
        except (UnicodeDecodeError, CanonicalJsonError, PipelineError, RecursionError) as error:
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "claimed session descriptor is invalid",
            ) from error
        if payload != canonical_json_bytes(session) + b"\n":
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "claimed session descriptor is not canonical",
            )
        owner_after_validation = verify_private_staging_file(opened, backend)
        if (
            not opened.capture_binding().same_identity_and_content(claimed_binding)
            or (
                owner_after_claim is not None
                and owner_after_validation is not None
                and owner_after_claim != owner_after_validation
            )
        ):
            raise OwnershipLostError("claimed session descriptor changed while read")
        yield session
    except PipelineError:
        raise
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_SESSION_INVALID,
            "session descriptor cannot be claimed",
        ) from error
    finally:
        cleanup_error: BaseException | None = None
        if opened is not None:
            if not rename_completed:
                try:
                    opened.close()
                except (OSError, OwnershipError) as error:
                    cleanup_error = error
            else:
                try:
                    if claimed_path is None or original_binding is None:
                        raise OwnershipLostError(
                            "renamed session descriptor has no retained identity"
                        )
                    if claimed_binding is not None:
                        try:
                            current = opened.capture_binding()
                        except (OSError, OwnershipError):
                            # The original pre-rename binding plus the retained
                            # exclusive handle is still the deletion authority.
                            # Do not strand a secret merely because diagnostic
                            # rebinding failed after the rename completed.
                            opened.request_delete()
                            opened.close()
                        else:
                            if not current.same_identity_and_content(claimed_binding):
                                # A successfully captured but different
                                # binding means ownership is no longer proven.
                                # Preserve the named replacement and surface
                                # cleanup failure rather than treating any
                                # deletion as a successful secret cleanup.
                                raise OwnershipLostError(
                                    "claimed session descriptor ownership was lost"
                                )
                            else:
                                dispose_retained_owned_path(opened, claimed_binding)
                    else:
                        # ``capture_binding`` failed immediately after rename.
                        # The handle still names the previously proved object,
                        # so delete it directly rather than leaking a claimed
                        # descriptor that contains pipe and nonce secrets.
                        opened.request_delete()
                        opened.close()
                    opened = None
                    if backend.path_exists(claimed_path):
                        raise OwnershipLostError(
                            "claimed session descriptor replacement survived cleanup"
                        )
                except (OSError, OwnershipError) as error:
                    cleanup_error = error
                finally:
                    if opened is not None:
                        try:
                            opened.close()
                        except (OSError, OwnershipError) as close_error:
                            if cleanup_error is None:
                                cleanup_error = close_error
        if chain is not None:
            try:
                chain.close()
            except (OSError, OwnershipError) as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            raise PipelineError(
                ErrorCode.NATIVE_SESSION_INVALID,
                "claimed session descriptor cleanup failed",
            ) from cleanup_error
