// SPDX-License-Identifier: MIT
// Package-free generated console test runner. Returns nonzero on any assertion failure.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using LiangPingfa.NativeCad.Core;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.Core.Tests
{
    internal static class Program
    {
        private static int failures;

        private static int Main(string[] arguments)
        {
            if (arguments.Length != 0)
            {
                return RunCommand(arguments);
            }

            Run("canonical vectors and strict parsing", TestCanonicalVectors);
            Run("path-aware opaque carrier vectors", TestPathAwareOpaqueCarriers);
            Run("syntax-only stub constructors", TestSyntaxOnlyStubs);
            Run("full manifest integrity binding", TestFullManifestIntegrityBinding);
            Run("committed result construction boundary", TestCommittedResultConstructionBoundary);
            Run("geometry binding constructor invariants", TestGeometryBindingConstructorInvariants);
            Run("source binding transition enforcement", TestSourceBindingTransitions);
            Run("stable host binding enforcement", TestStableHostBinding);
            Run("operation result transport budget", TestOperationResultTransportBudget);
            Run("translate delete marker transaction", TestAllOperationsAndReadback);
            Run("multiple markers and preflight atomicity", TestMultipleMarkersAndMixedInvalidPreflight);
            Run("marker reservations survive operation ordering", TestMarkerReservationsSurviveDeletes);
            Run("atomic rollback and fault injection", TestAtomicRollbackAndFaultInjection);
            Run("in-transaction stale-state revalidation", TestInTransactionStaleStateRevalidation);
            Run("transaction disposal ordering", TestTransactionDisposalOrdering);
            Run("owner state protection", TestOwnerStateProtection);
            Run("preflight error codes and binary64 edges", TestPreflightErrorCodes);

            if (failures != 0)
            {
                Console.Error.WriteLine("FAILED: " + failures.ToString(CultureInfo.InvariantCulture) + " generated core test group(s).");
                return 1;
            }

            Console.WriteLine("PASS: all generated native CAD core tests.");
            return 0;
        }

        /// <summary>
        /// Runs a deliberately narrow machine-readable test command. The
        /// Python suite uses this to prove byte-for-byte canonical compatibility
        /// with the built C# implementation rather than comparing two expected
        /// strings independently.
        /// </summary>
        private static int RunCommand(string[] arguments)
        {
            if (arguments.Length == 2 &&
                string.Equals(arguments[0], "canonical", StringComparison.Ordinal))
            {
                return RunCanonicalCommand(arguments[1]);
            }

            if (arguments.Length == 3 &&
                string.Equals(
                    arguments[0],
                    "canonical-profile",
                    StringComparison.Ordinal))
            {
                return RunCanonicalProfileCommand(arguments[1], arguments[2]);
            }

            if (arguments.Length == 2 &&
                string.Equals(
                    arguments[0],
                    "execute-marker-manifest",
                    StringComparison.Ordinal))
            {
                return RunMarkerManifestCommand(arguments[1]);
            }

            if (arguments.Length == 2 &&
                string.Equals(
                    arguments[0],
                    "execute-marker-manifest-readback",
                    StringComparison.Ordinal))
            {
                return RunMarkerManifestReadbackCommand(arguments[1]);
            }

            Console.Error.WriteLine(
                "Usage: LiangPingfa.NativeCad.Core.Tests canonical <json-path> | " +
                "canonical-profile <bridge-response|manifest|console-export> <json-path> | " +
                "execute-marker-manifest <manifest-path> | " +
                "execute-marker-manifest-readback <manifest-path>");
            return 64;
        }

        private static int RunCanonicalCommand(string path)
        {
            try
            {
                byte[] input = File.ReadAllBytes(path);
                object? value = CanonicalJson.ParseUtf8(
                    input,
                    NativeCadProtocolV2.MaxGeometryJsonBytes);
                byte[] canonical = CanonicalJson.SerializeUtf8(value);
                Dictionary<string, object?> response = new Dictionary<string, object?>(
                    StringComparer.Ordinal)
                {
                    { "canonical_json", Encoding.UTF8.GetString(canonical) },
                    { "canonical_sha256", CanonicalJson.Sha256Hex(canonical) },
                };
                Console.WriteLine(CanonicalJson.Serialize(response));
                return 0;
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine(
                    "Canonical JSON rejected: " + exception.Message);
                return 2;
            }
        }

        /// <summary>
        /// Canonicalizes a carrier-bearing native envelope using only its
        /// explicitly selected path profile. The response intentionally emits
        /// a digest and byte count rather than echoing a potentially 16 MiB
        /// carrier through an unrelated outer string field.
        /// </summary>
        private static int RunCanonicalProfileCommand(string profile, string path)
        {
            try
            {
                CanonicalJsonOptions options = ProfileFromName(profile);
                byte[] input = File.ReadAllBytes(path);
                byte[] canonical = CanonicalJson.SerializeUtf8(
                    CanonicalJson.ParseUtf8(
                        input,
                        NativeCadProtocolV2.MaxManifestDocumentBytes,
                        options),
                    options);
                Dictionary<string, object?> response = new Dictionary<string, object?>(
                    StringComparer.Ordinal)
                {
                    { "canonical_sha256", CanonicalJson.Sha256Hex(canonical) },
                    { "canonical_utf8_bytes", (long)canonical.Length },
                };
                Console.WriteLine(CanonicalJson.Serialize(response));
                return 0;
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine(
                    "Canonical profile JSON rejected: " + exception.Message);
                return 2;
            }
        }

        private static CanonicalJsonOptions ProfileFromName(string profile)
        {
            if (string.Equals(profile, "bridge-response", StringComparison.Ordinal))
            {
                return NativeCadCanonicalJsonProfiles.BridgeResponse;
            }

            if (string.Equals(profile, "manifest", StringComparison.Ordinal))
            {
                return NativeCadCanonicalJsonProfiles.Manifest;
            }

            if (string.Equals(profile, "console-export", StringComparison.Ordinal))
            {
                return NativeCadCanonicalJsonProfiles.ConsoleExport;
            }

            throw new CanonicalJsonException("Canonical profile is not allowlisted.");
        }

        /// <summary>
        /// Executes one generated marker-only projection after proving the
        /// original Python private manifest's full self-integrity. This is a
        /// test-only bridge: it exercises the typed core without claiming to
        /// parse arbitrary host manifests or CAD files.
        /// </summary>
        private static int RunMarkerManifestCommand(string path)
        {
            try
            {
                byte[] document = File.ReadAllBytes(path);
                ValidatedFullManifestIntegrityV2 fullIntegrity =
                    ValidatedFullManifestIntegrityV2.FromManifestDocumentUtf8(
                        document,
                        NativeCadProtocolV2.MaxManifestDocumentBytes);
                Dictionary<string, object?> manifest = AsObject(
                    CanonicalJson.ParseUtf8(
                        document,
                        NativeCadProtocolV2.MaxManifestDocumentBytes,
                        NativeCadCanonicalJsonProfiles.Manifest),
                    "full manifest");
                ManifestExecutionResultV2 result = ExecuteMarkerManifest(
                    manifest,
                    fullIntegrity);
                Console.WriteLine(CanonicalJson.Serialize(result.ToWireValue()));
                return 0;
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine(
                    "Full manifest rejected before transaction: " + exception.Message);
                return 2;
            }
        }

        /// <summary>
        /// Executes the same generated marker projection and emits only its
        /// post-save readback envelope. This exercises canonical outer carrier
        /// handling for native-console-export/v2.
        /// </summary>
        private static int RunMarkerManifestReadbackCommand(string path)
        {
            try
            {
                byte[] document = File.ReadAllBytes(path);
                ValidatedFullManifestIntegrityV2 fullIntegrity =
                    ValidatedFullManifestIntegrityV2.FromManifestDocumentUtf8(
                        document,
                        NativeCadProtocolV2.MaxManifestDocumentBytes);
                Dictionary<string, object?> manifest = AsObject(
                    CanonicalJson.ParseUtf8(
                        document,
                        NativeCadProtocolV2.MaxManifestDocumentBytes,
                        NativeCadCanonicalJsonProfiles.Manifest),
                    "full manifest");
                ManifestExecutionResultV2 result = ExecuteMarkerManifest(
                    manifest,
                    fullIntegrity);
                Console.WriteLine(
                    Encoding.UTF8.GetString(
                        result.CreateReadbackExport().ToCanonicalJsonUtf8()));
                return 0;
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine(
                    "Full manifest readback rejected: " + exception.Message);
                return 2;
            }
        }

        private static ManifestExecutionResultV2 ExecuteMarkerManifest(
            Dictionary<string, object?> manifest,
            ValidatedFullManifestIntegrityV2 fullIntegrity)
        {
            Dictionary<string, object?> policyWire = AsObject(
                manifest["marker_policy_binding"],
                "marker policy binding");
            MarkerPolicyBindingV2 policy = MarkerPolicyFromWire(policyWire);
            List<object?> operations = AsArray(manifest["operations"], "manifest operations");
            if (operations.Count != 1)
            {
                throw new CanonicalJsonException(
                    "Marker manifest runner accepts exactly one generated marker operation.");
            }

            Dictionary<string, object?> operation = AsObject(
                operations[0],
                "marker operation");
            if (!string.Equals(
                AsString(operation["kind"], "marker operation kind"),
                "create_review_marker",
                StringComparison.Ordinal))
            {
                throw new CanonicalJsonException(
                    "Marker manifest runner accepts create_review_marker only.");
            }

            CadContainer container = MarkerContainerFromWire(operation);
            long rawSequence = AsInt64(operation["sequence_index"], "marker sequence index");
            if (rawSequence < 1 || rawSequence > 1000000)
            {
                throw new CanonicalJsonException(
                    "Marker manifest runner requires a positive bounded sequence index.");
            }

            int sequence = (int)rawSequence;
            OverlayEvidence evidence = OverlayEvidenceFromWire(
                AsObject(operation["overlay_evidence"], "marker overlay evidence"));
            CreateReviewMarkerOperationV2 marker =
                new CreateReviewMarkerOperationV2(
                    AsString(operation["operation_id"], "marker operation ID"),
                    AsString(operation["owner_handle"], "marker owner handle"),
                    container,
                    sequence,
                    VectorFromWire(
                        AsArray(operation["position"], "marker position"),
                        "marker position"),
                    AsString(operation["marker_text"], "marker text"),
                    AsString(operation["marker_fingerprint"], "marker fingerprint"),
                    AsString(operation["layer"], "marker layer"),
                    AsString(operation["style"], "marker style"),
                    AsString(operation["height"], "marker height"),
                    AsString(operation["rotation"], "marker rotation"),
                    evidence);

            Dictionary<string, object?> preconditions = AsObject(
                NativeGeometryJsonV2.RequireCanonicalGeometryCarrier(
                    AsString(
                        manifest["preconditions_geometry_json"],
                        "preconditions geometry")),
                "preconditions geometry");
            NativeSourceBindingV2 prewriteSource = SourceBindingFromWire(
                AsObject(preconditions["source"], "prewrite source"));
            NativeGeometryBindingContextV2 bindingContext =
                BindingContextFromWire(
                    AsObject(preconditions["binding"], "prewrite binding"));
            CadDocumentSnapshot snapshot = CreateSyntheticMarkerSnapshot(
                container,
                sequence,
                policy,
                marker.OwnerHandle,
                prewriteSource,
                bindingContext);
            GeometryExportV2 before = ExactCadExporter.Export(snapshot);
            CoreManifestV2 coreManifest = new CoreManifestV2(
                AsString(manifest["manifest_id"], "manifest ID"),
                fullIntegrity,
                AsString(manifest["nonce"], "manifest nonce"),
                before,
                ExpectedPrewriteRevisionV2.From(before),
                SourceBindingFromWire(
                    AsObject(
                        manifest["expected_prewrite_output_copy_binding"],
                        "expected prewrite output binding")),
                FinalOutputConstraintsFromWire(
                    AsObject(
                        manifest["final_output_constraints"],
                        "final output constraints")),
                AsString(
                    manifest["stable_host_binding_digest"],
                    "stable host binding digest"),
                policy,
                new ManifestOperationV2[] { marker });
            return new ManifestExecutor().Execute(
                new InMemoryCadDatabase(snapshot),
                coreManifest);
        }

        private static MarkerPolicyBindingV2 MarkerPolicyFromWire(
            Dictionary<string, object?> policy)
        {
            Dictionary<string, object?> defaults = AsObject(
                policy["geometry_defaults"],
                "marker geometry defaults");
            return new MarkerPolicyBindingV2(
                AsBoolean(policy["profile_enabled"], "marker profile enabled"),
                AsBoolean(policy["enabled"], "marker enabled"),
                AsBoolean(policy["plugin_capability"], "marker capability"),
                AsString(policy["layer"], "marker layer"),
                AsString(policy["style"], "marker style"),
                AsString(policy["layer_fingerprint"], "marker layer fingerprint"),
                AsString(policy["style_fingerprint"], "marker style fingerprint"),
                AsString(policy["height_bits"], "marker height bits"),
                AsString(policy["rotation_bits"], "marker rotation bits"),
                OverlayEvidenceFromWire(
                    AsObject(
                        defaults["overlay_evidence"],
                        "marker default overlay evidence")));
        }

        private static NativeSourceBindingV2 SourceBindingFromWire(
            Dictionary<string, object?> source)
        {
            return new NativeSourceBindingV2(
                AsString(source["sha256"], "source SHA-256"),
                AsInt64(source["byte_size"], "source byte size"),
                AsString(source["path_fingerprint"], "source path fingerprint"),
                AsString(
                    source["file_identity_fingerprint"],
                    "source file identity fingerprint"),
                AsString(source["dwg_header_signature"], "source DWG header"));
        }

        private static FinalOutputConstraintsV2 FinalOutputConstraintsFromWire(
            Dictionary<string, object?> constraints)
        {
            string policy = AsString(
                constraints["file_identity_transition_policy"],
                "file identity transition policy");
            FileIdentityTransitionPolicyV2 transition =
                string.Equals(policy, "same_identity_required", StringComparison.Ordinal)
                    ? FileIdentityTransitionPolicyV2.SameIdentityRequired
                    : string.Equals(policy, "replacement_allowed", StringComparison.Ordinal)
                        ? FileIdentityTransitionPolicyV2.ReplacementAllowed
                        : throw new CanonicalJsonException(
                            "Unknown final output identity-transition policy.");
            if (!AsBoolean(
                    constraints["require_same_volume_as_prewrite"],
                    "same-volume constraint") ||
                !AsBoolean(
                    constraints["require_within_private_root"],
                    "private-root constraint"))
            {
                throw new CanonicalJsonException(
                    "Final output path constraints must remain fail-closed.");
            }

            return new FinalOutputConstraintsV2(
                AsString(
                    constraints["authorized_private_path_fingerprint"],
                    "authorized private output path"),
                AsString(
                    constraints["authorized_private_root_fingerprint"],
                    "authorized private output root"),
                AsString(
                    constraints["required_dwg_header_signature"],
                    "required DWG header"),
                AsString(
                    constraints["required_dwg_version"],
                    "required DWG version"),
                AsInt64(constraints["max_byte_size"], "maximum output bytes"),
                transition);
        }

        private static NativeGeometryBindingContextV2 BindingContextFromWire(
            Dictionary<string, object?> binding)
        {
            Dictionary<string, object?> host = AsObject(binding["host"], "host binding");
            Dictionary<string, object?> process = AsObject(
                binding["process"],
                "process binding");
            Dictionary<string, object?> adapter = AsObject(
                binding["adapter"],
                "adapter binding");
            Dictionary<string, object?> plugin = AsObject(
                binding["plugin"],
                "plugin binding");
            List<object?> rawCapabilities = AsArray(
                binding["capabilities"],
                "binding capabilities");
            List<string> capabilities = new List<string>();
            for (int index = 0; index < rawCapabilities.Count; index++)
            {
                capabilities.Add(AsString(
                    rawCapabilities[index],
                    "binding capability"));
            }

            return new NativeGeometryBindingContextV2(
                AsString(binding["session_id"], "binding session ID"),
                AsString(adapter["id"], "adapter ID"),
                AsString(adapter["profile"], "adapter profile"),
                AsString(adapter["version"], "adapter version"),
                AsString(plugin["id"], "plugin ID"),
                AsString(plugin["version"], "plugin version"),
                AsString(plugin["fingerprint"], "plugin fingerprint"),
                capabilities,
                AsString(host["product"], "host product"),
                AsString(host["release"], "host release"),
                AsString(host["runtime"], "host runtime"),
                AsString(host["mode"], "host mode"),
                AsInt64(process["pid"], "process PID"),
                AsInt64(process["windows_session_id"], "Windows session ID"),
                AsString(
                    process["instance_fingerprint"],
                    "process instance fingerprint"),
                AsString(
                    process["creation_time_100ns"],
                    "process creation time"),
                AsString(
                    process["executable_fingerprint"],
                    "host executable fingerprint"));
        }

        private static CadContainer MarkerContainerFromWire(
            Dictionary<string, object?> operation)
        {
            Dictionary<string, object?> space = AsObject(
                operation["space"],
                "marker space");
            if (!string.Equals(
                AsString(space["kind"], "marker space kind"),
                "modelspace",
                StringComparison.Ordinal) ||
                space["block_handle"] != null)
            {
                throw new CanonicalJsonException(
                    "Marker manifest runner requires direct Modelspace.");
            }

            List<object?> rawPath = AsArray(operation["block_path"], "marker block path");
            List<string> path = new List<string>();
            for (int index = 0; index < rawPath.Count; index++)
            {
                path.Add(AsString(rawPath[index], "marker block path item"));
            }

            return new CadContainer(
                NativeSpaceKind.Modelspace,
                AsString(space["layout_handle"], "marker layout handle"),
                null,
                path);
        }

        private static CadDocumentSnapshot CreateSyntheticMarkerSnapshot(
            CadContainer container,
            int markerSequence,
            MarkerPolicyBindingV2 policy,
            string ownerHandle,
            NativeSourceBindingV2 source,
            NativeGeometryBindingContextV2 bindingContext)
        {
            OverlayEvidence fillerEvidence =
                new OverlayEvidence(false, false, false, false, true);
            CadEntitySnapshot filler = new CadEntitySnapshot(
                "10",
                NativeEntityKind.DbText,
                ownerHandle,
                container,
                markerSequence - 1,
                policy.Layer,
                "generated-marker-runner",
                policy.Style,
                policy.HeightBits,
                policy.RotationBits,
                Vector(0d, 0d, 0d),
                Bounds(0d, 0d, 0d, 0d),
                new CadSegment[0],
                fillerEvidence);
            Dictionary<string, string> layers = new Dictionary<string, string>(
                StringComparer.Ordinal)
            {
                { policy.Layer, policy.LayerFingerprint },
            };
            Dictionary<string, string> styles = new Dictionary<string, string>(
                StringComparer.Ordinal)
            {
                { policy.Style, policy.StyleFingerprint },
            };
            CadDocumentTables tables = new CadDocumentTables(
                Digest("marker-runner-tables"),
                Digest("marker-runner-layouts"),
                Digest("marker-runner-blocks"),
                policy.LayerFingerprint,
                policy.StyleFingerprint,
                layers,
                styles);
            return new CadDocumentSnapshot(
                Digest("marker-runner-database"),
                Digest("marker-runner-revision"),
                new[] { ownerHandle },
                new[] { filler },
                tables,
                source,
                bindingContext);
        }

        private static Binary64Vector VectorFromWire(
            List<object?> values,
            string label)
        {
            if (values.Count != 3)
            {
                throw new CanonicalJsonException(label + " must contain three axes.");
            }

            return new Binary64Vector(
                AsString(values[0], label + " x"),
                AsString(values[1], label + " y"),
                AsString(values[2], label + " z"));
        }

        private static OverlayEvidence OverlayEvidenceFromWire(
            Dictionary<string, object?> evidence)
        {
            return new OverlayEvidence(
                AsBoolean(evidence["unique_content"], "overlay unique content"),
                AsBoolean(evidence["left_panel"], "overlay left panel"),
                AsBoolean(
                    evidence["corresponding_right_absent"],
                    "overlay right absence"),
                AsBoolean(
                    evidence["visible_interference"],
                    "overlay visible interference"),
                AsBoolean(evidence["unsupported_data"], "overlay unsupported data"));
        }

        private static void Run(string name, Action test)
        {
            try
            {
                test();
                Console.WriteLine("PASS: " + name);
            }
            catch (Exception exception)
            {
                failures++;
                Console.Error.WriteLine("FAIL: " + name + " :: " + exception);
            }
        }

        private static void TestCanonicalVectors()
        {
            string path = Path.GetFullPath(
                Path.Combine("native-cad", "tests", "fixtures", "native-cad-v2-golden.json"),
                Environment.CurrentDirectory);
            Assert(File.Exists(path), "Golden fixture is present.");
            object? raw = CanonicalJson.Parse(File.ReadAllText(path));
            Dictionary<string, object?> fixture = AsObject(raw, "fixture");
            AssertEqual("native-cad-v2", AsString(fixture["fixture_version"], "fixture version"), "fixture version");
            AssertEqual(true, AsBoolean(fixture["source_free"], "source_free"), "fixture privacy flag");
            Dictionary<string, object?> writeVersions = AsObject(
                fixture["mutable_write_artifact_versions"],
                "mutable write artifact versions");
            AssertEqual(
                NativeCadProtocolV2.ManifestSchemaVersion,
                AsString(writeVersions["manifest"], "v2 manifest schema"),
                "cross-language v2 manifest namespace");
            AssertEqual(
                NativeCadProtocolV2.ConsoleResultSchemaVersion,
                AsString(writeVersions["console_result"], "v2 result schema"),
                "cross-language v2 result namespace");
            AssertEqual(
                NativeCadProtocolV2.ConsoleExportSchemaVersion,
                AsString(writeVersions["console_export"], "v2 export schema"),
                "cross-language v2 export namespace");
            AssertEqual(
                NativeCadProtocolV2.VerificationSchemaVersion,
                AsString(writeVersions["verification"], "v2 verification schema"),
                "cross-language v2 verification namespace");
            Dictionary<string, object?> payload = AsObject(fixture["canonical_payload"], "canonical payload");
            string canonical = AsString(fixture["canonical_json"], "canonical JSON");
            AssertEqual(canonical, CanonicalJson.Serialize(payload), "cross-language canonical JSON");
            AssertEqual(
                AsString(fixture["canonical_sha256"], "canonical hash"),
                CanonicalJson.Sha256Hex(payload),
                "cross-language canonical SHA-256");

            List<object?> canonicalVectors = AsArray(
                fixture["canonical_vectors"],
                "canonical vectors");
            for (int index = 0; index < canonicalVectors.Count; index++)
            {
                Dictionary<string, object?> vector = AsObject(
                    canonicalVectors[index],
                    "canonical vector");
                object? vectorPayload = vector["payload"];
                AssertEqual(
                    AsString(vector["canonical_json"], "vector canonical JSON"),
                    CanonicalJson.Serialize(vectorPayload),
                    "canonical vector JSON " + index.ToString(CultureInfo.InvariantCulture));
                AssertEqual(
                    AsString(vector["canonical_sha256"], "vector canonical hash"),
                    CanonicalJson.Sha256Hex(vectorPayload),
                    "canonical vector hash " + index.ToString(CultureInfo.InvariantCulture));
            }

            TestCanonicalDepthVectors(
                AsArray(
                    fixture["canonical_depth_vectors"],
                    "canonical depth vectors"));

            List<object?> rejectedNumberTokens = AsArray(
                fixture["rejected_number_tokens"],
                "rejected number tokens");
            for (int index = 0; index < rejectedNumberTokens.Count; index++)
            {
                string token = AsString(rejectedNumberTokens[index], "rejected number token");
                AssertThrows<CanonicalJsonException>(
                    delegate { CanonicalJson.Parse(token); },
                    "rejected JSON number " + token);
            }

            List<object?> vectors = AsArray(fixture["binary64_vectors"], "binary64 vectors");
            for (int index = 0; index < vectors.Count; index++)
            {
                Dictionary<string, object?> vector = AsObject(vectors[index], "binary64 vector");
                AssertEqual(
                    AsString(vector["translated"], "translated"),
                    Binary64.Translate(
                        AsString(vector["original"], "original"),
                        AsString(vector["delta"], "delta")),
                    "binary64 golden vector " + index.ToString(CultureInfo.InvariantCulture));
            }

            Dictionary<string, object?> limits = AsObject(fixture["limits"], "limits");
            AssertEqual((long)NativeCadProtocolV2.MaxGeometryEntities, AsInt64(limits["max_geometry_entities"], "entity limit"), "entity limit");
            AssertEqual((long)NativeCadProtocolV2.MaxGeometrySegments, AsInt64(limits["max_geometry_segments"], "segment limit"), "segment limit");
            AssertEqual((long)NativeCadProtocolV2.MaxGeometryJsonBytes, AsInt64(limits["max_geometry_json_bytes"], "byte limit"), "geometry byte limit");
            AssertEqual((long)CanonicalJson.MaxNestingDepth, AsInt64(limits["max_json_nesting_depth"], "nesting limit"), "nesting limit");
            AssertEqual((long)NativeCadProtocolV2.MaxNativeOperations, AsInt64(limits["max_native_operations"], "operation limit"), "operation limit");
            AssertEqual((long)NativeCadProtocolV2.MaxConsoleResultBytes, AsInt64(limits["max_console_result_bytes"], "console result byte limit"), "console result byte limit");
            AssertEqual((long)NativeCadProtocolV2.MaxConsoleResultCanonicalBytes, AsInt64(limits["max_console_result_canonical_bytes"], "canonical result byte limit"), "canonical result byte limit");

            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.Parse("{\"a\":1,\"a\":2}"); },
                "duplicate JSON keys reject");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CanonicalJson.ParseUtf8(
                        new byte[] { 0xef, 0xbb, 0xbf, 0x7b, 0x7d },
                        NativeCadProtocolV2.MaxGeometryJsonBytes);
                },
                "UTF-8 BOM rejects");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CanonicalJson.ParseUtf8(
                        new byte[] { 0xc3, 0x28 },
                        NativeCadProtocolV2.MaxGeometryJsonBytes);
                },
                "invalid UTF-8 rejects");
            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.RequireCanonicalText("{\"b\":1,\"a\":2}"); },
                "non-sorted JSON rejects");
            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.Parse("-0"); },
                "negative zero rejects");
            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.Parse("1.0"); },
                "fractional JSON number rejects");
            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.Parse("1e-7"); },
                "exponent JSON number rejects");
            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.Serialize(-0d); },
                "CLR negative zero rejects");
            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.Serialize(1m); },
                "CLR decimal rejects");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CanonicalJson.RequireNfcString(
                        new string(new[] { 'e', '\u0301' }),
                        "decomposed");
                },
                "non-NFC string rejects");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CanonicalJson.Serialize(
                        new string(new[] { '\ud800' }));
                },
                "unpaired surrogate rejects");
            AssertThrows<CanonicalJsonException>(
                delegate { Binary64.ParseBits("8000000000000000"); },
                "negative zero rejects");
            AssertThrows<CanonicalJsonException>(
                delegate { Binary64.Translate(Binary64.ToBits(1e300), Binary64.ToBits(1d)); },
                "nonzero rounded binary64 no-op rejects");
        }

        private static void TestCanonicalDepthVectors(
            List<object?> vectors)
        {
            for (int index = 0; index < vectors.Count; index++)
            {
                Dictionary<string, object?> vector = AsObject(
                    vectors[index],
                    "canonical depth vector");
                string name = AsString(vector["name"], "canonical depth vector name");
                string shape = AsString(vector["shape"], "canonical depth vector shape");
                long rawDepth = AsInt64(
                    vector["depth"],
                    "canonical depth vector depth");
                if (rawDepth < 1 || rawDepth > int.MaxValue)
                {
                    throw new InvalidOperationException(
                        "Canonical depth vector has an invalid depth.");
                }

                int depth = (int)rawDepth;
                bool accepted = AsBoolean(
                    vector["accepted"],
                    "canonical depth vector acceptance");
                string text = BuildCanonicalDepthJson(shape, depth);
                object? value = BuildCanonicalDepthValue(shape, depth);
                if (accepted)
                {
                    object? parsed = CanonicalJson.Parse(text);
                    AssertEqual(
                        text,
                        CanonicalJson.Serialize(parsed),
                        name + " parser accepts and canonicalizes");
                    AssertEqual(
                        text,
                        CanonicalJson.Serialize(value),
                        name + " serializer accepts");
                }
                else
                {
                    AssertThrows<CanonicalJsonException>(
                        delegate { CanonicalJson.Parse(text); },
                        name + " parser rejects");
                    AssertThrows<CanonicalJsonException>(
                        delegate { CanonicalJson.Serialize(value); },
                        name + " serializer rejects");
                }

                // Opaque carriers retain the exact inner bytes at the outer
                // profile boundary. Whenever their inner JSON is decoded, it
                // enters the same canonical parser and therefore receives the
                // identical 128-container cap.
                Dictionary<string, object?> outer =
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "preconditions_geometry_json", text },
                    };
                string outerText = CanonicalJson.Serialize(
                    outer,
                    NativeCadCanonicalJsonProfiles.Manifest);
                Dictionary<string, object?> restoredOuter = AsObject(
                    CanonicalJson.RequireCanonicalText(
                        outerText,
                        NativeCadCanonicalJsonProfiles.Manifest),
                    name + " opaque outer carrier");
                string restoredInner = AsString(
                    restoredOuter["preconditions_geometry_json"],
                    name + " opaque inner carrier");
                AssertEqual(text, restoredInner, name + " opaque carrier is exact");
                if (accepted)
                {
                    AssertEqual(
                        text,
                        CanonicalJson.Serialize(CanonicalJson.Parse(restoredInner)),
                        name + " opaque inner parser accepts");
                }
                else
                {
                    AssertThrows<CanonicalJsonException>(
                        delegate { CanonicalJson.Parse(restoredInner); },
                        name + " opaque inner parser rejects");
                }
            }
        }

        private static string BuildCanonicalDepthJson(string shape, int depth)
        {
            if (depth < 1)
            {
                throw new ArgumentOutOfRangeException(nameof(depth));
            }

            string value;
            if (string.Equals(shape, "empty-arrays", StringComparison.Ordinal))
            {
                value = "[]";
                for (int index = 1; index < depth; index++)
                {
                    value = "[" + value + "]";
                }

                return value;
            }

            if (string.Equals(shape, "empty-objects", StringComparison.Ordinal))
            {
                value = "{}";
                for (int index = 1; index < depth; index++)
                {
                    value = "{\"node\":" + value + "}";
                }

                return value;
            }

            if (string.Equals(shape, "mixed-containers", StringComparison.Ordinal))
            {
                value = "[]";
                for (int index = 0; index < depth - 1; index++)
                {
                    value = index % 2 == 0
                        ? "{\"node\":" + value + "}"
                        : "[" + value + "]";
                }

                return value;
            }

            if (string.Equals(shape, "scalar-leaves", StringComparison.Ordinal))
            {
                value = "0";
                for (int index = 0; index < depth; index++)
                {
                    value = index % 2 == 0
                        ? "{\"node\":" + value + "}"
                        : "[" + value + "]";
                }

                return value;
            }

            throw new CanonicalJsonException(
                "Canonical depth vector shape is not allowlisted.");
        }

        private static object? BuildCanonicalDepthValue(string shape, int depth)
        {
            if (depth < 1)
            {
                throw new ArgumentOutOfRangeException(nameof(depth));
            }

            object? value;
            if (string.Equals(shape, "empty-arrays", StringComparison.Ordinal))
            {
                value = new List<object?>();
                for (int index = 1; index < depth; index++)
                {
                    value = new List<object?> { value };
                }

                return value;
            }

            if (string.Equals(shape, "empty-objects", StringComparison.Ordinal))
            {
                value = new Dictionary<string, object?>(StringComparer.Ordinal);
                for (int index = 1; index < depth; index++)
                {
                    value = new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "node", value },
                    };
                }

                return value;
            }

            if (string.Equals(shape, "mixed-containers", StringComparison.Ordinal))
            {
                value = new List<object?>();
                for (int index = 0; index < depth - 1; index++)
                {
                    value = index % 2 == 0
                        ? new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "node", value },
                        }
                        : new List<object?> { value };
                }

                return value;
            }

            if (string.Equals(shape, "scalar-leaves", StringComparison.Ordinal))
            {
                value = 0L;
                for (int index = 0; index < depth; index++)
                {
                    value = index % 2 == 0
                        ? new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "node", value },
                        }
                        : new List<object?> { value };
                }

                return value;
            }

            throw new CanonicalJsonException(
                "Canonical depth vector shape is not allowlisted.");
        }

        private static void TestPathAwareOpaqueCarriers()
        {
            const string combiningCarrier = "\"\u0344\"";
            Dictionary<string, object?> manifest =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "preconditions_geometry_json", combiningCarrier },
                };
            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.Serialize(manifest); },
                "ordinary serializer rejects non-NFC opaque-looking string");
            string canonical = CanonicalJson.Serialize(
                manifest,
                NativeCadCanonicalJsonProfiles.Manifest);
            Dictionary<string, object?> parsed = AsObject(
                CanonicalJson.RequireCanonicalText(
                    canonical,
                    NativeCadCanonicalJsonProfiles.Manifest),
                "opaque manifest");
            AssertEqual(
                combiningCarrier,
                AsString(
                    parsed["preconditions_geometry_json"],
                    "opaque manifest carrier"),
                "exact opaque carrier remains unchanged");
            AssertEqual(
                CanonicalJson.Sha256Hex(Encoding.UTF8.GetBytes(canonical)),
                CanonicalJson.Sha256Hex(
                    manifest,
                    NativeCadCanonicalJsonProfiles.Manifest),
                "opaque carrier contributes exact canonical hash bytes");

            Dictionary<string, object?> wrongPath =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    {
                        "nested",
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "preconditions_geometry_json", combiningCarrier },
                        }
                    },
                };
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CanonicalJson.Serialize(
                        wrongPath,
                        NativeCadCanonicalJsonProfiles.Manifest);
                },
                "same carrier key at an unapproved manifest path remains ordinary");

            Dictionary<string, object?> wrongResponsePath =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    {
                        "result",
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            {
                                "nested",
                                new Dictionary<string, object?>(StringComparer.Ordinal)
                                {
                                    { "geometry_json", combiningCarrier },
                                }
                            },
                        }
                    },
                };
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CanonicalJson.Serialize(
                        wrongResponsePath,
                        NativeCadCanonicalJsonProfiles.BridgeResponse);
                },
                "same bridge geometry key at an unapproved path remains ordinary");

            Dictionary<string, object?> ordinaryLong =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "ordinary", new string('a', CanonicalJson.MaxStringUtf8Bytes + 1) },
                };
            AssertThrows<CanonicalJsonException>(
                delegate { CanonicalJson.Serialize(ordinaryLong); },
                "ordinary 65537-byte string rejects");

            string raw65537 = new string('a', CanonicalJson.MaxStringUtf8Bytes + 1);
            Dictionary<string, object?> carrier65537 =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "preconditions_geometry_json", raw65537 },
                };
            CanonicalJson.Serialize(
                carrier65537,
                NativeCadCanonicalJsonProfiles.Manifest);

            string exactCap = new string(
                'a',
                NativeCadProtocolV2.MaxGeometryJsonBytes);
            Dictionary<string, object?> atCap =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "preconditions_geometry_json", exactCap },
                };
            CanonicalJson.Serialize(
                atCap,
                NativeCadCanonicalJsonProfiles.Manifest);
            Dictionary<string, object?> aboveCap =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    {
                        "preconditions_geometry_json",
                        exactCap + "a"
                    },
                };
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CanonicalJson.Serialize(
                        aboveCap,
                        NativeCadCanonicalJsonProfiles.Manifest);
                },
                "opaque carrier above 16 MiB rejects");

            const string chineseAstralCombiningCarrier = "\"中文😀\u0344\"";
            Dictionary<string, object?> unicodeCarrier =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "geometry_json", chineseAstralCombiningCarrier },
                };
            string unicodeCanonical = CanonicalJson.Serialize(
                unicodeCarrier,
                NativeCadCanonicalJsonProfiles.ConsoleExport);
            Dictionary<string, object?> parsedUnicodeCarrier = AsObject(
                CanonicalJson.RequireCanonicalText(
                    unicodeCanonical,
                    NativeCadCanonicalJsonProfiles.ConsoleExport),
                "opaque Unicode console export");
            AssertEqual(
                chineseAstralCombiningCarrier,
                AsString(parsedUnicodeCarrier["geometry_json"], "opaque Unicode carrier"),
                "Chinese, four-byte, and combining carrier code points are exact");

            Dictionary<string, object?> bridgeResponse =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    {
                        "result",
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "geometry_json", chineseAstralCombiningCarrier },
                            { "inventory_json", "[]" },
                        }
                    },
                };
            Dictionary<string, object?> parsedBridgeResponse = AsObject(
                CanonicalJson.RequireCanonicalText(
                    CanonicalJson.Serialize(
                        bridgeResponse,
                        NativeCadCanonicalJsonProfiles.BridgeResponse),
                    NativeCadCanonicalJsonProfiles.BridgeResponse),
                "opaque bridge response");
            Dictionary<string, object?> parsedBridgeResult = AsObject(
                parsedBridgeResponse["result"],
                "opaque bridge result");
            AssertEqual(
                chineseAstralCombiningCarrier,
                AsString(parsedBridgeResult["geometry_json"], "opaque bridge geometry"),
                "bridge geometry carrier uses its exact approved path");
            Dictionary<string, object?> oversizedInventory =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    {
                        "result",
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            {
                                "inventory_json",
                                new string(
                                    'a',
                                    NativeCadProtocolV2.MaxInventoryJsonBytes + 1)
                            },
                        }
                    },
                };
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CanonicalJson.Serialize(
                        oversizedInventory,
                        NativeCadCanonicalJsonProfiles.BridgeResponse);
                },
                "bridge inventory carrier above 64 KiB rejects");

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    NativeGeometryJsonV2.RequireCanonicalGeometryCarrier(
                        chineseAstralCombiningCarrier);
                },
                "opaque carrier still undergoes separate strict inner JSON validation");

            Dictionary<string, object?> malformedGeometry =
                ExactCadExporter.Export(CreateFixture().Snapshot).ToWireValue();
            malformedGeometry["source"] =
                new Dictionary<string, object?>(StringComparer.Ordinal);
            malformedGeometry["integrity"] =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "algorithm", "SHA-256" },
                    { "sha256", CanonicalJson.Sha256Hex(
                        PayloadWithoutIntegrity(malformedGeometry)) },
                };
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    NativeGeometryJsonV2.RequireCanonicalGeometryCarrier(
                        CanonicalJson.Serialize(malformedGeometry));
                },
                "opaque carrier must satisfy its independent geometry schema");
        }

        /// <summary>
        /// Loads the ephemeral syntax artifact only in this test runner and
        /// proves no public reference-type stub can silently instantiate.
        /// The runner output itself still carries no stub DLL.
        /// </summary>
        private static void TestSyntaxOnlyStubs()
        {
            string path = Path.GetFullPath(
                Path.Combine(
                    "native-cad",
                    "src",
                    "LiangPingfa.NativeCad.AutoCAD.ApiStubs",
                    "bin",
                    "Release",
                    "netstandard2.0",
                    "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll"),
                Environment.CurrentDirectory);
            Assert(File.Exists(path), "syntax-only stub build artifact exists for reflection test");
            Assembly assembly = Assembly.LoadFrom(path);
            Type application = Require(
                assembly.GetType("Autodesk.AutoCAD.ApplicationServices.Application"),
                "syntax-only Application type");
            Assert(
                application.IsAbstract && application.IsSealed,
                "static application facade is non-instantiable");

            foreach (Type type in assembly.GetExportedTypes())
            {
                if (!type.IsClass || type.IsAbstract)
                {
                    continue;
                }

                ConstructorInfo[] constructors = type.GetConstructors(
                    BindingFlags.Public | BindingFlags.Instance);
                Assert(
                    constructors.Length != 0,
                    "instantiable syntax stub declares an explicit public constructor: " +
                    type.FullName);
                foreach (ConstructorInfo constructor in constructors)
                {
                    object?[] arguments = ConstructorArguments(constructor);
                    bool threw = false;
                    try
                    {
                        constructor.Invoke(arguments);
                    }
                    catch (TargetInvocationException exception)
                    {
                        Assert(
                            exception.InnerException is NotSupportedException,
                            "syntax-stub constructor throws NotSupportedException: " +
                            type.FullName);
                        threw = true;
                    }

                    Assert(
                        threw,
                        "syntax-stub constructor cannot silently instantiate: " +
                        type.FullName);
                }
            }

            Type objectId = Require(
                assembly.GetType("Autodesk.AutoCAD.DatabaseServices.ObjectId"),
                "syntax-only ObjectId value type");
            Assert(
                objectId.IsValueType && Activator.CreateInstance(objectId) != null,
                "value-type stub default construction is the documented non-throwing boundary");
        }

        /// <summary>
        /// Compile-only future-adapter syntax proof. This method remains
        /// uncalled so production output does not load the syntax stub.
        /// </summary>
        private static void CompileAgainstThrowingStubSignatures()
        {
            Type dbText = typeof(Autodesk.AutoCAD.DatabaseServices.DBText);
            Type command = typeof(Autodesk.AutoCAD.Runtime.CommandMethodAttribute);
            Autodesk.AutoCAD.DatabaseServices.ObjectId objectId = default;
            object[] syntaxOnlyConstruction = new object[]
            {
                new Autodesk.AutoCAD.DatabaseServices.DBText(),
                new Autodesk.AutoCAD.DatabaseServices.Database(),
                new Autodesk.AutoCAD.ApplicationServices.DocumentCollection(),
                new Autodesk.AutoCAD.Runtime.CommandMethodAttribute("generated"),
                new Autodesk.AutoCAD.DatabaseServices.Handle(1),
            };
            Assert(dbText != null && command != null && objectId.Equals(default(
                Autodesk.AutoCAD.DatabaseServices.ObjectId)) &&
                syntaxOnlyConstruction.Length == 5, "compile-only syntax boundary");
        }

        private static object?[] ConstructorArguments(ConstructorInfo constructor)
        {
            ParameterInfo[] parameters = constructor.GetParameters();
            object?[] values = new object?[parameters.Length];
            for (int index = 0; index < parameters.Length; index++)
            {
                Type type = parameters[index].ParameterType;
                if (type == typeof(string))
                {
                    values[index] = "generated";
                }
                else if (type == typeof(Type))
                {
                    values[index] = typeof(Program);
                }
                else if (type.IsEnum)
                {
                    values[index] = Enum.ToObject(type, 0);
                }
                else if (type.IsValueType)
                {
                    values[index] = Activator.CreateInstance(type);
                }
                else
                {
                    values[index] = null;
                }
            }

            return values;
        }

        private static void TestFullManifestIntegrityBinding()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            ValidatedFullManifestIntegrityV2 firstFull =
                CreateValidatedFullManifestIntegrity("full-manifest-one");
            ValidatedFullManifestIntegrityV2 secondFull =
                CreateValidatedFullManifestIntegrity("full-manifest-two");
            CoreManifestV2 first = CreateManifest(
                fixture,
                before,
                new ManifestOperationV2[] { CreateMarker(
                    fixture,
                    OperationId('e'),
                    fixture.MarkerPolicy.DeriveMarkerText(OperationId('e'))) },
                firstFull);
            CoreManifestV2 second = CreateManifest(
                fixture,
                before,
                new ManifestOperationV2[] { CreateMarker(
                    fixture,
                    OperationId('e'),
                    fixture.MarkerPolicy.DeriveMarkerText(OperationId('e'))) },
                secondFull);

            AssertEqual(
                firstFull.Sha256,
                first.FullManifestIntegritySha256,
                "core retains validated full-manifest integrity");
            Assert(
                !string.Equals(
                    first.FullManifestIntegritySha256,
                    first.CoreProjectionIntegritySha256,
                    StringComparison.Ordinal),
                "full integrity is not substituted with projection integrity");
            AssertEqual(
                first.CoreProjectionIntegritySha256,
                second.CoreProjectionIntegritySha256,
                "projection integrity remains independent of full envelope");
            Assert(
                !string.Equals(
                    first.FullManifestIntegritySha256,
                    second.FullManifestIntegritySha256,
                    StringComparison.Ordinal),
                "distinct full envelopes retain distinct integrity");

            ManifestExecutionResultV2 result = new ManifestExecutor().Execute(
                new InMemoryCadDatabase(fixture.Snapshot),
                second);
            AssertEqual(
                secondFull.Sha256,
                AsString(
                    result.ToWireValue()["manifest_integrity_sha256"],
                    "wire manifest integrity"),
                "console result emits full manifest integrity");
            Assert(
                !string.Equals(
                    second.CoreProjectionIntegritySha256,
                    AsString(
                        result.ToWireValue()["manifest_integrity_sha256"],
                        "wire manifest integrity"),
                    StringComparison.Ordinal),
                "console result never emits the projection hash");

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    ValidatedFullManifestIntegrityV2.FromManifestDocumentUtf8(
                        CanonicalJson.SerializeUtf8(
                            new Dictionary<string, object?>(StringComparer.Ordinal)
                            {
                                { "schema_version", NativeCadProtocolV2.ManifestSchemaVersion },
                            }),
                        NativeCadProtocolV2.MaxGeometryJsonBytes);
                },
                "missing full manifest integrity rejects before transaction");

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    Dictionary<string, object?> malformed =
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "schema_version", NativeCadProtocolV2.ManifestSchemaVersion },
                            {
                                "integrity",
                                new Dictionary<string, object?>(StringComparer.Ordinal)
                                {
                                    { "algorithm", "SHA-1" },
                                    { "sha256", new string('a', 64) },
                                }
                            },
                        };
                    ValidatedFullManifestIntegrityV2.FromManifestDocumentUtf8(
                        CanonicalJson.SerializeUtf8(malformed),
                        NativeCadProtocolV2.MaxGeometryJsonBytes);
                },
                "malformed full manifest integrity rejects before transaction");

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    Dictionary<string, object?> malformed =
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "schema_version", NativeCadProtocolV2.ManifestSchemaVersion },
                            {
                                "integrity",
                                new Dictionary<string, object?>(StringComparer.Ordinal)
                                {
                                    { "algorithm", "SHA-256" },
                                    { "sha256", new string('0', 64) },
                                }
                            },
                        };
                    ValidatedFullManifestIntegrityV2.FromManifestDocumentUtf8(
                        CanonicalJson.SerializeUtf8(malformed),
                        NativeCadProtocolV2.MaxGeometryJsonBytes);
                },
                "mismatched full manifest integrity rejects before transaction");
        }

        private static void TestCommittedResultConstructionBoundary()
        {
            AssertPublicSuccessTypeHasNoCreationPath(typeof(ManifestExecutionResultV2));
            AssertPublicSuccessTypeHasNoCreationPath(typeof(NativeConsoleExportV2));

            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            ManifestExecutionResultV2 result = new ManifestExecutor().Execute(
                new InMemoryCadDatabase(fixture.Snapshot),
                CreateFullManifest(fixture, before));
            AssertEqual(
                FinalRevisionTransitionV2.SaveReopenChanged,
                result.FinalRevisionTransition,
                "executor remains the positive committed-result path");
            NativeGeometryJsonV2.RequireCanonicalGeometryCarrier(
                Encoding.UTF8.GetString(result.FinalExport.ToCanonicalJsonUtf8()));
            Assert(
                result.CreateReadbackExport().ToWireValue().ContainsKey("geometry_json"),
                "executor-created result follows a fully valid final geometry export");
        }

        private static void AssertPublicSuccessTypeHasNoCreationPath(Type type)
        {
            ConstructorInfo[] constructors = type.GetConstructors(
                BindingFlags.Public | BindingFlags.Instance);
            AssertEqual(0, constructors.Length, type.Name + " has no public constructor");
            foreach (MethodInfo method in type.GetMethods(
                BindingFlags.Public | BindingFlags.Static))
            {
                Assert(
                    method.ReturnType != type,
                    type.Name + " has no public static success factory: " + method.Name);
            }

            foreach (PropertyInfo property in type.GetProperties(
                BindingFlags.Public | BindingFlags.Instance))
            {
                Assert(
                    property.GetSetMethod(false) == null,
                    type.Name + " exposes only read-only public result properties: " +
                    property.Name);
            }
        }

        private static void TestAllOperationsAndReadback()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CoreManifestV2 manifest = CreateFullManifest(fixture, before);
            InMemoryCadDatabase database = new InMemoryCadDatabase(fixture.Snapshot);

            ManifestExecutionResultV2 result = new ManifestExecutor().Execute(database, manifest);
            GeometryExportV2 after = result.FinalExport;
            AssertEqual(1, database.CommitCount, "exactly one commit");
            AssertEqual(1, database.SaveReopenCount, "execute invokes one save/reopen boundary");
            AssertEqual(3, result.OperationResults.Count, "all three operation results");
            Assert(!string.Equals(
                before.Document.RevisionFingerprint,
                after.Document.RevisionFingerprint,
                StringComparison.Ordinal), "final revision changed");
            Assert(
                !string.Equals(
                    before.Snapshot.Source.Sha256,
                    after.Snapshot.Source.Sha256,
                    StringComparison.Ordinal),
                "simulated mutating save changes final DWG hash");
            Assert(
                before.Snapshot.Source.ByteSize != after.Snapshot.Source.ByteSize,
                "simulated mutating save changes final DWG byte size");
            Assert(after.FindByHandle("11") == null, "eligible overlay is absent");
            CadEntitySnapshot moved = Require(after.FindByHandle("10"), "translated entity exists");
            AssertEqual(Binary64.ToBits(2d), moved.Position.X, "translated X bit");
            AssertEqual(before.FindByHandle("10")!.Position.Y, moved.Position.Y, "untranslated Y bit");
            CadEntitySnapshot marker = Require(after.FindByHandle("14"), "generated marker exists");
            AssertEqual("LPF-REVIEW-" + new string('c', 24), marker.Text, "derived marker text");
            AssertEqual(3, marker.SequenceIndex, "gap-safe marker append index");
            Assert(after.FindByHandle("13")!.ExactlyEquals(before.FindByHandle("13")!), "opaque record is unchanged");
            ExactReadbackVerifier.Verify(before, manifest, after, true);
            manifest.FinalOutputConstraints.RequireActual(
                manifest.ExpectedPrewriteOutputCopyBinding,
                after.Snapshot.Source);
            Assert(
                !after.Snapshot.Source.ExactlyMatches(
                    ExactCadExporter.Export(database.ReadSnapshot()).Snapshot.Source),
                "fresh reopen export does not retain the prewrite private input binding");

            Dictionary<string, object?> resultWire = result.ToWireValue();
            AssertEqual(
                NativeCadProtocolV2.ConsoleResultSchemaVersion,
                AsString(resultWire["schema_version"], "result schema"),
                "result schema version");
            NativeConsoleExportV2 readback = result.CreateReadbackExport();
            Dictionary<string, object?> readbackWire = readback.ToWireValue();
            AssertEqual(
                NativeCadProtocolV2.ConsoleExportSchemaVersion,
                AsString(readbackWire["schema_version"], "readback schema"),
                "readback schema version");
            AssertEqual(
                CanonicalJson.Sha256Hex(after.ToCanonicalJsonUtf8()),
                readback.GeometrySha256,
                "readback exact geometry hash");
            AssertEqual(
                Encoding.UTF8.GetString(after.ToCanonicalJsonUtf8()),
                readback.GeometryJson,
                "readback exact geometry text");
            AssertEqual(
                CanonicalJson.Serialize(
                    readbackWire,
                    NativeCadCanonicalJsonProfiles.ConsoleExport),
                Encoding.UTF8.GetString(readback.ToCanonicalJsonUtf8()),
                "readback envelope uses the exact opaque carrier profile");
        }

        private static void TestGeometryBindingConstructorInvariants()
        {
            string session = "native-session-0123456789abcdef0123456789abcdef";
            string fingerprint = Digest("binding-plugin");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    new NativeGeometryBindingContextV2(
                        session, "adapter", "profile", "1.0", "plugin", "1.0",
                        fingerprint, new[] { "read.one/v1" });
                },
                "one capability rejects at the typed producer boundary");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    new NativeGeometryBindingContextV2(
                        session, "adapter", "profile", "1.0", "plugin", "1.0",
                        fingerprint, new[] { "read.one/v1", "read.one/v1" });
                },
                "duplicate capabilities reject at the typed producer boundary");
            List<string> excessive = new List<string>();
            for (int index = 0; index < 17; index++)
            {
                excessive.Add("read.cap" + index.ToString(CultureInfo.InvariantCulture) + "/v1");
            }
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    new NativeGeometryBindingContextV2(
                        session, "adapter", "profile", "1.0", "plugin", "1.0",
                        fingerprint, excessive);
                },
                "more than sixteen capabilities reject at the typed producer boundary");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    new NativeGeometryBindingContextV2(
                        "session", "adapter", "profile", "1.0", "plugin", "1.0",
                        fingerprint, new[] { "read.one/v1", "read.two/v1" });
                },
                "malformed session binding rejects at the typed producer boundary");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    new NativeGeometryBindingContextV2(
                        session, "adapter!", "profile", "1.0", "plugin", "1.0",
                        fingerprint, new[] { "read.one/v1", "read.two/v1" });
                },
                "malformed adapter binding rejects at the typed producer boundary");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    new NativeSourceBindingV2(
                        Digest("source"),
                        1,
                        Digest("path"),
                        Digest("identity"),
                        "AC10@2");
                },
                "malformed source binding rejects through the shared validator");
        }

        private static void TestSourceBindingTransitions()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CoreManifestV2 manifest = CreateFullManifest(fixture, before);
            InMemoryCadDatabase database = new InMemoryCadDatabase(fixture.Snapshot);
            ManifestExecutionResultV2 result =
                new ManifestExecutor().Execute(database, manifest);
            NativeSourceBindingV2 actual = result.FinalExport.Snapshot.Source;
            manifest.FinalOutputConstraints.RequireActual(
                manifest.ExpectedPrewriteOutputCopyBinding,
                actual);
            Assert(
                !actual.ExactlyMatches(
                    manifest.ExpectedPrewriteSourceBinding),
                "final export establishes an actual changed private binding");
            Assert(
                actual.ByteSize != manifest.ExpectedPrewriteSourceBinding.ByteSize,
                "generated save changes final output byte size");
            Dictionary<string, object?> finalBinding = AsObject(
                result.ToWireValue()["final_document_binding"],
                "final document binding");
            NativeSourceBindingV2 resultSource = SourceBindingFromWire(
                AsObject(finalBinding["output_copy_binding"], "result output source"));
            Assert(
                resultSource.ExactlyMatches(actual),
                "console result carries the retained actual final source");

            NativeSourceBindingV2 stalePrewriteSource =
                new NativeSourceBindingV2(
                    Digest("stale-private-prewrite-bytes"),
                    manifest.ExpectedPrewriteOutputCopyBinding.ByteSize,
                    manifest.ExpectedPrewriteOutputCopyBinding.PathFingerprint,
                    manifest.ExpectedPrewriteOutputCopyBinding
                        .FileIdentityFingerprint,
                    manifest.ExpectedPrewriteOutputCopyBinding
                        .DwgHeaderSignature);
            InMemoryCadDatabase stalePrewriteDatabase =
                new InMemoryCadDatabase(
                    fixture.Snapshot.WithSource(stalePrewriteSource));
            AssertCoreCode(
                CadCoreErrorCode.StalePrecondition,
                delegate
                {
                    new ManifestExecutor().Execute(
                        stalePrewriteDatabase,
                        manifest);
                },
                "current drawing source must equal exact private prewrite binding");
            AssertEqual(
                0,
                stalePrewriteDatabase.BeginTransactionCount,
                "stale private prewrite cannot begin a transaction");

            // Same-path changed bytes are valid when identity replacement is
            // allowed. Every other final-source dimension is checked after
            // save/readback rather than guessed before the transaction.
            NativeSourceBindingV2[] violations =
            {
                new NativeSourceBindingV2(
                    actual.Sha256,
                    actual.ByteSize,
                    Digest("wrong-output-path"),
                    actual.FileIdentityFingerprint,
                    actual.DwgHeaderSignature),
                new NativeSourceBindingV2(
                    actual.Sha256,
                    actual.ByteSize,
                    actual.PathFingerprint,
                    actual.FileIdentityFingerprint,
                    "AC1027"),
                new NativeSourceBindingV2(
                    actual.Sha256,
                    manifest.FinalOutputConstraints.MaxByteSize + 1,
                    actual.PathFingerprint,
                    actual.FileIdentityFingerprint,
                    actual.DwgHeaderSignature),
                new NativeSourceBindingV2(
                    manifest.ExpectedPrewriteOutputCopyBinding.Sha256,
                    actual.ByteSize,
                    actual.PathFingerprint,
                    actual.FileIdentityFingerprint,
                    actual.DwgHeaderSignature),
            };
            for (int index = 0; index < violations.Length; index++)
            {
                CadFaultInjector faults = new CadFaultInjector();
                NativeSourceBindingV2 mismatch = violations[index];
                faults.ReopenedSnapshotTransform =
                    delegate(CadDocumentSnapshot snapshot)
                    {
                        return snapshot.WithSource(mismatch);
                    };
                AssertReopenedReadbackMismatch(
                    fixture,
                    manifest,
                    faults,
                    "v2 final output constraint violation " +
                    index.ToString(CultureInfo.InvariantCulture));
            }

            FinalOutputConstraintsV2 sameIdentityOnly =
                new FinalOutputConstraintsV2(
                    manifest.FinalOutputConstraints.AuthorizedPrivatePathFingerprint,
                    manifest.FinalOutputConstraints.AuthorizedPrivateRootFingerprint,
                    manifest.FinalOutputConstraints.RequiredDwgHeaderSignature,
                    manifest.FinalOutputConstraints.RequiredDwgVersion,
                    manifest.FinalOutputConstraints.MaxByteSize,
                    FileIdentityTransitionPolicyV2.SameIdentityRequired);
            CoreManifestV2 sameIdentityManifest = new CoreManifestV2(
                manifest.ManifestId,
                CreateValidatedFullManifestIntegrity("same-identity"),
                manifest.Nonce,
                before,
                ExpectedPrewriteRevisionV2.From(before),
                before.Snapshot.Source,
                sameIdentityOnly,
                manifest.ExpectedStableHostBindingDigest,
                fixture.MarkerPolicy,
                manifest.Operations);
            ManifestExecutionResultV2 sameIdentityResult =
                new ManifestExecutor().Execute(
                    new InMemoryCadDatabase(fixture.Snapshot),
                    sameIdentityManifest);
            AssertEqual(
                sameIdentityManifest.ExpectedPrewriteOutputCopyBinding
                    .FileIdentityFingerprint,
                sameIdentityResult.FinalExport.Snapshot.Source
                    .FileIdentityFingerprint,
                "same-identity policy permits changed bytes without replacement");

            CadFaultInjector forbiddenReplacement = new CadFaultInjector();
            forbiddenReplacement.ReopenedSnapshotTransform =
                delegate(CadDocumentSnapshot snapshot)
                {
                    NativeSourceBindingV2 source = snapshot.Source;
                    return snapshot.WithSource(new NativeSourceBindingV2(
                        source.Sha256,
                        source.ByteSize,
                        source.PathFingerprint,
                        Digest("forbidden-replacement"),
                        source.DwgHeaderSignature));
                };
            AssertReopenedReadbackMismatch(
                fixture,
                sameIdentityManifest,
                forbiddenReplacement,
                "same-identity policy rejects replacement");
        }

        private static void TestStableHostBinding()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CoreManifestV2 manifest = CreateFullManifest(fixture, before);
            NativeGeometryBindingContextV2 initial =
                fixture.Snapshot.BindingContext;
            NativeGeometryBindingContextV2 renewed =
                initial.WithRenewedSession(
                    "native-session-" + new string('f', 32),
                    4242,
                    2,
                    Digest("renewed-process-instance"),
                    "2");
            CadFaultInjector renewedFault = new CadFaultInjector();
            renewedFault.ReopenedSnapshotTransform =
                delegate(CadDocumentSnapshot snapshot)
                {
                    return snapshot
                        .WithDatabaseInstance(Digest("renewed-database"))
                        .WithBindingContext(renewed);
                };
            ManifestExecutionResultV2 renewedResult =
                new ManifestExecutor().Execute(
                    new InMemoryCadDatabase(fixture.Snapshot, renewedFault),
                    manifest);
            Assert(
                renewedResult.FinalExport.Snapshot.BindingContext
                    .StableExecutionHostBindingDigest(fixture.MarkerPolicy) ==
                manifest.ExpectedStableHostBindingDigest,
                "new session/database retains the manifest stable host binding");

            Dictionary<string, NativeGeometryBindingContextV2> drifts =
                new Dictionary<string, NativeGeometryBindingContextV2>(
                    StringComparer.Ordinal)
                {
                    {
                        "adapter",
                        initial.WithAdapter(
                            "other-adapter",
                            initial.AdapterProfile,
                            initial.AdapterVersion)
                    },
                    {
                        "adapter-profile",
                        initial.WithAdapter(
                            initial.AdapterId,
                            "other-profile",
                            initial.AdapterVersion)
                    },
                    {
                        "adapter-version",
                        initial.WithAdapter(
                            initial.AdapterId,
                            initial.AdapterProfile,
                            "2.0.0")
                    },
                    {
                        "plugin",
                        initial.WithPlugin(
                            "other-plugin",
                            initial.PluginVersion,
                            initial.PluginFingerprint)
                    },
                    {
                        "plugin-version",
                        initial.WithPlugin(
                            initial.PluginId,
                            "2.0.0",
                            initial.PluginFingerprint)
                    },
                    {
                        "plugin-fingerprint",
                        initial.WithPlugin(
                            initial.PluginId,
                            initial.PluginVersion,
                            Digest("other-plugin-fingerprint"))
                    },
                    {
                        "host-product",
                        initial.WithHost(
                            "other-host",
                            initial.HostRelease,
                            initial.HostRuntime,
                            initial.HostMode,
                            initial.ExecutableFingerprint)
                    },
                    {
                        "host-release",
                        initial.WithHost(
                            initial.HostProduct,
                            "2.0",
                            initial.HostRuntime,
                            initial.HostMode,
                            initial.ExecutableFingerprint)
                    },
                    {
                        "host-runtime",
                        initial.WithHost(
                            initial.HostProduct,
                            initial.HostRelease,
                            "other-runtime",
                            initial.HostMode,
                            initial.ExecutableFingerprint)
                    },
                    {
                        "host-executable",
                        initial.WithHost(
                            initial.HostProduct,
                            initial.HostRelease,
                            initial.HostRuntime,
                            initial.HostMode,
                            Digest("other-host-executable"))
                    },
                    {
                        "capabilities",
                        initial.WithCapabilities(
                            new[]
                            {
                                "read.exact_geometry/v1",
                                "read.inventory/v1",
                                "read.extra/v1",
                            })
                    },
                };
            foreach (KeyValuePair<string, NativeGeometryBindingContextV2> drift
                in drifts)
            {
                CadFaultInjector faults = new CadFaultInjector();
                NativeGeometryBindingContextV2 changed = drift.Value;
                faults.ReopenedSnapshotTransform =
                    delegate(CadDocumentSnapshot snapshot)
                    {
                        return snapshot.WithBindingContext(changed);
                    };
                AssertReopenedReadbackMismatch(
                    fixture,
                    manifest,
                    faults,
                    "stable host drift " + drift.Key);
            }

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    initial.WithHost(
                        initial.HostProduct,
                        initial.HostRelease,
                        initial.HostRuntime,
                        "core_console",
                        initial.ExecutableFingerprint);
                },
                "host mode drift rejects at the typed context boundary");

            MarkerPolicyBindingV2 alteredPolicy = new MarkerPolicyBindingV2(
                fixture.MarkerPolicy.ProfileEnabled,
                fixture.MarkerPolicy.Enabled,
                fixture.MarkerPolicy.PluginCapability,
                fixture.MarkerPolicy.Layer,
                fixture.MarkerPolicy.Style,
                Digest("other-marker-layer"),
                fixture.MarkerPolicy.StyleFingerprint,
                fixture.MarkerPolicy.HeightBits,
                fixture.MarkerPolicy.RotationBits,
                fixture.MarkerPolicy.DefaultOverlayEvidence);
            InMemoryCadDatabase markerPolicyDatabase =
                new InMemoryCadDatabase(fixture.Snapshot);
            CoreManifestV2 markerPolicyDrift = new CoreManifestV2(
                manifest.ManifestId,
                CreateValidatedFullManifestIntegrity("marker-policy-drift"),
                manifest.Nonce,
                before,
                ExpectedPrewriteRevisionV2.From(before),
                manifest.ExpectedPrewriteOutputCopyBinding,
                manifest.FinalOutputConstraints,
                manifest.ExpectedStableHostBindingDigest,
                alteredPolicy,
                manifest.Operations);
            AssertCoreCode(
                CadCoreErrorCode.ManifestInvalid,
                delegate
                {
                    new ManifestExecutor().Execute(
                        markerPolicyDatabase,
                        markerPolicyDrift);
                },
                "marker policy stable binding drift");
            AssertEqual(
                0,
                markerPolicyDatabase.BeginTransactionCount,
                "marker policy drift cannot begin a transaction");
        }

        private static void TestOperationResultTransportBudget()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CoreManifestV2 maximum = CreateManifest(
                fixture,
                before,
                CreateMarkerOperations(
                    fixture,
                    NativeCadProtocolV2.MaxNativeOperations));
            maximum.ValidateSelf();
            int maximumBytes =
                ManifestExecutionResultV2.RequirePretransactionTransportBudget(
                    maximum);
            AssertEqual(
                NativeCadProtocolV2.MaxNativeOperations,
                maximum.Operations.Count,
                "exact maximum operation count is admitted");
            Assert(
                maximumBytes <=
                NativeCadProtocolV2.MaxConsoleResultCanonicalBytes,
                "maximum result remains below the canonical transport budget");

            CoreManifestV2 validated623 = CreateManifest(
                fixture,
                before,
                CreateMarkerOperations(fixture, 623));
            validated623.ValidateSelf();
            int bytes623 =
                ManifestExecutionResultV2.RequirePretransactionTransportBudget(
                    validated623);
            AssertEqual(
                623,
                validated623.Operations.Count,
                "validated 623-operation scenario remains supported");
            Assert(
                bytes623 <= NativeCadProtocolV2.MaxConsoleResultCanonicalBytes,
                "validated 623-operation result remains transport-safe");

            InMemoryCadDatabase maxPlusOneDatabase =
                new InMemoryCadDatabase(fixture.Snapshot);
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CreateManifest(
                        fixture,
                        before,
                        CreateMarkerOperations(
                            fixture,
                            NativeCadProtocolV2.MaxNativeOperations + 1));
                },
                "max plus one operation manifest rejects");
            AssertEqual(
                0,
                maxPlusOneDatabase.BeginTransactionCount,
                "max plus one manifest cannot begin a transaction");

            InMemoryCadDatabase twoThousandDatabase =
                new InMemoryCadDatabase(fixture.Snapshot);
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CreateManifest(
                        fixture,
                        before,
                        CreateMarkerOperations(fixture, 2000));
                },
                "two thousand operation manifest rejects");
            AssertEqual(
                0,
                twoThousandDatabase.BeginTransactionCount,
                "two thousand operation manifest cannot begin a transaction");

            Assert(
                NativeConsoleResultBudgetV2.FitsCanonicalPayloadBytes(
                    NativeCadProtocolV2.MaxConsoleResultCanonicalBytes),
                "canonical result budget includes its exact boundary");
            Assert(
                !NativeConsoleResultBudgetV2.FitsCanonicalPayloadBytes(
                    NativeCadProtocolV2.MaxConsoleResultCanonicalBytes + 1L),
                "canonical result budget rejects one byte over its boundary");

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    CreateMarker(
                        fixture,
                        "native-operation-" + new string('a', 25),
                        "LPF-REVIEW-" + new string('a', 25));
                },
                "long operation identifiers reject before result budgeting");
        }

        private static List<ManifestOperationV2> CreateMarkerOperations(
            Fixture fixture,
            int count)
        {
            List<ManifestOperationV2> operations =
                new List<ManifestOperationV2>();
            for (int index = 0; index < count; index++)
            {
                string operationId = "native-operation-" +
                    index.ToString("x24", CultureInfo.InvariantCulture);
                operations.Add(
                    CreateMarker(
                        fixture,
                        operationId,
                        fixture.MarkerPolicy.DeriveMarkerText(operationId),
                        3 + index));
            }

            return operations;
        }

        private static void TestAtomicRollbackAndFaultInjection()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CoreManifestV2 manifest = CreateFullManifest(fixture, before);

            CadFaultInjector partialFault = new CadFaultInjector();
            partialFault.FailAt(CadFaultPoint.AfterMutation);
            InMemoryCadDatabase partialDatabase = new InMemoryCadDatabase(fixture.Snapshot, partialFault);
            AssertCoreCode(
                CadCoreErrorCode.FaultInjected,
                delegate { new ManifestExecutor().Execute(partialDatabase, manifest); },
                "partial transaction fault");
            AssertEqual(0, partialDatabase.CommitCount, "partial fault commits zero times");
            AssertEqual(before.ExportDigest, ExactCadExporter.Export(partialDatabase.ReadSnapshot()).ExportDigest, "partial fault rolled back all state");

            CadFaultInjector commitFault = new CadFaultInjector();
            commitFault.FailAt(CadFaultPoint.Commit);
            InMemoryCadDatabase commitDatabase = new InMemoryCadDatabase(fixture.Snapshot, commitFault);
            AssertCoreCode(
                CadCoreErrorCode.CommitFailed,
                delegate { new ManifestExecutor().Execute(commitDatabase, manifest); },
                "commit fault");
            AssertEqual(0, commitDatabase.CommitCount, "commit fault publishes nothing");
            AssertEqual(before.ExportDigest, ExactCadExporter.Export(commitDatabase.ReadSnapshot()).ExportDigest, "commit fault rolls back all state");

            CadFaultInjector unplannedFault = new CadFaultInjector();
            bool alteredLine = false;
            unplannedFault.Callback = delegate(CadFaultPoint point, MutableCadDocument? document)
            {
                if (point != CadFaultPoint.AfterMutation || alteredLine || document == null)
                {
                    return;
                }

                CadEntitySnapshot line = Require(document.FindByHandle("12"), "fault line exists");
                document.Replace(line.Translate(Vector(1d, 0d, 0d)));
                alteredLine = true;
            };
            InMemoryCadDatabase unplannedDatabase = new InMemoryCadDatabase(fixture.Snapshot, unplannedFault);
            AssertCoreCode(
                CadCoreErrorCode.StalePrecondition,
                delegate { new ManifestExecutor().Execute(unplannedDatabase, manifest); },
                "unplanned line change");
            AssertEqual(0, unplannedDatabase.CommitCount, "unplanned drift rolls back");

            CadFaultInjector protectedFault = new CadFaultInjector();
            bool changedTables = false;
            protectedFault.Callback = delegate(CadFaultPoint point, MutableCadDocument? document)
            {
                if (point != CadFaultPoint.AfterMutation || changedTables || document == null)
                {
                    return;
                }

                document.ReplaceTablesForFaultInjection(CreateTables("protected-drift"));
                changedTables = true;
            };
            InMemoryCadDatabase protectedDatabase = new InMemoryCadDatabase(fixture.Snapshot, protectedFault);
            AssertCoreCode(
                CadCoreErrorCode.StalePrecondition,
                delegate { new ManifestExecutor().Execute(protectedDatabase, manifest); },
                "protected table drift");
            AssertEqual(0, protectedDatabase.CommitCount, "protected drift rolls back");

            CadFaultInjector orderFault = new CadFaultInjector();
            bool reversed = false;
            orderFault.Callback = delegate(CadFaultPoint point, MutableCadDocument? document)
            {
                if (point == CadFaultPoint.BeforeCommit && !reversed && document != null)
                {
                    document.ReverseOrderForFaultInjection();
                    reversed = true;
                }
            };
            InMemoryCadDatabase orderDatabase = new InMemoryCadDatabase(fixture.Snapshot, orderFault);
            AssertCoreCode(
                CadCoreErrorCode.StalePrecondition,
                delegate { new ManifestExecutor().Execute(orderDatabase, manifest); },
                "container order drift");
            AssertEqual(0, orderDatabase.CommitCount, "order drift rolls back");

            CadFaultInjector freshDatabaseFault = new CadFaultInjector();
            freshDatabaseFault.ReopenedSnapshotTransform =
                delegate(CadDocumentSnapshot snapshot)
                {
                    return snapshot.WithDatabaseInstance(Digest("fresh-reopened-database"));
                };
            InMemoryCadDatabase freshDatabase =
                new InMemoryCadDatabase(fixture.Snapshot, freshDatabaseFault);
            ManifestExecutionResultV2 freshResult =
                new ManifestExecutor().Execute(freshDatabase, manifest);
            AssertEqual(1, freshDatabase.SaveReopenCount, "fresh database save/reopen runs");
            AssertEqual(
                Digest("fresh-reopened-database"),
                freshResult.FinalExport.Document.DatabaseInstanceFingerprint,
                "result binds the fresh reopened database");
            Assert(
                !string.Equals(
                    freshResult.FinalExport.Document.RevisionFingerprint,
                    before.Document.RevisionFingerprint,
                    StringComparison.Ordinal),
                "fresh reopened result has the required revision transition");

            AssertPostCommitSaveReopenFailure(
                fixture,
                manifest,
                CadFaultPoint.Save,
                CadCoreErrorCode.SaveFailed,
                "save failure emits no success result");
            AssertPostCommitSaveReopenFailure(
                fixture,
                manifest,
                CadFaultPoint.Reopen,
                CadCoreErrorCode.ReopenFailed,
                "reopen failure emits no success result");

            CadFaultInjector unchangedRevisionFault = new CadFaultInjector();
            unchangedRevisionFault.ReopenedSnapshotTransform =
                delegate(CadDocumentSnapshot snapshot)
                {
                    return snapshot.WithRevision(fixture.Snapshot.RevisionFingerprint);
                };
            AssertReopenedReadbackMismatch(
                fixture,
                manifest,
                unchangedRevisionFault,
                "unchanged reopened revision");

            CadFaultInjector wrongReopenedStateFault = new CadFaultInjector();
            wrongReopenedStateFault.ReopenedSnapshotTransform =
                delegate(CadDocumentSnapshot snapshot)
                {
                    CadEntitySnapshot moved = Require(
                        snapshot.FindByHandle("10"),
                        "reopened translated entity");
                    return ReplaceEntity(
                        snapshot,
                        moved.Translate(Vector(1d, 0d, 0d)));
                };
            AssertReopenedReadbackMismatch(
                fixture,
                manifest,
                wrongReopenedStateFault,
                "wrong reopened target state");

            CadFaultInjector reopenedProtectedDriftFault = new CadFaultInjector();
            reopenedProtectedDriftFault.ReopenedSnapshotTransform =
                delegate(CadDocumentSnapshot snapshot)
                {
                    return snapshot.WithTables(CreateTables("reopened-protected-drift"));
                };
            AssertReopenedReadbackMismatch(
                fixture,
                manifest,
                reopenedProtectedDriftFault,
                "reopened protected state drift");

            CadFaultInjector reopenedOrderDriftFault = new CadFaultInjector();
            reopenedOrderDriftFault.ReopenedSnapshotTransform =
                delegate(CadDocumentSnapshot snapshot)
                {
                    CadEntitySnapshot line = Require(
                        snapshot.FindByHandle("12"),
                        "reopened line");
                    return ReplaceEntityAndSort(
                        snapshot,
                        CopyWithSequenceIndex(line, 4));
                };
            AssertReopenedReadbackMismatch(
                fixture,
                manifest,
                reopenedOrderDriftFault,
                "reopened order drift");
        }

        private static void TestInTransactionStaleStateRevalidation()
        {
            Fixture fixture = CreateFixture(new[] { "AA", "AB" });
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CoreManifestV2 manifest = CreateFullManifest(fixture, before);
            CadEntitySnapshot translateTarget = Require(
                fixture.Snapshot.FindByHandle("10"),
                "prewrite translate target");

            // These transforms model external committed host changes between
            // the out-of-transaction preflight read and BeginTransaction's
            // private snapshot. Every field must reject before the executor
            // invokes any mutation, commit, or save/reopen boundary.
            Dictionary<string, Func<CadDocumentSnapshot, CadDocumentSnapshot>>
                beforeTransactionDrifts =
                new Dictionary<string, Func<CadDocumentSnapshot, CadDocumentSnapshot>>(
                    StringComparer.Ordinal)
                {
                    {
                        "target text",
                        delegate(CadDocumentSnapshot snapshot)
                        {
                            return ReplaceEntity(
                                snapshot,
                                CopyDbText(
                                    translateTarget,
                                    translateTarget.Layer!,
                                    "externally-changed",
                                    translateTarget.RotationBits,
                                    translateTarget.Bounds));
                        }
                    },
                    {
                        "target layer",
                        delegate(CadDocumentSnapshot snapshot)
                        {
                            return ReplaceEntity(
                                snapshot,
                                CopyDbText(
                                    translateTarget,
                                    "OTHER",
                                    translateTarget.Text!,
                                    translateTarget.RotationBits,
                                    translateTarget.Bounds));
                        }
                    },
                    {
                        "target position",
                        delegate(CadDocumentSnapshot snapshot)
                        {
                            return ReplaceEntity(
                                snapshot,
                                translateTarget.Translate(Vector(1d, 0d, 0d)));
                        }
                    },
                    {
                        "target rotation",
                        delegate(CadDocumentSnapshot snapshot)
                        {
                            return ReplaceEntity(
                                snapshot,
                                CopyDbText(
                                    translateTarget,
                                    translateTarget.Layer!,
                                    translateTarget.Text!,
                                    Binary64.ToBits(1d),
                                    translateTarget.Bounds));
                        }
                    },
                    {
                        "target owner",
                        delegate(CadDocumentSnapshot snapshot)
                        {
                            return ReplaceEntity(
                                snapshot,
                                CopyWithOwner(translateTarget, "AB"));
                        }
                    },
                    {
                        "container order",
                        delegate(CadDocumentSnapshot snapshot)
                        {
                            return ReplaceEntityAndSort(
                                snapshot,
                                CopyWithSequenceIndex(translateTarget, 4));
                        }
                    },
                    {
                        "protected state",
                        delegate(CadDocumentSnapshot snapshot)
                        {
                            return snapshot.WithTables(
                                CreateTables("pretransaction-protected"));
                        }
                    },
                    {
                        "revision",
                        delegate(CadDocumentSnapshot snapshot)
                        {
                            return snapshot.WithRevision(
                                Digest("pretransaction-revision"));
                        }
                    },
                };

            foreach (
                KeyValuePair<string, Func<CadDocumentSnapshot, CadDocumentSnapshot>>
                    drift in beforeTransactionDrifts)
            {
                CadDocumentSnapshot externalState = drift.Value(fixture.Snapshot);
                CadFaultInjector faults = new CadFaultInjector
                {
                    BeforeTransactionSnapshotTransform = drift.Value,
                };
                InMemoryCadDatabase database =
                    new InMemoryCadDatabase(fixture.Snapshot, faults);
                AssertCoreCode(
                    CadCoreErrorCode.StalePrecondition,
                    delegate { new ManifestExecutor().Execute(database, manifest); },
                    "pre-transaction " + drift.Key + " drift");
                AssertEqual(
                    0,
                    database.CommitCount,
                    "pre-transaction " + drift.Key + " drift commits zero times");
                AssertEqual(
                    1,
                    database.AbortCount,
                    "pre-transaction " + drift.Key + " drift aborts exactly once");
                AssertEqual(
                    0,
                    database.SaveReopenCount,
                    "pre-transaction " + drift.Key + " drift never saves");
                AssertEqual(
                    ExactCadExporter.Export(externalState).ExportDigest,
                    ExactCadExporter.Export(database.ReadSnapshot()).ExportDigest,
                    "pre-transaction " + drift.Key +
                    " drift remains untouched by stale execution");
            }

            // This is the original regression shape: a target changes inside
            // the mutation call after the executor captured it. ReplaceExact
            // compares the full fresh state and target immediately after the
            // hook, so the stale replacement cannot overwrite it.
            CadFaultInjector beforeMutationFault = new CadFaultInjector();
            bool changedBeforeMutation = false;
            beforeMutationFault.Callback =
                delegate(CadFaultPoint point, MutableCadDocument? document)
                {
                    if (point != CadFaultPoint.BeforeMutation ||
                        changedBeforeMutation ||
                        document == null)
                    {
                        return;
                    }

                    CadEntitySnapshot target = Require(
                        document.FindByHandle("10"),
                        "before-mutation target");
                    document.Replace(
                        CopyDbText(
                            target,
                            target.Layer!,
                            "changed-at-before-mutation",
                            target.RotationBits,
                            target.Bounds));
                    changedBeforeMutation = true;
                };
            InMemoryCadDatabase beforeMutationDatabase =
                new InMemoryCadDatabase(fixture.Snapshot, beforeMutationFault);
            AssertCoreCode(
                CadCoreErrorCode.StalePrecondition,
                delegate
                {
                    new ManifestExecutor().Execute(
                        beforeMutationDatabase,
                        manifest);
                },
                "before-mutation target drift");
            Assert(changedBeforeMutation, "before-mutation drift hook ran");
            AssertEqual(
                0,
                beforeMutationDatabase.CommitCount,
                "before-mutation drift commits zero times");
            AssertEqual(
                1,
                beforeMutationDatabase.AbortCount,
                "before-mutation drift aborts exactly once");
            AssertEqual(
                0,
                beforeMutationDatabase.SaveReopenCount,
                "before-mutation drift never saves");
            AssertEqual(
                before.ExportDigest,
                ExactCadExporter.Export(
                    beforeMutationDatabase.ReadSnapshot()).ExportDigest,
                "before-mutation stale target is not overwritten or published");

            // The generated in-memory transaction serializes normal writers,
            // but callbacks model host-side staged drift. Revalidate the
            // complete permitted prefix before every subsequent operation,
            // rather than relying on that lock guarantee alone.
            Dictionary<string, Action<MutableCadDocument>> betweenOperations =
                new Dictionary<string, Action<MutableCadDocument>>(
                    StringComparer.Ordinal)
                {
                    {
                        "target text",
                        delegate(MutableCadDocument document)
                        {
                            CadEntitySnapshot target = Require(
                                document.FindByHandle("11"),
                                "between-operation text target");
                            document.Replace(
                                CopyDbText(
                                    target,
                                    target.Layer!,
                                    "changed-between-operations",
                                    target.RotationBits,
                                    target.Bounds));
                        }
                    },
                    {
                        "target layer",
                        delegate(MutableCadDocument document)
                        {
                            CadEntitySnapshot target = Require(
                                document.FindByHandle("11"),
                                "between-operation layer target");
                            document.Replace(
                                CopyDbText(
                                    target,
                                    "OTHER",
                                    target.Text!,
                                    target.RotationBits,
                                    target.Bounds));
                        }
                    },
                    {
                        "target position",
                        delegate(MutableCadDocument document)
                        {
                            CadEntitySnapshot target = Require(
                                document.FindByHandle("11"),
                                "between-operation position target");
                            document.Replace(
                                target.Translate(Vector(1d, 0d, 0d)));
                        }
                    },
                    {
                        "target rotation",
                        delegate(MutableCadDocument document)
                        {
                            CadEntitySnapshot target = Require(
                                document.FindByHandle("11"),
                                "between-operation rotation target");
                            document.Replace(
                                CopyDbText(
                                    target,
                                    target.Layer!,
                                    target.Text!,
                                    Binary64.ToBits(1d),
                                    target.Bounds));
                        }
                    },
                    {
                        "target owner",
                        delegate(MutableCadDocument document)
                        {
                            CadEntitySnapshot target = Require(
                                document.FindByHandle("11"),
                                "between-operation owner target");
                            document.Replace(CopyWithOwner(target, "AB"));
                        }
                    },
                    {
                        "container order",
                        delegate(MutableCadDocument document)
                        {
                            CadEntitySnapshot target = Require(
                                document.FindByHandle("11"),
                                "between-operation order target");
                            document.Replace(CopyWithSequenceIndex(target, 3));
                            document.SortForFaultInjection();
                        }
                    },
                    {
                        "protected state",
                        delegate(MutableCadDocument document)
                        {
                            document.ReplaceTablesForFaultInjection(
                                CreateTables("between-operation-protected"));
                        }
                    },
                    {
                        "revision",
                        delegate(MutableCadDocument document)
                        {
                            document.ReplaceRevisionForFaultInjection(
                                Digest("between-operation-revision"));
                        }
                    },
                };

            foreach (
                KeyValuePair<string, Action<MutableCadDocument>> drift in
                    betweenOperations)
            {
                CadFaultInjector faults = new CadFaultInjector();
                bool changedBetweenOperations = false;
                faults.Callback =
                    delegate(CadFaultPoint point, MutableCadDocument? document)
                    {
                        if (point != CadFaultPoint.AfterMutation ||
                            changedBetweenOperations ||
                            document == null)
                        {
                            return;
                        }

                        drift.Value(document);
                        changedBetweenOperations = true;
                    };
                InMemoryCadDatabase database =
                    new InMemoryCadDatabase(fixture.Snapshot, faults);
                AssertCoreCode(
                    CadCoreErrorCode.StalePrecondition,
                    delegate { new ManifestExecutor().Execute(database, manifest); },
                    "inter-operation " + drift.Key + " drift");
                Assert(
                    changedBetweenOperations,
                    "inter-operation " + drift.Key + " hook ran");
                AssertEqual(
                    0,
                    database.CommitCount,
                    "inter-operation " + drift.Key + " commits zero times");
                AssertEqual(
                    1,
                    database.AbortCount,
                    "inter-operation " + drift.Key + " aborts exactly once");
                AssertEqual(
                    0,
                    database.SaveReopenCount,
                    "inter-operation " + drift.Key + " never saves");
                AssertEqual(
                    before.ExportDigest,
                    ExactCadExporter.Export(database.ReadSnapshot()).ExportDigest,
                    "inter-operation " + drift.Key +
                    " drift is aborted without publishing staged state");
            }
        }

        private static void TestTransactionDisposalOrdering()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CoreManifestV2 manifest = CreateFullManifest(fixture, before);

            RecordingCadDatabase success = new RecordingCadDatabase(fixture.Snapshot);
            ManifestExecutionResultV2 result = new ManifestExecutor().Execute(success, manifest);
            Assert(result != null, "disposed transaction still produces verified success");
            AssertEventCount(success, "commit", 1, "success commits once");
            AssertEventCount(success, "abort", 0, "success never aborts");
            AssertEventCount(success, "dispose", 1, "success disposes once");
            AssertEventCount(success, "save", 1, "success saves once");
            AssertEventBefore(
                success,
                "begin",
                "capture",
                "transaction snapshot is captured after begin");
            AssertEventBefore(
                success,
                "capture",
                "replace",
                "transaction snapshot is captured before first mutation");
            AssertEventBefore(success, "commit", "dispose", "commit precedes disposal");
            AssertEventBefore(success, "dispose", "save", "disposal precedes save/reopen");
            Assert(!success.SaveCalledBeforeDispose, "save/reopen never starts with a live transaction");

            RecordingCadDatabase mutationFailure =
                new RecordingCadDatabase(fixture.Snapshot)
                {
                    FailOnReplace = true,
                };
            AssertCoreCode(
                CadCoreErrorCode.TransactionFailure,
                delegate { new ManifestExecutor().Execute(mutationFailure, manifest); },
                "mutation failure");
            AssertEventCount(mutationFailure, "commit", 0, "mutation failure commits zero times");
            AssertEventCount(mutationFailure, "abort", 1, "mutation failure aborts once");
            AssertEventCount(mutationFailure, "dispose", 1, "mutation failure disposes once");
            AssertEventCount(mutationFailure, "save", 0, "mutation failure never saves");
            AssertEventBefore(
                mutationFailure,
                "abort",
                "dispose",
                "mutation failure aborts before disposal");

            RecordingCadDatabase postconditionFailure =
                new RecordingCadDatabase(fixture.Snapshot)
                {
                    FailOnCaptureSnapshot = true,
                };
            AssertCoreCode(
                CadCoreErrorCode.ReadbackMismatch,
                delegate { new ManifestExecutor().Execute(postconditionFailure, manifest); },
                "pre-commit postcondition failure");
            AssertEventCount(postconditionFailure, "commit", 0, "postcondition failure commits zero times");
            AssertEventCount(postconditionFailure, "abort", 1, "postcondition failure aborts once");
            AssertEventCount(postconditionFailure, "dispose", 1, "postcondition failure disposes once");
            AssertEventBefore(
                postconditionFailure,
                "abort",
                "dispose",
                "postcondition failure aborts before disposal");

            RecordingCadDatabase commitFailure =
                new RecordingCadDatabase(fixture.Snapshot)
                {
                    FailOnCommit = true,
                };
            AssertCoreCode(
                CadCoreErrorCode.CommitFailed,
                delegate { new ManifestExecutor().Execute(commitFailure, manifest); },
                "commit failure");
            AssertEventCount(commitFailure, "commit", 1, "commit failure attempts once");
            AssertEventCount(commitFailure, "abort", 1, "commit failure aborts active state once");
            AssertEventCount(commitFailure, "dispose", 1, "commit failure disposes once");
            AssertEventCount(commitFailure, "save", 0, "commit failure never saves");
            AssertEventBefore(
                commitFailure,
                "abort",
                "dispose",
                "commit failure aborts before disposal");

            RecordingCadDatabase disposeFailure =
                new RecordingCadDatabase(fixture.Snapshot)
                {
                    FailOnDispose = true,
                };
            ManifestExecutionResultV2? failedResult = null;
            try
            {
                failedResult = new ManifestExecutor().Execute(disposeFailure, manifest);
            }
            catch (CadCoreException exception)
            {
                AssertEqual(
                    CadCoreErrorCode.TransactionFailure,
                    exception.Code,
                    "dispose failure is a transaction/private-copy failure");
            }

            Assert(failedResult == null, "dispose failure emits no success result");
            AssertEventCount(disposeFailure, "commit", 1, "dispose failure commits once");
            AssertEventCount(disposeFailure, "abort", 0, "dispose failure never rewrites committed history");
            AssertEventCount(disposeFailure, "dispose", 1, "dispose failure is attempted once");
            AssertEventCount(disposeFailure, "save", 0, "dispose failure prevents save/reopen");
            AssertEventBefore(
                disposeFailure,
                "commit",
                "dispose",
                "dispose failure occurs after the committed private transition");
        }

        private static void TestOwnerStateProtection()
        {
            Fixture fixture = CreateFixture(new[] { "AA", "AB" });
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CoreManifestV2 manifest = CreateFullManifest(fixture, before);

            GeometryExportV2 addedOwner = ExactCadExporter.Export(
                fixture.Snapshot.WithOwners(new[] { "AA", "AB", "AC" }));
            GeometryExportV2 reorderedOwners = ExactCadExporter.Export(
                fixture.Snapshot.WithOwners(new[] { "AB", "AA" }));
            Assert(
                !string.Equals(
                    before.Document.ProtectedStateDigest,
                    addedOwner.Document.ProtectedStateDigest,
                    StringComparison.Ordinal),
                "owner additions affect protected-state digest");
            Assert(
                !string.Equals(
                    before.Document.ProtectedOrderDigest,
                    reorderedOwners.Document.ProtectedOrderDigest,
                    StringComparison.Ordinal),
                "owner ordering affects protected-order digest");

            AssertOwnerReadbackMismatch(
                fixture,
                manifest,
                new[] { "AA", "AB", "AC" },
                "added unused owner");
            AssertOwnerReadbackMismatch(
                fixture,
                manifest,
                new[] { "AA" },
                "removed unused owner");
            AssertOwnerReadbackMismatch(
                fixture,
                manifest,
                new[] { "AB", "AA" },
                "reordered owner records");
            AssertOwnerReadbackMismatch(
                fixture,
                manifest,
                new[] { "AA", "AC" },
                "changed unused owner record");

            CadFaultInjector stagedOwnerFault = new CadFaultInjector();
            bool changedOwners = false;
            stagedOwnerFault.Callback =
                delegate(CadFaultPoint point, MutableCadDocument? document)
                {
                    if (point == CadFaultPoint.AfterMutation &&
                        !changedOwners &&
                        document != null)
                    {
                        document.ReplaceOwnersForFaultInjection(
                            new[] { "AA", "AB", "AC" });
                        changedOwners = true;
                    }
                };
            AssertCoreCode(
                CadCoreErrorCode.StalePrecondition,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot, stagedOwnerFault),
                        manifest);
                },
                "staged owner mutation");

            CreateReviewMarkerOperationV2 wrongOwnerMarker = CreateMarker(
                fixture,
                OperationId('a'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('a')),
                3,
                "AB");
            AssertCoreCode(
                CadCoreErrorCode.ManifestInvalid,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot),
                        CreateManifest(
                            fixture,
                            before,
                            new ManifestOperationV2[] { wrongOwnerMarker }));
                },
                "marker owner must be the pre-existing direct Modelspace owner");
        }

        private static void AssertOwnerReadbackMismatch(
            Fixture fixture,
            CoreManifestV2 manifest,
            IEnumerable<string> owners,
            string message)
        {
            CadFaultInjector faults = new CadFaultInjector();
            faults.ReopenedSnapshotTransform =
                delegate(CadDocumentSnapshot snapshot)
                {
                    return snapshot.WithOwners(owners);
                };
            AssertReopenedReadbackMismatch(fixture, manifest, faults, message);
        }

        private static void AssertEventCount(
            RecordingCadDatabase database,
            string name,
            int expected,
            string message)
        {
            AssertEqual(expected, database.EventCount(name), message);
        }

        private static void AssertEventBefore(
            RecordingCadDatabase database,
            string first,
            string second,
            string message)
        {
            int firstIndex = database.FirstEventIndex(first);
            int secondIndex = database.FirstEventIndex(second);
            Assert(
                firstIndex >= 0 && secondIndex >= 0 && firstIndex < secondIndex,
                message);
        }

        private static void TestMultipleMarkersAndMixedInvalidPreflight()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CadEntitySnapshot target = Require(fixture.Snapshot.FindByHandle("10"), "move target");
            CadEntitySnapshot overlay = Require(fixture.Snapshot.FindByHandle("11"), "overlay target");
            Binary64Vector delta = Vector(1d, 0d, 0d);
            TranslateDbTextOperationV2 move = new TranslateDbTextOperationV2(
                OperationId('a'),
                target.TargetId,
                delta,
                TranslatedGeometryV2.From(target, delta));
            DeleteAuxiliaryOverlayTextOperationV2 delete =
                new DeleteAuxiliaryOverlayTextOperationV2(OperationId('b'), overlay.TargetId, true);
            CreateReviewMarkerOperationV2 first = CreateMarker(
                fixture,
                OperationId('c'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('c')),
                3);
            CreateReviewMarkerOperationV2 second = CreateMarker(
                fixture,
                OperationId('d'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('d')),
                4);
            CoreManifestV2 multiple = CreateManifest(
                fixture,
                before,
                new ManifestOperationV2[] { move, delete, first, second });
            InMemoryCadDatabase database = new InMemoryCadDatabase(fixture.Snapshot);
            ManifestExecutionResultV2 multipleResult =
                new ManifestExecutor().Execute(database, multiple);
            GeometryExportV2 after = multipleResult.FinalExport;
            AssertEqual(1, database.CommitCount, "multiple marker transaction commits once");
            AssertEqual(3, Require(after.FindByHandle("14"), "first marker").SequenceIndex, "first marker sequence");
            AssertEqual(4, Require(after.FindByHandle("15"), "second marker").SequenceIndex, "second marker sequence");
            ExactReadbackVerifier.Verify(before, multiple, after, true);

            CreateReviewMarkerOperationV2 invalidMarker = CreateMarker(
                fixture,
                OperationId('b'),
                "LPF-REVIEW-" + new string('f', 24),
                3);
            CoreManifestV2 mixedInvalid = CreateManifest(
                fixture,
                before,
                new ManifestOperationV2[] { move, invalidMarker });
            InMemoryCadDatabase untouched = new InMemoryCadDatabase(fixture.Snapshot);
            AssertCoreCode(
                CadCoreErrorCode.ManifestInvalid,
                delegate { new ManifestExecutor().Execute(untouched, mixedInvalid); },
                "mixed valid and invalid operations preflight before mutation");
            AssertEqual(0, untouched.CommitCount, "mixed invalid preflight commits zero times");
            AssertEqual(before.ExportDigest, ExactCadExporter.Export(untouched.ReadSnapshot()).ExportDigest, "mixed invalid leaves every record unchanged");
        }

        private static void TestMarkerReservationsSurviveDeletes()
        {
            Fixture fixture = CreateFixtureWithMaximumOverlay();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CadEntitySnapshot firstDelete = Require(
                fixture.Snapshot.FindByHandle("11"),
                "first deletable overlay");
            CadEntitySnapshot maximumDelete = Require(
                fixture.Snapshot.FindByHandle("14"),
                "maximum-sequence deletable overlay");
            DeleteAuxiliaryOverlayTextOperationV2 deleteMaximum =
                new DeleteAuxiliaryOverlayTextOperationV2(
                    OperationId('a'), maximumDelete.TargetId, true);
            DeleteAuxiliaryOverlayTextOperationV2 deleteFirst =
                new DeleteAuxiliaryOverlayTextOperationV2(
                    OperationId('b'), firstDelete.TargetId, true);
            CreateReviewMarkerOperationV2 first = CreateMarker(
                fixture,
                OperationId('c'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('c')),
                4);
            CreateReviewMarkerOperationV2 second = CreateMarker(
                fixture,
                OperationId('d'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('d')),
                5);

            CoreManifestV2 deletesBeforeMarkers = CreateManifest(
                fixture,
                before,
                new ManifestOperationV2[] { deleteMaximum, deleteFirst, first, second });
            InMemoryCadDatabase database = new InMemoryCadDatabase(fixture.Snapshot);
            GeometryExportV2 after = new ManifestExecutor().Execute(
                database,
                deletesBeforeMarkers).FinalExport;
            Assert(Require(after.FindByHandle("15"), "first reserved marker").SequenceIndex == 4,
                "delete of original maximum does not lower the first reservation");
            Assert(Require(after.FindByHandle("16"), "second reserved marker").SequenceIndex == 5,
                "multiple deletes preserve every original marker reservation");
            Assert(after.FindByHandle("14") == null && after.FindByHandle("11") == null,
                "deleted sequence slots remain gaps");
            ExactReadbackVerifier.Verify(before, deletesBeforeMarkers, after, true);

            CreateReviewMarkerOperationV2 markerFirst = CreateMarker(
                fixture,
                OperationId('a'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('a')),
                4);
            DeleteAuxiliaryOverlayTextOperationV2 deleteAfterMarker =
                new DeleteAuxiliaryOverlayTextOperationV2(
                    OperationId('b'), maximumDelete.TargetId, true);
            CoreManifestV2 markerBeforeDelete = CreateManifest(
                fixture,
                before,
                new ManifestOperationV2[] { markerFirst, deleteAfterMarker });
            GeometryExportV2 markerFirstAfter = new ManifestExecutor().Execute(
                new InMemoryCadDatabase(fixture.Snapshot),
                markerBeforeDelete).FinalExport;
            AssertEqual(4, Require(markerFirstAfter.FindByHandle("15"), "reserved marker").SequenceIndex,
                "marker-before-delete uses the same original reservation");
            ExactReadbackVerifier.Verify(before, markerBeforeDelete, markerFirstAfter, true);

            CreateReviewMarkerOperationV2 wrongSlot = CreateMarker(
                fixture,
                OperationId('c'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('c')),
                5);
            AssertCoreCode(
                CadCoreErrorCode.ManifestInvalid,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot),
                        CreateManifest(fixture, before, new ManifestOperationV2[] { wrongSlot }));
                },
                "marker slot outside the original append reservation rejects");
            CreateReviewMarkerOperationV2 colliding = CreateMarker(
                fixture,
                OperationId('d'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('d')),
                4);
            AssertCoreCode(
                CadCoreErrorCode.ManifestInvalid,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot),
                        CreateManifest(
                            fixture,
                            before,
                            new ManifestOperationV2[] { first, colliding }));
                },
                "duplicate marker reservation rejects");
        }

        private static void TestPreflightErrorCodes()
        {
            Fixture fixture = CreateFixture();
            GeometryExportV2 before = ExactCadExporter.Export(fixture.Snapshot);
            CadEntitySnapshot target = Require(fixture.Snapshot.FindByHandle("10"), "target");
            Binary64Vector delta = Vector(1d, 0d, 0d);
            TranslateDbTextOperationV2 translate = new TranslateDbTextOperationV2(
                OperationId('a'),
                target.TargetId,
                delta,
                TranslatedGeometryV2.From(target, delta));

            InMemoryCadDatabase staleDatabase = new InMemoryCadDatabase(
                fixture.Snapshot.WithRevision(Digest("stale-revision")));
            CoreManifestV2 staleManifest = CreateManifest(fixture, before, new ManifestOperationV2[] { translate });
            AssertCoreCode(
                CadCoreErrorCode.StalePrecondition,
                delegate { new ManifestExecutor().Execute(staleDatabase, staleManifest); },
                "stale precondition");

            foreach (KeyValuePair<string, CadEntitySnapshot> altered in
                new Dictionary<string, CadEntitySnapshot>(StringComparer.Ordinal)
                {
                    {
                        "wrong layer",
                        CopyDbText(
                            target,
                            "OTHER",
                            target.Text!,
                            target.RotationBits,
                            target.Bounds)
                    },
                    {
                        "wrong text",
                        CopyDbText(
                            target,
                            target.Layer!,
                            "changed",
                            target.RotationBits,
                            target.Bounds)
                    },
                    {
                        "wrong rotation",
                        CopyDbText(
                            target,
                            target.Layer!,
                            target.Text!,
                            Binary64.ToBits(1d),
                            target.Bounds)
                    },
                    {
                        "wrong bounds",
                        CopyDbText(
                            target,
                            target.Layer!,
                            target.Text!,
                            target.RotationBits,
                            Bounds(1d, 2d, 4d, 5d))
                    },
                })
            {
                CadDocumentSnapshot alteredSnapshot =
                    ReplaceEntity(fixture.Snapshot, altered.Value);
                AssertCoreCode(
                    CadCoreErrorCode.StalePrecondition,
                    delegate
                    {
                        new ManifestExecutor().Execute(
                            new InMemoryCadDatabase(alteredSnapshot),
                            staleManifest);
                    },
                    altered.Key + " exact precondition");
            }

            TranslateDbTextOperationV2 duplicateTranslate = new TranslateDbTextOperationV2(
                OperationId('b'),
                target.TargetId,
                delta,
                TranslatedGeometryV2.From(target, delta));
            CoreManifestV2 duplicateManifest = CreateManifest(
                fixture,
                before,
                new ManifestOperationV2[] { translate, duplicateTranslate });
            InMemoryCadDatabase duplicateDatabase = new InMemoryCadDatabase(fixture.Snapshot);
            AssertCoreCode(
                CadCoreErrorCode.DuplicateTarget,
                delegate { new ManifestExecutor().Execute(duplicateDatabase, duplicateManifest); },
                "duplicate target");
            AssertEqual(0, duplicateDatabase.CommitCount, "duplicate target never begins a commit");

            TranslateDbTextOperationV2 duplicateOperationId = new TranslateDbTextOperationV2(
                OperationId('a'),
                target.TargetId,
                delta,
                TranslatedGeometryV2.From(target, delta));
            CoreManifestV2 duplicateIdManifest = CreateManifest(
                fixture,
                before,
                new ManifestOperationV2[] { translate, duplicateOperationId });
            AssertCoreCode(
                CadCoreErrorCode.DuplicateOperation,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot),
                        duplicateIdManifest);
                },
                "duplicate operation ID");

            CadEntitySnapshot line = Require(fixture.Snapshot.FindByHandle("12"), "line");
            TranslateDbTextOperationV2 wrongType = new TranslateDbTextOperationV2(
                OperationId('a'),
                line.TargetId,
                delta,
                TranslatedGeometryV2.From(line, delta));
            AssertCoreCode(
                CadCoreErrorCode.InvalidTarget,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot),
                        CreateManifest(fixture, before, new ManifestOperationV2[] { wrongType }));
                },
                "wrong entity type");

            DeleteAuxiliaryOverlayTextOperationV2 unauditedDelete =
                new DeleteAuxiliaryOverlayTextOperationV2(OperationId('a'), target.TargetId, false);
            AssertCoreCode(
                CadCoreErrorCode.InvalidTarget,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot),
                        CreateManifest(fixture, before, new ManifestOperationV2[] { unauditedDelete }));
                },
                "delete eligibility gate");

            TranslatedGeometryV2 wrongBounds = TranslatedGeometryV2.From(target, Vector(2d, 0d, 0d));
            TranslateDbTextOperationV2 wrongExpected = new TranslateDbTextOperationV2(
                OperationId('a'),
                target.TargetId,
                delta,
                wrongBounds);
            AssertCoreCode(
                CadCoreErrorCode.InvalidTarget,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot),
                        CreateManifest(fixture, before, new ManifestOperationV2[] { wrongExpected }));
                },
                "wrong translation bounds");

            CreateReviewMarkerOperationV2 mismatchedMarker = CreateMarker(
                fixture,
                OperationId('a'),
                "LPF-REVIEW-" + new string('f', 24));
            AssertCoreCode(
                CadCoreErrorCode.ManifestInvalid,
                delegate
                {
                    new ManifestExecutor().Execute(
                        new InMemoryCadDatabase(fixture.Snapshot),
                        CreateManifest(fixture, before, new ManifestOperationV2[] { mismatchedMarker }));
                },
                "marker text mismatch");

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    Binary64.Translate(
                        Binary64.ToBits(double.MaxValue),
                        Binary64.ToBits(double.MaxValue));
                },
                "binary64 overflow rejects");
        }

        private static void AssertPostCommitSaveReopenFailure(
            Fixture fixture,
            CoreManifestV2 manifest,
            CadFaultPoint faultPoint,
            CadCoreErrorCode expectedCode,
            string message)
        {
            CadFaultInjector faults = new CadFaultInjector();
            faults.FailAt(faultPoint);
            InMemoryCadDatabase database =
                new InMemoryCadDatabase(fixture.Snapshot, faults);
            ManifestExecutionResultV2? result = null;
            try
            {
                result = new ManifestExecutor().Execute(database, manifest);
            }
            catch (CadCoreException exception)
            {
                AssertEqual(expectedCode, exception.Code, message);
            }

            Assert(result == null, message + " does not construct a success result");
            AssertEqual(1, database.CommitCount, message + " occurs after commit");
            AssertEqual(1, database.SaveReopenCount, message + " invokes save/reopen once");
            Assert(
                !string.Equals(
                    ExactCadExporter.Export(database.ReadSnapshot()).ExportDigest,
                    ExactCadExporter.Export(fixture.Snapshot).ExportDigest,
                    StringComparison.Ordinal),
                message + " does not claim rollback of committed private state");
        }

        private static void AssertReopenedReadbackMismatch(
            Fixture fixture,
            CoreManifestV2 manifest,
            CadFaultInjector faults,
            string message)
        {
            InMemoryCadDatabase database =
                new InMemoryCadDatabase(fixture.Snapshot, faults);
            ManifestExecutionResultV2? result = null;
            try
            {
                result = new ManifestExecutor().Execute(database, manifest);
            }
            catch (CadCoreException exception)
            {
                AssertEqual(CadCoreErrorCode.ReadbackMismatch, exception.Code, message);
            }

            Assert(result == null, message + " does not construct a success result");
            AssertEqual(1, database.CommitCount, message + " occurs after commit");
            AssertEqual(1, database.SaveReopenCount, message + " invokes save/reopen once");
        }

        private static CoreManifestV2 CreateFullManifest(Fixture fixture, GeometryExportV2 before)
        {
            CadEntitySnapshot target = Require(fixture.Snapshot.FindByHandle("10"), "move target");
            CadEntitySnapshot overlay = Require(fixture.Snapshot.FindByHandle("11"), "overlay target");
            Binary64Vector delta = Vector(1d, 0d, 0d);
            TranslateDbTextOperationV2 move = new TranslateDbTextOperationV2(
                OperationId('a'),
                target.TargetId,
                delta,
                TranslatedGeometryV2.From(target, delta));
            DeleteAuxiliaryOverlayTextOperationV2 delete =
                new DeleteAuxiliaryOverlayTextOperationV2(OperationId('b'), overlay.TargetId, true);
            CreateReviewMarkerOperationV2 marker = CreateMarker(
                fixture,
                OperationId('c'),
                fixture.MarkerPolicy.DeriveMarkerText(OperationId('c')));
            return CreateManifest(fixture, before, new ManifestOperationV2[] { move, delete, marker });
        }

        private static CoreManifestV2 CreateManifest(
            Fixture fixture,
            GeometryExportV2 before,
            IEnumerable<ManifestOperationV2> operations,
            ValidatedFullManifestIntegrityV2? fullManifestIntegrity = null)
        {
            return new CoreManifestV2(
                "native-manifest-" + new string('d', 32),
                fullManifestIntegrity ?? CreateValidatedFullManifestIntegrity(
                    "generated-core-manifest"),
                new string('n', 43),
                before,
                ExpectedPrewriteRevisionV2.From(before),
                before.Snapshot.Source,
                CreateFinalOutputConstraints(before.Snapshot.Source),
                before.Snapshot.BindingContext.StableExecutionHostBindingDigest(
                    fixture.MarkerPolicy),
                fixture.MarkerPolicy,
                operations);
        }

        private static FinalOutputConstraintsV2 CreateFinalOutputConstraints(
            NativeSourceBindingV2 prewrite)
        {
            return new FinalOutputConstraintsV2(
                prewrite.PathFingerprint,
                Digest("generated-private-output-root"),
                prewrite.DwgHeaderSignature,
                prewrite.DwgHeaderSignature,
                Math.Max(6L, prewrite.ByteSize + 1_024L),
                FileIdentityTransitionPolicyV2.ReplacementAllowed);
        }

        private static ValidatedFullManifestIntegrityV2 CreateValidatedFullManifestIntegrity(
            string seed)
        {
            GeometryExportV2 geometry =
                ExactCadExporter.Export(CreateFixture().Snapshot);
            Dictionary<string, object?> payload = new Dictionary<string, object?>(
                StringComparer.Ordinal)
            {
                { "schema_version", NativeCadProtocolV2.ManifestSchemaVersion },
                { "generated_seed", seed },
                {
                    "preconditions_geometry_json",
                    Encoding.UTF8.GetString(geometry.ToCanonicalJsonUtf8())
                },
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
                            NativeCadCanonicalJsonProfiles.Manifest)
                    },
                });
            return ValidatedFullManifestIntegrityV2.FromManifestDocumentUtf8(
                CanonicalJson.SerializeUtf8(
                    payload,
                    NativeCadCanonicalJsonProfiles.Manifest));
        }

        private static CreateReviewMarkerOperationV2 CreateMarker(
            Fixture fixture,
            string operationId,
            string text,
            int sequence = 3,
            string ownerHandle = "AA")
        {
            CadContainer direct = fixture.DirectModelspace;
            Binary64Vector position = Vector(10d + sequence, 20d, 0d);
            string fingerprint = CreateReviewMarkerOperationV2.DeriveMarkerFingerprint(
                ownerHandle,
                direct,
                sequence,
                position,
                text,
                fixture.MarkerPolicy.Layer,
                fixture.MarkerPolicy.Style,
                fixture.MarkerPolicy.HeightBits,
                fixture.MarkerPolicy.RotationBits,
                fixture.MarkerPolicy.DefaultOverlayEvidence);
            return new CreateReviewMarkerOperationV2(
                operationId,
                ownerHandle,
                direct,
                sequence,
                position,
                text,
                fingerprint,
                fixture.MarkerPolicy.Layer,
                fixture.MarkerPolicy.Style,
                fixture.MarkerPolicy.HeightBits,
                fixture.MarkerPolicy.RotationBits,
                fixture.MarkerPolicy.DefaultOverlayEvidence);
        }

        private static Fixture CreateFixture(IEnumerable<string>? owners = null)
        {
            CadContainer modelspace = new CadContainer(
                NativeSpaceKind.Modelspace,
                "BB",
                null,
                new string[0]);
            CadContainer paperspace = new CadContainer(
                NativeSpaceKind.Paperspace,
                "BC",
                null,
                new string[0]);
            OverlayEvidence normal = new OverlayEvidence(false, false, false, false, true);
            OverlayEvidence eligible = new OverlayEvidence(true, true, true, true, false);
            OverlayEvidence marker = new OverlayEvidence(false, false, false, false, true);
            List<CadEntitySnapshot> entities = new List<CadEntitySnapshot>
            {
                DbText("10", modelspace, 0, "TEMP", "move", Vector(1d, 2d, 0d), Bounds(1d, 2d, 3d, 4d), normal),
                DbText("11", modelspace, 1, "TEMP", "overlay", Vector(5d, 6d, 0d), Bounds(5d, 6d, 5d, 6d), eligible),
                new CadEntitySnapshot(
                    "12",
                    NativeEntityKind.Line,
                    "AA",
                    modelspace,
                    2,
                    "0",
                    null,
                    null,
                    Binary64.ToBits(0d),
                    Binary64.ToBits(0d),
                    Vector(7d, 8d, 0d),
                    Bounds(7d, 8d, 8d, 8d),
                    new[] { new CadSegment(Vector(7d, 8d, 0d), Vector(8d, 8d, 0d)) },
                    normal),
                new CadEntitySnapshot(
                    "13",
                    NativeEntityKind.Opaque,
                    "AA",
                    paperspace,
                    0,
                    null,
                    null,
                    null,
                    Binary64.ToBits(0d),
                    Binary64.ToBits(0d),
                    Vector(0d, 0d, 0d),
                    Bounds(0d, 0d, 0d, 0d),
                    new CadSegment[0],
                    marker),
            };
            CadDocumentTables tables = CreateTables("base");
            MarkerPolicyBindingV2 policy = new MarkerPolicyBindingV2(
                true,
                true,
                true,
                "REVIEW",
                "STANDARD",
                Digest("layer-review"),
                Digest("style-standard"),
                Binary64.ToBits(2.5d),
                Binary64.ToBits(0d),
                marker);
            CadDocumentSnapshot snapshot = new CadDocumentSnapshot(
                Digest("database"),
                Digest("revision"),
                owners ?? new[] { "AA" },
                entities,
                tables,
                NativeSourceBindingV2.CreateGenerated(),
                NativeGeometryBindingContextV2.CreateGenerated());
            return new Fixture(snapshot, modelspace, policy);
        }

        private static Fixture CreateFixtureWithMaximumOverlay()
        {
            Fixture original = CreateFixture();
            List<CadEntitySnapshot> entities =
                new List<CadEntitySnapshot>(original.Snapshot.Entities);
            OverlayEvidence eligible =
                new OverlayEvidence(true, true, true, true, false);
            entities.Add(
                DbText(
                    "14",
                    original.DirectModelspace,
                    3,
                    "TEMP",
                    "maximum-overlay",
                    Vector(9d, 9d, 0d),
                    Bounds(9d, 9d, 9d, 9d),
                    eligible));
            entities.Sort(CadDocumentSnapshot.CompareEntityOrder);
            CadDocumentSnapshot snapshot = new CadDocumentSnapshot(
                original.Snapshot.DatabaseInstanceFingerprint,
                original.Snapshot.RevisionFingerprint,
                original.Snapshot.Owners,
                entities,
                original.Snapshot.Tables,
                original.Snapshot.Source,
                original.Snapshot.BindingContext);
            return new Fixture(
                snapshot,
                original.DirectModelspace,
                original.MarkerPolicy);
        }

        private static CadDocumentTables CreateTables(string seed)
        {
            Dictionary<string, string> layers = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                { "TEMP", Digest("layer-temp") },
                { "REVIEW", Digest("layer-review") },
                { "0", Digest("layer-zero") },
            };
            Dictionary<string, string> styles = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                { "STANDARD", Digest("style-standard") },
            };
            return new CadDocumentTables(
                Digest("tables-" + seed),
                Digest("layouts-" + seed),
                Digest("blocks-" + seed),
                Digest("layer-review"),
                Digest("style-standard"),
                layers,
                styles);
        }

        private static CadEntitySnapshot DbText(
            string handle,
            CadContainer container,
            int sequence,
            string layer,
            string text,
            Binary64Vector position,
            CadBounds bounds,
            OverlayEvidence evidence)
        {
            return new CadEntitySnapshot(
                handle,
                NativeEntityKind.DbText,
                "AA",
                container,
                sequence,
                layer,
                text,
                "STANDARD",
                Binary64.ToBits(2.5d),
                Binary64.ToBits(0d),
                position,
                bounds,
                new CadSegment[0],
                evidence);
        }

        private static CadEntitySnapshot CopyDbText(
            CadEntitySnapshot original,
            string layer,
            string text,
            string rotationBits,
            CadBounds bounds)
        {
            return new CadEntitySnapshot(
                original.Handle,
                NativeEntityKind.DbText,
                original.OwnerHandle,
                original.Container,
                original.SequenceIndex,
                layer,
                text,
                original.Style,
                original.HeightBits,
                rotationBits,
                original.Position,
                bounds,
                original.Segments,
                original.OverlayEvidence);
        }

        private static CadEntitySnapshot CopyWithOwner(
            CadEntitySnapshot original,
            string ownerHandle)
        {
            return new CadEntitySnapshot(
                original.Handle,
                original.Kind,
                ownerHandle,
                original.Container,
                original.SequenceIndex,
                original.Layer,
                original.Text,
                original.Style,
                original.HeightBits,
                original.RotationBits,
                original.Position,
                original.Bounds,
                original.Segments,
                original.OverlayEvidence);
        }

        private static CadDocumentSnapshot ReplaceEntity(
            CadDocumentSnapshot snapshot,
            CadEntitySnapshot replacement)
        {
            List<CadEntitySnapshot> entities = new List<CadEntitySnapshot>();
            for (int index = 0; index < snapshot.Entities.Count; index++)
            {
                CadEntitySnapshot entity = snapshot.Entities[index];
                entities.Add(
                    string.Equals(entity.Handle, replacement.Handle, StringComparison.Ordinal)
                        ? replacement
                        : entity);
            }

            return snapshot.WithEntities(entities);
        }

        private static CadDocumentSnapshot ReplaceEntityAndSort(
            CadDocumentSnapshot snapshot,
            CadEntitySnapshot replacement)
        {
            List<CadEntitySnapshot> entities = new List<CadEntitySnapshot>();
            for (int index = 0; index < snapshot.Entities.Count; index++)
            {
                CadEntitySnapshot entity = snapshot.Entities[index];
                entities.Add(
                    string.Equals(entity.Handle, replacement.Handle, StringComparison.Ordinal)
                        ? replacement
                        : entity);
            }

            entities.Sort(CadDocumentSnapshot.CompareEntityOrder);
            return snapshot.WithEntities(entities);
        }

        private static CadEntitySnapshot CopyWithSequenceIndex(
            CadEntitySnapshot original,
            int sequenceIndex)
        {
            return new CadEntitySnapshot(
                original.Handle,
                original.Kind,
                original.OwnerHandle,
                original.Container,
                sequenceIndex,
                original.Layer,
                original.Text,
                original.Style,
                original.HeightBits,
                original.RotationBits,
                original.Position,
                original.Bounds,
                original.Segments,
                original.OverlayEvidence);
        }

        private static Binary64Vector Vector(double x, double y, double z)
        {
            return new Binary64Vector(Binary64.ToBits(x), Binary64.ToBits(y), Binary64.ToBits(z));
        }

        private static CadBounds Bounds(double minX, double minY, double maxX, double maxY)
        {
            return new CadBounds(Vector(minX, minY, 0d), Vector(maxX, maxY, 0d));
        }

        private static string Digest(string seed)
        {
            return CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "generated", seed },
                });
        }

        private static Dictionary<string, object?> PayloadWithoutIntegrity(
            IDictionary<string, object?> value)
        {
            Dictionary<string, object?> payload =
                new Dictionary<string, object?>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, object?> pair in value)
            {
                if (!string.Equals(pair.Key, "integrity", StringComparison.Ordinal))
                {
                    payload.Add(pair.Key, pair.Value);
                }
            }

            return payload;
        }

        private static string OperationId(char suffix)
        {
            return "native-operation-" + new string(suffix, 24);
        }

        private static Dictionary<string, object?> AsObject(object? value, string label)
        {
            Dictionary<string, object?>? result = value as Dictionary<string, object?>;
            if (result == null)
            {
                throw new InvalidOperationException(label + " is not a JSON object.");
            }

            return result;
        }

        private static List<object?> AsArray(object? value, string label)
        {
            List<object?>? result = value as List<object?>;
            if (result == null)
            {
                throw new InvalidOperationException(label + " is not a JSON array.");
            }

            return result;
        }

        private static string AsString(object? value, string label)
        {
            string? result = value as string;
            if (result == null)
            {
                throw new InvalidOperationException(label + " is not a JSON string.");
            }

            return result;
        }

        private static bool AsBoolean(object? value, string label)
        {
            if (!(value is bool))
            {
                throw new InvalidOperationException(label + " is not a JSON Boolean.");
            }

            return (bool)value;
        }

        private static long AsInt64(object? value, string label)
        {
            if (!(value is long))
            {
                throw new InvalidOperationException(label + " is not a JSON integer.");
            }

            return (long)value;
        }

        private static T Require<T>(T? value, string label)
            where T : class
        {
            if (value == null)
            {
                throw new InvalidOperationException(label + " is absent.");
            }

            return value;
        }

        private static void Assert(bool condition, string message)
        {
            if (!condition)
            {
                throw new InvalidOperationException("Assertion failed: " + message);
            }
        }

        private static void AssertEqual<T>(T expected, T actual, string message)
        {
            if (!EqualityComparer<T>.Default.Equals(expected, actual))
            {
                throw new InvalidOperationException(
                    "Assertion failed: " + message + "; expected=" + expected + "; actual=" + actual + ".");
            }
        }

        private static void AssertThrows<TException>(Action action, string message)
            where TException : Exception
        {
            try
            {
                action();
            }
            catch (TException)
            {
                return;
            }

            throw new InvalidOperationException("Assertion failed: expected " + typeof(TException).Name + " for " + message + ".");
        }

        private static void AssertCoreCode(CadCoreErrorCode expected, Action action, string message)
        {
            try
            {
                action();
            }
            catch (CadCoreException exception)
            {
                AssertEqual(expected, exception.Code, message);
                return;
            }

            throw new InvalidOperationException("Assertion failed: expected core error for " + message + ".");
        }

        /// <summary>
        /// Records the public transaction contract without using internals so
        /// lifecycle guarantees stay valid for a future host adapter.
        /// </summary>
        private sealed class RecordingCadDatabase : ICadDatabase
        {
            private readonly InMemoryCadDatabase inner;
            private readonly List<string> events = new List<string>();
            private int disposedTransactions;

            internal RecordingCadDatabase(CadDocumentSnapshot snapshot)
            {
                inner = new InMemoryCadDatabase(snapshot);
            }

            internal bool FailOnReplace { get; set; }

            internal bool FailOnCaptureSnapshot { get; set; }

            internal bool FailOnCommit { get; set; }

            internal bool FailOnDispose { get; set; }

            internal bool SaveCalledBeforeDispose { get; private set; }

            public CadDocumentSnapshot ReadSnapshot()
            {
                events.Add("read");
                return inner.ReadSnapshot();
            }

            public ICadTransaction BeginTransaction()
            {
                events.Add("begin");
                return new RecordingCadTransaction(this, inner.BeginTransaction());
            }

            public ICadDatabase SaveAndReopen(
                FinalOutputConstraintsV2 finalOutputConstraints)
            {
                events.Add("save");
                if (disposedTransactions == 0)
                {
                    SaveCalledBeforeDispose = true;
                }

                return inner.SaveAndReopen(finalOutputConstraints);
            }

            internal int EventCount(string name)
            {
                int count = 0;
                for (int index = 0; index < events.Count; index++)
                {
                    if (string.Equals(events[index], name, StringComparison.Ordinal))
                    {
                        count++;
                    }
                }

                return count;
            }

            internal int FirstEventIndex(string name)
            {
                for (int index = 0; index < events.Count; index++)
                {
                    if (string.Equals(events[index], name, StringComparison.Ordinal))
                    {
                        return index;
                    }
                }

                return -1;
            }

            private sealed class RecordingCadTransaction : ICadTransaction
            {
                private readonly RecordingCadDatabase owner;
                private readonly ICadTransaction innerTransaction;

                internal RecordingCadTransaction(
                    RecordingCadDatabase owner,
                    ICadTransaction innerTransaction)
                {
                    this.owner = owner ?? throw new ArgumentNullException(nameof(owner));
                    this.innerTransaction = innerTransaction ??
                        throw new ArgumentNullException(nameof(innerTransaction));
                }

                public bool IsActive
                {
                    get { return innerTransaction.IsActive; }
                }

                public CadDocumentSnapshot CaptureSnapshot()
                {
                    owner.events.Add("capture");
                    if (owner.FailOnCaptureSnapshot)
                    {
                        throw new CadCoreException(
                            CadCoreErrorCode.ReadbackMismatch,
                            "Recorded transaction snapshot failure.");
                    }

                    return innerTransaction.CaptureSnapshot();
                }

                public void ReplaceExact(
                    CadDocumentSnapshot expectedState,
                    CadEntitySnapshot expectedTarget,
                    CadEntitySnapshot replacement)
                {
                    owner.events.Add("replace");
                    if (owner.FailOnReplace)
                    {
                        throw new CadCoreException(
                            CadCoreErrorCode.TransactionFailure,
                            "Recorded mutation failure.");
                    }

                    innerTransaction.ReplaceExact(
                        expectedState,
                        expectedTarget,
                        replacement);
                }

                public void EraseExact(
                    CadDocumentSnapshot expectedState,
                    CadEntitySnapshot expectedTarget)
                {
                    owner.events.Add("erase");
                    innerTransaction.EraseExact(expectedState, expectedTarget);
                }

                public void AppendExact(
                    CadDocumentSnapshot expectedState,
                    CadEntitySnapshot entity)
                {
                    owner.events.Add("append");
                    innerTransaction.AppendExact(expectedState, entity);
                }

                public void PrepareCommit()
                {
                    owner.events.Add("prepare");
                    innerTransaction.PrepareCommit();
                }

                public void CommitExact(CadDocumentSnapshot expectedState)
                {
                    owner.events.Add("commit");
                    if (owner.FailOnCommit)
                    {
                        throw new CadCoreException(
                            CadCoreErrorCode.CommitFailed,
                            "Recorded commit failure.");
                    }

                    innerTransaction.CommitExact(expectedState);
                }

                public void Abort()
                {
                    owner.events.Add("abort");
                    innerTransaction.Abort();
                }

                public void Dispose()
                {
                    owner.events.Add("dispose");
                    owner.disposedTransactions++;
                    innerTransaction.Dispose();
                    if (owner.FailOnDispose)
                    {
                        throw new InvalidOperationException(
                            "Recorded transaction disposal failure.");
                    }
                }
            }
        }

        private sealed class Fixture
        {
            internal Fixture(
                CadDocumentSnapshot snapshot,
                CadContainer directModelspace,
                MarkerPolicyBindingV2 markerPolicy)
            {
                Snapshot = snapshot;
                DirectModelspace = directModelspace;
                MarkerPolicy = markerPolicy;
            }

            internal CadDocumentSnapshot Snapshot { get; private set; }

            internal CadContainer DirectModelspace { get; private set; }

            internal MarkerPolicyBindingV2 MarkerPolicy { get; private set; }
        }
    }
}
