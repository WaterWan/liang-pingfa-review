// SPDX-License-Identifier: MIT
// Active v2 DTOs only. The published v1 source surface remains in ProtocolV1.cs.

using System.Text.Json.Serialization;

namespace LiangPingfa.NativeBridge.Contracts;

/// <summary>
/// Namespaces and bounds for active persisted native artifacts. The local
/// read-only bridge wire protocol remains <c>native-bridge/v1</c>.
/// </summary>
public static class NativeBridgeProtocolV2
{
    public const string AdapterConfigSchemaVersion =
        "liang-pingfa/native-adapter-config/v2";
    public const string SessionSchemaVersion =
        "liang-pingfa/native-bridge-session/v2";
    public const string GeometrySchemaVersion =
        "liang-pingfa/native-geometry-export/v2";
    public const string AuditSchemaVersion =
        "liang-pingfa/native-audit/v2";
    public const string IntentSchemaVersion =
        "liang-pingfa/native-edit-intent/v2";
    public const string PlanSchemaVersion =
        "liang-pingfa/native-edit-plan/v2";
    public const string ManifestSchemaVersion =
        "liang-pingfa/native-edit-manifest/v2";
    public const string ConsoleResultSchemaVersion =
        "liang-pingfa/native-console-result/v2";
    public const string ConsoleExportSchemaVersion =
        "liang-pingfa/native-console-export/v2";
    public const string VerificationSchemaVersion =
        "liang-pingfa/native-verification/v2";

    public const int MaxSessionLifetimeSeconds = 5 * 60;
    public const int MaxSessionLifetimeMilliseconds =
        MaxSessionLifetimeSeconds * 1000;
    public const string SessionMonotonicClock =
        "windows-gettickcount64-ms/v1";
    public const int MaxConsoleResultBytes = 256 * 1024;
    public const int ConsoleResultHeadroomBytes = 16 * 1024;
    public const int MaxConsoleResultCanonicalBytes =
        MaxConsoleResultBytes - ConsoleResultHeadroomBytes;
    public const int MaxNativeOperations = 1_024;
    public const int MaxNativeGeometryEntities = 2_000;
    public const int MaxNativeGeometrySegments = 10_000;
    public const int MaxGeometryJsonBytes = 16 * 1024 * 1024;
    public const int MaxInventoryJsonBytes = 64 * 1024;
}

/// <summary>Private same-boot lifetime required by every active session.</summary>
public sealed record NativePrivateSessionLifetimeV2(
    [property: JsonPropertyName("monotonic_clock")] string MonotonicClock,
    [property: JsonPropertyName("monotonic_boot_id")] string MonotonicBootId,
    [property: JsonPropertyName("monotonic_issued")] string MonotonicIssued,
    [property: JsonPropertyName("monotonic_expires")] string MonotonicExpires);

/// <summary>Stable full-host identity carried by active v2 artifacts.</summary>
public sealed record NativeHostIdentityV2(
    string Product,
    string Release,
    string Runtime,
    string Mode);

/// <summary>Stable host/profile binding for an active transaction.</summary>
public sealed record NativeStableHostBindingV2(string Digest);

/// <summary>
/// Full v2 source binding. Unlike the published v1 four-field DTO, v2
/// explicitly carries DWG format and header constraints.
/// </summary>
public sealed record NativeSourceBindingV2(
    [property: JsonPropertyName("format")] string Format,
    [property: JsonPropertyName("sha256")] string Sha256,
    [property: JsonPropertyName("byte_size")] long ByteSize,
    [property: JsonPropertyName("path_fingerprint")] string PathFingerprint,
    [property: JsonPropertyName("file_identity_fingerprint")]
        string FileIdentityFingerprint,
    [property: JsonPropertyName("dwg_header_signature")]
        string DwgHeaderSignature);

/// <summary>Exact active document binding.</summary>
public sealed record NativeDocumentBindingV2(
    NativeSourceBindingV2 Source,
    string DatabaseInstanceFingerprint,
    string RevisionFingerprint);

/// <summary>Exact adapter/plugin/capability identity carried by v2 prewrite state.</summary>
public sealed record NativeAdapterBindingV2(
    string AdapterId,
    string AdapterProfile,
    string AdapterVersion,
    string PluginId,
    string PluginVersion,
    string PluginFingerprint,
    int ProtocolMajor,
    int ProtocolMinor,
    string CapabilitiesDigest);

