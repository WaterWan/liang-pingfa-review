# ADR 0001: isolate the executable transaction proof from a host adapter

## Status

Accepted for checkpoint 1.

## Context

The public repository needs SDK-free executable evidence for active geometry
and mutable-write v2 semantics without bundling proprietary CAD assemblies or asserting
that a third-party host has executed a transaction.

## Decision

The core receives only typed `CoreManifestV2` values created after a separate
schema-validation boundary. That boundary passes a required validated copy of
the full private manifest's top-level `integrity.sha256`; the core keeps it
separate from its own reduced typed-projection hash and emits the full value
as `manifest_integrity_sha256` in console results. It still rechecks every
semantic precondition against an immutable generated snapshot, performs all
mutations in one vendor-neutral transaction, verifies exact staged state,
commits once, disposes that transaction, and only then invokes
`ICadDatabase.SaveAndReopen()` before it exports or emits a success result.
The result and its console envelope have no public construction path: an
internal executor token is minted only after commit, disposal, save/reopen,
and final exact verification. The returned database is a fresh readback
boundary and must pass the same exact allowed-delta, order, protected-state,
binding, owner-state, and final-revision checks. Owner records are complete
ordered protected state even where unused. Save/reopen failures after commit emit no
success result and must not be described as a rollback of the committed
private copy.

`native-cad` implements the v2 geometry subset plus v2 actual-output binding
needed for this proof: direct
Modelspace DBTEXT, LINE, simple LWPOLYLINE, opaque records, containers,
ordering, table/style markers, document digests, and the three fixed
operations.  It does not parse arbitrary user manifests, read paths, open
drawings, or create CAD tables. `CoreManifestV2` binds the exact private
prewrite source and constrained output destination/header/size/identity mode;
the final source hash/size/identity is accepted only when observed after
save/reopen and validated against those constraints.

The `Autodesk.*` declarations are new project-owned syntax stubs. Every
public nonabstract reference type has an explicit constructor that throws
`NotSupportedException`, and every executable member throws. Struct default
construction remains the normal CLR value-type boundary, while their explicit
constructors and executable members throw. The project rejects explicit
packing and publishing, does not include build output in packages or publish
payloads, and compile-only consumers disable copy-local. Its build outputs
are ephemeral syntax artifacts, never deployment assets. It only lets the
future adapter be designed against a narrow compile-time surface.

## Consequences

The solution is deterministic and testable with .NET 8 plus the standard
library.  Passing tests prove the model and protocol subset, not AutoCAD,
RealDWG, TSSD, ODA, object enabler, or transaction-runtime compatibility.
The next checkpoint must add a separately licensed real-host adapter and
demonstrate it with an explicitly authorized external host. That adapter may
implement the vendor-neutral save/reopen operation as private `SaveAs`
followed by a fresh `Database.ReadDwgFile`, or by a separate readback process.
The only prospective dependency-policy exception is a reviewed conditional
proprietary `<Reference>` ItemGroup in the future adapter project itself;
there is no `HintPath`, package, SDK, or shared props/targets exception.
Every native-CAD project nevertheless must end with the unconditional
`NativeCad.RepositoryPolicy.targets` import; the future adapter uses its
explicit reviewed policy mode rather than suppressing that import.

The transaction contract additionally requires the future host adapter to
capture its exact snapshot after `BeginTransaction()` while both its
`DocumentLock` and host transaction are active. It must conditionally apply
each mutation against that fresh state and conditionally commit the final
verified prefix. A snapshot captured before transaction start is never an
acceptable substitute; the SDK-free proof exercises this stale-state
revalidation through generated fault hooks.
