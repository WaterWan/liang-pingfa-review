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
The public `file_identity_fingerprint` is deliberately narrower and frozen
across Python and C#: canonical SHA-256 over
`creation_time_100ns`, NTFS volume serial (`first`), `windows-file-id`
namespace, and the unsigned high/low-combined file index (`second`). Size and
last-write time are excluded from that identity digest but remain mandatory
independent stable-capture checks; source SHA-256, byte size, header, and path
fingerprint remain their separate source-binding fields.

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

# Licensed operator package. Every path is explicit; the receipt stays under
# an existing current-user/SYSTEM-only private NTFS root.
native-cad\scripts\build-autocad-adapter.ps1 `
  -Profile autocad2025 `
  -CadSdkDir <absolute-licensed-sdk-directory> `
  -PackageDirectory <new-operator-package-directory> `
  -PrivateRoot <existing-private-ntfs-root> `
  -ReceiptPath <existing-private-ntfs-root>\adapter-build-receipt.json
```

Profiles are explicit: `autocad2024` uses `net48`; `autocad2025` uses
`net8.0-windows`; and `autocad2026` uses `net10.0-windows`. Autodesk's current
[managed .NET compatibility table](https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-Customization/files/GUID-A6C680F2-DE2E-418A-A182-E4884073338A.htm)
specifies .NET 10 for AutoCAD 2026 Update 1.2 and later. The explicit 2026
profile therefore must not be compiled as .NET 8.
TSSD is not implemented, built, advertised, or qualified by this concrete
AutoCAD adapter. A future TSSD integration must use a distinct adapter ID and
version with TSSD-specific product identity, plugin/vendor evidence, and any
required object enablers; it must not label an AutoCAD host as TSSD.

`UseAutodeskApiStubs=true` builds against the project-owned, reflection-marked
syntax-only declarations. The stub DLL is never copied to adapter output or a
deployment payload. A stub build proves compilation and command metadata
only; it is not runtime qualification. Real mode rejects missing/relative/
reparse SDK paths, copied SDK files, `pack`, and `publish`. It never searches
PATH, the registry, Program Files, or downloads a vendor SDK.

The optional `LiangPingfa.NativeCad.AutoCAD.RealHost.Tests` project is a
single `net8.0-windows`, SDK-free qualification launcher. It prints `SKIP`
unless an operator explicitly supplies a Core Console and generated private
fixture. It accepts the profile-specific adapter package only as an external
path passed to the PowerShell qualification script; it never links, reflects
over, or loads that adapter assembly. This lets the same runner build for the
2024, 2025, and .NET 10-based 2026 profiles without cross-TFM references. It
is intentionally not public-CI evidence and does not access user drawings.

When launching this runner, set the opt-in gates `LPF_REALHOST_TESTS=1` and
`LIANG_PINGFA_RUN_REAL_HOST=1`; all bindings are explicit environment variables:
`LPF_REALHOST_PHASE`, `LPF_REALHOST_PROFILE`, `LPF_REALHOST_PYTHON`,
`LPF_REALHOST_HOST`, `LPF_REALHOST_CORE_CONSOLE`,
`LPF_REALHOST_ADAPTER_PACKAGE`, `LIANG_PINGFA_REAL_HOST_RECEIPT`,
`LPF_REALHOST_NATIVE_CONFIG`, `LPF_REALHOST_BOOTSTRAP`,
`LPF_REALHOST_SESSION`, `LPF_REALHOST_SOURCE`, `LPF_REALHOST_WORK_ROOT`,
`LPF_REALHOST_EVIDENCE_OUTPUT`, `LPF_REALHOST_REPOSITORY_ROOT`, and
`LPF_REALHOST_POWERSHELL`. `LIANG_PINGFA_REAL_HOST_RECEIPT` is required to
be the existing, normal absolute local path of the current-user/SYSTEM-only
private build receipt generated by `build-autocad-adapter.ps1`; the runner
checks that path and its owner/DACL before forwarding it exactly once as
`-ReceiptPath`. Receipt schema, integrity, package, and runtime binding
validation remain authoritative in `qualify-real-host.ps1`. The runner never
prints the receipt path, content, or hashes.

