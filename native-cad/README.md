# Native CAD checkpoints 1–2

This directory is a source-only, SDK-free C# proof of the fixed native CAD
transaction protocol.  It has no vendor DLL, NuGet dependency, drawing input,
or host command implementation.

It proves three narrow things:

1. `LiangPingfa.NativeCad.Protocol` canonically encodes the active v2 subset used by
   the core: strict UTF-8 JSON, NFC strings, Unicode-scalar sorted keys,
   duplicate-key rejection, SHA-256, and fixed limits. The supported C#
   canonical JSON values are `null`, Booleans, NFC strings, signed `Int64`
   integers, nonnegative `UInt64` integers, arrays, and objects. Decimal,
   floating-point, exponent, and negative-zero number spellings are rejected;
   accepted integers use ordinary invariant decimal bytes only (`0` or an
   optional `-` followed by a nonzero digit), with no exponent, leading plus,
   or leading zero. Binary64 geometry values remain lowercase bit strings,
   never JSON numbers.
2. `LiangPingfa.NativeCad.Core` executes typed, already-validated fixed
   manifests against a generated in-memory database, verifies staged state,
   commits once, disposes the transaction, then calls its explicit
   `ICadDatabase.SaveAndReopen()` boundary and verifies the **freshly
   reopened** state before constructing any result or console export. A
   committed result/console export has no public constructor or factory: the
   executor alone mints its internal verified-readback token after that full
   sequence. Owners are complete ordered protected state, including unused
   owner records, so an add/remove/reorder/change fails exact readback. Its
   `manifest_integrity_sha256` result field is always the full validated
   Python manifest `integrity.sha256`, never the distinct internal typed-core
   projection hash.
3. `LiangPingfa.NativeCad.AutoCAD.ApiStubs` is original syntax-only source for
   a future adapter compilation boundary.  It is not vendor code, is not
   deployable, and does not prove runtime compatibility.

The stub project's `bin` output exists only as an ephemeral syntax artifact.
It is nonpackable and nonpublishable, rejects every explicit `dotnet pack`
and `dotnet publish`, is excluded from package/publish build output, and is
referenced with copy-local disabled by the compile-only boundary. It must
never be treated as a deployment asset.

Every public, nonabstract **reference-type** stub declares a public
constructor that immediately throws `NotSupportedException`; every executable
member/property/indexer also throws. Value-type declarations intentionally
retain normal CLR default construction (`default(ObjectId)`, for example), but
their explicit constructors and executable members throw. This is a syntax
boundary, not a runtime substitute.

`SaveAndReopen()` is vendor-neutral on purpose. A future separately licensed
AutoCAD adapter can implement it by saving only the private output copy with
`SaveAs`, then returning a fresh `Database.ReadDwgFile` database (or a
separate readback-process export). If saving, reopening, or readback
verification fails after commit, the core emits no success result and does
not claim that its already committed disposable private state was rolled
back.

The MSBuild policy is also intentionally closed. Every current project ends
with one exact unconditional import of
`..\..\NativeCad.RepositoryPolicy.targets`; therefore its target-framework,
warning, dependency, vendor, stub-mode, and adapter-mode guards remain active
when `ImportDirectoryBuildProps` or `ImportDirectoryBuildTargets` is disabled.
No child SDK source, `FrameworkReference`, package, assembly reference,
`HintPath`, import, task, or target-framework override is accepted. A future
adapter review may use only the policy's explicit
`reviewed-autocad-adapter` mode and narrowly documented conditional
proprietary `<Reference>` names; it does not permit an import bypass,
`HintPath`, package, SDK, or broad props/targets exemption.

`LiangPingfa.NativeCad.AutoCAD.Adapter` now contains source for the next
boundary: exact command registration, document locking, Autodesk transaction
mapping, private-copy `SaveAs`/fresh `ReadDwgFile` readback, and the
read-only named-pipe bridge. It is **not** part of the default SDK-free
solution build and nothing in this repository claims that a real host
transaction has run.

Bootstrap starts the one-instance pipe without synchronously waiting for an
AutoCAD command-context callback. Its private advertisement deliberately has
no session ID; the prepared Python client owns ID generation and the bridge
binds the first valid `health` request to that ID. Marker manifests likewise
contain no predicted entity handle: `AppendEntity`'s actual `ObjectId` handle
is recorded in the v2 operation result and matched during readback. The
initial exact-export profile rejects `DBText.HasFields` rather than treating
an evaluated `TextString` as lossless field state. Private DWG hashes are retained and reused for staged snapshots, with full
revalidation only at transaction/save/reopen/publication boundaries. Binding
capture prefers a retained no-write read lease, but a live AutoCAD document
may require write sharing. In that compatible-sharing case it fails closed
unless file ID, creation time, size, last-write time, and two independent
complete-file hashes agree before, between, and after the passes. These
trusted-local-session proof inputs are folded into opaque binding fingerprints
and are never emitted as timestamps or raw hashes in public logs.

