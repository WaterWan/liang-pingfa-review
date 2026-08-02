"""Shared opaque topology provenance identifiers.

The public topology artifact must be independently verifiable after signing.
These functions deliberately accept only public, privacy-safe entity
fingerprints, roles, and already-established ownership relationships.  In
particular, source handles, text, layers, and private geometry never affect an
identifier.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .canonical import canonical_sha256


def _opaque_identifier(prefix: str, value: Mapping[str, object]) -> str:
    return f"{prefix}-{canonical_sha256(dict(value))[:24]}"


def entity_provenance(
    identity_fingerprint: str,
    content_fingerprint: str,
) -> dict[str, str]:
    """Return the only entity identity material allowed in topology IDs."""

    # Content remains bound by every trace and audit manifest, but only the
    # source entity's established identity is admissible in topology IDs.
    del content_fingerprint
    return {"identity_fingerprint": identity_fingerprint}


def derive_trace_id(
    identity_fingerprint: str,
    content_fingerprint: str,
    role: str,
) -> str:
    return _opaque_identifier(
        "trace",
        {"role": role, "entity": entity_provenance(identity_fingerprint, content_fingerprint)},
    )


def derive_chain_id(
    beam_entities: Iterable[Mapping[str, str]],
) -> str:
    """Derive a chain from its admitted beam-edge provenance only."""

    members = sorted(
        (
            {
                "identity_fingerprint": entity["identity_fingerprint"],
            }
            for entity in beam_entities
        ),
        key=lambda entity: entity["identity_fingerprint"],
    )
    return _opaque_identifier("chain", {"role": "admitted-chain", "beam_entities": members})


def derive_support_id(
    chain_id: str,
    support_trace_id: str,
    identity_fingerprint: str,
    content_fingerprint: str,
) -> str:
    """Bind a support to one chain and one canonical support-geometry trace."""

    return _opaque_identifier(
        "support",
        {
            "role": "support",
            "chain_id": chain_id,
            "support_trace_id": support_trace_id,
            "support_entity": entity_provenance(
                identity_fingerprint,
                content_fingerprint,
            ),
        },
    )


def derive_span_id(
    chain_id: str,
    left_support_id: str,
    right_support_id: str,
) -> str:
    """Bind a span to its chain and its two canonical adjacent supports."""

    adjacent_support_ids = sorted((left_support_id, right_support_id))
    return _opaque_identifier(
        "span",
        {
            "role": "span",
            "chain_id": chain_id,
            "adjacent_support_ids": adjacent_support_ids,
        },
    )


def derive_annotation_target_provenance_id(
    trace_id: str,
    chain_id: str,
    support_id: str | None,
    span_id: str | None,
) -> str:
    """Bind an annotation source trace to exactly one canonical target."""

    return _opaque_identifier(
        "target",
        {
            "role": "annotation-target",
            "trace_id": trace_id,
            "chain_id": chain_id,
            "support_id": support_id,
            "span_id": span_id,
        },
    )


def derive_topology_finding_id(
    trace_id: str,
    status: str,
    role: str,
    chain_id: str | None,
    support_id: str | None,
    span_id: str | None,
) -> str:
    return _opaque_identifier(
        "finding",
        {
            "trace_id": trace_id,
            "status": status,
            "role": role,
            "target": {
                "chain_id": chain_id,
                "support_id": support_id,
                "span_id": span_id,
            },
        },
    )
