"""Read-only DXF snapshots used only inside the DWG pipeline and tests."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import io
from math import isfinite
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
import unicodedata

import ezdxf
from ezdxf import bbox
from ezdxf.colors import float2transparency
from ezdxf.entities.dxfclass import DXFClass
from ezdxf.enums import TextEntityAlignment
from ezdxf.lldxf import validator
from ezdxf.lldxf.tagwriter import TagCollector

from .canonical import canonical_sha256, normalize_json_value
from .contracts import ordered_entity_sequence_digest, state_from_manifest
from .errors import ErrorCode, PipelineError
from .raw_dxf import (
    RawDxfPreflight,
    assert_normalized_header_match,
    assert_normalized_records_match,
    preflight_ascii_dxf_bytes,
    read_bounded_dxf_chunks,
)
from .ownership import (
    FileOwnershipBackend,
    OwnedPath,
    OwnedPathBinding,
    OwnershipCleanupError,
    OwnershipError,
    OwnershipLostError,
    platform_backend,
)

if TYPE_CHECKING:
    from .topology_profile import TopologyEntityEvidence, TopologySnapshotContext


SUPPORTED_ENTITY_TYPES = frozenset(
    {"TEXT", "LINE", "LWPOLYLINE", "INSERT", "DIMENSION", "HATCH"}
)
PLANE_TOLERANCE = 1e-6
_TOLERANCE = PLANE_TOLERANCE
_LAYER_FROZEN_IN_NEW_VIEWPORT = 2
_NON_DISPLAYABLE_LAYER_FLAGS = 1 | _LAYER_FROZEN_IN_NEW_VIEWPORT
_SUPPORTED_LAYER_FLAGS = 1 | 2 | 4 | 16 | 32 | 64


def _normalized_layer_key(value: str) -> str:
    """Match the profile's NFC/casefolded layer namespace."""

    return unicodedata.normalize("NFC", value).casefold()

# Only tool-managed identity/allocation and elapsed-time fields are excluded.
# Representation, content, coordinate, unit, version, and display settings
# (for example $LTSCALE, $INSUNITS, $COORDS, $ACADVER, and $UCS*) remain bound.
VOLATILE_HEADER_VARIABLES = frozenset(
    {
        # Generated ODA 27.1 R2018 output carries maintenance release 55;
        # ezdxf 1.4.4 writes its own 4. Both remain AC1032/R2018 and have no
        # representation effect, unlike $ACADVER and display settings.
        "$ACADMAINTVER",
        "$HANDSEED",
        "$TDCREATE",
        "$TDUPDATE",
        "$TDUUPDATE",
        "$TDINDWG",
        "$TDUSRTIMER",
        "$VERSIONGUID",
        "$FINGERPRINTGUID",
        "$LASTSAVEDBY",
    }
)

# ezdxf updates this one value on every DXF write.  The object identity,
# ownership, dictionary relationship, and every other tag remain bound.
VOLATILE_OBJECT_TAG_ALLOWLIST = frozenset(
    {("EZDXF_META", "WRITTEN_BY_EZDXF", 1)}
)

# These are every OBJECTS-section type modeled by the pinned ezdxf version.
# Their ordered raw tags are captured below, including handles, owners, XDATA,
# appdata, and extension-dictionary references. Unknown/proxy object types fail
# closed rather than being omitted from the preservation state.
SUPPORTED_OBJECT_TYPES = frozenset(
    {
        "ACDBDICTIONARYWDFLT",
        "ACDBDETAILVIEWSTYLE",
        "ACDBPLACEHOLDER",
        "ACDBSECTIONVIEWSTYLE",
        "CELLSTYLEMAP",
        "DGNDEFINITION",
        "DICTIONARY",
        "DICTIONARYVAR",
        "DWFDEFINITION",
        "FIELDLIST",
        "GEODATA",
        "GROUP",
        "IDBUFFER",
        "IMAGEDEF",
        "IMAGEDEF_REACTOR",
        "LAYER_FILTER",
        "LAYOUT",
        "MATERIAL",
        "MLEADERSTYLE",
        "MLINESTYLE",
        "PDFDEFINITION",
        "PLOTSETTINGS",
        "RASTERVARIABLES",
        "SCALE",
        "SORTENTSTABLE",
        "SPATIAL_FILTER",
        "SUN",
        "TABLESTYLE",
        "UNDERLAYDEFINITION",
        "VBA_PROJECT",
        "VISUALSTYLE",
        "WIPEOUTVARIABLES",
        "XRECORD",
    }
)


@dataclass(frozen=True)
class Bounds:
    """A private in-memory bounding box; coordinates never enter artifacts."""

    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    def contains_xy(self, point: tuple[float, float], tolerance: float = 1e-6) -> bool:
        return (
            self.minimum[0] - tolerance <= point[0] <= self.maximum[0] + tolerance
            and self.minimum[1] - tolerance <= point[1] <= self.maximum[1] + tolerance
        )

    def contains_bounds_xy(self, other: "Bounds", tolerance: float = 1e-6) -> bool:
        """Require an entire axis-aligned geometry to be inside this rectangle."""

        return (
            self.minimum[0] - tolerance <= other.minimum[0]
            and other.maximum[0] <= self.maximum[0] + tolerance
            and self.minimum[1] - tolerance <= other.minimum[1]
            and other.maximum[1] <= self.maximum[1] + tolerance
        )

    def overlaps(self, other: "Bounds", tolerance: float = 1e-6) -> bool:
        return not (
            self.maximum[0] + tolerance < other.minimum[0]
            or other.maximum[0] + tolerance < self.minimum[0]
            or self.maximum[1] + tolerance < other.minimum[1]
            or other.maximum[1] + tolerance < self.minimum[1]
        )

    def translated(self, dx: float, dy: float) -> "Bounds":
        return Bounds(
            (
                self.minimum[0] + dx,
                self.minimum[1] + dy,
                self.minimum[2],
            ),
            (
                self.maximum[0] + dx,
                self.maximum[1] + dy,
                self.maximum[2],
            ),
        )


@dataclass(frozen=True)
class EntityRecord:
    """A normalized entity manifest record plus private profile-only geometry."""

    handle: str
    entity_type: str
    layout: str
    sequence_index: int
    container_fingerprint: str
    owner_fingerprint: str
    layer_fingerprint: str
    identity_fingerprint: str
    content_fingerprint: str
    layer_name: str
    entity_visible: bool
    layer_visible: bool
    entity_transparency: int | None
    layer_transparency: float
    plane_elevation: float | None
    anchor: tuple[float, float] | None
    bounds: Bounds | None
    # Optional v2-only data remains private to the in-memory snapshot.  It is
    # deliberately omitted from ``public()`` so an audit without a topology
    # profile remains byte/semantic audit/v1-compatible.
    topology_evidence: TopologyEntityEvidence | None = None

    @property
    def visible(self) -> bool:
        """Return whether this entity can provide visible overlay evidence."""

        return (
            self.entity_visible
            and self.layer_visible
            and _entity_transparency_is_opaque(self.entity_transparency)
            and _layer_transparency_is_opaque(self.layer_transparency)
        )

    def public(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "entity_type": self.entity_type,
            "layout": self.layout,
            "sequence_index": self.sequence_index,
            "container_fingerprint": self.container_fingerprint,
            "owner_fingerprint": self.owner_fingerprint,
            "layer_fingerprint": self.layer_fingerprint,
            "identity_fingerprint": self.identity_fingerprint,
            "content_fingerprint": self.content_fingerprint,
            "entity_visible": self.entity_visible,
            "layer_visible": self.layer_visible,
            "entity_transparency": self.entity_transparency,
            "layer_transparency": self.layer_transparency,
        }


