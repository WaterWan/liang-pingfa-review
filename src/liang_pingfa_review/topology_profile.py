"""Bounded, read-only beam topology and in-situ annotation assessment.

The profile in this module is deliberately narrow.  It recognizes a small
set of direct Modelspace primitives that have already passed the repository's
raw-DXF preservation gate.  It is not a general CAD topology engine, never
uses a nearest-object heuristic, and only emits local, privacy-safe evidence.

Raw geometry, layer names, and text are retained only while this module builds
an assessment.  The returned mapping contains opaque identifiers and
fingerprints, never source coordinates or text.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from math import floor, hypot, isfinite, ulp
from pathlib import Path
import json
import re
from typing import Any, Literal, TypeAlias, cast
import unicodedata

from ezdxf import bbox
from ezdxf.enums import TextEntityAlignment
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .canonical import canonical_sha256
from .errors import ErrorCode, PipelineError
from .topology_ids import (
    derive_annotation_target_provenance_id,
    derive_chain_id,
    derive_span_id,
    derive_support_id,
    derive_topology_finding_id,
    derive_trace_id,
    entity_provenance,
)


POLICY_NAME = "beam-plan-in-situ/v1"
PROFILE_SCHEMA_VERSION = "liang-pingfa/beam-topology-profile/v1"

# The limits are fixed policy, not profile-controlled settings.  They cap
# untrusted drawing complexity before a public audit artifact is built.
MAX_ROLE_ENTITIES = 2_048
# This caps broad-phase candidate materialization only; every resulting exact
# relation is additionally governed by the shared budget below.
MAX_EDGE_PAIRS = 4_096
MAX_AXES = 1_024
MAX_SUPPORTS = 1_024
MAX_ANNOTATIONS = 2_048
MAX_LEADERS = 1_024
# One assessment-wide exact-relation budget.
MAX_CHAIN_RELATIONS = 4_096
MAX_GRID_REFERENCES = 16_384
MAX_TOKEN_LENGTH = 96
MAX_LAYER_NAME_LENGTH = 128
MAX_PROFILE_BYTES = 64 * 1024
# The profile can only use two-point open paths (beam edges/leaders) and
# four-point closed paths (supports).  This hard cap is deliberately applied
# before reading any LWPOLYLINE vertices: no configured role needs a larger
# primitive, so retaining it would only create unbounded private state.
MAX_LWPOLYLINE_VERTICES = 4
MAX_TOPOLOGY_VERTICES = MAX_ROLE_ENTITIES * MAX_LWPOLYLINE_VERTICES
# Fixed numeric policy: source WCS coordinates and private analysis values
# deliberately have different envelopes.  ``MAX_INPUT_COORDINATE`` is not a
# soft version of the derived cap: the fixed 1e100 headroom remains after the
# largest operation used here, a product of two coordinate-scale values.
#
# More specifically, every source coordinate is at most I=1e100.  Differences,
# widths, corridor expansions, reconstructed rotated/scaled points, and grid
# numerators are bounded by fixed small multiples of I; dot/cross products and
# squared lengths are bounded by fixed small multiples of I²=1e200.  The
# derived cap D=1e300 therefore retains a factor of 1e100 beyond I² (and
# 1e200 beyond a linear coordinate).  This is deterministic policy headroom,
# not reliance on a particular drawing's scale or orientation.  Grid
# coordinates are additionally bounded so a tiny cell size cannot create
# unbounded integer keys despite finite floats.
MAX_INPUT_COORDINATE = 1.0e100
MAX_DERIVED_SCALAR = 1.0e300
NUMERIC_PRODUCT_HEADROOM_FACTOR = MAX_DERIVED_SCALAR / (
    MAX_INPUT_COORDINATE * MAX_INPUT_COORDINATE
)
MAX_NORMALIZED_COORDINATE_MAGNITUDE = 1_000_000_000.0

_EPSILON = 1e-9
_PLANE_TOLERANCE = 1e-6
_PARALLEL_SINE_LIMIT = 1e-4
_SCALE_RATIO_LIMIT = 1_000_000.0
_MIN_WIDTH_RATIO = 0.002
_MAX_WIDTH_RATIO = 0.45
_ENDPOINT_RATIO = 0.02
# Chain members must have one cross-section, modulo this fixed drawing-scale
# tolerance.  Equality at the boundary is a topology tie, not a license to
# choose a width or average the two sections.
_CHAIN_WIDTH_TOLERANCE_RATIO = 0.02
_ID_CORRIDOR_WIDTHS = 3.0
_ANNOTATION_OUTER_WIDTHS = 4.5
_MIDSPAN_FRACTION = 0.25
_TOKEN_PATTERN = re.compile(r"^[^\s\x00-\x1f\x7f]{1,96}$")

Role = Literal[
    "beam_edges",
    "beam_ids",
    "column_supports",
    "wall_supports",
    "generic_supports",
    "support_upper_annotations",
    "span_lower_annotations",
    "leaders",
]

_ROLES: tuple[Role, ...] = (
    "beam_edges",
    "beam_ids",
    "column_supports",
    "wall_supports",
    "generic_supports",
    "support_upper_annotations",
    "span_lower_annotations",
    "leaders",
)
_SUPPORT_ROLES = frozenset(
    {"column_supports", "wall_supports", "generic_supports"}
)
_ANNOTATION_ROLES = frozenset(
    {"support_upper_annotations", "span_lower_annotations"}
)

Point: TypeAlias = tuple[float, float, float]


@dataclass(frozen=True)
class TopologyEntityEvidence:
    """Private normalized source evidence captured while snapshotting a DXF.

    ``text`` is retained only when it is token-size bounded.  It never leaves
    this module as text: downstream code uses it only for private in-memory
    equality and renders no token-derived fingerprint.
    """

    entity_type: str
    vertices: tuple[Point, ...] = ()
    closed: bool = False
    widths_zero: bool = False
    bulges_zero: bool = False
    default_ocs: bool = False
    plane_elevation: float | None = None
    # These extrema describe the source geometry before any private work-plane
    # projection.  Binding gates use the complete envelope so tolerance cannot
    # be chained through intermediate baseline comparisons.
    plane_min: float | None = None
    plane_max: float | None = None
    vertex_count: int | None = None
    vertex_limit_exceeded: bool = False
    text: str | None = None
    text_length: int = 0
    text_bounds: "Aabb | None" = None
    text_alignment: str | None = None
    rotation: float | None = None
    finite: bool = False
    numeric_limit_exceeded: bool = False


@dataclass(frozen=True)
class LayerRoles:
    """Canonical, pairwise-disjoint local layer roles."""

    beam_edges: frozenset[str]
    beam_ids: frozenset[str]
    column_supports: frozenset[str]
    wall_supports: frozenset[str]
    generic_supports: frozenset[str]
    support_upper_annotations: frozenset[str]
    span_lower_annotations: frozenset[str]
    leaders: frozenset[str]

    def role_for(self, normalized_layer: str) -> Role | None:
        for role in _ROLES:
            if normalized_layer in getattr(self, role):
                return role
        return None


@dataclass(frozen=True)
class TopologyProfile:
    """The complete immutable, local-only topology profile."""

    roles: LayerRoles
    profile_fingerprint: str


@dataclass(frozen=True)
class TopologySnapshotContext:
    """Validated immutable role data needed before private extraction.

    Snapshot construction needs no topology rules or source profile payload:
    it receives only these normalized role names and uses them to reject
    unrelated records before any private bbox, vertex, or text capture.
    """

    roles: LayerRoles
    role_layers: frozenset[str]


@dataclass(frozen=True)
class Aabb:
    """A finite private XY bounding box on one WCS plane."""

    minimum: Point
    maximum: Point

    def __post_init__(self) -> None:
        if (
            not _points_within_derived_policy((self.minimum, self.maximum))
            or self.minimum[0] > self.maximum[0]
            or self.minimum[1] > self.maximum[1]
            or self.minimum[2] != self.maximum[2]
        ):
            raise _limit_error()

    @property
    def plane(self) -> float:
        return self.minimum[2]

    @property
    def width(self) -> float:
        return _checked_derived(self.maximum[0] - self.minimum[0])

    @property
    def height(self) -> float:
        return _checked_derived(self.maximum[1] - self.minimum[1])

    def corners(self) -> tuple[Point, Point, Point, Point]:
        z = self.plane
        return (
            (self.minimum[0], self.minimum[1], z),
            (self.minimum[0], self.maximum[1], z),
            (self.maximum[0], self.minimum[1], z),
            (self.maximum[0], self.maximum[1], z),
        )

    def overlaps(self, other: "Aabb", tolerance: float = _EPSILON) -> bool:
        return not (
            self.maximum[0] < other.minimum[0] - tolerance
            or other.maximum[0] < self.minimum[0] - tolerance
            or self.maximum[1] < other.minimum[1] - tolerance
            or other.maximum[1] < self.minimum[1] - tolerance
        )

    def expanded(self, amount: float) -> "Aabb":
        if not _within_numeric_policy(amount, derived=True):
            raise _limit_error()
        return Aabb(
            (
                _checked_derived(self.minimum[0] - amount),
                _checked_derived(self.minimum[1] - amount),
                self.plane,
            ),
            (
                _checked_derived(self.maximum[0] + amount),
                _checked_derived(self.maximum[1] + amount),
                self.plane,
            ),
        )


@dataclass(frozen=True)
class _Segment:
    record: Any
    start: Point
    end: Point
    length: float
    unit: tuple[float, float]
    bounds: Aabb
    plane: float


@dataclass(frozen=True)
class _UnresolvedBeamGeometry:
    """A valid configured beam edge that cannot safely become an axis.

    These records are deliberately private.  Their provenance and bounds let
    later annotation gates prove disjointness without exposing a handle,
    source coordinate, or raw layer/token value in the artifact.
    """

    segment: _Segment
    provenance: str
    bounds: Aabb
    reason: Literal["unpaired", "ambiguous", "orphan"]


@dataclass(frozen=True)
class _Axis:
    """One paired beam centre axis, expressed with a canonical direction."""

    first: _Segment
    second: _Segment
    start: Point
    end: Point
    unit: tuple[float, float]
    normal: tuple[float, float]
    radial: float
    width: float
    plane: float
    identifier: str

    @property
    def length(self) -> float:
        return _distance(self.start, self.end)

    @property
    def records(self) -> tuple[Any, Any]:
        return (self.first.record, self.second.record)

    @property
    def bounds(self) -> Aabb:
        return _bounds_from_points((self.start, self.end))

    def global_s(self, point: Point) -> float:
        return _dot((point[0], point[1]), self.unit)

    def global_r(self, point: Point) -> float:
        return _dot((point[0], point[1]), self.normal)


@dataclass(frozen=True)
class _AxisDiscovery:
    """The paired axes and every controlled edge that was not paired."""

    axes: tuple[_Axis, ...]
    unresolved: tuple[_UnresolvedBeamGeometry, ...]


@dataclass(frozen=True)
class _Support:
    record: Any
    role: Role
    vertices: tuple[Point, Point, Point, Point]
    bounds: Aabb
    plane: float
    geometry_fingerprint: str


@dataclass(frozen=True)
class _SupportOnChain:
    support: _Support
    low: float
    high: float
    support_id: str


@dataclass(frozen=True)
class _Span:
    span_id: str
    left: _SupportOnChain
    right: _SupportOnChain
    low: float
    high: float
    width: float


@dataclass(frozen=True)
class _Chain:
    chain_id: str
    axes: tuple[_Axis, ...]
    unit: tuple[float, float]
    normal: tuple[float, float]
    radial: float
    plane: float
    width: float
    low: float
    high: float
    supports: tuple[_SupportOnChain, ...]
    spans: tuple[_Span, ...]

    def project_bounds(self, bounds: Aabb) -> tuple[float, float, float, float]:
        s_values = [
            _dot((point[0], point[1]), self.unit) for point in bounds.corners()
        ]
        r_values = [
            _checked_derived(
                _dot((point[0], point[1]), self.normal) - self.radial
            )
            for point in bounds.corners()
        ]
        return min(s_values), max(s_values), min(r_values), max(r_values)

@dataclass(frozen=True)
class _TextCandidate:
    record: Any
    role: Role
    bounds: Aabb
    evidence: TopologyEntityEvidence


@dataclass(frozen=True)
class _TextOverlapEntry:
    """One direct visible controlled-layer TEXT before semantic eligibility."""

    record: Any
    role: Role
    bounds: Aabb | None
    plane: float | None


@dataclass(frozen=True)
class _Leader:
    record: Any
    start: Point
    end: Point
    bounds: Aabb


@dataclass(frozen=True)
class _AnnotationTarget:
    chain_id: str
    support_id: str | None
    span_id: str | None


def _profile_error() -> PipelineError:
    return PipelineError(ErrorCode.TOPOLOGY_PROFILE_INVALID, "topology profile invalid")


def _limit_error() -> PipelineError:
    return PipelineError(ErrorCode.TOPOLOGY_LIMIT_EXCEEDED, "topology policy limit exceeded")


@dataclass
class RelationBudget:
    """One fail-closed budget for every exact topology relation.

    Broad-phase grids and interval indexes are intentionally not charged:
    they only reject impossible pairs.  Callers must charge immediately
    before every exact geometry, zone, equality, or tie predicate.  Reading
    the module constant at charge time keeps policy-limit tests and deployed
    policy overrides deterministic.
    """

    used: int = 0

    def charge(self) -> None:
        if self.used >= MAX_CHAIN_RELATIONS:
            raise _limit_error()
        self.used += 1


def _finite_point(value: Any) -> Point | None:
    try:
        if all(hasattr(value, name) for name in ("x", "y", "z")):
            point = (float(value.x), float(value.y), float(value.z))
        elif isinstance(value, (tuple, list)) and len(value) >= 2:
            point = (
                float(value[0]),
                float(value[1]),
                float(value[2]) if len(value) >= 3 else 0.0,
            )
        else:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return point if all(isfinite(part) for part in point) else None


def _within_numeric_policy(value: float, *, derived: bool = False) -> bool:
    """Return whether a source or private-derived scalar is policy-safe."""

    magnitude = MAX_DERIVED_SCALAR if derived else MAX_INPUT_COORDINATE
    return isfinite(value) and abs(value) <= magnitude


def _points_within_numeric_policy(points: Iterable[Point]) -> bool:
    """Validate untrusted source WCS points against the input envelope."""

    return all(
        all(_within_numeric_policy(component) for component in point)
        for point in points
    )


def _points_within_derived_policy(points: Iterable[Point]) -> bool:
    """Validate private constructed geometry against the derived envelope."""

    return all(
        all(_within_numeric_policy(component, derived=True) for component in point)
        for point in points
    )


def _checked_derived(value: float) -> float:
    if not _within_numeric_policy(value, derived=True):
        raise _limit_error()
    return value


def _finite_scalar(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _bounds_from_points(points: Iterable[Point]) -> Aabb:
    values = tuple(points)
    if not values or not _points_within_derived_policy(values):
        raise _limit_error()
    return Aabb(
        (
            min(point[0] for point in values),
            min(point[1] for point in values),
            min(point[2] for point in values),
        ),
        (
            max(point[0] for point in values),
            max(point[1] for point in values),
            max(point[2] for point in values),
        ),
    )


def _reliable_text_bounds(
    minimum: Point,
    maximum: Point,
    *,
    glyph_height: float,
) -> Aabb | None:
    """Return only rendered TEXT bounds resolvable at their WCS magnitude.

    Renderer extents are input evidence, rather than geometry this module may
    repair.  In particular, a nonzero text height does not make a collapsed
    renderer box meaningful at a large WCS translation.  Require both
    dimensions to exceed the fixed planar floor, a scale-relative roundoff
    allowance, and one ULP at the reported coordinate magnitude.
    """

    if (
        not all(isfinite(value) for value in (*minimum, *maximum, glyph_height))
        or not _points_within_derived_policy((minimum, maximum))
        or not _within_numeric_policy(glyph_height)
        or glyph_height <= 0.0
        or minimum[0] > maximum[0]
        or minimum[1] > maximum[1]
    ):
        return None
    coordinate_magnitude = max(
        abs(value)
        for value in (
            minimum[0],
            minimum[1],
            maximum[0],
            maximum[1],
        )
    )
    tolerance = max(
        _PLANE_TOLERANCE,
        glyph_height * _EPSILON,
        ulp(coordinate_magnitude),
    )
    if (
        _checked_derived(maximum[0] - minimum[0]) <= tolerance
        or _checked_derived(maximum[1] - minimum[1]) <= tolerance
    ):
        return None
    return Aabb(minimum, maximum)


def _distance(first: Point, second: Point) -> float:
    vector = _subtract(first, second)
    return _checked_derived(hypot(*vector))


def _project_to_work_plane(
    points: Sequence[Point],
    *,
    plane: float,
) -> tuple[Point, ...] | None:
    """Return private planar analysis points without changing source records.

    A primitive establishes one canonical elevation from its DXF plane/anchor.
    Every accepted source point must be finite, inside the input envelope, and
    within the fixed residual tolerance of that elevation.  Only this returned
    private tuple is flattened; snapshots and their fingerprints still retain
    the original DXF coordinates.
    """

    if not _within_numeric_policy(plane) or not _points_within_numeric_policy(points):
        return None
    projected: list[Point] = []
    for x, y, z in points:
        if abs(_checked_derived(z - plane)) > _PLANE_TOLERANCE:
            return None
        projected.append((x, y, plane))
    return tuple(projected)


def _default_xy_extrusion(entity: Any) -> bool:
    try:
        extrusion = _finite_point(entity.dxf.get("extrusion", (0.0, 0.0, 1.0)))
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        extrusion is not None
        and abs(extrusion[0]) <= _PLANE_TOLERANCE
        and abs(extrusion[1]) <= _PLANE_TOLERANCE
        and abs(extrusion[2] - 1.0) <= _PLANE_TOLERANCE
    )


def _source_plane_extrema(points: Iterable[Point]) -> tuple[float, float] | None:
    """Return finite original-elevation extrema without flattening evidence."""

    values = tuple(points)
    if not values or not _points_within_numeric_policy(values):
        return None
    elevations = tuple(point[2] for point in values)
    return min(elevations), max(elevations)


def _line_evidence(entity: Any) -> TopologyEntityEvidence:
    try:
        start = _finite_point(entity.dxf.start)
        end = _finite_point(entity.dxf.end)
        thickness = _finite_scalar(entity.dxf.get("thickness", 0.0))
    except (AttributeError, TypeError, ValueError):
        return TopologyEntityEvidence(entity_type="LINE")
    plane_extrema = (
        _source_plane_extrema((start, end))
        if start is not None and end is not None
        else None
    )
    finite = (
        start is not None
        and end is not None
        and thickness is not None
        and abs(thickness) <= _PLANE_TOLERANCE
        and plane_extrema is not None
        and plane_extrema[1] - plane_extrema[0] <= _PLANE_TOLERANCE
        and _default_xy_extrusion(entity)
    )
    numeric_limit_exceeded = (
        (start is not None and not _points_within_numeric_policy((start,)))
        or (end is not None and not _points_within_numeric_policy((end,)))
        or (thickness is not None and not _within_numeric_policy(thickness))
    )
    return TopologyEntityEvidence(
        entity_type="LINE",
        # Preserve source elevations until a binding has validated its global
        # envelope; role conversion performs the private work-plane projection.
        vertices=(start, end) if start is not None and end is not None else (),
        closed=False,
        widths_zero=True,
        bulges_zero=True,
        default_ocs=_default_xy_extrusion(entity),
        plane_elevation=start[2] if finite and start is not None else None,
        plane_min=plane_extrema[0] if plane_extrema is not None else None,
        plane_max=plane_extrema[1] if plane_extrema is not None else None,
        vertex_count=2 if start is not None and end is not None else None,
        finite=finite,
        numeric_limit_exceeded=numeric_limit_exceeded,
    )


def _lwpolyline_evidence(entity: Any) -> TopologyEntityEvidence:
    """Accept only a zero-width, zero-thickness, default-OCS planar path.

    A LWPOLYLINE's centre path is not visible geometry when any width,
    thickness, bulge, elevation, or OCS transformation is present.  All role
    consumers (beam edges, supports, and leaders) share this extractor, so
    rejecting it here prevents an unsuitable path from becoming topology
    evidence in just one of those roles.
    """

    try:
        # ``dxf.count`` is the fixed-size tag-backed vertex count.  Read and
        # cap it before calling any vertex iterator; malformed/oversized paths
        # stay represented only by bounded unsupported metadata.
        count = int(entity.dxf.get("count"))
        closed = bool(entity.closed)
        const_width = _finite_scalar(entity.dxf.get("const_width", 0.0))
        thickness = _finite_scalar(entity.dxf.get("thickness", 0.0))
        elevation = _finite_scalar(entity.dxf.get("elevation", 0.0))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return TopologyEntityEvidence(entity_type="LWPOLYLINE")
    if count < 0 or count > MAX_LWPOLYLINE_VERTICES:
        return TopologyEntityEvidence(
            entity_type="LWPOLYLINE",
            closed=closed,
            plane_elevation=elevation,
            plane_min=elevation,
            plane_max=elevation,
            vertex_count=count,
            vertex_limit_exceeded=True,
        )
    try:
        # One bounded pass is sufficient under the default OCS policy.  Do
        # not call ``vertices_in_wcs`` as a second, independently materialized
        # iterator.
        points = tuple(entity.get_points("xyseb"))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return TopologyEntityEvidence(entity_type="LWPOLYLINE", vertex_count=count)
    if len(points) != count or elevation is None:
        return TopologyEntityEvidence(entity_type="LWPOLYLINE", vertex_count=count)

    widths_zero = (
        const_width is not None
        and abs(const_width) <= _PLANE_TOLERANCE
        and thickness is not None
        and abs(thickness) <= _PLANE_TOLERANCE
    )
    bulges_zero = True
    vertices: list[Point] = []
    numeric_limit_exceeded = False
    for point in points:
        if len(point) != 5:
            return TopologyEntityEvidence(entity_type="LWPOLYLINE")
        x, y, start_width, end_width, bulge = (
            _finite_scalar(value) for value in point
        )
        if (
            x is None
            or y is None
            or start_width is None
            or end_width is None
            or bulge is None
        ):
            return TopologyEntityEvidence(entity_type="LWPOLYLINE", vertex_count=count)
        vertices.append((x, y, elevation))
        numeric_limit_exceeded = numeric_limit_exceeded or any(
            not _within_numeric_policy(value)
            for value in (x, y, start_width, end_width, bulge)
        )
        widths_zero = (
            widths_zero
            and abs(start_width) <= _PLANE_TOLERANCE
            and abs(end_width) <= _PLANE_TOLERANCE
        )
        bulges_zero = bulges_zero and abs(bulge) <= _PLANE_TOLERANCE
    typed_vertices = tuple(vertices)
    numeric_limit_exceeded = numeric_limit_exceeded or not _points_within_numeric_policy(
        typed_vertices
    )

    default_ocs = _default_xy_extrusion(entity)
    plane_extrema = _source_plane_extrema(typed_vertices)
    finite = bool(typed_vertices) and (
        widths_zero
        and bulges_zero
        and elevation is not None
        and plane_extrema is not None
        and plane_extrema[1] - plane_extrema[0] <= _PLANE_TOLERANCE
        and default_ocs
    )
    return TopologyEntityEvidence(
        entity_type="LWPOLYLINE",
        vertices=typed_vertices,
        closed=closed,
        widths_zero=widths_zero,
        bulges_zero=bulges_zero,
        default_ocs=default_ocs,
        plane_elevation=elevation if finite else None,
        plane_min=plane_extrema[0] if plane_extrema is not None else None,
        plane_max=plane_extrema[1] if plane_extrema is not None else None,
        vertex_count=count,
        finite=finite,
        numeric_limit_exceeded=(
            numeric_limit_exceeded
            or any(
                value is not None and not _within_numeric_policy(value)
                for value in (const_width, thickness, elevation)
            )
        ),
    )


def _text_evidence(entity: Any) -> TopologyEntityEvidence:
    """Capture conservative WCS bounds for directly renderable TEXT forms.

    Semantic binding remains deliberately narrower than this geometry capture:
    the overlap gate must see CENTER, RIGHT, and every other supported
    alignment before it can reject a record for a role-specific grammar.
    """

    try:
        text = str(entity.dxf.text)
        rotation = _finite_scalar(entity.dxf.get("rotation", 0.0))
        # Missing, zero, negative, sub-tolerance, and non-finite heights do
        # not describe glyph geometry that semantic topology may bind.  Do
        # not substitute a style/default height: the snapshot predicate also
        # rejects an absent height rather than inventing one.
        height = _finite_scalar(entity.dxf.get("height"))
        thickness = _finite_scalar(entity.dxf.get("thickness", 0.0))
        insertion = _finite_point(entity.dxf.get("insert"))
    except (AttributeError, TypeError, ValueError):
        return TopologyEntityEvidence(entity_type="TEXT")
    try:
        alignment: TextEntityAlignment | None = entity.get_align_enum()
    except (AttributeError, TypeError, ValueError):
        alignment = None
    if not isinstance(alignment, TextEntityAlignment):
        alignment = None
    anchors: tuple[Point, ...] = ()
    if insertion is not None:
        try:
            _placement, primary_anchor, secondary_anchor = entity.get_placement()
            raw_anchors = tuple(
                point
                for point in (primary_anchor, secondary_anchor)
                if point is not None
            )
            parsed_anchors = tuple(_finite_point(point) for point in raw_anchors)
            if any(point is None for point in parsed_anchors):
                parsed_anchors = ()
            anchors = cast(tuple[Point, ...], parsed_anchors)
        except (AttributeError, TypeError, ValueError):
            anchors = ()
    projected_anchors = (
        _project_to_work_plane((insertion, *anchors), plane=insertion[2])
        if insertion is not None and anchors
        else None
    )
    plane_extrema = (
        _source_plane_extrema((insertion, *anchors))
        if insertion is not None and anchors
        else None
    )

    text_length = len(text)
    text_for_assessment = text if text_length <= MAX_TOKEN_LENGTH else None
    default_ocs = _default_xy_extrusion(entity)
    semantic_height = height is not None and height > _PLANE_TOLERANCE
    planar = (
        insertion is not None
        and rotation is not None
        and semantic_height
        and thickness is not None
        and abs(thickness) <= _PLANE_TOLERANCE
        and projected_anchors is not None
        and default_ocs
    )
    text_bounds: Aabb | None = None
    numeric_limit_exceeded = (
        (insertion is not None and not _points_within_numeric_policy((insertion,)))
        or any(not _points_within_numeric_policy((anchor,)) for anchor in anchors)
        or any(
            value is not None and not _within_numeric_policy(value)
            for value in (rotation, height, thickness)
        )
    )
    if planar and alignment is not None and insertion is not None:
        try:
            extents = bbox.extents([entity])
            minimum = _finite_point(extents.extmin) if extents.has_data else None
            maximum = _finite_point(extents.extmax) if extents.has_data else None
            if minimum is not None and maximum is not None and height is not None:
                numeric_limit_exceeded = numeric_limit_exceeded or not _points_within_derived_policy(
                    (minimum, maximum)
                )
                # ezdxf supplies WCS XY extents for every directly renderable
                # TEXT alignment.  Do not construct semantic bounds from a
                # zero-area, sub-tolerance, or ULP-collapsed renderer box.
                text_bounds = _reliable_text_bounds(
                    (minimum[0], minimum[1], insertion[2]),
                    (maximum[0], maximum[1], insertion[2]),
                    glyph_height=height,
                )
        except Exception:
            text_bounds = None
    return TopologyEntityEvidence(
        entity_type="TEXT",
        default_ocs=default_ocs,
        # Retain a known insertion plane even when bounds cannot be trusted;
        # the overlap gate can then block only candidates on that plane.
        plane_elevation=insertion[2] if insertion is not None and default_ocs else None,
        plane_min=plane_extrema[0] if plane_extrema is not None else None,
        plane_max=plane_extrema[1] if plane_extrema is not None else None,
        text=text_for_assessment,
        text_length=text_length,
        text_bounds=text_bounds,
        text_alignment=alignment.name.casefold() if alignment is not None else None,
        rotation=rotation,
        finite=planar and text_bounds is not None,
        numeric_limit_exceeded=numeric_limit_exceeded,
    )


def extract_topology_evidence(entity: Any, _dxfversion: str) -> TopologyEntityEvidence:
    """Capture only private geometry needed by the optional v2 branch.

    The snapshot module invokes this while it already owns the parsed,
    preflighted DXF.  Unsupported entities remain unsupported at the existing
    snapshot boundary; a supported but unsuitable role entity receives a
    non-finite evidence record and is later reported as insufficient evidence.
    """

    entity_type = str(entity.dxftype())
    if entity_type == "LINE":
        return _line_evidence(entity)
    if entity_type == "LWPOLYLINE":
        return _lwpolyline_evidence(entity)
    if entity_type == "TEXT":
        return _text_evidence(entity)
    return TopologyEntityEvidence(entity_type=entity_type)


def _profile_schema() -> dict[str, Any]:
    try:
        text = (
            resources.files("liang_pingfa_review.schemas")
            .joinpath("beam-topology-profile-v1.schema.json")
            .read_text(encoding="utf-8")
        )
        import json

        schema = json.loads(text)
        Draft202012Validator.check_schema(schema)
        return cast(dict[str, Any], schema)
    except (OSError, ModuleNotFoundError, ValueError, SchemaError) as error:
        raise PipelineError(
            ErrorCode.INTERNAL_ERROR, "topology profile schema unavailable"
        ) from error


def _normalize_layer_name(value: Any) -> str:
    if not isinstance(value, str):
        raise _profile_error()
    normalized = unicodedata.normalize("NFC", value).casefold()
    if (
        not normalized
        or len(normalized) > MAX_LAYER_NAME_LENGTH
        or normalized.strip() != normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise _profile_error()
    if normalized in {"temp", "textarea"}:
        raise _profile_error()
    return normalized


def _load_roles(loaded: Mapping[str, Any]) -> LayerRoles:
    layers = loaded.get("layers")
    if not isinstance(layers, Mapping) or set(layers) != set(_ROLES):
        raise _profile_error()
    normalized: dict[Role, frozenset[str]] = {}
    all_names: list[str] = []
    for role in _ROLES:
        values = layers.get(role)
        if not isinstance(values, list):
            raise _profile_error()
        names = [_normalize_layer_name(value) for value in values]
        if len(names) != len(set(names)):
            raise _profile_error()
        normalized[role] = frozenset(names)
        all_names.extend(names)
    if len(all_names) != len(set(all_names)):
        raise _profile_error()
    required = (
        "beam_edges",
        "beam_ids",
        "support_upper_annotations",
        "span_lower_annotations",
    )
    if any(not normalized[cast(Role, role)] for role in required):
        raise _profile_error()
    if not any(normalized[role] for role in _SUPPORT_ROLES):
        raise _profile_error()
    return LayerRoles(**normalized)


def _validate_runtime_profile(profile: TopologyProfile) -> None:
    """Defend the public in-process API from hand-built profile objects."""

    if not isinstance(profile, TopologyProfile) or not isinstance(
        profile.roles, LayerRoles
    ):
        raise _profile_error()
    all_names: list[str] = []
    for role in _ROLES:
        values = getattr(profile.roles, role, None)
        if not isinstance(values, frozenset):
            raise _profile_error()
        for value in values:
            if not isinstance(value, str) or _normalize_layer_name(value) != value:
                raise _profile_error()
        all_names.extend(values)
    if len(all_names) != len(set(all_names)):
        raise _profile_error()
    if (
        not profile.roles.beam_edges
        or not profile.roles.beam_ids
        or not profile.roles.support_upper_annotations
        or not profile.roles.span_lower_annotations
        or not any(getattr(profile.roles, role) for role in _SUPPORT_ROLES)
    ):
        raise _profile_error()


def topology_snapshot_context(profile: TopologyProfile) -> TopologySnapshotContext:
    """Return the validated immutable role boundary used by snapshotting."""

    _validate_runtime_profile(profile)
    return TopologySnapshotContext(
        roles=profile.roles,
        role_layers=frozenset(
            layer
            for role in _ROLES
            for layer in getattr(profile.roles, role)
        ),
    )


def load_topology_profile(path: Path) -> TopologyProfile:
    """Load one local, closed-world profile without leaking its pathname/data."""

    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_PROFILE_BYTES + 1)
        if len(raw) > MAX_PROFILE_BYTES:
            raise ValueError("profile too large")
        text = raw.decode("utf-8")

        def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate profile key")
                result[key] = value
            return result

        loaded = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite profile value")
            ),
        )
    except Exception as error:
        raise _profile_error() from error
    if not isinstance(loaded, Mapping):
        raise _profile_error()
    try:
        errors = sorted(
            Draft202012Validator(_profile_schema()).iter_errors(loaded),
            key=lambda item: list(item.path),
        )
    except (SchemaError, TypeError, ValueError) as error:
        raise _profile_error() from error
    if errors:
        raise _profile_error()
    if loaded.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise _profile_error()
    roles = _load_roles(loaded)
    return TopologyProfile(
        roles=roles,
        profile_fingerprint=canonical_sha256(
            {
                "schema_version": PROFILE_SCHEMA_VERSION,
                "layers": {
                    role: sorted(getattr(roles, role))
                    for role in _ROLES
                },
            }
        ),
    )


def _cross(first: tuple[float, float], second: tuple[float, float]) -> float:
    return _checked_derived(first[0] * second[1] - first[1] * second[0])


def _subtract(first: Point, second: Point) -> tuple[float, float]:
    return (
        _checked_derived(first[0] - second[0]),
        _checked_derived(first[1] - second[1]),
    )


def _dot(first: tuple[float, float], second: tuple[float, float]) -> float:
    return _checked_derived(first[0] * second[0] + first[1] * second[1])


def _canonical_unit(start: Point, end: Point) -> tuple[float, float] | None:
    dx, dy = _subtract(end, start)
    length = hypot(dx, dy)
    if not isfinite(length) or length <= _EPSILON:
        return None
    unit = (dx / length, dy / length)
    if unit[0] < -_EPSILON or (
        abs(unit[0]) <= _EPSILON and unit[1] < -_EPSILON
    ):
        return (-unit[0], -unit[1])
    return unit


def _same_plane(first: float | None, second: float | None) -> bool:
    return (
        first is not None
        and second is not None
        and _within_numeric_policy(first)
        and _within_numeric_policy(second)
        and abs(_checked_derived(first - second)) <= _PLANE_TOLERANCE
    )


def _participant_plane_extrema(participant: Any) -> tuple[float, float] | None:
    """Return unprojected source-plane extrema for one binding participant."""

    if isinstance(participant, TopologyEntityEvidence):
        minimum, maximum = participant.plane_min, participant.plane_max
    elif isinstance(participant, _Chain):
        extrema = [
            _participant_plane_extrema(item)
            for item in (*participant.axes, *(item.support for item in participant.supports))
        ]
        if any(item is None for item in extrema):
            return None
        typed = cast(list[tuple[float, float]], extrema)
        return min(item[0] for item in typed), max(item[1] for item in typed)
    elif isinstance(participant, _Axis):
        return _participant_plane_extrema((participant.first, participant.second))
    elif isinstance(participant, tuple):
        extrema = [_participant_plane_extrema(item) for item in participant]
        if not extrema or any(item is None for item in extrema):
            return None
        typed = cast(list[tuple[float, float]], extrema)
        return min(item[0] for item in typed), max(item[1] for item in typed)
    else:
        record = getattr(participant, "record", participant)
        evidence = _record_evidence(record)
        if evidence is None:
            return None
        return _participant_plane_extrema(evidence)
    if (
        minimum is None
        or maximum is None
        or not _within_numeric_policy(minimum)
        or not _within_numeric_policy(maximum)
        or minimum > maximum
    ):
        return None
    return minimum, maximum


def _binding_plane_envelope_is_valid(*participants: Any) -> bool:
    """Require one global source-elevation envelope before any binding use.

    Comparing every geometry to a baseline independently permits a
    ``-0.9e-6, 0, +0.9e-6`` chain.  Bindings instead consume the extrema of
    every participating axis, support, annotation, and leader at once.
    """

    extrema = [
        _participant_plane_extrema(participant)
        for participant in participants
        if not (isinstance(participant, tuple) and not participant)
    ]
    if not extrema or any(item is None for item in extrema):
        return False
    typed = cast(list[tuple[float, float]], extrema)
    return (
        _checked_derived(maximum := max(item[1] for item in typed))
        - _checked_derived(minimum := min(item[0] for item in typed))
        <= _PLANE_TOLERANCE
    )


def _strict_bounds_overlap(first: Aabb, second: Aabb) -> bool:
    """Require area overlap, not merely proximity or touching boundaries."""

    return (
        min(first.maximum[0], second.maximum[0])
        > max(first.minimum[0], second.minimum[0]) + _EPSILON
        and min(first.maximum[1], second.maximum[1])
        > max(first.minimum[1], second.minimum[1]) + _EPSILON
    )


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    vector = _subtract(end, start)
    relative = _subtract(point, start)
    length = _checked_derived(hypot(*vector))
    if length <= _EPSILON:
        return _distance(point, start) <= _EPSILON
    return (
        abs(_cross(vector, relative)) <= _EPSILON * max(1.0, length)
        and _dot(relative, _subtract(point, end)) <= _EPSILON
    )


def _orientation(first: Point, second: Point, third: Point) -> float:
    return _cross(_subtract(second, first), _subtract(third, first))


def _segments_intersect(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
) -> bool:
    """Exact-enough segment predicate for bounded finite double geometry."""

    first_a = _orientation(first_start, first_end, second_start)
    first_b = _orientation(first_start, first_end, second_end)
    second_a = _orientation(second_start, second_end, first_start)
    second_b = _orientation(second_start, second_end, first_end)
    if (
        ((first_a > _EPSILON and first_b < -_EPSILON)
         or (first_a < -_EPSILON and first_b > _EPSILON))
        and ((second_a > _EPSILON and second_b < -_EPSILON)
             or (second_a < -_EPSILON and second_b > _EPSILON))
    ):
        return True
    return any(
        (
            abs(value) <= _EPSILON
            and _point_on_segment(point, start, end)
        )
        for value, point, start, end in (
            (first_a, second_start, first_start, first_end),
            (first_b, second_end, first_start, first_end),
            (second_a, first_start, second_start, second_end),
            (second_b, first_end, second_start, second_end),
        )
    )


def _point_in_or_on_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Return inclusion for a simple convex support rectangle."""

    if len(polygon) != 4:
        return False
    if any(
        _point_on_segment(point, polygon[index], polygon[(index + 1) % 4])
        for index in range(4)
    ):
        return True
    signs: list[bool] = []
    for index in range(4):
        value = _orientation(
            polygon[index],
            polygon[(index + 1) % 4],
            point,
        )
        if abs(value) > _EPSILON:
            signs.append(value > 0.0)
    return bool(signs) and all(sign == signs[0] for sign in signs)


