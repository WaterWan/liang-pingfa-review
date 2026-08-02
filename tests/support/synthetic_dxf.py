"""Create original, runtime-only DXF fixtures and a mocked ODA converter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import io
from pathlib import Path
from typing import Any
import math
import random

import ezdxf
from ezdxf.enums import TextEntityAlignment


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


def create_topology_dxf(
    path: Path,
    *,
    orientation_degrees: float = 0.0,
    translation: tuple[float, float] = (0.0, 0.0),
    scale: float = 1.0,
    reversed_edges: bool = False,
    shuffle_seed: int | None = None,
    support_vertex_start: int = 0,
    reverse_support_winding: bool = False,
    variant: str = "consistent",
) -> None:
    """Create a source-free beam topology fixture entirely at runtime.

    The local model has one three-support beam with an ID, one support-upper
    label, and one span-lower label.  Variants mutate only the generated
    topology evidence; no external drawing, text dump, or coordinate fixture
    is used.
    """

    if scale <= 0:
        raise ValueError("topology fixture scale must be positive")
    angle = math.radians(orientation_degrees)
    cosine, sine = math.cos(angle), math.sin(angle)

    def transform(point: tuple[float, float]) -> tuple[float, float]:
        return (
            translation[0] + scale * (point[0] * cosine - point[1] * sine),
            translation[1] + scale * (point[0] * sine + point[1] * cosine),
        )

    document = ezdxf.new("R2018")
    document.ezdxf_metadata().discard("CREATED_BY_EZDXF")
    modelspace = document.modelspace()
    for layer in ("BEAM", "BEAM_ID", "SUPPORT", "UPPER", "LOWER", "LEADER"):
        document.layers.new(layer)
    role_layers = {
        "beam_edges": "BEAM",
        "beam_ids": "BEAM_ID",
        "generic_supports": "SUPPORT",
        "support_upper_annotations": "UPPER",
        "span_lower_annotations": "LOWER",
        "leaders": "LEADER",
    }

    operations: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def line(
        start: tuple[float, float],
        end: tuple[float, float],
        layer: str,
    ) -> None:
        if reversed_edges:
            start, end = end, start
        operations.append(
            ("line", (transform(start), transform(end)), {"layer": layer})
        )

    def polyline(
        points: list[tuple[float, float]],
        layer: str,
        *,
        close: bool = False,
        bulge: float = 0.0,
        start_width: float = 0.0,
        end_width: float = 0.0,
        const_width: float = 0.0,
        thickness: float = 0.0,
        elevation: float = 0.0,
        extrusion: tuple[float, float, float] = (0.0, 0.0, 1.0),
    ) -> None:
        transformed = [
            (*transform(point), start_width, end_width, bulge)
            for point in points
        ]
        attributes: dict[str, object] = {"layer": layer, "close": close}
        if const_width != 0.0:
            attributes["const_width"] = const_width
        if thickness != 0.0:
            attributes["thickness"] = thickness
        if elevation != 0.0:
            attributes["elevation"] = elevation
        if extrusion != (0.0, 0.0, 1.0):
            attributes["extrusion"] = extrusion
        operations.append(
            ("polyline", (transformed,), attributes)
        )

    def text(
        value: str,
        insert: tuple[float, float],
        layer: str,
        *,
        alignment: TextEntityAlignment = TextEntityAlignment.LEFT,
        invalid_alignment: bool = False,
    ) -> None:
        operations.append(
            (
                "text",
                (value, transform(insert), alignment, invalid_alignment),
                {
                    "layer": layer,
                    "height": 4.0 * scale,
                    "rotation": orientation_degrees,
                },
            )
        )

    if variant == "chain-relation-overload":
        # 100 independently paired fragments and 100 overlapping explicit
        # supports make an adversarial relation set without external fixtures.
        # The topology policy must fail at the fixed relation budget.
        for index in range(100):
            left = float(index * 100)
            line((left, 0), (left + 100, 0), "BEAM")
            line((left, 20), (left + 100, 20), "BEAM")
            text("B1", (left + 40, 10), "BEAM_ID")
        for _index in range(100):
            polyline(
                [(0, -10), (10_000, -10), (10_000, 30), (0, 30)],
                "SUPPORT",
                close=True,
            )
    elif variant == "collinear-different-ids":
        line((0, 0), (100, 0), "BEAM")
        line((0, 20), (100, 20), "BEAM")
        line((100, 0), (200, 0), "BEAM")
        line((100, 20), (200, 20), "BEAM")
    elif variant in {
        "uniform-width-chain",
        "variable-width-chain",
        "width-tolerance-boundary",
    }:
        # Two supported fragments require a chain admission decision.  The
        # wide second section makes the upper label cross its local beam edge
        # even though a former global average would have placed it outside.
        second_width = {
            "uniform-width-chain": 20.0,
            "variable-width-chain": 32.0,
            "width-tolerance-boundary": 22.0,
        }[variant]
        second_low = {
            "uniform-width-chain": 0.0,
            "variable-width-chain": -6.0,
            "width-tolerance-boundary": -1.0,
        }[variant]
        line((0, 0), (100, 0), "BEAM")
        line((0, 20), (100, 20), "BEAM")
        line((100, second_low), (200, second_low), "BEAM")
        line((100, second_low + second_width), (200, second_low + second_width), "BEAM")
    else:
        line((0, 0), (200, 0), "BEAM")
        line((0, 20), (200, 20), "BEAM")
    if variant != "chain-relation-overload":
        for x in (0, 100, 200):
            support_low, support_high = (-10, 30)
            if variant in {"variable-width-chain", "width-tolerance-boundary"}:
                support_low, support_high = (-20, 40)
            support_points = [
                (x - 10, support_low),
                (x + 10, support_low),
                (x + 10, support_high),
                (x - 10, support_high),
            ]
            if reverse_support_winding:
                support_points.reverse()
            offset = support_vertex_start % len(support_points)
            support_points = support_points[offset:] + support_points[:offset]
            polyline(
                support_points,
                "SUPPORT",
                close=True,
            )
        text("B1", (40, 10), "BEAM_ID")
        if variant == "collinear-different-ids":
            text("B2", (140, 10), "BEAM_ID")
        elif variant in {
            "uniform-width-chain",
            "variable-width-chain",
            "width-tolerance-boundary",
        }:
            text("B1", (140, 10), "BEAM_ID")
        elif variant == "conflicting-id-orphan-axis":
            text("B2", (45, 10), "BEAM_ID")

        upper_position = (50, 30)
        lower_position = (45, -30)
        lower_text = "L1"
        if variant == "wrong-side-upper":
            # Illegal relative to the interior support's two adjacent span
            # intervals, without assigning a global/polygon-derived side.
            upper_position = (195, 30)
        elif variant == "shared-upper-right":
            upper_position = (150, 30)
        elif variant == "upper-inside-shared-support":
            upper_position = (98, 30)
        elif variant == "lower-outside-midspan":
            lower_position = (18, -30)
        elif variant == "crossing-support":
            lower_position = (98, -30)
        elif variant == "entering-next-span":
            lower_position = (84, -30)
            lower_text = "L123456789"
        elif variant == "crossing-beam":
            lower_position = (150, 10)
        elif variant == "neighboring-crossing-beam":
            lower_position = (45, 80)
        elif variant in {
            "variable-width-chain",
            "width-tolerance-boundary",
        }:
            upper_position = (150, 24)
        text("U1", upper_position, "UPPER")
        text(lower_text, lower_position, "LOWER")
        if variant == "competing-shared-upper":
            text("U2", (150, 30), "UPPER")
        if variant == "leader-matches-geometry":
            # A sole exact leader reaches the lower label's established span.
            line((45, -30), (45, 10), "LEADER")
        if variant in {
            "leader-crosses-unbound-support",
            "leader-disjoint-unbound-support",
            "leader-touches-unbound-support",
            "leader-near-unbound-support",
            "leader-overlapping-unbound-supports",
        }:
            line((45, -30), (45, 10), "LEADER")
            if variant == "leader-crosses-unbound-support":
                polyline(
                    [(35, -20), (55, -20), (55, -10), (35, -10)],
                    "SUPPORT",
                    close=True,
                )
            elif variant == "leader-disjoint-unbound-support":
                polyline(
                    [(300, -20), (320, -20), (320, -10), (300, -10)],
                    "SUPPORT",
                    close=True,
                )
            elif variant == "leader-touches-unbound-support":
                polyline(
                    [(45, -20), (65, -20), (65, -10), (45, -10)],
                    "SUPPORT",
                    close=True,
                )
            elif variant == "leader-near-unbound-support":
                near = 5.0e-10
                polyline(
                    [(45 + near, -20), (65, -20), (65, -10), (45 + near, -10)],
                    "SUPPORT",
                    close=True,
                )
            else:
                for low, high in ((-22, -12), (-18, -8)):
                    polyline(
                        [(35, low), (55, low), (55, high), (35, high)],
                        "SUPPORT",
                        close=True,
                    )
        if variant == "annotation-overlaps-unbound-support":
            polyline(
                [(40, -34), (60, -34), (60, -26), (40, -26)],
                "SUPPORT",
                close=True,
            )
        if variant == "leader-conflicts-other-span":
            # The label is in the left span, but this leader reaches only the
            # right span axis and avoids the intervening support rectangle.
            line((45, -30), (185, 10), "LEADER")
        if variant == "leader-conflicts-other-support":
            # The upper label belongs to the interior support; this optional
            # leader reaches the distinct outer support instead.
            line((50, 30), (0, 10), "LEADER")
        if variant == "ambiguous-leader-targets":
            line((45, -30), (45, 10), "LEADER")
            line((45, -30), (185, 10), "LEADER")
        if variant == "repeated-text":
            text(lower_text, (55, -30), "LOWER")
        if variant == "overlap-blocked":
            text("L1", upper_position, "LOWER")
        if variant == "overlap-center":
            text(
                "U2",
                (54, 30),
                "UPPER",
                alignment=TextEntityAlignment.CENTER,
            )
        if variant == "overlap-right":
            text(
                "U3",
                (57, 30),
                "UPPER",
                alignment=TextEntityAlignment.RIGHT,
            )
        if variant == "overlap-unsupported-alignment":
            text(
                "U4",
                (54, 30),
                "UPPER",
                alignment=TextEntityAlignment.MIDDLE_CENTER,
            )
        if variant == "unbounded-unsupported-alignment":
            text("U6", (54, 30), "UPPER", invalid_alignment=True)
        if variant == "distant-unsupported-alignment":
            text(
                "U5",
                (1_000, 1_000),
                "UPPER",
                alignment=TextEntityAlignment.MIDDLE_CENTER,
            )
        if variant == "unsupported-edge":
            polyline([(20, 40), (80, 40)], "BEAM", bulge=0.5)
        if variant == "unpaired-intersecting-edge":
            # A configured but unmatched edge crosses the lower label's
            # glyph bounds.  It must remain controlled private geometry.
            line((44, -30), (60, -30), "BEAM")
        if variant == "unpaired-nearby-disjoint-edge":
            # Nearby only in the drawing at large: exact bounds/corridors
            # prove this unmatched edge is disjoint from both annotations.
            line((300, -100), (300, 100), "BEAM")
        if variant == "ambiguous-extra-parallel-edge":
            # Three otherwise matching parallel edges have no mutual pairing.
            line((0, 40), (200, 40), "BEAM")
        if variant == "unpaired-crossing-edge":
            # It misses both label bounds but crosses both relevant target
            # corridors, so a legal conclusion is not possible.
            line((60, -60), (60, 80), "BEAM")
        if variant == "paired-no-id-crossing-axis":
            # This is a valid mutually paired axis, but its absent beam ID
            # must leave it as private blocking geometry rather than discard
            # it after successful edge pairing.
            line((52, 20), (52, 80), "BEAM")
            line((72, 20), (72, 80), "BEAM")
        if variant == "paired-no-id-disjoint-axis":
            line((300, 0), (300, 100), "BEAM")
            line((320, 0), (320, 100), "BEAM")
        if variant == "ambiguous-chain-orphan-axis":
            # B2 uniquely identifies this vertical pair, but overlapping
            # explicit support clips make its chain admission ambiguous.
            line((52, 20), (52, 100), "BEAM")
            line((72, 20), (72, 100), "BEAM")
            text("B2", (62, 80), "BEAM_ID")
            polyline(
                [(42, 20), (82, 20), (82, 40), (42, 40)],
                "SUPPORT",
                close=True,
            )
            polyline(
                [(42, 30), (82, 30), (82, 50), (42, 50)],
                "SUPPORT",
                close=True,
            )
        if variant == "polyline-const-width":
            polyline([(20, 40), (80, 40)], "BEAM", const_width=1.0)
        if variant == "polyline-thickness":
            polyline(
                [(20, 40), (80, 40), (80, 60), (20, 60)],
                "SUPPORT",
                close=True,
                thickness=1.0,
            )
        if variant == "polyline-vertex-width":
            polyline(
                [(45, -30), (45, 10)],
                "LEADER",
                start_width=1.0,
                end_width=1.0,
            )
        if variant == "polyline-combination":
            polyline(
                [(20, 40), (80, 40)],
                "BEAM",
                bulge=0.5,
                const_width=1.0,
                thickness=1.0,
                elevation=1.0,
                extrusion=(1.0, 0.0, 0.0),
            )
        if variant == "secondary-intersection":
            line((100, -50), (100, 70), "BEAM")
            line((120, -50), (120, 70), "BEAM")
            text("B2", (110, 0), "BEAM_ID")
        if variant == "neighboring-crossing-beam":
            line((0, 120), (200, 120), "BEAM")
            line((0, 140), (200, 140), "BEAM")
            for x in (0, 100, 200):
                polyline(
                    [
                        (x - 10, 110),
                        (x + 10, 110),
                        (x + 10, 150),
                        (x - 10, 150),
                    ],
                    "SUPPORT",
                    close=True,
                )
            text("B2", (40, 130), "BEAM_ID")
            # Exact leader intersection points to the first beam while the
            # lower label's full bounds fall in the neighbouring beam's
            # legal span.
            line((45, 80), (45, 10), "LEADER")
        if variant == "relation-budget-50x50x50":
            # 50 labels × 50 touching leaders × 50 unpaired edges create
            # 125,000 plausible leader/geometry relations.  All source
            # geometry is generated at runtime; the assessment must hit its
            # shared relation budget before any partial artifact is emitted.
            for index in range(50):
                x = float(1_000 + index * 100)
                text(f"R{index}", (x, -30), "LOWER")
                line((950, -30), (6_000, -30), "LEADER")
                line((x + 50, -130), (x + 50, 70), "BEAM")

    if variant == "visible-malformed-annotation":
        # This is deliberately visible and on a configured layer: unlike
        # excluded display/layout records it must still reach the overlap and
        # insufficient-evidence paths.
        text(
            "U4",
            (54, 30),
            "UPPER",
            alignment=TextEntityAlignment.MIDDLE_CENTER,
        )

    if variant == "ignored-role-records":
        # The snapshot must preserve these entities, but topology may only
        # reason about direct visible opaque Modelspace graphics.  Each set
        # contains every configured role to exercise role caps and traces as
        # well as annotation overlap inventory.
        for prefix in ("OFF_", "FROZEN_"):
            for layer in role_layers.values():
                document.layers.new(f"{prefix}{layer}")

        def add_role_set(
            space: Any,
            *,
            offset: float,
            layer_prefix: str = "",
            invisible: bool = False,
            transparent: bool = False,
        ) -> None:
            def role_layer(role: str) -> str:
                return f"{layer_prefix}{role_layers[role]}"

            entities = [
                space.add_line(
                    (offset, 0),
                    (offset + 20, 0),
                    dxfattribs={"layer": role_layer("beam_edges")},
                ),
                space.add_lwpolyline(
                    [
                        (offset, -5),
                        (offset + 5, -5),
                        (offset + 5, 5),
                        (offset, 5),
                    ],
                    close=True,
                    dxfattribs={"layer": role_layer("generic_supports")},
                ),
                space.add_text(
                    "X1",
                    dxfattribs={
                        "layer": role_layer("beam_ids"),
                        "height": 4,
                        "insert": (offset + 5, 10),
                    },
                ),
                space.add_text(
                    "U2",
                    dxfattribs={
                        "layer": role_layer("support_upper_annotations"),
                        "height": 4,
                        "insert": (offset + 5, 20),
                    },
                ),
                space.add_text(
                    "L2",
                    dxfattribs={
                        "layer": role_layer("span_lower_annotations"),
                        "height": 4,
                        "insert": (offset + 5, -20),
                    },
                ),
                space.add_line(
                    (offset + 10, -20),
                    (offset + 10, 0),
                    dxfattribs={"layer": role_layer("leaders")},
                ),
            ]
            if invisible:
                for entity in entities:
                    entity.dxf.invisible = 1
            if transparent:
                for entity in entities:
                    entity.transparency = 1.0

        add_role_set(modelspace, offset=1_000, invisible=True)
        add_role_set(modelspace, offset=1_100, transparent=True)
        add_role_set(modelspace, offset=1_200, layer_prefix="OFF_")
        add_role_set(modelspace, offset=1_300, layer_prefix="FROZEN_")
        for layer in role_layers.values():
            document.layers.get(f"OFF_{layer}").off()
            document.layers.get(f"FROZEN_{layer}").freeze()
        add_role_set(document.layouts.new("ExcludedRoles"), offset=1_400)
        add_role_set(document.blocks.new("ExcludedRoles"), offset=1_500)

    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(operations)
    for operation, values, attributes in operations:
        if operation == "line":
            modelspace.add_line(
                values[0],  # type: ignore[arg-type]
                values[1],  # type: ignore[arg-type]
                dxfattribs=attributes,
            )
        elif operation == "polyline":
            modelspace.add_lwpolyline(
                values[0],  # type: ignore[arg-type]
                close=bool(attributes.pop("close")),
                dxfattribs=attributes,
                format="xyseb",
            )
        else:
            entity = modelspace.add_text(
                values[0],  # type: ignore[arg-type]
                dxfattribs={
                    **attributes,
                    "insert": values[1],  # type: ignore[index]
                },
            )
            alignment = values[2]
            if alignment != TextEntityAlignment.LEFT:
                entity.set_placement(
                    values[1],  # type: ignore[arg-type,index]
                    align=alignment,  # type: ignore[arg-type]
                )
            if values[3]:
                entity.dxf.halign = 99
    if path.exists():
        save_document_to_existing_default_stream(document, path)
    else:
        document.saveas(path)


def topology_profile_payload(
    *,
    layers: Mapping[str, list[str]] | None = None,
) -> dict[str, object]:
    """Return the strict local-only role profile used by generated fixtures."""

    default_layers: dict[str, list[str]] = {
        "beam_edges": ["BEAM"],
        "beam_ids": ["BEAM_ID"],
        "column_supports": [],
        "wall_supports": [],
        "generic_supports": ["SUPPORT"],
        "support_upper_annotations": ["UPPER"],
        "span_lower_annotations": ["LOWER"],
        "leaders": ["LEADER"],
    }
    if layers is not None:
        default_layers.update(layers)
    return {
        "schema_version": "liang-pingfa/beam-topology-profile/v1",
        "layers": default_layers,
    }
