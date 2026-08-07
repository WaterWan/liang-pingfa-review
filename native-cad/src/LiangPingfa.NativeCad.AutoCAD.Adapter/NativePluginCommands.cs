// SPDX-License-Identifier: MIT
// Fixed command entry points. No prompts, selection, UI, LISP, COM, or
// arbitrary command forwarding is available from this adapter.

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Runtime;
using LiangPingfa.NativeCad.Core;
using LiangPingfa.NativeCad.Protocol;

[assembly: ExtensionApplication(
    typeof(LiangPingfa.NativeCad.AutoCAD.Adapter.NativeExtensionApplication))]

namespace LiangPingfa.NativeCad.AutoCAD.Adapter
{
    /// <summary>Minimal host lifecycle entry. Bootstrap is explicit and session-scoped.</summary>
    public sealed class NativeExtensionApplication : IExtensionApplication
    {
        public void Initialize()
        {
            // Deliberately no automatic server start, document scan, or UI.
        }

        public void Terminate()
        {
            NativeBridgeHost.Stop();
        }
    }

    /// <summary>Only the three fixed commands recognized by the managed plugin.</summary>
    public static class NativePluginCommands
    {
        /// <summary>Starts a read-only current-document bridge from private bootstrap context.</summary>
        [CommandMethod(AdapterIdentity.BootstrapCommand, CommandFlags.Session)]
        public static void BootstrapBridge()
        {
            BootstrapCommandContext context = BootstrapCommandContext.Require();
            Document document = NativeCommandRuntime.RequireCurrentReadableDocument();
            NativeBridgeHost.Start(document, context);
        }

        /// <summary>Executes exactly one manifest against the current private Core Console DWG.</summary>
        [CommandMethod(AdapterIdentity.ExecuteCommand, CommandFlags.Modal)]
        public static void ExecuteManifest()
        {
            ConsoleCommandContext context = ConsoleCommandContext.Require();
            context.RequireRuntimePackageIntegrity();
            RequireResultFileName(context.ResultPath, "native-console-result.json");
            ParsedManifest manifest = ManifestProjectionReader.Read(
                context.ManifestPath,
                context,
                ConsoleOperationMode.Execute);
            Document document = NativeCommandRuntime.RequireCurrentPrivateDocument(
                context.PrivateRoot);
            AutodeskCadDatabase database = NativeCommandRuntime.CreateDatabase(
                document,
                context,
                manifest);
            try
            {
                NativeCommandRuntime.RequireCurrentSource(
                    database,
                    manifest.CoreManifest.ExpectedPrewriteOutputCopyBinding);
                ManifestExecutionResultV2 result =
                    new ManifestExecutor().Execute(database, manifest.CoreManifest);
                NativeCommandRuntime.RequireResultPublicationBinding(
                    document.Name,
                    context.PrivateRoot,
                    result);
                context.RequireRuntimePackageIntegrity();
                NativeConsoleArtifactWriter.WriteResult(
                    context.ResultPath,
                    context.RunId,
                    result,
                    context.RuntimePackageFingerprint);
            }
            finally
            {
                database.Dispose();
            }
        }

        /// <summary>Exports a fresh private post-save readback tied to the prior write receipt.</summary>
        [CommandMethod(AdapterIdentity.ExportCommand, CommandFlags.Modal)]
        public static void ExportManifest()
        {
            ConsoleCommandContext context = ConsoleCommandContext.Require();
            context.RequireRuntimePackageIntegrity();
            RequireResultFileName(context.ResultPath, "native-console-export.json");
            ParsedManifest manifest = ManifestProjectionReader.Read(
                context.ManifestPath,
                context,
                ConsoleOperationMode.Export);
            NativeConsoleReceipt receipt = NativeConsoleArtifactWriter.ReadReceipt(
                context.PrivateRoot,
                manifest.CoreManifest,
                context.RuntimePackageFingerprint);
            Document document = NativeCommandRuntime.RequireCurrentPrivateDocument(
                context.PrivateRoot);
            AutodeskCadDatabase database = NativeCommandRuntime.CreateDatabase(
                document,
                context,
                manifest);
            try
            {
                GeometryExportV2 export = ExactCadExporter.Export(database.ReadSnapshot());
                receipt.RequireMatches(export);
                // The separate readback command publishes an artifact, so
                // revalidate the held private DWG immediately beforehand.
                database.RequireCurrentPrivateBinding();
                context.RequireRuntimePackageIntegrity();
                NativeConsoleArtifactWriter.WriteExport(
                    context.ResultPath,
                    context.RunId,
                    manifest.CoreManifest,
                    receipt,
                    export,
                    context.RuntimePackageFingerprint);
            }
            finally
            {
                database.Dispose();
            }
        }

