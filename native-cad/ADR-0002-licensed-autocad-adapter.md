# ADR 0002: make the licensed AutoCAD adapter source opt-in and fail closed

## Status

Accepted for checkpoint 2. This is source availability, not runtime
qualification.

## Context

The SDK-free v2 transaction core proves exact manifest semantics but cannot
edit a DWG. Operators who hold a licensed Autodesk managed SDK need source
that calls the actual document, database, transaction, `SaveAs`, and fresh
`ReadDwgFile` APIs without putting a vendor binary, SDK lookup, or host claim
in the public repository.

## Decision

`LiangPingfa.NativeCad.AutoCAD.Adapter` is a separate opt-in project. It
requires all of:

- `BuildAutoCadAdapter=true`;
- one explicit `CadHostProfile`;
- either `UseAutodeskApiStubs=true`, or an absolute, existing, non-reparse
  `CadSdkDir` containing exactly named `AcMgd.dll`, `AcDbMgd.dll`, and
  `AcCoreMgd.dll`.

The public syntax lane references only the original
`LiangPingfa.NativeCad.AutoCAD.ApiStubs` project with `Private=false` and
`CopyLocal=false`. Its assembly metadata says `syntax-only`; it is never an
adapter deployment dependency. Real mode references only the three explicit
operator SDK files with `Private=false` and `CopyLocal=false`. The project
rejects missing SDK data, reparse points, package/publish, copied SDK files,
and default builds. It never searches PATH, registry, or installed-product
directories, and it never downloads an Autodesk or TSSD component.

The supported compilation profiles are:

| Profile | Target framework | Status |
| --- | --- | --- |
| `autocad2024` | `net48` | source/stub only |
| `autocad2025` | `net8.0-windows` | source/stub only |
| `autocad2026` | `net10.0-windows` | source/stub only |

Autodesk's current [AutoCAD 2026 managed compatibility
table](https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-Customization/files/GUID-A6C680F2-DE2E-418A-A182-E4884073338A.htm)
specifies .NET 10 for Update 1.2 and later (while 2026 through Update 1.1
used .NET 8). The explicit 2026 source profile tracks the current supported
API and must not silently retain `net8.0-windows`. TSSD is not implemented
or qualified by this adapter. The vendor-neutral protocol can support a
future external TSSD adapter only under a distinct adapter ID/version with
TSSD-specific product identity, plugin/vendor evidence, and object-enabler
requirements; it cannot route TSSD through this AutoCAD implementation.

The plugin registers only `LPF_NATIVE_BRIDGE_BOOTSTRAP`,
`LPF_NATIVE_EXECUTE_MANIFEST`, and `LPF_NATIVE_EXPORT_MANIFEST`. All command
context comes from fixed private environment variables. The bridge is a
single local named pipe with first-instance, remote-client rejection, and
current-user/SYSTEM DACL controls. Its worker only frames/authenticates RPC;
every Autodesk access runs through the command-context dispatcher and a
document lock where required. Bootstrap creates the pipe and publishes its
private advertisement without waiting for a command-context callback or a
database export. The advertisement contains no session ID: Python creates the
only session ID, and the server atomically adopts the first valid `health`
request's ID for that pipe lifetime. Static `health` requires no drawing
access; `get_session` captures the first document snapshot only after the
bootstrap command has returned.

The persisted `native-bridge-bootstrap/v1` advertisement is an exact,
bounded, canonical, one-use private file below an explicit private NTFS root.
It is no-replace, current-user/SYSTEM DACL-checked, and binds schema/protocol,
PID/pipe/process identity, read-only mode, adapter/plugin/host/runtime,
canonical capabilities, expiry, nonce, and canonical private-config SHA-256.
Python atomically renames and claims it before parsing or pipe use; a replay,
wrong config, nonce mismatch, expiry, reparse/DACL failure, host/plugin/
capability mismatch, PID reuse, or process image drift fails before a pipe
connection. The advertisement never carries a session ID.

