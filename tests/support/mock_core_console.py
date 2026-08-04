"""Generated test-only Core Console stand-in; not a CAD host or plugin."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    """Write a caller-provided generated result without interpreting CAD data."""

    result = Path(os.environ["LIANG_PINGFA_NATIVE_RESULT"])
    payload = os.environ.get("LIANG_PINGFA_TEST_CONSOLE_PAYLOAD")
    if payload is None:
        return 2
    result.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
