"""Bounded, fail-closed raw ASCII DXF validation.

This module deliberately runs before :func:`ezdxf.readfile`.  It retains only
private digests and typed CLASS metadata needed to prove that ezdxf did not
drop or normalize an unsupported source construct.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from ezdxf.sections.headervars import HEADER_VAR_MAP, version_specific_group_code

from .canonical import canonical_sha256
from .errors import ErrorCode, PipelineError


# These limits bound untrusted ODA output before any higher-level parser sees
# it.  The public workflow processes one private staged drawing at a time.
MAX_DXF_BYTES = 64 * 1024 * 1024
MAX_DXF_LINES = 2_000_000
MAX_DXF_TAGS = 1_000_000
MAX_DXF_LINE_BYTES = 1_048_576

_CORE_SECTION_ORDER = (
    "HEADER",
    "CLASSES",
    "TABLES",
    "BLOCKS",
    "ENTITIES",
    "OBJECTS",
)
_ALLOWED_SECTION_NAMES = frozenset((*_CORE_SECTION_ORDER, "ACDSDATA"))
_CLASS_TAG_SEQUENCE = (0, 1, 2, 3, 90, 91, 280, 281)
_CANONICAL_EMPTY_ACDSDATA_TAGS = (
    (0, b"SECTION"),
    (2, b"ACDSDATA"),
    (0, b"ENDSEC"),
)
_GROUP_CODE = re.compile(rb"[ \t]*([0-9]{1,4})[ \t]*\Z")
# ODA R2018 right-aligns CLASS integer values with ASCII spaces. Preserve the
# raw tags for comparison, but accept that fixed-width lexical rendering.
_CLASS_INTEGER = re.compile(rb"[ \t]*-?[0-9]{1,10}[ \t]*\Z")
_INTEGER = re.compile(rb"[+-]?[0-9]+\Z")
_MODELED_RECORD_SECTIONS = ("TABLES", "BLOCKS", "ENTITIES", "OBJECTS")
# No parser-created modeled records are currently safe to ignore. Keeping the
# allowlist explicit makes any future normalization exception reviewable and
# testable instead of silently accepting a broader parsed document.
_NORMALIZED_EXTRA_RECORD_ALLOWLIST: frozenset[tuple[str, str, str | None]] = (
    frozenset()
)
# ezdxf 1.4.4 serializes these R2018 records in canonical order and may add
# documented default DIMSTYLE fields. Their source tags must still survive
# with multiplicity, but raw wire order is not an editable drawing property.
# This narrowly qualifies generated ODA 27.1.0 output; unknown record types
# remain ordered-byte-congruent or fail closed.
_WRITER_REORDER_RECORD_TYPES = frozenset(
    {
        ("TABLES", "VPORT"),
        ("TABLES", "DIMSTYLE"),
        ("OBJECTS", "MLEADERSTYLE"),
        ("OBJECTS", "TABLESTYLE"),
    }
)
_SUPPORTED_R2018_TABLE_NAMES = frozenset(
    {
        # Every admitted table is consumed by the snapshot/preservation
        # engine. Unknown table names must not reach ezdxf, which otherwise
        # can raise a structural KeyError after accepting raw input.
        "VPORT",
        "LTYPE",
        "LAYER",
        "STYLE",
        "VIEW",
        "UCS",
        "APPID",
        "DIMSTYLE",
        "BLOCK_RECORD",
    }
)
_RAW_HANDLE_CODES = frozenset({5, 105})
_RAW_FLOAT_CODE_RANGES = (
    (10, 59),
    (110, 149),
    (210, 239),
    (460, 469),
    (1010, 1059),
)
_RAW_INTEGER_CODE_RANGES = (
    (60, 79),
    (90, 99),
    (160, 169),
    (170, 179),
    (270, 289),
    (290, 299),
    (370, 389),
    (400, 459),
    (1060, 1071),
)
_SUPPORTED_GRAPHIC_TYPES = frozenset(
    {"TEXT", "LINE", "LWPOLYLINE", "INSERT", "DIMENSION", "HATCH"}
)
_SUPPORTED_ENTITY_SUBCLASSES: dict[str, frozenset[bytes]] = {
    "TEXT": frozenset({b"AcDbEntity", b"AcDbText"}),
    "LINE": frozenset({b"AcDbEntity", b"AcDbLine"}),
    "LWPOLYLINE": frozenset({b"AcDbEntity", b"AcDbPolyline"}),
    "INSERT": frozenset({b"AcDbEntity", b"AcDbBlockReference"}),
    "DIMENSION": frozenset(
        {
            b"AcDbEntity",
            b"AcDbDimension",
            b"AcDbAlignedDimension",
            b"AcDbRotatedDimension",
            b"AcDb2LineAngularDimension",
            b"AcDb3PointAngularDimension",
            b"AcDbRadialDimension",
            b"AcDbDiametricDimension",
            b"AcDbOrdinateDimension",
            b"AcDbArcDimension",
            b"AcDbRadialDimensionLarge",
        }
    ),
    "HATCH": frozenset({b"AcDbEntity", b"AcDbHatch"}),
}
_BLOCK_SUBCLASSES: dict[str, frozenset[bytes]] = {
    "BLOCK": frozenset({b"AcDbEntity", b"AcDbBlockBegin"}),
    "ENDBLK": frozenset({b"AcDbEntity", b"AcDbBlockEnd"}),
}
_LAYER_TRANSPARENCY_APP = b"AcCmTransparency"
_LAYER_TRANSPARENCY_MASK = 0x02000000
# These are the only HEADER values which ODA/ezdxf are allowed to regenerate.
# Their variable identity, position, group-code sequence, and multiplicity stay
# bound by the raw manifest below; only their values are intentionally volatile.
_VOLATILE_HEADER_VARIABLES = frozenset(
    {
        # ODA 27.1 writes the R2018 maintenance release (55), while pinned
        # ezdxf rewrites its own maintenance marker (4). This version-detail
        # does not alter the AC1032/R2018 representation contract.
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
_CUSTOM_HEADER_VARIABLES = frozenset({"$CUSTOMPROPERTYTAG", "$CUSTOMPROPERTY"})


@dataclass(frozen=True)
class RawTag:
    """A raw DXF tag kept only while constructing a local snapshot."""

    code: int
    value: bytes


@dataclass(frozen=True)
class RawClassRecord:
    """One supported CLASS declaration with semantic and wire digests."""

    identity: tuple[str, str]
    name: str
    normalized_tags: tuple[tuple[int, Any], ...]
    # Semantic declaration digest remains part of phase-two preservation.
    # ``wire_digest`` is retained for independent ODA-run comparison only:
    # ezdxf may normalize harmless fixed-width integer spelling when an
    # authorized edit serializes a DXF.
    raw_digest: str
    wire_digest: str


@dataclass(frozen=True)
class RawRecord:
    """One bounded modeled-section record before ezdxf can normalize it.

    ``canonical_tags`` keeps an ordered, typed representation locally.  It is
    deliberately never emitted in artifacts or reports because DXF values can
    contain user data.  Handles are retained only as opaque matching keys for
    source-to-normalized congruence.
    """

    section: str
    record_type: str
    handle: str | None
    canonical_tags: tuple[tuple[int, Any], ...]
    raw_digest: str


@dataclass(frozen=True)
class RawDxfPreflight:
    """Losslessness bindings derived from exact pre-normalization DXF bytes."""

    raw_bytes_digest: str
    section_structure_digest: str
    raw_header_manifest_digest: str
    classes_manifest_digest: str
    classes_multiset_digest: str
    classes_wire_manifest_digest: str
    classes_wire_multiset_digest: str
    classes_record_count: int
    classes: tuple[RawClassRecord, ...]
    acdsdata_present: bool
    modeled_records: tuple[RawRecord, ...]
    modeled_records_digest: str


def _reject() -> None:
    """Raise one stable, redacted failure for malformed raw DXF input."""

    raise PipelineError(ErrorCode.UNSAFE_ENTITY_TYPE, "raw DXF preflight rejected")


def _read_bounded(path: Path) -> bytes:
    """Read an exact staged DXF payload without unbounded allocation."""

    chunks: list[bytes] = []
    size = 0
    try:
        with path.open("rb") as source:
            while True:
                chunk = source.read(min(1024 * 1024, MAX_DXF_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DXF_BYTES:
                    _reject()
                chunks.append(chunk)
    except PipelineError:
        raise
    except OSError as error:
        raise PipelineError(
            ErrorCode.CONVERSION_FAILURE, "DXF preflight cannot read input"
        ) from error
    if not chunks:
        _reject()
    return b"".join(chunks)


def read_bounded_dxf_chunks(chunks: Iterable[bytes]) -> bytes:
    """Collect one staged DXF stream without allocating beyond its size limit.

    Callers that already hold a no-write/no-delete file lease use this instead
    of reopening a pathname. The returned ``bytes`` object is the immutable
    identity consumed by both raw preflight and the DXF parser.
    """

    collected: list[bytes] = []
    size = 0
    iterator = iter(chunks)
    try:
        for chunk in iterator:
            if not isinstance(chunk, bytes):
                raise OSError("DXF lease yielded a non-byte chunk")
            size += len(chunk)
            if size > MAX_DXF_BYTES:
                _reject()
            if chunk:
                collected.append(chunk)
    except PipelineError:
        raise
    except OSError as error:
        raise PipelineError(
            ErrorCode.CONVERSION_FAILURE,
            "DXF preflight cannot read input",
        ) from error
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
    if not collected:
        _reject()
    return b"".join(collected)


def _parse_tags(raw: bytes) -> tuple[RawTag, ...]:
    """Parse bounded alternating group-code/value ASCII DXF lines."""

    if not raw or len(raw) > MAX_DXF_BYTES:
        _reject()

    lines: list[bytes] = []
    position = 0
    while position < len(raw):
        ending = raw.find(b"\n", position)
        if ending < 0:
            _reject()
        line = raw[position:ending]
        position = ending + 1
        if line.endswith(b"\r"):
            line = line[:-1]
        if (
            len(line) > MAX_DXF_LINE_BYTES
            or b"\r" in line
            or b"\x00" in line
        ):
            _reject()
        lines.append(line)
        if len(lines) > MAX_DXF_LINES:
            _reject()

    if len(lines) % 2:
        _reject()
    tags: list[RawTag] = []
    for index in range(0, len(lines), 2):
        match = _GROUP_CODE.fullmatch(lines[index])
        if match is None:
            _reject()
        code = int(match.group(1))
        if code > 1071:
            _reject()
        tags.append(RawTag(code=code, value=lines[index + 1]))
        if len(tags) > MAX_DXF_TAGS:
            _reject()
    if not tags:
        _reject()
    return tuple(tags)


def _ascii_section_name(value: bytes) -> str:
    """Decode a strict top-level section marker without retaining raw text."""

    try:
        name = value.decode("ascii")
    except UnicodeDecodeError:
        _reject()
    if name not in _ALLOWED_SECTION_NAMES:
        _reject()
    return name


def _parse_class_text(value: bytes) -> str:
    """Decode only the ASCII CLASS identifiers supported by this release."""

    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError:
        _reject()
    if not decoded or any(ord(character) < 32 or ord(character) > 126 for character in decoded):
        _reject()
    return decoded


def _parse_class_integer(value: bytes, *, flag: bool = False) -> int:
    """Parse a bounded CLASS integer while retaining its exact raw digest."""

    if _CLASS_INTEGER.fullmatch(value) is None:
        _reject()
    parsed = int(value)
    if not -(2**31) <= parsed <= 2**31 - 1:
        _reject()
    if flag and parsed not in (0, 1):
        _reject()
    return parsed


def _parse_classes(tags: tuple[RawTag, ...]) -> tuple[RawClassRecord, ...]:
    """Validate exact R2018 CLASS records and prohibit duplicate identities."""

    records: list[RawClassRecord] = []
    identities: set[tuple[str, str]] = set()
    names: set[str] = set()
    index = 0
    while index < len(tags):
        record = tags[index : index + len(_CLASS_TAG_SEQUENCE)]
        if (
            len(record) != len(_CLASS_TAG_SEQUENCE)
            or tuple(tag.code for tag in record) != _CLASS_TAG_SEQUENCE
            or record[0].value != b"CLASS"
        ):
            _reject()
        name = _parse_class_text(record[1].value)
        cpp_class_name = _parse_class_text(record[2].value)
        app_name = _parse_class_text(record[3].value)
        flags = _parse_class_integer(record[4].value)
        instance_count = _parse_class_integer(record[5].value)
        was_a_proxy = _parse_class_integer(record[6].value, flag=True)
        is_an_entity = _parse_class_integer(record[7].value, flag=True)
        identity = (name, cpp_class_name)
        if identity in identities or name in names:
            _reject()
        identities.add(identity)
        names.add(name)
        normalized_tags = (
            (0, "CLASS"),
            (1, name),
            (2, cpp_class_name),
            (3, app_name),
            (90, flags),
            (91, instance_count),
            (280, was_a_proxy),
            (281, is_an_entity),
        )
        records.append(
            RawClassRecord(
                identity=identity,
                name=name,
                normalized_tags=normalized_tags,
                raw_digest=canonical_sha256(
                    {"tags": [[code, value] for code, value in normalized_tags]}
                ),
                wire_digest=canonical_sha256(
                    {
                        "tags": [
                            [tag.code, tag.value.hex()]
                            for tag in record
                        ]
                    }
                ),
            )
        )
        index += len(_CLASS_TAG_SEQUENCE)
    return tuple(records)


def _validate_structure(tags: tuple[RawTag, ...]) -> tuple[
    tuple[str, ...], dict[str, tuple[RawTag, ...]]
]:
    """Require only canonical top-level SECTION/ENDSEC/EOF structure."""

    names: list[str] = []
    sections: dict[str, tuple[RawTag, ...]] = {}
    index = 0
    while index < len(tags):
        tag = tags[index]
        if tag.code == 0 and tag.value == b"EOF":
            if index != len(tags) - 1:
                _reject()
            break
        if tag.code != 0 or tag.value != b"SECTION" or index + 1 >= len(tags):
            _reject()
        section_name_tag = tags[index + 1]
        if section_name_tag.code != 2:
            _reject()
        name = _ascii_section_name(section_name_tag.value)
        if name in sections:
            _reject()
        start = index
        index += 2
        while index < len(tags):
            content_tag = tags[index]
            if content_tag.code == 0 and content_tag.value == b"ENDSEC":
                section = tags[start : index + 1]
                sections[name] = section
                names.append(name)
                index += 1
                break
            if content_tag.code == 0 and content_tag.value in {b"SECTION", b"EOF"}:
                _reject()
            index += 1
        else:
            _reject()
    else:
        _reject()

    expected = list(_CORE_SECTION_ORDER)
    if names == expected:
        return tuple(names), sections
    if names == [*expected, "ACDSDATA"]:
        return tuple(names), sections
    _reject()


def _assert_supported_r2018_header(tags: tuple[RawTag, ...]) -> None:
    """Reject non-R2018 input before any model loader can reinterpret it."""

    versions: list[bytes] = []
    for index, tag in enumerate(tags):
        if tag.code == 9 and tag.value == b"$ACADVER":
            if index + 1 >= len(tags) or tags[index + 1].code != 1:
                _reject()
            versions.append(tags[index + 1].value)
    if len(versions) != 1 or versions[0] != b"AC1032":
        raise PipelineError(ErrorCode.UNSUPPORTED_VERSION, "unsupported DXF version")


def _code_in_ranges(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= code <= end for start, end in ranges)


def _canonical_numeric(value: bytes, *, integer: bool) -> int | str:
    """Return a semantic numeric tag value without retaining its spelling."""

    stripped = value.strip(b" \t")
    if integer:
        if _INTEGER.fullmatch(stripped) is None:
            _reject()
        try:
            return int(stripped)
        except ValueError:
            _reject()
    try:
        decoded = stripped.decode("ascii")
        parsed = Decimal(decoded)
    except (InvalidOperation, UnicodeDecodeError):
        _reject()
    if not parsed.is_finite():
        _reject()
    normalized = parsed.normalize()
    # Decimal keeps a signed zero, which is not a meaningful DXF distinction.
    return "0" if normalized.is_zero() else str(normalized)


def _canonical_tag_value(tag: RawTag) -> Any:
    """Build a bounded canonical value for a raw group-code/value pair."""

    if tag.code in _RAW_HANDLE_CODES:
        try:
            handle = tag.value.decode("ascii").upper()
        except UnicodeDecodeError:
            _reject()
        if not handle or any(character not in "0123456789ABCDEF" for character in handle):
            _reject()
        return {"handle": handle}
    if _code_in_ranges(tag.code, _RAW_INTEGER_CODE_RANGES):
        return {"integer": _canonical_numeric(tag.value, integer=True)}
    if _code_in_ranges(tag.code, _RAW_FLOAT_CODE_RANGES):
        return {"number": _canonical_numeric(tag.value, integer=False)}
    # DXF strings can use the document code page.  Retaining their exact
    # bytes avoids a second, lossy pre-parser decode while still allowing
    # deterministic congruence checks against ezdxf's ASCII serialization.
    return {"bytes": tag.value.hex()}


def _header_variable_name(value: bytes) -> str:
    """Decode one modeled HEADER variable name without accepting extensions."""

    try:
        name = value.decode("ascii")
    except UnicodeDecodeError:
        _reject()
    if (
        not name.startswith("$")
        or any(ord(character) < 32 or ord(character) > 126 for character in name)
    ):
        _reject()
    return name


def _expected_header_value_codes(name: str) -> tuple[int, ...]:
    """Return the exact R2018 raw tag shape modeled for one HEADER variable."""

    definition = HEADER_VAR_MAP.get(name)
    if definition is None:
        _reject()
    factory_name = getattr(definition.factory, "__name__", "")
    if factory_name == "Point2D":
        return (10, 20)
    if factory_name == "Point3D":
        return (10, 20, 30)
    return (version_specific_group_code(name, "AC1032"),)


def _parse_header_manifest(tags: tuple[RawTag, ...]) -> str:
    """Strictly model every raw HEADER variable before ezdxf can discard it.

    HEADER uses group-code 9 as the only record boundary.  The pinned ezdxf
    R2018 definitions provide the complete accepted variable and tag-shape
    vocabulary.  Unknown names, duplicate normal variables, free tags, and
    comments therefore fail closed instead of being silently ignored by the
    permissive loader.  Custom properties are the one documented repeating
    pair and retain their original order and values.
    """

    records: list[dict[str, Any]] = []
    seen_variables: set[str] = set()
    index = 0
    while index < len(tags):
        marker = tags[index]
        if marker.code != 9:
            _reject()
        name = _header_variable_name(marker.value)
        index += 1
        start = index
        while index < len(tags) and tags[index].code != 9:
            if tags[index].code == 999:
                # DXF comments have no ezdxf HEADER representation in this
                # release, so accepting one would make preservation lossy.
                _reject()
            index += 1
        values = tags[start:index]
        if name in _CUSTOM_HEADER_VARIABLES:
            if len(values) != 1 or values[0].code != 1:
                _reject()
        else:
            if name in seen_variables or tuple(tag.code for tag in values) != _expected_header_value_codes(name):
                _reject()
            seen_variables.add(name)
        encoded_tags = [
            [tag.code, _canonical_tag_value(tag)]
            for tag in values
        ]
        if name in _VOLATILE_HEADER_VARIABLES:
            # Do not omit volatile records: retain their exact wire shape
            # while redacting only values ODA/ezdxf demonstrably regenerate.
            encoded_tags = [[tag.code, {"volatile": True}] for tag in values]
        records.append({"name": name, "tags": encoded_tags})

    for previous, current in zip(records, records[1:], strict=False):
        if (
            previous["name"] == "$CUSTOMPROPERTYTAG"
            and current["name"] != "$CUSTOMPROPERTY"
        ) or (
            previous["name"] == "$CUSTOMPROPERTY"
            and current["name"] != "$CUSTOMPROPERTYTAG"
        ):
            _reject()
    if records and records[-1]["name"] == "$CUSTOMPROPERTYTAG":
        _reject()
    return canonical_sha256({"section": "HEADER", "records": records})


def _record_type(value: bytes) -> str:
    """Decode a record marker, which DXF constrains to printable ASCII."""

    try:
        record_type = value.decode("ascii")
    except UnicodeDecodeError:
        _reject()
    if (
        not record_type
        or any(ord(character) < 32 or ord(character) > 126 for character in record_type)
    ):
        _reject()
    return record_type


def _record_handle(tags: tuple[RawTag, ...]) -> str | None:
    """Extract one stable record handle when DXF supplies one."""

    handles: list[str] = []
    for tag in tags:
        if tag.code not in _RAW_HANDLE_CODES:
            continue
        canonical = _canonical_tag_value(tag)
        assert isinstance(canonical, dict)
        handle = canonical["handle"]
        assert isinstance(handle, str)
        handles.append(handle)
    if not handles:
        return None
    if len(set(handles)) != 1:
        _reject()
    return handles[0]


def _make_raw_record(section: str, tags: tuple[RawTag, ...]) -> RawRecord:
    """Construct one exact modeled-section record from its group-code boundary."""

    if not tags or tags[0].code != 0:
        _reject()
    record_type = _record_type(tags[0].value)
    canonical_tags = tuple((tag.code, _canonical_tag_value(tag)) for tag in tags)
    handle = _record_handle(tags)
    payload = {
        "section": section,
        "record_type": record_type,
        "handle": handle,
        "tags": [[code, value] for code, value in canonical_tags],
    }
    return RawRecord(
        section=section,
        record_type=record_type,
        handle=handle,
        canonical_tags=canonical_tags,
        raw_digest=canonical_sha256(payload),
    )


def _parse_section_records(
    section: str, tags: tuple[RawTag, ...]
) -> tuple[tuple[RawRecord, ...], tuple[tuple[RawTag, ...], ...]]:
    """Split one modeled section at every DXF record boundary.

    DXF uses group code 0 as the only legal record marker in TABLES, BLOCKS,
    ENTITIES, and OBJECTS.  Refusing free-floating data prevents unknown tags
    from being attached to a neighboring modeled record by a permissive
    parser.
    """

    raw_records: list[tuple[RawTag, ...]] = []
    index = 0
    while index < len(tags):
        if tags[index].code != 0:
            _reject()
        start = index
        index += 1
        while index < len(tags) and tags[index].code != 0:
            index += 1
        raw_records.append(tags[start:index])
    records = tuple(_make_raw_record(section, record) for record in raw_records)
    return records, tuple(raw_records)


def _table_name(raw_tags: tuple[RawTag, ...]) -> str:
    """Decode one strict R2018 TABLE header name before model loading."""

    if len(raw_tags) < 2 or raw_tags[0].code != 0 or raw_tags[1].code != 2:
        _reject()
    try:
        name = raw_tags[1].value.decode("ascii")
    except UnicodeDecodeError:
        _reject()
    if name not in _SUPPORTED_R2018_TABLE_NAMES:
        _reject()
    return name


def _assert_table_boundaries(
    records: tuple[RawRecord, ...],
    raw_records: tuple[tuple[RawTag, ...], ...],
) -> None:
    """Require every TABLES record to live inside one explicit TABLE/ENDTAB."""

    table_open = False
    for record, raw_tags in zip(records, raw_records, strict=True):
        if record.record_type == "TABLE":
            if table_open:
                _reject()
            _table_name(raw_tags)
            table_open = True
            continue
        if record.record_type == "ENDTAB":
            if not table_open:
                _reject()
            if len(record.canonical_tags) != 1:
                _reject()
            table_open = False
            continue
        if not table_open:
            _reject()
    if table_open:
        _reject()


def _assert_block_boundaries(records: tuple[RawRecord, ...]) -> None:
    """Require complete non-nested BLOCK/ENDBLK record streams."""

    block_open = False
    for record in records:
        if record.record_type == "BLOCK":
            if block_open:
                _reject()
            block_open = True
            continue
        if record.record_type == "ENDBLK":
            if not block_open:
                _reject()
            block_open = False
            continue
        if not block_open:
            _reject()
    if block_open:
        _reject()


def _is_layer_transparency_value(value: bytes) -> bool:
    """Recognize the sole table XDATA form modeled by this release."""

    try:
        parsed = _canonical_numeric(value, integer=True)
    except PipelineError:
        return False
    if not isinstance(parsed, int):
        return False
    # ``Layer.transparency`` writes a true-color transparency bitfield with
    # only an 8-bit alpha component.  Anything else is unmodeled XDATA.
    return (
        (parsed & 0xFF000000) == _LAYER_TRANSPARENCY_MASK
        and 0 <= (parsed & 0xFF) <= 0xFF
        and parsed & 0x00FFFF00 == 0
    )


def _has_only_modeled_layer_transparency(tags: tuple[RawTag, ...]) -> bool:
    """Allow only the exact AcCmTransparency XDATA pair on a LAYER record."""

    special = [
        (index, tag)
        for index, tag in enumerate(tags)
        if 1000 <= tag.code <= 1071
    ]
    if len(special) != 2:
        return False
    first_index, first = special[0]
    second_index, second = special[1]
    return (
        first_index + 1 == second_index
        and first.code == 1001
        and first.value == _LAYER_TRANSPARENCY_APP
        and second.code == 1071
        and _is_layer_transparency_value(second.value)
    )


def _has_only_modeled_reactors(tags: tuple[RawTag, ...]) -> bool:
    """Allow the exact standard reactor app-data form that ezdxf models.

    GROUP membership attaches an ``ACAD_REACTORS`` application-data list to
    graphic entities.  It is not arbitrary extension data: the complete tag
    stream is preserved by the entity fingerprint and the raw congruence
    check.  Every other app-data marker remains unsupported.
    """

    markers = [(index, tag) for index, tag in enumerate(tags) if tag.code == 102]
    if not markers:
        return True
    if len(markers) != 2:
        return False
    (opening_index, opening), (closing_index, closing) = markers
    if (
        opening.value != b"{ACAD_REACTORS"
        or closing.value != b"}"
        or closing_index <= opening_index + 1
    ):
        return False
    return all(tag.code == 330 for tag in tags[opening_index + 1 : closing_index])


def _assert_no_unmodeled_graphic_tags(
    record: RawRecord, raw_tags: tuple[RawTag, ...]
) -> None:
    """Reject raw entity extensions before a model loader can discard them."""

    if record.record_type not in _SUPPORTED_GRAPHIC_TYPES:
        _reject()
    allowed_subclasses = _SUPPORTED_ENTITY_SUBCLASSES[record.record_type]
    if not _has_only_modeled_reactors(raw_tags):
        _reject()
    for tag in raw_tags:
        if (
            tag.code == 999
            or 1000 <= tag.code <= 1071
            or tag.code == 360
        ):
            _reject()
        if tag.code == 100 and tag.value not in allowed_subclasses:
            _reject()


def _assert_no_unmodeled_block_header_tags(
    record: RawRecord, raw_tags: tuple[RawTag, ...]
) -> None:
    """Reject metadata on BLOCK/ENDBLK that snapshotting cannot preserve."""

    allowed_subclasses = _BLOCK_SUBCLASSES[record.record_type]
    if not _has_only_modeled_reactors(raw_tags):
        _reject()
    for tag in raw_tags:
        if (
            tag.code == 999
            or 1000 <= tag.code <= 1071
            or tag.code == 360
        ):
            _reject()
        if tag.code == 100 and tag.value not in allowed_subclasses:
            _reject()


def _assert_safe_table_tags(
    record: RawRecord, raw_tags: tuple[RawTag, ...]
) -> None:
    """Reject table extension data except modeled LAYER transparency."""

    if (
        not _has_only_modeled_reactors(raw_tags)
        or any(tag.code == 999 or tag.code == 360 for tag in raw_tags)
    ):
        _reject()
    has_xdata = any(1000 <= tag.code <= 1071 for tag in raw_tags)
    if has_xdata and (
        record.record_type != "LAYER"
        or not _has_only_modeled_layer_transparency(raw_tags)
    ):
        _reject()


def _validate_modeled_records(
    sections: Mapping[str, tuple[RawTag, ...]],
) -> tuple[RawRecord, ...]:
    """Parse and validate every modeled raw record before ezdxf is invoked."""

    all_records: list[RawRecord] = []
    for section in _MODELED_RECORD_SECTIONS:
        records, raw_records = _parse_section_records(section, sections[section][2:-1])
        if section == "TABLES":
            _assert_table_boundaries(records, raw_records)
        elif section == "BLOCKS":
            _assert_block_boundaries(records)
        for record, raw_tags in zip(records, raw_records, strict=True):
            if section == "TABLES":
                _assert_safe_table_tags(record, raw_tags)
            elif section == "BLOCKS":
                if record.record_type in _BLOCK_SUBCLASSES:
                    _assert_no_unmodeled_block_header_tags(record, raw_tags)
                else:
                    _assert_no_unmodeled_graphic_tags(record, raw_tags)
            elif section == "ENTITIES":
                _assert_no_unmodeled_graphic_tags(record, raw_tags)
            else:
                # OBJECTS are represented by the complete object manifest.
                # Their legal app-data and XDATA forms are admitted only if
                # the later source-to-normalized congruence proves every raw
                # tag survived. DXF comments have no modeled representation.
                if any(tag.code == 999 for tag in raw_tags):
                    _reject()
            all_records.append(record)
    return tuple(all_records)


def _record_match_key(record: RawRecord) -> tuple[str, str, str | None]:
    """Return the stable matching key used for normalized-record congruence."""

    if record.handle is not None:
        return (record.section, record.record_type, record.handle)
    if record.record_type == "TABLE":
        name = next(
            (
                value
                for code, value in record.canonical_tags
                if code == 2
            ),
            None,
        )
        return (
            record.section,
            record.record_type,
            canonical_sha256({"table_name": name}),
        )
    return (record.section, record.record_type, None)


def _filtered_tags(
    record: RawRecord,
    ignored_codes_by_handle: Mapping[str, frozenset[int]],
) -> tuple[tuple[int, Any], ...]:
    ignored = (
        ignored_codes_by_handle.get(record.handle, frozenset())
        if record.handle is not None
        else frozenset()
    )
    return tuple(tag for tag in record.canonical_tags if tag[0] not in ignored)


def _is_ordered_subsequence(
    source: tuple[tuple[int, Any], ...],
    normalized: tuple[tuple[int, Any], ...],
) -> bool:
    """Require every source tag to survive in original relative order."""

    source_index = 0
    for tag in normalized:
        if source_index < len(source) and tag == source[source_index]:
            source_index += 1
    return source_index == len(source)


def _is_unordered_submultiset(
    source: tuple[tuple[int, Any], ...],
    normalized: tuple[tuple[int, Any], ...],
) -> bool:
    """Require every source tag/value to survive, allowing only known reorder."""

    remaining = list(normalized)
    for tag in source:
        try:
            remaining.remove(tag)
        except ValueError:
            return False
    return True


def _is_writer_normalized_record(
    source_record: RawRecord,
    normalized_record: RawRecord,
    source_tags: tuple[tuple[int, Any], ...],
    normalized_tags: tuple[tuple[int, Any], ...],
) -> bool:
    """Recognize the fixed, reviewed ezdxf R2018 serializer differences."""

    record_kind = (source_record.section, source_record.record_type)
    if record_kind in _WRITER_REORDER_RECORD_TYPES:
        return _is_unordered_submultiset(source_tags, normalized_tags)
    if record_kind != ("TABLES", "TABLE"):
        return False
    # TABLE group 70 is the writer-maintained member count. Every actual
    # table member remains separately raw-bound, so only this one derived
    # cardinality value may change while all other header tags stay ordered.
    source_without_count = tuple(tag for tag in source_tags if tag[0] != 70)
    normalized_without_count = tuple(tag for tag in normalized_tags if tag[0] != 70)
    source_counts = [tag for tag in source_tags if tag[0] == 70]
    normalized_counts = [tag for tag in normalized_tags if tag[0] == 70]
    return (
        len(source_counts) == 1
        and len(normalized_counts) == 1
        and _is_ordered_subsequence(
            source_without_count,
            normalized_without_count,
        )
    )


def assert_normalized_records_match(
    source: RawDxfPreflight,
    normalized: RawDxfPreflight,
    *,
    ignored_codes_by_handle: Mapping[str, frozenset[int]] | None = None,
) -> None:
    """Prove a raw source record was not silently dropped by normalization.

    This compares parser-independent raw records, not ``entity.export_dxf``.
    A source record may gain writer defaults, but every source tag must remain
    in the normalized record in the same relative order.  Only the already
    narrow, caller-proven volatile object tag exception may be ignored.
    """

    ignored = ignored_codes_by_handle or {}
    normalized_by_key: dict[tuple[str, str, str | None], list[RawRecord]] = (
        defaultdict(list)
    )
    for record in normalized.modeled_records:
        normalized_by_key[_record_match_key(record)].append(record)

    for source_record in source.modeled_records:
        candidates = normalized_by_key.get(_record_match_key(source_record), [])
        source_tags = _filtered_tags(source_record, ignored)
        matched_index: int | None = None
        for index, candidate in enumerate(candidates):
            candidate_tags = _filtered_tags(candidate, ignored)
            if (
                _is_ordered_subsequence(source_tags, candidate_tags)
                or _is_writer_normalized_record(
                    source_record,
                    candidate,
                    source_tags,
                    candidate_tags,
                )
            ):
                matched_index = index
                break
        if matched_index is None:
            raise PipelineError(
                ErrorCode.UNSAFE_ENTITY_TYPE,
                "raw record normalization mismatch",
            )
        del candidates[matched_index]
    if any(
        _record_match_key(record) not in _NORMALIZED_EXTRA_RECORD_ALLOWLIST
        for candidates in normalized_by_key.values()
        for record in candidates
    ):
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE,
            "unexpected normalized raw record",
        )


def assert_normalized_header_match(
    source: RawDxfPreflight,
    normalized: RawDxfPreflight,
) -> None:
    """Require the complete modeled raw HEADER shape to survive normalization."""

    if source.raw_header_manifest_digest != normalized.raw_header_manifest_digest:
        raise PipelineError(
            ErrorCode.UNSAFE_ENTITY_TYPE,
            "raw HEADER normalization mismatch",
        )


def preflight_ascii_dxf_bytes(raw: bytes) -> RawDxfPreflight:
    """Preflight exact ASCII DXF bytes before ezdxf can normalize them.

    A present ACDSDATA section has exactly one supported representation:
    ``SECTION / ACDSDATA / ENDSEC`` with no records or metadata.  The tag
    sequence is intentionally documented here and enforced before model load.
    """

    tags = _parse_tags(raw)
    section_names, sections = _validate_structure(tags)
    header_tags = sections["HEADER"][2:-1]
    _assert_supported_r2018_header(header_tags)
    raw_header_manifest_digest = _parse_header_manifest(header_tags)
    acdsdata = sections.get("ACDSDATA")
    if acdsdata is not None and tuple(
        (tag.code, tag.value) for tag in acdsdata
    ) != _CANONICAL_EMPTY_ACDSDATA_TAGS:
        _reject()
    classes = _parse_classes(sections["CLASSES"][2:-1])
    modeled_records = _validate_modeled_records(sections)
    semantic_digests = [record.raw_digest for record in classes]
    wire_digests = [record.wire_digest for record in classes]
    return RawDxfPreflight(
        raw_bytes_digest=sha256(raw).hexdigest(),
        section_structure_digest=canonical_sha256(
            {"top_level_sections": list(section_names)}
        ),
        raw_header_manifest_digest=raw_header_manifest_digest,
        classes_manifest_digest=canonical_sha256(
            {
                "section": "CLASSES",
                # DXF writers may reorder CLASS records while retaining their
                # exact declarations. Identity sorting keeps this preservation
                # binding canonical without collapsing source records.
                "records": [
                    {
                        "identity": list(record.identity),
                        "raw_digest": record.raw_digest,
                    }
                    for record in sorted(classes, key=lambda record: record.identity)
                ],
            }
        ),
        classes_multiset_digest=canonical_sha256(
            {"section": "CLASSES", "records": sorted(semantic_digests)}
        ),
        classes_wire_manifest_digest=canonical_sha256(
            {
                "section": "CLASSES",
                "records": [
                    {
                        "identity": list(record.identity),
                        "wire_digest": record.wire_digest,
                    }
                    for record in sorted(classes, key=lambda record: record.identity)
                ],
            }
        ),
        classes_wire_multiset_digest=canonical_sha256(
            {"section": "CLASSES", "records": sorted(wire_digests)}
        ),
        classes_record_count=len(classes),
        classes=classes,
        acdsdata_present=acdsdata is not None,
        modeled_records=modeled_records,
        modeled_records_digest=canonical_sha256(
            {
                "records": [
                    {
                        "section": record.section,
                        "record_type": record.record_type,
                        "handle": record.handle,
                        "raw_digest": record.raw_digest,
                    }
                    for record in modeled_records
                ]
            }
        ),
    )


def preflight_ascii_dxf(path: Path) -> RawDxfPreflight:
    """Read and preflight one exact staged ASCII DXF file before loading it."""

    return preflight_ascii_dxf_bytes(_read_bounded(path))