`build-autocad-adapter.ps1` and `qualify-real-host.ps1` support **Windows
PowerShell 5.1+** and PowerShell 7+. Their repository-owned compatibility
helper avoids PowerShell 7-only JSON/hash APIs. Before qualification accepts a
receipt, Python performs strict duplicate-key parsing, schema/receipt
validation, and complete package verification; PowerShell JSON conversion is
never a receipt trust boundary.

For example, retain the private receipt only in the current shell:

```powershell
$env:LPF_REALHOST_TESTS = "1"
$env:LIANG_PINGFA_RUN_REAL_HOST = "1"
$env:LIANG_PINGFA_REAL_HOST_RECEIPT = <private-root>\adapter-build-receipt.json
dotnet run --project native-cad\tests\LiangPingfa.NativeCad.AutoCAD.RealHost.Tests `
  -c Release --nologo
```

## Private operator flow: bootstrap, audit, translate, fresh readback

The actual source supports automatic `DBText` translation in its narrow,
field-free direct-Modelspace profile. It does not automate AutoCAD UI,
mouse/focus, NETLOAD, or selection. A licensed operator must perform these
steps on a clean, saved document and a private working copy:

1. Install the supported licensed AutoCAD host and matching managed SDK,
   then use `build-autocad-adapter.ps1` above. The package contains only
   repository-authored adapter/core/protocol DLLs, profile-required adapter
   `.deps.json`, PDBs, docs, and a context template—never `Ac*.dll`, object
   enablers, SDK files, or syntax stubs. Its private v2 receipt records the
   exact allowlist and hashes every package file. Its **critical runtime
   package** is profile-specific:

   | Profile | Target framework | Critical runtime files |
   | --- | --- | --- |
   | `autocad2024` | `net48` | `LiangPingfa.NativeCad.AutoCAD.Adapter.dll`, `LiangPingfa.NativeCad.Core.dll`, `LiangPingfa.NativeCad.Protocol.dll` |
   | `autocad2025` | `net8.0-windows` | the three DLLs plus `LiangPingfa.NativeCad.AutoCAD.Adapter.deps.json` |
   | `autocad2026` | `net10.0-windows` | the three DLLs plus `LiangPingfa.NativeCad.AutoCAD.Adapter.deps.json` |

   The package fingerprint is SHA-256 over the package-format version,
   profile, target framework, and ordinal-sorted NFC file-name/byte-size/
   SHA-256 records. PDBs, docs, and the template remain receipt-allowlisted
   but are not critical runtime inputs. A missing, renamed, case-colliding,
   substituted, or extra file fails receipt/config/qualification validation.
   The receipt and all SDK hashes are private and ignored by Git.
2. Create a current-user/SYSTEM-only private NTFS root, native config, and
   bootstrap context. The config's optional `bootstrap` object carries the
   exact base64url nonce and whole-second expiry. Its required
   `runtime_package` object copies the receipt's format/profile/framework/
   fingerprint/components and adds only the private package directory; both
   plugin entries carry the same `runtime_package_fingerprint` while their
   `sha256` remains the adapter DLL hash. Set the same nonce, expiry,
   canonical config SHA-256, runtime-package fingerprint, private root, and
   new output path through the
   exact `LIANG_PINGFA_NATIVE_BOOTSTRAP_NONCE`,
   `LIANG_PINGFA_NATIVE_BOOTSTRAP_EXPIRES_AT`,
   `LIANG_PINGFA_NATIVE_BOOTSTRAP_CONFIG_SHA256`, and
   `LIANG_PINGFA_NATIVE_BOOTSTRAP_OUTPUT` variables (plus
   `LIANG_PINGFA_NATIVE_PRIVATE_ROOT` and
   `LIANG_PINGFA_NATIVE_BOOTSTRAP_RUNTIME_PACKAGE_SHA256`). The SHA-256 is
   over Python's canonical validated config object, not arbitrary
   pretty-printed bytes. For example, with the private config path held only
   in the current shell:

   ```powershell
   $env:LPF_BOOTSTRAP_CONFIG = <private-config.json>
   $configHash = python -c "import os; from pathlib import Path; from liang_pingfa_review.canonical import canonical_sha256; from liang_pingfa_review.native_contracts import load_native_config; print(canonical_sha256(load_native_config(Path(os.environ['LPF_BOOTSTRAP_CONFIG']))))"
   $env:LIANG_PINGFA_NATIVE_BOOTSTRAP_CONFIG_SHA256 = $configHash
   $env:LIANG_PINGFA_NATIVE_BOOTSTRAP_RUNTIME_PACKAGE_SHA256 = <receipt-runtime-package-fingerprint>
   ```
3. Manually NETLOAD the operator package from an AutoCAD trusted location.
   In the explicitly selected, clean saved document, manually run
   `LPF_NATIVE_BRIDGE_BOOTSTRAP`. It creates exactly one bounded private
   `native-bridge-bootstrap/v1` advertisement with current-user/SYSTEM DACL,
   no replacement, no session ID, and a process/plugin/config/nonce binding.
4. Let Python atomically consume that one-use file; never put the pipe,
   nonce, or bootstrap path in a log:

   ```powershell
   python -m liang_pingfa_review native-session prepare `
     --bootstrap <private-bootstrap.json> `
     --native-config <private-config.json> `
     --session-out <new-private-audit-session.json>
   python -m liang_pingfa_review native-audit `
     --input <authorized-private-source.dwg> `
     --session <new-private-audit-session.json> `
     --audit-out <new-private-audit.json> `
     --report-out <new-private-audit.md> `
     --native-config <private-config.json>
   ```

5. Review or create a private translation intent, run `native-plan`, then
   manually create a **fresh** bootstrap in the unchanged full host. A session
   descriptor and bootstrap are one-use by design; Python prepares a fresh
   apply session before `native-apply`. The apply command saves only a new
   output copy and runs the fixed Core Console write plus independent fresh
   readback verification. Run `native-verify` on that output.

For a generated, authorized private fixture only, the opt-in
`qualify-real-host.ps1` runs the two explicit phases (`audit`, then `apply`)
with `LIANG_PINGFA_RUN_REAL_HOST=1`. It never launches or controls a GUI. It
requires the private `-ReceiptPath` produced by the build, hashes every
receipt-listed runtime component before bootstrap and immediately before
apply, and repeats receipt/package/config checks after audit and after
readback/verification. It rejects a missing/tampered receipt, package-root
switch, unlisted file, missing metadata, or same-size Core/Protocol/deps
substitution before a success/evidence record can be published. It separately
records source hash/identity/mtime before and after, requires changed output
bytes, and requires the no-reparse full-host executable's SHA-256 to equal
the integrity-checked audit/session fingerprint and the private native
configuration's full-host binding. Core Console remains independently bound
to its configured executable fingerprint, while both write/readback adapter
results must advertise the same runtime-package fingerprint. Private state
retains matching digests, while the redacted summary exposes neither host
path nor digest. Passing its dry-run or any public stub build is not runtime
qualification.
TSSD has no profile in this build or qualification path; qualifying it
requires the distinct, TSSD-specific adapter and evidence described above.

The audit phase explicitly receives the same private receipt that built the
package; the apply phase repeats it with a fresh bootstrap:

```powershell
native-cad\scripts\qualify-real-host.ps1 `
  -Phase audit -Profile autocad2025 `
  -PythonExecutable <python.exe> `
  -HostExecutable <licensed-full-host.exe> `
  -CoreConsoleExecutable <licensed-core-console.exe> `
  -AdapterPackage <operator-package-directory> `
  -ReceiptPath <private-root>\adapter-build-receipt.json `
  -NativeConfig <private-root>\native-config.json `
  -Bootstrap <private-root>\bootstrap.json `
  -SessionPath <private-root>\audit-session.json `
  -SourceDrawing <private-root>\liang-pingfa-qualification-fixture.dwg `
  -WorkRoot <private-root> -EvidenceOutput <private-evidence-root>
```

`-Phase apply` uses the same immutable package/config/receipt values but a
new bootstrap and session path. Do not replace a package directory, receipt,
or any component between phases.
