"""Deterministic .NET subprocesses for tests sharing native-CAD build outputs."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from typing import Iterator, Mapping, Sequence


DEFAULT_TIMEOUT_SECONDS = 240
LOCK_TIMEOUT_SECONDS = 300
OUTPUT_TAIL_BYTES = 8_192
_LOCK_PATH = Path(tempfile.gettempdir()) / "liang-pingfa-review-tests" / "dotnet.lock"
_CS2012_FILE_IN_USE = re.compile(
    r"\bCS2012\b\s*:\s*Cannot\s+open\b.*?\bfor\s+writing\b.*?"
    r"\b(?:being\s+used\s+by\s+another\s+process|"
    r"process\s+cannot\s+access\s+the\s+file)\b",
    re.IGNORECASE | re.DOTALL,
)
_BUILD_VERBS = frozenset({"build", "test", "pack", "publish"})


class DotnetProcessTimeout(TimeoutError):
    """A .NET child exceeded its bounded test timeout and was terminated."""


class DotnetLockTimeout(TimeoutError):
    """Another test process held the native-CAD .NET build lock too long."""


def output_tail(result: subprocess.CompletedProcess[bytes | str]) -> str:
    """Return a bounded, decoded diagnostic tail without changing test output."""

    stdout = result.stdout or b""
    stderr = result.stderr or b""
    if isinstance(stdout, str):
        text = stdout + (stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace"))
    else:
        text = (stdout + (stderr if isinstance(stderr, bytes) else stderr.encode())).decode(
            "utf-8",
            errors="replace",
        )
    return text[-OUTPUT_TAIL_BYTES:]


@contextmanager
def _exclusive_lock(path: Path, *, timeout: float) -> Iterator[None]:
    """Acquire a cross-process lock using exclusive file creation.

    Native CAD tests deliberately share project output roots.  The lock lives
    in the generated system temp directory rather than in the repository so
    it cannot become a tracked artifact.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise DotnetLockTimeout(f"timed out waiting for .NET test lock: {path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _command_prefix(executable: str | Sequence[str]) -> list[str]:
    return [executable] if isinstance(executable, str) else list(executable)


def _serialized_arguments(arguments: Sequence[str]) -> list[str]:
    """Disable nested compiler sharing and constrain MSBuild graph parallelism."""

    serialized = list(arguments)
    if not serialized:
        return serialized
    verb = serialized[0].casefold()
    property_argument = "-p:UseSharedCompilation=false"
    def append_msbuild_argument(value: str) -> None:
        try:
            separator = serialized.index("--")
        except ValueError:
            serialized.append(value)
        else:
            serialized.insert(separator, value)

    if not any(argument.casefold() == property_argument.casefold() for argument in serialized):
        append_msbuild_argument(property_argument)
    if verb == "msbuild":
        if not any(argument.casefold().startswith(("/m", "-m", "--maxcpucount")) for argument in serialized):
            append_msbuild_argument("/m:1")
    elif verb in _BUILD_VERBS:
        if not any(argument.casefold().startswith(("-m", "--maxcpucount")) for argument in serialized):
            append_msbuild_argument("-m:1")
    return serialized


def _test_environment(extra: Mapping[str, str] | None) -> dict[str, str]:
    environment = os.environ.copy()
    if extra:
        environment.update(extra)
    environment.update(
        {
            "DOTNET_CLI_DO_NOT_USE_MSBUILD_SERVER": "1",
            "MSBUILDDISABLENODEREUSE": "1",
            "UseSharedCompilation": "false",
        }
    )
    return environment


def _invoke(
    command: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    environment: Mapping[str, str],
    timeout: float,
    text: bool,
    encoding: str | None,
    errors: str | None,
) -> subprocess.CompletedProcess[bytes | str]:
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding=encoding if text else None,
        errors=errors if text else None,
        creationflags=creationflags,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The dedicated Windows process group gives MSBuild children the same
        # bounded cancellation signal as the dotnet parent.  Compiler/node
        # reuse is disabled separately, so no server can retain output locks.
        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                stdout, stderr = process.communicate(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                stdout, stderr = process.communicate()
        else:
            process.kill()
            stdout, stderr = process.communicate()
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        raise DotnetProcessTimeout(
            f".NET command timed out after {timeout:.1f}s: {' '.join(command)}\n"
            f"{output_tail(completed)}"
        )
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _is_cs2012_file_in_use(result: subprocess.CompletedProcess[bytes | str]) -> bool:
    return result.returncode != 0 and bool(_CS2012_FILE_IN_USE.search(output_tail(result)))


def run_dotnet(
    *arguments: str,
    cwd: str | os.PathLike[str] | None,
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    executable: str | Sequence[str] = "dotnet",
    lock_path: Path | None = None,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes | str]:
    """Run one .NET command while serializing shared native-CAD build outputs.

    Only the precise Windows CS2012 "file in use while writing" diagnostic is
    retried.  All source, restore, test, and unrelated compiler failures are
    returned unchanged to their callers.
    """

    command = _command_prefix(executable) + _serialized_arguments(arguments)
    environment = _test_environment(env)
    with _exclusive_lock(lock_path or _LOCK_PATH, timeout=lock_timeout):
        result = _invoke(
            command,
            cwd=cwd,
            environment=environment,
            timeout=timeout,
            text=text,
            encoding=encoding,
            errors=errors,
        )
        if not _is_cs2012_file_in_use(result):
            return result

        # A single retry is enough to cover the known transient lock without
        # hiding deterministic compiler, restore, or test failures.
        _invoke(
            _command_prefix(executable) + ["build-server", "shutdown"],
            cwd=cwd,
            environment=environment,
            timeout=min(timeout, 30),
            text=text,
            encoding=encoding,
            errors=errors,
        )
        return _invoke(
            command,
            cwd=cwd,
            environment=environment,
            timeout=timeout,
            text=text,
            encoding=encoding,
            errors=errors,
        )


def run_dotnet_command(
    command: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    capture_output: bool = True,
    check: bool = False,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes | str]:
    """Compatibility entry point for tests with an existing command list."""

    if not capture_output or check or not command or command[0].casefold() != "dotnet":
        raise ValueError("test .NET commands must be uncaught, captured dotnet commands")
    return run_dotnet(
        *command[1:],
        cwd=cwd,
        env=env,
        timeout=timeout,
        text=text,
        encoding=encoding,
        errors=errors,
    )
