"""Create original, runtime-only DXF fixtures and a mocked ODA converter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import io
from pathlib import Path
from typing import Any

import ezdxf


def overwrite_existing_default_stream(path: Path, payload: bytes) -> None:
    """Write test-only bytes through an existing generated file."""

    with path.open("r+b", buffering=0) as destination:
        destination.write(payload)
        destination.truncate()


def save_document_to_existing_default_stream(document: Any, path: Path) -> None:
    """Serialize a generated DXF through an already-created test file."""

    serialized = io.StringIO()
    document.write(serialized, fmt="asc")
    overwrite_existing_default_stream(
        path,
        serialized.getvalue().encode("utf-8"),
    )


def create_synthetic_dxf(path: Path, *, variant: str = "actionable") -> None:
    """Create a small left/right representation fixture with no source material."""

    document = ezdxf.new("R2018")
    # ``CREATED_BY_EZDXF`` carries a fresh timestamp for every test fixture.
    # It is neither source content nor an ODA normalization rule, so omit it
    # from generated fixtures rather than widening production volatility.
    document.ezdxf_metadata().discard("CREATED_BY_EZDXF")
    modelspace = document.modelspace()
    document.layers.new("TEMP")
    document.layers.new("textarea")
    document.layers.new("ANNOTATION")
    interference_lines = []

    def panel(origin_x: float) -> None:
        left = origin_x
        right = origin_x + 100
        bottom = 0
        top = 50
        modelspace.add_line((left, bottom), (right, bottom))
        modelspace.add_line((right, bottom), (right, top))
        modelspace.add_line((right, top), (left, top))
        modelspace.add_line((left, top), (left, bottom))
        interference_lines.append(
            modelspace.add_line((left + 20, 25), (left + 80, 25))
        )
        modelspace.add_text(
            "ordinary",
            dxfattribs={
                "layer": "ANNOTATION",
                "height": 3,
                "insert": (left + 35, 35),
            },
        )

    if variant != "ambiguous":
        panel(0)
        panel(200)
    else:
        panel(0)

    candidate = None
    if variant in {
        "actionable",
        "duplicate",
        "candidate-layer-off",
        "candidate-layer-frozen",
        "candidate-layer-viewport-frozen",
        "candidate-layer-unsupported-flag",
        "candidate-invisible",
        "hidden-evidence",
        "candidate-transparent",
        "interference-transparent",
        "candidate-layer-transparent",
        "noncoplanar",
        "coplanar",
    }:
        candidate = modelspace.add_text(
            "overlay",
            dxfattribs={"layer": "TEMP", "height": 5, "insert": (45, 25)},
        )
    if variant == "duplicate":
        modelspace.add_text(
            "overlay",
            dxfattribs={"layer": "TEMP", "height": 5, "insert": (45, 25)},
        )
    if variant == "ambiguous":
        modelspace.add_text(
            "overlay",
            dxfattribs={"layer": "TEMP", "height": 5, "insert": (45, 25)},
        )
    if variant == "candidate-layer-off":
        document.layers.get("TEMP").off()
    if variant == "candidate-layer-frozen":
        document.layers.get("TEMP").freeze()
    if variant == "candidate-layer-viewport-frozen":
        layer = document.layers.get("TEMP")
        layer.dxf.flags = int(layer.dxf.flags) | 2
    if variant == "candidate-layer-unsupported-flag":
        layer = document.layers.get("TEMP")
        layer.dxf.flags = int(layer.dxf.flags) | 8
    if variant == "candidate-invisible":
        assert candidate is not None
        candidate.dxf.invisible = 1
    if variant == "hidden-evidence":
        interference_lines[0].dxf.invisible = 1
    if variant == "candidate-transparent":
        assert candidate is not None
        candidate.transparency = 1.0
    if variant == "interference-transparent":
        interference_lines[0].transparency = 1.0
    if variant == "candidate-layer-transparent":
        document.layers.get("TEMP").transparency = 1.0
    if variant == "noncoplanar":
        assert candidate is not None
        candidate.dxf.insert = (45, 25, 100)
    if variant == "coplanar":
        assert candidate is not None
        candidate.dxf.insert = (45, 25, 100)
        for line in modelspace.query("LINE"):
            line.dxf.start = (
                line.dxf.start.x,
                line.dxf.start.y,
                100,
            )
            line.dxf.end = (
                line.dxf.end.x,
                line.dxf.end.y,
                100,
            )
    if path.exists():
        save_document_to_existing_default_stream(document, path)
    else:
        document.saveas(path)


class FakeOdaConverter:
    """A platform-neutral ODA test double preserving temporary DXF payloads."""

    version = "27.1.0"
    _MARKER = b"DXFSTAGE\n"

    def __init__(self, initial_dxf: Path) -> None:
        self.initial_dxf = initial_dxf
        self.calls: list[tuple[str, ...]] = []
        self.output_directories: list[Path] = []

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        """Create one exact synthetic output in an initially empty root."""

        del register_output
        self.calls.append((input_directory.name, output_directory.name, output_type))
        self.output_directories.append(output_directory)
        source = next(path for path in input_directory.iterdir() if path.is_file())
        destination = output_directory / f"{source.stem}.{output_type.lower()}"
        entries = list(output_directory.iterdir())
        if not output_directory.is_dir() or entries:
            raise ValueError("test output directory must be empty")
        if output_type == "DXF":
            payload = source.read_bytes()
            if payload.startswith(b"AC1032" + self._MARKER):
                destination.write_bytes(payload[6 + len(self._MARKER) :])
            else:
                destination.write_bytes(self.initial_dxf.read_bytes())
        elif output_type == "DWG":
            destination.write_bytes(b"AC1032" + self._MARKER + source.read_bytes())
        else:
            raise ValueError("unexpected test conversion type")
        return destination


def create_fake_dwg(path: Path) -> None:
    """Create a non-drawing source placeholder with the supported DWG signature."""

    path.write_bytes(b"AC1032synthetic-source")


def build_synthetic_audit(
    dxf_path: Path,
    source_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build an audit from runtime-only synthetic DXF data in test support."""

    from liang_pingfa_review.audit import build_audit
    from liang_pingfa_review.canonical import describe_source
    from liang_pingfa_review.oda import SUPPORTED_ODA_VERSION
    from liang_pingfa_review.snapshots import snapshot_dxf

    return build_audit(
        snapshot_dxf(dxf_path),
        describe_source(source_path),
        oda_version=SUPPORTED_ODA_VERSION,
        now=now,
    )