        private static void RequireResultFileName(string path, string expected)
        {
            if (!string.Equals(
                    Path.GetFileName(path),
                    expected,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new AdapterFailureException(
                    "LPF_CONSOLE_CONTEXT",
                    "The Core Console result file name is not fixed.");
            }
        }
    }

    /// <summary>Current-document command binding; no filename reaches a command argument.</summary>
    internal static class NativeCommandRuntime
    {
        internal static Document RequireCurrentReadableDocument()
        {
            Document? document = Application.DocumentManager.MdiActiveDocument;
            if (document == null || string.IsNullOrEmpty(document.Name))
            {
                throw new AdapterFailureException(
                    "LPF_CURRENT_DOCUMENT",
                    "No current saved document is available.");
            }

            string path = PrivatePathPolicy.RequireNormalLocalPath(document.Name);
            if (!path.EndsWith(".dwg", StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(path))
            {
                throw new AdapterFailureException(
                    "LPF_CURRENT_DOCUMENT",
                    "The current document is not a saved local DWG.");
            }

            return document;
        }

        internal static Document RequireCurrentPrivateDocument(string privateRoot)
        {
            Document? document = Application.DocumentManager.MdiActiveDocument;
            if (document == null || string.IsNullOrEmpty(document.Name))
            {
                throw new AdapterFailureException(
                    "LPF_CURRENT_DOCUMENT",
                    "No current private document is available.");
            }

            PrivatePathPolicy.RequirePrivateFile(document.Name, privateRoot, ".dwg");
            return document;
        }

        internal static AutodeskCadDatabase CreateDatabase(
            Document document,
            ConsoleCommandContext context,
            ParsedManifest manifest)
        {
            NativeGeometryBindingContextV2 binding =
                manifest.CoreManifest.Preconditions.Snapshot.BindingContext;
            // Retain and hash the private DWG before touching the Autodesk
            // database. Every transaction snapshot then reuses this exact
            // binding instead of rereading the full file per operation.
            RetainedPrivateDwgBinding privateBinding =
                RetainedPrivateDwgBinding.Open(
                    document.Name,
                    context.PrivateRoot);
            try
            {
                return new AutodeskCadDatabase(
                    document,
                    document.Database,
                    document.Name,
                    context.PrivateRoot,
                    privateBinding,
                    binding,
                    manifest.MarkerPolicy,
                    false);
            }
            catch
            {
                privateBinding.Dispose();
                throw;
            }
        }

        internal static void RequireCurrentSource(
            AutodeskCadDatabase database,
            NativeSourceBindingV2 expected)
        {
            NativeSourceBindingV2 actual = database.CachedPrivateBinding;
            if (!actual.ExactlyMatches(expected))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "The current Core Console DWG differs from the manifest private copy.");
            }
        }

        /// <summary>
        /// The core has finished fresh readback at this point. Reopen one
        /// final retained lease and prove it still names the exact final
        /// binding immediately before the result becomes observable.
        /// </summary>
        internal static void RequireResultPublicationBinding(
            string privatePath,
            string privateRoot,
            ManifestExecutionResultV2 result)
        {
            if (result == null)
            {
                throw new ArgumentNullException(nameof(result));
            }

            using (RetainedPrivateDwgBinding finalBinding =
                RetainedPrivateDwgBinding.Open(privatePath, privateRoot))
            {
                if (!finalBinding.CachedBinding.ExactlyMatches(
                        result.FinalExport.Snapshot.Source))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.StalePrecondition,
                        "The private DWG changed after fresh readback.");
                }

