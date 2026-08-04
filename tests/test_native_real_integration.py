"""Opt-in placeholder for an operator-owned external native conformance run."""

from __future__ import annotations

import os
import unittest


_REQUIRED = (
    "LIANG_PINGFA_RUN_NATIVE_INTEGRATION",
    "LIANG_PINGFA_NATIVE_TEST_CONFIG",
    "LIANG_PINGFA_NATIVE_TEST_SESSION",
    "LIANG_PINGFA_NATIVE_TEST_SOURCE",
)


@unittest.skipUnless(
    all(os.environ.get(name) for name in _REQUIRED)
    and os.environ.get("LIANG_PINGFA_RUN_NATIVE_INTEGRATION") == "1",
    "real native integration requires explicit operator-owned environment variables",
)
class NativeRealIntegrationTests(unittest.TestCase):
    """Public CI intentionally does not claim a proprietary host integration."""

    def test_operator_must_run_external_conformance_outside_public_ci(self) -> None:
        self.skipTest(
            "The repository ships no proprietary adapter/plugin; run the external "
            "conformance procedure only in an authorized private environment."
        )


if __name__ == "__main__":
    unittest.main()
