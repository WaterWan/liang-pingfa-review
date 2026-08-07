"""Regression coverage for deterministic native-CAD .NET test launches."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tests.support.dotnet_subprocess import DotnetProcessTimeout, run_dotnet


class DotnetSubprocessTests(unittest.TestCase):
    """Keep locking, cleanup, and the narrow CS2012 recovery behavior exact."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.state = root / "state"
        self.lock = root / "locks" / "dotnet.lock"
        self.script = root / "fake_dotnet.py"
        self.script.write_text(
            """
import os
from pathlib import Path
import sys
import time

state = Path(os.environ["FAKE_DOTNET_STATE"])
state.mkdir(parents=True, exist_ok=True)
arguments = sys.argv[1:]
(state / "calls.txt").open("a", encoding="utf-8").write(" ".join(arguments) + "\\n")
mode = os.environ.get("FAKE_DOTNET_MODE", "success")
if arguments[:2] == ["build-server", "shutdown"]:
    print("shutdown")
    raise SystemExit(0)
if mode == "cs2012-once" and not (state / "failed-once").exists():
    (state / "failed-once").write_text("1", encoding="utf-8")
    print("error CS2012: Cannot open 'Protocol.dll' for writing -- The process cannot access the file 'Protocol.dll' because it is being used by another process.")
    raise SystemExit(1)
if mode == "ordinary-failure":
    print("error CS1002: ; expected")
    raise SystemExit(1)
if mode == "sleep":
    (state / "entered.txt").write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(5)
if mode == "lock":
    (state / "events.txt").open("a", encoding="utf-8").write("start\\n")
    time.sleep(0.2)
    (state / "events.txt").open("a", encoding="utf-8").write("end\\n")
print("success")
""".strip()
            + "\n",
            encoding="utf-8",
        )
        self.executable = (sys.executable, os.fspath(self.script))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, mode: str, *, timeout: float = 5) -> subprocess.CompletedProcess[bytes]:
        return run_dotnet(
            "build",
            "fixture.csproj",
            cwd=self.temporary.name,
            env={"FAKE_DOTNET_MODE": mode, "FAKE_DOTNET_STATE": os.fspath(self.state)},
            executable=self.executable,
            lock_path=self.lock,
            timeout=timeout,
        )  # type: ignore[return-value]

    def test_retries_only_one_cs2012_file_in_use_failure_after_shutdown(self) -> None:
        result = self._run("cs2012-once")
        self.assertEqual(0, result.returncode)
        calls = (self.state / "calls.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(3, len(calls))
        self.assertEqual("build-server shutdown", calls[1])

    def test_does_not_retry_an_ordinary_compiler_failure(self) -> None:
        result = self._run("ordinary-failure")
        self.assertNotEqual(0, result.returncode)
        calls = (self.state / "calls.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, len(calls))

    def test_lock_serializes_concurrent_test_processes(self) -> None:
        failures: list[BaseException] = []

        def launch() -> None:
            try:
                self._run("lock")
            except BaseException as error:  # pragma: no cover - asserted below
                failures.append(error)

        first = threading.Thread(target=launch)
        second = threading.Thread(target=launch)
        first.start()
        time.sleep(0.05)
        second.start()
        first.join()
        second.join()
        self.assertEqual([], failures)
        self.assertEqual(
            ["start", "end", "start", "end"],
            (self.state / "events.txt").read_text(encoding="utf-8").splitlines(),
        )

    def test_timeout_terminates_the_child_and_releases_the_lock(self) -> None:
        with self.assertRaises(DotnetProcessTimeout):
            self._run("sleep", timeout=0.1)
        process_id = int((self.state / "entered.txt").read_text(encoding="utf-8"))
        time.sleep(0.1)
        if os.name == "nt":
            probe = subprocess.run(
                ["tasklist", "/FI", f"PID eq {process_id}", "/NH"],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertNotIn(str(process_id), probe.stdout)
        self.assertFalse(self.lock.exists())
        self.assertEqual(0, self._run("success").returncode)
