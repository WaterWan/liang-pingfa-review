// SPDX-License-Identifier: MIT
// Independent validation for JSON embedded in approved native-v1 carriers.

using System;
using System.Collections.Generic;
using System.Text;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.Core
{
    /// <summary>
    /// Validates the inner geometry document independently of its outer
    /// opaque-carrier policy. The outer profile preserves carrier code points;
    /// this boundary then requires ordinary strict canonical geometry JSON.
    /// </summary>
    public static class NativeGeometryJsonV2
    {
        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);

        /// <summary>
        /// Validates the source values shared by the typed producer and the
        /// JSON-carrier consumer. Keeping this boundary common prevents a
        /// producer from minting a source which its own readback rejects.
        /// </summary>
        internal static void ValidateSourceBindingFields(
            string sha256,
            long byteSize,
            string pathFingerprint,
            string fileIdentityFingerprint,
            string dwgHeaderSignature)
        {
            CanonicalJson.RequireSha256(sha256, nameof(sha256));
            CanonicalJson.RequireSha256(pathFingerprint, nameof(pathFingerprint));
            CanonicalJson.RequireSha256(fileIdentityFingerprint, nameof(fileIdentityFingerprint));
            if (byteSize < 0)
            {
                throw new CanonicalJsonException("Embedded geometry source byte size is invalid.");
            }

            CanonicalJson.RequireNfcString(dwgHeaderSignature, nameof(dwgHeaderSignature));
            if (dwgHeaderSignature.Length != 6 ||
                !dwgHeaderSignature.StartsWith("AC", StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry DWG header signature is invalid.");
            }

            for (int index = 2; index < dwgHeaderSignature.Length; index++)
            {
                char value = dwgHeaderSignature[index];
                if (!((value >= '0' && value <= '9') ||
                    (value >= 'A' && value <= 'Z')))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry DWG header signature is invalid.");
                }
            }
        }

        /// <summary>
        /// Validates the caller-controlled binding fields shared by typed
        /// exports and embedded geometry. Host/process/protocol constants and
        /// document/source values are then checked in the complete canonical
        /// export before an execution result may exist.
        /// </summary>
        internal static void ValidateBindingContextFields(
            string sessionId,
            string adapterId,
            string adapterProfile,
            string adapterVersion,
            string pluginId,
            string pluginVersion,
            string pluginFingerprint,
            IReadOnlyList<string> capabilities)
        {
            RequirePrefixedLowerHex(sessionId, "native-session-", 32, "session ID");
            RequireIdentifier(adapterId, 1, 96);
            RequireIdentifier(adapterProfile, 1, 96);
            RequireVersionToken(adapterVersion, 1, 64);
            RequireIdentifier(pluginId, 1, 96);
            RequireVersionToken(pluginVersion, 1, 64);
            CanonicalJson.RequireSha256(pluginFingerprint, nameof(pluginFingerprint));
            ValidateCapabilities(capabilities);
        }

        /// <summary>
        /// Validates the host/process fields retained by the typed geometry
        /// context. Only the executable fingerprint belongs to the stable
        /// projection; the remaining process values remain session-scoped.
        /// </summary>
        internal static void ValidateHostProcessBindingContextFields(
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
            RequireIdentifier(hostProduct, 1, 96);
            RequireVersionToken(hostRelease, 1, 64);
            RequireIdentifier(hostRuntime, 1, 96);
            if (!string.Equals(hostMode, "full_host", StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry host mode is invalid.");
            }

            if (processId < 1 || processId > uint.MaxValue ||
                windowsSessionId < 0 || windowsSessionId > uint.MaxValue)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry process identity is invalid.");
            }

            CanonicalJson.RequireSha256(
                processInstanceFingerprint,
                nameof(processInstanceFingerprint));
            if (processCreationTime100Ns.Length == 0 ||
                processCreationTime100Ns.Length > 20)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry process creation time is invalid.");
            }

            for (int index = 0; index < processCreationTime100Ns.Length; index++)
            {
                if (processCreationTime100Ns[index] < '0' ||
                    processCreationTime100Ns[index] > '9')
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry process creation time is invalid.");
                }
            }

            if (!string.Equals(executableFingerprint, "unavailable", StringComparison.Ordinal))
            {
                CanonicalJson.RequireSha256(
                    executableFingerprint,
                    nameof(executableFingerprint));
            }
        }

        /// <summary>
        /// Parses one canonical v1 geometry export carried as raw UTF-8 text.
        /// This validates the frozen v1 schema shape, entity fingerprints, and
        /// self-integrity before a typed core projection may use the carrier.
        /// </summary>
        public static Dictionary<string, object?> RequireCanonicalGeometryCarrier(
            string geometryJson)
        {
            if (geometryJson == null)
            {
                throw new ArgumentNullException(nameof(geometryJson));
            }

            byte[] bytes;
            try
            {
                bytes = StrictUtf8.GetBytes(geometryJson);
            }
            catch (EncoderFallbackException exception)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON is not valid Unicode: " + exception.Message);
            }

            object? parsed = CanonicalJson.RequireCanonicalUtf8(
                bytes,
                NativeCadProtocolV2.MaxGeometryJsonBytes,
                CanonicalJsonOptions.Strict);
            Dictionary<string, object?>? geometry =
                parsed as Dictionary<string, object?>;
            if (geometry == null)
            {
                throw new CanonicalJsonException("Embedded geometry JSON root is not an object.");
            }

            RequireExactKeys(
                geometry,
                "schema_version",
                "source",
                "binding",
                "document",
                "owners",
                "containers",
                "entities",
                "portable_prewrite_projection",
                "portable_prewrite_projection_digest",
                "integrity");
            if (!string.Equals(
                    RequireString(geometry, "schema_version"),
                    NativeCadProtocolV2.GeometrySchemaVersion,
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON has the wrong schema version.");
            }

            RequireObject(geometry, "source");
            RequireObject(geometry, "binding");
            RequireObject(geometry, "document");
            RequireObject(geometry, "portable_prewrite_projection");
            CanonicalJson.RequireSha256(
                RequireString(geometry, "portable_prewrite_projection_digest"),
                "portablePrewriteProjectionDigest");
            List<object?> owners = RequireArray(geometry, "owners");
            List<object?> containers = RequireArray(geometry, "containers");
            List<object?> entities = RequireArray(geometry, "entities");
            if (owners.Count == 0 ||
                owners.Count > NativeCadProtocolV2.MaxGeometryContainers ||
                containers.Count == 0 ||
                containers.Count > NativeCadProtocolV2.MaxGeometryContainers ||
                entities.Count > NativeCadProtocolV2.MaxGeometryEntities)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON exceeds frozen entity/owner limits.");
            }

            Dictionary<string, object?> integrity = RequireObject(geometry, "integrity");
            RequireExactKeys(integrity, "algorithm", "sha256");
            if (!string.Equals(
                    RequireString(integrity, "algorithm"),
                    "SHA-256",
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON integrity algorithm is invalid.");
            }

            string claimed = RequireString(integrity, "sha256");
            CanonicalJson.RequireSha256(claimed, "geometryIntegritySha256");
            Dictionary<string, object?> payload =
                new Dictionary<string, object?>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, object?> entry in geometry)
            {
                if (!string.Equals(entry.Key, "integrity", StringComparison.Ordinal))
                {
                    payload.Add(entry.Key, entry.Value);
                }
            }

            if (!string.Equals(
                    CanonicalJson.Sha256Hex(payload),
                    claimed,
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON integrity does not match its payload.");
            }

            ValidateGeometrySchema(geometry, owners, containers, entities);
            return geometry;
        }

        private static Dictionary<string, object?> RequireObject(
            IDictionary<string, object?> values,
            string key)
        {
            object? raw;
            if (!values.TryGetValue(key, out raw) ||
                !(raw is Dictionary<string, object?>))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON has an invalid " + key + " object.");
            }

            return (Dictionary<string, object?>)raw;
        }

        private static List<object?> RequireArray(
            IDictionary<string, object?> values,
            string key)
        {
            object? raw;
            if (!values.TryGetValue(key, out raw) ||
                !(raw is List<object?>))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON has an invalid " + key + " array.");
            }

            return (List<object?>)raw;
        }

        private static string RequireString(
            IDictionary<string, object?> values,
            string key)
        {
            object? raw;
            string? value;
            if (!values.TryGetValue(key, out raw) || (value = raw as string) == null)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON has an invalid " + key + " string.");
            }

            return value;
        }

        private static void RequireExactKeys(
            IDictionary<string, object?> values,
            params string[] expected)
        {
            if (values.Count != expected.Length)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON object has an invalid field set.");
            }

            for (int index = 0; index < expected.Length; index++)
            {
                if (!values.ContainsKey(expected[index]))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry JSON object has an invalid field set.");
                }
            }
        }

        private static void ValidateGeometrySchema(
            Dictionary<string, object?> geometry,
            List<object?> owners,
            List<object?> containers,
            List<object?> entities)
        {
            Dictionary<string, object?> source = RequireObject(geometry, "source");
            Dictionary<string, object?> document = RequireObject(geometry, "document");
            ValidateSource(source);
            ValidateDocument(document);
            ValidateBinding(
                RequireObject(geometry, "binding"),
                source,
                document,
                containers);
            ValidatePortablePrewriteProjection(
                RequireObject(geometry, "portable_prewrite_projection"),
                RequireString(geometry, "portable_prewrite_projection_digest"));

            HashSet<string> knownOwners = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < owners.Count; index++)
            {
                string owner = RequireStringValue(owners[index], "owner handle");
                CadHandle.Require(owner, "owner");
                if (!knownOwners.Add(owner))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry JSON repeats an owner handle.");
                }
            }

            List<CadContainerPhysicalSlots> physicalContainers =
                ParsePhysicalContainers(containers, knownOwners);
            Dictionary<string, CadContainerPhysicalSlots> containersByKey =
                new Dictionary<string, CadContainerPhysicalSlots>(
                    StringComparer.Ordinal);
            for (int index = 0; index < physicalContainers.Count; index++)
            {
                CadContainerPhysicalSlots container = physicalContainers[index];
                containersByKey.Add(container.Container.SortKey, container);
            }

            int aggregateSegments = 0;
            HashSet<string> handles = new HashSet<string>(StringComparer.Ordinal);
            HashSet<string> containerSequences =
                new HashSet<string>(StringComparer.Ordinal);
            CadEntitySnapshot? previous = null;
            for (int index = 0; index < entities.Count; index++)
            {
                CadEntitySnapshot current = ParseEntity(
                    entities[index],
                    knownOwners,
                    ref aggregateSegments);
                if (!handles.Add(current.Handle))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry JSON repeats an entity handle.");
                }

                CadContainerPhysicalSlots? physicalContainer;
                if (!containersByKey.TryGetValue(
                        current.Container.SortKey,
                        out physicalContainer) ||
                    !string.Equals(
                        physicalContainer.OwnerHandle,
                        current.OwnerHandle,
                        StringComparison.Ordinal) ||
                    current.SequenceIndex >= physicalContainer.PhysicalSlotCount)
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry active entity is outside its physical container extent.");
                }

                string sequenceKey = current.Container.SortKey + "\u001f" +
                    current.SequenceIndex.ToString(System.Globalization.CultureInfo.InvariantCulture);
                if (!containerSequences.Add(sequenceKey) ||
                    (previous != null &&
                     CadDocumentSnapshot.CompareEntityOrder(previous, current) >= 0))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry JSON entity ordering is not canonical.");
                }

                previous = current;
            }
        }

        private static void ValidatePortablePrewriteProjection(
            Dictionary<string, object?> projection,
            string claimedDigest)
        {
            RequireExactKeys(
                projection,
                "schema_version",
                "ordered_entity_digest",
                "container_order_digest",
                "geometry_digest",
                "protected_semantic_digest",
                "table_state_digest",
                "layout_state_digest",
                "block_state_digest");
            if (!string.Equals(
                    RequireString(projection, "schema_version"),
                    NativeCadProtocolV2.PortablePrewriteProjectionSchemaVersion,
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry portable prewrite schema is invalid.");
            }

            PortablePrewriteProjectionV2 typed =
                new PortablePrewriteProjectionV2(
                    RequireString(projection, "ordered_entity_digest"),
                    RequireString(projection, "container_order_digest"),
                    RequireString(projection, "geometry_digest"),
                    RequireString(projection, "protected_semantic_digest"),
                    RequireString(projection, "table_state_digest"),
                    RequireString(projection, "layout_state_digest"),
                    RequireString(projection, "block_state_digest"));
            if (!string.Equals(
                    typed.Digest,
                    claimedDigest,
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry portable prewrite digest is invalid.");
            }
        }

        private static void ValidateSource(Dictionary<string, object?> source)
        {
            RequireExactKeys(
                source,
                "format",
                "sha256",
                "byte_size",
                "path_fingerprint",
                "file_identity_fingerprint",
                "dwg_header_signature");
            if (!string.Equals(
                    RequireString(source, "format"),
                    "DWG",
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException("Embedded geometry source format is invalid.");
            }

            ValidateSourceBindingFields(
                RequireString(source, "sha256"),
                RequireBoundedInteger(source, "byte_size", 0, long.MaxValue),
                RequireString(source, "path_fingerprint"),
                RequireString(source, "file_identity_fingerprint"),
                RequireString(source, "dwg_header_signature"));
        }

        private static void ValidateBinding(
            Dictionary<string, object?> binding,
            Dictionary<string, object?> source,
            Dictionary<string, object?> document,
            List<object?> containers)
        {
            RequireExactKeys(
                binding,
                "session_id",
                "session_schema_version",
                "protocol_version",
                "protocol_major",
                "protocol_minor",
                "host",
                "process",
                "adapter",
                "plugin",
                "capabilities",
                "session_binding_digest",
                "stable_host_binding_digest",
                "document_binding_digest");
            List<object?> capabilities = RequireArray(binding, "capabilities");
            List<string> capabilityValues = new List<string>();
            for (int index = 0; index < capabilities.Count; index++)
            {
                capabilityValues.Add(RequireStringValue(capabilities[index], "capability"));
            }
            ValidateBindingContextFields(
                RequireString(binding, "session_id"),
                RequireString(RequireObject(binding, "adapter"), "id"),
                RequireString(RequireObject(binding, "adapter"), "profile"),
                RequireString(RequireObject(binding, "adapter"), "version"),
                RequireString(RequireObject(binding, "plugin"), "id"),
                RequireString(RequireObject(binding, "plugin"), "version"),
                RequireString(RequireObject(binding, "plugin"), "fingerprint"),
                capabilityValues);
            if (!string.Equals(
                    RequireString(binding, "session_schema_version"),
                    NativeCadProtocolV2.SessionSchemaVersion,
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry session schema binding is invalid.");
            }
            if (!string.Equals(
                    RequireString(binding, "protocol_version"),
                    NativeCadProtocolV2.BridgeVersion,
                    StringComparison.Ordinal) ||
                RequireBoundedInteger(binding, "protocol_major", 1, 1) != 1 ||
                RequireBoundedInteger(binding, "protocol_minor", 0, 0) != 0)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry protocol binding is invalid.");
            }

            Dictionary<string, object?> host = RequireObject(binding, "host");
            RequireExactKeys(host, "product", "release", "runtime", "mode");
            RequireIdentifier(RequireString(host, "product"), 1, 96);
            RequireVersionToken(RequireString(host, "release"), 1, 64);
            RequireIdentifier(RequireString(host, "runtime"), 1, 96);
            if (!string.Equals(
                    RequireString(host, "mode"),
                    "full_host",
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException("Embedded geometry host mode is invalid.");
            }

            Dictionary<string, object?> process = RequireObject(binding, "process");
            RequireExactKeys(
                process,
                "pid",
                "windows_session_id",
                "instance_fingerprint",
                "creation_time_100ns",
                "executable_fingerprint");
            RequireBoundedInteger(process, "pid", 1, uint.MaxValue);
            RequireBoundedInteger(process, "windows_session_id", 0, uint.MaxValue);
            RequireSha256(process, "instance_fingerprint");
            string creationTime = RequireString(process, "creation_time_100ns");
            if (creationTime.Length == 0 || creationTime.Length > 20)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry process creation time is invalid.");
            }

            for (int index = 0; index < creationTime.Length; index++)
            {
                if (creationTime[index] < '0' || creationTime[index] > '9')
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry process creation time is invalid.");
                }
            }

            string executable = RequireString(process, "executable_fingerprint");
            if (!string.Equals(executable, "unavailable", StringComparison.Ordinal))
            {
                CanonicalJson.RequireSha256(executable, "executableFingerprint");
            }

            Dictionary<string, object?> adapter = RequireObject(binding, "adapter");
            RequireExactKeys(adapter, "id", "profile", "version");
            RequireIdentifier(RequireString(adapter, "id"), 1, 96);
            RequireIdentifier(RequireString(adapter, "profile"), 1, 96);
            RequireVersionToken(RequireString(adapter, "version"), 1, 64);

            Dictionary<string, object?> plugin = RequireObject(binding, "plugin");
            RequireExactKeys(plugin, "id", "version", "fingerprint");
            RequireIdentifier(RequireString(plugin, "id"), 1, 96);
            RequireVersionToken(RequireString(plugin, "version"), 1, 64);
            RequireSha256(plugin, "fingerprint");

            RequireSha256(binding, "session_binding_digest");
            string stableHostBindingDigest =
                RequireSha256(binding, "stable_host_binding_digest");
            List<object?> stableCapabilities =
                NativeCadCapabilities.ToWireValue(capabilityValues);
            string expectedStableHostBindingDigest = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "protocol_version", NativeCadProtocolV2.BridgeVersion },
                    { "protocol_major", 1L },
                    { "protocol_minor", 0L },
                    { "host", host },
                    {
                        "host_executable_fingerprint",
                        process["executable_fingerprint"]
                    },
                    { "adapter", adapter },
                    { "plugin", plugin },
                    { "capabilities", stableCapabilities },
                });
            if (!string.Equals(
                    stableHostBindingDigest,
                    expectedStableHostBindingDigest,
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry stable host binding digest is invalid.");
            }
            string documentBindingDigest =
                RequireSha256(binding, "document_binding_digest");
            string expectedDocumentBindingDigest = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "source", source },
                    { "document", document },
                    { "containers", containers },
                });
            if (!string.Equals(
                    documentBindingDigest,
                    expectedDocumentBindingDigest,
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry document binding digest is invalid.");
            }
        }

        private static void ValidateDocument(Dictionary<string, object?> document)
        {
            RequireExactKeys(
                document,
                "database_instance_fingerprint",
                "revision_fingerprint",
                "ordered_entity_digest",
                "container_order_digest",
                "complete_geometry_digest",
                "protected_state_digest",
                "protected_order_digest",
                "table_state_digest",
                "layout_state_digest",
                "block_state_digest",
                "document_state_digest",
                "marker_layer_fingerprint",
                "marker_style_fingerprint");
            for (int index = 0; index < 11; index++)
            {
                RequireSha256(
                    document,
                    new[]
                    {
                        "database_instance_fingerprint",
                        "revision_fingerprint",
                        "ordered_entity_digest",
                        "container_order_digest",
                        "complete_geometry_digest",
                        "protected_state_digest",
                        "protected_order_digest",
                        "table_state_digest",
                        "layout_state_digest",
                        "block_state_digest",
                        "document_state_digest",
                    }[index]);
            }

            RequireNullableSha256(document, "marker_layer_fingerprint");
            RequireNullableSha256(document, "marker_style_fingerprint");
        }

        private static List<CadContainerPhysicalSlots> ParsePhysicalContainers(
            List<object?> rawContainers,
            ISet<string> knownOwners)
        {
            List<CadContainerPhysicalSlots> result =
                new List<CadContainerPhysicalSlots>();
            HashSet<string> containers = new HashSet<string>(StringComparer.Ordinal);
            CadContainerPhysicalSlots? previous = null;
            for (int index = 0; index < rawContainers.Count; index++)
            {
                Dictionary<string, object?> raw =
                    RequireDictionary(rawContainers[index], "container");
                RequireExactKeys(
                    raw,
                    "owner_handle",
                    "space",
                    "block_path",
                    "physical_slot_count");
                string owner = RequireString(raw, "owner_handle");
                CadHandle.Require(owner, "containerOwner");
                if (!knownOwners.Contains(owner))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry container owner is invalid.");
                }

                List<object?> rawBlockPath = RequireArray(raw, "block_path");
                if (rawBlockPath.Count > 32)
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry container block path exceeds the frozen bound.");
                }

                List<string> blockPath = new List<string>();
                for (int pathIndex = 0; pathIndex < rawBlockPath.Count; pathIndex++)
                {
                    string block = RequireStringValue(
                        rawBlockPath[pathIndex],
                        "container block path");
                    CadHandle.Require(block, "containerBlockPath");
                    blockPath.Add(block);
                }

                CadContainer container = ParseContainer(
                    RequireObject(raw, "space"),
                    blockPath);
                if (!containers.Add(container.SortKey))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry container repeats its tuple.");
                }

                CadContainerPhysicalSlots physical =
                    new CadContainerPhysicalSlots(
                        container,
                        owner,
                        (int)RequireBoundedInteger(
                            raw,
                            "physical_slot_count",
                            0,
                            NativeCadProtocolV2.MaxPhysicalSlotCount));
                if (previous != null &&
                    string.CompareOrdinal(
                        previous.Container.SortKey,
                        physical.Container.SortKey) >= 0)
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry containers are not in canonical order.");
                }

                result.Add(physical);
                previous = physical;
            }

            if (result.Count == 0)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry has no physical containers.");
            }

            return result;
        }

        private static CadEntitySnapshot ParseEntity(
            object? raw,
            ISet<string> knownOwners,
            ref int aggregateSegments)
        {
            Dictionary<string, object?> entity =
                RequireDictionary(raw, "entity");
            RequireExactKeys(
                entity,
                "handle",
                "native_type",
                "owner_handle",
                "space",
                "block_path",
                "sequence_index",
                "layer",
                "text",
                "style",
                "height",
                "rotation",
                "position",
                "bounds",
                "segments",
                "overlay_evidence",
                "geometry_fingerprint",
                "opaque_state_digest");
            string handle = RequireString(entity, "handle");
            string owner = RequireString(entity, "owner_handle");
            CadHandle.Require(handle, "entityHandle");
            CadHandle.Require(owner, "entityOwner");
            if (!knownOwners.Contains(owner))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry entity owner is absent.");
            }

            NativeEntityKind kind = ParseEntityKind(
                RequireString(entity, "native_type"));
            List<object?> rawBlockPath = RequireArray(entity, "block_path");
            if (rawBlockPath.Count > 32)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry block path exceeds the frozen bound.");
            }

            List<string> blockPath = new List<string>();
            for (int index = 0; index < rawBlockPath.Count; index++)
            {
                string block = RequireStringValue(rawBlockPath[index], "block path");
                CadHandle.Require(block, "blockPath");
                blockPath.Add(block);
            }

            CadContainer container = ParseContainer(
                RequireObject(entity, "space"),
                blockPath);
            int sequenceIndex = (int)RequireBoundedInteger(
                entity,
                "sequence_index",
                0,
                NativeCadProtocolV2.MaxGeometrySequenceIndex);
            string? layer = RequireNullableString(entity, "layer", 1, 255);
            string? text = RequireNullableString(entity, "text", 0, 4096);
            string? style = RequireNullableString(entity, "style", 1, 255);
            string height = RequireBits(entity, "height");
            string rotation = RequireBits(entity, "rotation");
            Binary64Vector position = RequireVector(
                RequireArray(entity, "position"),
                "position");
            Dictionary<string, object?> rawBounds = RequireObject(entity, "bounds");
            RequireExactKeys(rawBounds, "minimum", "maximum");
            CadBounds bounds = new CadBounds(
                RequireVector(RequireArray(rawBounds, "minimum"), "bounds minimum"),
                RequireVector(RequireArray(rawBounds, "maximum"), "bounds maximum"));
            List<object?> rawSegments = RequireArray(entity, "segments");
            if (rawSegments.Count > NativeCadProtocolV2.MaxGeometrySegments ||
                aggregateSegments > NativeCadProtocolV2.MaxGeometrySegments - rawSegments.Count)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry segments exceed the frozen aggregate bound.");
            }

            List<CadSegment> segments = new List<CadSegment>();
            for (int index = 0; index < rawSegments.Count; index++)
            {
                Dictionary<string, object?> segment =
                    RequireDictionary(rawSegments[index], "segment");
                RequireExactKeys(segment, "start", "end");
                segments.Add(
                    new CadSegment(
                        RequireVector(RequireArray(segment, "start"), "segment start"),
                        RequireVector(RequireArray(segment, "end"), "segment end")));
            }

            aggregateSegments += segments.Count;
            Dictionary<string, object?> evidence =
                RequireObject(entity, "overlay_evidence");
            RequireExactKeys(
                evidence,
                "unique_content",
                "left_panel",
                "corresponding_right_absent",
                "visible_interference",
                "unsupported_data");
            OverlayEvidence overlayEvidence = new OverlayEvidence(
                RequireBoolean(evidence, "unique_content"),
                RequireBoolean(evidence, "left_panel"),
                RequireBoolean(evidence, "corresponding_right_absent"),
                RequireBoolean(evidence, "visible_interference"),
                RequireBoolean(evidence, "unsupported_data"));
            CadEntitySnapshot snapshot = new CadEntitySnapshot(
                handle,
                kind,
                owner,
                container,
                sequenceIndex,
                layer,
                text,
                style,
                height,
                rotation,
                position,
                bounds,
                segments,
                overlayEvidence);
            string geometryFingerprint = RequireSha256(entity, "geometry_fingerprint");
            string opaqueStateDigest = RequireSha256(entity, "opaque_state_digest");
            if (!string.Equals(
                    snapshot.GeometryFingerprint,
                    geometryFingerprint,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    snapshot.OpaqueStateDigest,
                    opaqueStateDigest,
                    StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry entity fingerprints are invalid.");
            }

            return snapshot;
        }

        private static CadContainer ParseContainer(
            Dictionary<string, object?> space,
            IEnumerable<string> blockPath)
        {
            RequireExactKeys(space, "kind", "layout_handle", "block_handle");
            string kindToken = RequireString(space, "kind");
            string? layout = RequireNullableString(space, "layout_handle", 1, 16);
            string? block = RequireNullableString(space, "block_handle", 1, 16);
            NativeSpaceKind kind;
            if (string.Equals(kindToken, "modelspace", StringComparison.Ordinal))
            {
                kind = NativeSpaceKind.Modelspace;
            }
            else if (string.Equals(kindToken, "paperspace", StringComparison.Ordinal))
            {
                kind = NativeSpaceKind.Paperspace;
            }
            else if (string.Equals(kindToken, "block", StringComparison.Ordinal))
            {
                kind = NativeSpaceKind.Block;
            }
            else
            {
                throw new CanonicalJsonException(
                    "Embedded geometry entity space kind is invalid.");
            }

            return new CadContainer(kind, layout, block, blockPath);
        }

        private static NativeEntityKind ParseEntityKind(string value)
        {
            if (string.Equals(value, "DBTEXT", StringComparison.Ordinal))
            {
                return NativeEntityKind.DbText;
            }

            if (string.Equals(value, "LINE", StringComparison.Ordinal))
            {
                return NativeEntityKind.Line;
            }

            if (string.Equals(value, "LWPOLYLINE", StringComparison.Ordinal))
            {
                return NativeEntityKind.LwPolyline;
            }

            if (string.Equals(value, "OPAQUE", StringComparison.Ordinal))
            {
                return NativeEntityKind.Opaque;
            }

            throw new CanonicalJsonException("Embedded geometry entity type is invalid.");
        }

        private static Binary64Vector RequireVector(
            List<object?> values,
            string label)
        {
            if (values.Count != 3)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry " + label + " vector is invalid.");
            }

            return new Binary64Vector(
                RequireBitsValue(values[0], label + " x"),
                RequireBitsValue(values[1], label + " y"),
                RequireBitsValue(values[2], label + " z"));
        }

        private static string RequireBits(
            IDictionary<string, object?> values,
            string key)
        {
            return RequireBitsValue(RequireValue(values, key), key);
        }

        private static string RequireBitsValue(object? raw, string label)
        {
            string value = RequireStringValue(raw, label);
            Binary64.ParseBits(value);
            return value;
        }

        private static string RequireSha256(
            IDictionary<string, object?> values,
            string key)
        {
            string value = RequireString(values, key);
            CanonicalJson.RequireSha256(value, key);
            return value;
        }

        private static void RequireNullableSha256(
            IDictionary<string, object?> values,
            string key)
        {
            object? raw = RequireValue(values, key);
            if (raw == null)
            {
                return;
            }

            CanonicalJson.RequireSha256(
                RequireStringValue(raw, key),
                key);
        }

        private static long RequireBoundedInteger(
            IDictionary<string, object?> values,
            string key,
            long minimum,
            long maximum)
        {
            object? raw = RequireValue(values, key);
            long value;
            if (raw is long)
            {
                value = (long)raw;
            }
            else if (raw is ulong &&
                (ulong)raw <= (ulong)long.MaxValue)
            {
                value = (long)(ulong)raw;
            }
            else
            {
                throw new CanonicalJsonException(
                    "Embedded geometry " + key + " integer is invalid.");
            }

            if (value < minimum || value > maximum)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry " + key + " integer is outside its bounds.");
            }

            return value;
        }

        private static string? RequireNullableString(
            IDictionary<string, object?> values,
            string key,
            int minimumCodePoints,
            int maximumCodePoints)
        {
            object? raw = RequireValue(values, key);
            if (raw == null)
            {
                return null;
            }

            string value = RequireStringValue(raw, key);
            int codePoints = CountCodePoints(value);
            if (codePoints < minimumCodePoints || codePoints > maximumCodePoints)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry " + key + " string is outside its bounds.");
            }

            return value;
        }

        private static void RequireIdentifier(
            string value,
            int minimumCodePoints,
            int maximumCodePoints)
        {
            CanonicalJson.RequireNfcString(value, "identifier");
            int codePoints = CountCodePoints(value);
            if (codePoints < minimumCodePoints || codePoints > maximumCodePoints)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry identifier is outside its bounds.");
            }

            char first = value[0];
            if (!((first >= 'A' && first <= 'Z') ||
                (first >= 'a' && first <= 'z') ||
                (first >= '0' && first <= '9')))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry identifier has an invalid spelling.");
            }

            for (int index = 1; index < value.Length; index++)
            {
                char current = value[index];
                if (!((current >= 'A' && current <= 'Z') ||
                    (current >= 'a' && current <= 'z') ||
                    (current >= '0' && current <= '9') ||
                    current == '.' ||
                    current == '_' ||
                    current == '/' ||
                    current == '-'))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry identifier has an invalid spelling.");
                }
            }
        }

        private static void RequireVersionToken(
            string value,
            int minimumCodePoints,
            int maximumCodePoints)
        {
            CanonicalJson.RequireNfcString(value, "version");
            int codePoints = CountCodePoints(value);
            if (codePoints < minimumCodePoints || codePoints > maximumCodePoints)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry version is outside its bounds.");
            }

            for (int index = 0; index < value.Length; index++)
            {
                char current = value[index];
                if (!((current >= 'A' && current <= 'Z') ||
                    (current >= 'a' && current <= 'z') ||
                    (current >= '0' && current <= '9') ||
                    current == '.' ||
                    current == '_' ||
                    current == '-'))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry version has an invalid spelling.");
                }
            }
        }

        private static void RequireCapability(string value)
        {
            CanonicalJson.RequireNfcString(value, "capability");
            int codePoints = CountCodePoints(value);
            if (codePoints < 3 || codePoints > 96 ||
                value[0] < 'a' || value[0] > 'z')
            {
                throw new CanonicalJsonException(
                    "Embedded geometry capability is invalid.");
            }

            for (int index = 1; index < value.Length; index++)
            {
                char current = value[index];
                if (!((current >= 'a' && current <= 'z') ||
                    (current >= '0' && current <= '9') ||
                    current == '.' ||
                    current == '_' ||
                    current == '/' ||
                    current == '-'))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry capability is invalid.");
                }
            }
        }

        private static void ValidateCapabilities(IReadOnlyList<string> capabilities)
        {
            if (capabilities == null ||
                capabilities.Count < 2 ||
                capabilities.Count > 16)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry capabilities are outside the frozen bounds.");
            }

            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < capabilities.Count; index++)
            {
                string capability = capabilities[index];
                if (capability == null)
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry capability is invalid.");
                }

                RequireCapability(capability);
                if (!seen.Add(capability))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry capabilities are duplicated.");
                }
            }

            NativeCadCapabilities.RequireCanonicalOrder(
                capabilities,
                "Embedded geometry capabilities");
        }

        private static void RequirePrefixedLowerHex(
            string value,
            string prefix,
            int suffixLength,
            string label)
        {
            CanonicalJson.RequireNfcString(value, label);
            if (!value.StartsWith(prefix, StringComparison.Ordinal) ||
                value.Length != prefix.Length + suffixLength)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry " + label + " is invalid.");
            }

            for (int index = prefix.Length; index < value.Length; index++)
            {
                char current = value[index];
                if (!((current >= '0' && current <= '9') ||
                    (current >= 'a' && current <= 'f')))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry " + label + " is invalid.");
                }
            }
        }

        private static bool RequireBoolean(
            IDictionary<string, object?> values,
            string key)
        {
            object? raw = RequireValue(values, key);
            if (!(raw is bool))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry " + key + " Boolean is invalid.");
            }

            return (bool)raw;
        }

        private static object? RequireValue(
            IDictionary<string, object?> values,
            string key)
        {
            object? value;
            if (!values.TryGetValue(key, out value))
            {
                throw new CanonicalJsonException(
                    "Embedded geometry JSON is missing " + key + ".");
            }

            return value;
        }

        private static Dictionary<string, object?> RequireDictionary(
            object? raw,
            string label)
        {
            Dictionary<string, object?>? value =
                raw as Dictionary<string, object?>;
            if (value == null)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry " + label + " object is invalid.");
            }

            return value;
        }

        private static string RequireStringValue(object? raw, string label)
        {
            string? value = raw as string;
            if (value == null)
            {
                throw new CanonicalJsonException(
                    "Embedded geometry " + label + " string is invalid.");
            }

            return value;
        }

        private static int CountCodePoints(string value)
        {
            int count = 0;
            for (int index = 0; index < value.Length; index++)
            {
                char current = value[index];
                if (char.IsHighSurrogate(current))
                {
                    if (index + 1 >= value.Length ||
                        !char.IsLowSurrogate(value[index + 1]))
                    {
                        throw new CanonicalJsonException(
                            "Embedded geometry string has an unpaired high surrogate.");
                    }

                    index++;
                }
                else if (char.IsLowSurrogate(current))
                {
                    throw new CanonicalJsonException(
                        "Embedded geometry string has an unpaired low surrogate.");
                }

                count++;
            }

            return count;
        }
    }
}
