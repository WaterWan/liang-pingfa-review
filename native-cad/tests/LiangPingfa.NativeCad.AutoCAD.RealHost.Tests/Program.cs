// SPDX-License-Identifier: MIT
// Opt-in private runner. Public CI never supplies its licensed-host bindings.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Threading.Tasks;

namespace LiangPingfa.NativeCad.AutoCAD.RealHost.Tests
{
    internal static class Program
    {
        private static readonly string[] RequiredEnvironment =
        {
            "LPF_REALHOST_PHASE",
            "LPF_REALHOST_PROFILE",
            "LPF_REALHOST_PYTHON",
            "LPF_REALHOST_HOST",
            "LPF_REALHOST_CORE_CONSOLE",
            "LPF_REALHOST_ADAPTER_PACKAGE",
            "LIANG_PINGFA_REAL_HOST_RECEIPT",
            "LPF_REALHOST_NATIVE_CONFIG",
            "LPF_REALHOST_BOOTSTRAP",
            "LPF_REALHOST_SESSION",
            "LPF_REALHOST_SOURCE",
            "LPF_REALHOST_WORK_ROOT",
            "LPF_REALHOST_EVIDENCE_OUTPUT",
            "LPF_REALHOST_REPOSITORY_ROOT",
            "LPF_REALHOST_POWERSHELL",
        };

        private static int Main()
        {
            if (!string.Equals(
                    Environment.GetEnvironmentVariable("LPF_REALHOST_TESTS"),
                    "1",
                    StringComparison.Ordinal))
            {
                Console.WriteLine(
                    "SKIP: real-host qualification is opt-in; public stub builds are syntax-only.");
                return 0;
            }
            if (!string.Equals(
                    Environment.GetEnvironmentVariable("LIANG_PINGFA_RUN_REAL_HOST"),
                    "1",
                    StringComparison.Ordinal))
            {
                Console.Error.WriteLine(
                    "FAIL: LIANG_PINGFA_RUN_REAL_HOST=1 is required for licensed-host execution.");
                return 1;
            }

            Dictionary<string, string> values = new Dictionary<string, string>(
                StringComparer.Ordinal);
            foreach (string name in RequiredEnvironment)
            {
                string? value = Environment.GetEnvironmentVariable(name);
                if (string.IsNullOrWhiteSpace(value))
                {
                    Console.Error.WriteLine(
                        "FAIL: required real-host binding is unavailable.");
                    return 1;
                }
                values.Add(name, value);
            }
            if (!IsPhase(values["LPF_REALHOST_PHASE"]) ||
                !IsProfile(values["LPF_REALHOST_PROFILE"]) ||
                !AreBindingsSafe(values))
            {
                Console.Error.WriteLine(
                    "FAIL: real-host bindings are invalid or unavailable.");
                return 1;
            }

            string script = Path.Combine(
                values["LPF_REALHOST_REPOSITORY_ROOT"],
                "native-cad",
                "scripts",
                "qualify-real-host.ps1");
            if (!File.Exists(script))
            {
                Console.Error.WriteLine(
                    "FAIL: the repository qualification script is unavailable.");
                return 1;
            }

            try
            {
                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = values["LPF_REALHOST_POWERSHELL"],
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true,
                };
                start.ArgumentList.Add("-NoProfile");
                start.ArgumentList.Add("-NonInteractive");
                start.ArgumentList.Add("-ExecutionPolicy");
                start.ArgumentList.Add("Bypass");
                start.ArgumentList.Add("-File");
                start.ArgumentList.Add(script);
                AddArgument(start, "-Phase", values["LPF_REALHOST_PHASE"]);
                AddArgument(start, "-Profile", values["LPF_REALHOST_PROFILE"]);
                AddArgument(start, "-PythonExecutable", values["LPF_REALHOST_PYTHON"]);
                AddArgument(start, "-HostExecutable", values["LPF_REALHOST_HOST"]);
                AddArgument(start, "-CoreConsoleExecutable", values["LPF_REALHOST_CORE_CONSOLE"]);
                AddArgument(start, "-AdapterPackage", values["LPF_REALHOST_ADAPTER_PACKAGE"]);
                AddArgument(
                    start,
                    "-ReceiptPath",
                    values["LIANG_PINGFA_REAL_HOST_RECEIPT"]);
                AddArgument(start, "-NativeConfig", values["LPF_REALHOST_NATIVE_CONFIG"]);
                AddArgument(start, "-Bootstrap", values["LPF_REALHOST_BOOTSTRAP"]);
                AddArgument(start, "-SessionPath", values["LPF_REALHOST_SESSION"]);
                AddArgument(start, "-SourceDrawing", values["LPF_REALHOST_SOURCE"]);
                AddArgument(start, "-WorkRoot", values["LPF_REALHOST_WORK_ROOT"]);
                AddArgument(start, "-EvidenceOutput", values["LPF_REALHOST_EVIDENCE_OUTPUT"]);

                using (Process process = Process.Start(start) ??
                    throw new InvalidOperationException("qualification process did not start"))
                {
                    Task<string> standardOutputTask = process.StandardOutput.ReadToEndAsync();
                    Task<string> standardErrorTask = process.StandardError.ReadToEndAsync();
                    if (!process.WaitForExit(20 * 60 * 1000))
                    {
                        Console.Error.WriteLine(
                            "FAIL: private real-host qualification script failed.");
                        return 1;
                    }
                    Task.WaitAll(standardOutputTask, standardErrorTask);
                    string standardOutput = standardOutputTask.Result;
                    string standardError = standardErrorTask.Result;
                    if (process.ExitCode != 0)
                    {
                        Console.Error.WriteLine(
                            "FAIL: private real-host qualification script failed.");
                        return 1;
                    }
                    if (
                        standardOutput.IndexOf("\"status\":\"ok\"", StringComparison.Ordinal) < 0 ||
                        standardOutput.IndexOf("qualification", StringComparison.Ordinal) < 0 ||
                        standardError.Length != 0)
                    {
                        Console.Error.WriteLine(
                            "FAIL: real-host qualification did not produce its redacted summary.");
                        return 1;
                    }
                }
            }
            catch (Exception)
            {
                Console.Error.WriteLine(
                    "FAIL: private real-host qualification runner could not execute.");
                return 1;
            }