@dataclass(frozen=True)
class LayerVisualState:
    """One modeled layer display state shared by all records on the layer."""

    visible: bool
    transparency: float


@dataclass(frozen=True)
class Snapshot:
    """Immutable, full-document preservation snapshot."""

    records: tuple[EntityRecord, ...]
    layer_manifest_digest: str
    table_style_manifest_digest: str
    header_manifest_digest: str
    raw_header_manifest_digest: str
    objects_manifest_digest: str
    classes_manifest_digest: str
    raw_classes_manifest_digest: str
    raw_classes_multiset_digest: str
    raw_classes_record_count: int
    acdsdata_manifest_digest: str
    raw_section_structure_digest: str
    bounds_fingerprint: str
    bounds_has_data: bool

    @property
    def manifest(self) -> list[dict[str, Any]]:
        return [record.public() for record in self.records]

    @property
    def records_by_handle(self) -> dict[str, EntityRecord]:
        return {record.handle: record for record in self.records}

    def inventory(self) -> dict[str, Any]:
        manifest = self.manifest
        entity_counts = Counter(record.entity_type for record in self.records)
        layer_counts = Counter(record.layer_fingerprint for record in self.records)
        layout_counts = Counter(record.layout for record in self.records)
        return {
            "entity_manifest": manifest,
            "entity_type_counts": [
                {"entity_type": name, "count": entity_counts[name]}
                for name in sorted(entity_counts)
            ],
            "layer_counts": [
                {"layer_fingerprint": name, "count": layer_counts[name]}
                for name in sorted(layer_counts)
            ],
            "layout_counts": [
                {"layout": name, "count": layout_counts[name]}
                for name in sorted(layout_counts)
            ],
            "entity_order_manifest_digest": ordered_entity_sequence_digest(manifest),
            "layer_manifest_digest": self.layer_manifest_digest,
            "table_style_manifest_digest": self.table_style_manifest_digest,
            "header_manifest_digest": self.header_manifest_digest,
            "raw_header_manifest_digest": self.raw_header_manifest_digest,
            "objects_manifest_digest": self.objects_manifest_digest,
            "classes_manifest_digest": self.classes_manifest_digest,
            "raw_classes_manifest_digest": self.raw_classes_manifest_digest,
            "raw_classes_multiset_digest": self.raw_classes_multiset_digest,
            "raw_classes_record_count": self.raw_classes_record_count,
            "acdsdata_manifest_digest": self.acdsdata_manifest_digest,
            "raw_section_structure_digest": self.raw_section_structure_digest,
        }

    def preservation_state(
        self,
        *,
        excluded_handles: set[str] | None = None,
        paired_right_panel_digest: str,
    ) -> dict[str, Any]:
        excluded = excluded_handles or set()
        manifest = [
            record.public() for record in self.records if record.handle not in excluded
        ]
        return state_from_manifest(
            manifest,
            paired_right_panel_digest=paired_right_panel_digest,
            bounds_fingerprint=self.bounds_fingerprint,
            bounds_has_data=self.bounds_has_data,
            layer_manifest_digest=self.layer_manifest_digest,
            table_style_manifest_digest=self.table_style_manifest_digest,
            header_manifest_digest=self.header_manifest_digest,
            raw_header_manifest_digest=self.raw_header_manifest_digest,
            objects_manifest_digest=self.objects_manifest_digest,
            classes_manifest_digest=self.classes_manifest_digest,
            raw_classes_manifest_digest=self.raw_classes_manifest_digest,
            raw_classes_multiset_digest=self.raw_classes_multiset_digest,
            raw_classes_record_count=self.raw_classes_record_count,
            acdsdata_manifest_digest=self.acdsdata_manifest_digest,
            raw_section_structure_digest=self.raw_section_structure_digest,
        )


def _fingerprint_text(value: str) -> str:
    return canonical_sha256({"value": value})


def _normalize_tag_value(value: Any) -> Any:
    """Normalize DXF tag scalars without allowing arbitrary object repr values."""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return normalize_json_value(value)
    if isinstance(value, bytes):
        return {"bytes_sha256": canonical_sha256({"payload": value.hex()})}
    if isinstance(value, (tuple, list)):
        return [_normalize_tag_value(item) for item in value]
    if all(hasattr(value, part) for part in ("x", "y", "z")):
        return [float(value.x), float(value.y), float(value.z)]
    raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "unsupported DXF tag value")


def _tag_fingerprint(
    entity: Any,
    dxfversion: str,
    *,
    include_handles: bool = False,
    excluded_tag_codes: frozenset[int] = frozenset(),
    volatile_dictionary_reference_keys: frozenset[str] = frozenset(),
) -> str:
    """Fingerprint ordered exported tags, optionally binding object handles."""

    collector = TagCollector(dxfversion=dxfversion)
    entity.export_dxf(collector)
    tags: list[list[Any]] = []
    pending_dictionary_key: str | None = None
    for tag in collector.tags:
        if tag.code == 5 and not include_handles:
            continue
        if tag.code in excluded_tag_codes:
            continue
        if tag.code == 3:
            pending_dictionary_key = str(tag.value)
            tags.append([tag.code, _normalize_tag_value(tag.value)])
            continue
        if (
            tag.code in {350, 360}
            and pending_dictionary_key in volatile_dictionary_reference_keys
        ):
            tags.append(
                [
                    tag.code,
                    {"volatile_dictionary_reference": pending_dictionary_key},
                ]
            )
        else:
            tags.append([tag.code, _normalize_tag_value(tag.value)])
        if tag.code not in {102, 350, 360}:
            pending_dictionary_key = None
    return canonical_sha256({"entity_type": entity.dxftype(), "tags": tags})


def _export_tag_manifest(
    exporter: Any,
    dxfversion: str,
    *,
    section_name: str,
) -> list[list[Any]]:
    """Return all serializable tags or reject an incomplete section exporter.

    The resulting manifest remains local to snapshot construction and is
    immediately digested by callers.  It is never placed in an artifact,
    report, or log because it can contain private metadata or binary payloads.
    """

    collector = TagCollector(dxfversion=dxfversion)
    try:
        exporter.export_dxf(collector)
    except Exception as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, f"{section_name} cannot be serialized"
        ) from error
    tags: list[list[Any]] = []
    try:
        for tag in collector.tags:
            if not isinstance(tag.code, int):
                raise TypeError("DXF tag code is not an integer")
            tags.append([tag.code, _normalize_tag_value(tag.value)])
    except (AttributeError, TypeError, ValueError, PipelineError) as error:
        if isinstance(error, PipelineError):
            raise
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, f"{section_name} has unsupported tags"
        ) from error
    return tags


_CLASS_TAG_SEQUENCE = (0, 1, 2, 3, 90, 91, 280, 281)


