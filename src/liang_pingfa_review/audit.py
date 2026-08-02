"""Phase-one, read-only DWG audit construction."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Protocol
from uuid import uuid4

import ezdxf

from . import __version__
from .canonical import (
    SourceDescription,
    acquire_source_lease,
    attach_integrity,
    describe_leased_source,
    format_utc,
    source_lease_matches,
    utc_now,
)
from .contracts import validate_artifact
from .errors import ErrorCode, PipelineError
from .oda import SUPPORTED_ODA_VERSION, staged_dwg_to_dxf
from .overlay_profile import assess_auxiliary_overlays, profile_findings
from .snapshots import Snapshot, snapshot_dxf
from .temporary import PrivateWorkspace
from .topology_profile import (
    TopologyProfile,
    assess_beam_topology,
    topology_snapshot_context,
)


class Converter(Protocol):
    """The narrow converter surface needed by phase one and test doubles."""

    version: str

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path],
    ) -> Path:
        """Convert one isolated staged input."""


def _assert_toolchain(oda_version: str) -> None:
    if oda_version != SUPPORTED_ODA_VERSION:
        raise PipelineError(ErrorCode.TOOL_VERSION_MISMATCH, "converter version mismatch")
    if ezdxf.__version__ != "1.4.4":
        raise PipelineError(ErrorCode.TOOL_VERSION_MISMATCH, "ezdxf version mismatch")


def build_audit(
    snapshot: Snapshot,
    source: SourceDescription,
    *,
    oda_version: str,
    now: datetime | None = None,
    topology_profile: TopologyProfile | None = None,
) -> dict[str, Any]:
    """Build a signed audit artifact from an immutable temporary DXF snapshot."""

    _assert_toolchain(oda_version)
    current_time = now or utc_now()
    profile = assess_auxiliary_overlays(snapshot)
    findings, targets = profile_findings(snapshot, profile)
    target_handles = {target["handle"] for target in targets}
    pre_state = snapshot.preservation_state(
        paired_right_panel_digest=profile.paired_right_panel_digest
    )
    post_state = snapshot.preservation_state(
        excluded_handles=target_handles,
        paired_right_panel_digest=profile.paired_right_panel_digest,
    )
    artifact = {
        "schema_version": "liang-pingfa/audit/v1",
        "audit_id": f"audit-{uuid4().hex}",
        "created_at": format_utc(current_time),
        "expires_at": format_utc(current_time + timedelta(hours=24)),
        "scope": "representation-and-readability-only",
        "source": source.to_artifact(),
        "toolchain": {
            "oda_file_converter": {"version": oda_version},
            "ezdxf": {"version": ezdxf.__version__},
            "application": {"version": __version__},
        },
        "conversion": {
            "intermediary_format": "DXF",
            "output_version": "AC1032",
            "isolated_staging": True,
            "recursion": 0,
            "audit_mode": 1,
        },
        "inventory": snapshot.inventory(),
        "fingerprints": {
            "full_manifest_digest": pre_state["full_manifest_digest"],
            "content_multiset_digest": pre_state["content_multiset_digest"],
            "entity_order_manifest_digest": pre_state[
                "entity_order_manifest_digest"
            ],
            "non_target_manifest_digest": post_state["full_manifest_digest"],
            "paired_right_panel_digest": profile.paired_right_panel_digest,
            "bounds_fingerprint": snapshot.bounds_fingerprint,
            "bounds_has_data": snapshot.bounds_has_data,
        },
        "findings": findings,
        "audited_targets": targets,
    }
    if topology_profile is not None:
        # The topology branch is strictly additive, read-only evidence.  The
        # v1 fields remain the unchanged overlay/mutation authorization view.
        artifact["schema_version"] = "liang-pingfa/audit/v2"
        artifact["topology_assessment"] = assess_beam_topology(
            snapshot,
            topology_profile,
        )
    signed = attach_integrity(artifact)
    return validate_artifact("audit", signed)


@contextmanager
def bound_audit_dwg(
    source_path: Path,
    converter: Converter,
    *,
    now: datetime | None = None,
    topology_profile: TopologyProfile | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield an audit while its source chain/file lease remains retained.

    Phase-one callers that publish public artifacts must perform that
    publication inside this context.  The source file and every lexical
    ancestor remain no-follow and immutable through conversion, snapshot,
    artifact construction, and the caller's no-replace publication.
    """

    _assert_toolchain(converter.version)
    source_lease = acquire_source_lease(source_path)
    try:
        if source_lease.path.suffix.casefold() != ".dwg":
            raise PipelineError(ErrorCode.INVALID_ARGUMENT, "audit requires a DWG")
        source = describe_leased_source(source_lease)
        with PrivateWorkspace(prefix="liang-pingfa-audit-") as workspace:
            dxf_path = staged_dwg_to_dxf(
                source_lease,
                workspace,
                converter,  # type: ignore[arg-type]
            )
            snapshot = snapshot_dxf(
                dxf_path,
                include_topology_evidence=topology_profile is not None,
                topology_context=(
                    topology_snapshot_context(topology_profile)
                    if topology_profile is not None
                    else None
                ),
            )
            if not source_lease_matches(source_lease, source.to_artifact()):
                raise PipelineError(
                    ErrorCode.SOURCE_CHANGED_DURING_RUN,
                    "source changed while phase-one audit ran",
                )
            audit = build_audit(
                snapshot,
                source,
                oda_version=converter.version,
                now=now,
                topology_profile=topology_profile,
            )
            yield audit
            if not source_lease_matches(source_lease, source.to_artifact()):
                raise PipelineError(
                    ErrorCode.SOURCE_CHANGED_DURING_RUN,
                    "source changed during phase-one artifact publication",
                )
    finally:
        source_lease.close()


def audit_dwg(
    source_path: Path,
    converter: Converter,
    *,
    now: datetime | None = None,
    topology_profile: TopologyProfile | None = None,
) -> dict[str, Any]:
    """Audit a DWG without handing ODA the original directory or output path."""

    with bound_audit_dwg(
        source_path,
        converter,
        now=now,
        topology_profile=topology_profile,
    ) as audit:
        return audit
