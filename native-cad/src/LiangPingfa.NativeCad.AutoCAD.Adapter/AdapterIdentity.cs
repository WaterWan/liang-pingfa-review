// SPDX-License-Identifier: MIT
// Licensed-host AutoCAD adapter source. It is intentionally not a runtime claim.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using LiangPingfa.NativeCad.Core;
using LiangPingfa.NativeCad.Protocol;

[assembly: AssemblyMetadata("LiangPingfaAutoCadAdapter", "operator-supplied-licensed-sdk-required")]
[assembly: AssemblyMetadata("LiangPingfaAutoCadAdapterSyntaxOnlyBuild", "false")]
[assembly: InternalsVisibleTo("LiangPingfa.NativeCad.AutoCAD.Adapter.Tests")]

namespace LiangPingfa.NativeCad.AutoCAD.Adapter
{
    /// <summary>Constants that bind every host-facing operation to the fixed v2 surface.</summary>
    internal static class AdapterIdentity
    {
        internal const string AdapterId = "liang-pingfa-autocad-adapter";
        internal const string PluginId = "liang-pingfa-autocad-plugin";
        internal const string PluginVersion = "2.0.0";
        internal const string BridgePipePrefix = "liang-pingfa-native-";
        internal const string ExecuteCommand = "LPF_NATIVE_EXECUTE_MANIFEST";
        internal const string ExportCommand = "LPF_NATIVE_EXPORT_MANIFEST";
        internal const string BootstrapCommand = "LPF_NATIVE_BRIDGE_BOOTSTRAP";
        internal const string ManifestEnvironmentVariable = "LIANG_PINGFA_NATIVE_MANIFEST";
        internal const string ResultEnvironmentVariable = "LIANG_PINGFA_NATIVE_RESULT";
        internal const string RunIdEnvironmentVariable = "LIANG_PINGFA_NATIVE_RUN_ID";
        internal const string PrivateRootEnvironmentVariable = "LIANG_PINGFA_NATIVE_PRIVATE_ROOT";
        internal const string BootstrapNonceEnvironmentVariable =
            "LIANG_PINGFA_NATIVE_BOOTSTRAP_NONCE";
        internal const string BootstrapOutputEnvironmentVariable =
            "LIANG_PINGFA_NATIVE_BOOTSTRAP_OUTPUT";
        internal const string BootstrapExpiryEnvironmentVariable =
            "LIANG_PINGFA_NATIVE_BOOTSTRAP_EXPIRES_AT";

        internal static string Profile
        {
            get
            {
#if LPF_AUTOCAD_2024
#if LPF_TSSD_PROFILE
                return "tssd2024";
#else
                return "autocad2024";
#endif
#elif LPF_AUTOCAD_2025
#if LPF_TSSD_PROFILE
                return "tssd2025";
#else
                return "autocad2025";
#endif
#elif LPF_AUTOCAD_2026
#if LPF_TSSD_PROFILE
                return "tssd2026";
#else
                return "autocad2026";
#endif
#else
                throw new AdapterFailureException(
                    "LPF_ADAPTER_PROFILE",
                    "The adapter was built without one explicit host profile.");
#endif
            }
        }

        internal static string HostRelease
        {
            get
            {
#if LPF_AUTOCAD_2024
                return "2024";
#elif LPF_AUTOCAD_2025
                return "2025";
#elif LPF_AUTOCAD_2026
                return "2026";
#else
                throw new AdapterFailureException(
                    "LPF_ADAPTER_PROFILE",
                    "The adapter was built without one explicit host release.");
#endif
            }
        }

        internal static string HostRuntime
        {
            get
            {
#if NET48
                return "net48";
#else
                return "net8";
#endif
            }
        }

        /// <summary>
        /// Complete host-advertised surface for the initial AutoCAD profile.
        /// Deletion is intentionally absent: AutoCAD SaveAs/reopen compacts
        /// erased slots, while v2 requires their physical gap to survive. The
        /// core-owned canonical sequence is "create_review_marker/v1",
        /// "read.exact_geometry/v1", "read.inventory/v1", and
        /// "translate_dbtext/v1".
        /// </summary>
        internal static IReadOnlyList<string> Capabilities
        {
            get
            {
                return NativeCadCapabilities.AutoCadAdapter;
            }
        }

        internal static string AssemblyFingerprint()
        {
            string path = Assembly.GetExecutingAssembly().Location;
            return HashFile(path);
        }

        internal static string HashFile(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                throw new AdapterFailureException(
                    "LPF_HOST_BINDING",
                    "A required executable path is unavailable.");
            }

            using (SHA256 algorithm = SHA256.Create())
            using (System.IO.FileStream stream = new System.IO.FileStream(
                path,
                System.IO.FileMode.Open,
                System.IO.FileAccess.Read,
                System.IO.FileShare.Read))
            {
                byte[] digest = algorithm.ComputeHash(stream);
                StringBuilder value = new StringBuilder(digest.Length * 2);
                for (int index = 0; index < digest.Length; index++)
                {
                    value.Append(
                        digest[index].ToString("x2", CultureInfo.InvariantCulture));
                }

                return value.ToString();
            }
        }

        internal static string HashUtf8(string value)
        {
            if (value == null)
            {
                throw new ArgumentNullException(nameof(value));
            }

            return CanonicalJson.Sha256Hex(
                new UTF8Encoding(false, true).GetBytes(
                    value.Normalize(NormalizationForm.FormC)));
        }

        internal static string RequireFixedRunId(string value)
        {
            if (value == null || value.Length != "native-run-".Length + 32 ||
                !value.StartsWith("native-run-", StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_CONSOLE_CONTEXT",
                    "The Core Console run identifier is invalid.");
            }

            for (int index = "native-run-".Length; index < value.Length; index++)
            {
                char character = value[index];
                if (!((character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f')))
                {
                    throw new AdapterFailureException(
                        "LPF_CONSOLE_CONTEXT",
                        "The Core Console run identifier is invalid.");
                }
            }

            return value;
        }
    }

    /// <summary>Redacted adapter failure; no drawing or command data is emitted.</summary>
    internal sealed class AdapterFailureException : InvalidOperationException
    {
        internal AdapterFailureException(string code, string message)
            : base(message)
        {
            Code = code;
        }

        internal string Code { get; private set; }
    }
}