def _normalized_class_records(
    document: Any,
) -> dict[tuple[str, str], tuple[tuple[int, Any], ...]]:
    """Export the narrow supported CLASS model without silently filling gaps."""

    try:
        classes = document.classes.classes
        class_items = list(classes.items())
    except (AttributeError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "CLASSES section is unavailable"
        ) from error

    records: dict[tuple[str, str], tuple[tuple[int, Any], ...]] = {}
    names: set[str] = set()
    for key, dxf_class in class_items:
        if (
            not isinstance(dxf_class, DXFClass)
            or not isinstance(key, tuple)
            or len(key) != 2
            or not all(isinstance(part, str) for part in key)
        ):
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "unsupported CLASSES section content"
            )
        tags = _export_tag_manifest(
            dxf_class, document.dxfversion, section_name="CLASSES record"
        )
        if (
            len(tags) != len(_CLASS_TAG_SEQUENCE)
            or tuple(tag[0] for tag in tags) != _CLASS_TAG_SEQUENCE
            or tags[0] != [0, "CLASS"]
            or not all(isinstance(tags[index][1], str) and tags[index][1] for index in (1, 2, 3))
            or not all(
                isinstance(tags[index][1], int) and not isinstance(tags[index][1], bool)
                for index in (4, 5, 6, 7)
            )
            or any(tags[index][1] not in (0, 1) for index in (6, 7))
        ):
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "malformed CLASSES record"
            )
        try:
            name = str(dxf_class.dxf.name)
            cpp_class_name = str(dxf_class.dxf.cpp_class_name)
        except (AttributeError, TypeError, ValueError) as error:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "CLASSES record metadata is unavailable"
            ) from error
        identity = (name, cpp_class_name)
        if (
            key != identity
            or identity in records
            or name in names
            or (tags[1][1], tags[2][1]) != identity
        ):
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "CLASSES record registration is invalid"
            )
        names.add(name)
        records[identity] = tuple((int(code), value) for code, value in tags)
    return records


def _classes_manifest_digest(
    records: Mapping[tuple[str, str], tuple[tuple[int, Any], ...]],
) -> str:
    """Hash normalized CLASS content after a raw-to-model bijection succeeds."""

    return canonical_sha256(
        {
            "section": "CLASSES",
            "records": sorted(
                canonical_sha256(
                    {"tags": [[code, value] for code, value in tags]}
                )
                for tags in records.values()
            ),
        }
    )


def _assert_raw_classes_match(
    raw_preflight: RawDxfPreflight,
    normalized_records: Mapping[tuple[str, str], tuple[tuple[int, Any], ...]],
) -> None:
    """Require a one-to-one exact semantic match before using normalized data."""

    if (
        raw_preflight.classes_record_count != len(normalized_records)
        or len(raw_preflight.classes) != len(normalized_records)
    ):
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "raw CLASSES normalization mismatch"
        )
    raw_identities = {record.identity for record in raw_preflight.classes}
    if raw_identities != set(normalized_records):
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "raw CLASSES normalization mismatch"
        )
    for raw_record in raw_preflight.classes:
        if normalized_records[raw_record.identity] != raw_record.normalized_tags:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "raw CLASSES normalization mismatch"
            )


def _acdsdata_manifest_digest(document: Any) -> str:
    """Represent only the canonical empty ACDSDATA state.

    Raw preflight has already proven an on-disk ACDSDATA section, if present,
    is the exact empty three-tag representation.  This release intentionally
    rejects records rather than risk partial preservation of binary metadata.
    """

    try:
        acdsdata = document.acdsdata
        entities = list(acdsdata.entities)
        has_records = bool(acdsdata.has_records)
    except (AttributeError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "ACDSDATA section is unavailable"
        ) from error

    if entities or has_records:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "nonempty ACDSDATA is unsupported"
        )
    return canonical_sha256({"section": "ACDSDATA", "state": "canonical-empty"})


def _has_only_modeled_layer_transparency(layer: Any) -> bool:
    """Accept only ezdxf's exact AcCmTransparency layer XDATA representation."""

    xdata = getattr(layer, "xdata", None)
    if xdata is None:
        return True
    try:
        data = xdata.data
        tags = data.get("AcCmTransparency")
    except (AttributeError, TypeError, ValueError):
        return False
    if (
        not isinstance(data, Mapping)
        or set(data) != {"AcCmTransparency"}
        or not isinstance(tags, list)
        or len(tags) != 2
    ):
        return False
    first, second = tags
    try:
        value = second.value
        return (
            int(first.code) == 1001
            and first.value == "AcCmTransparency"
            and int(second.code) == 1071
            and isinstance(value, int)
            and not isinstance(value, bool)
            and validator.is_transparency(value)
            and (value & 0xFF000000) == 0x02000000
            and (value & 0x00FFFF00) == 0
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _assert_safe_object(entity: Any) -> None:
    """Reject data forms that the narrow preservation model does not support."""

    if (
        getattr(entity, "xdata", None) is not None
        and (
            entity.dxftype() != "LAYER"
            or not _has_only_modeled_layer_transparency(entity)
        )
    ):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "entity has XDATA")
    if getattr(entity, "appdata", None) is not None:
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "entity has application data")
    if bool(getattr(entity, "has_extension_dict", False)):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "entity has extension dictionary")
    if getattr(entity, "proxy_graphic", None):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "entity has proxy graphics")


def _entity_transparency(entity: Any) -> int | None:
    """Return one validated raw entity transparency setting, if explicitly set."""

    try:
        if not entity.dxf.hasattr("transparency"):
            return None
        value = entity.dxf.get("transparency")
    except (AttributeError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "entity transparency is unavailable"
        ) from error
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not validator.is_transparency(value)
    ):
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "entity transparency is unsupported"
        )
    return value


def _entity_transparency_is_opaque(value: int | None) -> bool:
    """Accept default/by-layer and explicit opaque settings only."""

    return value is None or value == float2transparency(0.0)


def _layer_transparency(layer: Any) -> float:
    """Return the validated modeled layer transparency as a normalized float."""

    if not _has_only_modeled_layer_transparency(layer):
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "layer transparency is unsupported"
        )
    try:
        value = float(layer.transparency)
    except (AttributeError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "layer transparency is unavailable"
        ) from error
    if not isfinite(value) or value < 0.0 or value > 1.0:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "layer transparency is unsupported"
        )
    return value


def _layer_transparency_is_opaque(value: float) -> bool:
    """Treat every nonzero layer transparency as non-actionable evidence."""

    return abs(value) <= _TOLERANCE


