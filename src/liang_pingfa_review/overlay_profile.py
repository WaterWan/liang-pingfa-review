"""Narrow, evidence-first auxiliary-overlay eligibility profile.

This module deliberately recognizes only a geometrically evidenced left/right
panel pair.  It never infers an edit target from text content, a layer alone,
or proximity alone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .canonical import canonical_sha256
from .snapshots import Bounds, EntityRecord, PLANE_TOLERANCE, Snapshot


PROFILE_NAME = "auxiliary-overlay-text-delete/v1"
OVERLAY_LAYERS = frozenset({"temp", "textarea"})
_TOLERANCE = PLANE_TOLERANCE
_MAX_PANEL_LINES = 256


@dataclass(frozen=True)
class ClosedPanel:
    """Private evidence for one connected, closed, axis-aligned rectangular panel."""

    bounds: Bounds
    frame_handles: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True)
class _FrameLine:
    """One axis-aligned frame segment in local coordinates."""

    record: EntityRecord
    orientation: str
    start: float
    end: float
    fixed: float
    elevation: float

    @property
    def key(self) -> tuple[str, float, float, float, float]:
        return (
            self.orientation,
            _coordinate_key(self.start),
            _coordinate_key(self.end),
            _coordinate_key(self.fixed),
            _coordinate_key(self.elevation),
        )


@dataclass(frozen=True)
class PanelPair:
    """Private structural evidence for a translated pair of closed panels."""

    dx: float
    dy: float
    left: ClosedPanel
    right: ClosedPanel
    fingerprint: str

    @property
    def left_bounds(self) -> Bounds:
        return self.left.bounds

    @property
    def right_bounds(self) -> Bounds:
        return self.right.bounds


@dataclass(frozen=True)
class TargetAssessment:
    """A profile result for one candidate direct Modelspace TEXT entity."""

    record: EntityRecord
    actionable: bool
    panel_pair: PanelPair | None
    unique_content: bool
    corresponding_right_absent: bool
    corresponding_right_ambiguous: bool
    visible_interference: bool


@dataclass(frozen=True)
class ProfileResult:
    """Structured, privacy-safe overlay profile assessment."""

    assessments: tuple[TargetAssessment, ...]
    paired_right_panel_digest: str


def _coordinate_key(value: float) -> float:
    """Normalize exact topology keys without widening the geometry tolerance."""

    return round(value, 6)


def _same_plane(first: float | None, second: float | None) -> bool:
    """Require two supported profile geometries to share one visible plane."""

    return (
        first is not None
        and second is not None
        and isfinite(first)
        and isfinite(second)
        and abs(first - second) <= _TOLERANCE
    )


def _axis_aligned_frame_line(record: EntityRecord) -> _FrameLine | None:
    """Support only finite, planar, non-degenerate horizontal or vertical lines."""

    if (
        not record.visible
        or record.bounds is None
        or record.plane_elevation is None
    ):
        return None
    minimum = record.bounds.minimum
    maximum = record.bounds.maximum
    if not all(isfinite(value) for value in (*minimum, *maximum)):
        return None
    if (
        abs(maximum[2] - minimum[2]) > _TOLERANCE
        or not _same_plane(minimum[2], record.plane_elevation)
        or not _same_plane(maximum[2], record.plane_elevation)
    ):
        return None
    width = maximum[0] - minimum[0]
    height = maximum[1] - minimum[1]
    if width > _TOLERANCE and abs(height) <= _TOLERANCE:
        return _FrameLine(
            record=record,
            orientation="horizontal",
            start=minimum[0],
            end=maximum[0],
            fixed=minimum[1],
            elevation=record.plane_elevation,
        )
    if height > _TOLERANCE and abs(width) <= _TOLERANCE:
        return _FrameLine(
            record=record,
            orientation="vertical",
            start=minimum[1],
            end=maximum[1],
            fixed=minimum[0],
            elevation=record.plane_elevation,
        )
    return None


def _closed_panels(snapshot: Snapshot) -> tuple[ClosedPanel, ...]:
    """Find only four-edge frames whose endpoints form one connected rectangle."""

    lines = [
        frame_line
        for record in snapshot.records
        if record.layout == "modelspace" and record.entity_type == "LINE"
        if (frame_line := _axis_aligned_frame_line(record)) is not None
    ]
    # Dense or irregular grids are not evidence. A bounded, strict recognizer
    # is safer than attempting to infer frames from arbitrary linework.
    if len(lines) > _MAX_PANEL_LINES:
        return ()

    by_key: dict[tuple[str, float, float, float, float], list[_FrameLine]] = (
        defaultdict(list)
    )
    horizontal_by_span: dict[tuple[float, float, float], list[_FrameLine]] = (
        defaultdict(list)
    )
    for line in lines:
        by_key[line.key].append(line)
        if line.orientation == "horizontal":
            horizontal_by_span[
                (
                    _coordinate_key(line.start),
                    _coordinate_key(line.end),
                    _coordinate_key(line.elevation),
                )
            ].append(line)

    panels: dict[tuple[str, ...], ClosedPanel] = {}
    for bottom in lines:
        if bottom.orientation != "horizontal":
            continue
        span_key = (
            _coordinate_key(bottom.start),
            _coordinate_key(bottom.end),
            _coordinate_key(bottom.elevation),
        )
        for top in horizontal_by_span[span_key]:
            if top.fixed <= bottom.fixed + _TOLERANCE:
                continue
            left_key = (
                "vertical",
                _coordinate_key(bottom.fixed),
                _coordinate_key(top.fixed),
                _coordinate_key(bottom.start),
                _coordinate_key(bottom.elevation),
            )
            right_key = (
                "vertical",
                _coordinate_key(bottom.fixed),
                _coordinate_key(top.fixed),
                _coordinate_key(bottom.end),
                _coordinate_key(bottom.elevation),
            )
            left_edges = by_key.get(left_key, [])
            right_edges = by_key.get(right_key, [])
            # Multiple coincident edges make topology ambiguous, so do not
            # select a subset as a trusted closed panel.
            if len(left_edges) != 1 or len(right_edges) != 1:
                continue
            frame = (bottom, top, left_edges[0], right_edges[0])
            if len({line.record.handle for line in frame}) != 4:
                continue
            handles = tuple(
                sorted((line.record.handle for line in frame), key=lambda handle: int(handle, 16))
            )
            bounds = Bounds(
                (bottom.start, bottom.fixed, bottom.elevation),
                (bottom.end, top.fixed, top.elevation),
            )
            panels[handles] = ClosedPanel(
                bounds=bounds,
                frame_handles=handles,
                fingerprint=canonical_sha256(
                    {
                        "closed_rectangular_frame": [
                            line.record.identity_fingerprint
                            for line in sorted(frame, key=lambda item: int(item.record.handle, 16))
                        ]
                    }
                ),
            )
    return tuple(
        sorted(
            panels.values(),
            key=lambda panel: (
                panel.bounds.minimum,
                panel.bounds.maximum,
                panel.fingerprint,
            ),
        )
    )


def _translated_bounds_match(left: Bounds, right: Bounds, dx: float, dy: float) -> bool:
    """Require the complete closed rectangle, not an envelope, to translate exactly."""

    translated = left.translated(dx, dy)
    return all(
        abs(first - second) <= _TOLERANCE
        for first, second in zip(
            (*translated.minimum, *translated.maximum),
            (*right.minimum, *right.maximum),
        )
    )


def _build_panel_pairs(snapshot: Snapshot) -> tuple[PanelPair, ...]:
    """Find horizontally translated pairs of independently closed panels."""

    panels = _closed_panels(snapshot)
    pairs: list[PanelPair] = []
    for left in panels:
        for right in panels:
            dx = right.bounds.minimum[0] - left.bounds.minimum[0]
            dy = right.bounds.minimum[1] - left.bounds.minimum[1]
            if dx <= _TOLERANCE or abs(dy) > _TOLERANCE:
                continue
            if not _translated_bounds_match(left.bounds, right.bounds, dx, dy):
                continue
            pairs.append(
                PanelPair(
                    dx=dx,
                    dy=dy,
                    left=left,
                    right=right,
                    fingerprint=canonical_sha256(
                        {
                            "translation": [dx, dy],
                            "left_closed_frame": left.fingerprint,
                            "right_closed_frame": right.fingerprint,
                        }
                    ),
                )
            )
    return tuple(sorted(pairs, key=lambda pair: pair.fingerprint))


def _overlay_text_inventory(snapshot: Snapshot) -> list[EntityRecord]:
    """Inventory every direct allowed-layer overlay TEXT before geometry filtering.

    Unsupported OCS, alignment, elevation, or visibility data must remain in
    this inventory.  Treating it as absent would turn ambiguity into deletion
    permission for a matching left-side candidate.
    """

    return [
        record
        for record in snapshot.records
        if (
            record.entity_type == "TEXT"
            and record.layout == "modelspace"
            and record.layer_name.casefold() in OVERLAY_LAYERS
        )
    ]


def _line_intersects_text_bounds(line: Bounds, text: Bounds) -> bool:
    """Test a supported line segment against the real supported TEXT rectangle."""

    width = line.maximum[0] - line.minimum[0]
    height = line.maximum[1] - line.minimum[1]
    if width > _TOLERANCE and abs(height) <= _TOLERANCE:
        return (
            text.minimum[1] - _TOLERANCE
            <= line.minimum[1]
            <= text.maximum[1] + _TOLERANCE
            and line.maximum[0] + _TOLERANCE >= text.minimum[0]
            and text.maximum[0] + _TOLERANCE >= line.minimum[0]
        )
    if height > _TOLERANCE and abs(width) <= _TOLERANCE:
        return (
            text.minimum[0] - _TOLERANCE
            <= line.minimum[0]
            <= text.maximum[0] + _TOLERANCE
            and line.maximum[1] + _TOLERANCE >= text.minimum[1]
            and text.maximum[1] + _TOLERANCE >= line.minimum[1]
        )
    return False


def _interferes(candidate: EntityRecord, records: tuple[EntityRecord, ...]) -> bool:
    """Accept only a real supported segment/text intersection as interference."""

    if (
        not candidate.visible
        or candidate.bounds is None
        or candidate.plane_elevation is None
    ):
        return False
    for record in records:
        if record.handle == candidate.handle or record.layout != "modelspace":
            continue
        if (
            record.entity_type != "LINE"
            or not record.visible
            or record.bounds is None
            or not _same_plane(record.plane_elevation, candidate.plane_elevation)
        ):
            continue
        if _line_intersects_text_bounds(record.bounds, candidate.bounds):
            return True
    return False


def _finite_anchor(record: EntityRecord) -> tuple[float, float] | None:
    """Return a finite raw TEXT insertion point, if one is available."""

    if record.anchor is None or not all(isfinite(value) for value in record.anchor):
        return None
    return record.anchor


def _right_panel_membership(
    record: EntityRecord,
    pair: PanelPair,
) -> tuple[bool, bool]:
    """Classify right-panel membership as safely assigned or ambiguous.

    The first return value means that the candidate could be relevant to the
    right panel.  The second means it cannot safely provide a clean/absent
    counterpart conclusion.  A TEXT without supported bounds is ambiguous for
    every pair: its insertion point cannot conservatively prove that rendered
    geometry is disjoint from a panel.
    """

    if record.bounds is not None:
        if not pair.right_bounds.overlaps(record.bounds):
            return False, False
        if not pair.right_bounds.contains_bounds_xy(record.bounds):
            return True, True
        if not _same_plane(record.plane_elevation, pair.right_bounds.minimum[2]):
            return True, True
        return True, False

    # Unsupported geometry has no supported extent from which panel
    # disjointness can be proven.  Do not infer one from an insertion point,
    # text height, alignment, or rotation.
    return True, True


def _right_overlay_status(
    candidate: EntityRecord,
    pair: PanelPair,
    overlay_inventory: list[EntityRecord],
) -> str:
    """Return ``absent``, ``present``, or ``ambiguous`` for a right counterpart."""

    if candidate.bounds is None or candidate.plane_elevation is None:
        return "ambiguous"
    expected = candidate.bounds.translated(pair.dx, pair.dy)
    for other in overlay_inventory:
        if other.handle == candidate.handle:
            continue
        relevant, ambiguous = _right_panel_membership(other, pair)
        if not relevant:
            continue
        # Unsupported geometry, a non-coplanar plane, a partial/boundary
        # assignment, or an invisible candidate cannot prove semantic absence.
        if ambiguous:
            return "ambiguous"
        assert other.bounds is not None
        if not expected.overlaps(other.bounds):
            continue
        if not other.visible:
            return "ambiguous"
        return "present"
    return "absent"


def _matching_pairs(candidate: EntityRecord, pairs: tuple[PanelPair, ...]) -> list[PanelPair]:
    if candidate.bounds is None or candidate.plane_elevation is None:
        return []
    return [
        pair
        for pair in pairs
        if (
            _same_plane(candidate.plane_elevation, pair.left_bounds.minimum[2])
            and _same_plane(candidate.plane_elevation, pair.right_bounds.minimum[2])
            and pair.left_bounds.contains_bounds_xy(candidate.bounds)
            and pair.right_bounds.contains_bounds_xy(
                candidate.bounds.translated(pair.dx, pair.dy)
            )
        )
    ]


def _right_manifest_digest(
    snapshot: Snapshot, selected_pairs: list[PanelPair]
) -> str:
    if not selected_pairs:
        return canonical_sha256([])
    records: dict[str, dict[str, Any]] = {}
    for pair in selected_pairs:
        for record in snapshot.records:
            if (
                record.layout == "modelspace"
                and record.bounds is not None
                and pair.right_bounds.contains_bounds_xy(record.bounds)
            ):
                records[record.handle] = record.public()
            elif (
                record.layout == "modelspace"
                and record.entity_type == "TEXT"
                and record.layer_name.casefold() in OVERLAY_LAYERS
                and (anchor := _finite_anchor(record)) is not None
                and pair.right_bounds.contains_xy(anchor)
            ):
                # Unsupported overlay geometry still belongs to the protected
                # right-side evidence inventory when its raw insertion maps
                # to the panel. Full preservation binds every record as well.
                records[record.handle] = record.public()
    return canonical_sha256([records[handle] for handle in sorted(records)])


def assess_auxiliary_overlays(snapshot: Snapshot) -> ProfileResult:
    """Assess only the exact, direct overlay class permitted by the release."""

    overlay_inventory = _overlay_text_inventory(snapshot)
    panel_pairs = _build_panel_pairs(snapshot)
    content_counts = Counter(record.content_fingerprint for record in snapshot.records)
    assessments: list[TargetAssessment] = []
    for candidate in sorted(overlay_inventory, key=lambda item: int(item.handle, 16)):
        matches = _matching_pairs(candidate, panel_pairs)
        # Multiple geometries that can explain a target are ambiguity, not a
        # selection heuristic. The profile intentionally refuses that case.
        pair = matches[0] if len(matches) == 1 else None
        unique_content = content_counts[candidate.content_fingerprint] == 1
        right_status = (
            _right_overlay_status(candidate, pair, overlay_inventory)
            if pair is not None
            else "ambiguous"
        )
        right_absent = right_status == "absent"
        interference = _interferes(candidate, snapshot.records)
        actionable = (
            candidate.visible
            and candidate.plane_elevation is not None
            and pair is not None
            and unique_content
            and right_absent
            and interference
        )
        assessments.append(
            TargetAssessment(
                record=candidate,
                actionable=actionable,
                panel_pair=pair,
                unique_content=unique_content,
                corresponding_right_absent=right_absent,
                corresponding_right_ambiguous=right_status == "ambiguous",
                visible_interference=interference,
            )
        )
    return ProfileResult(
        assessments=tuple(assessments),
        # Preserve every independently established right-side panel. This
        # remains stable after a permitted left-side deletion, so verification
        # can prove that paired content did not drift during the ODA cycle.
        paired_right_panel_digest=_right_manifest_digest(snapshot, list(panel_pairs)),
    )


def finding_identifier(kind: str, value: str) -> str:
    """Make a stable opaque finding identifier without exposing source content."""

    return f"finding-{canonical_sha256({'kind': kind, 'value': value})[:24]}"


def target_identifier(record: EntityRecord) -> str:
    """Make a stable opaque target identifier from a protected identity."""

    return f"target-{canonical_sha256({'identity': record.identity_fingerprint})[:24]}"


def profile_findings(snapshot: Snapshot, result: ProfileResult) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Render controlled Chinese findings and audit targets without raw CAD data."""

    findings: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    actionable_pairs: set[str] = set()
    for assessment in result.assessments:
        record = assessment.record
        if assessment.actionable:
            target_id = target_identifier(record)
            finding_id = finding_identifier("target", record.identity_fingerprint)
            assert assessment.panel_pair is not None
            actionable_pairs.add(assessment.panel_pair.fingerprint)
            findings.append(
                {
                    "finding_id": finding_id,
                    "status": "疑似不一致",
                    "object_position": "左侧对应区域",
                    "field": "字段分类与位置绑定",
                    "visible_evidence": "已确认辅助覆盖干扰",
                    "reasoning": "辅助覆盖阻断字段分类或位置绑定",
                    "source_topics": ["平面与截面表达、集中与原位作用域"],
                    "unreadable_parts": "受覆盖字段和引出关系",
                    "next_step": "生成受控删除计划",
                    "actionability": True,
                    "target_id": target_id,
                }
            )
            targets.append(
                {
                    "target_id": target_id,
                    "finding_id": finding_id,
                    "handle": record.handle,
                    "entity_type": "TEXT",
                    "layout": "modelspace",
                    "identity_fingerprint": record.identity_fingerprint,
                    "content_fingerprint": record.content_fingerprint,
                    "profile": PROFILE_NAME,
                    "unique_content_fingerprint": True,
                    "unsupported_data": False,
                    "left_panel_evidence": True,
                    "corresponding_right_absent": True,
                    "visible_interference": True,
                    "panel_evidence_fingerprint": assessment.panel_pair.fingerprint,
                }
            )
        else:
            finding_id = finding_identifier("insufficient", record.identity_fingerprint)
            findings.append(
                {
                    "finding_id": finding_id,
                    "status": "证据不足",
                    "object_position": "未建立面板对应关系",
                    "field": "字段分类与位置绑定",
                    "visible_evidence": "面板或干扰证据不足",
                    "reasoning": "不猜测缺失的面板或干扰关系",
                    "source_topics": ["平面与截面综合阅读"],
                    "unreadable_parts": "面板对应关系或干扰范围",
                    "next_step": "补充完整可读视图后重新审计",
                    "actionability": False,
                    "target_id": None,
                }
            )

    for pair_fingerprint in sorted(actionable_pairs):
        findings.append(
            {
                "finding_id": finding_identifier("right", pair_fingerprint),
                "status": "一致",
                "object_position": "右侧对应区域",
                "field": "字段分类与位置绑定",
                "visible_evidence": "对应区域未见辅助覆盖",
                "reasoning": "对应区域表示可读且位置可绑定",
                "source_topics": ["平面与截面表达、集中与原位作用域"],
                "unreadable_parts": "无",
                "next_step": "保持只读结论",
                "actionability": False,
                "target_id": None,
            }
        )

    if not findings:
        if panel_pairs := _build_panel_pairs(snapshot):
            findings.append(
                {
                    "finding_id": finding_identifier("clear", panel_pairs[0].fingerprint),
                    "status": "一致",
                    "object_position": "右侧对应区域",
                    "field": "字段分类与位置绑定",
                    "visible_evidence": "未见可操作辅助覆盖",
                    "reasoning": "未建立可修改的精确目标",
                    "source_topics": ["平面与截面表达、集中与原位作用域"],
                    "unreadable_parts": "无",
                    "next_step": "保持只读结论",
                    "actionability": False,
                    "target_id": None,
                }
            )
        else:
            findings.append(
                {
                    "finding_id": finding_identifier("unknown", snapshot.bounds_fingerprint),
                    "status": "证据不足",
                    "object_position": "未建立面板对应关系",
                    "field": "字段分类与位置绑定",
                    "visible_evidence": "面板或干扰证据不足",
                    "reasoning": "不猜测缺失的面板或干扰关系",
                    "source_topics": ["平面与截面综合阅读"],
                    "unreadable_parts": "面板对应关系或干扰范围",
                    "next_step": "补充完整可读视图后重新审计",
                    "actionability": False,
                    "target_id": None,
                }
            )
    findings.sort(key=lambda item: item["finding_id"])
    targets.sort(key=lambda item: int(item["handle"], 16))
    return findings, targets