def _segment_intersects_polygon(
    start: Point, end: Point, polygon: Sequence[Point]
) -> bool:
    if _point_in_or_on_polygon(start, polygon) or _point_in_or_on_polygon(end, polygon):
        return True
    return any(
        _segments_intersect(start, end, polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    )


def _polygons_intersect(
    first: Sequence[Point],
    second: Sequence[Point],
) -> bool:
    """Return whether closed private polygons touch or overlap."""

    if not first or not second:
        return False
    if any(_point_in_or_on_polygon(point, second) for point in first):
        return True
    if any(_point_in_or_on_polygon(point, first) for point in second):
        return True
    return any(
        _segments_intersect(
            first[index],
            first[(index + 1) % len(first)],
            second[other_index],
            second[(other_index + 1) % len(second)],
        )
        for index in range(len(first))
        for other_index in range(len(second))
    )


def _point_is_within_segment_tolerance(
    point: Point,
    start: Point,
    end: Point,
    tolerance: float = _EPSILON,
) -> bool:
    """Test a private point/segment relation without broad-phase guessing."""

    dx = _checked_derived(end[0] - start[0])
    dy = _checked_derived(end[1] - start[1])
    length_squared = _checked_derived(dx * dx + dy * dy)
    if length_squared <= _EPSILON:
        nearest_x, nearest_y = start[0], start[1]
    else:
        projection = _checked_derived(
            (
                _checked_derived((point[0] - start[0]) * dx)
                + _checked_derived((point[1] - start[1]) * dy)
            )
            / length_squared
        )
        clamped = min(1.0, max(0.0, projection))
        nearest_x = _checked_derived(start[0] + clamped * dx)
        nearest_y = _checked_derived(start[1] + clamped * dy)
    distance_squared = _checked_derived(
        _checked_derived(point[0] - nearest_x) ** 2
        + _checked_derived(point[1] - nearest_y) ** 2
    )
    return distance_squared <= _checked_derived(tolerance * tolerance)


def _segments_related_within_tolerance(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
) -> bool:
    """Treat intersection, touch, and epsilon-nearness as one conflict."""

    return _segments_intersect(
        first_start,
        first_end,
        second_start,
        second_end,
    ) or any(
        _point_is_within_segment_tolerance(point, start, end)
        for point, start, end in (
            (first_start, second_start, second_end),
            (first_end, second_start, second_end),
            (second_start, first_start, first_end),
            (second_end, first_start, first_end),
        )
    )


def _polygons_related_within_tolerance(
    first: Sequence[Point],
    second: Sequence[Point],
) -> bool:
    """Return whether private polygons overlap, touch, or are epsilon-near."""

    return _polygons_intersect(first, second) or any(
        _segments_related_within_tolerance(
            first[index],
            first[(index + 1) % len(first)],
            second[other_index],
            second[(other_index + 1) % len(second)],
        )
        for index in range(len(first))
        for other_index in range(len(second))
    )


def _segment_intersects_aabb(start: Point, end: Point, bounds: Aabb) -> bool:
    if (
        bounds.minimum[0] - _EPSILON <= start[0] <= bounds.maximum[0] + _EPSILON
        and bounds.minimum[1] - _EPSILON <= start[1] <= bounds.maximum[1] + _EPSILON
    ) or (
        bounds.minimum[0] - _EPSILON <= end[0] <= bounds.maximum[0] + _EPSILON
        and bounds.minimum[1] - _EPSILON <= end[1] <= bounds.maximum[1] + _EPSILON
    ):
        return True
    corners = bounds.corners()
    return any(
        _segments_intersect(start, end, corners[index], corners[(index + 1) % 4])
        for index in range(4)
    )


def _segment_polygon_interval(
    start: Point,
    end: Point,
    polygon: Sequence[Point],
    unit: tuple[float, float],
) -> tuple[float, float] | None:
    """Clip a segment to a rectangular support and return global s bounds."""

    direction = _subtract(end, start)
    length_squared = _dot(direction, direction)
    if length_squared <= _EPSILON:
        return None
    candidates: list[Point] = []
    if _point_in_or_on_polygon(start, polygon):
        candidates.append(start)
    if _point_in_or_on_polygon(end, polygon):
        candidates.append(end)
    for index in range(len(polygon)):
        edge_start = polygon[index]
        edge_end = polygon[(index + 1) % len(polygon)]
        edge = _subtract(edge_end, edge_start)
        denominator = _cross(direction, edge)
        relative = _subtract(edge_start, start)
        if abs(denominator) <= _EPSILON:
            # A beam centreline coincident with a support boundary has no
            # unique clip interval.  Do not infer an outer face.
            if abs(_cross(relative, direction)) <= _EPSILON:
                return None
            continue
        segment_t = _checked_derived(_cross(relative, edge) / denominator)
        edge_t = _checked_derived(_cross(relative, direction) / denominator)
        if -_EPSILON <= segment_t <= 1.0 + _EPSILON and -_EPSILON <= edge_t <= 1.0 + _EPSILON:
            candidates.append(
                (
                    start[0] + direction[0] * segment_t,
                    start[1] + direction[1] * segment_t,
                    start[2],
                )
            )
    if len(candidates) < 2:
        return None
    projected = [
        point[0] * unit[0] + point[1] * unit[1]
        for point in candidates
    ]
    low, high = min(projected), max(projected)
    return (low, high) if high - low > _EPSILON else None


def _line_polygon_interval(
    polygon: Sequence[Point],
    *,
    unit: tuple[float, float],
    normal: tuple[float, float],
    radial: float,
) -> tuple[float, float] | None:
    """Clip an infinite canonical centreline through a support rectangle."""

    intersections: list[Point] = []
    for index in range(len(polygon)):
        start = polygon[index]
        end = polygon[(index + 1) % len(polygon)]
        start_r = _dot((start[0], start[1]), normal) - radial
        end_r = _dot((end[0], end[1]), normal) - radial
        if abs(start_r) <= _EPSILON and abs(end_r) <= _EPSILON:
            # A support edge cannot be treated as an arbitrary centreline
            # face: it produces no unique cross-section.
            return None
        if abs(start_r) <= _EPSILON:
            intersections.append(start)
            continue
        if abs(end_r) <= _EPSILON:
            intersections.append(end)
            continue
        if (start_r > 0.0) == (end_r > 0.0):
            continue
        fraction = _checked_derived(start_r / _checked_derived(start_r - end_r))
        intersections.append(
            (
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
                start[2] + (end[2] - start[2]) * fraction,
            )
        )
    if len(intersections) < 2:
        return None
    values = [_dot((point[0], point[1]), unit) for point in intersections]
    low, high = min(values), max(values)
    return (low, high) if high - low > _EPSILON else None


class _UniformGrid:
    """Deterministic uniform-grid broad phase with a global reference cap."""

    def __init__(self, cell_size: float) -> None:
        if not isfinite(cell_size) or cell_size <= _EPSILON:
            raise _limit_error()
        self._cell_size = cell_size
        self._cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._references = 0
        self._query_references = 0

    def _cell_range(self, bounds: Aabb) -> tuple[range, range]:
        try:
            normalized = (
                bounds.minimum[0] / self._cell_size,
                bounds.maximum[0] / self._cell_size,
                bounds.minimum[1] / self._cell_size,
                bounds.maximum[1] / self._cell_size,
            )
            if any(
                not isfinite(value)
                or abs(value) > MAX_NORMALIZED_COORDINATE_MAGNITUDE
                for value in normalized
            ):
                raise _limit_error()
            start_x, end_x, start_y, end_y = (floor(value) for value in normalized)
        except (OverflowError, ValueError):
            raise _limit_error()
        if (
            end_x < start_x
            or end_y < start_y
            or end_x - start_x + 1 > MAX_GRID_REFERENCES
            or end_y - start_y + 1 > MAX_GRID_REFERENCES
        ):
            raise _limit_error()
        return range(start_x, end_x + 1), range(start_y, end_y + 1)

    def insert(self, index: int, bounds: Aabb) -> None:
        x_range, y_range = self._cell_range(bounds)
        for x in x_range:
            for y in y_range:
                self._references += 1
                if self._references > MAX_GRID_REFERENCES:
                    raise _limit_error()
                self._cells[(x, y)].append(index)

    def query(self, bounds: Aabb) -> tuple[int, ...]:
        """Return deterministic nearby indexes while bounding lookup work."""

        x_range, y_range = self._cell_range(bounds)
        members: set[int] = set()
        for x in x_range:
            for y in y_range:
                self._query_references += 1
                if self._query_references > MAX_GRID_REFERENCES:
                    raise _limit_error()
                members.update(self._cells.get((x, y), ()))
        return tuple(sorted(members))

    def candidate_pairs(self) -> tuple[tuple[int, int], ...]:
        pairs: set[tuple[int, int]] = set()
        for key in sorted(self._cells):
            members = sorted(set(self._cells[key]))
            for first_index, first in enumerate(members):
                for second in members[first_index + 1 :]:
                    pairs.add((first, second))
                    if len(pairs) > MAX_EDGE_PAIRS:
                        raise _limit_error()
        return tuple(sorted(pairs))


class _SpatialIndex:
    """A bounded broad-phase index for private finite AABBs.

    The index never decides a topology relation.  It only supplies nearby
    records for a caller that will charge the shared relation budget before
    its exact predicate.
    """

    def __init__(
        self,
        items: Sequence[Any],
        bounds: Sequence[Aabb],
        *,
        cell_size: float,
    ) -> None:
        if len(items) != len(bounds):
            raise _limit_error()
        self._items = tuple(items)
        self._bounds = tuple(bounds)
        self._grid = _UniformGrid(cell_size)
        for index, item_bounds in enumerate(self._bounds):
            self._grid.insert(index, item_bounds)

    def query(self, bounds: Aabb) -> tuple[Any, ...]:
        return tuple(
            self._items[index]
            for index in self._grid.query(bounds)
            if self._bounds[index].overlaps(bounds)
        )


class _IntervalIndex:
    """A deterministic interval broad phase for one chain projection."""

    def __init__(self, entries: Iterable[tuple[float, float, Any]]) -> None:
        normalized: list[tuple[float, float, Any]] = []
        for low, high, value in entries:
            if (
                not _within_numeric_policy(low, derived=True)
                or not _within_numeric_policy(high, derived=True)
                or high < low
            ):
                raise _limit_error()
            normalized.append((low, high, value))
        # Python's stable sort preserves the deterministic input order where
        # intervals have identical bounds without comparing private values.
        normalized.sort(key=lambda item: (item[0], item[1]))
        if len(normalized) > MAX_GRID_REFERENCES:
            raise _limit_error()
        self._entries = tuple(normalized)
        self._lows = tuple(item[0] for item in self._entries)

    def query(self, low: float, high: float) -> tuple[Any, ...]:
        if (
            not _within_numeric_policy(low, derived=True)
            or not _within_numeric_policy(high, derived=True)
            or high < low
        ):
            raise _limit_error()
        stop = bisect_right(self._lows, high + _EPSILON)
        return tuple(
            value
            for entry_low, entry_high, value in self._entries[:stop]
            if entry_high >= low - _EPSILON
        )


def _record_visible(record: Any) -> bool:
    try:
        return bool(record.visible)
    except (AttributeError, TypeError, ValueError):
        return False


def _record_evidence(record: Any) -> TopologyEntityEvidence | None:
    evidence = getattr(record, "topology_evidence", None)
    return evidence if isinstance(evidence, TopologyEntityEvidence) else None


def _record_layer(record: Any) -> str | None:
    try:
        value = unicodedata.normalize("NFC", str(record.layer_name)).casefold()
    except (AttributeError, TypeError, ValueError):
        return None
    return value or None


def _semantic_role_record(record: Any) -> bool:
    """Return whether a record may enter the topology-only evidence branch.

    Snapshots intentionally retain every supported entity, including paper
    space and reusable block content, so preservation manifests can bind the
    complete source.  Topology is narrower: it reasons only about direct,
    displayable Modelspace graphics.  Apply this boundary before role caps,
    traces, overlap inventory, and structural validation so excluded records
    cannot consume a budget or turn a visible topology into an insufficiency.
    """

    return bool(
        getattr(record, "layout", None) == "modelspace"
        and _record_visible(record)
    )


def _usable_role_record(record: Any, evidence: TopologyEntityEvidence | None) -> bool:
    return bool(
        _semantic_role_record(record)
        and evidence is not None
        and evidence.finite
        and evidence.default_ocs
        and evidence.plane_elevation is not None
        and _participant_plane_extrema(evidence) is not None
    )


def _segment_for_role(record: Any) -> _Segment | None:
    evidence = _record_evidence(record)
    if not _usable_role_record(record, evidence) or evidence is None:
        return None
    if evidence.entity_type == "LINE":
        vertices = evidence.vertices
    elif (
        evidence.entity_type == "LWPOLYLINE"
        and not evidence.closed
        and evidence.widths_zero
        and evidence.bulges_zero
        and len(evidence.vertices) == 2
    ):
        vertices = evidence.vertices
    else:
        return None
    if (
        len(vertices) != 2
        or evidence.plane_elevation is None
        or not _binding_plane_envelope_is_valid(evidence)
    ):
        return None
    projected = _project_to_work_plane(vertices, plane=evidence.plane_elevation)
    if projected is None:
        return None
    unit = _canonical_unit(projected[0], projected[1])
    if unit is None:
        return None
    length = _distance(projected[0], projected[1])
    if length <= _EPSILON:
        return None
    return _Segment(
        record=record,
        start=projected[0],
        end=projected[1],
        length=length,
        unit=unit,
        bounds=_bounds_from_points(projected),
        plane=projected[0][2],
    )


def _is_rectangle(vertices: Sequence[Point]) -> bool:
    if len(vertices) != 4:
        return False
    if len({(point[0], point[1]) for point in vertices}) != 4:
        return False
    vectors = [
        _subtract(vertices[(index + 1) % 4], vertices[index])
        for index in range(4)
    ]
    lengths = [_checked_derived(hypot(*vector)) for vector in vectors]
    if any(length <= _EPSILON for length in lengths):
        return False
    # Four straight sides, orthogonal neighbours, and parallel opposite sides
    # are required.  The tolerance is scale-free here because source vertices
    # are already finite and the final topology uses scale-relative margins.
    return (
        all(
            abs(_dot(vectors[index], vectors[(index + 1) % 4]))
            <= _EPSILON * max(1.0, lengths[index] * lengths[(index + 1) % 4])
            for index in range(4)
        )
        and abs(_cross(vectors[0], vectors[2]))
        <= _EPSILON * max(1.0, lengths[0] * lengths[2])
        and abs(_cross(vectors[1], vectors[3]))
        <= _EPSILON * max(1.0, lengths[1] * lengths[3])
    )


def _support_for_record(record: Any, role: Role) -> _Support | None:
    evidence = _record_evidence(record)
    if not _usable_role_record(record, evidence) or evidence is None:
        return None
    if (
        evidence.entity_type != "LWPOLYLINE"
        or not evidence.closed
        or not evidence.widths_zero
        or not evidence.bulges_zero
        or len(evidence.vertices) != 4
        or evidence.plane_elevation is None
        or not _is_rectangle(evidence.vertices)
        or not _binding_plane_envelope_is_valid(evidence)
    ):
        return None
    projected = _project_to_work_plane(
        evidence.vertices,
        plane=evidence.plane_elevation,
    )
    if projected is None or not _is_rectangle(projected):
        return None
    return _Support(
        record=record,
        role=role,
        vertices=cast(tuple[Point, Point, Point, Point], projected),
        bounds=_bounds_from_points(projected),
        plane=evidence.plane_elevation,
        # A rectangle's start vertex and winding are drawing representation,
        # not topology semantics.  IDs below use this canonical private key.
        geometry_fingerprint=canonical_sha256(
            {
                "rectangle_vertices": sorted(
                    tuple(point) for point in projected
                )
            }
        ),
    )


def _text_candidate(record: Any, role: Role) -> _TextCandidate | None:
    evidence = _record_evidence(record)
    if not _usable_role_record(record, evidence) or evidence is None:
        return None
    if (
        evidence.entity_type != "TEXT"
        or not evidence.finite
        or evidence.text_bounds is None
        or evidence.text_alignment != "left"
        or evidence.rotation is None
    ):
        return None
    return _TextCandidate(
        record=record,
        role=role,
        bounds=evidence.text_bounds,
        evidence=evidence,
    )


def _visible_text_overlap_entry(
    record: Any,
    role: Role,
) -> _TextOverlapEntry | None:
    """Inventory visible controlled TEXT before semantic eligibility checks."""

    if (
        role not in {"beam_ids", *tuple(_ANNOTATION_ROLES)}
        or getattr(record, "layout", None) != "modelspace"
        or not _record_visible(record)
        or getattr(record, "entity_type", None) != "TEXT"
    ):
        return None
    evidence = _record_evidence(record)
    if evidence is None or evidence.entity_type != "TEXT":
        return _TextOverlapEntry(
            record=record,
            role=role,
            bounds=None,
            plane=None,
        )
    return _TextOverlapEntry(
        record=record,
        role=role,
        bounds=evidence.text_bounds,
        plane=evidence.plane_elevation,
    )


def _leader_for_record(record: Any) -> _Leader | None:
    segment = _segment_for_role(record)
    if segment is None:
        return None
    return _Leader(
        record=record,
        start=segment.start,
        end=segment.end,
        bounds=segment.bounds,
    )


def _parse_token(candidate: _TextCandidate) -> str | None:
    """Return a normalized token for private, in-memory equality only."""

    text = candidate.evidence.text
    if (
        text is None
        or candidate.evidence.text_length <= 0
        or candidate.evidence.text_length > MAX_TOKEN_LENGTH
        or "\n" in text
        or "\r" in text
    ):
        return None
    normalized = unicodedata.normalize("NFC", text)
    if normalized != text or not _TOKEN_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _opaque_identifier(prefix: str, value: Mapping[str, Any]) -> str:
    """Create a private-only identifier for intermediate geometry."""

    return f"{prefix}-{canonical_sha256(dict(value))[:24]}"


def _finding(
    *,
    category: Literal[
        "support_upper_annotation",
        "span_lower_annotation",
        "topology",
    ],
    status: Literal["一致", "疑似不一致", "证据不足"],
    identity: str,
) -> dict[str, Any]:
    """Create an intentionally generic non-actionable topology finding."""

    if status == "一致":
        evidence = "已建立唯一拓扑位置"
        reasoning = "角色、文本边界和唯一拓扑目标一致"
        unreadable = "无"
        next_step = "保持只读结论"
    elif status == "疑似不一致":
        evidence = "位置与已建立拓扑不相容"
        reasoning = "角色、文本边界或引出线与唯一拓扑目标不相容"
        unreadable = "无"
        next_step = "保持只读结论"
    else:
        evidence = "拓扑或标注证据不足"
        reasoning = "不以图层、距离或最近对象推断拓扑归属"
        unreadable = "拓扑或标注位置证据"
        next_step = "补充完整可读视图后重新审计"
    position = {
        "support_upper_annotation": "支座上部原位注写",
        "span_lower_annotation": "跨中下部原位注写",
        "topology": "梁图拓扑",
    }[category]
    return {
        "category": category,
        "status": status,
        "object_position": position,
        "field": "原位注写语义位置",
        "visible_evidence": evidence,
        "reasoning": reasoning,
        "source_topics": ["平面与截面表达、集中与原位作用域"],
        "unreadable_parts": unreadable,
        "next_step": next_step,
        "actionability": False,
        "target_id": None,
        # Kept only during private assessment so the final artifact can bind
        # this conclusion to manifest-resolvable opaque traces.  It is
        # removed before serialization; public findings never expose source
        # fingerprints beyond their own opaque finding ID.
        "_trace_identity": identity,
    }


@dataclass
class _TraceBook:
    """Private builder for strict, manifest-resolvable topology traces."""

    traces: dict[str, dict[str, Any]] = field(default_factory=dict)
    axis_label_handles: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )

    def add(
        self,
        record: Any,
        role: str,
    ) -> None:
        handle = str(record.handle)
        self.traces[handle] = {
            # Render recomputes this after any conservative ambiguity
            # reclassification, so role changes cannot retain an old ID.
            "trace_id": "",
            "entity_handle": handle,
            "identity_fingerprint": record.identity_fingerprint,
            "content_fingerprint": record.content_fingerprint,
            "role": role,
            # This exposes only whether a local token equality gate was
            # established.  It is not a value, token class, or token-derived
            # deterministic fingerprint.
            "token_equality_established": False,
            "chain_id": None,
            "support_id": None,
            "span_id": None,
            "target_provenance_id": None,
        }

    def bind(
        self,
        record: Any,
        *,
        chain_id: str | None = None,
        support_id: str | None = None,
        span_id: str | None = None,
        token_equality_established: bool | None = None,
    ) -> None:
        trace = self.traces.get(str(record.handle))
        if trace is None:
            return
        if token_equality_established is not None:
            trace["token_equality_established"] = token_equality_established
        if chain_id is not None:
            trace["chain_id"] = chain_id
        if support_id is not None:
            trace["support_id"] = support_id
        if span_id is not None:
            trace["span_id"] = span_id
        if (
            trace["role"] in _ANNOTATION_ROLES
            and chain_id is not None
            and (support_id is not None or span_id is not None)
        ):
            trace_id = derive_trace_id(
                str(trace["identity_fingerprint"]),
                str(trace["content_fingerprint"]),
                str(trace["role"]),
            )
            trace["target_provenance_id"] = derive_annotation_target_provenance_id(
                trace_id,
                chain_id,
                support_id,
                span_id,
            )

    def bind_axis_label(self, record: Any, axis_id: str) -> None:
        """Retain the private axis relation until its chain is admitted."""

        if str(record.handle) in self.traces:
            self.axis_label_handles[axis_id].add(str(record.handle))

    def bind_axis_labels_to_chain(self, axis_id: str, chain_id: str) -> None:
        """Publish an ID label's chain only after exact chain admission."""

        for handle in self.axis_label_handles.get(axis_id, ()):
            trace = self.traces.get(handle)
            if trace is not None:
                trace["chain_id"] = chain_id

    def mark_all_ambiguous(self) -> None:
        """Revoke every partial target when the whole topology is incomplete.

        A soft construction failure leaves no trustworthy owner tuple.  The
        source records remain useful, visible evidence of that failure, but
        must serialize as target-free ambiguity rather than as partial beam,
        support, or annotation provenance.
        """

        for trace in self.traces.values():
            trace["role"] = "ambiguity"
            trace["chain_id"] = None
            trace["support_id"] = None
            trace["span_id"] = None
            trace["target_provenance_id"] = None

    def render(self) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        for handle in sorted(self.traces, key=lambda value: int(value, 16)):
            trace = dict(self.traces[handle])
            # A source annotation that did not establish the exact required
            # target remains useful as opaque ambiguity evidence, but must not
            # masquerade as a support/span annotation binding.  This preserves
            # the source record's privacy while making the public role tuple
            # unambiguous: concrete annotation roles always carry exactly one
            # owning chain-and-support/span relation.
            if trace["role"] == "support_upper_annotations" and not (
                trace["chain_id"] is not None
                and trace["support_id"] is not None
                and trace["span_id"] is None
            ):
                trace["role"] = "ambiguity"
            elif trace["role"] == "span_lower_annotations" and not (
                trace["chain_id"] is not None
                and trace["support_id"] is None
                and trace["span_id"] is not None
            ):
                trace["role"] = "ambiguity"
            elif trace["role"] == "beam_ids" and trace["chain_id"] is None:
                # A beam-ID label is concrete evidence only when the exact
                # labelled axis survives admission into one chain.
                trace["role"] = "ambiguity"
            elif trace["role"] == "support_geometry" and not (
                trace["chain_id"] is not None
                and trace["support_id"] is not None
                and trace["span_id"] is None
            ):
                # A support source is canonical public support evidence only
                # after it owns exactly one registered chain/support tuple.
                # Shared or unadmitted source geometry remains ambiguity
                # rather than becoming an orphaned registry provenance.
                trace["role"] = "ambiguity"
            if trace["role"] == "ambiguity":
                # Ambiguity never carries an owner target.  A consumer must
                # not read an unproven partial relation as a valid binding.
                trace["chain_id"] = None
                trace["support_id"] = None
                trace["span_id"] = None
                trace["target_provenance_id"] = None
            trace["trace_id"] = derive_trace_id(
                str(trace["identity_fingerprint"]),
                str(trace["content_fingerprint"]),
                str(trace["role"]),
            )
            rendered.append(trace)
        return rendered