                // A second full capture is deliberately the final boundary
                // immediately before private result publication.
                finalBinding.RequireCurrent();
            }
        }
    }

    /// <summary>Canonical private result/export writer with create-new receipt semantics.</summary>
    internal static class NativeConsoleArtifactWriter
    {
        private const string ResultReceiptFileName = "native-console-result.json";

        internal static void WriteResult(
            string path,
            string runId,
            ManifestExecutionResultV2 result,
            string runtimePackageFingerprint)
        {
            AdapterIdentity.RequireRuntimePackageFingerprint(
                runtimePackageFingerprint);
            Dictionary<string, object?> payload = result.ToWireValue();
            payload["run_id"] = runId;
            payload["runtime_package_fingerprint"] = runtimePackageFingerprint;
            WriteCanonicalNew(
                path,
                CanonicalizeConsoleResultPayload(payload));
        }

        internal static void WriteExport(
            string path,
            string runId,
            CoreManifestV2 manifest,
            NativeConsoleReceipt receipt,
            GeometryExportV2 export,
            string runtimePackageFingerprint)
        {
            AdapterIdentity.RequireRuntimePackageFingerprint(
                runtimePackageFingerprint);
            if (!string.Equals(
                    receipt.RuntimePackageFingerprint,
                    runtimePackageFingerprint,
                    StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "The write/readback runtime package differs.");
            }
            byte[] geometry = export.ToCanonicalJsonUtf8();
            string geometryJson = new UTF8Encoding(false, true).GetString(geometry);
            Dictionary<string, object?> payload =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "schema_version", NativeCadProtocolV2.ConsoleExportSchemaVersion },
                    { "run_id", runId },
                    { "manifest_id", manifest.ManifestId },
                    { "manifest_integrity_sha256", manifest.FullManifestIntegritySha256 },
                    { "manifest_schema_version", NativeCadProtocolV2.ManifestSchemaVersion },
                    {
                        "console_result_integrity_sha256",
                        receipt.IntegritySha256
                    },
                    {
                        "console_result_schema_version",
                        NativeCadProtocolV2.ConsoleResultSchemaVersion
                    },
                    { "nonce", manifest.Nonce },
                    {
                        "runtime_package_fingerprint",
                        runtimePackageFingerprint
                    },
                    {
                        "final_revision_fingerprint",
                        export.Document.RevisionFingerprint
                    },
                    {
                        "final_document_binding",
                        receipt.FinalDocumentBinding
                    },
                    { "geometry_json", geometryJson },
                    { "geometry_sha256", CanonicalJson.Sha256Hex(geometry) },
                };
            WriteCanonicalNew(
                path,
                CanonicalizeConsoleExportPayload(payload));
        }

        /// <summary>
        /// Adds exact integrity and serializes a result with ordinary strict
        /// canonical JSON. Results have no opaque carrier exemption.
        /// </summary>
        internal static byte[] CanonicalizeConsoleResultPayload(
            Dictionary<string, object?> payload)
        {
            ReplaceIntegrity(payload, CanonicalJsonOptions.Strict);
            return SerializeBoundedCanonicalPayload(
                payload,
                NativeCadProtocolV2.MaxConsoleResultBytes,
                CanonicalJsonOptions.Strict);
        }

        /// <summary>
        /// Adds exact integrity and serializes an export using its sole
        /// approved 16 MiB opaque geometry carrier path.
        /// </summary>
        internal static byte[] CanonicalizeConsoleExportPayload(
            Dictionary<string, object?> payload)
        {
            ReplaceIntegrity(
                payload,
                NativeCadCanonicalJsonProfiles.ConsoleExport);
            return SerializeBoundedCanonicalPayload(
                payload,
                NativeCadProtocolV2.MaxConsoleExportBytes,
                NativeCadCanonicalJsonProfiles.ConsoleExport);
        }

        internal static NativeConsoleReceipt ReadReceipt(
            string privateRoot,
            CoreManifestV2 manifest,
            string runtimePackageFingerprint)
        {
            string path = PrivatePathPolicy.RequirePrivateFile(
                Path.Combine(privateRoot, ResultReceiptFileName),
                privateRoot,
                ".json");
            byte[] bytes = File.ReadAllBytes(path);
            object? raw = CanonicalJson.RequireCanonicalUtf8(
                bytes,
                NativeCadProtocolV2.MaxConsoleResultBytes,
                CanonicalJsonOptions.Strict);
            Dictionary<string, object?> result = RequireObject(raw, "receipt");
            RequireExactKeys(
                result,
                "schema_version",
                "run_id",
                "manifest_id",
                "manifest_integrity_sha256",
                "nonce",
                "final_revision_fingerprint",
                "final_revision_transition",
                "final_document_binding",
                "transaction",
                "operation_results",
                "integrity",
                "manifest_schema_version",
                "runtime_package_fingerprint");
            VerifyIntegrity(result);
            RequireLiteral(
                RequireString(result, "schema_version"),
                NativeCadProtocolV2.ConsoleResultSchemaVersion);
            RequireLiteral(
                RequireString(result, "manifest_id"),
                manifest.ManifestId);
            RequireLiteral(
                RequireString(result, "manifest_integrity_sha256"),
                manifest.FullManifestIntegritySha256);
            RequireLiteral(RequireString(result, "nonce"), manifest.Nonce);
            RequireLiteral(
                RequireString(result, "manifest_schema_version"),
                NativeCadProtocolV2.ManifestSchemaVersion);
            RequireLiteral(
                RequireString(result, "runtime_package_fingerprint"),
                runtimePackageFingerprint);
            RequireLiteral(
                RequireString(result, "final_revision_transition"),
                "save_reopen_changed");
            Dictionary<string, object?> final = RequireObject(
                result,
                "final_document_binding");
            RequireExactKeys(
                final,
                "database_instance_fingerprint",
                "revision_fingerprint",
                "output_copy_binding");
            Dictionary<string, object?> output = RequireObject(
                final,
                "output_copy_binding");
            NativeSourceBindingV2 binding = ReadSource(output);
            manifest.FinalOutputConstraints.RequireActual(
                manifest.ExpectedPrewriteOutputCopyBinding,
                binding);
            if (!string.Equals(
                    RequireString(result, "final_revision_fingerprint"),
                    RequireString(final, "revision_fingerprint"),
                    StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "The private console receipt revision is invalid.");
            }

            Dictionary<string, object?> transaction = RequireObject(result, "transaction");
            RequireExactKeys(transaction, "preflight", "outcome", "rollback");
            RequireLiteral(RequireString(transaction, "preflight"), "passed");
            RequireLiteral(RequireString(transaction, "outcome"), "committed");
            RequireLiteral(RequireString(transaction, "rollback"), "not_required");
            ValidateOperationResults(
                RequireArray(result, "operation_results"),
                manifest);
            return new NativeConsoleReceipt(
                RequireString(RequireObject(result, "integrity"), "sha256"),
                final,
                binding,
                RequireString(final, "database_instance_fingerprint"),
                RequireString(final, "revision_fingerprint"),
                runtimePackageFingerprint);
        }

        private static void WriteCanonicalNew(
            string path,
            byte[] bytes)
        {
            using (FileStream stream = new FileStream(
                path,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None,
                64 * 1024,
                FileOptions.WriteThrough))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }

            PrivatePathPolicy.RequirePrivateFile(
                path,
                Path.GetDirectoryName(path) ?? string.Empty,
                ".json");
        }

        private static byte[] SerializeBoundedCanonicalPayload(
            Dictionary<string, object?> payload,
            int maximumBytes,
            CanonicalJsonOptions profile)
        {
            byte[] bytes = CanonicalJson.SerializeUtf8(payload, profile);
            if (bytes.Length == 0 || bytes.Length > maximumBytes)
            {
                throw new AdapterFailureException(
                    "LPF_CONSOLE_ARTIFACT",
                    "A private console artifact exceeds its fixed byte bound.");
            }

            return bytes;
        }

        private static void ReplaceIntegrity(
            Dictionary<string, object?> payload,
            CanonicalJsonOptions profile)
        {
            payload.Remove("integrity");
            payload.Add(
                "integrity",
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "algorithm", "SHA-256" },
                    { "sha256", CanonicalJson.Sha256Hex(payload, profile) },
                });
        }

        private static void VerifyIntegrity(Dictionary<string, object?> payload)
        {
            Dictionary<string, object?> integrity = RequireObject(payload, "integrity");
            RequireExactKeys(integrity, "algorithm", "sha256");
            RequireLiteral(RequireString(integrity, "algorithm"), "SHA-256");
            string claimed = RequireString(integrity, "sha256");
            CanonicalJson.RequireSha256(claimed, "receipt integrity");
            Dictionary<string, object?> unsigned =
                new Dictionary<string, object?>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, object?> entry in payload)
            {
                if (!string.Equals(entry.Key, "integrity", StringComparison.Ordinal))
                {
                    unsigned.Add(entry.Key, entry.Value);
                }
            }

            if (!string.Equals(
                    claimed,
                    CanonicalJson.Sha256Hex(unsigned),
                    StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "The private console receipt integrity is invalid.");
            }
        }

        private static NativeSourceBindingV2 ReadSource(
            Dictionary<string, object?> source)
        {
            RequireExactKeys(
                source,
                "format",
                "sha256",
                "byte_size",
                "path_fingerprint",
                "file_identity_fingerprint",
                "dwg_header_signature");
            RequireLiteral(RequireString(source, "format"), "DWG");
            return new NativeSourceBindingV2(
                RequireString(source, "sha256"),
                RequireInteger(source, "byte_size"),
                RequireString(source, "path_fingerprint"),
                RequireString(source, "file_identity_fingerprint"),
                RequireString(source, "dwg_header_signature"));
        }

        private static void ValidateOperationResults(
            List<object?> values,
            CoreManifestV2 manifest)
        {
            if (values.Count != manifest.Operations.Count)
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "The private receipt operation cardinality is invalid.");
            }

            for (int index = 0; index < values.Count; index++)
            {
                Dictionary<string, object?> result = RequireObject(
                    values[index],
                    "operation result");
                RequireExactKeys(
                    result,
                    "operation_id",
                    "status",
                    "postcondition_digest",
                    "marker_handle");
                ManifestOperationV2 operation = manifest.Operations[index];
                string? markerHandle = RequireNullableHandle(
                    result,
                    "marker_handle");
                bool isMarker = operation is CreateReviewMarkerOperationV2;
                if (!string.Equals(
                        RequireString(result, "operation_id"),
                        operation.OperationId,
                        StringComparison.Ordinal) ||
                    !string.Equals(
                        RequireString(result, "status"),
                        "applied",
                        StringComparison.Ordinal) ||
                    !string.Equals(
                        RequireString(result, "postcondition_digest"),
                        OperationExecutionResultV2.ComputePostconditionDigest(
                            operation,
                            markerHandle),
                        StringComparison.Ordinal) ||
                    (isMarker && markerHandle == null) ||
                    (!isMarker && markerHandle != null))
                {
                    throw new AdapterFailureException(
                        "LPF_RECEIPT",
                        "The private receipt operation result is invalid.");
                }
            }
        }

        private static string? RequireNullableHandle(
            IDictionary<string, object?> values,
            string key)
        {
            object? value;
            if (!values.TryGetValue(key, out value))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt field is missing.");
            }

            if (value == null)
            {
                return null;
            }

            string? handle = value as string;
            if (handle == null)
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt marker handle is invalid.");
            }

            try
            {
                CadHandle.Require(handle, key);
                return handle;
            }
            catch (CanonicalJsonException exception)
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt marker handle is invalid: " +
                    exception.Message);
            }
        }

        private static Dictionary<string, object?> RequireObject(object? value, string label)
        {
            Dictionary<string, object?>? result = value as Dictionary<string, object?>;
            if (result == null)
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt object is invalid: " + label);
            }

            return result;
        }

        private static Dictionary<string, object?> RequireObject(
            IDictionary<string, object?> values,
            string key)
        {
            object? value;
            if (!values.TryGetValue(key, out value))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt field is missing.");
            }

            return RequireObject(value, key);
        }

        private static List<object?> RequireArray(
            IDictionary<string, object?> values,
            string key)
        {
            object? value;
            if (!values.TryGetValue(key, out value) ||
                !(value is List<object?>))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt array is invalid.");
            }

            return (List<object?>)value;
        }

        private static string RequireString(
            IDictionary<string, object?> values,
            string key)
        {
            object? value;
            if (!values.TryGetValue(key, out value) || !(value is string))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt string is invalid.");
            }

            return (string)value;
        }

        private static long RequireInteger(
            IDictionary<string, object?> values,
            string key)
        {
            object? value;
            if (!values.TryGetValue(key, out value))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt integer is missing.");
            }

            if (value is long)
            {
                return (long)value;
            }

            if (value is ulong && (ulong)value <= long.MaxValue)
            {
                return (long)(ulong)value;
            }

            throw new AdapterFailureException(
                "LPF_RECEIPT",
                "A private receipt integer is invalid.");
        }

        private static void RequireExactKeys(
            IDictionary<string, object?> values,
            params string[] keys)
        {
            if (values.Count != keys.Length)
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt field set is invalid.");
            }

            for (int index = 0; index < keys.Length; index++)
            {
                if (!values.ContainsKey(keys[index]))
                {
                    throw new AdapterFailureException(
                        "LPF_RECEIPT",
                        "A private receipt field is missing.");
                }
            }
        }

        private static void RequireLiteral(string observed, string expected)
        {
            if (!string.Equals(observed, expected, StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "A private receipt fixed field is invalid.");
            }
        }
    }

    /// <summary>Receipt fields retained only inside the private Core Console workspace.</summary>
    internal sealed class NativeConsoleReceipt
    {
        internal NativeConsoleReceipt(
            string integritySha256,
            Dictionary<string, object?> finalDocumentBinding,
            NativeSourceBindingV2 outputBinding,
            string databaseFingerprint,
            string revisionFingerprint,
            string runtimePackageFingerprint)
        {
            IntegritySha256 = integritySha256;
            FinalDocumentBinding = finalDocumentBinding;
            OutputBinding = outputBinding;
            DatabaseFingerprint = databaseFingerprint;
            RevisionFingerprint = revisionFingerprint;
            RuntimePackageFingerprint = runtimePackageFingerprint;
        }

        internal string IntegritySha256 { get; private set; }

        internal Dictionary<string, object?> FinalDocumentBinding { get; private set; }

        internal NativeSourceBindingV2 OutputBinding { get; private set; }

        internal string DatabaseFingerprint { get; private set; }

        internal string RevisionFingerprint { get; private set; }

        internal string RuntimePackageFingerprint { get; private set; }

        internal void RequireMatches(GeometryExportV2 export)
        {
            if (!string.Equals(
                    export.Document.DatabaseInstanceFingerprint,
                    DatabaseFingerprint,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    export.Document.RevisionFingerprint,
                    RevisionFingerprint,
                    StringComparison.Ordinal) ||
                !export.Snapshot.Source.ExactlyMatches(OutputBinding))
            {
                throw new AdapterFailureException(
                    "LPF_RECEIPT",
                    "Fresh readback differs from the private write receipt.");
            }
        }
    }
}
