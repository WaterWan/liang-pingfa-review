// SPDX-License-Identifier: MIT
// Strict v2 manifest projection. The core owns transactional semantics; this
// boundary owns canonical document/schema-shaped parsing and host bindings.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using LiangPingfa.NativeCad.Core;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.AutoCAD.Adapter
{
    internal enum ConsoleOperationMode
    {
        Execute,
        Export,
    }

    /// <summary>Validated private manifest plus the fields needed for command binding.</summary>
    internal sealed class ParsedManifest
    {
        internal ParsedManifest(
            CoreManifestV2 coreManifest,
            Dictionary<string, object?> raw,
            MarkerPolicyBindingV2 markerPolicy)
        {
            CoreManifest = coreManifest;
            Raw = raw;
            MarkerPolicy = markerPolicy;
        }

        internal CoreManifestV2 CoreManifest { get; private set; }

        internal Dictionary<string, object?> Raw { get; private set; }

        internal MarkerPolicyBindingV2 MarkerPolicy { get; private set; }
    }

    /// <summary>Reads only canonical, private, current v2 manifests.</summary>
    internal static class ManifestProjectionReader
    {
        private static readonly string[] ManifestKeys =
        {
            "schema_version",
            "manifest_id",
            "created_at",
            "expires_at",
            "consumed",
            "nonce",
            "audit_binding",
            "plan_binding",
            "intent_binding",
            "native_host_binding",
            "stable_host_binding_digest",
            "marker_policy_binding",
            "session_renewal",
            "source",
            "expected_prewrite_output_copy_binding",
            "final_output_constraints",
            "output_target_path_fingerprint",
            "environment",
            "expected_prewrite_revision",
            "preconditions_geometry_json",
            "preconditions_geometry_sha256",
            "operations",
            "record_cardinality",
            "integrity",
        };

        internal static ParsedManifest Read(
            string path,
            ConsoleCommandContext context,
            ConsoleOperationMode mode)
        {
            byte[] document = ReadBoundedDocument(path);
            byte[] canonical = StripRequiredFinalLineFeed(document);
            object? parsed = CanonicalJson.RequireCanonicalUtf8(
                canonical,
                NativeCadProtocolV2.MaxManifestDocumentBytes,
                NativeCadCanonicalJsonProfiles.Manifest);
            Dictionary<string, object?> manifest = RequireObjectValue(parsed, "manifest");
            RequireExactKeys(manifest, ManifestKeys);
            ValidatedFullManifestIntegrityV2 integrity =
                ValidatedFullManifestIntegrityV2.FromParsedManifest(manifest);

            RequireLiteral(
                RequireString(manifest, "schema_version"),
                NativeCadProtocolV2.ManifestSchemaVersion,
                "manifest schema");
            RequireTimestampCurrent(manifest, "created_at", "expires_at");
            if (RequireBoolean(manifest, "consumed"))
            {
                throw Failure("LPF_MANIFEST_REPLAY", "A native manifest was already consumed.");
            }

            string manifestId = RequireString(manifest, "manifest_id");
            string nonce = RequireString(manifest, "nonce");
            ValidateArtifactBindings(manifest);
            string nativeHostBinding = RequireSha256(manifest, "native_host_binding");
            string stableHostBinding = RequireSha256(
                manifest,
                "stable_host_binding_digest");
            MarkerPolicyBindingV2 marker = ReadMarkerPolicy(
                RequireObject(manifest, "marker_policy_binding"));
            ValidateSessionRenewal(RequireObject(manifest, "session_renewal"));
            NativeSourceBindingV2 source = ReadSourceBinding(
                RequireObject(manifest, "source"),
                true);
            NativeSourceBindingV2 prewriteSource = ReadSourceBinding(
                RequireObject(manifest, "expected_prewrite_output_copy_binding"),
                true);
            if (source.ExactlyMatches(prewriteSource))
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The private output copy must not bind the public source.");
            }

            FinalOutputConstraintsV2 finalConstraints = ReadFinalConstraints(
                RequireObject(manifest, "final_output_constraints"),
                context.PrivateRoot);
            RequireSha256(manifest, "output_target_path_fingerprint");
            Dictionary<string, object?> environment = RequireObject(
                manifest,
                "environment");
            ValidateEnvironment(environment, mode);
            GeometryExportV2 preconditions = ReadGeometry(
                RequireString(manifest, "preconditions_geometry_json"),
                marker);
            string preconditionsHash = RequireSha256(
                manifest,
                "preconditions_geometry_sha256");
            if (!string.Equals(
                    preconditionsHash,
                    CanonicalJson.Sha256Hex(preconditions.ToCanonicalJsonUtf8()),
                    StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_INTEGRITY",
                    "The manifest precondition geometry hash is invalid.");
            }
            if (!string.Equals(
                    RequireSha256(environment, "capabilities_digest"),
                    CapabilityDigest(preconditions.Snapshot.BindingContext.Capabilities),
                    StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_ENVIRONMENT",
                    "The manifest capability binding differs from preconditions.");
            }

            ExpectedPrewriteRevisionV2 expected = ValidatePrewriteRevision(
                RequireObject(manifest, "expected_prewrite_revision"),
                preconditions,
                nativeHostBinding,
                stableHostBinding);
            if (!expected.Source.ExactlyMatches(prewriteSource))
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The manifest prewrite source binding is inconsistent.");
            }

            List<ManifestOperationV2> operations = ReadOperations(
                RequireArray(manifest, "operations"));
            RequireLiteral(
                RequireString(manifest, "record_cardinality"),
                NativeCadProtocolV2.PrivateRecordCardinality,
                "manifest record cardinality");
            CoreManifestV2 core = new CoreManifestV2(
                manifestId,
                integrity,
                nonce,
                preconditions,
                expected,
                prewriteSource,
                finalConstraints,
                stableHostBinding,
                marker,
                operations);
            core.ValidateSelf();
            ManifestExecutionResultV2.RequirePretransactionTransportBudget(core);
            return new ParsedManifest(core, manifest, marker);
        }

        private static byte[] ReadBoundedDocument(string path)
        {
            FileInfo info = new FileInfo(path);
            if (!info.Exists ||
                info.Length < 1 ||
                info.Length > NativeCadProtocolV2.MaxManifestDocumentBytes + 1)
            {
                throw Failure(
                    "LPF_MANIFEST_DOCUMENT",
                    "The private manifest document is outside its byte bound.");
            }

            return File.ReadAllBytes(path);
        }

        private static byte[] StripRequiredFinalLineFeed(byte[] document)
        {
            if (document.Length == 0)
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "The private manifest is empty.");
            }

            int length = document.Length;
            if (document[length - 1] == 0x0a)
            {
                length--;
            }

            if (length == 0)
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "The private manifest is empty.");
            }

            byte[] result = new byte[length];
            Buffer.BlockCopy(document, 0, result, 0, length);
            return result;
        }

        private static void ValidateArtifactBindings(
            Dictionary<string, object?> manifest)
        {
            ValidateBinding(
                RequireObject(manifest, "audit_binding"),
                new[]
                {
                    "audit_id",
                    "audit_integrity_sha256",
                    "audit_schema_version",
                },
                "audit_schema_version",
                NativeCadProtocolV2.AuditSchemaVersion);
            ValidateBinding(
                RequireObject(manifest, "plan_binding"),
                new[]
                {
                    "plan_id",
                    "plan_integrity_sha256",
                    "plan_schema_version",
                },
                "plan_schema_version",
                NativeCadProtocolV2.PlanSchemaVersion);
            ValidateBinding(
                RequireObject(manifest, "intent_binding"),
                new[]
                {
                    "intent_id",
                    "intent_integrity_sha256",
                    "intent_schema_version",
                },
                "intent_schema_version",
                NativeCadProtocolV2.IntentSchemaVersion);
        }

        private static void ValidateBinding(
            Dictionary<string, object?> binding,
            string[] keys,
            string schemaKey,
            string schemaVersion)
        {
            RequireExactKeys(binding, keys);
            foreach (string key in keys)
            {
                if (key.EndsWith("_sha256", StringComparison.Ordinal))
                {
                    RequireSha256(binding, key);
                }
                else
                {
                    RequireString(binding, key);
                }
            }

            RequireLiteral(RequireString(binding, schemaKey), schemaVersion, schemaKey);
        }

        private static void ValidateSessionRenewal(
            Dictionary<string, object?> renewal)
        {
            RequireExactKeys(
                renewal,
                "audited_session_binding",
                "fresh_session_binding",
                "native_host_binding",
                "expires_at",
                "audited_session_schema_version",
                "fresh_session_schema_version");
            RequireSha256(renewal, "audited_session_binding");
            RequireSha256(renewal, "fresh_session_binding");
            RequireSha256(renewal, "native_host_binding");
            if (RequireTimestamp(
                    RequireString(renewal, "expires_at"),
                    "session renewal expiry") <= DateTime.UtcNow)
            {
                throw Failure(
                    "LPF_MANIFEST_REPLAY",
                    "The renewed native session has expired.");
            }
            RequireLiteral(
                RequireString(renewal, "audited_session_schema_version"),
                NativeCadProtocolV2.SessionSchemaVersion,
                "audited session schema");
            RequireLiteral(
                RequireString(renewal, "fresh_session_schema_version"),
                NativeCadProtocolV2.SessionSchemaVersion,
                "fresh session schema");
        }

        private static FinalOutputConstraintsV2 ReadFinalConstraints(
            Dictionary<string, object?> values,
            string privateRoot)
        {
            RequireExactKeys(
                values,
                "authorized_private_path_fingerprint",
                "authorized_private_root_fingerprint",
                "require_same_volume_as_prewrite",
                "require_within_private_root",
                "required_dwg_header_signature",
                "required_dwg_version",
                "max_byte_size",
                "file_identity_transition_policy");
            string rootHash = RequireSha256(
                values,
                "authorized_private_root_fingerprint");
            if (!string.Equals(
                    rootHash,
                    AdapterIdentity.HashUtf8(privateRoot),
                    StringComparison.Ordinal) ||
                !RequireBoolean(values, "require_same_volume_as_prewrite") ||
                !RequireBoolean(values, "require_within_private_root"))
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The final private workspace authorization is invalid.");
            }

            string transition = RequireString(
                values,
                "file_identity_transition_policy");
            FileIdentityTransitionPolicyV2 policy;
            if (string.Equals(
                    transition,
                    "same_identity_required",
                    StringComparison.Ordinal))
            {
                policy = FileIdentityTransitionPolicyV2.SameIdentityRequired;
            }
            else if (string.Equals(
                transition,
                "replacement_allowed",
                StringComparison.Ordinal))
            {
                policy = FileIdentityTransitionPolicyV2.ReplacementAllowed;
            }
            else
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The final identity transition policy is invalid.");
            }

            return new FinalOutputConstraintsV2(
                RequireSha256(values, "authorized_private_path_fingerprint"),
                rootHash,
                RequireString(values, "required_dwg_header_signature"),
                RequireString(values, "required_dwg_version"),
                RequireInteger(values, "max_byte_size", 6, int.MaxValue),
                policy);
        }

        private static void ValidateEnvironment(
            Dictionary<string, object?> environment,
            ConsoleOperationMode mode)
        {
            RequireExactKeys(
                environment,
                "core_console_fingerprint",
                "write_plugin_fingerprint",
                "readback_plugin_fingerprint",
                "write_command",
                "readback_command",
                "write_revision_transition",
                "protocol_major",
                "protocol_minor",
                "capabilities_digest");
            RequireLiteral(
                RequireString(environment, "write_command"),
                AdapterIdentity.ExecuteCommand,
                "write command");
            RequireLiteral(
                RequireString(environment, "readback_command"),
                AdapterIdentity.ExportCommand,
                "readback command");
            RequireInteger(environment, "protocol_major", 1, 1);
            RequireInteger(environment, "protocol_minor", 0, 0);
            string revision = RequireString(environment, "write_revision_transition");
            if (!string.Equals(
                    revision,
                    "save_reopen_changes_revision",
                    StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_ENVIRONMENT",
                    "The adapter requires an observed save/reopen revision transition.");
            }

            string writeFingerprint = RequireSha256(
                environment,
                "write_plugin_fingerprint");
            string readbackFingerprint = RequireSha256(
                environment,
                "readback_plugin_fingerprint");
            string assembly = AdapterIdentity.AssemblyFingerprint();
            string expectedPlugin = mode == ConsoleOperationMode.Execute
                ? writeFingerprint
                : readbackFingerprint;
            if (!string.Equals(assembly, expectedPlugin, StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_ENVIRONMENT",
                    "The loaded adapter fingerprint differs from the manifest.");
            }

            string processExecutable = AutodeskHostBinding.CurrentExecutableFingerprint();
            if (!string.Equals(
                    processExecutable,
                    RequireSha256(environment, "core_console_fingerprint"),
                    StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_ENVIRONMENT",
                    "The Core Console fingerprint differs from the manifest.");
            }
            RequireSha256(environment, "capabilities_digest");
        }

        private static MarkerPolicyBindingV2 ReadMarkerPolicy(
            Dictionary<string, object?> values)
        {
            RequireExactKeys(
                values,
                "policy_version",
                "profile",
                "profile_enabled",
                "enabled",
                "plugin_capability",
                "layer",
                "style",
                "layer_fingerprint",
                "style_fingerprint",
                "height_bits",
                "rotation_bits",
                "text_prefix",
                "text_derivation_version",
                "geometry_defaults");
            RequireLiteral(
                RequireString(values, "policy_version"),
                "marker-policy/v1",
                "marker policy version");
            RequireLiteral(
                RequireString(values, "profile"),
                "create_review_marker/v1",
                "marker profile");
            RequireLiteral(
                RequireString(values, "text_prefix"),
                NativeCadProtocolV2.MarkerTextPrefix,
                "marker text prefix");
            RequireLiteral(
                RequireString(values, "text_derivation_version"),
                "operation-id-suffix/v1",
                "marker text derivation");
            Dictionary<string, object?> defaults = RequireObject(
                values,
                "geometry_defaults");
            RequireExactKeys(defaults, "space_kind", "block_path", "overlay_evidence");
            RequireLiteral(
                RequireString(defaults, "space_kind"),
                "modelspace",
                "marker space");
            if (RequireArray(defaults, "block_path").Count != 0)
            {
                throw Failure("LPF_MARKER_POLICY", "Marker block paths are forbidden.");
            }

            OverlayEvidence evidence = ReadEvidence(
                RequireObject(defaults, "overlay_evidence"));
            return new MarkerPolicyBindingV2(
                RequireBoolean(values, "profile_enabled"),
                RequireBoolean(values, "enabled"),
                RequireBoolean(values, "plugin_capability"),
                RequireString(values, "layer"),
                RequireString(values, "style"),
                RequireSha256(values, "layer_fingerprint"),
                RequireSha256(values, "style_fingerprint"),
                RequireString(values, "height_bits"),
                RequireString(values, "rotation_bits"),
                evidence);
        }

        private static GeometryExportV2 ReadGeometry(
            string geometryJson,
            MarkerPolicyBindingV2 marker)
        {
            Dictionary<string, object?> raw =
                NativeGeometryJsonV2.RequireCanonicalGeometryCarrier(geometryJson);
            Dictionary<string, object?> sourceRaw = RequireObject(raw, "source");
            NativeSourceBindingV2 source = ReadSourceBinding(sourceRaw, true);
            NativeGeometryBindingContextV2 binding = ReadBinding(
                RequireObject(raw, "binding"));
            Dictionary<string, object?> document = RequireObject(raw, "document");
            List<object?> ownersRaw = RequireArray(raw, "owners");
            List<string> owners = new List<string>();
            for (int index = 0; index < ownersRaw.Count; index++)
            {
                owners.Add(RequireStringValue(ownersRaw[index], "owner"));
            }

            CadDocumentTables tables = ReadDocumentTables(document, marker);
            List<object?> containersRaw = RequireArray(raw, "containers");
            List<CadContainerPhysicalSlots> containers =
                new List<CadContainerPhysicalSlots>();
            for (int index = 0; index < containersRaw.Count; index++)
            {
                containers.Add(ReadPhysicalContainer(containersRaw[index]));
            }

            List<object?> entitiesRaw = RequireArray(raw, "entities");
            List<CadEntitySnapshot> entities = new List<CadEntitySnapshot>();
            for (int index = 0; index < entitiesRaw.Count; index++)
            {
                entities.Add(ReadEntity(entitiesRaw[index]));
            }

            CadDocumentSnapshot snapshot = new CadDocumentSnapshot(
                RequireSha256(document, "database_instance_fingerprint"),
                RequireSha256(document, "revision_fingerprint"),
                owners,
                containers,
                entities,
                tables,
                source,
                binding);
            GeometryExportV2 export = ExactCadExporter.Export(snapshot);
            byte[] expected = new UTF8Encoding(false, true).GetBytes(geometryJson);
            byte[] actual = export.ToCanonicalJsonUtf8();
            if (!ByteArraysEqual(expected, actual))
            {
                throw Failure(
                    "LPF_MANIFEST_GEOMETRY",
                    "The typed precondition geometry differs from its canonical carrier.");
            }

            return export;
        }

        private static NativeGeometryBindingContextV2 ReadBinding(
            Dictionary<string, object?> binding)
        {
            Dictionary<string, object?> host = RequireObject(binding, "host");
            Dictionary<string, object?> process = RequireObject(binding, "process");
            Dictionary<string, object?> adapter = RequireObject(binding, "adapter");
            Dictionary<string, object?> plugin = RequireObject(binding, "plugin");
            List<object?> rawCapabilities = RequireArray(binding, "capabilities");
            List<string> capabilities = new List<string>();
            for (int index = 0; index < rawCapabilities.Count; index++)
            {
                capabilities.Add(RequireStringValue(rawCapabilities[index], "capability"));
            }
            NativeCadCapabilities.RequireAutoCadAdapter(
                capabilities,
                "manifest capabilities");

            return new NativeGeometryBindingContextV2(
                RequireString(binding, "session_id"),
                RequireString(adapter, "id"),
                RequireString(adapter, "profile"),
                RequireString(adapter, "version"),
                RequireString(plugin, "id"),
                RequireString(plugin, "version"),
                RequireSha256(plugin, "fingerprint"),
                capabilities,
                RequireString(host, "product"),
                RequireString(host, "release"),
                RequireString(host, "runtime"),
                RequireString(host, "mode"),
                RequireInteger(process, "pid", 1, uint.MaxValue),
                RequireInteger(process, "windows_session_id", 0, uint.MaxValue),
                RequireSha256(process, "instance_fingerprint"),
                RequireString(process, "creation_time_100ns"),
                RequireString(process, "executable_fingerprint"));
        }

        private static CadDocumentTables ReadDocumentTables(
            Dictionary<string, object?> document,
            MarkerPolicyBindingV2 marker)
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
            string? markerLayer = RequireNullableSha256(
                document,
                "marker_layer_fingerprint");
            string? markerStyle = RequireNullableSha256(
                document,
                "marker_style_fingerprint");
            Dictionary<string, string> layers = new Dictionary<string, string>(
                StringComparer.Ordinal);
            Dictionary<string, string> styles = new Dictionary<string, string>(
                StringComparer.Ordinal);
            if (string.Equals(
                    markerLayer,
                    marker.LayerFingerprint,
                    StringComparison.Ordinal) &&
                string.Equals(
                    markerStyle,
                    marker.StyleFingerprint,
                    StringComparison.Ordinal))
            {
                layers.Add(marker.Layer, marker.LayerFingerprint);
                styles.Add(marker.Style, marker.StyleFingerprint);
            }

            return new CadDocumentTables(
                RequireSha256(document, "table_state_digest"),
                RequireSha256(document, "layout_state_digest"),
                RequireSha256(document, "block_state_digest"),
                markerLayer,
                markerStyle,
                layers,
                styles);
        }

        private static CadContainerPhysicalSlots ReadPhysicalContainer(object? raw)
        {
            Dictionary<string, object?> container =
                RequireObjectValue(raw, "physical container");
            RequireExactKeys(
                container,
                "owner_handle",
                "space",
                "block_path",
                "physical_slot_count");
            List<object?> pathRaw = RequireArray(container, "block_path");
            List<string> path = new List<string>();
            for (int index = 0; index < pathRaw.Count; index++)
            {
                path.Add(RequireStringValue(pathRaw[index], "container block path"));
            }

            Dictionary<string, object?> space = RequireObject(container, "space");
            RequireExactKeys(space, "kind", "layout_handle", "block_handle");
            string kind = RequireString(space, "kind");
            NativeSpaceKind spaceKind;
            if (string.Equals(kind, "modelspace", StringComparison.Ordinal))
            {
                spaceKind = NativeSpaceKind.Modelspace;
            }
            else if (string.Equals(kind, "paperspace", StringComparison.Ordinal))
            {
                spaceKind = NativeSpaceKind.Paperspace;
            }
            else if (string.Equals(kind, "block", StringComparison.Ordinal))
            {
                spaceKind = NativeSpaceKind.Block;
            }
            else
            {
                throw Failure(
                    "LPF_MANIFEST_GEOMETRY",
                    "A physical container space is invalid.");
            }

            return new CadContainerPhysicalSlots(
                new CadContainer(
                    spaceKind,
                    RequireNullableString(space, "layout_handle"),
                    RequireNullableString(space, "block_handle"),
                    path),
                RequireString(container, "owner_handle"),
                (int)RequireInteger(
                    container,
                    "physical_slot_count",
                    0,
                    NativeCadProtocolV2.MaxPhysicalSlotCount));
        }

        private static CadEntitySnapshot ReadEntity(object? raw)
        {
            Dictionary<string, object?> entity = RequireObjectValue(raw, "entity");
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
            List<object?> pathRaw = RequireArray(entity, "block_path");
            List<string> path = new List<string>();
            for (int index = 0; index < pathRaw.Count; index++)
            {
                path.Add(RequireStringValue(pathRaw[index], "block path"));
            }

            Dictionary<string, object?> space = RequireObject(entity, "space");
            RequireExactKeys(space, "kind", "layout_handle", "block_handle");
            string kind = RequireString(space, "kind");
            NativeSpaceKind spaceKind;
            if (string.Equals(kind, "modelspace", StringComparison.Ordinal))
            {
                spaceKind = NativeSpaceKind.Modelspace;
            }
            else if (string.Equals(kind, "paperspace", StringComparison.Ordinal))
            {
                spaceKind = NativeSpaceKind.Paperspace;
            }
            else if (string.Equals(kind, "block", StringComparison.Ordinal))
            {
                spaceKind = NativeSpaceKind.Block;
            }
            else
            {
                throw Failure("LPF_MANIFEST_GEOMETRY", "An entity space is invalid.");
            }

            CadContainer container = new CadContainer(
                spaceKind,
                RequireNullableString(space, "layout_handle"),
                RequireNullableString(space, "block_handle"),
                path);
            NativeEntityKind entityKind = ReadEntityKind(
                RequireString(entity, "native_type"));
            Dictionary<string, object?> bounds = RequireObject(entity, "bounds");
            RequireExactKeys(bounds, "minimum", "maximum");
            List<object?> segmentsRaw = RequireArray(entity, "segments");
            List<CadSegment> segments = new List<CadSegment>();
            for (int index = 0; index < segmentsRaw.Count; index++)
            {
                Dictionary<string, object?> segment =
                    RequireObjectValue(segmentsRaw[index], "segment");
                RequireExactKeys(segment, "start", "end");
                segments.Add(new CadSegment(
                    ReadVector(RequireArray(segment, "start")),
                    ReadVector(RequireArray(segment, "end"))));
            }

            CadEntitySnapshot result = new CadEntitySnapshot(
                RequireString(entity, "handle"),
                entityKind,
                RequireString(entity, "owner_handle"),
                container,
                (int)RequireInteger(
                    entity,
                    "sequence_index",
                    0,
                    NativeCadProtocolV2.MaxGeometrySequenceIndex),
                RequireNullableString(entity, "layer"),
                RequireNullableString(entity, "text"),
                RequireNullableString(entity, "style"),
                RequireString(entity, "height"),
                RequireString(entity, "rotation"),
                ReadVector(RequireArray(entity, "position")),
                new CadBounds(
                    ReadVector(RequireArray(bounds, "minimum")),
                    ReadVector(RequireArray(bounds, "maximum"))),
                segments,
                ReadEvidence(RequireObject(entity, "overlay_evidence")));
            if (!string.Equals(
                    result.GeometryFingerprint,
                    RequireSha256(entity, "geometry_fingerprint"),
                    StringComparison.Ordinal) ||
                !string.Equals(
                    result.OpaqueStateDigest,
                    RequireSha256(entity, "opaque_state_digest"),
                    StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_GEOMETRY",
                    "An entity fingerprint is invalid.");
            }

            return result;
        }

        private static NativeEntityKind ReadEntityKind(string value)
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

            throw Failure("LPF_MANIFEST_GEOMETRY", "An entity type is invalid.");
        }

        private static ExpectedPrewriteRevisionV2 ValidatePrewriteRevision(
            Dictionary<string, object?> values,
            GeometryExportV2 preconditions,
            string nativeHostBinding,
            string stableHostBinding)
        {
            RequireExactKeys(
                values,
                "source_binding",
                "document_path_fingerprint",
                "document_file_identity_fingerprint",
                "document_content_sha256",
                "document_byte_size",
                "bridge_document_identity",
                "portable_prewrite_projection",
                "portable_prewrite_projection_digest",
                "adapter_binding",
                "native_host_binding",
                "stable_host_binding_digest",
                "audited_semantic_state_digest");
            ExpectedPrewriteRevisionV2 expected =
                ExpectedPrewriteRevisionV2.From(preconditions);
            NativeSourceBindingV2 source = ReadSourceBinding(
                RequireObject(values, "source_binding"),
                true);
            if (!source.ExactlyMatches(expected.Source) ||
                !string.Equals(
                    RequireSha256(values, "document_path_fingerprint"),
                    expected.Source.PathFingerprint,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireSha256(values, "document_file_identity_fingerprint"),
                    expected.Source.FileIdentityFingerprint,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireSha256(values, "document_content_sha256"),
                    expected.Source.Sha256,
                    StringComparison.Ordinal) ||
                RequireInteger(values, "document_byte_size", 0, long.MaxValue) !=
                    expected.Source.ByteSize)
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The prewrite private source differs from geometry.");
            }

            Dictionary<string, object?> bridgeIdentity = RequireObject(
                values,
                "bridge_document_identity");
            RequireExactKeys(
                bridgeIdentity,
                "database_instance_fingerprint",
                "revision_fingerprint");
            if (!string.Equals(
                    RequireSha256(bridgeIdentity, "database_instance_fingerprint"),
                    preconditions.Document.DatabaseInstanceFingerprint,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireSha256(bridgeIdentity, "revision_fingerprint"),
                    preconditions.Document.RevisionFingerprint,
                    StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The bridge-only document identity differs from geometry.");
            }

            Dictionary<string, object?> portableRaw = RequireObject(
                values,
                "portable_prewrite_projection");
            RequireExactKeys(
                portableRaw,
                "schema_version",
                "ordered_entity_digest",
                "container_order_digest",
                "geometry_digest",
                "protected_semantic_digest",
                "table_state_digest",
                "layout_state_digest",
                "block_state_digest");
            RequireLiteral(
                RequireString(portableRaw, "schema_version"),
                NativeCadProtocolV2.PortablePrewriteProjectionSchemaVersion,
                "portable prewrite projection schema");
            PortablePrewriteProjectionV2 portable =
                new PortablePrewriteProjectionV2(
                    RequireSha256(portableRaw, "ordered_entity_digest"),
                    RequireSha256(portableRaw, "container_order_digest"),
                    RequireSha256(portableRaw, "geometry_digest"),
                    RequireSha256(portableRaw, "protected_semantic_digest"),
                    RequireSha256(portableRaw, "table_state_digest"),
                    RequireSha256(portableRaw, "layout_state_digest"),
                    RequireSha256(portableRaw, "block_state_digest"));
            if (!string.Equals(
                    RequireSha256(values, "portable_prewrite_projection_digest"),
                    portable.Digest,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    portable.Digest,
                    expected.PortableProjectionDigest,
                    StringComparison.Ordinal) ||
                !expected.PortableProjection.Matches(preconditions))
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The portable prewrite projection differs from geometry.");
            }

            if (
                !string.Equals(
                    RequireSha256(values, "native_host_binding"),
                    nativeHostBinding,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireSha256(values, "stable_host_binding_digest"),
                    stableHostBinding,
                    StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The prewrite revision does not match the exact geometry.");
            }

            ValidateAdapterBinding(
                RequireObject(values, "adapter_binding"),
                preconditions.Snapshot.BindingContext);
            RequireSha256(values, "audited_semantic_state_digest");
            return expected;
        }

        private static void ValidateAdapterBinding(
            Dictionary<string, object?> values,
            NativeGeometryBindingContextV2 binding)
        {
            RequireExactKeys(
                values,
                "adapter_id",
                "adapter_profile",
                "adapter_version",
                "plugin_id",
                "plugin_version",
                "plugin_fingerprint",
                "protocol_major",
                "protocol_minor",
                "capabilities_digest");
            if (!string.Equals(
                    RequireString(values, "adapter_id"),
                    binding.AdapterId,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireString(values, "adapter_profile"),
                    binding.AdapterProfile,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireString(values, "adapter_version"),
                    binding.AdapterVersion,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireString(values, "plugin_id"),
                    binding.PluginId,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireString(values, "plugin_version"),
                    binding.PluginVersion,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    RequireSha256(values, "plugin_fingerprint"),
                    binding.PluginFingerprint,
                    StringComparison.Ordinal) ||
                RequireInteger(values, "protocol_major", 1, 1) != 1 ||
                RequireInteger(values, "protocol_minor", 0, 0) != 0 ||
                !string.Equals(
                    RequireSha256(values, "capabilities_digest"),
                    CapabilityDigest(binding.Capabilities),
                    StringComparison.Ordinal))
            {
                throw Failure(
                    "LPF_MANIFEST_BINDING",
                    "The prewrite adapter binding differs from geometry.");
            }
        }

        private static List<ManifestOperationV2> ReadOperations(
            List<object?> raw)
        {
            if (raw.Count == 0 || raw.Count > NativeCadProtocolV2.MaxNativeOperations)
            {
                throw Failure(
                    "LPF_MANIFEST_OPERATION",
                    "The fixed operation cardinality is invalid.");
            }

            List<ManifestOperationV2> result = new List<ManifestOperationV2>();
            for (int index = 0; index < raw.Count; index++)
            {
                Dictionary<string, object?> operation =
                    RequireObjectValue(raw[index], "operation");
                string kind = RequireString(operation, "kind");
                if (string.Equals(kind, "translate_dbtext", StringComparison.Ordinal))
                {
                    RequireExactKeys(
                        operation,
                        "operation_id",
                        "kind",
                        "target_id",
                        "delta",
                        "expected_after");
                    Dictionary<string, object?> after =
                        RequireObject(operation, "expected_after");
                    RequireExactKeys(after, "position", "bounds", "segments");
                    Dictionary<string, object?> bounds = RequireObject(after, "bounds");
                    RequireExactKeys(bounds, "minimum", "maximum");
                    List<CadSegment> segments = ReadSegments(
                        RequireArray(after, "segments"));
                    result.Add(new TranslateDbTextOperationV2(
                        RequireString(operation, "operation_id"),
                        RequireString(operation, "target_id"),
                        ReadVector(RequireArray(operation, "delta")),
                        new TranslatedGeometryV2(
                            ReadVector(RequireArray(after, "position")),
                            new CadBounds(
                                ReadVector(RequireArray(bounds, "minimum")),
                                ReadVector(RequireArray(bounds, "maximum"))),
                            segments)));
                }
                else if (string.Equals(
                    kind,
                    "delete_auxiliary_overlay_text",
                    StringComparison.Ordinal))
                {
                    RequireExactKeys(operation, "operation_id", "kind", "target_id");
                    // This command parser runs before CreateDatabase and
                    // BeginTransaction. Do not let a core-level delete reach
                    // AutoCAD until a separately versioned post-SaveAs slot
                    // compaction policy has real-host evidence.
                    throw Failure(
                        "LPF_UNSUPPORTED_OPERATION",
                        "delete_auxiliary_overlay_text is unsupported by the AutoCAD adapter.");
                }
                else if (string.Equals(
                    kind,
                    "create_review_marker",
                    StringComparison.Ordinal))
                {
                    RequireExactKeys(
                        operation,
                        "operation_id",
                        "kind",
                        "owner_handle",
                        "space",
                        "block_path",
                        "sequence_index",
                        "position",
                        "marker_text",
                        "marker_fingerprint",
                        "layer",
                        "style",
                        "height",
                        "rotation",
                        "overlay_evidence");
                    Dictionary<string, object?> space = RequireObject(operation, "space");
                    RequireExactKeys(space, "kind", "layout_handle", "block_handle");
                    RequireLiteral(
                        RequireString(space, "kind"),
                        "modelspace",
                        "marker space");
                    List<object?> blockPath = RequireArray(operation, "block_path");
                    if (blockPath.Count != 0 || RequireNullableString(space, "block_handle") != null)
                    {
                        throw Failure(
                            "LPF_MANIFEST_OPERATION",
                            "Marker block paths are forbidden.");
                    }

                    result.Add(new CreateReviewMarkerOperationV2(
                        RequireString(operation, "operation_id"),
                        RequireString(operation, "owner_handle"),
                        new CadContainer(
                            NativeSpaceKind.Modelspace,
                            RequireString(space, "layout_handle"),
                            null,
                            new string[0]),
                        (int)RequireInteger(
                            operation,
                            "sequence_index",
                            0,
                            NativeCadProtocolV2.MaxGeometrySequenceIndex),
                        ReadVector(RequireArray(operation, "position")),
                        RequireString(operation, "marker_text"),
                        RequireSha256(operation, "marker_fingerprint"),
                        RequireString(operation, "layer"),
                        RequireString(operation, "style"),
                        RequireString(operation, "height"),
                        RequireString(operation, "rotation"),
                        ReadEvidence(RequireObject(operation, "overlay_evidence"))));
                }
                else
                {
                    throw Failure(
                        "LPF_MANIFEST_OPERATION",
                        "A manifest operation is not allowlisted.");
                }
            }

            return result;
        }

        private static List<CadSegment> ReadSegments(List<object?> values)
        {
            List<CadSegment> result = new List<CadSegment>();
            for (int index = 0; index < values.Count; index++)
            {
                Dictionary<string, object?> segment =
                    RequireObjectValue(values[index], "segment");
                RequireExactKeys(segment, "start", "end");
                result.Add(new CadSegment(
                    ReadVector(RequireArray(segment, "start")),
                    ReadVector(RequireArray(segment, "end"))));
            }

            return result;
        }

        private static NativeSourceBindingV2 ReadSourceBinding(
            Dictionary<string, object?> values,
            bool requiresFormat)
        {
            if (requiresFormat)
            {
                RequireExactKeys(
                    values,
                    "format",
                    "sha256",
                    "byte_size",
                    "path_fingerprint",
                    "file_identity_fingerprint",
                    "dwg_header_signature");
                RequireLiteral(RequireString(values, "format"), "DWG", "source format");
            }
            else
            {
                RequireExactKeys(
                    values,
                    "sha256",
                    "byte_size",
                    "path_fingerprint",
                    "file_identity_fingerprint",
                    "dwg_header_signature");
            }

            return new NativeSourceBindingV2(
                RequireSha256(values, "sha256"),
                RequireInteger(values, "byte_size", 0, long.MaxValue),
                RequireSha256(values, "path_fingerprint"),
                RequireSha256(values, "file_identity_fingerprint"),
                RequireString(values, "dwg_header_signature"));
        }

        private static Binary64Vector ReadVector(List<object?> values)
        {
            if (values.Count != 3)
            {
                throw Failure("LPF_MANIFEST_GEOMETRY", "A binary64 vector is invalid.");
            }

            return new Binary64Vector(
                RequireStringValue(values[0], "vector x"),
                RequireStringValue(values[1], "vector y"),
                RequireStringValue(values[2], "vector z"));
        }

        private static OverlayEvidence ReadEvidence(
            Dictionary<string, object?> values)
        {
            RequireExactKeys(
                values,
                "unique_content",
                "left_panel",
                "corresponding_right_absent",
                "visible_interference",
                "unsupported_data");
            return new OverlayEvidence(
                RequireBoolean(values, "unique_content"),
                RequireBoolean(values, "left_panel"),
                RequireBoolean(values, "corresponding_right_absent"),
                RequireBoolean(values, "visible_interference"),
                RequireBoolean(values, "unsupported_data"));
        }

        private static string CapabilityDigest(IReadOnlyList<string> capabilities)
        {
            NativeCadCapabilities.RequireAutoCadAdapter(
                capabilities,
                "manifest capabilities");
            return CanonicalJson.Sha256Hex(
                NativeCadCapabilities.ToWireValue(capabilities));
        }

        private static void RequireTimestampCurrent(
            Dictionary<string, object?> values,
            string createdKey,
            string expiresKey)
        {
            DateTime created = RequireTimestamp(
                RequireString(values, createdKey),
                createdKey);
            DateTime expires = RequireTimestamp(
                RequireString(values, expiresKey),
                expiresKey);
            DateTime now = DateTime.UtcNow;
            if (created > now || now >= expires || expires > created.AddMinutes(5))
            {
                throw Failure(
                    "LPF_MANIFEST_REPLAY",
                    "The native manifest is expired or outside its fixed lifetime.");
            }
        }

        private static DateTime RequireTimestamp(string value, string label)
        {
            DateTime parsed;
            if (!DateTime.TryParseExact(
                    value,
                    "yyyy-MM-dd'T'HH:mm:ss'Z'",
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out parsed))
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A timestamp is invalid: " + label);
            }

            return parsed;
        }

        private static long RequireInteger(
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
            else if (raw is ulong && (ulong)raw <= long.MaxValue)
            {
                value = (long)(ulong)raw;
            }
            else
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest integer is invalid.");
            }

            if (value < minimum || value > maximum)
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest integer is out of bounds.");
            }

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

        private static string? RequireNullableSha256(
            IDictionary<string, object?> values,
            string key)
        {
            object? raw = RequireValue(values, key);
            if (raw == null)
            {
                return null;
            }

            string value = RequireStringValue(raw, key);
            CanonicalJson.RequireSha256(value, key);
            return value;
        }

        private static string? RequireNullableString(
            IDictionary<string, object?> values,
            string key)
        {
            object? raw = RequireValue(values, key);
            return raw == null ? null : RequireStringValue(raw, key);
        }

        private static string RequireString(
            IDictionary<string, object?> values,
            string key)
        {
            return RequireStringValue(RequireValue(values, key), key);
        }

        private static string RequireStringValue(object? value, string label)
        {
            string? result = value as string;
            if (result == null)
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest string is invalid: " + label);
            }

            CanonicalJson.RequireNfcString(result, label);
            return result;
        }

        private static bool RequireBoolean(
            IDictionary<string, object?> values,
            string key)
        {
            object? value = RequireValue(values, key);
            if (!(value is bool))
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest Boolean is invalid.");
            }

            return (bool)value;
        }

        private static Dictionary<string, object?> RequireObject(
            IDictionary<string, object?> values,
            string key)
        {
            return RequireObjectValue(RequireValue(values, key), key);
        }

        private static Dictionary<string, object?> RequireObjectValue(
            object? value,
            string label)
        {
            Dictionary<string, object?>? result = value as Dictionary<string, object?>;
            if (result == null)
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest object is invalid: " + label);
            }

            return result;
        }

        private static List<object?> RequireArray(
            IDictionary<string, object?> values,
            string key)
        {
            object? value = RequireValue(values, key);
            List<object?>? result = value as List<object?>;
            if (result == null)
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest array is invalid: " + key);
            }

            return result;
        }

        private static object? RequireValue(
            IDictionary<string, object?> values,
            string key)
        {
            object? value;
            if (!values.TryGetValue(key, out value))
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest field is missing.");
            }

            return value;
        }

        private static void RequireExactKeys(
            IDictionary<string, object?> values,
            params string[] keys)
        {
            if (values.Count != keys.Length)
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest object has extra fields.");
            }

            for (int index = 0; index < keys.Length; index++)
            {
                if (!values.ContainsKey(keys[index]))
                {
                    throw Failure("LPF_MANIFEST_DOCUMENT", "A manifest object is incomplete.");
                }
            }
        }

        private static void RequireLiteral(
            string value,
            string expected,
            string label)
        {
            if (!string.Equals(value, expected, StringComparison.Ordinal))
            {
                throw Failure("LPF_MANIFEST_DOCUMENT", "A fixed manifest field is invalid: " + label);
            }
        }

        private static bool ByteArraysEqual(byte[] left, byte[] right)
        {
            if (left.Length != right.Length)
            {
                return false;
            }

            int difference = 0;
            for (int index = 0; index < left.Length; index++)
            {
                difference |= left[index] ^ right[index];
            }

            return difference == 0;
        }

        private static AdapterFailureException Failure(string code, string message)
        {
            return new AdapterFailureException(code, message);
        }
    }
}