def _complete_incomplete_topology_findings(
    findings: list[dict[str, Any]],
    traces: _TraceBook,
) -> None:
    """Bind one insufficiency finding to every controlled incomplete record.

    Soft failures (as distinct from hard policy-limit failures) publish an
    artifact.  Every visible configured record that entered that artifact is
    affected: there is no complete topology from which a target could be
    inferred.  Reclassifying all traces to ambiguity both prevents a forged
    partial target tuple and makes each deterministic finding compatible with
    the public role/status matrix.
    """

    existing_identities = {
        str(finding.get("_trace_identity", ""))
        for finding in findings
        if finding.get("status") == "证据不足"
    }
    for trace in traces.traces.values():
        identity = str(trace["identity_fingerprint"])
        if identity in existing_identities:
            continue
        raw_role = str(trace["role"])
        category: Literal[
            "support_upper_annotation",
            "span_lower_annotation",
            "topology",
        ]
        if raw_role == "support_upper_annotations":
            category = "support_upper_annotation"
        elif raw_role == "span_lower_annotations":
            category = "span_lower_annotation"
        else:
            category = "topology"
        findings.append(
            _finding(
                category=category,
                status="证据不足",
                identity=identity,
            )
        )
        existing_identities.add(identity)
    traces.mark_all_ambiguous()