            Console.WriteLine(
                "PASS: private real-host qualification phase completed; no public runtime claim was made.");
            return 0;
        }

        private static void AddArgument(
            ProcessStartInfo start,
            string name,
            string value)
        {
            start.ArgumentList.Add(name);
            start.ArgumentList.Add(value);
        }

        private static bool IsPhase(string value)
        {
            return string.Equals(value, "audit", StringComparison.Ordinal) ||
                string.Equals(value, "apply", StringComparison.Ordinal);
        }

        private static bool IsProfile(string value)
        {
            return value == "autocad2024" ||
                value == "autocad2025" ||
                value == "autocad2026";
        }

        private static bool AreBindingsSafe(
            IReadOnlyDictionary<string, string> values)
        {
            foreach (string name in new[]
            {
                "LPF_REALHOST_PYTHON",
                "LPF_REALHOST_HOST",
                "LPF_REALHOST_CORE_CONSOLE",
                "LPF_REALHOST_NATIVE_CONFIG",
                "LPF_REALHOST_BOOTSTRAP",
                "LPF_REALHOST_SOURCE",
                // The supplied host is an explicit selection, not a shell
                // lookup. This keeps both Windows PowerShell 5.1 and pwsh
                // testable without printing its private executable path.
                "LPF_REALHOST_POWERSHELL",
                "LPF_REALHOST_POWERSHELL",
            })
            {
                if (!IsAbsoluteExistingFile(values[name]))
                {
                    return false;
                }
            }
            if (!IsPrivateReceiptFile(values["LIANG_PINGFA_REAL_HOST_RECEIPT"]))
            {
                return false;
            }
            foreach (string name in new[]
            {
                "LPF_REALHOST_ADAPTER_PACKAGE",
                "LPF_REALHOST_WORK_ROOT",
                "LPF_REALHOST_EVIDENCE_OUTPUT",
                "LPF_REALHOST_REPOSITORY_ROOT",
            })
            {
                if (!IsAbsoluteExistingDirectory(values[name]))
                {
                    return false;
                }
            }
            string session = values["LPF_REALHOST_SESSION"];
            return Path.IsPathFullyQualified(session) &&
                session.EndsWith(".json", StringComparison.OrdinalIgnoreCase) &&
                !File.Exists(session) &&
                !Directory.Exists(session);
        }

