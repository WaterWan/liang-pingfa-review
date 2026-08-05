# Native CAD checkpoint 1

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

The real AutoCAD adapter, command registration, document locking, and
Autodesk-object mapping are deliberately deferred to the next checkpoint.
Nothing in this solution claims that a real host transaction has run.

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

When that adapter is added, its `ICadTransaction.CaptureSnapshot()` mapping
must export the live database only while the relevant `DocumentLock` and host
transaction are active. It must perform the core's exact conditional
replace/erase/append checks inside that same boundary, not reuse an export
captured before `BeginTransaction()`. The core independently rechecks the
complete staged prefix before every operation and before commit, so a host
callback or other drift aborts without a save/reopen or success result.

Build and run only the generated core proof:

```powershell
dotnet build native-cad\LiangPingfa.NativeCad.sln -c Release --nologo
dotnet run --project native-cad\tests\LiangPingfa.NativeCad.Core.Tests -c Release --no-build
```

The shared fixture is source-free and deliberately uses synthetic handles,
text, geometry, and hashes only.
