// SPDX-License-Identifier: MIT
// Licensed-host AutoCAD adapter source. It is intentionally not a runtime claim.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
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
        internal const string BootstrapConfigSha256EnvironmentVariable =
            "LIANG_PINGFA_NATIVE_BOOTSTRAP_CONFIG_SHA256";
        internal const string BootstrapRuntimePackageSha256EnvironmentVariable =
            "LIANG_PINGFA_NATIVE_BOOTSTRAP_RUNTIME_PACKAGE_SHA256";
        internal const string RuntimePackageSha256EnvironmentVariable =
            "LIANG_PINGFA_NATIVE_RUNTIME_PACKAGE_SHA256";
        internal const string RuntimePackageFormatVersion =
            "liang-pingfa/autocad-runtime-package/v1";
        internal const string AdapterAssemblyName =
            "LiangPingfa.NativeCad.AutoCAD.Adapter.dll";
        internal const string CoreAssemblyName =
            "LiangPingfa.NativeCad.Core.dll";
        internal const string ProtocolAssemblyName =
            "LiangPingfa.NativeCad.Protocol.dll";
        internal const string AdapterDepsName =
            "LiangPingfa.NativeCad.AutoCAD.Adapter.deps.json";
        private static readonly string[] RuntimeAuxiliaryFileNames =
        {
            "LiangPingfa.NativeCad.AutoCAD.Adapter.pdb",
            "LiangPingfa.NativeCad.Core.pdb",
            "LiangPingfa.NativeCad.Protocol.pdb",
            "README.md",
            "native-bootstrap-context.template.json",
        };

        internal static string Profile
        {
            get
            {
#if LPF_AUTOCAD_2024
                return "autocad2024";
#elif LPF_AUTOCAD_2025
                return "autocad2025";
#elif LPF_AUTOCAD_2026
                return "autocad2026";
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
#elif NET10_0_OR_GREATER
                return "net10";
#else
                return "net8";
#endif
            }
        }

        internal static string RuntimeTargetFramework
        {
            get
            {
#if NET48
                return "net48";
#elif NET10_0_OR_GREATER
                return "net10.0-windows";
#else
                return "net8.0-windows";
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

        /// <summary>
        /// Recompute the complete repository-authored runtime package
        /// fingerprint from the adapter's actual assembly directory.  The
        /// package explicitly excludes Autodesk/TSSD/stub binaries and PDB/
        /// documentation sidecars; those are never runtime dependencies.
        /// </summary>
        internal static string RuntimePackageFingerprint()
        {
            string assembly = Assembly.GetExecutingAssembly().Location;
            if (string.IsNullOrEmpty(assembly) ||
                !string.Equals(
                    Path.GetFileName(assembly),
                    AdapterAssemblyName,
                    StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "The adapter runtime assembly location is unavailable.");
            }

            string? directory = Path.GetDirectoryName(assembly);
            if (string.IsNullOrEmpty(directory))
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "The adapter runtime package directory is unavailable.");
            }

            RequireLoadedRuntimeAssembliesInDirectory(directory);
            return RuntimePackageFingerprintForDirectory(directory);
        }

        /// <summary>
        /// Testable package calculation that has the exact same byte contract
        /// as the private PowerShell receipt and Python configuration gate.
        /// </summary>
        internal static string RuntimePackageFingerprintForDirectory(string directory)
        {
            if (string.IsNullOrEmpty(directory) || !Path.IsPathRooted(directory))
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "The adapter runtime package directory is invalid.");
            }
            RequireExactRuntimePackageInventory(directory);

            List<RuntimePackageComponent> components =
                new List<RuntimePackageComponent>();
            foreach (string name in RuntimeComponentNames())
            {
                components.Add(CaptureRuntimeComponent(directory, name));
            }
            components.Sort(
                delegate(RuntimePackageComponent left, RuntimePackageComponent right)
                {
                    return string.CompareOrdinal(left.Name, right.Name);
                });

            StringBuilder canonical = new StringBuilder();
            canonical.Append(RuntimePackageFormatVersion).Append('\n');
            canonical.Append(Profile).Append('\n');
            canonical.Append(RuntimeTargetFramework).Append('\n');
            foreach (RuntimePackageComponent component in components)
            {
                canonical.Append(component.Name).Append('\t');
                canonical.Append(component.ByteSize.ToString(CultureInfo.InvariantCulture));
                canonical.Append('\t').Append(component.Sha256).Append('\n');
            }
            return CanonicalJson.Sha256Hex(
                new UTF8Encoding(false, true).GetBytes(canonical.ToString()));
        }

        internal static string RequireRuntimePackageFingerprint(string expected)
        {
            try
            {
                CanonicalJson.RequireSha256(
                    expected,
                    "runtime package fingerprint");
            }
            catch (CanonicalJsonException)
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "The runtime package fingerprint is invalid.");
            }

            string actual = RuntimePackageFingerprint();
            if (!string.Equals(actual, expected, StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "The runtime package fingerprint differs.");
            }
            return actual;
        }

        private static IEnumerable<string> RuntimeComponentNames()
        {
            yield return AdapterAssemblyName;
            yield return CoreAssemblyName;
            yield return ProtocolAssemblyName;
#if !NET48
            yield return AdapterDepsName;
#endif
        }

        private static void RequireLoadedRuntimeAssembliesInDirectory(
            string directory)
        {
            RequireLoadedRuntimeAssembly(
                Assembly.GetExecutingAssembly(),
                directory,
                AdapterAssemblyName);
            RequireLoadedRuntimeAssembly(
                typeof(GeometryExportV2).Assembly,
                directory,
                CoreAssemblyName);
            RequireLoadedRuntimeAssembly(
                typeof(CanonicalJson).Assembly,
                directory,
                ProtocolAssemblyName);
        }

        private static void RequireLoadedRuntimeAssembly(
            Assembly assembly,
            string directory,
            string expectedName)
        {
            string location = assembly.Location;
            string? parent = string.IsNullOrEmpty(location)
                ? null
                : Path.GetDirectoryName(location);
            if (
                string.IsNullOrEmpty(parent) ||
                !string.Equals(
                    Path.GetFileName(location),
                    expectedName,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    Path.GetFullPath(parent).TrimEnd(
                        Path.DirectorySeparatorChar,
                        Path.AltDirectorySeparatorChar),
                    Path.GetFullPath(directory).TrimEnd(
                        Path.DirectorySeparatorChar,
                        Path.AltDirectorySeparatorChar),
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "A loaded runtime assembly escaped the package directory.");
            }
        }

        private static void RequireExactRuntimePackageInventory(string directory)
        {
            HashSet<string> allowed = new HashSet<string>(
                StringComparer.Ordinal);
            foreach (string name in RuntimeComponentNames())
            {
                allowed.Add(name);
            }
            for (int index = 0; index < RuntimeAuxiliaryFileNames.Length; index++)
            {
                allowed.Add(RuntimeAuxiliaryFileNames[index]);
            }

            HashSet<string> seen = new HashSet<string>(
                StringComparer.OrdinalIgnoreCase);
            string[] entries;
            try
            {
                entries = Directory.GetFileSystemEntries(directory);
            }
            catch (Exception)
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "The adapter runtime package inventory is unavailable.");
            }

            for (int index = 0; index < entries.Length; index++)
            {
                string entry = entries[index];
                string name = Path.GetFileName(entry);
                FileAttributes attributes;
                try
                {
                    attributes = File.GetAttributes(entry);
                }
                catch (Exception)
                {
                    throw new AdapterFailureException(
                        "LPF_RUNTIME_PACKAGE",
                        "The adapter runtime package inventory is unavailable.");
                }
                if (
                    string.IsNullOrEmpty(name) ||
                    (attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint))
                        != 0 ||
                    !allowed.Contains(name) ||
                    !seen.Add(name))
                {
                    throw new AdapterFailureException(
                        "LPF_RUNTIME_PACKAGE",
                        "The adapter runtime package inventory differs.");
                }
            }
            if (seen.Count != allowed.Count)
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "The adapter runtime package inventory differs.");
            }
        }

        private static RuntimePackageComponent CaptureRuntimeComponent(
            string directory,
            string name)
        {
            string path = Path.Combine(directory, name);
            FileInfo before = new FileInfo(path);
            if (!before.Exists || before.Length <= 0 ||
                !string.Equals(before.Name, name, StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "A required runtime package component is unavailable.");
            }
            long size = before.Length;
            DateTime writeTime = before.LastWriteTimeUtc;
            string digest = HashFile(path);
            FileInfo after = new FileInfo(path);
            if (!after.Exists || after.Length != size ||
                after.LastWriteTimeUtc != writeTime)
            {
                throw new AdapterFailureException(
                    "LPF_RUNTIME_PACKAGE",
                    "A runtime package component changed while it was read.");
            }
            return new RuntimePackageComponent(name, size, digest);
        }

        private sealed class RuntimePackageComponent
        {
            internal RuntimePackageComponent(string name, long byteSize, string sha256)
            {
                Name = name;
                ByteSize = byteSize;
                Sha256 = sha256;
            }

            internal string Name { get; private set; }

            internal long ByteSize { get; private set; }

            internal string Sha256 { get; private set; }
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