        private static bool IsAbsoluteExistingFile(string value)
        {
            return TryGetNormalLocalFile(value, out _);
        }

        private static bool IsAbsoluteExistingDirectory(string value)
        {
            return Path.IsPathFullyQualified(value) && Directory.Exists(value);
        }

        private static bool IsPrivateReceiptFile(string value)
        {
            if (!TryGetNormalLocalFile(value, out string path))
            {
                return false;
            }

            try
            {
                FileSecurity security = new FileInfo(path).GetAccessControl();
                SecurityIdentifier currentUser = WindowsIdentity.GetCurrent().User
                    ?? throw new InvalidOperationException("current user SID is unavailable");
                SecurityIdentifier system = new SecurityIdentifier(
                    WellKnownSidType.LocalSystemSid,
                    null);
                SecurityIdentifier administrators = new SecurityIdentifier(
                    WellKnownSidType.BuiltinAdministratorsSid,
                    null);
                IdentityReference? owner = security.GetOwner(typeof(SecurityIdentifier));
                if (owner is not SecurityIdentifier ownerSid ||
                    (ownerSid != currentUser && ownerSid != system &&
                        ownerSid != administrators))
                {
                    return false;
                }

                bool hasCurrentUser = false;
                bool hasSystem = false;
                AuthorizationRuleCollection rules = security.GetAccessRules(
                    includeExplicit: true,
                    includeInherited: true,
                    targetType: typeof(SecurityIdentifier));
                foreach (AuthorizationRule rule in rules)
                {
                    if (rule is not FileSystemAccessRule accessRule ||
                        accessRule.AccessControlType != AccessControlType.Allow ||
                        accessRule.IdentityReference is not SecurityIdentifier sid ||
                        (sid != currentUser && sid != system))
                    {
                        return false;
                    }
                    hasCurrentUser |= sid == currentUser;
                    hasSystem |= sid == system;
                }
                return hasCurrentUser && hasSystem;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static bool TryGetNormalLocalFile(string value, out string path)
        {
            path = string.Empty;
            if (
                string.IsNullOrWhiteSpace(value) ||
                value.IndexOf('\0') >= 0 ||
                value.IndexOf('"') >= 0 ||
                value.IndexOf('\'') >= 0 ||
                value.StartsWith(@"\\", StringComparison.Ordinal) ||
                !Path.IsPathFullyQualified(value))
            {
                return false;
            }

            try
            {
                string fullPath = Path.GetFullPath(value);
                if (
                    fullPath.Length < 3 ||
                    !char.IsAsciiLetter(fullPath[0]) ||
                    fullPath[1] != ':' ||
                    fullPath[2] != Path.DirectorySeparatorChar ||
                    HasRelativeComponent(value) ||
                    HasReparsePointComponent(fullPath) ||
                    !File.Exists(fullPath) ||
                    Directory.Exists(fullPath)
                )
                {
                    return false;
                }
                path = fullPath;
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static bool HasRelativeComponent(string value)
        {
            string[] components = value.Split(
                new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                StringSplitOptions.RemoveEmptyEntries);
            foreach (string component in components)
            {
                if (component == "." || component == "..")
                {
                    return true;
                }
            }
            return false;
        }

        private static bool HasReparsePointComponent(string fullPath)
        {
            string current = fullPath.Substring(0, 3);
            foreach (string component in fullPath.Substring(3).Split(
                new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                StringSplitOptions.RemoveEmptyEntries))
            {
                current = Path.Combine(current, component);
                if ((File.Exists(current) || Directory.Exists(current)) &&
                    (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                {
                    return true;
                }
            }
            return false;
        }
    }
}