def _stable_scale(segments: Sequence[_Segment]) -> float | None:
    values = sorted(segment.length for segment in segments if segment.length > _EPSILON)
    if not values or not all(isfinite(value) for value in values):
        return None
    if _checked_derived(values[-1] / values[0]) > _SCALE_RATIO_LIMIT:
        return None
    midpoint = len(values) // 2
    scale = (
        values[midpoint]
        if len(values) % 2
        else _checked_derived((values[midpoint - 1] + values[midpoint]) / 2.0)
    )
    return scale if isfinite(scale) and scale > _EPSILON else None


def _axis_candidate(
    first: _Segment,
    second: _Segment,
    *,
    scale: float,
) -> _Axis | None:
    if (
        not _binding_plane_envelope_is_valid(first, second)
        or not _same_plane(first.plane, second.plane)
    ):
        return None
    if abs(_cross(first.unit, second.unit)) > _PARALLEL_SINE_LIMIT:
        return None
    unit = first.unit
    normal = (-unit[1], unit[0])
    first_start = _dot((first.start[0], first.start[1]), unit)
    first_end = _dot((first.end[0], first.end[1]), unit)
    second_start = _dot((second.start[0], second.start[1]), unit)
    second_end = _dot((second.end[0], second.end[1]), unit)
    first_low, first_high = min(first_start, first_end), max(first_start, first_end)
    second_low, second_high = min(second_start, second_end), max(second_start, second_end)
    overlap_low, overlap_high = max(first_low, second_low), min(first_high, second_high)
    endpoint_tolerance = scale * _ENDPOINT_RATIO
    if (
        overlap_high - overlap_low <= endpoint_tolerance
        or abs(first_low - second_low) > endpoint_tolerance
        or abs(first_high - second_high) > endpoint_tolerance
    ):
        return None
    first_radial_values = [
        _dot((point[0], point[1]), normal) for point in (first.start, first.end)
    ]
    second_radial_values = [
        _dot((point[0], point[1]), normal) for point in (second.start, second.end)
    ]
    if (
        max(first_radial_values) - min(first_radial_values) > endpoint_tolerance
        or max(second_radial_values) - min(second_radial_values) > endpoint_tolerance
    ):
        return None
    first_radial = _checked_derived(
        (first_radial_values[0] + first_radial_values[1]) / 2.0
    )
    second_radial = _checked_derived(
        (second_radial_values[0] + second_radial_values[1]) / 2.0
    )
    width = abs(_checked_derived(first_radial - second_radial))
    if not (_MIN_WIDTH_RATIO * scale <= width <= _MAX_WIDTH_RATIO * scale):
        return None
    radial = _checked_derived((first_radial + second_radial) / 2.0)
    plane = first.plane
    start = (
        _checked_derived(unit[0] * overlap_low + normal[0] * radial),
        _checked_derived(unit[1] * overlap_low + normal[1] * radial),
        plane,
    )
    end = (
        _checked_derived(unit[0] * overlap_high + normal[0] * radial),
        _checked_derived(unit[1] * overlap_high + normal[1] * radial),
        plane,
    )
    return _Axis(
        first=first,
        second=second,
        start=start,
        end=end,
        unit=unit,
        normal=normal,
        radial=radial,
        width=width,
        plane=plane,
        identifier=_opaque_identifier(
            "axis",
            {
                "edges": sorted(
                    (
                        first.record.identity_fingerprint,
                        second.record.identity_fingerprint,
                    )
                )
            },
        ),
    )


def _build_axes(
    segments: Sequence[_Segment],
    scale: float,
    *,
    budget: RelationBudget,
) -> _AxisDiscovery:
    """Pair edges only when each has one mutual mate; retain every other edge."""

    ordered_segments = tuple(
        sorted(
            segments,
            key=lambda segment: (
                segment.plane,
                segment.bounds.minimum[0],
                segment.bounds.minimum[1],
                segment.bounds.maximum[0],
                segment.bounds.maximum[1],
                segment.length,
                segment.unit,
                segment.record.identity_fingerprint,
            ),
        )
    )
    grid = _UniformGrid(scale)
    for index, segment in enumerate(ordered_segments):
        grid.insert(index, segment.bounds.expanded(_MAX_WIDTH_RATIO * scale))
    candidates: list[tuple[int, int, _Axis]] = []
    for first_index, second_index in grid.candidate_pairs():
        # Pairing is an exact topology predicate, even when it fails because
        # the two nearby lines differ in plane, extent, or width.
        budget.charge()
        candidate = _axis_candidate(
            ordered_segments[first_index],
            ordered_segments[second_index],
            scale=scale,
        )
        if candidate is not None:
            candidates.append((first_index, second_index, candidate))
            if len(candidates) > MAX_EDGE_PAIRS:
                raise _limit_error()
    partners: dict[int, list[tuple[int, _Axis]]] = defaultdict(list)
    for first, second, axis in candidates:
        partners[first].append((second, axis))
        partners[second].append((first, axis))
    paired_indexes: set[int] = set()
    axes: list[_Axis] = []
    for first, second, axis in candidates:
        if len(partners[first]) == 1 and len(partners[second]) == 1:
            paired_indexes.update((first, second))
            axes.append(axis)
    unresolved = [
        _UnresolvedBeamGeometry(
            segment=segment,
            provenance=segment.record.identity_fingerprint,
            bounds=segment.bounds,
            reason="ambiguous" if partners[index] else "unpaired",
        )
        for index, segment in enumerate(ordered_segments)
        if index not in paired_indexes
    ]
    if len(axes) > MAX_AXES:
        raise _limit_error()
    return _AxisDiscovery(
        axes=tuple(
            sorted(
                axes,
                key=lambda axis: (
                    axis.plane,
                    axis.start[0],
                    axis.start[1],
                    axis.end[0],
                    axis.end[1],
                    axis.identifier,
                ),
            )
        ),
        unresolved=tuple(
            sorted(
                unresolved,
                key=lambda item: (
                    item.segment.plane,
                    item.bounds.minimum[0],
                    item.bounds.minimum[1],
                    item.bounds.maximum[0],
                    item.bounds.maximum[1],
                    item.provenance,
                ),
            )
        ),
    )


def _bounds_in_axis_id_corridor(candidate: _TextCandidate, axis: _Axis) -> bool:
    if not _same_plane(candidate.bounds.plane, axis.plane):
        return False
    values_s = [axis.global_s(point) for point in candidate.bounds.corners()]
    values_r = [axis.global_r(point) - axis.radial for point in candidate.bounds.corners()]
    start = axis.global_s(axis.start)
    end = axis.global_s(axis.end)
    edge_margin = axis.length * _ENDPOINT_RATIO
    return (
        min(values_s) >= min(start, end) - edge_margin
        and max(values_s) <= max(start, end) + edge_margin
        and min(values_r) >= -_ID_CORRIDOR_WIDTHS * axis.width
        and max(values_r) <= _ID_CORRIDOR_WIDTHS * axis.width
    )


def _projected_rectangle_bounds(
    *,
    unit: tuple[float, float],
    normal: tuple[float, float],
    radial: float,
    plane: float,
    low: float,
    high: float,
    r_low: float,
    r_high: float,
) -> Aabb:
    """Return an AABB broad phase for one private oriented corridor."""

    return _bounds_from_points(
        _projected_rectangle_polygon(
            unit=unit,
            normal=normal,
            radial=radial,
            plane=plane,
            low=low,
            high=high,
            r_low=r_low,
            r_high=r_high,
        )
    )


