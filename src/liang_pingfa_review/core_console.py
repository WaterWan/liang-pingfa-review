"""Copy-only, fixed-script Core Console orchestration.

This module does not implement a proprietary plugin.  It only invokes an
explicitly configured external executable against a private copy with a
three-line fixed script and validates the external conformance artifact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import secrets
import subprocess
import threading
from typing import Any, Literal

from .errors import ErrorCode, PipelineError
from .native_bridge import (
    NativeInstallationLeases,
    acquire_native_installation_leases,
)
from .native_contracts import (
    load_native_json_value,
    require_active_native_contract,
)
from .native_protocol import MAX_NATIVE_CONSOLE_RESULT_BYTES
from .temporary import PrivateWorkspace


_MAX_CONSOLE_STREAM_BYTES = 64 * 1024
# Retain the private spelling for compatibility with focused launcher tests,
# but keep the public protocol constant authoritative.
_MAX_CONSOLE_RESULT_BYTES = MAX_NATIVE_CONSOLE_RESULT_BYTES
_MAX_CONSOLE_EXPORT_BYTES = 32 * 1024 * 1024
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_HANDLE_FLAG_INHERIT = 0x00000001
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x00000102
_INFINITE = 0xFFFFFFFF
_STILL_ACTIVE = 259
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def fixed_script_content(plugin_path: Path, command: str) -> bytes:
    """Return the only permitted Core Console script body.

    The manifest path deliberately does not appear here; it reaches an
    external plugin solely through ``LIANG_PINGFA_NATIVE_MANIFEST``.
    """

    if command not in {
        "LPF_NATIVE_EXECUTE_MANIFEST",
        "LPF_NATIVE_EXPORT_MANIFEST",
    }:
        raise PipelineError(ErrorCode.NATIVE_CONFIG_INVALID, "Core Console command is not fixed")
    path = os.fspath(plugin_path)
    if (
        not path
        or '"' in path
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in path)
    ):
        raise PipelineError(ErrorCode.NATIVE_CONFIG_INVALID, "plugin path is unsafe")
    return f'_.NETLOAD\r\n"{path}"\r\n{command}\r\n'.encode("utf-8")


def write_fixed_script(
    workspace: PrivateWorkspace,
    path: Path,
    plugin_path: Path,
    command: str,
) -> Path:
    """Create a workspace-owned exact script before launching any process."""

    opened = workspace.create_owned_file(path)
    try:
        opened.write_bytes(fixed_script_content(plugin_path, command))
        return workspace.seal_owned_file(opened)
    except BaseException:
        try:
            workspace.discard_owned_file(opened)
        except BaseException:
            pass
        raise


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfo),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _JobBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _WindowsKillJob:
    """Required kill-on-close Job Object created before every child process."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise PipelineError(
                ErrorCode.WINDOWS_PLATFORM_REQUIRED,
                "Core Console containment is Windows-only",
            )
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError) as error:
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console Job Object API is unavailable",
            ) from error
        create = kernel32.CreateJobObjectW
        create.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE
        handle = int(create(None, None) or 0)
        if not handle or handle == _INVALID_HANDLE_VALUE:
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console Job Object cannot be created",
            )
        self._kernel32 = kernel32
        self._handle: int | None = handle
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        set_info = kernel32.SetInformationJobObject
        set_info.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        set_info.restype = wintypes.BOOL
        if not set_info(
            wintypes.HANDLE(handle),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            self.close()
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console Job Object cannot be configured",
            )

    def assign(self, process_handle: int) -> None:
        """Assign the still-suspended primary process before it can run."""

        if self._handle is None:
            raise PipelineError(ErrorCode.NATIVE_CONSOLE_FAILURE, "Job Object was released")
        assign = self._kernel32.AssignProcessToJobObject
        assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign.restype = wintypes.BOOL
        if not assign(wintypes.HANDLE(self._handle), wintypes.HANDLE(process_handle)):
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console cannot be assigned to Job Object",
            )

    def _active_processes(self) -> int:
        if self._handle is None:
            return 0
        query = self._kernel32.QueryInformationJobObject
        query.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        query.restype = wintypes.BOOL
        information = _JobBasicAccountingInformation()
        if not query(
            wintypes.HANDLE(self._handle),
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        ):
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console Job Object cannot be queried",
            )
        return int(information.ActiveProcesses)

    def terminate_and_wait(
        self,
        process: "_WindowsConsoleProcess",
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Terminate the complete job and prove descendants have exited."""

        if self._handle is None:
            raise PipelineError(ErrorCode.NATIVE_CONSOLE_FAILURE, "Job Object was released")
        terminate = self._kernel32.TerminateJobObject
        terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
        terminate.restype = wintypes.BOOL
        if not terminate(wintypes.HANDLE(self._handle), 1):
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console Job Object cannot be terminated",
            )
        deadline = __import__("time").monotonic() + timeout_seconds
        while True:
            try:
                process.wait(timeout=max(0.01, deadline - __import__("time").monotonic()))
            except subprocess.TimeoutExpired as error:
                raise PipelineError(
                    ErrorCode.NATIVE_CONSOLE_FAILURE,
                    "Core Console primary process survived Job termination",
                ) from error
            if self._active_processes() == 0:
                return
            if __import__("time").monotonic() >= deadline:
                raise PipelineError(
                    ErrorCode.NATIVE_CONSOLE_FAILURE,
                    "Core Console descendant survived Job termination",
                )
            __import__("time").sleep(0.01)

    def close(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        if not self._kernel32.CloseHandle(wintypes.HANDLE(handle)):
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console Job Object cannot be released",
            )


class _WindowsConsoleProcess:
    """A CreateProcessW result whose thread was resumed only after Job assignment."""

    def __init__(
        self,
        kernel32: Any,
        *,
        process_handle: int,
        stdout: Any,
        stderr: Any,
    ) -> None:
        self._kernel32 = kernel32
        self._process_handle = process_handle
        self.stdout = stdout
        self.stderr = stderr
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        wait = self._kernel32.WaitForSingleObject
        wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait.restype = wintypes.DWORD
        milliseconds = (
            _INFINITE
            if timeout is None
            else max(0, min(_INFINITE - 1, int(timeout * 1000)))
        )
        result = int(wait(wintypes.HANDLE(self._process_handle), milliseconds))
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(["Core Console"], timeout)
        if result != _WAIT_OBJECT_0:
            raise OSError("Core Console wait failed")
        exit_code = wintypes.DWORD()
        get_exit_code = self._kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code.restype = wintypes.BOOL
        if not get_exit_code(wintypes.HANDLE(self._process_handle), ctypes.byref(exit_code)):
            raise OSError("Core Console exit code unavailable")
        self.returncode = int(exit_code.value)
        return self.returncode

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        wait = self._kernel32.WaitForSingleObject
        wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait.restype = wintypes.DWORD
        result = int(wait(wintypes.HANDLE(self._process_handle), 0))
        if result == _WAIT_TIMEOUT:
            return None
        if result != _WAIT_OBJECT_0:
            raise OSError("Core Console poll failed")
        return self.wait(timeout=0)

    def close(self) -> None:
        if self._process_handle:
            handle = self._process_handle
            self._process_handle = 0
            if not self._kernel32.CloseHandle(wintypes.HANDLE(handle)):
                raise OSError("Core Console process handle close failed")


def _close_native_handle(kernel32: Any, handle: int) -> None:
    if handle and handle != _INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def _create_restricted_environment_block(environment: Mapping[str, str]) -> Any:
    """Encode an exact, sorted Unicode environment without ambient inheritance."""

    entries: list[str] = []
    for key, value in sorted(environment.items(), key=lambda item: item[0].casefold()):
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or "=" in key
            or "\x00" in key
            or "\x00" in value
        ):
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console environment is invalid",
            )
        entries.append(f"{key}={value}")
    return ctypes.create_unicode_buffer("\x00".join(entries) + "\x00\x00")


def _launch_windows_contained_process(
    *,
    job: _WindowsKillJob,
    application: Path,
    command: list[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> _WindowsConsoleProcess:
    """Create suspended with an explicit handle list, assign, then resume.

    This is intentionally the only production launcher.  There is no Popen
    fallback: a failure before containment terminates the still-suspended
    primary process and fails the native operation.
    """

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as error:
        raise PipelineError(
            ErrorCode.NATIVE_CONSOLE_FAILURE,
            "Core Console process API is unavailable",
        ) from error
    security = _SecurityAttributes(
        nLength=ctypes.sizeof(_SecurityAttributes),
        lpSecurityDescriptor=None,
        bInheritHandle=True,
    )
    create_pipe = kernel32.CreatePipe
    create_pipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
    ]
    create_pipe.restype = wintypes.BOOL
    set_handle_information = kernel32.SetHandleInformation
    set_handle_information.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    set_handle_information.restype = wintypes.BOOL
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    stdout_read = stdout_write = stderr_read = stderr_write = stdin_handle = 0
    process_information = _ProcessInformation()
    created = False
    assigned = False
    attribute_buffer: Any = None
    attribute_initialized = False
    try:
        stdout_read_value = wintypes.HANDLE()
        stdout_write_value = wintypes.HANDLE()
        stderr_read_value = wintypes.HANDLE()
        stderr_write_value = wintypes.HANDLE()
        if not create_pipe(
            ctypes.byref(stdout_read_value),
            ctypes.byref(stdout_write_value),
            ctypes.byref(security),
            0,
        ) or not create_pipe(
            ctypes.byref(stderr_read_value),
            ctypes.byref(stderr_write_value),
            ctypes.byref(security),
            0,
        ):
            raise OSError("Core Console pipe creation failed")
        stdout_read = int(stdout_read_value.value or 0)
        stdout_write = int(stdout_write_value.value or 0)
        stderr_read = int(stderr_read_value.value or 0)
        stderr_write = int(stderr_write_value.value or 0)
        if not all((stdout_read, stdout_write, stderr_read, stderr_write)):
            raise OSError("Core Console pipe handle is invalid")
        if not set_handle_information(
            wintypes.HANDLE(stdout_read), _HANDLE_FLAG_INHERIT, 0
        ) or not set_handle_information(
            wintypes.HANDLE(stderr_read), _HANDLE_FLAG_INHERIT, 0
        ):
            raise OSError("Core Console pipe inheritance cannot be restricted")
        stdin_handle = int(
            create_file(
                "NUL",
                _GENERIC_READ,
                _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                ctypes.byref(security),
                _OPEN_EXISTING,
                _FILE_ATTRIBUTE_NORMAL,
                None,
            )
            or 0
        )
        if not stdin_handle or stdin_handle == _INVALID_HANDLE_VALUE:
            raise OSError("Core Console stdin cannot be created")
        if not set_handle_information(
            wintypes.HANDLE(stdin_handle),
            _HANDLE_FLAG_INHERIT,
            _HANDLE_FLAG_INHERIT,
        ):
            raise OSError("Core Console stdin inheritance cannot be set")

        initialize_attributes = kernel32.InitializeProcThreadAttributeList
        initialize_attributes.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        initialize_attributes.restype = wintypes.BOOL
        update_attribute = kernel32.UpdateProcThreadAttribute
        update_attribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        update_attribute.restype = wintypes.BOOL
        delete_attributes = kernel32.DeleteProcThreadAttributeList
        delete_attributes.argtypes = [ctypes.c_void_p]
        delete_attributes.restype = None
        required_size = ctypes.c_size_t()
        initialize_attributes(None, 1, 0, ctypes.byref(required_size))
        if not required_size.value:
            raise OSError("Core Console attribute list size is unavailable")
        attribute_buffer = ctypes.create_string_buffer(required_size.value)
        attribute_pointer = ctypes.cast(attribute_buffer, ctypes.c_void_p)
        if not initialize_attributes(
            attribute_pointer,
            1,
            0,
            ctypes.byref(required_size),
        ):
            raise OSError("Core Console attribute list cannot be initialized")
        attribute_initialized = True
        inherited_handles = (wintypes.HANDLE * 3)(
            wintypes.HANDLE(stdin_handle),
            wintypes.HANDLE(stdout_write),
            wintypes.HANDLE(stderr_write),
        )
        if not update_attribute(
            attribute_pointer,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(inherited_handles, ctypes.c_void_p),
            ctypes.sizeof(inherited_handles),
            None,
            None,
        ):
            raise OSError("Core Console inherited handle list cannot be set")
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = wintypes.HANDLE(stdin_handle)
        startup.StartupInfo.hStdOutput = wintypes.HANDLE(stdout_write)
        startup.StartupInfo.hStdError = wintypes.HANDLE(stderr_write)
        startup.lpAttributeList = attribute_pointer
        create_process = kernel32.CreateProcessW
        create_process.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfo),
            ctypes.POINTER(_ProcessInformation),
        ]
        create_process.restype = wintypes.BOOL
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        environment_block = _create_restricted_environment_block(environment)
        if not create_process(
            os.fspath(application),
            command_line,
            None,
            None,
            True,
            _CREATE_SUSPENDED
            | _CREATE_NO_WINDOW
            | _CREATE_UNICODE_ENVIRONMENT
            | _EXTENDED_STARTUPINFO_PRESENT,
            ctypes.cast(environment_block, ctypes.c_void_p),
            os.fspath(cwd),
            ctypes.byref(startup.StartupInfo),
            ctypes.byref(process_information),
        ):
            raise OSError("Core Console CreateProcessW failed")
        created = True
        process_handle = int(process_information.hProcess or 0)
        thread_handle = int(process_information.hThread or 0)
        if not process_handle or not thread_handle:
            raise OSError("Core Console process handles are unavailable")
        try:
            job.assign(process_handle)
            assigned = True
        except BaseException:
            # Assignment failed before containment; terminate the never-
            # resumed primary process solely to avoid leaving a suspended
            # process behind.  This is not a process-kill success fallback.
            terminate_process = kernel32.TerminateProcess
            terminate_process.argtypes = [wintypes.HANDLE, wintypes.UINT]
            terminate_process.restype = wintypes.BOOL
            terminated = bool(terminate_process(wintypes.HANDLE(process_handle), 1))
            wait = kernel32.WaitForSingleObject
            wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            wait.restype = wintypes.DWORD
            waited = int(wait(wintypes.HANDLE(process_handle), 5000))
            if not terminated or waited != _WAIT_OBJECT_0:
                raise PipelineError(
                    ErrorCode.NATIVE_CONSOLE_FAILURE,
                    "unassigned suspended Core Console cannot be terminated",
                )
            raise
        resume = kernel32.ResumeThread
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = wintypes.DWORD
        if int(resume(wintypes.HANDLE(thread_handle))) == 0xFFFFFFFF:
            process = _WindowsConsoleProcess(
                kernel32,
                process_handle=process_handle,
                stdout=None,
                stderr=None,
            )
            try:
                job.terminate_and_wait(process)
            finally:
                process.close()
                process_information.hProcess = wintypes.HANDLE()
            raise OSError("Core Console primary thread cannot resume")
        _close_native_handle(kernel32, thread_handle)
        process_information.hThread = wintypes.HANDLE()
        import msvcrt

        stdout_stream = os.fdopen(
            msvcrt.open_osfhandle(stdout_read, os.O_RDONLY | os.O_BINARY),
            "rb",
            buffering=0,
        )
        stderr_stream = os.fdopen(
            msvcrt.open_osfhandle(stderr_read, os.O_RDONLY | os.O_BINARY),
            "rb",
            buffering=0,
        )
        stdout_read = stderr_read = 0
        return _WindowsConsoleProcess(
            kernel32,
            process_handle=process_handle,
            stdout=stdout_stream,
            stderr=stderr_stream,
        )
    except BaseException as error:
        if created and assigned and process_information.hProcess:
            contained = _WindowsConsoleProcess(
                kernel32,
                process_handle=int(process_information.hProcess),
                stdout=None,
                stderr=None,
            )
            try:
                job.terminate_and_wait(contained)
            except BaseException:
                # The original launch fault already fails closed. Closing the
                # Job Object in the caller remains kill-on-close defense in
                # depth if a mocked/failed query cannot prove emptiness here.
                pass
            finally:
                try:
                    contained.close()
                except OSError:
                    pass
                process_information.hProcess = wintypes.HANDLE()
        if created and process_information.hThread:
            _close_native_handle(kernel32, int(process_information.hThread))
        if created and process_information.hProcess:
            _close_native_handle(kernel32, int(process_information.hProcess))
        if isinstance(error, PipelineError):
            raise
        raise PipelineError(
            ErrorCode.NATIVE_CONSOLE_FAILURE,
            "Core Console cannot launch in required containment",
        ) from error
    finally:
        if attribute_initialized:
            kernel32.DeleteProcThreadAttributeList(
                ctypes.cast(attribute_buffer, ctypes.c_void_p)
            )
        _close_native_handle(kernel32, stdout_write)
        _close_native_handle(kernel32, stderr_write)
        _close_native_handle(kernel32, stdin_handle)
        _close_native_handle(kernel32, stdout_read)
        _close_native_handle(kernel32, stderr_read)


def _bounded_drain(stream: Any, bucket: bytearray, overflow: list[bool]) -> None:
    """Drain a process stream without retaining/logging more than 64 KiB."""

    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            remaining = _MAX_CONSOLE_STREAM_BYTES - len(bucket)
            if len(chunk) > remaining:
                if remaining > 0:
                    bucket.extend(chunk[:remaining])
                overflow[0] = True
                # Keep draining to avoid a child blocked on its pipe, while
                # intentionally discarding every byte after the fixed bound.
            elif not overflow[0]:
                bucket.extend(chunk)
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _minimal_environment(
    *,
    workspace: PrivateWorkspace,
    manifest_path: Path,
    result_path: Path,
    run_id: str,
) -> dict[str, str]:
    """Create the small inherited environment allowed to an external console."""

    environment: dict[str, str] = {}
    for key in ("SystemRoot", "WINDIR", "ComSpec", "SystemDrive"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment.update(
        {
            "TEMP": os.fspath(workspace.path),
            "TMP": os.fspath(workspace.path),
            "LIANG_PINGFA_NATIVE_MANIFEST": os.fspath(manifest_path),
            "LIANG_PINGFA_NATIVE_RESULT": os.fspath(result_path),
            "LIANG_PINGFA_NATIVE_RUN_ID": run_id,
            "LIANG_PINGFA_NATIVE_PRIVATE_ROOT": os.fspath(workspace.path),
        }
    )
    return environment


def _terminate(process: _WindowsConsoleProcess, job: _WindowsKillJob) -> None:
    """Terminate and wait for the Job Object; never fall back to process kill."""

    job.terminate_and_wait(process)


@dataclass(frozen=True)
class CoreConsoleOutcome:
    """Validated private result from one fixed Core Console launch."""

    run_id: str
    artifact: dict[str, Any]
    result_path: Path


def run_core_console(
    *,
    workspace: PrivateWorkspace,
    private_dwg: Path,
    manifest_path: Path,
    config: Mapping[str, Any],
    mode: Literal["write", "readback"],
    component_leases: NativeInstallationLeases | None = None,
) -> CoreConsoleOutcome:
    """Run one bounded external console process against one private DWG only."""

    checked = require_active_native_contract("config", config)
    if os.name != "nt":
        raise PipelineError(ErrorCode.WINDOWS_PLATFORM_REQUIRED, "Core Console is Windows-only")
    owned_component_leases = component_leases is None
    leases = component_leases or acquire_native_installation_leases(checked)
    installations = leases.paths
    try:
        leases.require_bindings()
        private_dwg.relative_to(workspace.path)
        manifest_path.relative_to(workspace.path)
        workspace.require_tracked_file_security(private_dwg)
        workspace.require_tracked_file_security(manifest_path)
    except (ValueError, PipelineError) as error:
        if owned_component_leases:
            leases.close()
        if isinstance(error, PipelineError):
            raise
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_INVALID, "console path escaped private workspace") from error
    if private_dwg.suffix.casefold() != ".dwg":
        if owned_component_leases:
            leases.close()
        raise PipelineError(ErrorCode.NATIVE_MANIFEST_INVALID, "private console input is unavailable")
    key = "write_plugin" if mode == "write" else "readback_plugin"
    plugin_config = checked["plugins"]["write" if mode == "write" else "readback"]
    script_path = workspace.path / (
        "native-write.scr" if mode == "write" else "native-readback.scr"
    )
    result_path = workspace.path / (
        "native-console-result.json"
        if mode == "write"
        else "native-console-export.json"
    )
    try:
        if result_path.exists():
            raise PipelineError(ErrorCode.NATIVE_CONSOLE_FAILURE, "private console result exists")
        write_fixed_script(workspace, script_path, installations[key], plugin_config["command"])
        workspace.require_tracked_file_security(script_path)
    except BaseException:
        if owned_component_leases:
            leases.close()
        raise
    run_id = "native-run-" + secrets.token_hex(16)
    timeout = int(
        checked["timeouts"][
            "write_console_seconds" if mode == "write" else "readback_console_seconds"
        ]
    )
    command = [
        os.fspath(installations["core_console"]),
        "/i",
        os.fspath(private_dwg),
        "/s",
        os.fspath(script_path),
    ]
    job: _WindowsKillJob | None = None
    process: _WindowsConsoleProcess | None = None
    stdout = bytearray()
    stderr = bytearray()
    stdout_overflow = [False]
    stderr_overflow = [False]
    threads: list[threading.Thread] = []
    failure: PipelineError | None = None
    try:
        # The Job Object exists before CreateProcessW.  The launcher assigns
        # the suspended process before its primary thread is resumed.
        job = _WindowsKillJob()
        leases.require_bindings()
        process = _launch_windows_contained_process(
            job=job,
            application=installations["core_console"],
            command=command,
            cwd=workspace.path,
            environment=_minimal_environment(
                workspace=workspace,
                manifest_path=manifest_path,
                result_path=result_path,
                run_id=run_id,
            ),
        )
        threads = [
            threading.Thread(
                target=_bounded_drain,
                args=(process.stdout, stdout, stdout_overflow),
                daemon=True,
            ),
            threading.Thread(
                target=_bounded_drain,
                args=(process.stderr, stderr, stderr_overflow),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _terminate(process, job)
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_TIMEOUT,
                "Core Console timed out",
            ) from error
        for thread in threads:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in threads):
            _terminate(process, job)
            for thread in threads:
                thread.join(timeout=2)
            raise PipelineError(
                ErrorCode.NATIVE_CONSOLE_FAILURE,
                "Core Console streams did not close",
            )
        # Even after the primary process exits, no descendant is permitted to
        # survive to retain a pipe, sidecar, or workspace handle.
        _terminate(process, job)
        leases.require_bindings()
        if (
            process.returncode != 0
            or stdout_overflow[0]
            or stderr_overflow[0]
        ):
            raise PipelineError(ErrorCode.NATIVE_CONSOLE_FAILURE, "Core Console result rejected")
        # The result was written by an external process. Do not merely bind
        # its pathname: open it no-follow with a read lease and prove its
        # owner/DACL before it is registered, parsed, or allowed to remain in
        # the private workspace.
        workspace.track_external_file(result_path)
        artifact = workspace.read_tracked_file_bytes(
            result_path,
            maximum_bytes=(
                _MAX_CONSOLE_RESULT_BYTES
                if mode == "write"
                else _MAX_CONSOLE_EXPORT_BYTES
            ),
            consume=lambda raw_result: require_active_native_contract(
                "console_result" if mode == "write" else "console_export",
                load_native_json_value(
                    "console_result" if mode == "write" else "console_export",
                    raw_result.decode("utf-8", errors="strict"),
                ),
            ),
        )
    except PipelineError as error:
        failure = error
    except Exception as error:
        failure = PipelineError(
            ErrorCode.NATIVE_CONSOLE_RESULT_INVALID,
            "console JSON invalid",
        )
        failure.__cause__ = error
    finally:
        if process is not None and job is not None and process.poll() is None:
            try:
                _terminate(process, job)
            except PipelineError as termination_error:
                if failure is None:
                    failure = termination_error
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=2)
        if process is not None:
            try:
                process.close()
            except OSError as error:
                if failure is None:
                    failure = PipelineError(
                        ErrorCode.NATIVE_CONSOLE_FAILURE,
                        "Core Console process handle cleanup failed",
                    )
                    failure.__cause__ = error
        if job is not None:
            try:
                job.close()
            except PipelineError as error:
                if failure is None:
                    failure = error
        if owned_component_leases:
            try:
                leases.close()
            except PipelineError as error:
                if failure is None:
                    failure = error
    if failure is not None:
        if failure.__cause__ is not None:
            raise failure from failure.__cause__
        raise failure
    return CoreConsoleOutcome(run_id=run_id, artifact=artifact, result_path=result_path)
