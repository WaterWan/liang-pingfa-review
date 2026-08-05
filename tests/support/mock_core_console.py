"""Generated test-only Core Console stand-in; not a CAD host or plugin."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def _private_dwg_argument(arguments: list[str]) -> Path:
    """Extract the fixed Core Console `/i` private drawing argument."""

    try:
        index = arguments.index("/i")
        return Path(arguments[index + 1])
    except (ValueError, IndexError) as error:
        raise ValueError("generated Core Console lacks a private DWG argument") from error


def _mutate_private_dwg() -> None:
    """Append a deterministic v2 edit marker for every requested operation."""

    manifest_path = Path(os.environ["LIANG_PINGFA_NATIVE_MANIFEST"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    operations = manifest.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("generated Core Console manifest has no operations")
    private_dwg = _private_dwg_argument(sys.argv[1:])
    before = private_dwg.read_bytes()
    if not before.startswith(b"AC"):
        raise ValueError("generated Core Console private input is not a DWG")
    # JSON's deterministic compact encoding gives translate/delete/marker
    # requests unique, repeatable bytes without pretending to be CAD data.
    operation_bytes = json.dumps(
        operations,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    private_dwg.write_bytes(
        before
        + b"\nLPF-GENERATED-CORE-CONSOLE-V2:"
        + operation_bytes
    )


def main() -> int:
    """Mutate write-mode private bytes and write a caller-provided result."""

    result = Path(os.environ["LIANG_PINGFA_NATIVE_RESULT"])
    payload = os.environ.get("LIANG_PINGFA_TEST_CONSOLE_PAYLOAD")
    if payload is None:
        return 2
    try:
        if result.name == "native-console-result.json":
            _mutate_private_dwg()
        result.write_text(payload, encoding="utf-8")
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
