"""Exercise Ubuntu's pure-test selection from a Windows development runner."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


REPOSITORY_ROOT = Path(__file__).parent.parent


class LinuxCiSetupTests(unittest.TestCase):
    """Ensure test-only backend patches cover every non-Windows pure path."""

    @unittest.skipUnless(
        os.name == "nt",
        "Ubuntu executes the portable selection directly",
    )
    def test_ubuntu_selection_passes_with_non_windows_platform_paths(self) -> None:
        """Run the configured discovery selection after forcing ``os.name``."""

        runner = textwrap.dedent(
            f"""
            import os
            import pathlib
            import subprocess
            import sys
            import tempfile
            import unittest
            from contextlib import ExitStack
            from unittest import mock

            # Keep the real process-level os module intact: changing its name
            # breaks pathlib and ctypes on Windows. Instead, discovery first
            # imports the configured selection normally, then each test and
            # ownership module receives a test-process-only os proxy whose
            # ``name`` selects its non-Windows branch.
            del pathlib, subprocess, tempfile
            os.chdir({str(REPOSITORY_ROOT)!r})

            class NonWindowsOs:
                def __init__(self, wrapped):
                    self._wrapped = wrapped
                    self.name = "posix"

                def __getattr__(self, name):
                    return getattr(self._wrapped, name)

            native_markers = (
                "ApplyAndVerifyTests",
                "WindowsHandleIntegrationTests",
                "test_input_junctions_are_rejected_without_recursive_traversal",
                "test_prelaunch_input_and_output_replacements_are_denied_or_detected",
                "test_windows_input_directory_lease_denies_or_detects_child_creation",
                "test_output_junction_sidecar_is_quarantined_without_external_write",
                "test_local_regression_preserves_output_replaced_after_apply",
                "test_staged_dwg_aba_attempts_before_during_and_after_roundtrip_fail_closed",
                "test_relative_cli_artifact_and_output_paths_are_lexically_anchored",
                "test_relative_cli_output_rejects_a_junctioned_current_directory",
                "test_cli_audit_retains_source_through_artifact_publication",
                "SourceBindingNativeTests",
                "GeneratedOdaIntegrationTests",
            )

            def portable_selection(suite):
                selected = unittest.TestSuite()
                for item in suite:
                    if isinstance(item, unittest.TestSuite):
                        filtered = portable_selection(item)
                        if filtered.countTestCases():
                            selected.addTest(filtered)
                    elif (
                        "test_linux_ci_setup" not in item.id()
                        and not any(marker in item.id() for marker in native_markers)
                    ):
                        selected.addTest(item)
                return selected

            discovered = unittest.defaultTestLoader.discover(
                "tests",
                pattern="test_*.py",
            )
            proxy = NonWindowsOs(os)
            patched_modules = (
                "liang_pingfa_review.atomic_output",
                "liang_pingfa_review.canonical",
                "liang_pingfa_review.oda",
                "liang_pingfa_review.ownership",
                "liang_pingfa_review.snapshots",
                "liang_pingfa_review.temporary",
                "liang_pingfa_review.apply",
                "liang_pingfa_review.verify",
                "test_apply_verify",
                "test_audit_plan",
                "test_canonical_contracts",
                "test_handle_ownership",
                "test_oda_cli",
                "tests.support.owned_files",
            )
            with ExitStack() as patches:
                for name in patched_modules:
                    module = sys.modules.get(name)
                    if module is not None and hasattr(module, "os"):
                        patches.enter_context(mock.patch.object(module, "os", proxy))
                result = unittest.TextTestRunner(verbosity=1).run(
                    portable_selection(discovered)
                )
            sys.exit(0 if result.wasSuccessful() else 1)
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", runner],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if completed.returncode != 0:
            self.fail(
                "Linux-simulated Ubuntu test selection failed:\n"
                + completed.stdout
                + completed.stderr
            )


if __name__ == "__main__":
    unittest.main()