def _projected_rectangle_polygon(
    *,
    unit: tuple[float, float],
    normal: tuple[float, float],
    radial: float,
    plane: float,
    low: float,
    high: float,
    r_low: float,
    r_high: float,
) -> tuple[Point, Point, Point, Point]:
    """Build one private oriented rectangle in perimeter order."""

    return (
        _projected_point(
            unit=unit, normal=normal, radial=radial, plane=plane, s=low, r=r_low
        ),
        _projected_point(
            unit=unit, normal=normal, radial=radial, plane=plane, s=high, r=r_low
        ),
        _projected_point(
            unit=unit, normal=normal, radial=radial, plane=plane, s=high, r=r_high
        ),
        _projected_point(
            unit=unit, normal=normal, radial=radial, plane=plane, s=low, r=r_high
        ),
    )


def _projected_point(
    *,
    unit: tuple[float, float],
    normal: tuple[float, float],
    radial: float,
    plane: float,
    s: float,
    r: float,
) -> Point:
    """Build and validate one finite WCS point from topology coordinates."""

    radial_coordinate = _checked_derived(radial + r)
    point = (
        _checked_derived(unit[0] * s + normal[0] * radial_coordinate),
        _checked_derived(unit[1] * s + normal[1] * radial_coordinate),
        plane,
    )
    if not _points_within_derived_policy((point,)):
        raise _limit_error()
    return point


def _axis_id_corridor_bounds(axis: _Axis) -> Aabb:
    start = axis.global_s(axis.start)
    end = axis.global_s(axis.end)
    margin = axis.length * _ENDPOINT_RATIO
    return _projected_rectangle_bounds(
        unit=axis.unit,
        normal=axis.normal,
        radial=axis.radial,
        plane=axis.plane,
        low=min(start, end) - margin,
        high=max(start, end) + margin,
        r_low=-_ID_CORRIDOR_WIDTHS * axis.width,
        r_high=_ID_CORRIDOR_WIDTHS * axis.width,
    )


