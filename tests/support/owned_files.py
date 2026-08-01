"""Test-only local ownership doubles for platform-neutral contract tests."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from hashlib import sha256
import io
import os
from pathlib import Path
import stat as stat_module
import sys
from typing import BinaryIO, TextIO
import unittest
from unittest import mock

from liang_pingfa_review.ownership import (
    DestinationExistsError,
    FileIdentity,
    OwnedPathBinding,
    OwnershipCleanupError,
    OwnershipLostError,
)


def _identity(status: os.stat_result) -> FileIdentity:
    return FileIdentity(
        namespace="test-stat",
        first=int(status.st_dev),
        second=int(status.st_ino),
        creation_time_100ns=0,
    )


def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
    stream.seek(0)
    digest = sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    stream.seek(0)
    return digest.hexdigest(), size


def _flush(stream: BinaryIO) -> None:
    stream.flush()
    os.fsync(stream.fileno())
    stream.seek(0)


class TestOwnedPath:
    """A test-local ownership double, never importable from installed code."""

    def __init__(
        self,
        path: Path,
        stream: BinaryIO | None,
        *,
        is_directory: bool,
        backend: "TestOwnershipBackend | None" = None,
    ) -> None:
        self.path = path
        self._stream = stream
        self._is_directory = is_directory
        self._backend = backend
        self._delete_requested = False

    def _status(self) -> os.stat_result:
        return (
            os.fstat(self._stream.fileno())
            if self._stream is not None
            else self.path.stat()
        )

    def copy_from(self, source: Path) -> None:
        with source.open("rb", buffering=0) as input_file:
            self.write_chunks(iter(lambda: input_file.read(1024 * 1024), b""))

    def read_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if self._stream is None or self._is_directory or chunk_size <= 0:
            raise OwnershipLostError("not a readable test file")
        self._stream.seek(0)
        try:
            while chunk := self._stream.read(chunk_size):
                yield chunk
        finally:
            self._stream.seek(0)

    def write_chunks(self, chunks: Iterable[bytes]) -> None:
        if self._stream is None or self._is_directory:
            raise OwnershipCleanupError("not a writable test file")
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise OwnershipCleanupError("invalid test chunk")
            written = self._stream.write(chunk)
            if written is not None and written != len(chunk):
                raise OSError("short test write")
        _flush(self._stream)

    def write_bytes(self, payload: bytes) -> None:
        self.write_chunks((payload,))

    def write_text(self, writer: Callable[[TextIO], None]) -> None:
        if self._stream is None or self._is_directory:
            raise OwnershipCleanupError("not a writable test file")
        text = io.TextIOWrapper(
            self._stream,
            encoding="utf-8",
            newline="",
            write_through=True,
        )
        try:
            writer(text)
            text.flush()
        finally:
            try:
                text.detach()
            except ValueError:
                pass
        _flush(self._stream)

    def read_prefix(self, length: int) -> bytes:
        if self._stream is None or self._is_directory:
            raise OwnershipLostError("not a regular test file")
        self._stream.seek(0)
        try:
            return self._stream.read(length)
        finally:
            self._stream.seek(0)

    def capture_binding(self) -> OwnedPathBinding:
        status_before = self._status()
        if self._is_directory:
            if not stat_module.S_ISDIR(status_before.st_mode):
                raise OwnershipLostError("test object type changed")
            return OwnedPathBinding(
                path=self.path,
                identity=_identity(status_before),
                byte_size=None,
                sha256=None,
                is_directory=True,
            )
        if self._stream is None or not stat_module.S_ISREG(status_before.st_mode):
            raise OwnershipLostError("test object type changed")
        digest, size = _hash_stream(self._stream)
        status_after = self._status()
        if (
            _identity(status_before) != _identity(status_after)
            or status_after.st_size != size
        ):
            raise OwnershipLostError("test object changed while binding")
        return OwnedPathBinding(
            path=self.path,
            identity=_identity(status_after),
            byte_size=size,
            sha256=digest,
            is_directory=False,
        )

    def final_path(self) -> Path:
        """Mirror the no-follow handle contract for the POSIX-only test double."""

        return Path(os.path.abspath(os.fspath(self.path)))

    def rename_no_replace(self, destination: Path) -> None:
        if self._is_directory:
            raise OwnershipCleanupError("test directories cannot publish")
        if sys.platform == "win32":
            # The test double models POSIX-style rename/unlink behavior on a
            # Windows host during the Linux-simulation subprocess. Windows
            # cannot rename or unlink the file while this Python descriptor is
            # live, so release and reacquire only inside this test-only path.
            if os.path.lexists(destination):
                raise DestinationExistsError("destination exists")
            if self._stream is not None:
                stream = self._stream
                self._stream = None
                stream.close()
            try:
                os.rename(self.path, destination)
            except FileExistsError as error:
                raise DestinationExistsError("destination exists") from error
            self.path = destination
            self._stream = destination.open("r+b", buffering=0)
            return
        try:
            os.link(self.path, destination)
        except FileExistsError as error:
            raise DestinationExistsError("destination exists") from error
        self.path.unlink()
        self.path = destination

    def request_delete(self) -> None:
        if self._delete_requested:
            return
        if sys.platform == "win32" and self._stream is not None:
            stream = self._stream
            self._stream = None
            stream.close()
        if self._is_directory:
            self.path.rmdir()
        else:
            self.path.unlink()
        self._delete_requested = True

    def close(self) -> None:
        if self._stream is not None:
            stream = self._stream
            self._stream = None
            stream.close()


class TestOwnershipBackend:
    """Minimal local backend injected only by tests that run off Windows."""

    def __init__(self) -> None:
        self.private_ancestry_checks: list[Path] = []
        self.secured_private_directories: list[Path] = []
        self.fail_private_ancestry = False
        self.fail_private_dacl = False

    def create_new_file(self, path: Path) -> TestOwnedPath:
        return TestOwnedPath(
            path,
            path.open("x+b", buffering=0),
            is_directory=False,
            backend=self,
        )

    def open_existing_file(self, path: Path, *, for_delete: bool) -> TestOwnedPath:
        del for_delete
        if path.is_symlink():
            raise OwnershipLostError("test link is not owned")
        return TestOwnedPath(
            path,
            path.open("rb", buffering=0),
            is_directory=False,
            backend=self,
        )

    def open_existing_file_read_lease(self, path: Path) -> TestOwnedPath:
        return self.open_existing_file(path, for_delete=False)

    def open_existing_directory(
        self,
        path: Path,
        *,
        for_delete: bool,
    ) -> TestOwnedPath:
        del for_delete
        if path.is_symlink():
            raise OwnershipLostError("test link is not owned")
        return TestOwnedPath(path, None, is_directory=True, backend=self)

    def open_existing_directory_read_lease(self, path: Path) -> TestOwnedPath:
        """Mirror the production ODA input-directory lease shape in tests."""

        return self.open_existing_directory(path, for_delete=False)

    def open_output_parent_directory(self, path: Path) -> TestOwnedPath:
        return self.open_existing_directory(path, for_delete=False)

    def path_exists(self, path: Path) -> bool:
        return os.path.lexists(path)

    def path_matches_binding(self, path: Path, binding: OwnedPathBinding) -> bool:
        try:
            opened = (
                self.open_existing_directory(path, for_delete=False)
                if binding.is_directory
                else self.open_existing_file(path, for_delete=False)
            )
        except (OSError, OwnershipLostError):
            return False
        try:
            return opened.capture_binding().same_identity_and_content(binding)
        except OwnershipLostError:
            return False
        finally:
            opened.close()

    def validate_private_staging_ancestry(self, path: Path) -> None:
        """Model the explicitly injected portable NTFS qualification."""

        self.private_ancestry_checks.append(path)
        if self.fail_private_ancestry:
            raise OwnershipCleanupError("synthetic non-NTFS workspace")

    def secure_private_staging_directory(self, path: Path) -> None:
        """Model verified current-user/SYSTEM DACL application in pure tests."""

        self.secured_private_directories.append(path)
        if self.fail_private_dacl:
            raise OwnershipCleanupError("synthetic private DACL failure")


def install_non_windows_test_ownership(test_case: unittest.TestCase) -> None:
    """Patch private ownership factories only inside a non-Windows test case.

    Production code remains Windows-only.  Ubuntu's pure synthetic suite uses
    these test-local doubles for every internal boundary that otherwise
    constructs the native ownership backend, including ODA and DXF snapshots.
    """

    if os.name == "nt":
        return
    backend = TestOwnershipBackend()
    for target in (
        "liang_pingfa_review.temporary.platform_backend",
        "liang_pingfa_review.canonical.platform_backend",
        "liang_pingfa_review.atomic_output.platform_backend",
        "liang_pingfa_review.oda._converter_backend",
        "liang_pingfa_review.snapshots._snapshot_backend",
    ):
        patcher = mock.patch(target, return_value=backend)
        patcher.start()
        test_case.addCleanup(patcher.stop)