def _to_point(value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if all(hasattr(value, coordinate) for coordinate in ("x", "y", "z")):
        return (float(value.x), float(value.y), float(value.z))
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        third = float(value[2]) if len(value) >= 3 else 0.0
        return (float(value[0]), float(value[1]), third)
    return None


def _finite_scalar(value: Any) -> float | None:
    """Return a finite scalar or reject unsupported DXF scalar representations."""

    if isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if isfinite(converted) else None


def _has_default_xy_extrusion(entity: Any) -> bool:
    """Support only the explicit/default OCS normal used by the profile."""

    try:
        extrusion = _to_point(entity.dxf.get("extrusion", (0.0, 0.0, 1.0)))
    except (AttributeError, TypeError, ValueError):
        return False
    return extrusion is not None and (
        abs(extrusion[0]) <= _TOLERANCE
        and abs(extrusion[1]) <= _TOLERANCE
        and abs(extrusion[2] - 1.0) <= _TOLERANCE
    )


def _entity_tag_values(entity: Any, dxfversion: str, code: int) -> list[Any]:
    """Return raw values for a DXF tag code without discarding extension tags."""

    collector = TagCollector(dxfversion=dxfversion)
    entity.export_dxf(collector)
    return [tag.value for tag in collector.tags if tag.code == code]


def _text_elevation_matches_insert(
    entity: Any,
    dxfversion: str,
    insertion_z: float,
) -> bool:
    """Bind a legacy TEXT elevation tag when one is present.

    R2018 TEXT normally carries its plane in the insertion point.  If an
    elevation tag is present, accepting it without binding it to that point
    would let conflicting OCS data authorize a deletion.
    """

    elevation_tags = _entity_tag_values(entity, dxfversion, 38)
    if len(elevation_tags) > 1:
        return False
    if not elevation_tags:
        return True
    elevation = _finite_scalar(elevation_tags[0])
    return elevation is not None and abs(elevation - insertion_z) <= _TOLERANCE


def _line_geometry(entity: Any) -> tuple[Bounds | None, float | None]:
    """Return bounds and a trusted plane only for simple planar LINE evidence."""

    try:
        start = _to_point(entity.dxf.start)
        end = _to_point(entity.dxf.end)
        thickness = _finite_scalar(entity.dxf.get("thickness", 0.0))
    except (AttributeError, TypeError, ValueError):
        return None, None
    if (
        start is None
        or end is None
        or not all(isfinite(value) for value in (*start, *end))
    ):
        return None, None
    bounds = Bounds(
        tuple(min(start[index], end[index]) for index in range(3)),
        tuple(max(start[index], end[index]) for index in range(3)),
    )
    if (
        thickness is None
        or abs(thickness) > _TOLERANCE
        or not _has_default_xy_extrusion(entity)
        or abs(start[2] - end[2]) > _TOLERANCE
    ):
        return bounds, None
    return bounds, start[2]


def _supported_text_geometry(
    entity: Any,
    dxfversion: str,
) -> tuple[Bounds | None, float | None]:
    """Return TEXT bounds and plane only for the deliberately supported OCS."""

    try:
        alignment = entity.get_align_enum()
        rotation = float(entity.dxf.get("rotation", 0.0))
        oblique = float(entity.dxf.get("oblique", 0.0))
        width_factor = float(entity.dxf.get("width", 1.0))
        height = float(entity.dxf.get("height", 0.0))
        thickness = _finite_scalar(entity.dxf.get("thickness", 0.0))
        insertion = _to_point(entity.dxf.get("insert"))
    except (AttributeError, TypeError, ValueError):
        return None, None
    rotation_from_zero = min(rotation % 360.0, 360.0 - (rotation % 360.0))
    if (
        alignment != TextEntityAlignment.LEFT
        or rotation_from_zero > _TOLERANCE
        or abs(oblique) > _TOLERANCE
        or width_factor <= 0.0
        or height <= 0.0
        or thickness is None
        or abs(thickness) > _TOLERANCE
        or insertion is None
        or not all(
            isfinite(value)
            for value in (rotation, oblique, width_factor, height, *insertion)
        )
        or not _has_default_xy_extrusion(entity)
        or not _text_elevation_matches_insert(entity, dxfversion, insertion[2])
    ):
        return None, None
    try:
        text_bounds = bbox.extents([entity])
    except Exception:
        return None, None
    if not text_bounds.has_data:
        return None, None
    minimum = _to_point(text_bounds.extmin)
    maximum = _to_point(text_bounds.extmax)
    if (
        minimum is None
        or maximum is None
        or not all(isfinite(value) for value in (*minimum[:2], *maximum[:2]))
    ):
        return None, None
    # bbox.extents() may report its own Z convention for planar TEXT.  The
    # supported OCS plane is the insertion Z, so bind the private bounds to it.
    return (
        Bounds(
            (minimum[0], minimum[1], insertion[2]),
            (maximum[0], maximum[1], insertion[2]),
        ),
        insertion[2],
    )


def _entity_geometry(
    entity: Any,
    dxfversion: str,
) -> tuple[Bounds | None, float | None]:
    """Extract private profile geometry and a trusted planar elevation."""

    entity_type = entity.dxftype()
    if entity_type == "LINE":
        return _line_geometry(entity)
    if entity_type == "TEXT":
        return _supported_text_geometry(entity, dxfversion)
    return None, None


def _entity_anchor(entity: Any) -> tuple[float, float] | None:
    if entity.dxftype() != "TEXT":
        return None
    point = _to_point(entity.dxf.get("insert"))
    if point is None:
        return None
    return (point[0], point[1])


def _entity_is_visible(entity: Any) -> bool:
    """Interpret DXF group 60 conservatively for every graphic evidence type."""

    try:
        invisible = _finite_scalar(entity.dxf.get("invisible", 0))
    except (AttributeError, TypeError, ValueError):
        return False
    return invisible is not None and invisible == 0.0


def _layer_is_displayable(layer: Any) -> bool:
    """Require a visible, unfrozen layer under the supported DXF flags.

    Bit 1 freezes a layer and bit 2 freezes it in new viewports.  The latter
    cannot be resolved without viewport state, so evidence on that layer is
    conservatively non-displayable.  Locked and xref flags remain displayable;
    unsupported flag bits fail closed.
    """

    try:
        flags = int(layer.dxf.get("flags", 0))
        color = int(layer.dxf.get("color", 0))
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        flags >= 0
        and not bool(flags & ~_SUPPORTED_LAYER_FLAGS)
        and color > 0
        and not bool(flags & _NON_DISPLAYABLE_LAYER_FLAGS)
    )


def _layer_visual_state_by_name(document: Any) -> dict[str, LayerVisualState]:
    """Build a case-insensitive, deterministic layer visual-state lookup."""

    visual_state: dict[str, LayerVisualState] = {}
    for layer in document.layers:
        name = str(layer.dxf.get("name", ""))
        if not name:
            raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "layer lacks a name")
        key = _normalized_layer_key(name)
        if key in visual_state:
            raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "duplicate layer name")
        visual_state[key] = LayerVisualState(
            visible=_layer_is_displayable(layer),
            transparency=_layer_transparency(layer),
        )
    return visual_state


def _record(
    entity: Any,
    *,
    layout: str,
    sequence_index: int,
    container_name: str,
    dxfversion: str,
    layer_visual_state: Mapping[str, LayerVisualState] | None = None,
    include_topology_evidence: bool = False,
    topology_context: "TopologySnapshotContext | None" = None,
) -> EntityRecord:
    entity_type = entity.dxftype()
    _assert_supported_entity(entity)
    handle = str(entity.dxf.get("handle", "")).upper()
    owner = str(entity.dxf.get("owner", ""))
    layer = str(entity.dxf.get("layer", ""))
    if not handle or not owner or not layer:
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "entity lacks stable ownership")
    layer_key = _normalized_layer_key(layer)
    if layer_visual_state is not None and layer_key not in layer_visual_state:
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "entity layer is unavailable")
    visual_state = (
        layer_visual_state[layer_key]
        if layer_visual_state is not None
        else LayerVisualState(visible=True, transparency=0.0)
    )
    content_fingerprint = _tag_fingerprint(entity, dxfversion)
    container_fingerprint = _fingerprint_text(container_name)
    owner_fingerprint = _fingerprint_text(owner)
    layer_fingerprint = _fingerprint_text(layer)
    identity_fingerprint = canonical_sha256(
        {
            "handle": handle,
            "entity_type": entity_type,
            "layout": layout,
            "container_fingerprint": container_fingerprint,
            "owner_fingerprint": owner_fingerprint,
            "content_fingerprint": content_fingerprint,
        }
    )
    bounds, plane_elevation = _entity_geometry(entity, dxfversion)
    topology_eligible = bool(
        include_topology_evidence
        and topology_context is not None
        and layout == "modelspace"
        and _entity_is_visible(entity)
        and visual_state.visible
        and _entity_transparency_is_opaque(_entity_transparency(entity))
        and _layer_transparency_is_opaque(visual_state.transparency)
        and layer_key in topology_context.role_layers
    )
    return EntityRecord(
        handle=handle,
        entity_type=entity_type,
        layout=layout,
        sequence_index=sequence_index,
        container_fingerprint=container_fingerprint,
        owner_fingerprint=owner_fingerprint,
        layer_fingerprint=layer_fingerprint,
        identity_fingerprint=identity_fingerprint,
        content_fingerprint=content_fingerprint,
        layer_name=layer,
        entity_visible=_entity_is_visible(entity),
        layer_visible=visual_state.visible,
        entity_transparency=_entity_transparency(entity),
        layer_transparency=visual_state.transparency,
        plane_elevation=plane_elevation,
        anchor=_entity_anchor(entity),
        bounds=bounds,
        topology_evidence=(
            _topology_evidence(entity, dxfversion)
            if topology_eligible
            else None
        ),
    )