The typed `CoreManifestV2` binds the exact prewrite/private-input source and
a v2 `FinalOutputConstraintsV2` authorization, never predicted final bytes.
The staged transaction must retain the former; `SaveAndReopen(finalOutputConstraints)`
returns a fresh database whose **actual** source is checked for authorized
private path/root, header/version, maximum size, identity-transition policy,
and changed content. A stale prewrite input, wrong final path/header/size,
forbidden replacement, unchanged mutating output, or result/readback source
mismatch is rejected. It also carries a stable execution-host digest over protocol, host product/release/runtime/mode and
executable, adapter profile/version, plugin identity, capability set, and the
full marker policy. Renewed sessions/processes and reopened database/revision
tokens may differ, but any stable binding drift prevents a result token.

Bridge and Core Console preflight additionally share one
`PortablePrewriteProjectionV2`. It compares ordered entity/container state,
geometry, protected owners/opaque state, and policy-independent
table/layout/block state after source-to-private-copy retargeting. It excludes
source path/file identity and host database/revision GUID-derived values:
those are local session evidence, not copy-portable semantic state. The
manifest carries its bridge-only identity explicitly and verifies it against
the embedded bridge export; marker policy is separately bound through the
stable execution-host digest. Neither bridge nor Core Console snapshot accepts
a marker-policy input; Core preflight separately validates the policy against
the private copy's full layer/style map.

Active `native-geometry-export/v2` additionally requires one private
`containers` record per physical entity container. Its bounded
`physical_slot_count` is an erased-inclusive slot extent, not an active-entity
maximum: erased-only containers and trailing/internal gaps remain observable
without exposing erased handles or payload. The count is included in geometry,
protected-state, container-order, and document-binding digests. Core
transactions retain it independently from active records: the vendor-neutral
core deletion model and translation leave it unchanged; each successful marker
append reserves the original direct-Modelspace count plus its ordinal and
increments exactly once. The actual AutoCAD adapter does not implement
deletion: AutoCAD SaveAs/reopen compacts erased slots, so deleting would
violate this v2 gap-preserving contract.
Readback rejects any container set, owner, count, active index, or resulting
gap drift.

The external `native-bridge-contracts` project separately preserves published
v1 DTO/interface signatures for legacy reads. This core accepts and emits v2
workflow artifacts only; a v1 session/audit/plan/manifest must be rejected by
the Python execution boundary with `NATIVE_LEGACY_ARTIFACT_READ_ONLY`, then
replaced by a fresh v2 session and audit. The frozen `native-bridge/v1` wire
namespace remains only for its unchanged read-only request/response shape.

`native-console-result/v2` has a fixed 256 KiB reader limit. The shared
`MaxNativeOperations` limit is 1,024, and the core constructs the complete
canonical success envelope before `BeginTransaction()` with a 16 KiB safety
margin. This count supports the validated 623-operation scenario; a larger
manifest is rejected before mutation rather than producing an unreadable
console result.

`native-console-export/v2` uses the explicit
`NativeCadCanonicalJsonProfiles.ConsoleExport` profile for both its integrity
hash and its serialized bytes. Only its root `geometry_json` carrier may
exceed the normal 64 KiB string bound, never more than 16 MiB raw UTF-8; the
complete outer export is rejected before write above its independent 32 MiB
file cap. Result artifacts retain the ordinary strict profile.

The AutoCAD adapter's `ICadTransaction.CaptureSnapshot()` mapping exports the
live database only while the relevant `DocumentLock` and host transaction are
active. It performs only the exact conditional replace/append checks inside
that same boundary, not reuse an export captured before `BeginTransaction()`.
Its manifest parser rejects delete before database construction or
`BeginTransaction()`. The core independently rechecks the
complete staged prefix before every operation and before commit, so a host
callback or other drift aborts without a save/reopen or success result.

Build and run only the generated core proof:

```powershell
dotnet build native-cad\LiangPingfa.NativeCad.sln -c Release --nologo
dotnet run --project native-cad\tests\LiangPingfa.NativeCad.Core.Tests -c Release --no-build
```

The shared fixture is source-free and deliberately uses synthetic handles,
text, geometry, and hashes only.

## Licensed AutoCAD adapter source

The adapter source is available at
`src/LiangPingfa.NativeCad.AutoCAD.Adapter`. It contains exactly these
host commands and no prompt/argument command surface:

- `LPF_NATIVE_BRIDGE_BOOTSTRAP` — a full-host/session command that starts a
  one-user, read-only local pipe only from a private bootstrap environment.
- `LPF_NATIVE_EXECUTE_MANIFEST` — a Core Console modal command that consumes
  the fixed private manifest/result/run/root environment and edits only the
  current private DWG copy.