def delete_audited_text_in_synthetic_dxf(
    source_dxf: Path,
    destination_dxf: Path,
    audit: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> None:
    """Test-only synthetic DXF harness; no production module exposes this path API."""

    from liang_pingfa_review.apply import (
        _live_target_entity,
        _validate_targets_before_mutation,
    )
    from liang_pingfa_review.contracts import validate_artifact
    from liang_pingfa_review.plan import validate_plan_against_audit
    from liang_pingfa_review.snapshots import open_preflighted_dxf, snapshot_document

    checked_audit = validate_artifact("audit", audit)
    checked_plan = validate_plan_against_audit(checked_audit, plan)
    with open_preflighted_dxf(source_dxf) as (document, raw_preflight):
        _validate_targets_before_mutation(
            snapshot_document(document, raw_preflight=raw_preflight),
            checked_plan,
        )
    modelspace = document.modelspace()
    seen: set[str] = set()
    for operation in checked_plan["operations"]:
        target = operation["target"]
        handle = target["handle"]
        if handle in seen:
            raise ValueError("synthetic plan contains duplicate target")
        seen.add(handle)
        _live_target_entity(document, modelspace, target)
    for operation in checked_plan["operations"]:
        modelspace.delete_entity(
            _live_target_entity(document, modelspace, operation["target"])
        )
    document.saveas(destination_dxf)
