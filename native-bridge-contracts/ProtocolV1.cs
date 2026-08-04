// SPDX-License-Identifier: MIT
// Contract DTOs only. This project contains no host adapter or plugin code.

using System.Text.Json.Serialization;

namespace LiangPingfa.NativeBridge.Contracts;

/// <summary>Immutable protocol constants shared by external conforming adapters.</summary>
public static class NativeBridgeProtocolV1
{
    public const string Version = "liang-pingfa/native-bridge/v1";
    /// <summary>
    /// Version of the mandatory challenge-response transcript derivation.
    /// Hash UTF-8 ASCII fields in this exact order, each prefixed by its
    /// unsigned 32-bit big-endian byte length: protocol version, this
    /// derivation version, session ID, client nonce, challenge, bridge nonce.
    /// The result is lowercase SHA-256 hex; implementations compare it in
    /// constant time. No plain string concatenation is conforming.
    /// </summary>
    public const string ChallengeResponseDerivationVersion =
        "liang-pingfa/native-bridge/challenge-response/v1";
    public const int Major = 1;
    public const int Minor = 0;
    public const int MaxControlResponseBytes = 256 * 1024;
    /// <summary>Outer framed limit for the fixed two-digest inventory result.</summary>
    public const int MaxInventoryResponseBytes = 256 * 1024;
    /// <summary>Outer framed limit for a geometry response carrying escaped JSON.</summary>
    public const int MaxGeometryResponseBytes = 32 * 1024 * 1024;
    /// <summary>
    /// Maximum unescaped canonical geometry JSON inside a response or
    /// manifest, measured in UTF-8 encoded bytes (not UTF-16 characters or
    /// JSON Schema code points).
    /// </summary>
    public const int MaxGeometryJsonBytes = 16 * 1024 * 1024;
    /// <summary>Maximum canonical JSON in the fixed two-digest inventory result.</summary>
    public const int MaxInventoryJsonBytes = 64 * 1024;
    /// <summary>v1 semantic capacity proven below the fixed 60-second RPC deadline.</summary>
    public const int MaxNativeGeometryEntities = 2_000;
    /// <summary>v1 aggregate segment capacity across all exported entities.</summary>
    public const int MaxNativeGeometrySegments = 10_000;
    /// <summary>
    /// Fixed maximum JSON object/array nesting accepted by every conforming
    /// artifact and wire implementation. Contract schemas stay below it.
    /// </summary>
    public const int MaxJsonNestingDepth = 128;
}

/// <summary>Internal client context, distinct from a wire session response.</summary>
public sealed record NativeBridgeSessionContextV1(
    string SessionId,
    int ProcessId,
    uint WindowsSessionId,
    string ProcessInstanceFingerprint,
    string ClientNonce,
    string BridgeNonce,
    NativeHostIdentityV1 Host,
    string DocumentRevisionFingerprint,
    IReadOnlyList<string> Capabilities);

/// <summary>Stable full-host identity; no PID, pipe, nonce, or session ID appears here.</summary>
public sealed record NativeHostIdentityV1(
    string Product,
    string Release,
    string Runtime,
    string Mode);

/// <summary>Redacted compatibility digest that may remain equal across renewed sessions.</summary>
public sealed record NativeStableHostBindingV1(string Digest);

/// <summary>Private manifest proof linking an audited session to one fresh compatible session.</summary>
public sealed record NativeSessionRenewalProofV1(
    string AuditedSessionBinding,
    string FreshSessionBinding,
    NativeStableHostBindingV1 HostBinding,
    string ExpiresAtUtc);

/// <summary>Redacted saved-file identity exposed by the read-only bridge.</summary>
public sealed record NativeSourceBindingV1(
    string Sha256,
    long ByteSize,
    string PathFingerprint,
    string FileIdentityFingerprint);

/// <summary>Redacted current document identity exposed by the read-only bridge.</summary>
public sealed record NativeDocumentBindingV1(
    NativeSourceBindingV1 Source,
    string DatabaseInstanceFingerprint,
    string RevisionFingerprint);

/// <summary>Stable adapter/plugin/capability identity carried by a pre-write binding.</summary>
public sealed record NativeAdapterBindingV1(
    string AdapterId,
    string AdapterProfile,
    string AdapterVersion,
    string PluginId,
    string PluginVersion,
    string PluginFingerprint,
    int ProtocolMajor,
    int ProtocolMinor,
    string CapabilitiesDigest);