def _topology_evidence(
    entity: Any,
    dxfversion: str,
) -> "TopologyEntityEvidence | None":
    """Extract v2-only primitives lazily so v1 never loads topology policy."""

    from .topology_profile import extract_topology_evidence

    return extract_topology_evidence(entity, dxfversion)


def _assert_topology_capture_limits(
    document: Any,
    *,
    layer_visual_state: Mapping[str, LayerVisualState],
    topology_context: "TopologySnapshotContext | None",
) -> None:
    """Reject over-cap eligible roles before materializing private evidence."""

    if topology_context is None:
        return
    from .topology_profile import (
        MAX_ANNOTATIONS,
        MAX_LEADERS,
        MAX_ROLE_ENTITIES,
        MAX_SUPPORTS,
        _ANNOTATION_ROLES,
        _SUPPORT_ROLES,
        _limit_error,
    )

    role_count = support_count = text_count = leader_count = 0
    for entity in document.modelspace():
        try:
            layer = str(entity.dxf.get("layer", ""))
            layer_key = _normalized_layer_key(layer)
            visual_state = layer_visual_state[layer_key]
        except (AttributeError, KeyError, TypeError, ValueError):
            # Structural validation/redaction remains owned by _record().
            continue
        if not (
            layer_key in topology_context.role_layers
            and _entity_is_visible(entity)
            and visual_state.visible
            and _entity_transparency_is_opaque(_entity_transparency(entity))
            and _layer_transparency_is_opaque(visual_state.transparency)
        ):
            continue
        role = topology_context.roles.role_for(layer_key)
        if role is None:
            continue
        role_count += 1
        if role in _SUPPORT_ROLES:
            support_count += 1
        elif role in {"beam_ids", *_ANNOTATION_ROLES}:
            text_count += 1
        elif role == "leaders":
            leader_count += 1
        if (
            role_count > MAX_ROLE_ENTITIES
            or support_count > MAX_SUPPORTS
            or text_count > MAX_ANNOTATIONS
            or leader_count > MAX_LEADERS
        ):
            raise _limit_error()


def _assert_supported_entity(entity: Any) -> None:
    """Reject entity forms that cannot participate in exact preservation."""

    if entity.dxftype() not in SUPPORTED_ENTITY_TYPES:
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "unsupported DXF entity type")
    _assert_safe_object(entity)
    if entity.dxftype() == "INSERT" and getattr(entity, "attribs", ()):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "nested attributes are unsupported")


def _object_digest(
    objects: Iterable[Any],
    dxfversion: str,
    *,
    expected_types: frozenset[str] | None = None,
) -> list[str]:
    """Fingerprint complete supported table/style records, including handles."""

    fingerprints: list[str] = []
    for object_ in objects:
        object_type = str(object_.dxftype())
        if expected_types is not None and object_type not in expected_types:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "unexpected table record type"
            )
        _assert_safe_object(object_)
        try:
            handle = str(object_.dxf.get("handle", "")).upper()
        except (AttributeError, TypeError, ValueError) as error:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "table record metadata is unavailable"
            ) from error
        if not handle:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "table record lacks stable identity"
            )
        # Raw canonical export binds all modeled attributes: owner references,
        # flags, geometry, name, and handle. XDATA/appdata/extension metadata
        # are rejected by _assert_safe_object rather than silently skipped.
        fingerprints.append(
            _tag_fingerprint(object_, dxfversion, include_handles=True)
        )
    fingerprints.sort()
    if len(fingerprints) != len(set(fingerprints)):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "duplicate table record")
    return fingerprints


def _layout_block_record_handles(document: Any) -> set[str]:
    """Return only the block records that back actual layouts."""

    handles: set[str] = set()
    for layout in document.layouts:
        raw_handle = getattr(layout, "block_record_handle", None)
        if raw_handle is None or not str(raw_handle):
            raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "layout lacks block ownership")
        handle = str(raw_handle).upper()
        handles.add(handle)
    return handles


def _all_blocks(document: Any) -> list[Any]:
    """Return every block definition, including layout-backed definitions."""

    blocks: list[Any] = []
    for block in document.blocks:
        raw_handle = getattr(block, "block_record_handle", None)
        if raw_handle is None or not str(raw_handle):
            raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "block lacks stable ownership")
        blocks.append(block)
    return sorted(
        blocks,
        key=lambda block: (str(block.block_record_handle).upper(), str(block.name)),
    )


def _non_layout_blocks(document: Any) -> list[Any]:
    """Include every reusable block, including anonymous *U and *D definitions."""

    layout_handles = _layout_block_record_handles(document)
    return [
        block
        for block in _all_blocks(document)
        if str(block.block_record_handle).upper() not in layout_handles
    ]


def _assert_complete_block_metadata(block: Any, dxfversion: str) -> None:
    """Validate every serializable BLOCK/ENDBLK header before hashing it."""

    metadata = (
        (block.block, "BLOCK"),
        (block.endblk, "ENDBLK"),
        (block.block_record, "BLOCK_RECORD"),
    )
    for object_, expected_type in metadata:
        if object_ is None or object_.dxftype() != expected_type:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "block metadata is unavailable"
            )
        _assert_safe_object(object_)
        tags = _export_tag_manifest(
            object_, dxfversion, section_name="block metadata"
        )
        if not tags or tags[0] != [0, expected_type]:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "block metadata is malformed"
            )
    try:
        block_name = str(block.name)
        header_name = str(block.block.dxf.name)
        record_name = str(block.block_record.dxf.name)
        base_point = _to_point(block.block.dxf.base_point)
        flags = int(block.block.dxf.flags)
        owner = str(block.block.dxf.owner)
        endblk_owner = str(block.endblk.dxf.owner)
    except (AttributeError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "block metadata is unavailable"
        ) from error
    if (
        not block_name
        or header_name != block_name
        or record_name != block_name
        or base_point is None
        or not all(isfinite(value) for value in base_point)
        or flags < 0
        or not owner
        or not endblk_owner
    ):
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "block metadata is unsupported"
        )