/// <summary>Active source state selected immediately before writing.</summary>
public sealed record NativePrewriteRevisionV2(
    NativeSourceBindingV2 SourceBinding,
    string DocumentPathFingerprint,
    string DocumentFileIdentityFingerprint,
    string DocumentContentSha256,
    long DocumentByteSize,
    string DatabaseInstanceFingerprint,
    string RevisionFingerprint,
    string GeometryDigest,
    string ProtectedStateDigest,
    string ProtectedOrderDigest,
    string DocumentStateDigest,
    NativeAdapterBindingV2 AdapterBinding,
    NativeStableHostBindingV2 HostBinding,
    string StableHostBindingDigest,
    string AuditedSemanticStateDigest);

/// <summary>Observed post-save binding; it is never predicted by a v2 manifest.</summary>
public sealed record NativeFinalDocumentBindingV2(
    string DatabaseInstanceFingerprint,
    string RevisionFingerprint,
    NativeSourceBindingV2 OutputCopyBinding);

/// <summary>Bounded relationship between a prewrite revision and observed result.</summary>
[JsonConverter(typeof(JsonStringEnumConverter))]
public enum NativeFinalRevisionTransitionV2
{
    save_reopen_changed,
    preserved_by_plugin_capability,
}

/// <summary>Authorization constraints for an output that does not yet exist.</summary>
public sealed record NativeFinalOutputConstraintsV2(
    string AuthorizedPrivatePathFingerprint,
    string AuthorizedPrivateRootFingerprint,
    bool RequireSameVolumeAsPrewrite,
    bool RequireWithinPrivateRoot,
    string RequiredDwgHeaderSignature,
    string RequiredDwgVersion,
    long MaxByteSize,
    string FileIdentityTransitionPolicy);

/// <summary>V2 manifest binding: exact prewrite bytes plus final constraints.</summary>
public sealed record NativeManifestSourceBindingsV2(
    NativeSourceBindingV2 ExpectedPrewriteOutputCopyBinding,
    NativeFinalOutputConstraintsV2 FinalOutputConstraints);

/// <summary>Proof that audit and fresh sessions both use active v2 semantics.</summary>
public sealed record NativeSessionRenewalProofV2(
    string AuditedSessionBinding,
    string FreshSessionBinding,
    string AuditedSessionSchemaVersion,
    string FreshSessionSchemaVersion,
    NativeStableHostBindingV2 HostBinding,
    string ExpiresAtUtc);

/// <summary>One v2 fixed-command execution request.</summary>
public sealed record NativeManifestExecutionRequestV2(
    string ManifestPath,
    string ManifestId,
    string ManifestIntegritySha256,
    string ManifestSchemaVersion,
    string RunId,
    string Nonce);

/// <summary>One v2 allowlisted operation result.</summary>
public sealed record NativeOperationResultV2(
    string OperationId,
    string Status,
    string PostconditionDigest);

/// <summary>Bounded external executor assertion with actual output binding.</summary>
public sealed record NativeManifestExecutionResultV2(
    string RunId,
    string ManifestId,
    string ManifestIntegritySha256,
    string ManifestSchemaVersion,
    string Nonce,
    string FinalRevisionFingerprint,
    NativeFinalRevisionTransitionV2 FinalRevisionTransition,
    NativeFinalDocumentBindingV2 FinalDocumentBinding,
    string TransactionPreflight,
    string TransactionOutcome,
    string RollbackClaim,
    IReadOnlyList<NativeOperationResultV2> Operations);

/// <summary>Separate v2 readback envelope bound to the exact write result.</summary>
public sealed record NativeConsoleExportV2(
    string RunId,
    string ManifestId,
    string ManifestIntegritySha256,
    string ManifestSchemaVersion,
    string ConsoleResultIntegritySha256,
    string ConsoleResultSchemaVersion,
    string Nonce,
    string FinalRevisionFingerprint,
    NativeFinalDocumentBindingV2 FinalDocumentBinding,
    string CanonicalGeometryJson,
    string CanonicalGeometrySha256);

/// <summary>Readback request emitted only after a v2 result is accepted.</summary>
public sealed record NativeReadbackRequestV2(
    string ManifestPath,
    string ManifestId,
    string ManifestIntegritySha256,
    string ManifestSchemaVersion,
    string ConsoleResultIntegritySha256,
    string ConsoleResultSchemaVersion,
    string RunId,
    string Nonce);