- `LPF_NATIVE_EXPORT_MANIFEST` — a separate Core Console modal command that
  validates the private write receipt and exports a fresh readback.

The code has no `Editor.Command`, `SendStringToExecute`, LISP, COM, UI,
selection, keyboard, mouse, focus, prompt, public-path save, or arbitrary
command escape hatch. Its actual initial write profile advertises
`translate_dbtext/v1` and `create_review_marker/v1` only. Translation is
limited to direct Modelspace field-free BaseLeft `DBText`; marker is
capability-gated and disabled in the default profile. It never advertises or
calls `delete_auxiliary_overlay_text/v1`: AutoCAD SaveAs/reopen compacts erased
slots, so the current v2 gap-preserving delete contract cannot be honored.
Every adapter, bootstrap, health, session, geometry, and Core Console
capability DTO uses this immutable NFC/ordinal wire sequence without
reordering: `create_review_marker/v1`, `read.exact_geometry/v1`,
`read.inventory/v1`, `translate_dbtext/v1`. Python compares that list exactly;
duplicates, drift, and unsorted inputs are rejected.

After the one private `SaveAs`, the adapter opens a new, private DWG only for
readback with `Database.ReadDwgFile(..., allowCPConversion: true, ...)`.
Autodesk's [`ReadDwgFile` documentation](https://help.autodesk.com/cloudhelp/2024/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_Database_ReadDwgFile_string_FileOpenMode__MarshalAsUnmanagedType_U1__bool_string.html)
states that the false setting may show a code-page/NLS dialog, whereas true
performs fallback conversion silently. Core Console therefore never takes the
dialog-capable path. The new database is never saved. Instead, exact
before/manifest/after verification treats any silently converted DBText text,
layer/style, geometry, opaque/protected record, or document-state difference
as a readback mismatch and publishes no result. There is no portable,
documented code-page property common to all supported host profiles; exact
text/state projection is intentionally the fail-closed conversion gate.
Deletion remains useful in the vendor-neutral core and the separate ODA narrow
profile, but no future AutoCAD delete claim is permitted without a new
versioned sequence-compaction policy and real-host evidence. Unsupported,
proxy, custom, or missing-enabler objects fail closed unless a
unsupported, proxy, custom, or missing-enabler objects fail closed unless a
reviewed deterministic opaque provider exists.
For erased-inclusive physical order, the adapter follows Autodesk's documented
read-only [`BlockTableRecord.IncludingErased`](https://help.autodesk.com/cloudhelp/2026/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_BlockTableRecord_IncludingErased.html)
`BlockTableRecord` view and enumerates that returned record. The syntax-only
stub intentionally has the same public property type rather than an invented
generic enumerable.

The project is deliberately opt-in:

```powershell
# Syntax-only source checks; no Autodesk binary is loaded or copied.
dotnet build native-cad\src\LiangPingfa.NativeCad.AutoCAD.Adapter\LiangPingfa.NativeCad.AutoCAD.Adapter.csproj `
  -c Release --nologo `
  -p:BuildAutoCadAdapter=true `
  -p:UseAutodeskApiStubs=true `
  -p:CadHostProfile=autocad2025

# Licensed operator build. CadSdkDir is an explicit local, non-reparse SDK
# directory containing AcMgd.dll, AcDbMgd.dll, and AcCoreMgd.dll.
dotnet build native-cad\src\LiangPingfa.NativeCad.AutoCAD.Adapter\LiangPingfa.NativeCad.AutoCAD.Adapter.csproj `
  -c Release --nologo `
  -p:BuildAutoCadAdapter=true `
  -p:UseAutodeskApiStubs=false `
  -p:CadHostProfile=autocad2025 `
  -p:CadSdkDir=<absolute-licensed-sdk-directory>
```

Profiles are explicit: `autocad2024`/`tssd2024` use `net48`;
`autocad2025`/`tssd2025` and `autocad2026`/`tssd2026` use
`net8.0-windows`. Autodesk's AutoCAD 2026 managed API documentation specifies
.NET 8 compatibility; this project does not guess or target .NET 10.
`tssd*` aliases are source build profiles only and remain unqualified until
private licensed-host evidence is supplied.

`UseAutodeskApiStubs=true` builds against the project-owned, reflection-marked
syntax-only declarations. The stub DLL is never copied to adapter output or a
deployment payload. A stub build proves compilation and command metadata
only; it is not runtime qualification. Real mode rejects missing/relative/
reparse SDK paths, copied SDK files, `pack`, and `publish`. It never searches
PATH, the registry, Program Files, or downloads a vendor SDK.

The optional `LiangPingfa.NativeCad.AutoCAD.RealHost.Tests` project prints
`SKIP` unless an operator explicitly supplies licensed SDK/Core Console and a
generated private fixture. It is intentionally not public-CI evidence and
does not access user drawings.