def _bind_axis_ids(
    axes: Sequence[_Axis],
    candidates: Sequence[_TextCandidate],
    blocked_handles: frozenset[str],
    traces: _TraceBook,
    findings: list[dict[str, Any]],
    *,
    axis_index: _SpatialIndex,
    budget: RelationBudget,
) -> dict[str, str]:
    """Bind private beam-ID equality classes into unique full-bound corridors."""

    labels_by_axis: dict[str, list[tuple[_TextCandidate, str]]] = defaultdict(list)
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.bounds.plane,
            item.bounds.minimum[0],
            item.bounds.minimum[1],
            item.bounds.maximum[0],
            item.bounds.maximum[1],
            item.role,
            item.record.identity_fingerprint,
        ),
    ):
        if str(candidate.record.handle) in blocked_handles:
            continue
        parsed = _parse_token(candidate)
        if parsed is None:
            findings.append(
                _finding(
                    category="topology",
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        # A boolean confirms that a private local equality value was accepted;
        # the value itself and any token-only fingerprint never leave memory.
        traces.bind(candidate.record, token_equality_established=True)
        matches: list[_Axis] = []
        for axis in axis_index.query(candidate.bounds):
            if (
                not isinstance(axis, _Axis)
                or not _binding_plane_envelope_is_valid(candidate, axis)
                or not _same_plane(candidate.bounds.plane, axis.plane)
            ):
                continue
            budget.charge()
            if _bounds_in_axis_id_corridor(candidate, axis):
                matches.append(axis)
        if len(matches) != 1:
            findings.append(
                _finding(
                    category="topology",
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        labels = labels_by_axis[matches[0].identifier]
        if labels:
            # A second label in the exact same corridor is a semantic tie.
            budget.charge()
        labels.append((candidate, parsed))

    ids: dict[str, str] = {}
    for axis in axes:
        labels = labels_by_axis.get(axis.identifier, [])
        if len(labels) != 1:
            if labels:
                for candidate, _value in labels:
                    findings.append(
                        _finding(
                            category="topology",
                            status="证据不足",
                            identity=candidate.record.identity_fingerprint,
                        )
                    )
            continue
        candidate, parsed = labels[0]
        ids[axis.identifier] = parsed
        traces.bind_axis_label(candidate.record, axis.identifier)
    return ids


def _axis_global_interval(axis: _Axis, unit: tuple[float, float]) -> tuple[float, float]:
    values = [_dot((point[0], point[1]), unit) for point in (axis.start, axis.end)]
    return min(values), max(values)


def _axes_collinear(first: _Axis, second: _Axis, scale: float) -> bool:
    if (
        not _same_plane(first.plane, second.plane)
        or abs(_cross(first.unit, second.unit)) > _PARALLEL_SINE_LIMIT
    ):
        return False
    normal = first.normal
    distance = abs(
        _dot((second.start[0], second.start[1]), normal) - first.radial
    )
    return distance <= scale * _ENDPOINT_RATIO


def _axis_endpoint_in_support(axis: _Axis, support: _Support) -> tuple[bool, bool]:
    return (
        _point_in_or_on_polygon(axis.start, support.vertices),
        _point_in_or_on_polygon(axis.end, support.vertices),
    )


def _axes_join_through_support(
    first: _Axis,
    second: _Axis,
    support: _Support,
    *,
    scale: float,
    budget: RelationBudget,
) -> bool:
    """Require two collinear axes to meet through the same explicit support."""

    # This one relation includes collinearity, endpoint-in-support, and
    # support-face clipping.  It is charged before any exact result can be
    # observed, including a failed relation.
    budget.charge()
    if (
        not _binding_plane_envelope_is_valid(first, second, support)
        or not _axes_collinear(first, second, scale)
        or not _same_plane(
        first.plane, support.plane
        )
    ):
        return False
    first_endpoints = _axis_endpoint_in_support(first, support)
    second_endpoints = _axis_endpoint_in_support(second, support)
    # A shared support is evidence only where both axis fragments terminate in
    # it.  An arbitrary beam crossing a support body is not a continuity edge.
    if sum(first_endpoints) != 1 or sum(second_endpoints) != 1:
        return False
    interval = _line_polygon_interval(
        support.vertices,
        unit=first.unit,
        normal=first.normal,
        radial=first.radial,
    )
    if interval is None:
        return False
    first_low, first_high = _axis_global_interval(first, first.unit)
    second_low, second_high = _axis_global_interval(second, first.unit)
    support_low, support_high = interval
    # One fragment must approach from each outer face.  This prevents a
    # branch or duplicate parallel fragment from being joined by proximity.
    left_to_right = (
        first_low < support_low + scale * _ENDPOINT_RATIO
        and first_high >= support_low - scale * _ENDPOINT_RATIO
        and second_high > support_high - scale * _ENDPOINT_RATIO
        and second_low <= support_high + scale * _ENDPOINT_RATIO
    )
    right_to_left = (
        second_low < support_low + scale * _ENDPOINT_RATIO
        and second_high >= support_low - scale * _ENDPOINT_RATIO
        and first_high > support_high - scale * _ENDPOINT_RATIO
        and first_low <= support_high + scale * _ENDPOINT_RATIO
    )
    return left_to_right or right_to_left


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self._parents = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self._parents[value]
        if parent != value:
            self._parents[value] = self.find(parent)
        return self._parents[value]

    def union(self, first: str, second: str) -> None:
        first_root, second_root = self.find(first), self.find(second)
        if first_root != second_root:
            self._parents[second_root] = first_root


def _nearby_axes_by_support(
    axes: Sequence[_Axis],
    supports: Sequence[_Support],
    *,
    scale: float,
) -> Iterable[tuple[_Support, tuple[_Axis, ...]]]:
    """Use a bounded grid to enumerate only nearby axis/support relations."""

    if not axes or not supports:
        return
    margin = scale * _ENDPOINT_RATIO
    grid = _UniformGrid(scale)
    expanded_bounds = [axis.bounds.expanded(margin) for axis in axes]
    for index, bounds in enumerate(expanded_bounds):
        grid.insert(index, bounds)

    for support in sorted(
        supports,
        key=lambda item: (
            item.plane,
            item.bounds.minimum[0],
            item.bounds.minimum[1],
            item.bounds.maximum[0],
            item.bounds.maximum[1],
            str(item.record.handle),
        ),
    ):
        nearby = tuple(
            sorted(
                (
                    axes[index]
                    for index in grid.query(support.bounds.expanded(margin))
                    if _same_plane(axes[index].plane, support.plane)
                    and expanded_bounds[index].overlaps(support.bounds)
                ),
                key=lambda axis: axis.identifier,
            )
        )
        if nearby:
            yield support, nearby


def _chain_supports(
    axes: Sequence[_Axis],
    support_index: _SpatialIndex,
    *,
    unit: tuple[float, float],
    scale: float,
    budget: RelationBudget,
) -> tuple[tuple[_Support, float, float], ...] | None:
    """Clip indexed explicit supports that actually meet the chain axis."""

    if not axes:
        return None
    normal = (-unit[1], unit[0])
    radial = axes[0].radial
    axis_low = min(_axis_global_interval(axis, unit)[0] for axis in axes)
    axis_high = max(_axis_global_interval(axis, unit)[1] for axis in axes)
    axis_bounds = _projected_rectangle_bounds(
        unit=unit,
        normal=normal,
        radial=radial,
        plane=axes[0].plane,
        low=axis_low,
        high=axis_high,
        r_low=0.0,
        r_high=0.0,
    ).expanded(scale * _ENDPOINT_RATIO)
    collected: dict[str, tuple[_Support, float, float]] = {}
    nearby_supports = sorted(
        (
            support
            for support in support_index.query(axis_bounds)
            if isinstance(support, _Support)
        ),
        key=lambda support: (
            support.plane,
            support.bounds.minimum[0],
            support.bounds.minimum[1],
            support.bounds.maximum[0],
            support.bounds.maximum[1],
            str(support.record.handle),
        ),
    )
    for support in nearby_supports:
        if (
            not _binding_plane_envelope_is_valid(tuple(axes), support)
            or not _same_plane(axes[0].plane, support.plane)
        ):
            continue
        # Support clipping is an exact axis/support relation.
        budget.charge()
        interval = _line_polygon_interval(
            support.vertices,
            unit=unit,
            normal=normal,
            radial=radial,
        )
        if interval is None:
            continue
        low, high = interval
        if high < axis_low - _EPSILON or low > axis_high + _EPSILON:
            continue
        if high - low <= _EPSILON:
            return None
        collected[str(support.record.handle)] = (support, low, high)
    ordered = tuple(
        sorted(
            collected.values(),
            key=lambda value: (
                value[1],
                value[2],
                int(value[0].record.handle, 16),
            ),
        )
    )
    if len(ordered) < 2:
        return None
    for previous, current in zip(ordered, ordered[1:]):
        # Overlapping clip intervals are a deterministic support-face tie.
        budget.charge()
        if current[1] <= previous[2] + _EPSILON:
            return None
    return ordered


def _component_is_continuous(
    axes: Sequence[_Axis],
    supports: Sequence[tuple[_Support, float, float]],
    *,
    unit: tuple[float, float],
    scale: float,
    budget: RelationBudget,
) -> bool:
    """Reject gaps and terminal/cantilever geometry for topology v1."""

    intervals = sorted(
        (_axis_global_interval(axis, unit) for axis in axes),
        key=lambda item: (item[0], item[1]),
    )
    if not intervals:
        return False
    tolerance = scale * _ENDPOINT_RATIO
    support_index = _IntervalIndex(
        (support_low, support_high, (support, support_low, support_high))
        for support, support_low, support_high in supports
    )
    for previous, current in zip(intervals, intervals[1:]):
        budget.charge()
        if current[0] <= previous[1] + tolerance:
            continue
        continuous = False
        for _support, support_low, support_high in support_index.query(
            previous[1] - tolerance,
            current[0] + tolerance,
        ):
            budget.charge()
            if (
                support_low <= previous[1] + tolerance
                and support_high >= current[0] - tolerance
            ):
                continuous = True
                break
        if not continuous:
            return False
    first_support = supports[0]
    last_support = supports[-1]
    overall_low = min(interval[0] for interval in intervals)
    overall_high = max(interval[1] for interval in intervals)
    # A supported v1 chain begins and ends inside its outer explicit support.
    # Any uncovered extension is a terminal/cantilever case and is not
    # silently converted into a span.
    budget.charge()
    return (
        overall_low >= first_support[1] - tolerance
        and overall_high <= last_support[2] + tolerance
    )


def _chain_widths_are_uniform(
    axes: Sequence[_Axis],
    *,
    scale: float,
    budget: RelationBudget,
) -> bool:
    """Accept a chain only when every paired member has one cross-section.

    The predicate deliberately has no averaging fallback.  A value exactly
    on the scale-relative threshold is an unresolved boundary rather than an
    arbitrary choice of either section width.
    """

    if not axes:
        return False
    reference = axes[0].width
    tolerance = scale * _CHAIN_WIDTH_TOLERANCE_RATIO
    for axis in axes[1:]:
        budget.charge()
        if abs(axis.width - reference) >= tolerance - _EPSILON:
            return False
    return True


def _span_width(
    axes: Sequence[_Axis],
    *,
    unit: tuple[float, float],
    low: float,
    high: float,
    budget: RelationBudget,
) -> float | None:
    """Return the conservative local width for a span without averaging."""

    overlapping: list[float] = []
    for axis in axes:
        budget.charge()
        axis_low, axis_high = _axis_global_interval(axis, unit)
        if axis_high >= low - _EPSILON and axis_low <= high + _EPSILON:
            overlapping.append(axis.width)
    return max(overlapping) if overlapping else None


def _build_chains(
    axes: Sequence[_Axis],
    axis_ids: Mapping[str, str],
    supports: Sequence[_Support],
    *,
    scale: float,
    traces: _TraceBook,
    findings: list[dict[str, Any]],
    support_index: _SpatialIndex,
    budget: RelationBudget,
) -> tuple[_Chain, ...]:
    """Build only unbranched private-equality chains at explicit supports."""

    eligible = [axis for axis in axes if axis.identifier in axis_ids]
    by_id = {axis.identifier: axis for axis in eligible}
    links: list[tuple[str, str, str]] = []
    axis_link_count: dict[str, int] = defaultdict(int)
    support_link_count: dict[str, int] = defaultdict(int)
    linked_axis_pairs: set[tuple[str, str]] = set()
    for support, nearby_axes in _nearby_axes_by_support(
        eligible,
        supports,
        scale=scale,
    ):
        by_beam_id: dict[str, list[_Axis]] = defaultdict(list)
        for axis in nearby_axes:
            token = axis_ids[axis.identifier]
            equal_axes = by_beam_id[token]
            if equal_axes:
                # Beam-ID grouping is private equality only, but every
                # confirmed equality comparison still consumes the one
                # relation budget.
                budget.charge()
            equal_axes.append(axis)
        # Keep equality values strictly in memory and order groups by
        # geometry-derived axis identities, not their private token strings.
        same_id_groups = sorted(
            by_beam_id.values(),
            key=lambda group: tuple(sorted(axis.identifier for axis in group)),
        )
        for same_id_axes in same_id_groups:
            for first_index, first in enumerate(same_id_axes):
                for second in same_id_axes[first_index + 1 :]:
                    pair = tuple(sorted((first.identifier, second.identifier)))
                    if pair in linked_axis_pairs:
                        budget.charge()
                        continue
                    if _axes_join_through_support(
                        first,
                        second,
                        support,
                        scale=scale,
                        budget=budget,
                    ):
                        links.append((first.identifier, second.identifier, str(support.record.handle)))
                        linked_axis_pairs.add(pair)
                        # Linking an already related axis/support pair would
                        # be a branch tie, so its relation is budgeted too.
                        budget.charge()
                        axis_link_count[first.identifier] += 1
                        axis_link_count[second.identifier] += 1
                        support_link_count[str(support.record.handle)] += 1
    branched_axes: set[str] = set()
    for axis_id, count in axis_link_count.items():
        budget.charge()
        if count > 2:
            branched_axes.add(axis_id)
    # More than one continuity relation at a support is a branch/ambiguous
    # join.  It cannot be resolved by choosing a neighbor.
    for first_id, second_id, support_handle in links:
        budget.charge()
        if support_link_count[support_handle] > 1:
            branched_axes.update((first_id, second_id))
    if branched_axes:
        for axis_id in sorted(branched_axes):
            axis = by_id[axis_id]
            for record in axis.records:
                findings.append(
                    _finding(
                        category="topology",
                        status="证据不足",
                        identity=record.identity_fingerprint,
                    )
                )
    usable = [axis for axis in eligible if axis.identifier not in branched_axes]
    union = _UnionFind(axis.identifier for axis in usable)
    for first_id, second_id, _support_handle in links:
        if first_id not in branched_axes and second_id not in branched_axes:
            union.union(first_id, second_id)
    groups: dict[str, list[_Axis]] = defaultdict(list)
    for axis in usable:
        groups[union.find(axis.identifier)].append(axis)

    chains: list[_Chain] = []
    for group_axes in groups.values():
        group_axes.sort(key=lambda axis: axis.identifier)
        first = group_axes[0]
        unit, normal, radial = first.unit, first.normal, first.radial
        group_is_consistent = True
        for axis in group_axes[1:]:
            # This exact collinearity and token-equality relation remains
            # private, but cannot escape the shared budget.
            budget.charge()
            if (
                not _axes_collinear(first, axis, scale)
                or axis_ids[axis.identifier] != axis_ids[first.identifier]
            ):
                group_is_consistent = False
                break
        if not group_is_consistent:
            continue
        if not _chain_widths_are_uniform(
            group_axes,
            scale=scale,
            budget=budget,
        ):
            continue
        clipped = _chain_supports(
            group_axes,
            support_index,
            unit=unit,
            scale=scale,
            budget=budget,
        )
        if clipped is None or not _component_is_continuous(
            group_axes,
            clipped,
            unit=unit,
            scale=scale,
            budget=budget,
        ):
            for axis in group_axes:
                for record in axis.records:
                    findings.append(
                        _finding(
                            category="topology",
                            status="证据不足",
                            identity=record.identity_fingerprint,
                        )
                    )
            continue
        chain_id = derive_chain_id(
            entity_provenance(
                record.identity_fingerprint,
                record.content_fingerprint,
            )
            for axis in group_axes
            for record in axis.records
        )
        support_entries = tuple(
            _SupportOnChain(
                support=support,
                low=low,
                high=high,
                support_id=derive_support_id(
                    chain_id,
                    derive_trace_id(
                        support.record.identity_fingerprint,
                        support.record.content_fingerprint,
                        "support_geometry",
                    ),
                    support.record.identity_fingerprint,
                    support.record.content_fingerprint,
                ),
            )
            for support, low, high in clipped
        )
        spans: list[_Span] = []
        invalid_span = False
        for left, right in zip(support_entries, support_entries[1:]):
            budget.charge()
            if right.low - left.high <= scale * _ENDPOINT_RATIO:
                invalid_span = True
                break
            local_width = _span_width(
                group_axes,
                unit=unit,
                low=left.high,
                high=right.low,
                budget=budget,
            )
            if local_width is None:
                invalid_span = True
                break
            spans.append(
                _Span(
                    span_id=derive_span_id(
                        chain_id,
                        left.support_id,
                        right.support_id,
                    ),
                    left=left,
                    right=right,
                    low=left.high,
                    high=right.low,
                    width=local_width,
                )
            )
        if invalid_span or not spans:
            continue
        # The chain is one binding participant.  Validate every original
        # edge/support elevation together before retaining any projected
        # centreline or target corridor derived from it.
        if not _binding_plane_envelope_is_valid(
            tuple(group_axes),
            tuple(entry.support for entry in support_entries),
        ):
            continue
        chains.append(
            _Chain(
                chain_id=chain_id,
                axes=tuple(group_axes),
                unit=unit,
                normal=normal,
                radial=radial,
                plane=first.plane,
                # Uniformity was proven above.  Keep the conservative member
                # width for whole-chain envelopes; never average sections.
                width=max(axis.width for axis in group_axes),
                low=min(_axis_global_interval(axis, unit)[0] for axis in group_axes),
                high=max(_axis_global_interval(axis, unit)[1] for axis in group_axes),
                supports=support_entries,
                spans=tuple(spans),
            )
        )
    # A canonical support trace may own exactly one registered support.  If
    # the same source support would terminate multiple candidate chains, no
    # chain may claim it by selecting an arbitrary owner.
    candidate_support_counts: dict[str, int] = defaultdict(int)
    for chain in chains:
        for entry in chain.supports:
            candidate_support_counts[str(entry.support.record.handle)] += 1
    chains = [
        chain
        for chain in chains
        if all(
            candidate_support_counts[str(entry.support.record.handle)] == 1
            for entry in chain.supports
        )
    ]

    support_bindings: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for chain in chains:
        for entry in chain.supports:
            support_bindings[str(entry.support.record.handle)].append(
                (chain.chain_id, entry.support_id)
            )
    for chain in chains:
        for axis in chain.axes:
            for record in axis.records:
                traces.bind(record, chain_id=chain.chain_id)
            traces.bind_axis_labels_to_chain(axis.identifier, chain.chain_id)
        for support in chain.supports:
            bindings = support_bindings[str(support.support.record.handle)]
            # An explicit support can terminate two different-ID beams.  It
            # remains valid topology, but one raw trace cannot honestly claim
            # a singular chain/support target, so retain its entity evidence
            # with null target IDs rather than selecting a preferred owner.
            budget.charge()
            if len(bindings) == 1:
                traces.bind(
                    support.support.record,
                    chain_id=chain.chain_id,
                    support_id=support.support_id,
                )
    return tuple(sorted(chains, key=lambda chain: chain.chain_id))


def _orphaned_axis_geometry(
    axes: Sequence[_Axis],
    chains: Sequence[_Chain],
    *,
    budget: RelationBudget,
) -> tuple[_UnresolvedBeamGeometry, ...]:
    """Keep every paired axis outside exactly one admitted chain private.

    An edge pair remains potentially relevant geometry when it lacks an ID,
    has a conflicting ID, or fails a structural admission gate.  Retaining
    both visible edges prevents the centreline from hiding a local edge
    conflict during later annotation checks.
    """

    admitted_counts: dict[str, int] = defaultdict(int)
    for chain in chains:
        for axis in chain.axes:
            admitted_counts[axis.identifier] += 1
    unresolved: list[_UnresolvedBeamGeometry] = []
    for axis in sorted(axes, key=lambda item: item.identifier):
        # Inclusion in exactly one chain is a relation that authorizes
        # omitting this otherwise paired geometry, so it shares the budget.
        budget.charge()
        if admitted_counts[axis.identifier] == 1:
            continue
        for source in (axis.first, axis.second):
            # Reuse the original edge's private segment and bounds.  The
            # serialized conclusion must bind to this canonical source edge,
            # not an aggregate axis identifier that cannot resolve to a
            # manifest-backed trace.
            unresolved.append(
                _UnresolvedBeamGeometry(
                    segment=source,
                    provenance=source.record.identity_fingerprint,
                    bounds=source.bounds,
                    reason="orphan",
                )
            )
    return tuple(
        sorted(
            unresolved,
            key=lambda item: (
                item.segment.plane,
                item.bounds.minimum[0],
                item.bounds.minimum[1],
                item.bounds.maximum[0],
                item.bounds.maximum[1],
                item.provenance,
            ),
        )
    )


def _overlap_blocked_handles(
    inventory: Sequence[_TextOverlapEntry],
    *,
    scale: float,
    findings: list[dict[str, Any]],
    budget: RelationBudget,
) -> frozenset[str]:
    """Gate all controlled visible TEXT before semantic eligibility or parsing."""

    if len(inventory) > MAX_ANNOTATIONS:
        raise _limit_error()
    blocked: set[str] = set()
    reported: set[str] = set()

    def report(entry: _TextOverlapEntry) -> None:
        handle = str(entry.record.handle)
        if handle in reported:
            return
        reported.add(handle)
        category: Literal[
            "support_upper_annotation", "span_lower_annotation", "topology"
        ]
        if entry.role == "support_upper_annotations":
            category = "support_upper_annotation"
        elif entry.role == "span_lower_annotations":
            category = "span_lower_annotation"
        else:
            category = "topology"
        findings.append(
            _finding(
                category=category,
                status="证据不足",
                identity=entry.record.identity_fingerprint,
            )
        )

    # A visible controlled TEXT with no reliable bounds cannot be silently
    # dropped from the gate.  A known plane limits the affected region to that
    # plane; an unknown plane means no conservative disjointness proof exists.
    unresolved = [entry for entry in inventory if entry.bounds is None]
    for entry in unresolved:
        for affected in inventory:
            if (
                entry.plane is None
                or affected.plane is None
                or _same_plane(entry.plane, affected.plane)
            ):
                # An unbounded controlled TEXT is an exact uncertainty
                # relation with every potentially co-planar candidate.
                budget.charge()
                blocked.add(str(affected.record.handle))
                report(affected)

    bounded = sorted(
        (entry for entry in inventory if entry.bounds is not None),
        key=lambda entry: (
            entry.bounds.plane if entry.bounds is not None else float("inf"),
            entry.bounds.minimum[0] if entry.bounds is not None else float("inf"),
            entry.bounds.minimum[1] if entry.bounds is not None else float("inf"),
            entry.bounds.maximum[0] if entry.bounds is not None else float("inf"),
            entry.bounds.maximum[1] if entry.bounds is not None else float("inf"),
            entry.role,
            entry.record.identity_fingerprint,
        ),
    )
    grid = _UniformGrid(max(scale, _EPSILON))
    for index, entry in enumerate(bounded):
        assert entry.bounds is not None
        grid.insert(index, entry.bounds)
    for first, second in grid.candidate_pairs():
        first_entry, second_entry = bounded[first], bounded[second]
        assert first_entry.bounds is not None and second_entry.bounds is not None
        if not _same_plane(first_entry.bounds.plane, second_entry.bounds.plane):
            continue
        # A precise text-bound overlap is never performed outside the shared
        # budget, including the overwhelmingly common disjoint case.
        budget.charge()
        if not _strict_bounds_overlap(first_entry.bounds, second_entry.bounds):
            continue
        blocked.add(str(first_entry.record.handle))
        blocked.add(str(second_entry.record.handle))
        report(first_entry)
        report(second_entry)
    return frozenset(blocked)


@dataclass(frozen=True)
class _LeaderSpanTarget:
    """One private span-axis segment available to an exact leader predicate."""

    chain: _Chain
    span: _Span
    bounds: Aabb


@dataclass(frozen=True)
class _TopologyIndexes:
    """All bounded broad phases needed after chain construction."""

    chains_by_id: Mapping[str, _Chain]
    chain_index: _SpatialIndex
    leader_index: _SpatialIndex
    unresolved_index: _SpatialIndex | None
    unbound_support_index: _SpatialIndex | None
    support_index: _SpatialIndex
    support_bindings: Mapping[str, tuple[tuple[_Chain, _SupportOnChain], ...]]
    span_index: _SpatialIndex
    span_intervals: Mapping[str, _IntervalIndex]
    support_intervals: Mapping[str, _IntervalIndex]


def _chain_annotation_bounds(chain: _Chain) -> Aabb:
    return _projected_rectangle_bounds(
        unit=chain.unit,
        normal=chain.normal,
        radial=chain.radial,
        plane=chain.plane,
        low=chain.low,
        high=chain.high,
        r_low=-_ANNOTATION_OUTER_WIDTHS * chain.width,
        r_high=_ANNOTATION_OUTER_WIDTHS * chain.width,
    )


def _span_axis_bounds(chain: _Chain, span: _Span) -> Aabb:
    return _projected_rectangle_bounds(
        unit=chain.unit,
        normal=chain.normal,
        radial=chain.radial,
        plane=chain.plane,
        low=span.low,
        high=span.high,
        r_low=0.0,
        r_high=0.0,
    )


def _build_topology_indexes(
    *,
    chains: Sequence[_Chain],
    supports: Sequence[_Support],
    leaders: Sequence[_Leader],
    unresolved: Sequence[_UnresolvedBeamGeometry],
    scale: float,
) -> _TopologyIndexes:
    """Construct fixed-size broad phases before any annotation exact checks."""

    cell_size = max(scale, _EPSILON)
    ordered_chains = tuple(sorted(chains, key=lambda chain: chain.chain_id))
    ordered_supports = tuple(
        sorted(
            supports,
            key=lambda support: (
                support.plane,
                support.bounds.minimum[0],
                support.bounds.minimum[1],
                support.bounds.maximum[0],
                support.bounds.maximum[1],
                str(support.record.handle),
            ),
        )
    )
    ordered_leaders = tuple(
        sorted(
            leaders,
            key=lambda leader: (
                leader.start,
                leader.end,
                leader.bounds.minimum[0],
                leader.bounds.minimum[1],
                leader.bounds.maximum[0],
                leader.bounds.maximum[1],
                leader.record.identity_fingerprint,
            ),
        )
    )
    chain_index = _SpatialIndex(
        ordered_chains,
        tuple(_chain_annotation_bounds(chain) for chain in ordered_chains),
        cell_size=cell_size,
    )
    leader_index = _SpatialIndex(
        ordered_leaders,
        tuple(leader.bounds for leader in ordered_leaders),
        cell_size=cell_size,
    )
    unresolved_index = (
        _SpatialIndex(
            tuple(unresolved),
            tuple(item.bounds for item in unresolved),
            cell_size=cell_size,
        )
        if unresolved
        else None
    )
    support_index = _SpatialIndex(
        ordered_supports,
        tuple(support.bounds for support in ordered_supports),
        cell_size=cell_size,
    )
    support_bindings: dict[str, list[tuple[_Chain, _SupportOnChain]]] = defaultdict(
        list
    )
    span_targets: list[_LeaderSpanTarget] = []
    span_intervals: dict[str, _IntervalIndex] = {}
    support_intervals: dict[str, _IntervalIndex] = {}
    for chain in ordered_chains:
        for support in chain.supports:
            support_bindings[str(support.support.record.handle)].append(
                (chain, support)
            )
        span_intervals[chain.chain_id] = _IntervalIndex(
            (span.low, span.high, span) for span in chain.spans
        )
        support_intervals[chain.chain_id] = _IntervalIndex(
            (support.low, support.high, support) for support in chain.supports
        )
        span_targets.extend(
            _LeaderSpanTarget(
                chain=chain,
                span=span,
                bounds=_span_axis_bounds(chain, span),
            )
            for span in chain.spans
        )
    span_targets.sort(key=lambda item: (item.chain.chain_id, item.span.span_id))
    unbound_supports = tuple(
        support
        for support in ordered_supports
        if len(support_bindings.get(str(support.record.handle), ())) != 1
    )
    return _TopologyIndexes(
        chains_by_id={chain.chain_id: chain for chain in ordered_chains},
        chain_index=chain_index,
        leader_index=leader_index,
        unresolved_index=unresolved_index,
        unbound_support_index=(
            _SpatialIndex(
                unbound_supports,
                tuple(support.bounds for support in unbound_supports),
                cell_size=cell_size,
            )
            if unbound_supports
            else None
        ),
        support_index=support_index,
        support_bindings={
            handle: tuple(
                sorted(bindings, key=lambda item: (item[0].chain_id, item[1].support_id))
            )
            for handle, bindings in support_bindings.items()
        },
        span_index=_SpatialIndex(
            tuple(span_targets),
            tuple(item.bounds for item in span_targets),
            cell_size=cell_size,
        ),
        span_intervals=span_intervals,
        support_intervals=support_intervals,
    )


def _candidate_chains(
    candidate: _TextCandidate,
    indexes: _TopologyIndexes,
) -> tuple[_Chain, ...]:
    """Return only broad-phase-near, coplanar chains in stable order."""

    return tuple(
        sorted(
            (
                chain
                for chain in indexes.chain_index.query(candidate.bounds)
                if isinstance(chain, _Chain)
                and _binding_plane_envelope_is_valid(candidate, chain)
                and _same_plane(candidate.bounds.plane, chain.plane)
            ),
            key=lambda chain: chain.chain_id,
        )
    )


def _in_exterior_annotation_band(
    r_low: float,
    r_high: float,
    width: float,
) -> bool:
    """Require one full bound outside the beam without inventing upper/lower."""

    inner = width / 2.0
    outer = _ANNOTATION_OUTER_WIDTHS * width
    return (
        r_low > inner + _EPSILON and r_high < outer - _EPSILON
    ) or (
        r_high < -inner - _EPSILON and r_low > -outer + _EPSILON
    )


def _chain_local_width(
    chain: _Chain,
    low: float,
    high: float,
    *,
    budget: RelationBudget,
) -> float | None:
    """Use intersecting member sections for an annotation, never an average."""

    return _span_width(
        chain.axes,
        unit=chain.unit,
        low=low,
        high=high,
        budget=budget,
    )


def _support_upper_matches(
    candidate: _TextCandidate,
    chains: Sequence[_Chain],
    *,
    indexes: _TopologyIndexes,
    budget: RelationBudget,
) -> list[_AnnotationTarget]:
    matches: list[_AnnotationTarget] = []
    for chain in chains:
        # Projection and exterior-band classification are an exact text-zone
        # predicate, not a nearest-object heuristic.
        budget.charge()
        low, high, r_low, r_high = chain.project_bounds(candidate.bounds)
        width = _chain_local_width(chain, low, high, budget=budget)
        if width is None or not _in_exterior_annotation_band(r_low, r_high, width):
            continue
        for span in indexes.span_intervals[chain.chain_id].query(low, high):
            if not isinstance(span, _Span):
                continue
            budget.charge()
            if not (low > span.low + _EPSILON and high < span.high - _EPSILON):
                continue
            if span.left.support_id != chain.supports[0].support_id:
                matches.append(
                    _AnnotationTarget(
                        chain_id=chain.chain_id,
                        support_id=span.left.support_id,
                        span_id=None,
                    )
                )
            if span.right.support_id != chain.supports[-1].support_id:
                matches.append(
                    _AnnotationTarget(
                        chain_id=chain.chain_id,
                        support_id=span.right.support_id,
                        span_id=None,
                    )
                )
    return matches


def _span_lower_matches(
    candidate: _TextCandidate,
    chains: Sequence[_Chain],
    *,
    indexes: _TopologyIndexes,
    budget: RelationBudget,
) -> list[_AnnotationTarget]:
    matches: list[_AnnotationTarget] = []
    for chain in chains:
        budget.charge()
        low, high, r_low, r_high = chain.project_bounds(candidate.bounds)
        width = _chain_local_width(chain, low, high, budget=budget)
        if width is None or not _in_exterior_annotation_band(r_low, r_high, width):
            continue
        for span in indexes.span_intervals[chain.chain_id].query(low, high):
            if not isinstance(span, _Span):
                continue
            budget.charge()
            length = span.high - span.low
            middle_low = span.low + length * _MIDSPAN_FRACTION
            middle_high = span.high - length * _MIDSPAN_FRACTION
            if low > middle_low + _EPSILON and high < middle_high - _EPSILON:
                matches.append(
                    _AnnotationTarget(
                        chain_id=chain.chain_id,
                        support_id=None,
                        span_id=span.span_id,
                    )
                )
    return matches


def _annotation_on_zone_boundary(
    candidate: _TextCandidate,
    chains: Sequence[_Chain],
    *,
    indexes: _TopologyIndexes,
    budget: RelationBudget,
) -> bool:
    """Detect exact zone-face ties before classifying a misplacement."""

    for chain in chains:
        budget.charge()
        low, high, r_low, r_high = chain.project_bounds(candidate.bounds)
        width = _chain_local_width(chain, low, high, budget=budget)
        if width is None:
            return True
        radial_faces = (
            -_ANNOTATION_OUTER_WIDTHS * width,
            -width / 2.0,
            width / 2.0,
            _ANNOTATION_OUTER_WIDTHS * width,
        )
        if any(
            abs(value - face) <= _EPSILON
            for value in (r_low, r_high)
            for face in radial_faces
        ):
            return True
        if candidate.role == "support_upper_annotations":
            for support in indexes.support_intervals[chain.chain_id].query(low, high):
                if not isinstance(support, _SupportOnChain):
                    continue
                budget.charge()
                if any(
                    abs(value - face) <= _EPSILON
                    for value in (low, high)
                    for face in (support.low, support.high)
                ):
                    return True
        elif candidate.role == "span_lower_annotations":
            for span in indexes.span_intervals[chain.chain_id].query(low, high):
                if not isinstance(span, _Span):
                    continue
                budget.charge()
                length = span.high - span.low
                faces = (
                    span.low,
                    span.high,
                    span.low + length * _MIDSPAN_FRACTION,
                    span.high - length * _MIDSPAN_FRACTION,
                )
                if any(
                    abs(value - face) <= _EPSILON
                    for value in (low, high)
                    for face in faces
                ):
                    return True
    return False


def _target_key(target: _AnnotationTarget) -> tuple[str, str | None, str | None]:
    return target.chain_id, target.support_id, target.span_id


def _annotation_chain_relations(
    candidate: _TextCandidate,
    chains: Sequence[_Chain],
    *,
    budget: RelationBudget,
) -> list[_Chain]:
    """Return only exact envelope relations; this is not a distance search."""

    related: list[_Chain] = []
    for chain in chains:
        budget.charge()
        low, high, r_low, r_high = chain.project_bounds(candidate.bounds)
        width = _chain_local_width(chain, low, high, budget=budget)
        if width is None:
            continue
        if (
            high >= chain.supports[0].low - _EPSILON
            and low <= chain.supports[-1].high + _EPSILON
            and r_high >= -_ANNOTATION_OUTER_WIDTHS * width - _EPSILON
            and r_low <= _ANNOTATION_OUTER_WIDTHS * width + _EPSILON
        ):
            related.append(chain)
    return related


def _illegal_annotation_targets(
    candidate: _TextCandidate,
    chains: Sequence[_Chain],
    *,
    indexes: _TopologyIndexes,
    budget: RelationBudget,
) -> list[_AnnotationTarget]:
    """Return exact canonical targets for a uniquely illegal placement.

    Legal-zone matching intentionally rejects the placements considered here.
    That does not permit a nearest-target fallback: the full text envelope
    must still intersect exactly one canonical support or span on the one
    already-established related chain.  Multiple interval intersections are
    ownership ambiguity and remain evidence insufficiency.
    """

    targets: set[_AnnotationTarget] = set()
    for chain in chains:
        low, high, _r_low, _r_high = chain.project_bounds(candidate.bounds)
        intervals: Iterable[_SupportOnChain | _Span]
        if candidate.role == "support_upper_annotations":
            intervals = indexes.support_intervals[chain.chain_id].query(low, high)
        else:
            intervals = indexes.span_intervals[chain.chain_id].query(low, high)
        for interval in intervals:
            budget.charge()
            if not (interval.low <= high + _EPSILON and interval.high >= low - _EPSILON):
                continue
            targets.add(
                _AnnotationTarget(
                    chain_id=chain.chain_id,
                    support_id=(
                        interval.support_id
                        if isinstance(interval, _SupportOnChain)
                        else None
                    ),
                    span_id=interval.span_id if isinstance(interval, _Span) else None,
                )
            )
    return sorted(
        targets,
        key=lambda target: (
            target.chain_id,
            target.support_id or "",
            target.span_id or "",
        ),
    )


def _target_from_leaders(
    candidate: _TextCandidate,
    *,
    indexes: _TopologyIndexes,
    budget: RelationBudget,
) -> tuple[_AnnotationTarget | None, bool, tuple[_Leader, ...]]:
    """Return unique exact leader evidence or an ambiguity marker.

    A leader is optional.  If one touches a text bound, however, conflicting
    or unresolvable leader geometry is evidence insufficiency, never ignored.
    """

    touching: list[_Leader] = []
    unsafe_plane = False
    for leader in indexes.leader_index.query(candidate.bounds):
        if not isinstance(leader, _Leader):
            continue
        # A leader that touches the same XY text bounds participates in this
        # binding even when its source plane differs.  Its plane envelope is
        # therefore validated after the exact geometric touch, not filtered
        # away by a baseline comparison.
        budget.charge()
        if _segment_intersects_aabb(leader.start, leader.end, candidate.bounds):
            touching.append(leader)
            unsafe_plane = unsafe_plane or not _binding_plane_envelope_is_valid(
                candidate, leader
            )
    if not touching:
        return None, False, ()
    if unsafe_plane:
        return None, True, tuple(touching)
    targets: set[_AnnotationTarget] = set()
    for leader in touching:
        for support in indexes.support_index.query(leader.bounds):
            if not isinstance(support, _Support) or not _same_plane(
                support.plane, leader.start[2]
            ):
                continue
            for chain, support_target in indexes.support_bindings.get(
                str(support.record.handle), ()
            ):
                if (
                    not _binding_plane_envelope_is_valid(
                        candidate, leader, chain, support_target.support
                    )
                    or not _same_plane(chain.plane, leader.start[2])
                ):
                    continue
                # Exact leader/support-zone intersection.
                budget.charge()
                if _segment_intersects_polygon(
                    leader.start,
                    leader.end,
                    support_target.support.vertices,
                ):
                    if targets:
                        budget.charge()
                    targets.add(
                        _AnnotationTarget(
                            chain_id=chain.chain_id,
                            support_id=support_target.support_id,
                            span_id=None,
                        )
                    )
        for span_target in indexes.span_index.query(leader.bounds):
            if (
                not isinstance(span_target, _LeaderSpanTarget)
                or not _binding_plane_envelope_is_valid(
                    candidate, leader, span_target.chain
                )
                or not _same_plane(span_target.chain.plane, leader.start[2])
            ):
                continue
            span = span_target.span
            # A leader's evidence for a span is an actual axis intersection,
            # not proximity to a midpoint.
            axis_start = _projected_point(
                unit=span_target.chain.unit,
                normal=span_target.chain.normal,
                radial=span_target.chain.radial,
                plane=span_target.chain.plane,
                s=span.low,
                r=0.0,
            )
            axis_end = _projected_point(
                unit=span_target.chain.unit,
                normal=span_target.chain.normal,
                radial=span_target.chain.radial,
                plane=span_target.chain.plane,
                s=span.high,
                r=0.0,
            )
            budget.charge()
            if _segments_intersect(leader.start, leader.end, axis_start, axis_end):
                if targets:
                    budget.charge()
                targets.add(
                    _AnnotationTarget(
                        chain_id=span_target.chain.chain_id,
                        support_id=None,
                        span_id=span.span_id,
                    )
                )
    if len(targets) != 1:
        return None, True, tuple(touching)
    return next(iter(targets)), False, tuple(touching)


def _target_corridor(
    candidate: _TextCandidate,
    target: _AnnotationTarget,
    indexes: _TopologyIndexes,
    *,
    budget: RelationBudget,
) -> tuple[Aabb, tuple[Point, Point, Point, Point]] | None:
    """Return the exact private corridor that justified one legal target."""

    chain = indexes.chains_by_id.get(target.chain_id)
    if chain is None or not _same_plane(candidate.bounds.plane, chain.plane):
        return None
    low, high, r_low, r_high = chain.project_bounds(candidate.bounds)
    width = _chain_local_width(chain, low, high, budget=budget)
    if width is None:
        return None
    target_low: float | None = None
    target_high: float | None = None
    if target.support_id is not None:
        for span in chain.spans:
            budget.charge()
            if (
                target.support_id
                in {span.left.support_id, span.right.support_id}
                and low > span.low + _EPSILON
                and high < span.high - _EPSILON
            ):
                target_low, target_high = span.low, span.high
                width = span.width
                break
    elif target.span_id is not None:
        for span in chain.spans:
            budget.charge()
            if target.span_id == span.span_id:
                length = span.high - span.low
                target_low = span.low + length * _MIDSPAN_FRACTION
                target_high = span.high - length * _MIDSPAN_FRACTION
                width = span.width
                break
    if target_low is None or target_high is None:
        return None
    inner = width / 2.0
    outer = _ANNOTATION_OUTER_WIDTHS * width
    if r_low >= inner - _EPSILON and r_high <= outer + _EPSILON:
        corridor_low, corridor_high = inner, outer
    elif r_high <= -inner + _EPSILON and r_low >= -outer - _EPSILON:
        corridor_low, corridor_high = -outer, -inner
    else:
        return None
    polygon = _projected_rectangle_polygon(
        unit=chain.unit,
        normal=chain.normal,
        radial=chain.radial,
        plane=chain.plane,
        low=target_low,
        high=target_high,
        r_low=corridor_low,
        r_high=corridor_high,
    )
    return _bounds_from_points(polygon), polygon


def _unbound_support_conflicts(
    candidate: _TextCandidate,
    *,
    touching_leaders: Sequence[_Leader],
    indexes: _TopologyIndexes,
    budget: RelationBudget,
) -> bool:
    """Reject bindings touched by a configured support without one owner.

    A valid configured support polygon remains competing private evidence
    even when no chain admitted it (or more than one chain claims it).
    Neither a nearest-support fallback nor a unique valid target can erase
    that explicit conflict.
    """

    unbound_index = indexes.unbound_support_index
    if unbound_index is None:
        return False
    annotation_polygon = candidate.bounds.corners()
    for support in unbound_index.query(candidate.bounds.expanded(_EPSILON)):
        if not isinstance(support, _Support):
            continue
        if not _same_plane(candidate.bounds.plane, support.plane):
            continue
        budget.charge()
        if _polygons_related_within_tolerance(
            annotation_polygon,
            support.vertices,
        ):
            return True
    for leader in touching_leaders:
        for support in unbound_index.query(leader.bounds.expanded(_EPSILON)):
            if not isinstance(support, _Support):
                continue
            if not _same_plane(leader.start[2], support.plane):
                continue
            budget.charge()
            if _segment_intersects_polygon(
                leader.start, leader.end, support.vertices
            ) or any(
                _segments_related_within_tolerance(
                    leader.start,
                    leader.end,
                    support.vertices[index],
                    support.vertices[(index + 1) % len(support.vertices)],
                )
                for index in range(len(support.vertices))
            ):
                return True
    return False


def _unresolved_geometry_conflicts(
    candidate: _TextCandidate,
    *,
    target: _AnnotationTarget | None,
    candidate_chains: Sequence[_Chain],
    touching_leaders: Sequence[_Leader],
    indexes: _TopologyIndexes,
    budget: RelationBudget,
) -> bool:
    """Fail closed if unpaired configured beam geometry affects this label."""

    unresolved_index = indexes.unresolved_index
    if unresolved_index is None:
        return False
    for unresolved in unresolved_index.query(candidate.bounds):
        if not isinstance(unresolved, _UnresolvedBeamGeometry):
            continue
        # Exact annotation-bound/unresolved-edge relation.
        budget.charge()
        if _segment_intersects_aabb(
            unresolved.segment.start,
            unresolved.segment.end,
            candidate.bounds,
        ):
            if not _binding_plane_envelope_is_valid(candidate, unresolved.segment):
                return True
            return True
    for leader in touching_leaders:
        for unresolved in unresolved_index.query(leader.bounds):
            if not isinstance(unresolved, _UnresolvedBeamGeometry):
                continue
            # Exact leader/unresolved-geometry relation.
            budget.charge()
            if _segments_intersect(
                leader.start,
                leader.end,
                unresolved.segment.start,
                unresolved.segment.end,
            ):
                if not _binding_plane_envelope_is_valid(
                    candidate, leader, unresolved.segment
                ):
                    return True
                return True
    corridors: list[tuple[Aabb, tuple[Point, Point, Point, Point]]] = []
    if target is not None:
        # A private unresolved edge that crosses the exact target corridor is
        # a plausible competing beam relation, even if it misses the text
        # glyph bounds and optional leader.
        corridor = _target_corridor(candidate, target, indexes, budget=budget)
        if corridor is None:
            return True
        corridors.append(corridor)
    else:
        # An invalid-zone label has no legal target, but a sole nearby chain
        # is still the geometry behind a possible 疑似不一致 conclusion.  Test
        # its complete private annotation envelope before emitting that status.
        for chain in candidate_chains:
            polygon = _projected_rectangle_polygon(
                unit=chain.unit,
                normal=chain.normal,
                radial=chain.radial,
                plane=chain.plane,
                low=chain.low,
                high=chain.high,
                r_low=-_ANNOTATION_OUTER_WIDTHS * chain.width,
                r_high=_ANNOTATION_OUTER_WIDTHS * chain.width,
            )
            corridors.append((_bounds_from_points(polygon), polygon))
    for corridor_bounds, corridor_polygon in corridors:
        for unresolved in unresolved_index.query(corridor_bounds):
            if not isinstance(unresolved, _UnresolvedBeamGeometry):
                continue
            budget.charge()
            if _segment_intersects_polygon(
                unresolved.segment.start,
                unresolved.segment.end,
                corridor_polygon,
            ):
                if not _binding_plane_envelope_is_valid(candidate, unresolved.segment):
                    return True
                return True
    return False


def _annotation_category(role: Role) -> Literal[
    "support_upper_annotation", "span_lower_annotation"
]:
    return (
        "support_upper_annotation"
        if role == "support_upper_annotations"
        else "span_lower_annotation"
    )


def _bind_annotations(
    candidates: Sequence[_TextCandidate],
    *,
    blocked_handles: frozenset[str],
    traces: _TraceBook,
    findings: list[dict[str, Any]],
    indexes: _TopologyIndexes,
    budget: RelationBudget,
) -> None:
    """Assess support-upper/span-lower labels without changing any target."""

    annotation_candidates = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.role in _ANNOTATION_ROLES
        ),
        key=lambda item: (
            item.bounds.plane,
            item.bounds.minimum[0],
            item.bounds.minimum[1],
            item.bounds.maximum[0],
            item.bounds.maximum[1],
            item.role,
            item.record.identity_fingerprint,
        ),
    )
    parsed_by_handle: dict[str, str] = {}
    values: dict[tuple[Role, str], list[_TextCandidate]] = defaultdict(list)
    for candidate in annotation_candidates:
        handle = str(candidate.record.handle)
        if handle in blocked_handles:
            continue
        parsed = _parse_token(candidate)
        if parsed is None:
            findings.append(
                _finding(
                    category=_annotation_category(candidate.role),
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        parsed_by_handle[handle] = parsed
        equal_values = values[(candidate.role, parsed)]
        if equal_values:
            # Exact duplicate-token equality remains entirely in memory.
            budget.charge()
        equal_values.append(candidate)
        traces.bind(candidate.record, token_equality_established=True)

    repeated_handles = {
        str(candidate.record.handle)
        for grouped in values.values()
        if len(grouped) > 1
        for candidate in grouped
    }
    for candidate in annotation_candidates:
        handle = str(candidate.record.handle)
        if handle in repeated_handles:
            findings.append(
                _finding(
                    category=_annotation_category(candidate.role),
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )

    legal_targets: dict[str, list[_AnnotationTarget]] = {}
    candidate_chains: dict[str, tuple[_Chain, ...]] = {}
    for candidate in annotation_candidates:
        handle = str(candidate.record.handle)
        if handle in blocked_handles or handle in repeated_handles:
            continue
        if handle not in parsed_by_handle:
            continue
        nearby_chains = _candidate_chains(candidate, indexes)
        candidate_chains[handle] = nearby_chains
        legal_targets[handle] = (
            _support_upper_matches(
                candidate,
                nearby_chains,
                indexes=indexes,
                budget=budget,
            )
            if candidate.role == "support_upper_annotations"
            else _span_lower_matches(
                candidate,
                nearby_chains,
                indexes=indexes,
                budget=budget,
            )
        )

    # A support-upper label is permitted on exactly one adjacent side.  A
    # second candidate for its other side turns both records into ambiguity;
    # no positional tie breaker is allowed.
    competing_support_handles: set[str] = set()
    support_target_candidates: dict[tuple[str, str | None, str | None], list[str]] = (
        defaultdict(list)
    )
    for handle, targets in legal_targets.items():
        if len(targets) == 1 and targets[0].support_id is not None:
            tied_handles = support_target_candidates[_target_key(targets[0])]
            if tied_handles:
                # A second label for the same exact support target is a tie.
                budget.charge()
            tied_handles.append(handle)
    for handles in support_target_candidates.values():
        if len(handles) > 1:
            competing_support_handles.update(handles)

    for candidate in annotation_candidates:
        handle = str(candidate.record.handle)
        if (
            handle in blocked_handles
            or handle in repeated_handles
            or handle not in parsed_by_handle
        ):
            continue
        category = _annotation_category(candidate.role)
        targets = legal_targets[handle]
        # Zone faces are uncertainty, including a label that happens to
        # satisfy one target's predicates.  This must precede both the
        # unique-target and leader checks: touching is not legal ownership.
        if _annotation_on_zone_boundary(
            candidate,
            candidate_chains[handle],
            indexes=indexes,
            budget=budget,
        ):
            findings.append(
                _finding(
                    category=category,
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        leader_target, leader_ambiguous, touching_leaders = _target_from_leaders(
            candidate,
            indexes=indexes,
            budget=budget,
        )
        target = targets[0] if len(targets) == 1 else None
        target_chain = (
            indexes.chains_by_id.get(target.chain_id) if target is not None else None
        )
        if target_chain is not None and not _binding_plane_envelope_is_valid(
            candidate, target_chain, tuple(touching_leaders)
        ):
            findings.append(
                _finding(
                    category=category,
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        if _unbound_support_conflicts(
            candidate,
            touching_leaders=touching_leaders,
            indexes=indexes,
            budget=budget,
        ):
            findings.append(
                _finding(
                    category=category,
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        if _unresolved_geometry_conflicts(
            candidate,
            target=target,
            candidate_chains=candidate_chains[handle],
            touching_leaders=touching_leaders,
            indexes=indexes,
            budget=budget,
        ):
            findings.append(
                _finding(
                    category=category,
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        if leader_ambiguous or handle in competing_support_handles:
            findings.append(
                _finding(
                    category=category,
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        if len(targets) == 1:
            assert target is not None
            leader_matches = True
            if leader_target is not None:
                # Compare opaque geometry/entity-derived targets only after
                # charging the exact leader/target tie relation.
                budget.charge()
                leader_matches = (
                    leader_target.chain_id == target.chain_id
                    and (
                        (
                            target.support_id is not None
                            and leader_target.support_id == target.support_id
                        )
                        or (
                            target.span_id is not None
                            and leader_target.span_id == target.span_id
                        )
                    )
                )
            if not leader_matches:
                findings.append(
                    _finding(
                        category=category,
                        status="证据不足",
                        identity=candidate.record.identity_fingerprint,
                    )
                )
                continue
            traces.bind(
                candidate.record,
                chain_id=target.chain_id,
                support_id=target.support_id,
                span_id=target.span_id,
            )
            findings.append(
                _finding(
                    category=category,
                    status="一致",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        if len(targets) > 1:
            findings.append(
                _finding(
                    category=category,
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
            continue
        nearby_chains = candidate_chains.get(handle, ())
        relations = _annotation_chain_relations(
            candidate,
            nearby_chains,
            budget=budget,
        )
        illegal_targets = _illegal_annotation_targets(
            candidate,
            relations,
            indexes=indexes,
            budget=budget,
        )
        if len(relations) == 1 and len(illegal_targets) == 1:
            # It is geometrically associated with one established chain, but
            # crosses support faces, lies outside its required support/span
            # interval, or otherwise fails the full-bounds zone.  Its
            # concrete target is still proven by one exact interval relation,
            # not by proximity.
            illegal_target = illegal_targets[0]
            traces.bind(
                candidate.record,
                chain_id=illegal_target.chain_id,
                support_id=illegal_target.support_id,
                span_id=illegal_target.span_id,
            )
            findings.append(
                _finding(
                    category=category,
                    status="疑似不一致",
                    identity=candidate.record.identity_fingerprint,
                )
            )
        else:
            findings.append(
                _finding(
                    category=category,
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )


def _chains_artifact(chains: Sequence[_Chain]) -> list[dict[str, Any]]:
    return [
        {
            "chain_id": chain.chain_id,
            "supports": [
                {
                    "support_id": support.support_id,
                    "support_geometry_trace_id": derive_trace_id(
                        support.support.record.identity_fingerprint,
                        support.support.record.content_fingerprint,
                        "support_geometry",
                    ),
                }
                for support in chain.supports
            ],
            "spans": [
                {
                    "span_id": span.span_id,
                    "left_support_id": span.left.support_id,
                    "right_support_id": span.right.support_id,
                }
                for span in chain.spans
            ],
        }
        for chain in sorted(chains, key=lambda item: item.chain_id)
    ]


def _topology_artifact(
    *,
    findings: Iterable[dict[str, Any]],
    traces: _TraceBook,
    chains: Sequence[_Chain] = (),
) -> dict[str, Any]:
    rendered_traces = traces.render()
    traces_by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in rendered_traces:
        traces_by_identity[str(trace["identity_fingerprint"])].append(trace)

    rendered_by_id: dict[str, dict[str, Any]] = {}
    for original in findings:
        finding = dict(original)
        identity = str(finding.pop("_trace_identity", ""))
        matched = traces_by_identity.get(identity, ())
        # All normal topology findings originate from one source record.  If
        # that record cannot resolve to exactly one public trace, publishing
        # a partial artifact would silently discard evidence.
        if len(matched) != 1:
            raise PipelineError(
                ErrorCode.INTERNAL_ERROR,
                "topology finding does not resolve to exactly one trace",
            )
        trace = matched[0]
        category = str(finding["category"])
        expected_role = {
            "support_upper_annotation": "support_upper_annotations",
            "span_lower_annotation": "span_lower_annotations",
        }.get(category)
        if (
            expected_role is not None
            and finding["status"] != "证据不足"
            and trace["role"] != expected_role
        ):
            # Trace rendering can conservatively revoke a provisional target
            # after an assessment branch selected its status.  An unbound
            # ambiguity trace is evidence only of insufficiency, never of a
            # legal or uniquely illegal annotation position.
            finding = _finding(
                category=cast(
                    Literal[
                        "support_upper_annotation",
                        "span_lower_annotation",
                        "topology",
                    ],
                    category,
                ),
                status="证据不足",
                identity=identity,
            )
            # `_finding` retains this private key only until the original
            # record has been resolved to a public trace.  This path already
            # has that trace, so it must not re-enter the serialized artifact.
            finding.pop("_trace_identity")
        if category == "support_upper_annotation":
            if trace["role"] not in {"support_upper_annotations", "ambiguity"}:
                raise PipelineError(
                    ErrorCode.INTERNAL_ERROR,
                    "support finding resolved to an incompatible trace",
                )
        elif category == "span_lower_annotation":
            if trace["role"] not in {"span_lower_annotations", "ambiguity"}:
                raise PipelineError(
                    ErrorCode.INTERNAL_ERROR,
                    "span finding resolved to an incompatible trace",
                )
        finding["finding_id"] = derive_topology_finding_id(
            str(trace["trace_id"]),
            str(finding["status"]),
            str(trace["role"]),
            cast(str | None, trace["chain_id"]),
            cast(str | None, trace["support_id"]),
            cast(str | None, trace["span_id"]),
        )
        finding["trace_ids"] = [trace["trace_id"]]
        finding_id = str(finding["finding_id"])
        existing = rendered_by_id.get(finding_id)
        if existing is not None and existing != finding:
            raise PipelineError(
                ErrorCode.INTERNAL_ERROR,
                "duplicate topology finding provenance",
            )
        # Separate conservative gates can encounter the same source edge or
        # malformed annotation.  An identical canonical conclusion is one
        # finding, not a second publication entry; any material difference
        # above is an internal contradiction and fails closed.
        rendered_by_id[finding_id] = finding

    # Every rendered ambiguity is evidence of an unresolved relation.  A
    # chainless beam edge is likewise unresolved even when it retains its
    # concrete beam-edge role so that an admitted chain cannot be invented.
    # These public records require exactly one canonical insufficiency
    # conclusion.  This last publication gate makes a future soft-failure
    # path fail closed rather than dropping its finding during rendering.
    required_trace_ids = {
        str(trace["trace_id"])
        for trace in rendered_traces
        if trace["role"] == "ambiguity"
        or (
            trace["role"] == "beam_edges"
            and trace["chain_id"] is None
        )
    }
    findings_by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in rendered_by_id.values():
        findings_by_trace[str(finding["trace_ids"][0])].append(finding)
    for trace_id in sorted(required_trace_ids):
        covered = findings_by_trace.get(trace_id, ())
        if not covered:
            # Some source roles become ambiguity only after rendering revokes
            # an incomplete owner tuple.  Materialize their generic topology
            # conclusion here, while the source trace is still available,
            # instead of omitting that uncertainty from the artifact.
            trace = next(
                trace for trace in rendered_traces if trace["trace_id"] == trace_id
            )
            finding = _finding(
                category="topology",
                status="证据不足",
                identity=str(trace["identity_fingerprint"]),
            )
            finding.pop("_trace_identity")
            finding["finding_id"] = derive_topology_finding_id(
                trace_id,
                "证据不足",
                str(trace["role"]),
                cast(str | None, trace["chain_id"]),
                cast(str | None, trace["support_id"]),
                cast(str | None, trace["span_id"]),
            )
            finding["trace_ids"] = [trace_id]
            finding_id = str(finding["finding_id"])
            if finding_id in rendered_by_id:
                raise PipelineError(
                    ErrorCode.INTERNAL_ERROR,
                    "generated ambiguity finding collides with existing provenance",
                )
            rendered_by_id[finding_id] = finding
            findings_by_trace[trace_id].append(finding)
            covered = findings_by_trace[trace_id]
        if len(covered) != 1 or covered[0]["status"] != "证据不足":
            raise PipelineError(
                ErrorCode.INTERNAL_ERROR,
                "unresolved topology trace lacks one insufficiency finding",
            )

    return {
        "policy": POLICY_NAME,
        "mode": "read-only",
        "authorization": "topology-never-authorizes-edits",
        "privacy": "local-only",
        "findings": [rendered_by_id[key] for key in sorted(rendered_by_id)],
        "traces": rendered_traces,
        "chains": _chains_artifact(chains),
    }


def _assess_beam_topology(
    snapshot: Any,
    profile: TopologyProfile,
) -> dict[str, Any]:
    """Assess topology and semantic position without producing edit targets.

    Every branch is conservative.  A profile role entity that cannot provide
    the exact primitive required by the fixed policy yields an insufficient
    finding; a configured complexity cap yields a redacted hard failure; and
    no path creates an actionable target or mutation instruction.
    """

    _validate_runtime_profile(profile)
    records = tuple(getattr(snapshot, "records", ()))
    role_records: list[tuple[Any, Role]] = []
    for record in records:
        layer = _record_layer(record)
        if layer is None:
            continue
        role = profile.roles.role_for(layer)
        if role is not None and _semantic_role_record(record):
            role_records.append((record, role))
    if len(role_records) > MAX_ROLE_ENTITIES:
        raise _limit_error()
    if any(
        evidence is not None and evidence.numeric_limit_exceeded
        for record, _role in role_records
        for evidence in (_record_evidence(record),)
    ):
        raise _limit_error()
    # Extraction has already refused any oversized LWPOLYLINE before
    # iteration.  Charge the remaining materialized vertices to the same
    # assessment-wide fixed policy so no later role path can accumulate
    # partial primitive state.
    processed_vertices = sum(
        len(evidence.vertices)
        for record, _role in role_records
        for evidence in (_record_evidence(record),)
        if evidence is not None
    )
    if processed_vertices > MAX_TOPOLOGY_VERTICES:
        raise _limit_error()

    # A single budget belongs to the entire assessment, not an individual
    # phase.  Exhaustion therefore aborts before an audit artifact can expose
    # a partial topology conclusion.
    budget = RelationBudget()
    traces = _TraceBook()
    findings: list[dict[str, Any]] = []
    edge_segments: list[_Segment] = []
    supports: list[_Support] = []
    text_candidates: list[_TextCandidate] = []
    text_overlap_inventory: list[_TextOverlapEntry] = []
    leaders: list[_Leader] = []
    structural_invalid = False
    invalid_annotation_handles: set[str] = set()
    invalid_leader_present = False

    for record, role in sorted(
        role_records,
        key=lambda item: (
            str(item[0].layout),
            int(str(item[0].handle), 16),
            item[1],
        ),
    ):
        # A configured source role is relevant only while constructing the
        # private topology.  Public registered supports use one canonical
        # role so their trace is an exact, role-independent provenance anchor.
        traces.add(
            record,
            "support_geometry" if role in _SUPPORT_ROLES else role,
        )
        if role == "beam_edges":
            segment = _segment_for_role(record)
            if segment is None:
                structural_invalid = True
                findings.append(
                    _finding(
                        category="topology",
                        status="证据不足",
                        identity=record.identity_fingerprint,
                    )
                )
            else:
                edge_segments.append(segment)
        elif role in _SUPPORT_ROLES:
            support = _support_for_record(record, role)
            if support is None:
                structural_invalid = True
                findings.append(
                    _finding(
                        category="topology",
                        status="证据不足",
                        identity=record.identity_fingerprint,
                    )
                )
            else:
                supports.append(support)
        elif role in {"beam_ids", *tuple(_ANNOTATION_ROLES)}:
            overlap_entry = _visible_text_overlap_entry(record, role)
            if overlap_entry is not None:
                # This occurs before alignment, token grammar, and semantic
                # role eligibility can remove the TEXT from later stages.
                text_overlap_inventory.append(overlap_entry)
            candidate = _text_candidate(record, role)
            if candidate is None:
                if role in _ANNOTATION_ROLES:
                    invalid_annotation_handles.add(str(record.handle))
                    category = _annotation_category(role)
                else:
                    category = "topology"
                findings.append(
                    _finding(
                        category=category,
                        status="证据不足",
                        identity=record.identity_fingerprint,
                    )
                )
            else:
                text_candidates.append(candidate)
        elif role == "leaders":
            leader = _leader_for_record(record)
            if leader is None:
                invalid_leader_present = True
                findings.append(
                    _finding(
                        category="topology",
                        status="证据不足",
                        identity=record.identity_fingerprint,
                    )
                )
            else:
                leaders.append(leader)

    if (
        len(edge_segments) > MAX_ROLE_ENTITIES
        or len(supports) > MAX_SUPPORTS
        or len(text_candidates) > MAX_ANNOTATIONS
        or len(text_overlap_inventory) > MAX_ANNOTATIONS
        or len(leaders) > MAX_LEADERS
    ):
        raise _limit_error()

    # Any malformed structural role object prevents using a partial beam model.
    # Annotation-only failures remain local to their candidate, but cannot
    # make a malformed beam/support graph appear well formed.
    scale = _stable_scale(edge_segments)
    if structural_invalid or scale is None or len(edge_segments) < 2:
        _complete_incomplete_topology_findings(findings, traces)
        return _topology_artifact(findings=findings, traces=traces)

    axis_discovery = _build_axes(edge_segments, scale, budget=budget)
    axes = axis_discovery.axes
    unresolved_geometry = axis_discovery.unresolved
    for unresolved in unresolved_geometry:
        # A configured valid edge that did not mutually pair remains
        # controlled evidence.  Its bounds/provenance stay private, while the
        # public artifact records only that topology is insufficient.
        findings.append(
            _finding(
                category="topology",
                status="证据不足",
                identity=unresolved.provenance,
            )
        )
    if not axes:
        # No mutual axes is itself enough to prevent every otherwise valid
        # controlled record from being classified or partially bound.
        _complete_incomplete_topology_findings(findings, traces)
        return _topology_artifact(findings=findings, traces=traces)

    blocked_handles = _overlap_blocked_handles(
        text_overlap_inventory,
        scale=scale,
        findings=findings,
        budget=budget,
    )
    id_candidates = [
        candidate for candidate in text_candidates if candidate.role == "beam_ids"
    ]
    axis_ids = _bind_axis_ids(
        axes,
        id_candidates,
        blocked_handles,
        traces,
        findings,
        axis_index=_SpatialIndex(
            axes,
            tuple(_axis_id_corridor_bounds(axis) for axis in axes),
            cell_size=max(scale, _EPSILON),
        ),
        budget=budget,
    )
    if invalid_leader_present:
        # A configured leader layer is optional only when it contains no
        # relevant evidence.  A malformed visible leader prevents us from
        # treating a nearby valid label as unambiguously un-led.
        for candidate in text_candidates:
            if candidate.role in _ANNOTATION_ROLES:
                invalid_annotation_handles.add(str(candidate.record.handle))
    chains = _build_chains(
        axes,
        axis_ids,
        supports,
        scale=scale,
        traces=traces,
        findings=findings,
        support_index=_SpatialIndex(
            supports,
            tuple(support.bounds for support in supports),
            cell_size=max(scale, _EPSILON),
        ),
        budget=budget,
    )
    orphaned_axis_geometry = _orphaned_axis_geometry(
        axes,
        chains,
        budget=budget,
    )
    # Paired geometry that did not become exactly one admitted chain is not
    # discarded after ID/chain admission.  It remains a private blocker for
    # labels that could plausibly refer to it.
    unresolved_geometry = (*unresolved_geometry, *orphaned_axis_geometry)
    insufficient_identities = {
        str(finding.get("_trace_identity", ""))
        for finding in findings
        if finding.get("status") == "证据不足"
    }
    for provenance in sorted({item.provenance for item in orphaned_axis_geometry}):
        # A source edge can be both unpaired during discovery and present in
        # a later rejected aggregate path.  It still has one canonical trace
        # and therefore exactly one insufficiency finding.
        if provenance in insufficient_identities:
            continue
        findings.append(
            _finding(
                category="topology",
                status="证据不足",
                identity=provenance,
            )
        )
        insufficient_identities.add(provenance)
    if not chains:
        # Missing IDs, supports, or spans have the same publication rule:
        # every controlled input is target-free ambiguity with exactly one
        # trace-bound insufficiency conclusion.
        _complete_incomplete_topology_findings(findings, traces)
        return _topology_artifact(findings=findings, traces=traces)

    # Retain invalid annotation records as insufficient without trying to
    # recover their text/geometry.  Valid candidates proceed through the
    # overlap, full-token, full-bounds, and optional-leader gates.
    for candidate in text_candidates:
        if (
            candidate.role in _ANNOTATION_ROLES
            and str(candidate.record.handle) in invalid_annotation_handles
        ):
            findings.append(
                _finding(
                    category=_annotation_category(candidate.role),
                    status="证据不足",
                    identity=candidate.record.identity_fingerprint,
                )
            )
    valid_annotation_candidates = [
        candidate
        for candidate in text_candidates
        if candidate.role in _ANNOTATION_ROLES
        and str(candidate.record.handle) not in invalid_annotation_handles
    ]
    indexes = _build_topology_indexes(
        chains=chains,
        supports=supports,
        leaders=leaders,
        unresolved=unresolved_geometry,
        scale=scale,
    )
    _bind_annotations(
        valid_annotation_candidates,
        blocked_handles=blocked_handles,
        traces=traces,
        findings=findings,
        indexes=indexes,
        budget=budget,
    )
    return _topology_artifact(findings=findings, traces=traces, chains=chains)


def assess_beam_topology(
    snapshot: Any,
    profile: TopologyProfile,
) -> dict[str, Any]:
    """Run topology assessment without exposing numeric implementation errors.

    The input policy admits only a range where normal topology arithmetic is
    safe.  The catch is still mandatory because parser- and library-provided
    finite values can trigger overflow during derived geometry; an audit must
    fail as a redacted topology limit before a partial assessment is built.
    """

    try:
        return _assess_beam_topology(snapshot, profile)
    except (OverflowError, ValueError) as error:
        raise _limit_error() from error