/// <summary>
/// Exact source state selected immediately before writing. This never predicts
/// a post-save revision; that is supplied only by <see cref="NativeFinalDocumentBindingV1"/>.
/// </summary>
public sealed record NativePrewriteRevisionV1(
    NativeSourceBindingV1 SourceBinding,
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
    NativeAdapterBindingV1 AdapterBinding,
    NativeStableHostBindingV1 HostBinding,
    string AuditedSemanticStateDigest);

/// <summary>New post-save binding produced by the external fixed command.</summary>
public sealed record NativeFinalDocumentBindingV1(
    string DatabaseInstanceFingerprint,
    string RevisionFingerprint,
    NativeSourceBindingV1 OutputCopyBinding);

/// <summary>Proven relationship between pre-write and final revision tokens.</summary>
[JsonConverter(typeof(JsonStringEnumConverter))]
public enum NativeFinalRevisionTransitionV1
{
    save_reopen_changed,
    preserved_by_plugin_capability,
}

/// <summary>Internal capability declaration, distinct from a wire health response.</summary>
public sealed record NativeBridgeCapabilityHealthV1(
    int ProtocolMajor,
    int ProtocolMinor,
    string AdapterId,
    string AdapterProfile,
    string AdapterVersion,
    string PluginId,
    string PluginVersion,
    string PluginFingerprint,
    NativeHostIdentityV1 Host,
    IReadOnlyList<string> Capabilities);

/// <summary>Private canonical geometry used only by the fixed post-save exporter.</summary>
public sealed record NativePrivateGeometryExportV1(
    NativeDocumentBindingV1 Document,
    string CanonicalGeometryJson,
    string CanonicalGeometrySha256);

/// <summary>One internally generated manifest path and run nonce for a fixed executor command.</summary>
public sealed record NativeManifestExecutionRequestV1(
    string ManifestPath,
    string ManifestId,
    string ManifestIntegritySha256,
    string RunId,
    string Nonce);

/// <summary>External executor's bounded conformance assertion, not proof of internals.</summary>
public sealed record NativeManifestExecutionResultV1(
    string RunId,
    string ManifestId,
    string ManifestIntegritySha256,
    string Nonce,
    string FinalRevisionFingerprint,
    NativeFinalRevisionTransitionV1 FinalRevisionTransition,
    NativeFinalDocumentBindingV1 FinalDocumentBinding,
    string TransactionPreflight,
    string TransactionOutcome,
    string RollbackClaim,
    IReadOnlyList<NativeOperationResultV1> Operations);

/// <summary>
/// Separate post-save envelope that binds the embedded geometry revision.
/// <paramref name="CanonicalGeometryJson"/> is limited by
/// <see cref="NativeBridgeProtocolV1.MaxGeometryJsonBytes"/> UTF-8 bytes.
/// </summary>
public sealed record NativeConsoleExportV1(
    string RunId,
    string ManifestId,
    string Nonce,
    string FinalRevisionFingerprint,
    NativeFinalDocumentBindingV1 FinalDocumentBinding,
    string CanonicalGeometryJson,
    string CanonicalGeometrySha256);

/// <summary>One allowlisted operation result.</summary>
public sealed record NativeOperationResultV1(
    string OperationId,
    string Status,
    string PostconditionDigest);

/// <summary>Readback request emitted only after the separate post-save process starts.</summary>
public sealed record NativeReadbackRequestV1(
    string ManifestPath,
    string ManifestId,
    string RunId,
    string Nonce);

// The types below are wire DTOs for native-bridge-response-v1.schema.json.
// They intentionally do not reuse the higher-level audit/manifest records
// above: JSON names, nesting, and required fields must remain identical to
// the length-prefixed local bridge protocol.

[JsonConverter(typeof(JsonStringEnumConverter))]
public enum NativeWireResultKindV1
{
    health,
    session,
    document,
    inventory,
    geometry,
}

[JsonConverter(typeof(JsonStringEnumConverter))]
public enum NativeWireHostModeV1
{
    full_host,
}

