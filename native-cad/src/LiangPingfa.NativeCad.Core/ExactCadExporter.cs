// SPDX-License-Identifier: MIT
// Exact generated export and digest projection for the vendor-neutral model.

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.Core
{
    /// <summary>All v1 document digest fields derived from one immutable snapshot.</summary>
    public sealed class GeometryDocumentDigestsV2
    {
        /// <summary>Creates an immutable document digest projection.</summary>
        public GeometryDocumentDigestsV2(
            string databaseInstanceFingerprint,
            string revisionFingerprint,
            string orderedEntityDigest,
            string containerOrderDigest,
            string completeGeometryDigest,
            string protectedStateDigest,
            string protectedOrderDigest,
            string tableStateDigest,
            string layoutStateDigest,
            string blockStateDigest,
            string documentStateDigest,
            string? markerLayerFingerprint,
            string? markerStyleFingerprint)
        {
            CanonicalJson.RequireSha256(databaseInstanceFingerprint, nameof(databaseInstanceFingerprint));
            CanonicalJson.RequireSha256(revisionFingerprint, nameof(revisionFingerprint));
            CanonicalJson.RequireSha256(orderedEntityDigest, nameof(orderedEntityDigest));
            CanonicalJson.RequireSha256(containerOrderDigest, nameof(containerOrderDigest));
            CanonicalJson.RequireSha256(completeGeometryDigest, nameof(completeGeometryDigest));
            CanonicalJson.RequireSha256(protectedStateDigest, nameof(protectedStateDigest));
            CanonicalJson.RequireSha256(protectedOrderDigest, nameof(protectedOrderDigest));
            CanonicalJson.RequireSha256(tableStateDigest, nameof(tableStateDigest));
            CanonicalJson.RequireSha256(layoutStateDigest, nameof(layoutStateDigest));
            CanonicalJson.RequireSha256(blockStateDigest, nameof(blockStateDigest));
            CanonicalJson.RequireSha256(documentStateDigest, nameof(documentStateDigest));
            if (markerLayerFingerprint != null)
            {
                CanonicalJson.RequireSha256(markerLayerFingerprint, nameof(markerLayerFingerprint));
            }

            if (markerStyleFingerprint != null)
            {
                CanonicalJson.RequireSha256(markerStyleFingerprint, nameof(markerStyleFingerprint));
            }

            DatabaseInstanceFingerprint = databaseInstanceFingerprint;
            RevisionFingerprint = revisionFingerprint;
            OrderedEntityDigest = orderedEntityDigest;
            ContainerOrderDigest = containerOrderDigest;
            CompleteGeometryDigest = completeGeometryDigest;
            ProtectedStateDigest = protectedStateDigest;
            ProtectedOrderDigest = protectedOrderDigest;
            TableStateDigest = tableStateDigest;
            LayoutStateDigest = layoutStateDigest;
            BlockStateDigest = blockStateDigest;
            DocumentStateDigest = documentStateDigest;
            MarkerLayerFingerprint = markerLayerFingerprint;
            MarkerStyleFingerprint = markerStyleFingerprint;
        }

        /// <summary>Generated database instance fingerprint.</summary>
        public string DatabaseInstanceFingerprint { get; private set; }

        /// <summary>Generated revision fingerprint.</summary>
        public string RevisionFingerprint { get; private set; }

        /// <summary>Digest of exact globally ordered records.</summary>
        public string OrderedEntityDigest { get; private set; }

        /// <summary>Digest of exact per-container sequences.</summary>
        public string ContainerOrderDigest { get; private set; }

        /// <summary>Digest of all exported geometry projections.</summary>
        public string CompleteGeometryDigest { get; private set; }

        /// <summary>Digest of modeled protected state.</summary>
        public string ProtectedStateDigest { get; private set; }

        /// <summary>Digest of modeled protected ordering state.</summary>
        public string ProtectedOrderDigest { get; private set; }

        /// <summary>Opaque table digest.</summary>
        public string TableStateDigest { get; private set; }

        /// <summary>Opaque layout digest.</summary>
        public string LayoutStateDigest { get; private set; }

        /// <summary>Opaque block digest.</summary>
        public string BlockStateDigest { get; private set; }

        /// <summary>Digest of the protected table/layout/block marker tuple.</summary>
        public string DocumentStateDigest { get; private set; }

        /// <summary>Marker layer fingerprint.</summary>
        public string? MarkerLayerFingerprint { get; private set; }

        /// <summary>Marker style fingerprint.</summary>
        public string? MarkerStyleFingerprint { get; private set; }

        /// <summary>Returns all current native-geometry-export document field names.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "database_instance_fingerprint", DatabaseInstanceFingerprint },
                { "revision_fingerprint", RevisionFingerprint },
                { "ordered_entity_digest", OrderedEntityDigest },
                { "container_order_digest", ContainerOrderDigest },
                { "complete_geometry_digest", CompleteGeometryDigest },
                { "protected_state_digest", ProtectedStateDigest },
                { "protected_order_digest", ProtectedOrderDigest },
                { "table_state_digest", TableStateDigest },
                { "layout_state_digest", LayoutStateDigest },
                { "block_state_digest", BlockStateDigest },
                { "document_state_digest", DocumentStateDigest },
                { "marker_layer_fingerprint", MarkerLayerFingerprint },
                { "marker_style_fingerprint", MarkerStyleFingerprint },
            };
        }

        /// <summary>Returns exact prewrite fields independently rechecked by the executor.</summary>
        public Dictionary<string, object?> ToPrewriteWireValue(NativeSourceBindingV2 source)
        {
            if (source == null)
            {
                throw new ArgumentNullException(nameof(source));
            }

            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "source_binding", source.ToWireValue() },
                { "document_path_fingerprint", source.PathFingerprint },
                { "document_file_identity_fingerprint", source.FileIdentityFingerprint },
                { "document_content_sha256", source.Sha256 },
                { "document_byte_size", source.ByteSize },
                { "database_instance_fingerprint", DatabaseInstanceFingerprint },
                { "revision_fingerprint", RevisionFingerprint },
                { "geometry_digest", CompleteGeometryDigest },
                { "protected_state_digest", ProtectedStateDigest },
                { "protected_order_digest", ProtectedOrderDigest },
                { "document_state_digest", DocumentStateDigest },
            };
        }
    }

    /// <summary>Full generated v1-shaped exact geometry export.</summary>
    public sealed class GeometryExportV2
    {
        internal GeometryExportV2(
            CadDocumentSnapshot snapshot,
            GeometryDocumentDigestsV2 document,
            IReadOnlyList<object?> containerSequences)
        {
            Snapshot = snapshot ?? throw new ArgumentNullException(nameof(snapshot));
            Document = document ?? throw new ArgumentNullException(nameof(document));
            ContainerSequences = containerSequences ?? throw new ArgumentNullException(nameof(containerSequences));
            PortablePrewriteProjection =
                PortablePrewriteProjectionV2.From(this);
        }

        /// <summary>Underlying immutable snapshot.</summary>
        public CadDocumentSnapshot Snapshot { get; private set; }

        /// <summary>Exact derived document digest fields.</summary>
        public GeometryDocumentDigestsV2 Document { get; private set; }

        /// <summary>Exact container sequences retained for readback verification.</summary>
        public IReadOnlyList<object?> ContainerSequences { get; private set; }

        /// <summary>
        /// Source-to-private-copy portable semantic/protected projection
        /// emitted by full-host bridge exports and compared by Core Console.
        /// </summary>
        public PortablePrewriteProjectionV2 PortablePrewriteProjection
        {
            get;
            private set;
        }

        /// <summary>Returns a matching entity by handle.</summary>
        public CadEntitySnapshot? FindByHandle(string handle)
        {
            return Snapshot.FindByHandle(handle);
        }

        /// <summary>Returns an entity by durable opaque target ID.</summary>
        public CadEntitySnapshot? FindByTargetId(string targetId)
        {
            for (int index = 0; index < Snapshot.Entities.Count; index++)
            {
                CadEntitySnapshot entity = Snapshot.Entities[index];
                if (string.Equals(entity.TargetId, targetId, StringComparison.Ordinal))
                {
                    return entity;
                }
            }

            return null;
        }

        /// <summary>Returns the full v1 schema-shaped object including integrity.</summary>
        public Dictionary<string, object?> ToWireValue()
        {
            List<object?> owners = new List<object?>();
            for (int index = 0; index < Snapshot.Owners.Count; index++)
            {
                owners.Add(Snapshot.Owners[index]);
            }

            List<object?> entities = new List<object?>();
            for (int index = 0; index < Snapshot.Entities.Count; index++)
            {
                CadEntitySnapshot entity = Snapshot.Entities[index];
                Dictionary<string, object?> record = entity.ToWireValue();
                record.Add("geometry_fingerprint", entity.GeometryFingerprint);
                record.Add("opaque_state_digest", entity.OpaqueStateDigest);
                entities.Add(record);
            }

            List<object?> containers = new List<object?>();
            for (int index = 0; index < Snapshot.Containers.Count; index++)
            {
                containers.Add(Snapshot.Containers[index].ToWireValue());
            }

            Dictionary<string, object?> payload = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "schema_version", NativeCadProtocolV2.GeometrySchemaVersion },
                { "source", Snapshot.Source.ToWireValue() },
                {
                    "binding",
                    Snapshot.BindingContext.ToWireValue(
                        Snapshot.Source,
                        Document.ToWireValue(),
                        containers)
                },
                { "document", Document.ToWireValue() },
                { "owners", owners },
                { "containers", containers },
                { "entities", entities },
                {
                    "portable_prewrite_projection",
                    PortablePrewriteProjection.ToWireValue()
                },
                {
                    "portable_prewrite_projection_digest",
                    PortablePrewriteProjection.Digest
                },
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

        /// <summary>Returns exact canonical UTF-8 JSON, bounded before publication.</summary>
        public byte[] ToCanonicalJsonUtf8()
        {
            byte[] json = CanonicalJson.SerializeUtf8(ToWireValue());
            if (json.Length > NativeCadProtocolV2.MaxGeometryJsonBytes)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ManifestInvalid,
                    "Generated exact geometry exceeds the frozen UTF-8 byte limit.");
            }

            return json;
        }

        /// <summary>Returns a deterministic whole-export digest for strict precondition equality.</summary>
        public string ExportDigest
        {
            get
            {
                return CanonicalJson.Sha256Hex(ToCanonicalJsonUtf8());
            }
        }
    }

    /// <summary>Exports every field and digest from the immutable generated model.</summary>
    public static class ExactCadExporter
    {
        /// <summary>Creates a full exact export without reading an external database.</summary>
        public static GeometryExportV2 Export(CadDocumentSnapshot snapshot)
        {
            if (snapshot == null)
            {
                throw new ArgumentNullException(nameof(snapshot));
            }

            RequireV2ContainerBounds(snapshot);
            List<object?> orderedRecords = new List<object?>();
            List<object?> geometryRecords = new List<object?>();
            List<object?> opaqueStateDigests = new List<object?>();
            List<object?> owners = new List<object?>();
            List<object?> containers = BuildContainerRecords(snapshot.Containers);
            for (int index = 0; index < snapshot.Owners.Count; index++)
            {
                owners.Add(snapshot.Owners[index]);
            }

            for (int index = 0; index < snapshot.Entities.Count; index++)
            {
                CadEntitySnapshot entity = snapshot.Entities[index];
                orderedRecords.Add(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "container", entity.Container.ToKeyWireValue() },
                        { "sequence_index", (long)entity.SequenceIndex },
                        { "handle", entity.Handle },
                        { "geometry_fingerprint", entity.GeometryFingerprint },
                        { "opaque_state_digest", entity.OpaqueStateDigest },
                    });
                geometryRecords.Add(entity.ToWireValue());
                opaqueStateDigests.Add(entity.OpaqueStateDigest);
            }

            IReadOnlyList<object?> containerSequences = BuildContainerSequences(
                snapshot.Containers,
                snapshot.Entities);
            Dictionary<string, object?> documentState = snapshot.Tables.ToStateWireValue();
            string documentStateDigest = CanonicalJson.Sha256Hex(documentState);
            string orderedEntityDigest = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "containers", containers },
                    { "entities", orderedRecords },
                });
            string containerOrderDigest = CanonicalJson.Sha256Hex(containerSequences);
            string completeGeometryDigest = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "containers", containers },
                    { "entities", geometryRecords },
                });
            string protectedStateDigest = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "document_state_digest", documentStateDigest },
                    // Owners are protected even when no entity currently uses
                    // one. Their canonical sequence is host state, not an
                    // operation-owned resource.
                    { "owners", owners },
                    { "containers", containers },
                    { "opaque_state_digests", opaqueStateDigests },
                });
            string protectedOrderDigest = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "container_sequences", containerSequences },
                    { "document_state_digest", documentStateDigest },
                    { "owners", owners },
                });

            GeometryDocumentDigestsV2 document = new GeometryDocumentDigestsV2(
                snapshot.DatabaseInstanceFingerprint,
                snapshot.RevisionFingerprint,
                orderedEntityDigest,
                containerOrderDigest,
                completeGeometryDigest,
                protectedStateDigest,
                protectedOrderDigest,
                snapshot.Tables.TableStateDigest,
                snapshot.Tables.LayoutStateDigest,
                snapshot.Tables.BlockStateDigest,
                documentStateDigest,
                snapshot.Tables.MarkerLayerFingerprint,
                snapshot.Tables.MarkerStyleFingerprint);
            return new GeometryExportV2(snapshot, document, containerSequences);
        }

        private static void RequireV2ContainerBounds(CadDocumentSnapshot snapshot)
        {
            if (snapshot.Owners.Count > NativeCadProtocolV2.MaxGeometryContainers ||
                snapshot.Containers.Count > NativeCadProtocolV2.MaxGeometryContainers)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ManifestInvalid,
                    "Snapshot exceeds the v2 geometry container limit.");
            }

            HashSet<string> owners = new HashSet<string>(StringComparer.Ordinal);
            HashSet<string> containers = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < snapshot.Owners.Count; index++)
            {
                if (!owners.Add(snapshot.Owners[index]))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ManifestInvalid,
                        "Snapshot has duplicate owners.");
                }
            }

            for (int index = 0; index < snapshot.Containers.Count; index++)
            {
                CadContainerPhysicalSlots container = snapshot.Containers[index];
                if (!owners.Contains(container.OwnerHandle) ||
                    !containers.Add(container.Container.SortKey))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ManifestInvalid,
                        "Snapshot has an invalid owner/container mapping.");
                }
            }
        }

        private static List<object?> BuildContainerRecords(
            IReadOnlyList<CadContainerPhysicalSlots> containers)
        {
            List<object?> result = new List<object?>();
            for (int index = 0; index < containers.Count; index++)
            {
                result.Add(containers[index].ToWireValue());
            }

            return result;
        }

        private static IReadOnlyList<object?> BuildContainerSequences(
            IReadOnlyList<CadContainerPhysicalSlots> containers,
            IReadOnlyList<CadEntitySnapshot> entities)
        {
            SortedDictionary<string, List<CadEntitySnapshot>> grouped =
                new SortedDictionary<string, List<CadEntitySnapshot>>(StringComparer.Ordinal);
            for (int index = 0; index < entities.Count; index++)
            {
                CadEntitySnapshot entity = entities[index];
                List<CadEntitySnapshot>? records;
                if (!grouped.TryGetValue(entity.Container.SortKey, out records))
                {
                    records = new List<CadEntitySnapshot>();
                    grouped.Add(entity.Container.SortKey, records);
                }

                records.Add(entity);
            }

            List<object?> result = new List<object?>();
            for (int containerIndex = 0; containerIndex < containers.Count; containerIndex++)
            {
                CadContainerPhysicalSlots container = containers[containerIndex];
                List<CadEntitySnapshot>? records;
                if (!grouped.TryGetValue(container.Container.SortKey, out records))
                {
                    records = new List<CadEntitySnapshot>();
                }

                List<object?> projected = new List<object?>();
                for (int index = 0; index < records.Count; index++)
                {
                    CadEntitySnapshot entity = records[index];
                    projected.Add(
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "geometry_fingerprint", entity.GeometryFingerprint },
                            { "handle", entity.Handle },
                            { "opaque_state_digest", entity.OpaqueStateDigest },
                            { "sequence_index", (long)entity.SequenceIndex },
                        });
                }

                result.Add(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "container", container.Container.ToKeyWireValue() },
                        { "owner_handle", container.OwnerHandle },
                        { "physical_slot_count", (long)container.PhysicalSlotCount },
                        { "entities", projected },
                    });
            }

            return new ReadOnlyCollection<object?>(result);
        }
    }
}