def _block_definition_manifest(document: Any) -> list[dict[str, Any]]:
    """Digest every block header and content, including layout-backed blocks."""

    definitions: list[dict[str, Any]] = []
    layout_handles = _layout_block_record_handles(document)
    for block in _all_blocks(document):
        _assert_complete_block_metadata(block, document.dxfversion)
        entity_fingerprints: list[str] = []
        # Layout entities are already represented in the entity manifest,
        # where expected-after intentionally removes the one audited TEXT.
        # Their BLOCK/ENDBLK/BLOCK_RECORD headers still remain bound below.
        if str(block.block_record_handle).upper() not in layout_handles:
            for entity in block:
                _assert_supported_entity(entity)
                entity_fingerprints.append(
                    _tag_fingerprint(entity, document.dxfversion)
                )
        definitions.append(
            {
                # Handles and owners bind base point, flags, names, xref/path,
                # and every serializable BLOCK/ENDBLK header tag for layouts
                # as well as ordinary reusable definitions.
                "block": _tag_fingerprint(
                    block.block, document.dxfversion, include_handles=True
                ),
                "endblk": _tag_fingerprint(
                    block.endblk, document.dxfversion, include_handles=True
                ),
                "block_record": _tag_fingerprint(
                    block.block_record, document.dxfversion, include_handles=True
                ),
                "entities": sorted(entity_fingerprints),
            }
        )
    return definitions


def _table_style_digest(document: Any) -> str:
    """Hash complete supported table/style records and block definitions privately."""

    layout_names = sorted(
        _fingerprint_text(str(layout.name)) for layout in document.layouts
    )
    return canonical_sha256(
        {
            "layers": _object_digest(
                document.layers, document.dxfversion, expected_types=frozenset({"LAYER"})
            ),
            "styles": _object_digest(
                document.styles, document.dxfversion, expected_types=frozenset({"STYLE"})
            ),
            "linetypes": _object_digest(
                document.linetypes,
                document.dxfversion,
                expected_types=frozenset({"LTYPE"}),
            ),
            "dimstyles": _object_digest(
                document.dimstyles,
                document.dxfversion,
                expected_types=frozenset({"DIMSTYLE"}),
            ),
            "appids": _object_digest(
                document.appids, document.dxfversion, expected_types=frozenset({"APPID"})
            ),
            "ucs": _object_digest(
                document.ucs, document.dxfversion, expected_types=frozenset({"UCS"})
            ),
            "views": _object_digest(
                document.views, document.dxfversion, expected_types=frozenset({"VIEW"})
            ),
            "viewports": _object_digest(
                document.viewports,
                document.dxfversion,
                expected_types=frozenset({"VPORT"}),
            ),
            "block_records": _object_digest(
                document.block_records,
                document.dxfversion,
                expected_types=frozenset({"BLOCK_RECORD"}),
            ),
            "layouts": layout_names,
            "block_definitions": _block_definition_manifest(document),
        }
    )


def _layer_manifest_digest(document: Any) -> str:
    """Bind every layer's visibility flags and raw supported table state."""

    manifest: list[dict[str, Any]] = []
    for layer in document.layers:
        name = str(layer.dxf.get("name", ""))
        try:
            flags = int(layer.dxf.get("flags", 0))
            color = int(layer.dxf.get("color", 0))
        except (AttributeError, TypeError, ValueError) as error:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "layer visibility is unsupported"
            ) from error
        if not name or flags < 0:
            raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "invalid layer state")
        manifest.append(
            {
                "layer_fingerprint": _fingerprint_text(name),
                "flags": flags,
                "color": color,
                "displayable": _layer_is_displayable(layer),
                "transparency": _layer_transparency(layer),
                "raw_tag_fingerprint": _tag_fingerprint(
                    layer,
                    document.dxfversion,
                    include_handles=True,
                ),
            }
        )
    manifest.sort(key=lambda item: item["layer_fingerprint"])
    if len({item["layer_fingerprint"] for item in manifest}) != len(manifest):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "duplicate layer manifest")
    return canonical_sha256(manifest)


def _header_manifest_digest(document: Any) -> str:
    """Hash all nonvolatile R2018 HEADER variables in canonical name order."""

    manifest: list[dict[str, Any]] = []
    for raw_name in document.header.varnames():
        name = str(raw_name)
        if name in VOLATILE_HEADER_VARIABLES:
            continue
        if not name.startswith("$"):
            raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "invalid header variable")
        try:
            value = document.header[name]
            header_var = document.header.hdrvars[name]
            code = int(header_var.code)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "header value is unavailable"
            ) from error
        manifest.append(
            {
                "name": name,
                "code": code,
                "value": _normalize_tag_value(value),
            }
        )
    manifest.sort(key=lambda item: item["name"])
    if len({item["name"] for item in manifest}) != len(manifest):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "duplicate header variable")
    try:
        custom_properties = [
            {
                "tag": _normalize_tag_value(tag),
                "value": _normalize_tag_value(value),
            }
            for tag, value in document.header.custom_vars
        ]
    except (AttributeError, TypeError, ValueError) as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "custom HEADER data is unavailable"
        ) from error
    return canonical_sha256(
        {
            "variables": manifest,
            "custom_properties": custom_properties,
        }
    )


def _volatile_object_tag_codes(document: Any) -> dict[str, frozenset[int]]:
    """Locate the one known ezdxf writer timestamp without hiding other data."""

    metadata_name, marker_name, tag_code = next(
        iter(VOLATILE_OBJECT_TAG_ALLOWLIST)
    )
    try:
        metadata = document.rootdict.get(metadata_name)
        if metadata is None or metadata.dxftype() != "DICTIONARY":
            return {}
        writer_marker = metadata.get(marker_name)
        if (
            writer_marker is None
            or writer_marker.dxftype() != "DICTIONARYVAR"
            or str(writer_marker.dxf.get("owner", ""))
            != str(metadata.dxf.get("handle", ""))
            or len(_entity_tag_values(writer_marker, document.dxfversion, tag_code))
            != 1
        ):
            return {}
        handle = str(writer_marker.dxf.get("handle", "")).upper()
    except (AttributeError, KeyError, TypeError, ValueError):
        return {}
    if not handle:
        return {}
    return {handle: frozenset({tag_code})}


