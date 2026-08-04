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

The declarations distinguish an exact `NativePrewriteRevisionV1` from the
new `NativeFinalDocumentBindingV1` returned after save/readback. A conforming
executor must never predict a final revision from pre-write state.

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

Private `geometry_json` repeats an exact `NativeGeometryExportBindingV1`:
protocol, session ID, full process identity, host, adapter/plugin, exact
capabilities, and session/document binding digests must all match the issuing
session and saved source, including its DWG header signature. Source equality
alone is not conforming.  v1 accepts at most
`MaxNativeGeometryEntities` entities and `MaxNativeGeometrySegments` aggregate
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
This preserves v1 hashes for previously valid canonical inner JSON; no
artifact version migration is required.