[JsonConverter(typeof(JsonStringEnumConverter))]
public enum NativeWireErrorCodeV1
{
    DOCUMENT_CHANGED,
    SESSION_EXPIRED,
    SESSION_INVALID,
    UNSUPPORTED_METHOD,
    EXPORT_REJECTED,
    INTERNAL_ERROR,
}

/// <summary>Exact wire adapter object: <c>id</c>, <c>profile</c>, and <c>version</c>.</summary>
public sealed record NativeWireAdapterV1(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("profile")] string Profile,
    [property: JsonPropertyName("version")] string Version);

/// <summary>Exact wire plugin identity emitted by read-only bridge responses.</summary>
public sealed record NativeWirePluginV1(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("version")] string Version,
    [property: JsonPropertyName("fingerprint")] string Fingerprint);

/// <summary>Exact wire host identity emitted by read-only bridge responses.</summary>
public sealed record NativeWireHostV1(
    [property: JsonPropertyName("product")] string Product,
    [property: JsonPropertyName("release")] string Release,
    [property: JsonPropertyName("runtime")] string Runtime,
    [property: JsonPropertyName("mode")] NativeWireHostModeV1 Mode);

/// <summary>Exact wire current-document object; all fields are required and non-null.</summary>
public sealed record NativeCurrentDocumentV1(
    [property: JsonPropertyName("saved")] bool Saved,
    [property: JsonPropertyName("path_fingerprint")] string PathFingerprint,
    [property: JsonPropertyName("file_identity_fingerprint")] string FileIdentityFingerprint,
    [property: JsonPropertyName("sha256")] string Sha256,
    [property: JsonPropertyName("byte_size")] long ByteSize,
    [property: JsonPropertyName("dwg_header_signature")] string DwgHeaderSignature,
    [property: JsonPropertyName("database_instance_fingerprint")] string DatabaseInstanceFingerprint,
    [property: JsonPropertyName("revision_fingerprint")] string RevisionFingerprint);

/// <summary>
/// Exact ephemeral process identity repeated inside private geometry JSON.
/// It prevents a same-PID replacement or another Windows logon session from
/// being accepted as the issuing export session.
/// </summary>
public sealed record NativeGeometryProcessBindingV1(
    [property: JsonPropertyName("pid")] int ProcessId,
    [property: JsonPropertyName("windows_session_id")] uint WindowsSessionId,
    [property: JsonPropertyName("instance_fingerprint")] string InstanceFingerprint,
    [property: JsonPropertyName("creation_time_100ns")] string CreationTime100Ns,
    [property: JsonPropertyName("executable_fingerprint")] string ExecutableFingerprint);

/// <summary>
/// Private binding carried inside canonical <c>geometry_json</c>.  A conforming
/// adapter copies every field from the exact issuing session/current document;
/// it never substitutes a source-only equality check.
/// </summary>
public sealed record NativeGeometryExportBindingV1(
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("protocol_major")] int ProtocolMajor,
    [property: JsonPropertyName("protocol_minor")] int ProtocolMinor,
    [property: JsonPropertyName("host")] NativeWireHostV1 Host,
    [property: JsonPropertyName("process")] NativeGeometryProcessBindingV1 Process,
    [property: JsonPropertyName("adapter")] NativeWireAdapterV1 Adapter,
    [property: JsonPropertyName("plugin")] NativeWirePluginV1 Plugin,
    [property: JsonPropertyName("capabilities")] IReadOnlyList<string> Capabilities,
    [property: JsonPropertyName("session_binding_digest")] string SessionBindingDigest,
    [property: JsonPropertyName("stable_host_binding_digest")] string StableHostBindingDigest,
    [property: JsonPropertyName("document_binding_digest")] string DocumentBindingDigest);

/// <summary>Exact successful health result object.</summary>
public sealed record NativeBridgeHealthResultV1(
    [property: JsonPropertyName("kind")] NativeWireResultKindV1 Kind,
    [property: JsonPropertyName("protocol_major")] int ProtocolMajor,
    [property: JsonPropertyName("protocol_minor")] int ProtocolMinor,
    [property: JsonPropertyName("adapter")] NativeWireAdapterV1 Adapter,
    [property: JsonPropertyName("plugin")] NativeWirePluginV1 Plugin,
    [property: JsonPropertyName("host")] NativeWireHostV1 Host,
    [property: JsonPropertyName("capabilities")] IReadOnlyList<string> Capabilities);

