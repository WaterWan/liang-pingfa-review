// SPDX-License-Identifier: MIT
// Fixed-manifest executor for generated vendor-neutral CAD state only.

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Text;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.Core
{
    /// <summary>Exact geometry transition required by a translate_dbtext operation.</summary>
    public sealed class TranslatedGeometryV2
    {
        /// <summary>Creates an immutable translated geometry requirement.</summary>
        public TranslatedGeometryV2(
            Binary64Vector position,
            CadBounds bounds,
            IEnumerable<CadSegment> segments)
        {
            Position = position ?? throw new ArgumentNullException(nameof(position));
            Bounds = bounds ?? throw new ArgumentNullException(nameof(bounds));
            if (segments == null)
            {
                throw new ArgumentNullException(nameof(segments));
            }

            List<CadSegment> copied = new List<CadSegment>();
            foreach (CadSegment segment in segments)
            {
                copied.Add(segment ?? throw new CanonicalJsonException("Translated segment may not be null."));
            }

            Segments = new ReadOnlyCollection<CadSegment>(copied);
        }

        /// <summary>Translated position.</summary>
        public Binary64Vector Position { get; private set; }

        /// <summary>Translated bounds.</summary>
        public CadBounds Bounds { get; private set; }

        /// <summary>Translated ordered segments.</summary>
        public IReadOnlyList<CadSegment> Segments { get; private set; }

        /// <summary>Derives a frozen exact geometry transition.</summary>
        public static TranslatedGeometryV2 From(CadEntitySnapshot entity, Binary64Vector delta)
        {
            if (entity == null)
            {
                throw new ArgumentNullException(nameof(entity));
            }

            CadEntitySnapshot translated = entity.Translate(delta);
            return new TranslatedGeometryV2(
                translated.Position,
                translated.Bounds,
                translated.Segments);
        }

        /// <summary>Returns whether all translated geometry axes/segments match exactly.</summary>
        public bool Matches(CadEntitySnapshot entity)
        {
            if (entity == null ||
                !Position.Equals(entity.Position) ||
                !Bounds.Minimum.Equals(entity.Bounds.Minimum) ||
                !Bounds.Maximum.Equals(entity.Bounds.Maximum) ||
                Segments.Count != entity.Segments.Count)
            {
                return false;
            }

            for (int index = 0; index < Segments.Count; index++)
            {
                CadSegment expected = Segments[index];
                CadSegment actual = entity.Segments[index];
                if (!expected.Start.Equals(actual.Start) || !expected.End.Equals(actual.End))
                {
                    return false;
                }
            }

            return true;
        }

        /// <summary>Returns exact v1 expected_after fields.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            List<object?> segments = new List<object?>();
            for (int index = 0; index < Segments.Count; index++)
            {
                segments.Add(Segments[index].ToWireValue());
            }

            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "position", Position.ToWireValue() },
                { "bounds", Bounds.ToWireValue() },
                { "segments", segments },
            };
        }
    }

    /// <summary>Full fixed marker policy binding consumed by the generated core.</summary>
    public sealed class MarkerPolicyBindingV2
    {
        /// <summary>Creates an immutable marker policy without any creation capability.</summary>
        public MarkerPolicyBindingV2(
            bool profileEnabled,
            bool enabled,
            bool pluginCapability,
            string layer,
            string style,
            string layerFingerprint,
            string styleFingerprint,
            string heightBits,
            string rotationBits,
            OverlayEvidence defaultOverlayEvidence)
        {
            CanonicalJson.RequireNfcString(layer, nameof(layer));
            CanonicalJson.RequireNfcString(style, nameof(style));
            CanonicalJson.RequireSha256(layerFingerprint, nameof(layerFingerprint));
            CanonicalJson.RequireSha256(styleFingerprint, nameof(styleFingerprint));
            Binary64.ParseBits(heightBits);
            Binary64.ParseBits(rotationBits);
            if (defaultOverlayEvidence == null)
            {
                throw new ArgumentNullException(nameof(defaultOverlayEvidence));
            }

            if (defaultOverlayEvidence.UniqueContent ||
                defaultOverlayEvidence.LeftPanel ||
                defaultOverlayEvidence.CorrespondingRightAbsent ||
                defaultOverlayEvidence.VisibleInterference ||
                !defaultOverlayEvidence.UnsupportedData)
            {
                throw new CanonicalJsonException("Marker overlay evidence must use the frozen v1 default.");
            }

            ProfileEnabled = profileEnabled;
            Enabled = enabled;
            PluginCapability = pluginCapability;
            Layer = layer;
            Style = style;
            LayerFingerprint = layerFingerprint;
            StyleFingerprint = styleFingerprint;
            HeightBits = heightBits;
            RotationBits = rotationBits;
            DefaultOverlayEvidence = defaultOverlayEvidence;
        }

        /// <summary>Whether the marker operation profile is enabled.</summary>
        public bool ProfileEnabled { get; private set; }

        /// <summary>Whether configured marker output is enabled.</summary>
        public bool Enabled { get; private set; }

        /// <summary>Whether an external capability is explicitly asserted.</summary>
        public bool PluginCapability { get; private set; }

        /// <summary>Required pre-existing layer token.</summary>
        public string Layer { get; private set; }

        /// <summary>Required pre-existing style token.</summary>
        public string Style { get; private set; }

        /// <summary>Required layer fingerprint.</summary>
        public string LayerFingerprint { get; private set; }

        /// <summary>Required style fingerprint.</summary>
        public string StyleFingerprint { get; private set; }

        /// <summary>Fixed marker height bits.</summary>
        public string HeightBits { get; private set; }

        /// <summary>Fixed marker rotation bits.</summary>
        public string RotationBits { get; private set; }

        /// <summary>Fixed marker evidence.</summary>
        public OverlayEvidence DefaultOverlayEvidence { get; private set; }

        /// <summary>Returns whether all explicit capability gates are open.</summary>
        public bool IsEnabled
        {
            get { return ProfileEnabled && Enabled && PluginCapability; }
        }

        /// <summary>Derives the only permitted marker text from an operation ID.</summary>
        public string DeriveMarkerText(string operationId)
        {
            NativeIdentifiers.RequireOperationId(operationId);
            return NativeCadProtocolV2.MarkerTextPrefix +
                operationId.Substring("native-operation-".Length);
        }

        /// <summary>Returns the frozen v1 marker policy object.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "policy_version", "marker-policy/v1" },
                { "profile", "create_review_marker/v1" },
                { "profile_enabled", ProfileEnabled },
                { "enabled", Enabled },
                { "plugin_capability", PluginCapability },
                { "layer", Layer },
                { "style", Style },
                { "layer_fingerprint", LayerFingerprint },
                { "style_fingerprint", StyleFingerprint },
                { "height_bits", HeightBits },
                { "rotation_bits", RotationBits },
                { "text_prefix", NativeCadProtocolV2.MarkerTextPrefix },
                { "text_derivation_version", "operation-id-suffix/v1" },
                {
                    "geometry_defaults",
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "space_kind", "modelspace" },
                        { "block_path", new List<object?>() },
                        { "overlay_evidence", DefaultOverlayEvidence.ToWireValue() },
                    }
                },
            };
        }
    }

    /// <summary>Exact prewrite revision projection compared before every mutation.</summary>
    public sealed class ExpectedPrewriteRevisionV2
    {
        /// <summary>Creates an immutable prewrite tuple.</summary>
        public ExpectedPrewriteRevisionV2(
            string databaseInstanceFingerprint,
            string revisionFingerprint,
            string geometryDigest,
            string protectedStateDigest,
            string protectedOrderDigest,
            string documentStateDigest,
            NativeSourceBindingV2 source)
        {
            CanonicalJson.RequireSha256(databaseInstanceFingerprint, nameof(databaseInstanceFingerprint));
            CanonicalJson.RequireSha256(revisionFingerprint, nameof(revisionFingerprint));
            CanonicalJson.RequireSha256(geometryDigest, nameof(geometryDigest));
            CanonicalJson.RequireSha256(protectedStateDigest, nameof(protectedStateDigest));
            CanonicalJson.RequireSha256(protectedOrderDigest, nameof(protectedOrderDigest));
            CanonicalJson.RequireSha256(documentStateDigest, nameof(documentStateDigest));
            Source = source ?? throw new ArgumentNullException(nameof(source));
            DatabaseInstanceFingerprint = databaseInstanceFingerprint;
            RevisionFingerprint = revisionFingerprint;
            GeometryDigest = geometryDigest;
            ProtectedStateDigest = protectedStateDigest;
            ProtectedOrderDigest = protectedOrderDigest;
            DocumentStateDigest = documentStateDigest;
        }

        /// <summary>Expected generated database instance.</summary>
        public string DatabaseInstanceFingerprint { get; private set; }

        /// <summary>Expected generated revision.</summary>
        public string RevisionFingerprint { get; private set; }

        /// <summary>Expected complete geometry digest.</summary>
        public string GeometryDigest { get; private set; }

        /// <summary>Expected protected-state digest.</summary>
        public string ProtectedStateDigest { get; private set; }

        /// <summary>Expected protected-order digest.</summary>
        public string ProtectedOrderDigest { get; private set; }

        /// <summary>Expected document-state digest.</summary>
        public string DocumentStateDigest { get; private set; }

        /// <summary>Expected source binding.</summary>
        public NativeSourceBindingV2 Source { get; private set; }

        /// <summary>Builds the tuple from a full exact export.</summary>
        public static ExpectedPrewriteRevisionV2 From(GeometryExportV2 export)
        {
            if (export == null)
            {
                throw new ArgumentNullException(nameof(export));
            }

            return new ExpectedPrewriteRevisionV2(
                export.Document.DatabaseInstanceFingerprint,
                export.Document.RevisionFingerprint,
                export.Document.CompleteGeometryDigest,
                export.Document.ProtectedStateDigest,
                export.Document.ProtectedOrderDigest,
                export.Document.DocumentStateDigest,
                export.Snapshot.Source);
        }

        /// <summary>Returns true only when every prewrite state field is exact.</summary>
        public bool Matches(GeometryExportV2 export)
        {
            return export != null &&
                string.Equals(DatabaseInstanceFingerprint, export.Document.DatabaseInstanceFingerprint, StringComparison.Ordinal) &&
                string.Equals(RevisionFingerprint, export.Document.RevisionFingerprint, StringComparison.Ordinal) &&
                string.Equals(GeometryDigest, export.Document.CompleteGeometryDigest, StringComparison.Ordinal) &&
                string.Equals(ProtectedStateDigest, export.Document.ProtectedStateDigest, StringComparison.Ordinal) &&
                string.Equals(ProtectedOrderDigest, export.Document.ProtectedOrderDigest, StringComparison.Ordinal) &&
                string.Equals(DocumentStateDigest, export.Document.DocumentStateDigest, StringComparison.Ordinal) &&
                Source.ExactlyMatches(export.Snapshot.Source);
        }

        /// <summary>Returns frozen v1 prewrite fields used by the subset.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "source_binding", Source.ToWireValue() },
                { "document_path_fingerprint", Source.PathFingerprint },
                { "document_file_identity_fingerprint", Source.FileIdentityFingerprint },
                { "document_content_sha256", Source.Sha256 },
                { "document_byte_size", Source.ByteSize },
                { "database_instance_fingerprint", DatabaseInstanceFingerprint },
                { "revision_fingerprint", RevisionFingerprint },
                { "geometry_digest", GeometryDigest },
                { "protected_state_digest", ProtectedStateDigest },
                { "protected_order_digest", ProtectedOrderDigest },
                { "document_state_digest", DocumentStateDigest },
            };
        }
    }

    /// <summary>Base type for the only three typed operations accepted by the core.</summary>
    public abstract class ManifestOperationV2
    {
        /// <summary>Creates an operation with a fixed v1 ID.</summary>
        protected ManifestOperationV2(string operationId, NativeOperationKind kind)
        {
            NativeIdentifiers.RequireOperationId(operationId);
            OperationId = operationId;
            Kind = kind;
        }

        /// <summary>Opaque operation ID.</summary>
        public string OperationId { get; private set; }

        /// <summary>Closed operation kind.</summary>
        public NativeOperationKind Kind { get; private set; }

        /// <summary>Target ID for mutation operations; null for markers.</summary>
        public abstract string? TargetId { get; }

        /// <summary>Returns the frozen operation object fields.</summary>
        public abstract Dictionary<string, object?> ToWireValue();
    }

    /// <summary>Fixed exact DBTEXT translation operation.</summary>
    public sealed class TranslateDbTextOperationV2 : ManifestOperationV2
    {
        /// <summary>Creates a translation operation.</summary>
        public TranslateDbTextOperationV2(
            string operationId,
            string targetId,
            Binary64Vector delta,
            TranslatedGeometryV2 expectedAfter)
            : base(operationId, NativeOperationKind.TranslateDbText)
        {
            NativeIdentifiers.RequireTargetId(targetId);
            Delta = delta ?? throw new ArgumentNullException(nameof(delta));
            ExpectedAfter = expectedAfter ?? throw new ArgumentNullException(nameof(expectedAfter));
            Target = targetId;
        }

        /// <summary>Opaque target ID.</summary>
        public string Target { get; private set; }

        /// <summary>Exact x/y/z bit delta.</summary>
        public Binary64Vector Delta { get; private set; }

        /// <summary>Exact expected post-translation geometry.</summary>
        public TranslatedGeometryV2 ExpectedAfter { get; private set; }

        /// <inheritdoc />
        public override string? TargetId
        {
            get { return Target; }
        }

        /// <inheritdoc />
        public override Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "operation_id", OperationId },
                { "kind", NativeWireNames.OperationKind(Kind) },
                { "target_id", Target },
                { "delta", Delta.ToWireValue() },
                { "expected_after", ExpectedAfter.ToWireValue() },
            };
        }
    }

    /// <summary>Fixed exact overlay DBTEXT delete operation.</summary>
    public sealed class DeleteAuxiliaryOverlayTextOperationV2 : ManifestOperationV2
    {
        /// <summary>Creates a delete operation whose evidence was already audited.</summary>
        public DeleteAuxiliaryOverlayTextOperationV2(
            string operationId,
            string targetId,
            bool eligibilityAudited)
            : base(operationId, NativeOperationKind.DeleteAuxiliaryOverlayText)
        {
            NativeIdentifiers.RequireTargetId(targetId);
            Target = targetId;
            EligibilityAudited = eligibilityAudited;
        }

        /// <summary>Opaque target ID.</summary>
        public string Target { get; private set; }

        /// <summary>Typed-core gate requiring the frozen evidence to have been audited.</summary>
        public bool EligibilityAudited { get; private set; }

        /// <inheritdoc />
        public override string? TargetId
        {
            get { return Target; }
        }

        /// <inheritdoc />
        public override Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "operation_id", OperationId },
                { "kind", NativeWireNames.OperationKind(Kind) },
                { "target_id", Target },
            };
        }
    }

    /// <summary>Fixed derived review marker append operation.</summary>
    public sealed class CreateReviewMarkerOperationV2 : ManifestOperationV2
    {
        /// <summary>Creates one exact marker operation.</summary>
        public CreateReviewMarkerOperationV2(
            string operationId,
            string ownerHandle,
            CadContainer container,
            int sequenceIndex,
            Binary64Vector position,
            string markerText,
            string markerFingerprint,
            string layer,
            string style,
            string heightBits,
            string rotationBits,
            OverlayEvidence overlayEvidence)
            : base(operationId, NativeOperationKind.CreateReviewMarker)
        {
            CadHandle.Require(ownerHandle, nameof(ownerHandle));
            if (container == null)
            {
                throw new ArgumentNullException(nameof(container));
            }

            if (sequenceIndex < 0 || sequenceIndex > 1000000)
            {
                throw new CanonicalJsonException("Marker sequence index is invalid.");
            }

            CanonicalJson.RequireNfcString(markerText, nameof(markerText));
            CanonicalJson.RequireSha256(markerFingerprint, nameof(markerFingerprint));
            CanonicalJson.RequireNfcString(layer, nameof(layer));
            CanonicalJson.RequireNfcString(style, nameof(style));
            Binary64.ParseBits(heightBits);
            Binary64.ParseBits(rotationBits);
            OwnerHandle = ownerHandle;
            Container = container;
            SequenceIndex = sequenceIndex;
            Position = position ?? throw new ArgumentNullException(nameof(position));
            MarkerText = markerText;
            MarkerFingerprint = markerFingerprint;
            Layer = layer;
            Style = style;
            HeightBits = heightBits;
            RotationBits = rotationBits;
            OverlayEvidence = overlayEvidence ?? throw new ArgumentNullException(nameof(overlayEvidence));
            if (!string.Equals(MarkerFingerprint, ComputeMarkerFingerprint(this), StringComparison.Ordinal))
            {
                throw new CanonicalJsonException("Marker fingerprint differs from its fixed fields.");
            }
        }

        /// <summary>Owner handle.</summary>
        public string OwnerHandle { get; private set; }

        /// <summary>Fixed direct Modelspace container.</summary>
        public CadContainer Container { get; private set; }

        /// <summary>Fixed gap-safe append index.</summary>
        public int SequenceIndex { get; private set; }

        /// <summary>Fixed marker position.</summary>
        public Binary64Vector Position { get; private set; }

        /// <summary>Operation-derived marker text.</summary>
        public string MarkerText { get; private set; }

        /// <summary>Exact fingerprint of all marker fields except its generated handle.</summary>
        public string MarkerFingerprint { get; private set; }

        /// <summary>Required layer token.</summary>
        public string Layer { get; private set; }

        /// <summary>Required style token.</summary>
        public string Style { get; private set; }

        /// <summary>Fixed height bits.</summary>
        public string HeightBits { get; private set; }

        /// <summary>Fixed rotation bits.</summary>
        public string RotationBits { get; private set; }

        /// <summary>Fixed marker overlay evidence.</summary>
        public OverlayEvidence OverlayEvidence { get; private set; }

        /// <inheritdoc />
        public override string? TargetId
        {
            get { return null; }
        }

        /// <inheritdoc />
        public override Dictionary<string, object?> ToWireValue()
        {
            List<object?> path = new List<object?>();
            for (int index = 0; index < Container.BlockPath.Count; index++)
            {
                path.Add(Container.BlockPath[index]);
            }

            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "operation_id", OperationId },
                { "kind", NativeWireNames.OperationKind(Kind) },
                { "owner_handle", OwnerHandle },
                { "space", Container.ToSpaceWireValue() },
                { "block_path", path },
                { "sequence_index", (long)SequenceIndex },
                { "position", Position.ToWireValue() },
                { "marker_text", MarkerText },
                { "marker_fingerprint", MarkerFingerprint },
                { "layer", Layer },
                { "style", Style },
                { "height", HeightBits },
                { "rotation", RotationBits },
                { "overlay_evidence", OverlayEvidence.ToWireValue() },
            };
        }

        /// <summary>Computes the exact marker fingerprint without a generated handle.</summary>
        public static string ComputeMarkerFingerprint(CreateReviewMarkerOperationV2 operation)
        {
            if (operation == null)
            {
                throw new ArgumentNullException(nameof(operation));
            }

            return DeriveMarkerFingerprint(
                operation.OwnerHandle,
                operation.Container,
                operation.SequenceIndex,
                operation.Position,
                operation.MarkerText,
                operation.Layer,
                operation.Style,
                operation.HeightBits,
                operation.RotationBits,
                operation.OverlayEvidence);
        }

        /// <summary>Derives a marker fingerprint before constructing the immutable operation.</summary>
        public static string DeriveMarkerFingerprint(
            string ownerHandle,
            CadContainer container,
            int sequenceIndex,
            Binary64Vector position,
            string markerText,
            string layer,
            string style,
            string heightBits,
            string rotationBits,
            OverlayEvidence overlayEvidence)
        {
            if (container == null)
            {
                throw new ArgumentNullException(nameof(container));
            }

            if (position == null)
            {
                throw new ArgumentNullException(nameof(position));
            }

            if (overlayEvidence == null)
            {
                throw new ArgumentNullException(nameof(overlayEvidence));
            }

            List<object?> path = new List<object?>();
            for (int index = 0; index < container.BlockPath.Count; index++)
            {
                path.Add(container.BlockPath[index]);
            }

            return CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "kind", "create_review_marker" },
                    { "owner_handle", ownerHandle },
                    { "space", container.ToSpaceWireValue() },
                    { "block_path", path },
                    { "sequence_index", (long)sequenceIndex },
                    { "position", position.ToWireValue() },
                    { "marker_text", markerText },
                    { "layer", layer },
                    { "style", style },
                    { "height", heightBits },
                    { "rotation", rotationBits },
                    { "overlay_evidence", overlayEvidence.ToWireValue() },
                });
        }
    }

    /// <summary>
    /// Full private-manifest integrity accepted only after the schema boundary
    /// has checked the Python envelope. The core retains this value instead of
    /// substituting a hash of its smaller typed projection.
    /// </summary>
    public sealed class ValidatedFullManifestIntegrityV2
    {
        private ValidatedFullManifestIntegrityV2(string sha256)
        {
            CanonicalJson.RequireSha256(sha256, nameof(sha256));
            Sha256 = sha256;
        }

        /// <summary>Validated top-level <c>integrity.sha256</c> value.</summary>
        public string Sha256 { get; private set; }

        /// <summary>
        /// Validates a canonical private-manifest document, accepting the one
        /// final LF emitted by Python's private workspace writer. This helper
        /// checks integrity only; the caller's schema boundary remains
        /// responsible for validating all manifest semantics before projection.
        /// </summary>
        public static ValidatedFullManifestIntegrityV2 FromManifestDocumentUtf8(
            byte[] document)
        {
            return FromManifestDocumentUtf8(
                document,
                NativeCadProtocolV2.MaxManifestDocumentBytes);
        }

        /// <summary>
        /// Validates a canonical private-manifest document against an explicit
        /// whole-document boundary. The boundary must cover the outer envelope,
        /// not merely its embedded 16 MiB geometry carrier.
        /// </summary>
        public static ValidatedFullManifestIntegrityV2 FromManifestDocumentUtf8(
            byte[] document,
            int maximumBytes)
        {
            if (document == null)
            {
                throw new ArgumentNullException(nameof(document));
            }

            if (maximumBytes < 1 || document.Length == 0 || document.Length > maximumBytes + 1)
            {
                throw new CanonicalJsonException("Full manifest document exceeds its fixed byte limit.");
            }

            int length = document.Length;
            if (document[length - 1] == 0x0a)
            {
                length--;
            }

            if (length == 0)
            {
                throw new CanonicalJsonException("Full manifest document is empty.");
            }

            byte[] canonical = new byte[length];
            Buffer.BlockCopy(document, 0, canonical, 0, length);
            object? parsed = CanonicalJson.RequireCanonicalUtf8(
                canonical,
                maximumBytes,
                NativeCadCanonicalJsonProfiles.Manifest);
            return FromParsedManifest(parsed);
        }

        /// <summary>
        /// Validates the full envelope's required top-level self-integrity
        /// value without attempting to reconstruct a full manifest from the
        /// smaller core projection.
        /// </summary>
        public static ValidatedFullManifestIntegrityV2 FromParsedManifest(object? parsed)
        {
            Dictionary<string, object?>? manifest = parsed as Dictionary<string, object?>;
            if (manifest == null)
            {
                throw new CanonicalJsonException("Full manifest root is not an object.");
            }

            object? rawGeometry;
            if (!manifest.TryGetValue("preconditions_geometry_json", out rawGeometry) ||
                !(rawGeometry is string))
            {
                throw new CanonicalJsonException(
                    "Full manifest geometry carrier is missing or invalid.");
            }

            // The outer manifest profile preserves this exact carrier string.
            // Its own JSON must nevertheless satisfy the strict inner v1
            // geometry envelope before the full-manifest hash is trusted.
            NativeGeometryJsonV2.RequireCanonicalGeometryCarrier((string)rawGeometry);

            object? rawIntegrity;
            if (!manifest.TryGetValue("integrity", out rawIntegrity))
            {
                throw new CanonicalJsonException("Full manifest integrity is missing.");
            }

            Dictionary<string, object?>? integrity = rawIntegrity as Dictionary<string, object?>;
            if (integrity == null ||
                integrity.Count != 2 ||
                !integrity.ContainsKey("algorithm") ||
                !integrity.ContainsKey("sha256"))
            {
                throw new CanonicalJsonException("Full manifest integrity is malformed.");
            }

            object? algorithm;
            object? claimed;
            if (!integrity.TryGetValue("algorithm", out algorithm) ||
                !string.Equals(algorithm as string, "SHA-256", StringComparison.Ordinal) ||
                !integrity.TryGetValue("sha256", out claimed) ||
                !(claimed is string))
            {
                throw new CanonicalJsonException("Full manifest integrity is malformed.");
            }

            string claimedSha256 = (string)claimed;
            CanonicalJson.RequireSha256(claimedSha256, "fullManifestIntegritySha256");

            Dictionary<string, object?> payload = new Dictionary<string, object?>(
                StringComparer.Ordinal);
            foreach (KeyValuePair<string, object?> entry in manifest)
            {
                if (!string.Equals(entry.Key, "integrity", StringComparison.Ordinal))
                {
                    payload.Add(entry.Key, entry.Value);
                }
            }

            string expectedSha256 = CanonicalJson.Sha256Hex(
                payload,
                NativeCadCanonicalJsonProfiles.Manifest);
            if (!string.Equals(expectedSha256, claimedSha256, StringComparison.Ordinal))
            {
                throw new CanonicalJsonException("Full manifest integrity does not match its payload.");
            }

            return new ValidatedFullManifestIntegrityV2(claimedSha256);
        }
    }

    /// <summary>Whether a private SaveAs may replace the prewrite file identity.</summary>
    public enum FileIdentityTransitionPolicyV2
    {
        /// <summary>The final private file must retain the prewrite identity.</summary>
        SameIdentityRequired,

        /// <summary>A SaveAs replacement is permitted only at the authorized private path.</summary>
        ReplacementAllowed,
    }

    /// <summary>
    /// Immutable post-save constraints. They deliberately exclude final
    /// content bytes: hash, size, identity, and final revision are observable
    /// only after SaveAndReopen has produced a retained actual output.
    /// </summary>
    public sealed class FinalOutputConstraintsV2
    {
        /// <summary>Creates a narrow authorization for one private output path.</summary>
        public FinalOutputConstraintsV2(
            string authorizedPrivatePathFingerprint,
            string authorizedPrivateRootFingerprint,
            string requiredDwgHeaderSignature,
            string requiredDwgVersion,
            long maxByteSize,
            FileIdentityTransitionPolicyV2 fileIdentityTransitionPolicy)
        {
            CanonicalJson.RequireSha256(
                authorizedPrivatePathFingerprint,
                nameof(authorizedPrivatePathFingerprint));
            CanonicalJson.RequireSha256(
                authorizedPrivateRootFingerprint,
                nameof(authorizedPrivateRootFingerprint));
            if (!IsDwgHeader(requiredDwgHeaderSignature) ||
                !string.Equals(
                    requiredDwgHeaderSignature,
                    requiredDwgVersion,
                    StringComparison.Ordinal) ||
                maxByteSize < 6)
            {
                throw new CanonicalJsonException(
                    "Final output constraints are not a valid DWG authorization.");
            }

            AuthorizedPrivatePathFingerprint = authorizedPrivatePathFingerprint;
            AuthorizedPrivateRootFingerprint = authorizedPrivateRootFingerprint;
            RequiredDwgHeaderSignature = requiredDwgHeaderSignature;
            RequiredDwgVersion = requiredDwgVersion;
            MaxByteSize = maxByteSize;
            FileIdentityTransitionPolicy = fileIdentityTransitionPolicy;
        }

        /// <summary>Exact private output path fingerprint permitted after save.</summary>
        public string AuthorizedPrivatePathFingerprint { get; private set; }

        /// <summary>Exact private workspace root fingerprint retained by the host adapter.</summary>
        public string AuthorizedPrivateRootFingerprint { get; private set; }

        /// <summary>Required saved DWG header signature.</summary>
        public string RequiredDwgHeaderSignature { get; private set; }

        /// <summary>Required saved DWG version token.</summary>
        public string RequiredDwgVersion { get; private set; }

        /// <summary>Maximum permitted post-save file size.</summary>
        public long MaxByteSize { get; private set; }

        /// <summary>Authorized identity-transition mode.</summary>
        public FileIdentityTransitionPolicyV2 FileIdentityTransitionPolicy
        {
            get;
            private set;
        }

        /// <summary>Returns the exact v2 manifest constraint fields.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                {
                    "authorized_private_path_fingerprint",
                    AuthorizedPrivatePathFingerprint
                },
                {
                    "authorized_private_root_fingerprint",
                    AuthorizedPrivateRootFingerprint
                },
                { "require_same_volume_as_prewrite", true },
                { "require_within_private_root", true },
                { "required_dwg_header_signature", RequiredDwgHeaderSignature },
                { "required_dwg_version", RequiredDwgVersion },
                { "max_byte_size", MaxByteSize },
                {
                    "file_identity_transition_policy",
                    FileIdentityTransitionPolicy ==
                        FileIdentityTransitionPolicyV2.SameIdentityRequired
                        ? "same_identity_required"
                        : "replacement_allowed"
                },
            };
        }

        /// <summary>Fails unless an actual saved source satisfies every constraint.</summary>
        public void RequireActual(
            NativeSourceBindingV2 prewrite,
            NativeSourceBindingV2 actual)
        {
            if (prewrite == null)
            {
                throw new ArgumentNullException(nameof(prewrite));
            }

            if (actual == null ||
                !string.Equals(
                    actual.PathFingerprint,
                    AuthorizedPrivatePathFingerprint,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    actual.DwgHeaderSignature,
                    RequiredDwgHeaderSignature,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    actual.DwgHeaderSignature,
                    RequiredDwgVersion,
                    StringComparison.Ordinal) ||
                actual.ByteSize < 6 ||
                actual.ByteSize > MaxByteSize ||
                string.Equals(actual.Sha256, prewrite.Sha256, StringComparison.Ordinal) ||
                (FileIdentityTransitionPolicy ==
                    FileIdentityTransitionPolicyV2.SameIdentityRequired &&
                 !string.Equals(
                    actual.FileIdentityFingerprint,
                    prewrite.FileIdentityFingerprint,
                    StringComparison.Ordinal)))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ReadbackMismatch,
                    "Actual final source violates v2 output constraints.");
            }
        }

        /// <summary>Creates a bounded source-free constraint for legacy generated helpers.</summary>
        public static FinalOutputConstraintsV2 ForGeneratedSource(
            NativeSourceBindingV2 source)
        {
            if (source == null)
            {
                throw new ArgumentNullException(nameof(source));
            }

            return new FinalOutputConstraintsV2(
                source.PathFingerprint,
                CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(
                        StringComparer.Ordinal)
                    {
                        { "generated_private_root", source.PathFingerprint },
                    }),
                source.DwgHeaderSignature,
                source.DwgHeaderSignature,
                Math.Max(6L, source.ByteSize + 1024L),
                FileIdentityTransitionPolicyV2.ReplacementAllowed);
        }

        private static bool IsDwgHeader(string value)
        {
            if (value == null || value.Length != 6 ||
                value[0] != 'A' || value[1] != 'C')
            {
                return false;
            }

            for (int index = 2; index < value.Length; index++)
            {
                char character = value[index];
                if (!((character >= '0' && character <= '9') ||
                    (character >= 'A' && character <= 'Z')))
                {
                    return false;
                }
            }

            return true;
        }
    }

    /// <summary>Typed subset of a schema-validated fixed v2 manifest.</summary>
    public sealed class CoreManifestV2
    {
        /// <summary>Creates a typed manifest with one immutable precondition export.</summary>
        public CoreManifestV2(
            string manifestId,
            ValidatedFullManifestIntegrityV2 fullManifestIntegrity,
            string nonce,
            GeometryExportV2 preconditions,
            ExpectedPrewriteRevisionV2 expectedPrewriteRevision,
            NativeSourceBindingV2 expectedPrewriteOutputCopyBinding,
            FinalOutputConstraintsV2 finalOutputConstraints,
            string expectedStableHostBindingDigest,
            MarkerPolicyBindingV2 markerPolicy,
            IEnumerable<ManifestOperationV2> operations)
        {
            NativeIdentifiers.RequireManifestId(manifestId);
            if (fullManifestIntegrity == null)
            {
                throw new ArgumentNullException(nameof(fullManifestIntegrity));
            }

            CanonicalJson.RequireNfcString(nonce, nameof(nonce));
            if (nonce.Length < 1 || nonce.Length > 128)
            {
                throw new CanonicalJsonException("Manifest nonce is invalid.");
            }

            Preconditions = preconditions ?? throw new ArgumentNullException(nameof(preconditions));
            ExpectedPrewriteRevision = expectedPrewriteRevision ?? throw new ArgumentNullException(nameof(expectedPrewriteRevision));
            ExpectedPrewriteOutputCopyBinding = expectedPrewriteOutputCopyBinding ??
                throw new ArgumentNullException(nameof(expectedPrewriteOutputCopyBinding));
            FinalOutputConstraints = finalOutputConstraints ??
                throw new ArgumentNullException(nameof(finalOutputConstraints));
            CanonicalJson.RequireSha256(
                expectedStableHostBindingDigest,
                nameof(expectedStableHostBindingDigest));
            ExpectedStableHostBindingDigest = expectedStableHostBindingDigest;
            MarkerPolicy = markerPolicy ?? throw new ArgumentNullException(nameof(markerPolicy));
            if (operations == null)
            {
                throw new ArgumentNullException(nameof(operations));
            }

            List<ManifestOperationV2> copied = new List<ManifestOperationV2>();
            foreach (ManifestOperationV2 operation in operations)
            {
                copied.Add(operation ?? throw new CanonicalJsonException("Manifest operation may not be null."));
            }

            if (copied.Count == 0 ||
                copied.Count > NativeCadProtocolV2.MaxNativeOperations)
            {
                throw new CanonicalJsonException("Manifest operation count is invalid.");
            }

            ManifestId = manifestId;
            FullManifestIntegritySha256 = fullManifestIntegrity.Sha256;
            Nonce = nonce;
            Operations = new ReadOnlyCollection<ManifestOperationV2>(copied);
            PreconditionsGeometrySha256 = CanonicalJson.Sha256Hex(Preconditions.ToCanonicalJsonUtf8());
            CoreProjectionIntegritySha256 = CanonicalJson.Sha256Hex(
                ToCoreProjectionWireValue(),
                NativeCadCanonicalJsonProfiles.Manifest);
        }

        /// <summary>Manifest ID.</summary>
        public string ManifestId { get; private set; }

        /// <summary>
        /// SHA-256 from the already schema-validated full Python manifest's
        /// top-level <c>integrity.sha256</c> field.
        /// </summary>
        public string FullManifestIntegritySha256 { get; private set; }

        /// <summary>One-use generated nonce.</summary>
        public string Nonce { get; private set; }

        /// <summary>Full exact precondition geometry export.</summary>
        public GeometryExportV2 Preconditions { get; private set; }

        /// <summary>Canonical SHA-256 of the preconditions geometry JSON.</summary>
        public string PreconditionsGeometrySha256 { get; private set; }

        /// <summary>Exact prewrite revision tuple.</summary>
        public ExpectedPrewriteRevisionV2 ExpectedPrewriteRevision { get; private set; }

        /// <summary>
        /// Exact private input source required at preflight and inside the
        /// transaction. It is integrity-bound before the console starts.
        /// </summary>
        public NativeSourceBindingV2 ExpectedPrewriteSourceBinding
        {
            get { return ExpectedPrewriteOutputCopyBinding; }
        }

        /// <summary>
        /// Exact pre-write private copy binding. This is not a prediction of
        /// final bytes and must match the current drawing at preflight.
        /// </summary>
        public NativeSourceBindingV2 ExpectedPrewriteOutputCopyBinding
        {
            get;
            private set;
        }

        /// <summary>
        /// Narrow authorization evaluated against the actual retained final
        /// binding after SaveAndReopen.
        /// </summary>
        public FinalOutputConstraintsV2 FinalOutputConstraints
        {
            get;
            private set;
        }

        /// <summary>
        /// Stable execution host digest shared with the Python manifest/audit/
        /// plan projection. Session, PID, database, and revision values are
        /// intentionally not part of this digest.
        /// </summary>
        public string ExpectedStableHostBindingDigest
        {
            get;
            private set;
        }

        /// <summary>Fixed marker policy binding.</summary>
        public MarkerPolicyBindingV2 MarkerPolicy { get; private set; }

        /// <summary>Closed operation list.</summary>
        public IReadOnlyList<ManifestOperationV2> Operations { get; private set; }

        /// <summary>
        /// Internal integrity of the reduced typed projection. It is never
        /// emitted as the wire <c>manifest_integrity_sha256</c> value.
        /// </summary>
        public string CoreProjectionIntegritySha256 { get; private set; }

        /// <summary>Checks every self-consistency invariant before database preflight.</summary>
        public void ValidateSelf()
        {
            NativeIdentifiers.RequireManifestId(ManifestId);
            CanonicalJson.RequireSha256(
                FullManifestIntegritySha256,
                nameof(FullManifestIntegritySha256));
            CanonicalJson.RequireNfcString(Nonce, nameof(Nonce));
            string currentGeometryHash = CanonicalJson.Sha256Hex(Preconditions.ToCanonicalJsonUtf8());
            if (!string.Equals(currentGeometryHash, PreconditionsGeometrySha256, StringComparison.Ordinal))
            {
                throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, "Manifest geometry hash differs.");
            }

            if (!ExpectedPrewriteRevision.Matches(Preconditions))
            {
                throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, "Manifest prewrite tuple differs from preconditions.");
            }
            if (!ExpectedPrewriteOutputCopyBinding.ExactlyMatches(
                    ExpectedPrewriteRevision.Source))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ManifestInvalid,
                    "Manifest prewrite private source differs from prewrite revision.");
            }
            if (!string.Equals(
                    Preconditions.Snapshot.BindingContext.StableExecutionHostBindingDigest(
                        MarkerPolicy),
                    ExpectedStableHostBindingDigest,
                    StringComparison.Ordinal))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ManifestInvalid,
                    "Manifest stable host binding differs from preconditions.");
            }

            string? previous = null;
            HashSet<string> targets = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < Operations.Count; index++)
            {
                ManifestOperationV2 operation = Operations[index];
                NativeIdentifiers.RequireOperationId(operation.OperationId);
                if (previous != null)
                {
                    int comparison = string.CompareOrdinal(previous, operation.OperationId);
                    if (comparison == 0)
                    {
                        throw new CadCoreException(CadCoreErrorCode.DuplicateOperation, "Manifest operation ID is repeated.");
                    }

                    if (comparison > 0)
                    {
                        throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, "Manifest operations are not deterministically ordered.");
                    }
                }

                previous = operation.OperationId;
                string? target = operation.TargetId;
                if (target != null && !targets.Add(target))
                {
                    throw new CadCoreException(CadCoreErrorCode.DuplicateTarget, "Manifest mutable target is repeated.");
                }
            }

            string currentProjectionIntegrity = CanonicalJson.Sha256Hex(
                ToCoreProjectionWireValue(),
                NativeCadCanonicalJsonProfiles.Manifest);
            if (!string.Equals(
                currentProjectionIntegrity,
                CoreProjectionIntegritySha256,
                StringComparison.Ordinal))
            {
                throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, "Core manifest projection integrity differs.");
            }
        }

        /// <summary>
        /// Returns the canonical reduced projection used only for internal
        /// consistency checks. It must not replace full-manifest integrity on
        /// the console-result wire contract.
        /// </summary>
        public Dictionary<string, object?> ToCoreProjectionWireValue()
        {
            List<object?> operations = new List<object?>();
            for (int index = 0; index < Operations.Count; index++)
            {
                operations.Add(Operations[index].ToWireValue());
            }

            string geometryJson = Encoding.UTF8.GetString(Preconditions.ToCanonicalJsonUtf8());
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "schema_version", NativeCadProtocolV2.ManifestSchemaVersion },
                { "manifest_id", ManifestId },
                { "nonce", Nonce },
                { "expected_prewrite_revision", ExpectedPrewriteRevision.ToWireValue() },
                {
                    "expected_prewrite_output_copy_binding",
                    ExpectedPrewriteOutputCopyBinding.ToWireValue()
                },
                {
                    "final_output_constraints",
                    FinalOutputConstraints.ToWireValue()
                },
                {
                    "stable_host_binding_digest",
                    ExpectedStableHostBindingDigest
                },
                { "preconditions_geometry_json", geometryJson },
                { "preconditions_geometry_sha256", PreconditionsGeometrySha256 },
                { "marker_policy_binding", MarkerPolicy.ToWireValue() },
                { "operations", operations },
                { "record_cardinality", NativeCadProtocolV2.PrivateRecordCardinality },
            };
        }
    }

    /// <summary>One deterministic operation result.</summary>
    public sealed class OperationExecutionResultV2
    {
        /// <summary>Creates a successful fixed operation result.</summary>
        public OperationExecutionResultV2(ManifestOperationV2 operation)
        {
            if (operation == null)
            {
                throw new ArgumentNullException(nameof(operation));
            }

            OperationId = operation.OperationId;
            PostconditionDigest = CanonicalJson.Sha256Hex(operation.ToWireValue());
        }

        /// <summary>Operation ID.</summary>
        public string OperationId { get; private set; }

        /// <summary>Exact deterministic postcondition digest.</summary>
        public string PostconditionDigest { get; private set; }

        /// <summary>Returns v1 console-result operation fields.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "operation_id", OperationId },
                { "status", "applied" },
                { "postcondition_digest", PostconditionDigest },
            };
        }
    }

    /// <summary>Frozen final-revision transition result.</summary>
    public enum FinalRevisionTransitionV2
    {
        /// <summary>Generated save/reopen changed the revision.</summary>
        SaveReopenChanged,
    }

    /// <summary>Deterministic successful manifest execution result.</summary>
    public sealed class ManifestExecutionResultV2
    {
        private readonly ManifestExecutor.VerifiedReadbackToken verifiedReadback;

        /// <summary>
        /// Creates a result only from the executor's private verified-readback
        /// token. The token cannot be constructed by a public caller and is
        /// minted only after commit, disposal, save/reopen, and exact final
        /// readback verification.
        /// </summary>
        internal ManifestExecutionResultV2(
            ManifestExecutor.VerifiedReadbackToken verifiedReadback)
        {
            this.verifiedReadback = verifiedReadback ??
                throw new ArgumentNullException(nameof(verifiedReadback));
            CoreManifestV2 manifest = verifiedReadback.Manifest;
            GeometryExportV2 finalExport = verifiedReadback.FinalExport;
            IReadOnlyList<OperationExecutionResultV2> operationResults =
                verifiedReadback.OperationResults;
            if (manifest == null || finalExport == null || operationResults == null)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.TransactionFailure,
                    "Verified execution state is incomplete.");
            }
            manifest.FinalOutputConstraints.RequireActual(
                manifest.ExpectedPrewriteOutputCopyBinding,
                finalExport.Snapshot.Source);

            FinalExport = finalExport;
            List<OperationExecutionResultV2> copied = new List<OperationExecutionResultV2>();
            foreach (OperationExecutionResultV2 result in operationResults)
            {
                copied.Add(result ?? throw new CanonicalJsonException("Operation result may not be null."));
            }

            ManifestId = manifest.ManifestId;
            ManifestIntegritySha256 = manifest.FullManifestIntegritySha256;
            ManifestSchemaVersion = NativeCadProtocolV2.ManifestSchemaVersion;
            Nonce = manifest.Nonce;
            RunId = "native-run-" +
                CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "manifest_id", ManifestId },
                        { "manifest_integrity_sha256", ManifestIntegritySha256 },
                        { "final_revision", finalExport.Document.RevisionFingerprint },
                    }).Substring(0, 32);
            OperationResults = new ReadOnlyCollection<OperationExecutionResultV2>(copied);
            FinalRevisionTransition = FinalRevisionTransitionV2.SaveReopenChanged;
        }

        internal ManifestExecutor.VerifiedReadbackToken VerifiedReadbackToken
        {
            get { return verifiedReadback; }
        }

        /// <summary>Derived run ID.</summary>
        public string RunId { get; }

        /// <summary>Manifest ID.</summary>
        public string ManifestId { get; }

        /// <summary>Validated full private-manifest integrity for the wire contract.</summary>
        public string ManifestIntegritySha256 { get; }

        /// <summary>Exact active manifest namespace bound into the result.</summary>
        public string ManifestSchemaVersion { get; }

        /// <summary>Bound nonce.</summary>
        public string Nonce { get; }

        /// <summary>Exact final generated export.</summary>
        public GeometryExportV2 FinalExport { get; }

        /// <summary>Explicit revision transition.</summary>
        public FinalRevisionTransitionV2 FinalRevisionTransition { get; }

        /// <summary>Applied operations in deterministic manifest order.</summary>
        public IReadOnlyList<OperationExecutionResultV2> OperationResults { get; }

        /// <summary>Returns full native-console-result/v2-shaped success data.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            Dictionary<string, object?> payload = BuildSuccessWireValue(
                RunId,
                ManifestId,
                ManifestIntegritySha256,
                Nonce,
                FinalExport.Document.DatabaseInstanceFingerprint,
                FinalExport.Document.RevisionFingerprint,
                FinalExport.Snapshot.Source,
                OperationResults);
            RequireConsoleResultTransportBudget(payload);
            return payload;
        }

        /// <summary>
        /// Returns canonical success-result bytes after applying the same
        /// budget enforced before a transaction can begin.
        /// </summary>
        public byte[] ToCanonicalJsonUtf8()
        {
            return CanonicalJson.SerializeUtf8(ToWireValue());
        }

        /// <summary>
        /// Calculates the complete canonical result envelope before the first
        /// transaction. Database/revision hashes and run IDs have fixed v1
        /// widths, so placeholder hashes produce the exact byte count of the
        /// later verified result while the manifest supplies all variable
        /// source, nonce, operation-ID, and status fields.
        /// </summary>
        public static int RequirePretransactionTransportBudget(
            CoreManifestV2 manifest)
        {
            if (manifest == null)
            {
                throw new ArgumentNullException(nameof(manifest));
            }

            List<OperationExecutionResultV2> results =
                new List<OperationExecutionResultV2>();
            for (int index = 0; index < manifest.Operations.Count; index++)
            {
                results.Add(new OperationExecutionResultV2(
                    manifest.Operations[index]));
            }

            Dictionary<string, object?> payload = BuildSuccessWireValue(
                "native-run-" + new string('f', 32),
                manifest.ManifestId,
                manifest.FullManifestIntegritySha256,
                manifest.Nonce,
                new string('f', 64),
                new string('e', 64),
                new NativeSourceBindingV2(
                    new string('e', 64),
                    manifest.FinalOutputConstraints.MaxByteSize,
                    manifest.FinalOutputConstraints.AuthorizedPrivatePathFingerprint,
                    new string('d', 64),
                    manifest.FinalOutputConstraints.RequiredDwgHeaderSignature),
                results);
            try
            {
                RequireConsoleResultTransportBudget(payload);
                return CanonicalJson.SerializeUtf8(payload).Length;
            }
            catch (CadCoreException exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ManifestInvalid,
                    "Manifest success result exceeds the fixed transport budget: " +
                    exception.Message);
            }
        }

        private static Dictionary<string, object?> BuildSuccessWireValue(
            string runId,
            string manifestId,
            string manifestIntegritySha256,
            string nonce,
            string databaseInstanceFingerprint,
            string finalRevisionFingerprint,
            NativeSourceBindingV2 outputCopyBinding,
            IReadOnlyList<OperationExecutionResultV2> operationResults)
        {
            List<object?> results = new List<object?>();
            for (int index = 0; index < operationResults.Count; index++)
            {
                results.Add(operationResults[index].ToWireValue());
            }

            Dictionary<string, object?> finalBinding =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "database_instance_fingerprint", databaseInstanceFingerprint },
                    { "revision_fingerprint", finalRevisionFingerprint },
                    { "output_copy_binding", outputCopyBinding.ToWireValue() },
                };
            Dictionary<string, object?> payload =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "schema_version", NativeCadProtocolV2.ConsoleResultSchemaVersion },
                    { "run_id", runId },
                    { "manifest_id", manifestId },
                    { "manifest_integrity_sha256", manifestIntegritySha256 },
                    {
                        "manifest_schema_version",
                        NativeCadProtocolV2.ManifestSchemaVersion
                    },
                    { "nonce", nonce },
                    { "final_revision_fingerprint", finalRevisionFingerprint },
                    { "final_revision_transition", "save_reopen_changed" },
                    { "final_document_binding", finalBinding },
                    {
                        "transaction",
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "preflight", "passed" },
                            { "outcome", "committed" },
                            { "rollback", "not_required" },
                        }
                    },
                    { "operation_results", results },
                };
            payload.Add(
                "integrity",
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "algorithm", "SHA-256" },
                    { "sha256", CanonicalJson.Sha256Hex(payload) },
                });
            return payload;
        }

        private static void RequireConsoleResultTransportBudget(
            Dictionary<string, object?> payload)
        {
            int bytes = CanonicalJson.SerializeUtf8(payload).Length;
            if (!NativeConsoleResultBudgetV2.FitsCanonicalPayloadBytes(bytes))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ManifestInvalid,
                    "Canonical native console result exceeds " +
                    NativeCadProtocolV2.MaxConsoleResultCanonicalBytes.ToString(
                        CultureInfo.InvariantCulture) +
                    " bytes (hard cap " +
                    NativeCadProtocolV2.MaxConsoleResultBytes.ToString(
                        CultureInfo.InvariantCulture) + ").");
            }
        }

        /// <summary>Builds the separate exact post-save console export envelope.</summary>
        public NativeConsoleExportV2 CreateReadbackExport()
        {
            return new NativeConsoleExportV2(this, verifiedReadback);
        }
    }

    /// <summary>
    /// Exact native-console-export/v2-shaped readback envelope. Its geometry
    /// carrier is the canonical generated export itself, never a normalized
    /// surrogate or a path-backed drawing.
    /// </summary>
    public sealed class NativeConsoleExportV2
    {
        /// <summary>
        /// Creates a readback envelope from one executor-verified result. This
        /// is internal so public callers cannot manufacture a success wire
        /// envelope without the result's unforgeable verification token.
        /// </summary>
        internal NativeConsoleExportV2(
            ManifestExecutionResultV2 result,
            ManifestExecutor.VerifiedReadbackToken verifiedReadback)
        {
            if (result == null)
            {
                throw new ArgumentNullException(nameof(result));
            }

            if (verifiedReadback == null ||
                !ReferenceEquals(result.VerifiedReadbackToken, verifiedReadback))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.TransactionFailure,
                    "Console export lacks verified execution state.");
            }

            RunId = result.RunId;
            ManifestId = result.ManifestId;
            ManifestIntegritySha256 = result.ManifestIntegritySha256;
            ManifestSchemaVersion = result.ManifestSchemaVersion;
            Dictionary<string, object?> resultWire = result.ToWireValue();
            object? rawIntegrity;
            if (!resultWire.TryGetValue("integrity", out rawIntegrity) ||
                !(rawIntegrity is Dictionary<string, object?> resultIntegrity) ||
                !resultIntegrity.TryGetValue("sha256", out object? rawSha256) ||
                !(rawSha256 is string resultSha256))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.TransactionFailure,
                    "Console export lacks a result integrity binding.");
            }
            CanonicalJson.RequireSha256(resultSha256, "consoleResultIntegritySha256");
            ConsoleResultIntegritySha256 = resultSha256;
            ConsoleResultSchemaVersion =
                NativeCadProtocolV2.ConsoleResultSchemaVersion;
            Nonce = result.Nonce;
            FinalExport = result.FinalExport;
            GeometryJson = Encoding.UTF8.GetString(FinalExport.ToCanonicalJsonUtf8());
            NativeGeometryJsonV2.RequireCanonicalGeometryCarrier(GeometryJson);
            GeometrySha256 = CanonicalJson.Sha256Hex(FinalExport.ToCanonicalJsonUtf8());
        }

        /// <summary>Bound run ID.</summary>
        public string RunId { get; }

        /// <summary>Bound manifest ID.</summary>
        public string ManifestId { get; }

        /// <summary>Exact manifest self-integrity inherited from the write result.</summary>
        public string ManifestIntegritySha256 { get; }

        /// <summary>Exact manifest namespace inherited from the write result.</summary>
        public string ManifestSchemaVersion { get; }

        /// <summary>Exact accepted write-result self-integrity.</summary>
        public string ConsoleResultIntegritySha256 { get; }

        /// <summary>Exact accepted write-result namespace.</summary>
        public string ConsoleResultSchemaVersion { get; }

        /// <summary>Bound nonce.</summary>
        public string Nonce { get; }

        /// <summary>Exact final export.</summary>
        public GeometryExportV2 FinalExport { get; }

        /// <summary>Opaque outer canonical geometry JSON carrier.</summary>
        public string GeometryJson { get; }

        /// <summary>SHA-256 of exact geometry carrier bytes.</summary>
        public string GeometrySha256 { get; }

        /// <summary>Returns a full native-console-export/v2-shaped object with integrity.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            Dictionary<string, object?> finalBinding = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "database_instance_fingerprint", FinalExport.Document.DatabaseInstanceFingerprint },
                { "revision_fingerprint", FinalExport.Document.RevisionFingerprint },
                { "output_copy_binding", FinalExport.Snapshot.Source.ToWireValue() },
            };
            Dictionary<string, object?> payload = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "schema_version", NativeCadProtocolV2.ConsoleExportSchemaVersion },
                { "run_id", RunId },
                { "manifest_id", ManifestId },
                { "manifest_integrity_sha256", ManifestIntegritySha256 },
                { "manifest_schema_version", ManifestSchemaVersion },
                {
                    "console_result_integrity_sha256",
                    ConsoleResultIntegritySha256
                },
                { "console_result_schema_version", ConsoleResultSchemaVersion },
                { "nonce", Nonce },
                { "final_revision_fingerprint", FinalExport.Document.RevisionFingerprint },
                { "final_document_binding", finalBinding },
                { "geometry_json", GeometryJson },
                { "geometry_sha256", GeometrySha256 },
            };
            payload.Add(
                "integrity",
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "algorithm", "SHA-256" },
                    {
                        "sha256",
                        CanonicalJson.Sha256Hex(
                            payload,
                            NativeCadCanonicalJsonProfiles.ConsoleExport)
                    },
                });
            return payload;
        }

        /// <summary>
        /// Returns the canonical console-export envelope using its exact
        /// outer geometry-carrier profile. Callers must not serialize this
        /// envelope with the ordinary 64 KiB string policy.
        /// </summary>
        public byte[] ToCanonicalJsonUtf8()
        {
            return CanonicalJson.SerializeUtf8(
                ToWireValue(),
                NativeCadCanonicalJsonProfiles.ConsoleExport);
        }
    }

    /// <summary>Executes only preflighted fixed operations in one generated transaction.</summary>
    public sealed class ManifestExecutor
    {
        /// <summary>Applies one manifest or aborts all staged changes on every failure.</summary>
        public ManifestExecutionResultV2 Execute(ICadDatabase database, CoreManifestV2 manifest)
        {
            if (database == null)
            {
                throw new ArgumentNullException(nameof(database));
            }

            if (manifest == null)
            {
                throw new ArgumentNullException(nameof(manifest));
            }

            try
            {
                manifest.ValidateSelf();
                ManifestExecutionResultV2.RequirePretransactionTransportBudget(
                    manifest);
                GeometryExportV2 before = ExactCadExporter.Export(database.ReadSnapshot());
                // Keep preflight outside a write transaction so malformed
                // manifests never obtain one. Its target snapshots are only
                // validation evidence; mutation obtains fresh targets after
                // BeginTransaction below.
                Preflight(before, manifest);
                ExecuteAndDisposeTransaction(database, before, manifest);

                // ExecuteAndDisposeTransaction does not return until the
                // committed transaction has been disposed. A save/reopen call
                // before that boundary is deliberately impossible here.
                ICadDatabase reopenedDatabase;
                try
                {
                    reopenedDatabase = database.SaveAndReopen(
                        manifest.FinalOutputConstraints);
                }
                catch (CadCoreException exception)
                {
                    if (exception.Code == CadCoreErrorCode.SaveFailed ||
                        exception.Code == CadCoreErrorCode.ReopenFailed)
                    {
                        // The commit has already occurred. Do not claim a
                        // rollback of the disposable private in-memory copy.
                        throw;
                    }

                    throw new CadCoreException(
                        CadCoreErrorCode.SaveFailed,
                        "Committed private state was not saved/reopened: " +
                        exception.Message);
                }
                catch (Exception exception)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.SaveFailed,
                        "Committed private state was not saved/reopened: " +
                        exception.Message);
                }

                if (reopenedDatabase == null ||
                    ReferenceEquals(reopenedDatabase, database))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ReopenFailed,
                        "Save/reopen did not return a fresh database.");
                }

                GeometryExportV2 finalExport;
                try
                {
                    finalExport = ExactCadExporter.Export(reopenedDatabase.ReadSnapshot());
                }
                catch (CanonicalJsonException exception)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ReadbackMismatch,
                        exception.Message);
                }

                // This verifies the newly reopened state, including protected
                // state and order, and requires its final revision to differ
                // from the manifest's exact pre-write revision.
                ExactReadbackVerifier.Verify(before, manifest, finalExport, true);
                RequireVerifiedFinalGeometryExport(finalExport);

                List<OperationExecutionResultV2> results = new List<OperationExecutionResultV2>();
                for (int index = 0; index < manifest.Operations.Count; index++)
                {
                    results.Add(new OperationExecutionResultV2(manifest.Operations[index]));
                }

                // This private token is deliberately minted last: only a
                // committed, disposed, saved/reopened, exact-readback state
                // can cross the public result construction boundary.
                VerifiedReadbackToken verifiedReadback =
                    new VerifiedReadbackToken(manifest, finalExport, results);
                return new ManifestExecutionResultV2(verifiedReadback);
            }
            catch (CadCoreException)
            {
                throw;
            }
            catch (CanonicalJsonException exception)
            {
                throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, exception.Message);
            }
            catch (Exception exception)
            {
                throw new CadCoreException(CadCoreErrorCode.TransactionFailure, exception.Message);
            }
        }

        /// <summary>
        /// Completes the post-save boundary before a result token can be
        /// minted. Exact readback proves the transition; this separate pass
        /// proves the complete exported carrier is canonical and satisfies
        /// every v1 schema and semantic invariant.
        /// </summary>
        private static void RequireVerifiedFinalGeometryExport(
            GeometryExportV2 finalExport)
        {
            try
            {
                string geometryJson = Encoding.UTF8.GetString(
                    finalExport.ToCanonicalJsonUtf8());
                NativeGeometryJsonV2.RequireCanonicalGeometryCarrier(geometryJson);
            }
            catch (CadCoreException exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ReadbackMismatch,
                    "Final geometry export is invalid: " + exception.Message);
            }
            catch (CanonicalJsonException exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ReadbackMismatch,
                    "Final geometry export is invalid: " + exception.Message);
            }
        }

        private static void ExecuteAndDisposeTransaction(
            ICadDatabase database,
            GeometryExportV2 before,
            CoreManifestV2 manifest)
        {
            ICadTransaction? transaction = null;
            bool committed = false;
            try
            {
                transaction = database.BeginTransaction();
                if (transaction == null)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.TransactionFailure,
                        "Database returned no transaction.");
                }

                // This is deliberately the first transaction operation. The
                // snapshot is captured inside the transaction consistency
                // boundary, not by reusing the out-of-transaction preflight
                // snapshot. It must equal every exact prewrite field before
                // any mutation is admitted.
                GeometryExportV2 transactionBefore = CaptureTransactionExport(transaction);
                RequireExactPrewriteState(before, transactionBefore, manifest);
                PreflightPlan plan = Preflight(transactionBefore, manifest);
                CadDocumentSnapshot expectedStagedState = transactionBefore.Snapshot;

                ApplyAll(transaction, plan, ref expectedStagedState);
                transaction.PrepareCommit();

                // All mutable postconditions are checked before the one
                // irreversible generated commit. Final revision/result/export
                // data is deliberately deferred until a fresh save/reopen
                // readback has been verified below.
                GeometryExportV2 staged = CaptureTransactionExport(transaction);
                RequireExactStagedState(expectedStagedState, staged);

                ExactReadbackVerifier.Verify(before, manifest, staged, false);
                transaction.CommitExact(expectedStagedState);
                committed = true;
            }
            catch
            {
                if (transaction != null && !committed && transaction.IsActive)
                {
                    // Abort precisely once when staged state remains active.
                    // The finally block always disposes but never performs a
                    // second explicit abort.
                    transaction.Abort();
                }

                throw;
            }
            finally
            {
                if (transaction != null)
                {
                    try
                    {
                        transaction.Dispose();
                    }
                    catch (Exception exception)
                    {
                        string phase = committed
                            ? "Committed private transaction could not be disposed before save/reopen."
                            : "Uncommitted transaction could not be disposed.";
                        throw new CadCoreException(
                            CadCoreErrorCode.TransactionFailure,
                            phase + " " + exception.Message);
                    }
                }
            }
        }

        /// <summary>
        /// Captures and exports current staged state from the transaction
        /// itself. An adapter cannot satisfy this by returning the stale
        /// preflight database snapshot because the caller has already begun
        /// the transaction before reaching this method.
        /// </summary>
        private static GeometryExportV2 CaptureTransactionExport(
            ICadTransaction transaction)
        {
            if (transaction == null)
            {
                throw new ArgumentNullException(nameof(transaction));
            }

            try
            {
                CadDocumentSnapshot snapshot = transaction.CaptureSnapshot();
                if (snapshot == null)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.StalePrecondition,
                        "Transaction did not capture a staged snapshot.");
                }

                return ExactCadExporter.Export(snapshot);
            }
            catch (CadCoreException)
            {
                throw;
            }
            catch (CanonicalJsonException exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Transaction staged state cannot be revalidated: " +
                    exception.Message);
            }
        }

        /// <summary>
        /// Requires byte-for-byte equality of the complete canonical export
        /// captured before and immediately after transaction start. This
        /// covers the revision/prewrite binding, entity/owner/container order,
        /// tables, protected and opaque state, source/binding values, and
        /// every derived document digest; the subsequent Preflight call
        /// rechecks every operation target against manifest preconditions.
        /// </summary>
        private static void RequireExactPrewriteState(
            GeometryExportV2 before,
            GeometryExportV2 transactionBefore,
            CoreManifestV2 manifest)
        {
            if (!ExportsExactlyEqual(before, transactionBefore) ||
                !manifest.ExpectedPrewriteRevision.Matches(transactionBefore))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Transaction staged state differs from exact preflight state.");
            }
        }

        /// <summary>
        /// Requires the current transaction state to equal the exact allowed
        /// prefix transition before another operation or commit may proceed.
        /// This does not rely on a host lock alone: any inter-operation drift
        /// is rejected before the next mutation.
        /// </summary>
        private static void RequireExactStagedState(
            CadDocumentSnapshot expected,
            GeometryExportV2 current)
        {
            GeometryExportV2 expectedExport;
            try
            {
                expectedExport = ExactCadExporter.Export(expected);
            }
            catch (CanonicalJsonException exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Expected staged state cannot be exported: " + exception.Message);
            }

            if (!ExportsExactlyEqual(expectedExport, current))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Transaction staged state differs from its exact allowed prefix.");
            }
        }

        private static bool ExportsExactlyEqual(
            GeometryExportV2 expected,
            GeometryExportV2 observed)
        {
            byte[] expectedJson = expected.ToCanonicalJsonUtf8();
            byte[] observedJson = observed.ToCanonicalJsonUtf8();
            if (expectedJson.Length != observedJson.Length)
            {
                return false;
            }

            for (int index = 0; index < expectedJson.Length; index++)
            {
                if (expectedJson[index] != observedJson[index])
                {
                    return false;
                }
            }

            return true;
        }

        private static PreflightPlan Preflight(GeometryExportV2 before, CoreManifestV2 manifest)
        {
            if (!string.Equals(
                before.ExportDigest,
                manifest.Preconditions.ExportDigest,
                StringComparison.Ordinal) ||
                !manifest.ExpectedPrewriteRevision.Matches(before))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Current generated database does not match manifest preconditions.");
            }

            Dictionary<string, MarkerReservation> markerReservations =
                new Dictionary<string, MarkerReservation>(StringComparer.Ordinal);
            List<CreateReviewMarkerOperationV2> markerOperations =
                new List<CreateReviewMarkerOperationV2>();
            for (int index = 0; index < manifest.Operations.Count; index++)
            {
                ManifestOperationV2 operation = manifest.Operations[index];
                if (operation is TranslateDbTextOperationV2)
                {
                    TranslateDbTextOperationV2 translate = (TranslateDbTextOperationV2)operation;
                    CadEntitySnapshot expected = RequireExactCurrentPrecondition(
                        before,
                        manifest.Preconditions,
                        translate.Target);
                    ValidateTranslate(expected, translate);
                }
                else if (operation is DeleteAuxiliaryOverlayTextOperationV2)
                {
                    DeleteAuxiliaryOverlayTextOperationV2 delete = (DeleteAuxiliaryOverlayTextOperationV2)operation;
                    CadEntitySnapshot expected = RequireExactCurrentPrecondition(
                        before,
                        manifest.Preconditions,
                        delete.Target);
                    ValidateDelete(expected, delete);
                }
                else if (operation is CreateReviewMarkerOperationV2)
                {
                    markerOperations.Add((CreateReviewMarkerOperationV2)operation);
                }
                else
                {
                    throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, "Manifest operation kind is not allowlisted.");
                }
            }

            ReserveMarkers(
                before,
                manifest.MarkerPolicy,
                markerOperations,
                markerReservations);
            return new PreflightPlan(manifest, markerReservations);
        }

        private static CadEntitySnapshot RequireExactCurrentPrecondition(
            GeometryExportV2 current,
            GeometryExportV2 preconditions,
            string targetId)
        {
            CadEntitySnapshot? expected = preconditions.FindByTargetId(targetId);
            CadEntitySnapshot? observed = current.FindByTargetId(targetId);
            if (expected == null || observed == null || !expected.ExactlyEquals(observed))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Manifest target no longer has its exact precondition.");
            }

            return observed;
        }

        private static void ValidateTranslate(
            CadEntitySnapshot target,
            TranslateDbTextOperationV2 operation)
        {
            RequireDirectDbText(target);
            double x = Binary64.ParseBits(operation.Delta.X);
            double y = Binary64.ParseBits(operation.Delta.Y);
            double z = Binary64.ParseBits(operation.Delta.Z);
            if (z != 0d ||
                (x == 0d && y == 0d) ||
                Math.Abs(x) > NativeCadProtocolV2.MaxTranslation ||
                Math.Abs(y) > NativeCadProtocolV2.MaxTranslation)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Translation is outside the fixed finite nonzero XY profile.");
            }

            TranslatedGeometryV2 expected;
            try
            {
                expected = TranslatedGeometryV2.From(target, operation.Delta);
            }
            catch (CanonicalJsonException exception)
            {
                throw new CadCoreException(CadCoreErrorCode.InvalidTarget, exception.Message);
            }

            if (!operation.ExpectedAfter.Matches(
                new CadEntitySnapshot(
                    target.Handle,
                    target.Kind,
                    target.OwnerHandle,
                    target.Container,
                    target.SequenceIndex,
                    target.Layer,
                    target.Text,
                    target.Style,
                    target.HeightBits,
                    target.RotationBits,
                    expected.Position,
                    expected.Bounds,
                    expected.Segments,
                    target.OverlayEvidence)))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Translation expected_after differs from exact binary64 geometry.");
            }
        }

        private static void ValidateDelete(
            CadEntitySnapshot target,
            DeleteAuxiliaryOverlayTextOperationV2 operation)
        {
            RequireDirectDbText(target);
            if (!operation.EligibilityAudited ||
                target.Layer == null ||
                (!string.Equals(target.Layer, "TEMP", StringComparison.OrdinalIgnoreCase) &&
                 !string.Equals(target.Layer, "textarea", StringComparison.OrdinalIgnoreCase)) ||
                !target.OverlayEvidence.IsEligibleOverlay())
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Delete target is not an exact eligible TEMP/textarea overlay DBTEXT.");
            }
        }

        /// <summary>
        /// Allocates each marker's fixed reservation from the immutable
        /// prewrite export. In particular, deletes are deliberately not
        /// allowed to lower the reservation maximum for a later marker.
        /// </summary>
        private static void ReserveMarkers(
            GeometryExportV2 before,
            MarkerPolicyBindingV2 policy,
            IReadOnlyList<CreateReviewMarkerOperationV2> operations,
            IDictionary<string, MarkerReservation> reservations)
        {
            if (operations.Count == 0)
            {
                return;
            }

            if (!policy.IsEnabled ||
                !before.Snapshot.Tables.HasMarkerResources(
                    policy.Layer,
                    policy.Style,
                    policy.LayerFingerprint,
                    policy.StyleFingerprint))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.CapabilityDenied,
                    "Marker policy/capability or pre-existing layer/style gate is closed.");
            }

            CadContainer? directContainer = null;
            int maximumSequence = -1;
            for (int index = 0; index < before.Snapshot.Entities.Count; index++)
            {
                CadEntitySnapshot entity = before.Snapshot.Entities[index];
                if (!entity.Container.IsDirectModelspace)
                {
                    continue;
                }

                if (directContainer == null)
                {
                    directContainer = entity.Container;
                }
                else if (!directContainer.Equals(entity.Container))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ManifestInvalid,
                        "Direct Modelspace marker destination is ambiguous.");
                }

                maximumSequence = Math.Max(maximumSequence, entity.SequenceIndex);
            }

            if (directContainer == null)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ManifestInvalid,
                    "No direct Modelspace container exists for marker append.");
            }

            ulong nextHandle = NextGeneratedHandle(before.Snapshot.Entities);
            HashSet<int> reservedSequences = new HashSet<int>();
            for (int index = 0; index < operations.Count; index++)
            {
                CreateReviewMarkerOperationV2 operation = operations[index];
                if (!operation.Container.IsDirectModelspace ||
                    !operation.Container.Equals(directContainer) ||
                    operation.SequenceIndex != maximumSequence + index + 1 ||
                    !IsDeclaredOwner(before.Snapshot.Owners, operation.OwnerHandle) ||
                    !string.Equals(operation.OwnerHandle, OwnerFor(directContainer, before), StringComparison.Ordinal) ||
                    !string.Equals(operation.MarkerText, policy.DeriveMarkerText(operation.OperationId), StringComparison.Ordinal) ||
                    !string.Equals(operation.Layer, policy.Layer, StringComparison.Ordinal) ||
                    !string.Equals(operation.Style, policy.Style, StringComparison.Ordinal) ||
                    !string.Equals(operation.HeightBits, policy.HeightBits, StringComparison.Ordinal) ||
                    !string.Equals(operation.RotationBits, policy.RotationBits, StringComparison.Ordinal) ||
                    !operation.OverlayEvidence.Equals(policy.DefaultOverlayEvidence) ||
                    !reservedSequences.Add(operation.SequenceIndex) ||
                    !string.Equals(
                        operation.MarkerFingerprint,
                        CreateReviewMarkerOperationV2.ComputeMarkerFingerprint(operation),
                        StringComparison.Ordinal))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ManifestInvalid,
                        "Marker fields differ from fixed policy-derived append requirements.");
                }

                if (operation.SequenceIndex > 1000000 || nextHandle == ulong.MaxValue)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ManifestInvalid,
                        "Marker append cannot allocate a canonical sequence/handle.");
                }

                reservations.Add(
                    operation.OperationId,
                    new MarkerReservation(
                        operation.OperationId,
                        nextHandle.ToString("X", CultureInfo.InvariantCulture),
                        operation.OwnerHandle,
                        operation.Container,
                        operation.SequenceIndex));
                nextHandle++;
            }
        }

        private static string OwnerFor(CadContainer container, GeometryExportV2 before)
        {
            string? owner = null;
            for (int index = 0; index < before.Snapshot.Entities.Count; index++)
            {
                CadEntitySnapshot entity = before.Snapshot.Entities[index];
                if (entity.Container.Equals(container))
                {
                    if (owner == null)
                    {
                        owner = entity.OwnerHandle;
                    }
                    else if (!string.Equals(owner, entity.OwnerHandle, StringComparison.Ordinal))
                    {
                        throw new CadCoreException(
                            CadCoreErrorCode.ManifestInvalid,
                            "Marker container owner is ambiguous.");
                    }
                }
            }

            if (owner == null)
            {
                throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, "Marker container owner is absent.");
            }

            if (!IsDeclaredOwner(before.Snapshot.Owners, owner))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ManifestInvalid,
                    "Marker container owner is not a pre-existing declared owner.");
            }

            return owner;
        }

        private static bool IsDeclaredOwner(IReadOnlyList<string> owners, string owner)
        {
            for (int index = 0; index < owners.Count; index++)
            {
                if (string.Equals(owners[index], owner, StringComparison.Ordinal))
                {
                    return true;
                }
            }

            return false;
        }

        private static ulong NextGeneratedHandle(IReadOnlyList<CadEntitySnapshot> entities)
        {
            ulong maximum = 0;
            for (int index = 0; index < entities.Count; index++)
            {
                ulong parsed;
                if (!ulong.TryParse(
                    entities[index].Handle,
                    NumberStyles.AllowHexSpecifier,
                    CultureInfo.InvariantCulture,
                    out parsed))
                {
                    throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, "Existing handle is not canonical.");
                }

                maximum = Math.Max(maximum, parsed);
            }

            if (maximum == ulong.MaxValue)
            {
                throw new CadCoreException(CadCoreErrorCode.ManifestInvalid, "No canonical generated handle remains.");
            }

            return maximum + 1;
        }

        private static void RequireDirectDbText(CadEntitySnapshot target)
        {
            if (target.Kind != NativeEntityKind.DbText ||
                !target.Container.IsDirectModelspace ||
                target.Layer == null ||
                target.Text == null ||
                target.Style == null)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Operation target must be direct Modelspace DBTEXT with exact fields.");
            }
        }

        private static void ApplyAll(
            ICadTransaction transaction,
            PreflightPlan plan,
            ref CadDocumentSnapshot expectedStagedState)
        {
            for (int index = 0; index < plan.Manifest.Operations.Count; index++)
            {
                ManifestOperationV2 operation = plan.Manifest.Operations[index];
                // Do not rely on either the out-of-transaction preflight
                // objects or an earlier per-operation capture. Every
                // operation starts by exporting the current staged state and
                // proving it equals the exact permitted prefix transition.
                GeometryExportV2 current = CaptureTransactionExport(transaction);
                RequireExactStagedState(expectedStagedState, current);
                if (operation is TranslateDbTextOperationV2)
                {
                    TranslateDbTextOperationV2 translate = (TranslateDbTextOperationV2)operation;
                    CadEntitySnapshot target = RequireExactCurrentPrecondition(
                        current,
                        plan.Manifest.Preconditions,
                        translate.Target);
                    ValidateTranslate(target, translate);
                    CadEntitySnapshot replacement = target.Translate(translate.Delta);
                    // ReplaceExact rechecks both the complete fresh staged
                    // snapshot and this freshly resolved target after its
                    // host-side BeforeMutation hook, closing the last
                    // compare-to-write interval.
                    transaction.ReplaceExact(
                        current.Snapshot,
                        target,
                        replacement);
                    expectedStagedState = ReplaceExpected(
                        expectedStagedState,
                        replacement);
                }
                else if (operation is DeleteAuxiliaryOverlayTextOperationV2)
                {
                    DeleteAuxiliaryOverlayTextOperationV2 delete =
                        (DeleteAuxiliaryOverlayTextOperationV2)operation;
                    CadEntitySnapshot target = RequireExactCurrentPrecondition(
                        current,
                        plan.Manifest.Preconditions,
                        delete.Target);
                    ValidateDelete(target, delete);
                    transaction.EraseExact(current.Snapshot, target);
                    expectedStagedState = EraseExpected(
                        expectedStagedState,
                        target.Handle);
                }
                else if (operation is CreateReviewMarkerOperationV2)
                {
                    CreateReviewMarkerOperationV2 marker =
                        (CreateReviewMarkerOperationV2)operation;
                    RequireCurrentMarkerPrecondition(current, plan, marker);
                    CadEntitySnapshot markerEntity = CreateMarkerEntity(
                        plan.MarkerReservations[marker.OperationId].Handle,
                        marker);
                    transaction.AppendExact(current.Snapshot, markerEntity);
                    expectedStagedState = AppendExpected(
                        expectedStagedState,
                        markerEntity);
                }
                else
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ManifestInvalid,
                        "Operation is not allowlisted.");
                }
            }
        }

        /// <summary>
        /// Rechecks a marker against its original reservation and current
        /// non-marker state. No mutable staged maximum participates here:
        /// deleted original slots remain gaps and prior markers retain their
        /// distinct original reservations.
        /// </summary>
        private static void RequireCurrentMarkerPrecondition(
            GeometryExportV2 current,
            PreflightPlan plan,
            CreateReviewMarkerOperationV2 marker)
        {
            MarkerReservation? reservation;
            if (!plan.MarkerReservations.TryGetValue(
                    marker.OperationId,
                    out reservation) ||
                reservation == null ||
                !string.Equals(reservation.OwnerHandle, marker.OwnerHandle, StringComparison.Ordinal) ||
                !reservation.Container.Equals(marker.Container) ||
                reservation.SequenceIndex != marker.SequenceIndex)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Marker reservation no longer matches the original plan.");
            }

            if (!plan.Manifest.MarkerPolicy.IsEnabled ||
                !current.Snapshot.Tables.HasMarkerResources(
                    plan.Manifest.MarkerPolicy.Layer,
                    plan.Manifest.MarkerPolicy.Style,
                    plan.Manifest.MarkerPolicy.LayerFingerprint,
                    plan.Manifest.MarkerPolicy.StyleFingerprint))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Marker policy resources changed after reservation.");
            }

            for (int index = 0; index < current.Snapshot.Entities.Count; index++)
            {
                CadEntitySnapshot entity = current.Snapshot.Entities[index];
                if (string.Equals(entity.Handle, reservation.Handle, StringComparison.Ordinal) ||
                    (entity.Container.Equals(reservation.Container) &&
                     entity.SequenceIndex == reservation.SequenceIndex))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.StalePrecondition,
                        "Marker reserved handle or sequence slot is occupied.");
                }

                if (entity.Container.IsDirectModelspace &&
                    (!entity.Container.Equals(reservation.Container) ||
                     !string.Equals(entity.OwnerHandle, reservation.OwnerHandle, StringComparison.Ordinal)))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.StalePrecondition,
                        "Current direct Modelspace no longer matches marker reservation.");
                }
            }
        }

        private static CadEntitySnapshot CreateMarkerEntity(
            string handle,
            CreateReviewMarkerOperationV2 marker)
        {
            return new CadEntitySnapshot(
                handle,
                NativeEntityKind.DbText,
                marker.OwnerHandle,
                marker.Container,
                marker.SequenceIndex,
                marker.Layer,
                marker.MarkerText,
                marker.Style,
                marker.HeightBits,
                marker.RotationBits,
                marker.Position,
                new CadBounds(marker.Position, marker.Position),
                new CadSegment[0],
                marker.OverlayEvidence);
        }

        // These pure expected-prefix helpers intentionally operate on a
        // private model copy. They never supply objects to a live transaction;
        // actual mutations above always use targets freshly captured from it.
        private static CadDocumentSnapshot ReplaceExpected(
            CadDocumentSnapshot expected,
            CadEntitySnapshot replacement)
        {
            MutableCadDocument model = new MutableCadDocument(expected);
            model.Replace(replacement);
            return model.ToSnapshot();
        }

        private static CadDocumentSnapshot EraseExpected(
            CadDocumentSnapshot expected,
            string handle)
        {
            MutableCadDocument model = new MutableCadDocument(expected);
            model.Erase(handle);
            return model.ToSnapshot();
        }

        private static CadDocumentSnapshot AppendExpected(
            CadDocumentSnapshot expected,
            CadEntitySnapshot addition)
        {
            MutableCadDocument model = new MutableCadDocument(expected);
            model.Append(addition);
            return model.ToSnapshot();
        }

        private sealed class PreflightPlan
        {
            internal PreflightPlan(
                CoreManifestV2 manifest,
                IDictionary<string, MarkerReservation> markerReservations)
            {
                Manifest = manifest;
                MarkerReservations =
                    new ReadOnlyDictionary<string, MarkerReservation>(
                        new Dictionary<string, MarkerReservation>(
                            markerReservations,
                            StringComparer.Ordinal));
            }

            internal CoreManifestV2 Manifest { get; private set; }

            internal IReadOnlyDictionary<string, MarkerReservation> MarkerReservations
            {
                get;
                private set;
            }
        }

        /// <summary>
        /// Immutable original-plan marker allocation. The explicit sequence
        /// slot is an insertion index, not a recalculated append position.
        /// </summary>
        private sealed class MarkerReservation
        {
            internal MarkerReservation(
                string operationId,
                string handle,
                string ownerHandle,
                CadContainer container,
                int sequenceIndex)
            {
                OperationId = operationId;
                Handle = handle;
                OwnerHandle = ownerHandle;
                Container = container;
                SequenceIndex = sequenceIndex;
            }

            internal string OperationId { get; private set; }

            internal string Handle { get; private set; }

            internal string OwnerHandle { get; private set; }

            internal CadContainer Container { get; private set; }

            internal int SequenceIndex { get; private set; }
        }

        /// <summary>
        /// Capability token for constructing a committed result. Its private
        /// result construction path is exercised only by
        /// <see cref="ManifestExecutor"/>. The type and constructor are
        /// internal, so callers outside this assembly cannot create either a
        /// success result or its console wire envelope without running
        /// <see cref="Execute"/>.
        /// </summary>
        internal sealed class VerifiedReadbackToken
        {
            internal VerifiedReadbackToken(
                CoreManifestV2 manifest,
                GeometryExportV2 finalExport,
                IEnumerable<OperationExecutionResultV2> operationResults)
            {
                Manifest = manifest ?? throw new ArgumentNullException(nameof(manifest));
                FinalExport = finalExport ?? throw new ArgumentNullException(nameof(finalExport));
                if (operationResults == null)
                {
                    throw new ArgumentNullException(nameof(operationResults));
                }

                List<OperationExecutionResultV2> copied =
                    new List<OperationExecutionResultV2>();
                foreach (OperationExecutionResultV2 result in operationResults)
                {
                    copied.Add(result ?? throw new CanonicalJsonException(
                        "Verified operation result may not be null."));
                }

                OperationResults =
                    new ReadOnlyCollection<OperationExecutionResultV2>(copied);
            }

            internal CoreManifestV2 Manifest { get; private set; }

            internal GeometryExportV2 FinalExport { get; private set; }

            internal IReadOnlyList<OperationExecutionResultV2> OperationResults
            {
                get;
                private set;
            }
        }
    }

    internal static class NativeIdentifiers
    {
        internal static void RequireManifestId(string value)
        {
            Require(value, "native-manifest-", 32, "manifest");
        }

        internal static void RequireOperationId(string value)
        {
            Require(value, "native-operation-", 24, "operation");
        }

        internal static void RequireTargetId(string value)
        {
            Require(value, "native-target-", 24, "target");
        }

        private static void Require(string value, string prefix, int suffixLength, string label)
        {
            CanonicalJson.RequireNfcString(value, label);
            if (!value.StartsWith(prefix, StringComparison.Ordinal) ||
                value.Length != prefix.Length + suffixLength)
            {
                throw new CanonicalJsonException("Native " + label + " ID is invalid.");
            }

            for (int index = prefix.Length; index < value.Length; index++)
            {
                char character = value[index];
                if (!((character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f')))
                {
                    throw new CanonicalJsonException("Native " + label + " ID is invalid.");
                }
            }
        }
    }
}