def _objects_manifest_digest(document: Any) -> str:
    """Hash the complete supported OBJECTS section without omitting metadata."""

    manifest: list[dict[str, str]] = []
    volatile_tag_codes = _volatile_object_tag_codes(document)
    rules: dict[str, tuple[str, frozenset[int], frozenset[str]]] = {}

    # ODA regenerates this documented, application-managed TABLESTYLE helper
    # graph after an ezdxf write. It preserves the graph's named role and
    # semantic content but reallocates its private helper handles. The
    # explicit role tags below retain all non-helper data and reject every
    # other OBJECTS relationship from this normalization exception.
    for object_ in document.objects:
        if object_.dxftype() != "TABLESTYLE":
            continue
        style_handle = str(object_.dxf.get("handle", "")).upper()
        if not style_handle:
            continue
        for candidate in document.objects:
            if (
                candidate.dxftype() != "DICTIONARY"
                or str(candidate.dxf.get("owner", "")).upper() != style_handle
            ):
                continue
            try:
                cell_map = candidate.get(
                    "ACAD_ROUNDTRIP_2008_TABLESTYLE_CELLSTYLEMAP"
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if cell_map is None or cell_map.dxftype() != "CELLSTYLEMAP":
                continue
            dictionary_handle = str(candidate.dxf.get("handle", "")).upper()
            cell_map_handle = str(cell_map.dxf.get("handle", "")).upper()
            if not dictionary_handle or not cell_map_handle:
                raise PipelineError(
                    ErrorCode.UNSAFE_ENTITY_TYPE,
                    "TABLESTYLE helper graph lacks stable metadata",
                )
            rules[style_handle] = (
                "table-style",
                frozenset({360}),
                frozenset(),
            )
            rules[dictionary_handle] = (
                "table-style-helper-dictionary",
                frozenset({5, 330, 360}),
                frozenset(),
            )
            rules[cell_map_handle] = (
                "table-style-cell-style-map",
                frozenset({5, 330}),
                frozenset(),
            )

    # The ACDB_RECOMPOSE_DATA XRECORD is another ODA-managed helper. Bind its
    # dictionary key and all payload while canonicalizing only its regenerated
    # private handle/reaction references.
    for object_ in document.objects:
        if object_.dxftype() != "DICTIONARY":
            continue
        try:
            recompose = object_.get("ACDB_RECOMPOSE_DATA")
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if recompose is None or recompose.dxftype() != "XRECORD":
            continue
        dictionary_handle = str(object_.dxf.get("handle", "")).upper()
        record_handle = str(recompose.dxf.get("handle", "")).upper()
        if not dictionary_handle or not record_handle:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE,
                "recompose helper graph lacks stable metadata",
            )
        rules[dictionary_handle] = (
            "recompose-owner",
            frozenset(),
            frozenset({"ACDB_RECOMPOSE_DATA"}),
        )
        rules[record_handle] = (
            "recompose-record",
            frozenset({5, 330}),
            frozenset(),
        )

    for object_ in document.objects:
        object_type = str(object_.dxftype())
        if object_type not in SUPPORTED_OBJECT_TYPES:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "unsupported OBJECTS section type"
            )
        try:
            handle = str(object_.dxf.get("handle", "")).upper()
            owner = str(object_.dxf.get("owner", ""))
        except (AttributeError, TypeError, ValueError) as error:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "OBJECTS metadata is unavailable"
            ) from error
        if not handle or not owner:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE, "OBJECTS entry lacks stable ownership"
            )
        role, role_excluded_codes, volatile_reference_keys = rules.get(
            handle,
            ("ordinary", frozenset(), frozenset()),
        )
        # Unlike entity content fingerprints, this includes tag 5.  The
        # ordered export also includes owner references, XDATA, appdata, and
        # extension-dictionary tags, so every supported object datum is bound.
        manifest.append(
            {
                "role": role,
                "fingerprint": _tag_fingerprint(
                    object_,
                    document.dxfversion,
                    include_handles=True,
                    excluded_tag_codes=(
                        volatile_tag_codes.get(handle, frozenset())
                        | role_excluded_codes
                    ),
                    volatile_dictionary_reference_keys=volatile_reference_keys,
                )
            }
        )
    manifest.sort(key=lambda item: (item["role"], item["fingerprint"]))
    if len({(item["role"], item["fingerprint"]) for item in manifest}) != len(manifest):
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "duplicate OBJECTS entry")
    return canonical_sha256(manifest)


def _bounds_fingerprint(document: Any) -> tuple[str, bool]:
    try:
        model_bounds = bbox.extents(document.modelspace())
    except Exception as error:
        raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "unable to determine bounds") from error
    if not model_bounds.has_data:
        return canonical_sha256({"bounds": "empty"}), False
    return (
        canonical_sha256(
            {
                "min": [
                    float(model_bounds.extmin.x),
                    float(model_bounds.extmin.y),
                    float(model_bounds.extmin.z),
                ],
                "max": [
                    float(model_bounds.extmax.x),
                    float(model_bounds.extmax.y),
                    float(model_bounds.extmax.z),
                ],
            }
        ),
        True,
    )


def _assert_raw_record_congruence(
    document: Any,
    raw_preflight: RawDxfPreflight,
) -> None:
    """Prove ezdxf retained every preflighted modeled-section source tag.

    The comparison re-parses full raw DXF output, so an entity/object export
    cannot hide data that was already discarded during loading.  The only
    exception is the separately identified ezdxf writer timestamp, whose
    volatility is already narrowly allowlisted for OBJECTS preservation.
    """

    stream = io.StringIO()
    try:
        document.write(stream, fmt="asc")
    except Exception as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE,
            "normalized DXF cannot be serialized",
        ) from error
    try:
        normalized_preflight = preflight_ascii_dxf_bytes(
            stream.getvalue().encode("utf-8")
        )
    except UnicodeEncodeError as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE,
            "normalized DXF cannot be preflighted",
        ) from error
    assert_normalized_records_match(
        raw_preflight,
        normalized_preflight,
        ignored_codes_by_handle=_volatile_object_tag_codes(document),
    )
    assert_normalized_header_match(raw_preflight, normalized_preflight)


def _preflight_in_memory_document(document: Any) -> RawDxfPreflight:
    """Serialize synthetic/in-memory drawings through the production preflight."""

    stream = io.StringIO()
    try:
        document.write(stream, fmt="asc")
    except Exception as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "in-memory DXF cannot be serialized"
        ) from error
    try:
        return preflight_ascii_dxf_bytes(stream.getvalue().encode("utf-8"))
    except UnicodeEncodeError as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE, "in-memory DXF cannot be preflighted"
        ) from error


def _snapshot_document_unchecked(
    document: Any,
    *,
    raw_preflight: RawDxfPreflight | None = None,
    include_topology_evidence: bool = False,
    topology_context: "TopologySnapshotContext | None" = None,
) -> Snapshot:
    """Create an immutable snapshot of supported DXF entities and table state.

    Direct in-memory callers are serialized and preflighted first so their
    tests cannot bypass the raw semantics enforced for ODA-produced DXF.
    """

    if document.dxfversion != "AC1032":
        raise PipelineError(ErrorCode.UNSUPPORTED_VERSION, "unsupported DXF version")
    checked_raw = raw_preflight or _preflight_in_memory_document(document)
    _assert_raw_record_congruence(document, checked_raw)
    normalized_classes = _normalized_class_records(document)
    _assert_raw_classes_match(checked_raw, normalized_classes)
    records: list[EntityRecord] = []
    layer_visual_state = _layer_visual_state_by_name(document)
    if include_topology_evidence:
        _assert_topology_capture_limits(
            document,
            layer_visual_state=layer_visual_state,
            topology_context=topology_context,
        )
    for layout in document.layouts:
        layout_kind = "modelspace" if str(layout.name) == "Model" else "paperspace"
        records.extend(
            _record(
                entity,
                layout=layout_kind,
                sequence_index=sequence_index,
                container_name=str(layout.name),
                dxfversion=document.dxfversion,
                layer_visual_state=layer_visual_state,
                include_topology_evidence=include_topology_evidence,
                topology_context=topology_context,
            )
            for sequence_index, entity in enumerate(layout)
        )
    for block in _non_layout_blocks(document):
        block_name = str(block.name)
        records.extend(
            _record(
                entity,
                layout="block",
                sequence_index=sequence_index,
                container_name=block_name,
                dxfversion=document.dxfversion,
                layer_visual_state=layer_visual_state,
                include_topology_evidence=include_topology_evidence,
                topology_context=topology_context,
            )
            for sequence_index, entity in enumerate(block)
        )
    if len({record.handle for record in records}) != len(records):
        raise PipelineError(ErrorCode.DUPLICATE_TARGET, "duplicate DXF entity handle")
    records.sort(
        key=lambda record: (
            record.layout,
            record.container_fingerprint,
            int(record.handle, 16),
        )
    )
    bounds_fingerprint, bounds_has_data = _bounds_fingerprint(document)
    return Snapshot(
        records=tuple(records),
        layer_manifest_digest=_layer_manifest_digest(document),
        table_style_manifest_digest=_table_style_digest(document),
        header_manifest_digest=_header_manifest_digest(document),
        raw_header_manifest_digest=checked_raw.raw_header_manifest_digest,
        objects_manifest_digest=_objects_manifest_digest(document),
        classes_manifest_digest=_classes_manifest_digest(normalized_classes),
        raw_classes_manifest_digest=checked_raw.classes_manifest_digest,
        raw_classes_multiset_digest=checked_raw.classes_multiset_digest,
        raw_classes_record_count=checked_raw.classes_record_count,
        acdsdata_manifest_digest=_acdsdata_manifest_digest(document),
        raw_section_structure_digest=checked_raw.section_structure_digest,
        bounds_fingerprint=bounds_fingerprint,
        bounds_has_data=bounds_has_data,
    )


