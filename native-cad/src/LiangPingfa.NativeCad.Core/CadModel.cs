// SPDX-License-Identifier: MIT
// Vendor-neutral immutable generated CAD model. No drawing file or vendor API is used.

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.Core
{
    /// <summary>Stable, non-host-specific failures emitted by the transaction core.</summary>
    public enum CadCoreErrorCode
    {
        /// <summary>The typed manifest is internally inconsistent.</summary>
        ManifestInvalid,

        /// <summary>The database no longer equals the manifest precondition.</summary>
        StalePrecondition,

        /// <summary>An operation identifier is repeated.</summary>
        DuplicateOperation,

        /// <summary>A mutable target is repeated.</summary>
        DuplicateTarget,

        /// <summary>An entity kind/container/field gate failed.</summary>
        InvalidTarget,

        /// <summary>A capability or fixed marker policy gate failed.</summary>
        CapabilityDenied,

        /// <summary>A one-shot in-memory transaction could not begin.</summary>
        TransactionConflict,

        /// <summary>An injected or staged transaction step failed.</summary>
        TransactionFailure,

        /// <summary>The single commit failed without publishing staged state.</summary>
        CommitFailed,

        /// <summary>The committed private copy could not be saved.</summary>
        SaveFailed,

        /// <summary>The saved private copy could not be reopened fresh.</summary>
        ReopenFailed,

        /// <summary>Before/manifest/after exact transition verification failed.</summary>
        ReadbackMismatch,

        /// <summary>A deliberate test-only fault was reached.</summary>
        FaultInjected,
    }

    /// <summary>Exception with a redacted core failure code.</summary>
    public sealed class CadCoreException : InvalidOperationException
    {
        /// <summary>Creates a coded core exception.</summary>
        public CadCoreException(CadCoreErrorCode code, string message)
            : base(message)
        {
            Code = code;
        }

        /// <summary>Stable classification used by tests and future adapters.</summary>
        public CadCoreErrorCode Code { get; private set; }
    }

    /// <summary>Immutable active v2 source binding with generated values only.</summary>
    public sealed class NativeSourceBindingV2
    {
        /// <summary>Creates a source binding without retaining a path.</summary>
        public NativeSourceBindingV2(
            string sha256,
            long byteSize,
            string pathFingerprint,
            string fileIdentityFingerprint,
            string dwgHeaderSignature)
        {
            NativeGeometryJsonV2.ValidateSourceBindingFields(
                sha256,
                byteSize,
                pathFingerprint,
                fileIdentityFingerprint,
                dwgHeaderSignature);

            Sha256 = sha256;
            ByteSize = byteSize;
            PathFingerprint = pathFingerprint;
            FileIdentityFingerprint = fileIdentityFingerprint;
            DwgHeaderSignature = dwgHeaderSignature;
        }

        /// <summary>Generated source content fingerprint.</summary>
        public string Sha256 { get; private set; }

        /// <summary>Generated source size.</summary>
        public long ByteSize { get; private set; }

        /// <summary>Generated lexical path fingerprint, never a path.</summary>
        public string PathFingerprint { get; private set; }

        /// <summary>Generated file identity fingerprint.</summary>
        public string FileIdentityFingerprint { get; private set; }

        /// <summary>DWG header signature token.</summary>
        public string DwgHeaderSignature { get; private set; }

        /// <summary>Returns v2 snake-case source fields.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "format", "DWG" },
                { "sha256", Sha256 },
                { "byte_size", ByteSize },
                { "path_fingerprint", PathFingerprint },
                { "file_identity_fingerprint", FileIdentityFingerprint },
                { "dwg_header_signature", DwgHeaderSignature },
            };
        }

        /// <summary>Returns true only when every source-binding field is exact.</summary>
        public bool ExactlyMatches(NativeSourceBindingV2? other)
        {
            return other != null &&
                string.Equals(Sha256, other.Sha256, StringComparison.Ordinal) &&
                ByteSize == other.ByteSize &&
                string.Equals(
                    PathFingerprint,
                    other.PathFingerprint,
                    StringComparison.Ordinal) &&
                string.Equals(
                    FileIdentityFingerprint,
                    other.FileIdentityFingerprint,
                    StringComparison.Ordinal) &&
                string.Equals(
                    DwgHeaderSignature,
                    other.DwgHeaderSignature,
                    StringComparison.Ordinal);
        }

        /// <summary>Creates only source-free deterministic metadata for generated tests.</summary>
        public static NativeSourceBindingV2 CreateGenerated()
        {
            return new NativeSourceBindingV2(
                CanonicalJson.Sha256Hex(new Dictionary<string, object?> { { "generated", "source" } }),
                0,
                CanonicalJson.Sha256Hex(new Dictionary<string, object?> { { "generated", "path" } }),
                CanonicalJson.Sha256Hex(new Dictionary<string, object?> { { "generated", "identity" } }),
                "AC1032");
        }
    }

    /// <summary>
    /// Generated host/binding context retained only to emit a full v2-shaped
    /// export. The stable projection deliberately omits session/process
    /// instance values while retaining host executable, adapter/plugin,
    /// protocol, capability, and marker-policy identity.
    /// </summary>
    public sealed class NativeGeometryBindingContextV2
    {
        /// <summary>Creates a v2 binding context from opaque generated identifiers.</summary>
        public NativeGeometryBindingContextV2(
            string sessionId,
            string adapterId,
            string adapterProfile,
            string adapterVersion,
            string pluginId,
            string pluginVersion,
            string pluginFingerprint,
            IReadOnlyList<string> capabilities,
            string? hostProduct = null,
            string? hostRelease = null,
            string? hostRuntime = null,
            string? hostMode = null,
            long processId = 1L,
            long windowsSessionId = 0L,
            string? processInstanceFingerprint = null,
            string? processCreationTime100Ns = null,
            string? executableFingerprint = null)
        {
            NativeGeometryJsonV2.ValidateBindingContextFields(
                sessionId,
                adapterId,
                adapterProfile,
                adapterVersion,
                pluginId,
                pluginVersion,
                pluginFingerprint,
                capabilities);

            List<string> copied = new List<string>();
            for (int index = 0; index < capabilities.Count; index++)
            {
                copied.Add(capabilities[index]);
            }

            copied.Sort(StringComparer.Ordinal);
            string resolvedHostProduct = hostProduct ?? "generated-host";
            string resolvedHostRelease = hostRelease ?? "1.0";
            string resolvedHostRuntime = hostRuntime ?? "generated-runtime";
            string resolvedHostMode = hostMode ?? "full_host";
            string resolvedInstanceFingerprint = processInstanceFingerprint ??
                Digest("process-instance");
            string resolvedCreationTime = processCreationTime100Ns ?? "1";
            string resolvedExecutableFingerprint = executableFingerprint ??
                Digest("host-executable");
            NativeGeometryJsonV2.ValidateHostProcessBindingContextFields(
                resolvedHostProduct,
                resolvedHostRelease,
                resolvedHostRuntime,
                resolvedHostMode,
                processId,
                windowsSessionId,
                resolvedInstanceFingerprint,
                resolvedCreationTime,
                resolvedExecutableFingerprint);
            SessionId = sessionId;
            AdapterId = adapterId;
            AdapterProfile = adapterProfile;
            AdapterVersion = adapterVersion;
            PluginId = pluginId;
            PluginVersion = pluginVersion;
            PluginFingerprint = pluginFingerprint;
            Capabilities = new ReadOnlyCollection<string>(copied);
            HostProduct = resolvedHostProduct;
            HostRelease = resolvedHostRelease;
            HostRuntime = resolvedHostRuntime;
            HostMode = resolvedHostMode;
            ProcessId = processId;
            WindowsSessionId = windowsSessionId;
            ProcessInstanceFingerprint = resolvedInstanceFingerprint;
            ProcessCreationTime100Ns = resolvedCreationTime;
            ExecutableFingerprint = resolvedExecutableFingerprint;
        }

        /// <summary>Generated session identity.</summary>
        public string SessionId { get; private set; }

        /// <summary>Generated adapter identifier.</summary>
        public string AdapterId { get; private set; }

        /// <summary>Generated adapter profile.</summary>
        public string AdapterProfile { get; private set; }

        /// <summary>Generated adapter version.</summary>
        public string AdapterVersion { get; private set; }

        /// <summary>Generated plugin identifier.</summary>
        public string PluginId { get; private set; }

        /// <summary>Generated plugin version.</summary>
        public string PluginVersion { get; private set; }

        /// <summary>Generated plugin fingerprint.</summary>
        public string PluginFingerprint { get; private set; }

        /// <summary>Exact sorted generated capabilities.</summary>
        public IReadOnlyList<string> Capabilities { get; private set; }

        /// <summary>Stable host product token.</summary>
        public string HostProduct { get; private set; }

        /// <summary>Stable host release token.</summary>
        public string HostRelease { get; private set; }

        /// <summary>Stable host runtime token.</summary>
        public string HostRuntime { get; private set; }

        /// <summary>Stable host operating mode.</summary>
        public string HostMode { get; private set; }

        /// <summary>Ephemeral process ID excluded from stable binding.</summary>
        public long ProcessId { get; private set; }

        /// <summary>Ephemeral Windows logon-session ID excluded from stable binding.</summary>
        public long WindowsSessionId { get; private set; }

        /// <summary>Ephemeral process-instance token excluded from stable binding.</summary>
        public string ProcessInstanceFingerprint { get; private set; }

        /// <summary>Ephemeral process creation time excluded from stable binding.</summary>
        public string ProcessCreationTime100Ns { get; private set; }

        /// <summary>Host executable identity retained in stable binding.</summary>
        public string ExecutableFingerprint { get; private set; }

        /// <summary>Returns the protocol/host/adapter/plugin/capability digest.</summary>
        public string StableHostBindingDigest
        {
            get
            {
                return CanonicalJson.Sha256Hex(StableHostWireValue());
            }
        }

        /// <summary>
        /// Returns the execution-stable digest shared with Python's
        /// native_execution_stable_host_binding_digest projection. It adds
        /// every output-affecting marker-policy field while still excluding
        /// session, PID, process instance, database, and revision values.
        /// </summary>
        public string StableExecutionHostBindingDigest(MarkerPolicyBindingV2 markerPolicy)
        {
            if (markerPolicy == null)
            {
                throw new ArgumentNullException(nameof(markerPolicy));
            }

            Dictionary<string, object?> stable = StableHostWireValue();
            stable.Add("marker_policy_binding", markerPolicy.ToWireValue());
            return CanonicalJson.Sha256Hex(stable);
        }

        /// <summary>Returns a full v2 binding object whose document digest is fresh.</summary>
        public Dictionary<string, object?> ToWireValue(
            NativeSourceBindingV2 source,
            Dictionary<string, object?> document)
        {
            if (source == null)
            {
                throw new ArgumentNullException(nameof(source));
            }

            if (document == null)
            {
                throw new ArgumentNullException(nameof(document));
            }

            Dictionary<string, object?> host = HostWireValue();
            Dictionary<string, object?> process = ProcessWireValue();
            Dictionary<string, object?> adapter = AdapterWireValue();
            Dictionary<string, object?> plugin = PluginWireValue();
            List<object?> capabilities = CapabilitiesWireValue();
            string stableHostDigest = StableHostBindingDigest;
            string sessionDigest = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "adapter", adapter },
                    { "capabilities", capabilities },
                    { "host", host },
                    { "plugin", plugin },
                    { "process", process },
                    { "session_id", SessionId },
                });
            string documentDigest = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "source", source.ToWireValue() },
                    { "document", document },
                });

            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "session_id", SessionId },
                { "session_schema_version", NativeCadProtocolV2.SessionSchemaVersion },
                { "protocol_version", NativeCadProtocolV2.BridgeVersion },
                { "protocol_major", 1L },
                { "protocol_minor", 0L },
                { "host", host },
                { "process", process },
                { "adapter", adapter },
                { "plugin", plugin },
                { "capabilities", capabilities },
                { "session_binding_digest", sessionDigest },
                { "stable_host_binding_digest", stableHostDigest },
                { "document_binding_digest", documentDigest },
            };
        }

        /// <summary>Creates source-free deterministic v2 binding metadata.</summary>
        public static NativeGeometryBindingContextV2 CreateGenerated()
        {
            return new NativeGeometryBindingContextV2(
                "native-session-0123456789abcdef0123456789abcdef",
                "generated-adapter",
                "generated-profile",
                "1.0.0",
                "generated-plugin",
                "1.0.0",
                Digest("plugin"),
                new[] { "read.exact_geometry/v1", "read.inventory/v1" });
        }

        /// <summary>Returns an identical stable context with a renewed session/process.</summary>
        public NativeGeometryBindingContextV2 WithRenewedSession(
            string sessionId,
            long processId,
            long windowsSessionId,
            string processInstanceFingerprint,
            string processCreationTime100Ns)
        {
            return Copy(
                sessionId,
                AdapterId,
                AdapterProfile,
                AdapterVersion,
                PluginId,
                PluginVersion,
                PluginFingerprint,
                Capabilities,
                HostProduct,
                HostRelease,
                HostRuntime,
                HostMode,
                processId,
                windowsSessionId,
                processInstanceFingerprint,
                processCreationTime100Ns,
                ExecutableFingerprint);
        }

        /// <summary>Returns a context with one stable host field changed for tests/adapters.</summary>
        public NativeGeometryBindingContextV2 WithHost(
            string product,
            string release,
            string runtime,
            string mode,
            string executableFingerprint)
        {
            return Copy(
                SessionId,
                AdapterId,
                AdapterProfile,
                AdapterVersion,
                PluginId,
                PluginVersion,
                PluginFingerprint,
                Capabilities,
                product,
                release,
                runtime,
                mode,
                ProcessId,
                WindowsSessionId,
                ProcessInstanceFingerprint,
                ProcessCreationTime100Ns,
                executableFingerprint);
        }

        /// <summary>Returns a context with a changed adapter tuple.</summary>
        public NativeGeometryBindingContextV2 WithAdapter(
            string adapterId,
            string adapterProfile,
            string adapterVersion)
        {
            return Copy(
                SessionId,
                adapterId,
                adapterProfile,
                adapterVersion,
                PluginId,
                PluginVersion,
                PluginFingerprint,
                Capabilities,
                HostProduct,
                HostRelease,
                HostRuntime,
                HostMode,
                ProcessId,
                WindowsSessionId,
                ProcessInstanceFingerprint,
                ProcessCreationTime100Ns,
                ExecutableFingerprint);
        }

        /// <summary>Returns a context with a changed plugin tuple.</summary>
        public NativeGeometryBindingContextV2 WithPlugin(
            string pluginId,
            string pluginVersion,
            string pluginFingerprint)
        {
            return Copy(
                SessionId,
                AdapterId,
                AdapterProfile,
                AdapterVersion,
                pluginId,
                pluginVersion,
                pluginFingerprint,
                Capabilities,
                HostProduct,
                HostRelease,
                HostRuntime,
                HostMode,
                ProcessId,
                WindowsSessionId,
                ProcessInstanceFingerprint,
                ProcessCreationTime100Ns,
                ExecutableFingerprint);
        }

        /// <summary>Returns a context with an exact capability set.</summary>
        public NativeGeometryBindingContextV2 WithCapabilities(
            IReadOnlyList<string> capabilities)
        {
            return Copy(
                SessionId,
                AdapterId,
                AdapterProfile,
                AdapterVersion,
                PluginId,
                PluginVersion,
                PluginFingerprint,
                capabilities,
                HostProduct,
                HostRelease,
                HostRuntime,
                HostMode,
                ProcessId,
                WindowsSessionId,
                ProcessInstanceFingerprint,
                ProcessCreationTime100Ns,
                ExecutableFingerprint);
        }

        private NativeGeometryBindingContextV2 Copy(
            string sessionId,
            string adapterId,
            string adapterProfile,
            string adapterVersion,
            string pluginId,
            string pluginVersion,
            string pluginFingerprint,
            IReadOnlyList<string> capabilities,
            string hostProduct,
            string hostRelease,
            string hostRuntime,
            string hostMode,
            long processId,
            long windowsSessionId,
            string processInstanceFingerprint,
            string processCreationTime100Ns,
            string executableFingerprint)
        {
            return new NativeGeometryBindingContextV2(
                sessionId,
                adapterId,
                adapterProfile,
                adapterVersion,
                pluginId,
                pluginVersion,
                pluginFingerprint,
                capabilities,
                hostProduct,
                hostRelease,
                hostRuntime,
                hostMode,
                processId,
                windowsSessionId,
                processInstanceFingerprint,
                processCreationTime100Ns,
                executableFingerprint);
        }

        private Dictionary<string, object?> HostWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "product", HostProduct },
                { "release", HostRelease },
                { "runtime", HostRuntime },
                { "mode", HostMode },
            };
        }

        private Dictionary<string, object?> ProcessWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "pid", ProcessId },
                { "windows_session_id", WindowsSessionId },
                { "instance_fingerprint", ProcessInstanceFingerprint },
                { "creation_time_100ns", ProcessCreationTime100Ns },
                { "executable_fingerprint", ExecutableFingerprint },
            };
        }

        private Dictionary<string, object?> AdapterWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "id", AdapterId },
                { "profile", AdapterProfile },
                { "version", AdapterVersion },
            };
        }

        private Dictionary<string, object?> PluginWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "id", PluginId },
                { "version", PluginVersion },
                { "fingerprint", PluginFingerprint },
            };
        }

        private List<object?> CapabilitiesWireValue()
        {
            List<object?> values = new List<object?>();
            for (int index = 0; index < Capabilities.Count; index++)
            {
                values.Add(Capabilities[index]);
            }

            return values;
        }

        private Dictionary<string, object?> StableHostWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "protocol_version", NativeCadProtocolV2.BridgeVersion },
                { "protocol_major", 1L },
                { "protocol_minor", 0L },
                { "host", HostWireValue() },
                { "host_executable_fingerprint", ExecutableFingerprint },
                { "adapter", AdapterWireValue() },
                { "plugin", PluginWireValue() },
                { "capabilities", CapabilitiesWireValue() },
            };
        }

        private static string Digest(string value)
        {
            return CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal) { { "generated", value } });
        }

    }

    /// <summary>Immutable exact CAD container and block path.</summary>
    public sealed class CadContainer
    {
        /// <summary>Creates a valid v1 container tuple.</summary>
        public CadContainer(
            NativeSpaceKind kind,
            string? layoutHandle,
            string? blockHandle,
            IEnumerable<string> blockPath)
        {
            if (blockPath == null)
            {
                throw new ArgumentNullException(nameof(blockPath));
            }

            if ((kind == NativeSpaceKind.Modelspace || kind == NativeSpaceKind.Paperspace) &&
                (layoutHandle == null || blockHandle != null))
            {
                throw new CanonicalJsonException("Layout containers require a layout handle and no block handle.");
            }

            if (kind == NativeSpaceKind.Block && (layoutHandle != null || blockHandle == null))
            {
                throw new CanonicalJsonException("Block containers require a block handle and no layout handle.");
            }

            if (layoutHandle != null)
            {
                CadHandle.Require(layoutHandle, nameof(layoutHandle));
            }

            if (blockHandle != null)
            {
                CadHandle.Require(blockHandle, nameof(blockHandle));
            }

            List<string> copied = new List<string>();
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (string handle in blockPath)
            {
                CadHandle.Require(handle, nameof(blockPath));
                if (!seen.Add(handle))
                {
                    throw new CanonicalJsonException("Block path repeats a handle.");
                }

                copied.Add(handle);
            }

            Kind = kind;
            LayoutHandle = layoutHandle;
            BlockHandle = blockHandle;
            BlockPath = new ReadOnlyCollection<string>(copied);
        }

        /// <summary>Space kind.</summary>
        public NativeSpaceKind Kind { get; private set; }

        /// <summary>Layout handle for Modelspace/Paperspace.</summary>
        public string? LayoutHandle { get; private set; }

        /// <summary>Block handle for nested block records.</summary>
        public string? BlockHandle { get; private set; }

        /// <summary>Exact outer-to-inner block path.</summary>
        public IReadOnlyList<string> BlockPath { get; private set; }

        /// <summary>Whether this is the one direct Modelspace form editable by v1.</summary>
        public bool IsDirectModelspace
        {
            get
            {
                return Kind == NativeSpaceKind.Modelspace &&
                    BlockHandle == null &&
                    BlockPath.Count == 0;
            }
        }

        /// <summary>Returns the schema space object.</summary>
        public Dictionary<string, object?> ToSpaceWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "kind", NativeWireNames.SpaceKind(Kind) },
                { "layout_handle", LayoutHandle },
                { "block_handle", BlockHandle },
            };
        }

        /// <summary>Returns the canonical container sorting/key array used by v1 digests.</summary>
        public List<object?> ToKeyWireValue()
        {
            List<object?> path = new List<object?>();
            for (int index = 0; index < BlockPath.Count; index++)
            {
                path.Add(BlockPath[index]);
            }

            return new List<object?>
            {
                NativeWireNames.SpaceKind(Kind),
                LayoutHandle ?? string.Empty,
                BlockHandle ?? string.Empty,
                path,
            };
        }

        /// <summary>Returns a stable ordinal sorting key without source content.</summary>
        public string SortKey
        {
            get
            {
                return NativeWireNames.SpaceKind(Kind) + "\u001f" +
                    (LayoutHandle ?? string.Empty) + "\u001f" +
                    (BlockHandle ?? string.Empty) + "\u001f" +
                    string.Join("\u001e", BlockPath);
            }
        }

        /// <inheritdoc />
        public override bool Equals(object? other)
        {
            CadContainer? candidate = other as CadContainer;
            if (candidate == null ||
                Kind != candidate.Kind ||
                !string.Equals(LayoutHandle, candidate.LayoutHandle, StringComparison.Ordinal) ||
                !string.Equals(BlockHandle, candidate.BlockHandle, StringComparison.Ordinal) ||
                BlockPath.Count != candidate.BlockPath.Count)
            {
                return false;
            }

            for (int index = 0; index < BlockPath.Count; index++)
            {
                if (!string.Equals(BlockPath[index], candidate.BlockPath[index], StringComparison.Ordinal))
                {
                    return false;
                }
            }

            return true;
        }

        /// <inheritdoc />
        public override int GetHashCode()
        {
            return StringComparer.Ordinal.GetHashCode(SortKey);
        }
    }

    /// <summary>Immutable supported entity or protected opaque record.</summary>
    public sealed class CadEntitySnapshot
    {
        /// <summary>Creates an immutable entity snapshot with frozen v1 shape checks.</summary>
        public CadEntitySnapshot(
            string handle,
            NativeEntityKind kind,
            string ownerHandle,
            CadContainer container,
            int sequenceIndex,
            string? layer,
            string? text,
            string? style,
            string heightBits,
            string rotationBits,
            Binary64Vector position,
            CadBounds bounds,
            IEnumerable<CadSegment> segments,
            OverlayEvidence overlayEvidence)
        {
            CadHandle.Require(handle, nameof(handle));
            CadHandle.Require(ownerHandle, nameof(ownerHandle));
            if (container == null)
            {
                throw new ArgumentNullException(nameof(container));
            }

            if (sequenceIndex < 0 || sequenceIndex > 1000000)
            {
                throw new CanonicalJsonException("Sequence index is outside the frozen v1 range.");
            }

            if (layer != null)
            {
                CanonicalJson.RequireNfcString(layer, nameof(layer));
                if (layer.Length == 0 || layer.Length > 255)
                {
                    throw new CanonicalJsonException("Layer token is invalid.");
                }
            }

            if (text != null)
            {
                CanonicalJson.RequireNfcString(text, nameof(text));
                if (text.Length > 4096)
                {
                    throw new CanonicalJsonException("DBTEXT exceeds the frozen v1 code-point limit.");
                }
            }

            if (style != null)
            {
                CanonicalJson.RequireNfcString(style, nameof(style));
                if (style.Length == 0 || style.Length > 255)
                {
                    throw new CanonicalJsonException("Text style token is invalid.");
                }
            }

            Binary64.ParseBits(heightBits);
            Binary64.ParseBits(rotationBits);
            if (position == null)
            {
                throw new ArgumentNullException(nameof(position));
            }

            if (bounds == null)
            {
                throw new ArgumentNullException(nameof(bounds));
            }

            if (segments == null)
            {
                throw new ArgumentNullException(nameof(segments));
            }

            if (overlayEvidence == null)
            {
                throw new ArgumentNullException(nameof(overlayEvidence));
            }

            List<CadSegment> copiedSegments = new List<CadSegment>();
            foreach (CadSegment segment in segments)
            {
                if (segment == null)
                {
                    throw new CanonicalJsonException("Segment may not be null.");
                }

                copiedSegments.Add(segment);
            }

            ValidateKind(kind, text, style, copiedSegments.Count);
            Handle = handle;
            Kind = kind;
            OwnerHandle = ownerHandle;
            Container = container;
            SequenceIndex = sequenceIndex;
            Layer = layer;
            Text = text;
            Style = style;
            HeightBits = heightBits;
            RotationBits = rotationBits;
            Position = position;
            Bounds = bounds;
            Segments = new ReadOnlyCollection<CadSegment>(copiedSegments);
            OverlayEvidence = overlayEvidence;
        }

        /// <summary>Uppercase canonical entity handle.</summary>
        public string Handle { get; private set; }

        /// <summary>Supported or protected entity kind.</summary>
        public NativeEntityKind Kind { get; private set; }

        /// <summary>Owner handle.</summary>
        public string OwnerHandle { get; private set; }

        /// <summary>Exact space and block path.</summary>
        public CadContainer Container { get; private set; }

        /// <summary>Gap-preserving record sequence index.</summary>
        public int SequenceIndex { get; private set; }

        /// <summary>Layer token when modeled.</summary>
        public string? Layer { get; private set; }

        /// <summary>DBTEXT content only.</summary>
        public string? Text { get; private set; }

        /// <summary>DBTEXT style token only.</summary>
        public string? Style { get; private set; }

        /// <summary>Exact finite height bits.</summary>
        public string HeightBits { get; private set; }

        /// <summary>Exact finite rotation bits.</summary>
        public string RotationBits { get; private set; }

        /// <summary>Exact position bits.</summary>
        public Binary64Vector Position { get; private set; }

        /// <summary>Exact logical bounds.</summary>
        public CadBounds Bounds { get; private set; }

        /// <summary>Ordered simple segments.</summary>
        public IReadOnlyList<CadSegment> Segments { get; private set; }

        /// <summary>Overlay audit evidence.</summary>
        public OverlayEvidence OverlayEvidence { get; private set; }

        /// <summary>Returns a projection with all frozen v1 entity fields except fingerprints.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            List<object?> path = new List<object?>();
            for (int index = 0; index < Container.BlockPath.Count; index++)
            {
                path.Add(Container.BlockPath[index]);
            }

            List<object?> segments = new List<object?>();
            for (int index = 0; index < Segments.Count; index++)
            {
                segments.Add(Segments[index].ToWireValue());
            }

            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "handle", Handle },
                { "native_type", NativeWireNames.EntityKind(Kind) },
                { "owner_handle", OwnerHandle },
                { "space", Container.ToSpaceWireValue() },
                { "block_path", path },
                { "sequence_index", (long)SequenceIndex },
                { "layer", Layer },
                { "text", Text },
                { "style", Style },
                { "height", HeightBits },
                { "rotation", RotationBits },
                { "position", Position.ToWireValue() },
                { "bounds", Bounds.ToWireValue() },
                { "segments", segments },
                { "overlay_evidence", OverlayEvidence.ToWireValue() },
            };
        }

        /// <summary>Returns the exact v1 geometry fingerprint.</summary>
        public string GeometryFingerprint
        {
            get
            {
                return CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "geometry", ToWireValue() },
                    });
            }
        }

        /// <summary>Returns the exact v1 opaque-state digest.</summary>
        public string OpaqueStateDigest
        {
            get
            {
                return CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "opaque_state", ToWireValue() },
                    });
            }
        }

        /// <summary>Returns the durable opaque v1 target identifier.</summary>
        public string TargetId
        {
            get
            {
                string digest = CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "geometry_fingerprint", GeometryFingerprint },
                        { "opaque_state_digest", OpaqueStateDigest },
                    });
                return "native-target-" + digest.Substring(0, 24);
            }
        }

        /// <summary>Returns a DBTEXT geometry-only translated clone.</summary>
        public CadEntitySnapshot Translate(Binary64Vector delta)
        {
            if (delta == null)
            {
                throw new ArgumentNullException(nameof(delta));
            }

            return new CadEntitySnapshot(
                Handle,
                Kind,
                OwnerHandle,
                Container,
                SequenceIndex,
                Layer,
                Text,
                Style,
                HeightBits,
                RotationBits,
                Position.Translate(delta),
                Bounds.Translate(delta),
                TranslateSegments(delta),
                OverlayEvidence);
        }

        /// <summary>Returns whether every exact exported field is unchanged.</summary>
        public bool ExactlyEquals(CadEntitySnapshot other)
        {
            if (other == null)
            {
                return false;
            }

            return string.Equals(
                CanonicalJson.Serialize(ToWireValue()),
                CanonicalJson.Serialize(other.ToWireValue()),
                StringComparison.Ordinal);
        }

        private IEnumerable<CadSegment> TranslateSegments(Binary64Vector delta)
        {
            List<CadSegment> result = new List<CadSegment>();
            for (int index = 0; index < Segments.Count; index++)
            {
                result.Add(Segments[index].Translate(delta));
            }

            return result;
        }

        private static void ValidateKind(
            NativeEntityKind kind,
            string? text,
            string? style,
            int segmentCount)
        {
            if (kind == NativeEntityKind.DbText)
            {
                if (text == null || style == null || segmentCount != 0)
                {
                    throw new CanonicalJsonException("DBTEXT must have exact text/style and no segments.");
                }

                return;
            }

            if (kind == NativeEntityKind.Line)
            {
                if (text != null || style != null || segmentCount != 1)
                {
                    throw new CanonicalJsonException("LINE must have one segment and no text/style.");
                }

                return;
            }

            if (kind == NativeEntityKind.LwPolyline)
            {
                if (text != null || style != null || segmentCount == 0)
                {
                    throw new CanonicalJsonException("LWPOLYLINE must have segments and no text/style.");
                }

                return;
            }

            if (kind == NativeEntityKind.Opaque && (text != null || style != null))
            {
                throw new CanonicalJsonException("Opaque records must not expose text/style.");
            }
        }
    }

    /// <summary>Immutable modeled table/layout/block state and pre-existing marker resources.</summary>
    public sealed class CadDocumentTables
    {
        /// <summary>Creates exact table/document state from generated token maps.</summary>
        public CadDocumentTables(
            string tableStateDigest,
            string layoutStateDigest,
            string blockStateDigest,
            string? markerLayerFingerprint,
            string? markerStyleFingerprint,
            IDictionary<string, string> layers,
            IDictionary<string, string> styles)
        {
            CanonicalJson.RequireSha256(tableStateDigest, nameof(tableStateDigest));
            CanonicalJson.RequireSha256(layoutStateDigest, nameof(layoutStateDigest));
            CanonicalJson.RequireSha256(blockStateDigest, nameof(blockStateDigest));
            if (markerLayerFingerprint != null)
            {
                CanonicalJson.RequireSha256(markerLayerFingerprint, nameof(markerLayerFingerprint));
            }

            if (markerStyleFingerprint != null)
            {
                CanonicalJson.RequireSha256(markerStyleFingerprint, nameof(markerStyleFingerprint));
            }

            Layers = CopyTokens(layers, nameof(layers));
            Styles = CopyTokens(styles, nameof(styles));
            TableStateDigest = tableStateDigest;
            LayoutStateDigest = layoutStateDigest;
            BlockStateDigest = blockStateDigest;
            MarkerLayerFingerprint = markerLayerFingerprint;
            MarkerStyleFingerprint = markerStyleFingerprint;
        }

        /// <summary>Opaque table state digest.</summary>
        public string TableStateDigest { get; private set; }

        /// <summary>Opaque layout state digest.</summary>
        public string LayoutStateDigest { get; private set; }

        /// <summary>Opaque block state digest.</summary>
        public string BlockStateDigest { get; private set; }

        /// <summary>Audited marker-layer fingerprint, if present.</summary>
        public string? MarkerLayerFingerprint { get; private set; }

        /// <summary>Audited marker-style fingerprint, if present.</summary>
        public string? MarkerStyleFingerprint { get; private set; }

        /// <summary>Pre-existing layer names/fingerprints.</summary>
        public IReadOnlyDictionary<string, string> Layers { get; private set; }

        /// <summary>Pre-existing style names/fingerprints.</summary>
        public IReadOnlyDictionary<string, string> Styles { get; private set; }

        /// <summary>Returns state fields carried by a v1 document.</summary>
        public Dictionary<string, object?> ToStateWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "table_state_digest", TableStateDigest },
                { "layout_state_digest", LayoutStateDigest },
                { "block_state_digest", BlockStateDigest },
                { "marker_layer_fingerprint", MarkerLayerFingerprint },
                { "marker_style_fingerprint", MarkerStyleFingerprint },
            };
        }

        /// <summary>Requires a pre-existing exact layer/style pair; never creates either resource.</summary>
        public bool HasMarkerResources(
            string layer,
            string style,
            string layerFingerprint,
            string styleFingerprint)
        {
            string? observedLayer;
            string? observedStyle;
            return Layers.TryGetValue(layer, out observedLayer) &&
                Styles.TryGetValue(style, out observedStyle) &&
                string.Equals(observedLayer, layerFingerprint, StringComparison.Ordinal) &&
                string.Equals(observedStyle, styleFingerprint, StringComparison.Ordinal) &&
                string.Equals(MarkerLayerFingerprint, layerFingerprint, StringComparison.Ordinal) &&
                string.Equals(MarkerStyleFingerprint, styleFingerprint, StringComparison.Ordinal);
        }

        private static IReadOnlyDictionary<string, string> CopyTokens(
            IDictionary<string, string> source,
            string parameterName)
        {
            if (source == null)
            {
                throw new ArgumentNullException(parameterName);
            }

            Dictionary<string, string> copied = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, string> pair in source)
            {
                CanonicalJson.RequireNfcString(pair.Key, parameterName);
                CanonicalJson.RequireSha256(pair.Value, parameterName);
                copied.Add(pair.Key, pair.Value);
            }

            return new ReadOnlyDictionary<string, string>(copied);
        }
    }

    /// <summary>Immutable generated database snapshot; there are no raw drawing paths.</summary>
    public sealed class CadDocumentSnapshot
    {
        /// <summary>Creates a fully checked immutable state snapshot.</summary>
        public CadDocumentSnapshot(
            string databaseInstanceFingerprint,
            string revisionFingerprint,
            IEnumerable<string> owners,
            IEnumerable<CadEntitySnapshot> entities,
            CadDocumentTables tables,
            NativeSourceBindingV2 source,
            NativeGeometryBindingContextV2 bindingContext)
        {
            CanonicalJson.RequireSha256(databaseInstanceFingerprint, nameof(databaseInstanceFingerprint));
            CanonicalJson.RequireSha256(revisionFingerprint, nameof(revisionFingerprint));
            if (owners == null)
            {
                throw new ArgumentNullException(nameof(owners));
            }

            if (entities == null)
            {
                throw new ArgumentNullException(nameof(entities));
            }

            Tables = tables ?? throw new ArgumentNullException(nameof(tables));
            Source = source ?? throw new ArgumentNullException(nameof(source));
            BindingContext = bindingContext ?? throw new ArgumentNullException(nameof(bindingContext));

            List<string> copiedOwners = new List<string>();
            HashSet<string> knownOwners = new HashSet<string>(StringComparer.Ordinal);
            foreach (string owner in owners)
            {
                CadHandle.Require(owner, nameof(owners));
                if (!knownOwners.Add(owner))
                {
                    throw new CanonicalJsonException("Duplicate owner handle.");
                }

                copiedOwners.Add(owner);
            }

            if (copiedOwners.Count == 0)
            {
                throw new CanonicalJsonException("At least one owner is required.");
            }

            List<CadEntitySnapshot> copiedEntities = new List<CadEntitySnapshot>();
            HashSet<string> handles = new HashSet<string>(StringComparer.Ordinal);
            HashSet<string> sequences = new HashSet<string>(StringComparer.Ordinal);
            int segmentCount = 0;
            CadEntitySnapshot? previous = null;
            foreach (CadEntitySnapshot entity in entities)
            {
                if (entity == null)
                {
                    throw new CanonicalJsonException("Entity may not be null.");
                }

                if (!knownOwners.Contains(entity.OwnerHandle))
                {
                    throw new CanonicalJsonException("Entity owner is not declared.");
                }

                if (!handles.Add(entity.Handle))
                {
                    throw new CanonicalJsonException("Duplicate entity handle.");
                }

                string sequenceKey = entity.Container.SortKey + "\u001f" +
                    entity.SequenceIndex.ToString(CultureInfo.InvariantCulture);
                if (!sequences.Add(sequenceKey))
                {
                    throw new CanonicalJsonException("Duplicate container sequence index.");
                }

                if (previous != null && CompareEntityOrder(previous, entity) >= 0)
                {
                    throw new CanonicalJsonException("Entities are not in canonical container/sequence order.");
                }

                segmentCount += entity.Segments.Count;
                if (segmentCount > NativeCadProtocolV2.MaxGeometrySegments)
                {
                    throw new CanonicalJsonException("Aggregate segment limit exceeded.");
                }

                copiedEntities.Add(entity);
                previous = entity;
            }

            if (copiedEntities.Count > NativeCadProtocolV2.MaxGeometryEntities)
            {
                throw new CanonicalJsonException("Entity limit exceeded.");
            }

            DatabaseInstanceFingerprint = databaseInstanceFingerprint;
            RevisionFingerprint = revisionFingerprint;
            Owners = new ReadOnlyCollection<string>(copiedOwners);
            Entities = new ReadOnlyCollection<CadEntitySnapshot>(copiedEntities);
        }

        /// <summary>Generated database-instance fingerprint.</summary>
        public string DatabaseInstanceFingerprint { get; private set; }

        /// <summary>Current generated revision fingerprint.</summary>
        public string RevisionFingerprint { get; private set; }

        /// <summary>Declared owners.</summary>
        public IReadOnlyList<string> Owners { get; private set; }

        /// <summary>Canonical ordered records.</summary>
        public IReadOnlyList<CadEntitySnapshot> Entities { get; private set; }

        /// <summary>Protected tables/layouts/blocks and marker resources.</summary>
        public CadDocumentTables Tables { get; private set; }

        /// <summary>Generated source binding.</summary>
        public NativeSourceBindingV2 Source { get; private set; }

        /// <summary>Generated full-geometry binding context.</summary>
        public NativeGeometryBindingContextV2 BindingContext { get; private set; }

        /// <summary>Finds a record by exact handle.</summary>
        public CadEntitySnapshot? FindByHandle(string handle)
        {
            for (int index = 0; index < Entities.Count; index++)
            {
                if (string.Equals(Entities[index].Handle, handle, StringComparison.Ordinal))
                {
                    return Entities[index];
                }
            }

            return null;
        }

        /// <summary>Returns an exact clone with a new ordered entity list.</summary>
        public CadDocumentSnapshot WithEntities(IEnumerable<CadEntitySnapshot> entities)
        {
            return new CadDocumentSnapshot(
                DatabaseInstanceFingerprint,
                RevisionFingerprint,
                Owners,
                entities,
                Tables,
                Source,
                BindingContext);
        }

        /// <summary>
        /// Returns an exact clone with supplied ordered owner state. This is
        /// retained for generated fault/readback tests; normal v1 operations
        /// never receive owner mutation capability.
        /// </summary>
        public CadDocumentSnapshot WithOwners(IEnumerable<string> owners)
        {
            return new CadDocumentSnapshot(
                DatabaseInstanceFingerprint,
                RevisionFingerprint,
                owners,
                Entities,
                Tables,
                Source,
                BindingContext);
        }

        /// <summary>Returns an exact clone with a fresh database-instance token.</summary>
        public CadDocumentSnapshot WithDatabaseInstance(string databaseInstanceFingerprint)
        {
            return new CadDocumentSnapshot(
                databaseInstanceFingerprint,
                RevisionFingerprint,
                Owners,
                Entities,
                Tables,
                Source,
                BindingContext);
        }

        /// <summary>Returns an exact clone with the supplied revision token.</summary>
        public CadDocumentSnapshot WithRevision(string revisionFingerprint)
        {
            return new CadDocumentSnapshot(
                DatabaseInstanceFingerprint,
                revisionFingerprint,
                Owners,
                Entities,
                Tables,
                Source,
                BindingContext);
        }

        /// <summary>Returns an exact clone with the supplied protected table state.</summary>
        public CadDocumentSnapshot WithTables(CadDocumentTables tables)
        {
            return new CadDocumentSnapshot(
                DatabaseInstanceFingerprint,
                RevisionFingerprint,
                Owners,
                Entities,
                tables,
                Source,
                BindingContext);
        }

        /// <summary>
        /// Returns an exact clone with the generated source replaced at the
        /// explicit save/output-copy boundary only.
        /// </summary>
        public CadDocumentSnapshot WithSource(NativeSourceBindingV2 source)
        {
            return new CadDocumentSnapshot(
                DatabaseInstanceFingerprint,
                RevisionFingerprint,
                Owners,
                Entities,
                Tables,
                source,
                BindingContext);
        }

        /// <summary>
        /// Returns an exact clone with a fresh host/session binding context.
        /// This is used by generated reopen tests; real adapters must provide
        /// the equivalent fresh export after SaveAndReopen.
        /// </summary>
        public CadDocumentSnapshot WithBindingContext(
            NativeGeometryBindingContextV2 bindingContext)
        {
            return new CadDocumentSnapshot(
                DatabaseInstanceFingerprint,
                RevisionFingerprint,
                Owners,
                Entities,
                Tables,
                Source,
                bindingContext);
        }

        /// <summary>Derives a deterministic post-commit revision from generated state.</summary>
        public string DeriveNextRevision()
        {
            List<object?> owners = new List<object?>();
            for (int index = 0; index < Owners.Count; index++)
            {
                owners.Add(Owners[index]);
            }

            List<object?> entities = new List<object?>();
            for (int index = 0; index < Entities.Count; index++)
            {
                entities.Add(Entities[index].ToWireValue());
            }

            return CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "previous_revision", RevisionFingerprint },
                    { "owners", owners },
                    { "entities", entities },
                    { "document_state", Tables.ToStateWireValue() },
                });
        }

        /// <summary>Compares exact canonical entity order.</summary>
        public static int CompareEntityOrder(CadEntitySnapshot left, CadEntitySnapshot right)
        {
            int container = string.CompareOrdinal(left.Container.SortKey, right.Container.SortKey);
            if (container != 0)
            {
                return container;
            }

            return left.SequenceIndex.CompareTo(right.SequenceIndex);
        }
    }
}
