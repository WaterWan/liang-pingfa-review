# Native bridge contract declarations

This is an SDK-free `net8.0` contract project. It contains only DTOs and
interfaces used by the optional local native-bridge protocol.

It does **not** contain a CAD host adapter, plugin implementation, binaries,
object enablers, reverse-engineered APIs, or vendor assemblies. A separately
installed and appropriately licensed external adapter is solely responsible
for implementing these contracts and for any transaction claim it emits.

Windows-only CI compiles these declarations and exercises generated Python
mocks only. That is not evidence that an external host or plugin was
integrated on the CI machine.

`ProtocolV1.cs` and `Interfaces.cs` are the frozen published v1 surface.
Their source signatures—including four-field `NativeSourceBindingV1`—must not
change. They are legacy-read-only declarations, not authorization for an
active write. `ProtocolV2.cs` and `InterfacesV2.cs` are the active
configuration/session/audit/plan/manifest/result/readback DTO/interface
surface. A conforming
executor must never predict a final revision—or final DWG hash/size/identity—
from pre-write state. `NativeManifestSourceBindingsV2` carries the exact
private prewrite binding plus `NativeFinalOutputConstraintsV2`: authorized
private path/root, same-volume/private-root policy, header/version, maximum
size, and identity-transition mode. The final result must carry the
**observed** retained post-save binding that satisfies those constraints; it
must not reuse a stale input or accept an arbitrary save target.

`NativePortablePrewriteProjectionV2` is the one bridge/Core Console
cross-context preflight contract. It contains only ordered entity/container,
geometry, protected owner/opaque, and policy-independent
table/layout/block digests. Source path/file identity and database/revision
identity are excluded because a private copy may receive new host values.
`NativeBridgeDocumentIdentityV2` retains the bridge-only values solely to
verify the embedded bridge export; marker policy remains separately bound by
the stable-host digest.

Active v2 geometry carriers also use `NativeGeometryContainerV2` for each
physical entity container. `physical_slot_count` is a bounded
erased-inclusive extent, never a count inferred from active records and never
an erased handle/content disclosure. It participates in the v2 geometry,
protected/order, document-binding, audit/plan/manifest precondition, and
post-save readback bindings. It does not alter any frozen v1 DTO or wire
surface.

`NativeBridgeProtocolV2.MaxNativeOperations` is fixed at 1,024. Conforming
writers must calculate the full canonical success or failure result envelope,
including every operation ID/status/digest, below
`MaxConsoleResultCanonicalBytes` (16 KiB below the 256 KiB hard reader cap)
before beginning a transaction.

`Native*ResponseV1` and their result/parameter DTOs are wire-exact for the
read-only JSON protocol: snake-case property names, required nested host,
adapter, plugin, current-document, capability, inventory, and geometry fields
are intentionally separate from higher-level audit/manifest DTOs. In
particular, inventory remains bounded `inventory_json`; it is never modeled as
a geometry export.

Every artifact and wire JSON value has a fixed maximum object/array nesting
depth of **128** (`NativeBridgeProtocolV1.MaxJsonNestingDepth`). Conforming
adapters must configure their JSON readers and writers to reject deeper input
before recursive processing; all shipped schemas remain below this limit.

Active v2 native session timestamps are whole-second RFC 3339 UTC values. A session is
valid only for `created_at <= current UTC < expires_at`, with a positive
lifetime no greater than the fixed five-minute
`NativeBridgeProtocolV2.MaxSessionLifetimeSeconds` bound. Conforming clients
allow no future-clock skew and retain the one preparation-time private
`GetTickCount64` deadline after validation, so wall-clock rollback, delayed
handshake, descriptor storage, or later client construction cannot extend a
connected session. The signed private descriptor carries strict decimal
`monotonic_issued`/`monotonic_expires` ticks plus the
`windows-gettickcount64-ms/v1` clock and Windows boot identifier. It is
same-boot only: a boot/domain mismatch, a current tick before issuance, or a
tick at/after expiry fails closed. These fields are never wire DTOs, reports,
or events.

The frozen wire `Native*ResponseV1` DTOs remain exact for
`native-bridge/v1`; this wire compatibility does not make persisted v1
artifacts executable. Private active `geometry_json` repeats an exact v2
binding:
protocol, session ID, full process identity, host, adapter/plugin, exact
capabilities, and session/document binding digests must all match the issuing
session and saved source, including its DWG header signature. Source equality
alone is not conforming. Active v2 accepts at most
`NativeBridgeProtocolV2.MaxNativeGeometryEntities` entities and
`NativeBridgeProtocolV2.MaxNativeGeometrySegments` aggregate
segments; the unescaped geometry JSON cap is `MaxGeometryJsonBytes` **UTF-8
encoded bytes** (not .NET UTF-16 character count or JSON Schema code points),
within the separate escaped-frame cap `MaxGeometryResponseBytes`. Conforming
adapters must enforce that byte cap before parsing or normalizing every raw or
embedded geometry field. Inventory remains
separate and is capped by `MaxInventoryJsonBytes` and
`MaxInventoryResponseBytes`.

At the exact schema paths `result.geometry_json`, `result.inventory_json`,
manifest `preconditions_geometry_json`, and console-export `geometry_json`,
the serialized JSON carrier is opaque outer data: bind/hash its exact
codepoints and UTF-8 bytes, and do **not** NFC-normalize the whole carrier.
After its byte cap passes, parse the inner JSON independently with the same
depth, duplicate-key, per-string NFC, schema, semantic, and deadline rules.
Historical v1 values remain structurally readable, but missing monotonic,
stable-host, prewrite, and actual-output bindings are never synthesized.
They require a fresh v2 session and audit; only a v1 adapter config can be
explicitly migrated when its unchanged declarative semantics validate as v2.