/// <summary>
/// Exact get-session result. The response is lowercase SHA-256 over the
/// versioned, length-prefixed transcript specified by
/// <see cref="NativeBridgeProtocolV1.ChallengeResponseDerivationVersion"/>.
/// The request-only session ID, client nonce, and challenge are represented by
/// <see cref="NativeSessionHandshakeParametersV1"/>.
/// </summary>
public sealed record NativeSessionHandshakeResultV1(
    [property: JsonPropertyName("kind")] NativeWireResultKindV1 Kind,
    [property: JsonPropertyName("bridge_nonce")] string BridgeNonce,
    [property: JsonPropertyName("challenge_response")] string ChallengeResponse,
    [property: JsonPropertyName("adapter")] NativeWireAdapterV1 Adapter,
    [property: JsonPropertyName("plugin")] NativeWirePluginV1 Plugin,
    [property: JsonPropertyName("host")] NativeWireHostV1 Host,
    [property: JsonPropertyName("capabilities")] IReadOnlyList<string> Capabilities,
    [property: JsonPropertyName("current_document")] NativeCurrentDocumentV1 CurrentDocument);

/// <summary>Exact get-current-document result.</summary>
public sealed record NativeCurrentDocumentResultV1(
    [property: JsonPropertyName("kind")] NativeWireResultKindV1 Kind,
    [property: JsonPropertyName("current_document")] NativeCurrentDocumentV1 CurrentDocument);

/// <summary>
/// Exact inventory result.  The bridge intentionally returns the bounded
/// canonical inventory as <c>inventory_json</c>, not a geometry export.
/// </summary>
public sealed record NativeInventoryExportV1(
    [property: JsonPropertyName("kind")] NativeWireResultKindV1 Kind,
    [property: JsonPropertyName("inventory_json")] string InventoryJson);

/// <summary>
/// Exact bounded geometry result, whose private canonical JSON must not
/// exceed <see cref="NativeBridgeProtocolV1.MaxGeometryJsonBytes"/> UTF-8
/// bytes before an adapter parses or normalizes it.
/// </summary>
public sealed record NativeExactGeometryExportV1(
    [property: JsonPropertyName("kind")] NativeWireResultKindV1 Kind,
    [property: JsonPropertyName("geometry_json")] string GeometryJson);

/// <summary>Exact successful response envelope used by the health RPC.</summary>
public sealed record NativeHealthResponseV1(
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("result")] NativeBridgeHealthResultV1 Result);

/// <summary>Exact successful response envelope used by the get-session RPC.</summary>
public sealed record NativeSessionHandshakeResponseV1(
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("result")] NativeSessionHandshakeResultV1 Result);

/// <summary>Exact successful response envelope used by get-current-document.</summary>
public sealed record NativeCurrentDocumentResponseV1(
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("result")] NativeCurrentDocumentResultV1 Result);

/// <summary>Exact successful response envelope used by export-inventory.</summary>
public sealed record NativeInventoryResponseV1(
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("result")] NativeInventoryExportV1 Result);

/// <summary>Exact successful response envelope used by export-exact-geometry.</summary>
public sealed record NativeExactGeometryResponseV1(
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("result")] NativeExactGeometryExportV1 Result);

/// <summary>Exact failure payload and envelope for the bridge response schema.</summary>
public sealed record NativeBridgeErrorV1(
    [property: JsonPropertyName("code")] NativeWireErrorCodeV1 Code);

public sealed record NativeBridgeFailureResponseV1(
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("error")] NativeBridgeErrorV1 Error);

/// <summary>Exact <c>health</c>/<c>get_current_document</c> request parameters.</summary>
public sealed record NativeSessionOnlyParametersV1(
    [property: JsonPropertyName("session_id")] string SessionId);

/// <summary>Exact <c>get_session</c> request parameters.</summary>
public sealed record NativeSessionHandshakeParametersV1(
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("client_nonce")] string ClientNonce,
    [property: JsonPropertyName("challenge")] string Challenge);

/// <summary>Exact document-bound export request parameters.</summary>
public sealed record NativeDocumentBoundParametersV1(
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("expected_document_revision")] string ExpectedDocumentRevision);