The adapter maps deterministic handles, owners, physical container order,
direct Modelspace `DBText`, `LINE`, and simple zero-bulge `LWPOLYLINE`.
The [Autodesk managed reference for `BlockTableRecord.IncludingErased`](https://help.autodesk.com/cloudhelp/2026/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_BlockTableRecord_IncludingErased.html)
documents a public, read-only `BlockTableRecord` result. The syntax stub
therefore declares that exact shape and the adapter enumerates the returned
record; it does not invent an `IEnumerable`-typed convenience property.
Every active v2 geometry export also carries a required, bounded
`containers` record for each entity container with its
`physical_slot_count`. The count comes from erased-inclusive iteration,
includes empty-active containers, and exposes no erased object identity or
payload. It is bound into geometry, protected-state, container-order,
document-binding, manifest precondition, and readback comparisons.
The vendor-neutral core's deletes preserve the count and their gap,
translations preserve it, and each marker append reserves the original direct-
Modelspace count plus its deterministic ordinal before incrementing it exactly
once. The actual AutoCAD adapter intentionally does not erase: real-host
review found SaveAs/reopen compacts erased slots, which violates the active v2
gap-preserving contract.
The exact text profile admits only field-free BaseLeft `DBText`: Autodesk
`TextHorizontalMode.TextLeft`, `TextVerticalMode.TextBase`, and
`AttachmentPoint.BaseLeft`. Its sole anchor is `Position`; the adapter never
reads or writes `AlignmentPoint`. Fit, Aligned, Center, and all other
justifications fail closed. Logical text bounds use that insertion point
instead of glyph-dependent extents.
The initial field policy is deliberately narrow: a `DBText` with
`HasFields=true` rejects exact export, preflight, and translation.
The v2 carrier does not losslessly represent an AutoCAD field expression and
its dependencies, so an evaluated `TextString` is never treated as sufficient
editable or protected state. Ordinary field-free `DBText` remains supported.
Unknown/proxy/custom entities and missing object enablers fail closed unless
a reviewed stable opaque provider exists. The core preflights and applies all
operations in one Autodesk transaction, aborts on failure, commits once,
saves only the current private copy, creates a fresh database with
`ReadDwgFile`, and emits a success artifact only after exact fresh readback.
The fresh private-DWG readback passes `allowCPConversion=true`.  Autodesk's
[`ReadDwgFile` reference](https://help.autodesk.com/cloudhelp/2024/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_Database_ReadDwgFile_string_FileOpenMode__MarshalAsUnmanagedType_U1__bool_string.html)
documents that `false` can display a code-page/NLS fallback dialog, while
`true` opens with default conversion silently.  Core Console is unattended,
so the dialog-capable option is prohibited.  The opened database is strictly
readback-only and is never saved.  A silent conversion is not accepted as
semantic success: exact before/manifest/after comparison rejects any DBText
text, layer/style, binary geometry, opaque record, owner/container, or
protected table/layout/block/document-state drift before a result can be
published. No portable code-page property is assumed: no reliable common
managed API property is documented across every supported profile, so exact
text and state projection is the fail-closed gate.
Marker preflight reserves only deterministic container, sequence, text, and
geometry. It does not predict a handle from an exported maximum or
`Handseed`; the `ObjectId` returned by `AppendEntity` and the marker's
post-append handle are authoritative. That actual handle is bound to the
operation result and independently checked against post-save readback.
The initial actual AutoCAD capability set is `translate_dbtext/v1` plus
`create_review_marker/v1`; marker remains default-disabled and requires its
exact profile/capability gate. `delete_auxiliary_overlay_text/v1` is neither
advertised nor accepted: manifest parsing returns the stable
`LPF_UNSUPPORTED_OPERATION` before database construction or
`BeginTransaction()`. Delete can only be reconsidered after a new versioned
sequence-compaction policy and real-host evidence exist.

The full-host read-only bridge advertises or serves a document only if it is
named, exists on disk, has `DWGTITLED=1` and `DBMOD=0`, and its document and
database paths match. Before opening each export transaction and again after
it closes, the bridge captures disk bytes through a read handle and checks the
document/database path plus documented database `FingerprintGuid` and
`VersionGuid` drift indicators. It does not rely on a nonexistent
`Document.Saved` member or on a profile-dependent active-transaction counter.
Any dirty transition, SaveAs/path drift, database replacement, saved revision
change, or byte drift invalidates the pipe session as `DOCUMENT_CHANGED`; the
bridge never saves or prompts the interactive document.

The bridge's full geometry export retains its local database/revision identity
for session invalidation, but Core Console preflight compares the separately
defined `PortablePrewriteProjectionV2`: ordered entities, container order,
geometry, owners/opaque state, and policy-independent table/layout/block
digests. Source path/file identity, session/process identity, and
GUID-derived database/revision fields are deliberately excluded because a
private copy may receive new host values. The manifest explicitly records the
bridge-only identity as evidence and checks it only against its embedded
bridge export. Marker policy is not folded into portable geometry: the exact
policy is separately bound by the manifest stable-host digest, while the
portable table digest rejects marker-resource drift.

Before Autodesk database work, the adapter captures the private DWG binding
through a retained file lease. It first attempts a no-write read lease, but
does not reject a live AutoCAD document merely because its host requires
`FILE_SHARE_WRITE`. Under that compatible-sharing fallback, capture is
fail-closed: it samples file ID, creation time, size, and last-write time
before, between, and after two complete seeked hash passes, requiring all
metadata and both hashes to match. Those local trusted-session proof values
are folded into opaque binding identity and never logged as raw timestamps or
hashes. Transaction snapshots reuse that immutable binding rather than
hashing the DWG once per operation. It rehashes only at security boundaries:
immediately before the write transaction, immediately before `SaveAs`, after
save/reopen for the final binding, and before readback/result publication.
The artifact-safe `file_identity_fingerprint` is a different, frozen
cross-language projection: canonical SHA-256 of only
`creation_time_100ns`, volume serial `first`, `windows-file-id` namespace,
and unsigned combined file index `second`. Byte size and last-write time are
not identity components; they remain separate stability/content proof checks,
as do source SHA-256, header, and path fingerprint.

The deployable adapter is a complete repository-authored runtime package, not
an adapter-DLL-only identity. Its v1 package record hashes the format version,
explicit AutoCAD profile, profile TFM, and ordinal-sorted NFC
`name/byte-size/SHA-256` records. Every profile contains Adapter/Core/Protocol;
2025/2026 additionally require the adapter `.deps.json`. PDBs, README, and
the bootstrap template may accompany the package but remain noncritical
receipt allowlist entries. The private v2 build receipt hashes every allowed
file, contains all critical component records and the package fingerprint, and
never includes Autodesk, TSSD, object-enabler, or syntax-stub binaries.

The active v2 config repeats the package descriptor (with a private package
directory) and each plugin carries the same runtime-package fingerprint while
retaining its direct Adapter DLL hash. Python leases every critical component
and its ancestor DACL through bridge/Core Console completion, rechecks the
aggregate package fingerprint, and rejects unexpected/case-colliding package
entries. The adapter recomputes the same fingerprint from its own directory
before bootstrap, every bridge operation, Core Console manifest processing,
and result/export publication. Bridge v1's structural DTO is frozen: its
existing plugin fingerprint carries the runtime-package fingerprint; no v1
field is added. Active v2 audit/plan/manifest/result/export/verification
artifacts bind that value explicitly where their schemas permit it.

The qualification runner receives a mandatory private receipt path. It checks
receipt integrity, the complete directory allowlist, every component
hash/size, config/package agreement, and distinct host/Core Console
executables before bootstrap, after audit, immediately before apply, and after
readback/verification. A receipt/package mismatch cannot produce an evidence
success record.

## Consequences

Public CI compiles source using the original syntax-only stubs, exercises
reflection/static checks, and verifies missing-SDK/pack/publish/copy failure
paths. It does **not** load AutoCAD, TSSD, RealDWG, an object enabler, or a
user drawing. Passing public checks does not establish runtime compatibility,
license compliance, save behavior, or any TSSD qualification.

`LiangPingfa.NativeCad.AutoCAD.RealHost.Tests` is intentionally optional. It
skips unless an operator explicitly provides a licensed SDK, Core Console,
and generated private fixture. The initial real-host qualification matrix
requires translation evidence only; it does not require or claim delete.
Such private evidence is required before any runtime qualification statement.

The operator-facing `build-autocad-adapter.ps1` accepts no discovered SDK:
`-Profile`, `-CadSdkDir`, package destination, private root, and receipt path
are explicit. It validates fixed local NTFS/no-reparse inputs, references only
the three named Autodesk DLLs with `Private=false`, packages only
repository-authored output, and records private fingerprints. It does not
pack/publish or copy a vendor/stub binary. `qualify-real-host.ps1` similarly
requires an explicit host, Core Console, package, config, bootstrap, session,
source, work root, and evidence root. Its separate audit/apply phases force a
fresh manual bootstrap/session before mutation and never automate GUI input or
NETLOAD.

Both operator scripts support Windows PowerShell 5.1+ and PowerShell 7+.
Their shared helper uses .NET Framework `BitConverter` for lowercase hash
encoding and ordered ordinal JSON conversion rather than PowerShell 7-only
APIs. PowerShell parsing is not a receipt trust boundary: qualification first
uses Python's strict duplicate-key parser, receipt/schema validation, and full
package verification.