def snapshot_document(
    document: Any,
    *,
    raw_preflight: RawDxfPreflight | None = None,
    include_topology_evidence: bool = False,
    topology_context: "TopologySnapshotContext | None" = None,
) -> Snapshot:
    """Create a snapshot while redacting loader structural failures.

    Raw preflight rejects unsupported constructs first, but ezdxf can still
    surface implementation-specific ``KeyError``/shape exceptions for a
    malformed table or graph. Those details must never escape a pipeline or
    CLI boundary as a traceback.
    """

    try:
        return _snapshot_document_unchecked(
            document,
            raw_preflight=raw_preflight,
            include_topology_evidence=include_topology_evidence,
            topology_context=topology_context,
        )
    except PipelineError:
        raise
    except (
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
        ValueError,
        ezdxf.DXFError,
    ) as error:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE,
            "DXF snapshot structural failure",
        ) from error


def _absolute_without_resolving_links(path: Path) -> Path:
    """Make a file path absolute without reinterpreting a reparse target."""

    return Path(os.path.abspath(os.fspath(path)))


def _snapshot_backend() -> FileOwnershipBackend:
    """Select the mandatory retained-handle backend for staged DXF reads."""

    try:
        return platform_backend(require_windows=True)
    except OwnershipCleanupError as error:
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "DXF snapshot requires Windows handle semantics",
        ) from error


@dataclass
class _DxfReadLease:
    """One no-write/no-delete DXF file identity retained through snapshotting."""

    lexical_path: Path
    path: Path
    owned: OwnedPath
    binding: OwnedPathBinding
    backend: FileOwnershipBackend
    _closed: bool = False

    def require_binding(self) -> None:
        """Fail closed if the held bytes or either pathname binding changed."""

        try:
            current = self.owned.capture_binding()
            if (
                current.is_directory
                or not current.same_identity_and_content(self.binding)
                or not self.backend.path_matches_binding(self.lexical_path, current)
                or not self.backend.path_matches_binding(self.path, current)
            ):
                raise OwnershipLostError("DXF file identity changed")
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.SOURCE_CHANGED_DURING_RUN,
                "DXF changed during leased loading",
            ) from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.owned.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.CONVERSION_FAILURE,
                "DXF read lease cannot be released",
            ) from error
        self._closed = True


def _acquire_dxf_read_lease(path: Path) -> _DxfReadLease:
    """Open the lexical DXF file no-follow before inspecting or decoding it."""

    backend = _snapshot_backend()
    lexical = _absolute_without_resolving_links(path)
    opened: OwnedPath | None = None
    try:
        opened = backend.open_existing_file_read_lease(lexical)
        binding = opened.capture_binding()
        final_path = _absolute_without_resolving_links(opened.final_path())
        if (
            binding.is_directory
            or not backend.path_matches_binding(lexical, binding)
            or not backend.path_matches_binding(final_path, binding)
        ):
            raise OwnershipLostError("DXF file did not retain its binding")
        return _DxfReadLease(
            lexical_path=lexical,
            path=final_path,
            owned=opened,
            binding=binding,
            backend=backend,
        )
    except PipelineError:
        if opened is not None:
            try:
                opened.close()
            except (OSError, OwnershipError):
                pass
        raise
    except (OSError, OwnershipError) as error:
        if opened is not None:
            try:
                opened.close()
            except (OSError, OwnershipError):
                pass
        raise PipelineError(ErrorCode.CONVERSION_FAILURE, "DXF cannot be read") from error


@contextmanager
def open_preflighted_dxf(path: Path) -> Iterator[tuple[Any, RawDxfPreflight]]:
    """Yield a parsed DXF while one exact staged byte identity remains leased.

    The raw preflight, UTF-8 decoding, parser input, raw-to-normalized
    congruence, and caller's snapshot construction all share the immutable
    byte string read once through the held file handle. No pathname is opened
    again after the lease acquisition.
    """

    lease = _acquire_dxf_read_lease(path)
    try:
        raw = read_bounded_dxf_chunks(lease.owned.read_chunks())
        lease.require_binding()
        raw_preflight = preflight_ascii_dxf_bytes(raw)
        stream = io.TextIOWrapper(
            io.BytesIO(raw),
            encoding="utf-8",
            errors="strict",
        )
        try:
            document = ezdxf.read(stream)
        except (UnicodeDecodeError, OSError, IOError, ezdxf.DXFError) as error:
            raise PipelineError(ErrorCode.CONVERSION_FAILURE, "DXF cannot be read") from error
        except (KeyError, IndexError, AttributeError, TypeError, ValueError) as error:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE,
                "DXF loader structural failure",
            ) from error
        finally:
            stream.close()
        lease.require_binding()
        yield document, raw_preflight
        # The consumer's snapshot construction ran while this lease remained
        # live. A synthetic or unexpected filesystem implementation still
        # cannot report success if its original identity was lost.
        lease.require_binding()
    finally:
        lease.close()


def read_preflighted_dxf(path: Path) -> tuple[Any, RawDxfPreflight]:
    """Load and congruence-check one exact staged DXF byte image.

    This compatibility helper returns a document only after the raw
    preflight, parser, and raw congruence proof have completed under one
    retained lease. Callers that also construct a ``Snapshot`` should use
    :func:`open_preflighted_dxf` so construction stays in that same lease.
    """

    with open_preflighted_dxf(path) as (document, raw_preflight):
        _assert_raw_record_congruence(document, raw_preflight)
        return document, raw_preflight


def snapshot_dxf(
    path: Path,
    *,
    include_topology_evidence: bool = False,
    topology_context: "TopologySnapshotContext | None" = None,
) -> Snapshot:
    """Read a temporary DXF and create a read-only preservation snapshot.

    Topology primitives are opt-in private analysis data.  The default v1
    path therefore retains full entity fingerprints without extracting or
    storing geometry vertices.
    """

    with open_preflighted_dxf(path) as (document, raw_preflight):
        return snapshot_document(
            document,
            raw_preflight=raw_preflight,
            include_topology_evidence=include_topology_evidence,
            topology_context=topology_context,
        )
