// SPDX-License-Identifier: MIT
// SDK-free reflection/static checks only. They do not load an Autodesk host.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using LiangPingfa.NativeCad.AutoCAD.Adapter;
using LiangPingfa.NativeCad.Core;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.AutoCAD.Adapter.Tests
{
    internal static class Program
    {
        private static int Main(string[] arguments)
        {
            try
            {
                // `dotnet run --nologo` forwards this SDK cosmetic option to
                // the test executable on supported SDKs. It is not a test
                // command and must not turn the default check into a usage
                // failure.
                if (arguments.Length == 1 &&
                    string.Equals(arguments[0], "--nologo", StringComparison.Ordinal))
                {
                    arguments = new string[0];
                }

                if (arguments.Length == 2 &&
                    string.Equals(
                        arguments[0],
                        "canonical-console-export",
                        StringComparison.Ordinal))
                {
                    return CanonicalizeConsoleExport(arguments[1]);
                }

                if (arguments.Length == 2 &&
                    string.Equals(
                        arguments[0],
                        "file-identity-fingerprint",
                        StringComparison.Ordinal))
                {
                    return EmitFileIdentityFingerprint(arguments[1]);
                }

                if (arguments.Length == 2 &&
                    string.Equals(
                        arguments[0],
                        "runtime-package-fingerprint",
                        StringComparison.Ordinal))
                {
                    return EmitRuntimePackageFingerprint(arguments[1]);
                }

                if (arguments.Length == 2 &&
                    string.Equals(arguments[0], "pipe-tokens", StringComparison.Ordinal))
                {
                    return EmitPipeTokens(arguments[1]);
                }

                if (arguments.Length == 1 &&
                    string.Equals(
                        arguments[0],
                        "canonical-capabilities",
                        StringComparison.Ordinal))
                {
                    return EmitCanonicalCapabilities();
                }

                if (arguments.Length == 6 &&
                    string.Equals(
                        arguments[0],
                        "bootstrap-advertisement",
                        StringComparison.Ordinal))
                {
                    return EmitBootstrapAdvertisement(
                        arguments[1],
                        arguments[2],
                        arguments[3],
                        arguments[4],
                        arguments[5]);
                }

                if (arguments.Length != 0)
                {
                    Console.Error.WriteLine(
                        "Usage: canonical-console-export <canonical-json-path> | " +
                        "file-identity-fingerprint <local-ntfs-dwg-path> | " +
                        "runtime-package-fingerprint <package-directory> | " +
                        "canonical-capabilities | pipe-tokens <count> | " +
                        "bootstrap-advertisement <nonce> <config-sha256> " +
                        "<plugin-sha256> <issued-at> <expires-at>");
                    return 64;
                }

                CheckCommandMetadata();
                CheckSyntaxStubTestMarker();
                CheckSdkFreeBridgeContractFakes();
                CheckFixedNtfsVolumePolicy();
                CheckCanonicalCapabilities();
                CheckPipeTokenGrammar();
                CheckProfileRuntimeBinding();
                CheckBootstrapAdvertisementWireShape();
                CheckPrivateOwnerPolicy();
                BridgeExpiryLifetimeTests.Run();
                CheckBootstrapDoesNotBlockCommandContext();
                CheckQueuedDispatcherRunsAfterCommandReturns();
                CheckFieldBackedDbTextPolicy();
                CheckBaseLeftDbTextProfile();
                CheckSavedDocumentReadGate();
                CheckDocumentedDocumentGateApiAllowlist();
                CheckConsoleExportCanonicalProfile();
                CheckRetainedPrivateBindingCache();
                CheckErasedPhysicalSequencePolicy();
                CheckInitialWriteCapabilityBoundary();
                CheckStableBindingCapture();
                CheckFileIdentityProjection();
                CheckStaticSafetyBoundaries();
                Console.WriteLine("PASS: AutoCAD adapter syntax-only metadata and source safety checks");
                return 0;
            }

            catch (Exception exception)
            {
                Console.Error.WriteLine("FAIL: " + exception);
                return 1;
            }
        }

        private static int EmitFileIdentityFingerprint(string path)
        {
            NativeSourceBindingV2 binding = NativeSourceBindingCapture.Capture(path);
            Console.WriteLine(binding.FileIdentityFingerprint);
            return 0;
        }

        private static int EmitRuntimePackageFingerprint(string directory)
        {
            Console.WriteLine(
                AdapterIdentity.RuntimePackageFingerprintForDirectory(
                    Path.GetFullPath(directory)));
            return 0;
        }

        private static int EmitPipeTokens(string rawCount)
        {
            int count;
            if (!int.TryParse(
                    rawCount,
                    NumberStyles.None,
                    CultureInfo.InvariantCulture,
                    out count) ||
                count < 1 ||
                count > 1024)
            {
                Console.Error.WriteLine("pipe-tokens requires a count from 1 through 1024.");
                return 64;
            }

            for (int index = 0; index < count; index++)
            {
                Console.WriteLine(
                    new string(Path.DirectorySeparatorChar, 2) + "." +
                    Path.DirectorySeparatorChar + "pipe" +
                    Path.DirectorySeparatorChar + AdapterIdentity.BridgePipePrefix +
                    NativePipeBridgeServer.RandomPipeToken());
            }

            return 0;
        }

        private static int EmitCanonicalCapabilities()
        {
            Console.WriteLine(CanonicalJson.Serialize(
                NativeCadCapabilities.ToWireValue(AdapterIdentity.Capabilities)));
            return 0;
        }

        private static int EmitBootstrapAdvertisement(
            string nonce,
            string configSha256,
            string pluginSha256,
            string issuedAt,
            string expiresAt)
        {
            DateTime issuedUtc;
            DateTime expiresUtc;
            if (!DateTime.TryParseExact(
                    issuedAt,
                    "yyyy-MM-dd'T'HH:mm:ss'Z'",
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out issuedUtc) ||
                !DateTime.TryParseExact(
                    expiresAt,
                    "yyyy-MM-dd'T'HH:mm:ss'Z'",
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out expiresUtc))
            {
                Console.Error.WriteLine("bootstrap timestamps must be whole-second UTC values.");
                return 64;
            }
            byte[] bytes = NativeBridgeAdvertisement.SerializeForTest(
                nonce,
                configSha256,
                pluginSha256,
                issuedUtc,
                expiresUtc);
            Console.Write(Encoding.UTF8.GetString(bytes));
            return 0;
        }

        private static void CheckProfileRuntimeBinding()
        {
#if LPF_AUTOCAD_2024
            Assert(
                string.Equals(AdapterIdentity.HostRuntime, "net48", StringComparison.Ordinal),
                "The 2024 adapter profile must bind net48.");
            Assert(
                string.Equals(AdapterIdentity.Profile, "autocad2024", StringComparison.Ordinal) &&
                string.Equals(AdapterIdentity.HostRelease, "2024", StringComparison.Ordinal),
                "The 2024 adapter must advertise its matching AutoCAD profile and release.");
#elif LPF_AUTOCAD_2025
            Assert(
                string.Equals(AdapterIdentity.HostRuntime, "net8", StringComparison.Ordinal),
                "The 2025 adapter profile must bind net8.");
            Assert(
                string.Equals(AdapterIdentity.Profile, "autocad2025", StringComparison.Ordinal) &&
                string.Equals(AdapterIdentity.HostRelease, "2025", StringComparison.Ordinal),
                "The 2025 adapter must advertise its matching AutoCAD profile and release.");
#elif LPF_AUTOCAD_2026
            Assert(
                string.Equals(AdapterIdentity.HostRuntime, "net10", StringComparison.Ordinal),
                "The 2026 adapter profile must bind net10.");
            Assert(
                string.Equals(AdapterIdentity.Profile, "autocad2026", StringComparison.Ordinal) &&
                string.Equals(AdapterIdentity.HostRelease, "2026", StringComparison.Ordinal),
                "The 2026 adapter must advertise its matching AutoCAD profile and release.");
#else
            throw new InvalidOperationException(
                "The adapter test host was built without an explicit profile.");
#endif
        }

        private static void CheckBootstrapAdvertisementWireShape()
        {
            string nonce = new string('a', 43);
            string configSha256 = new string('b', 64);
            string pluginSha256 = new string('c', 64);
            DateTime issuedUtc = DateTime.UtcNow;
            string advertisement = Encoding.UTF8.GetString(
                NativeBridgeAdvertisement.SerializeForTest(
                    nonce,
                    configSha256,
                    pluginSha256,
                    issuedUtc,
                    issuedUtc.AddMinutes(2)));
            Assert(
                advertisement.IndexOf(
                    "\"schema_version\":\"liang-pingfa/native-bridge-bootstrap/v1\"",
                    StringComparison.Ordinal) >= 0 &&
                advertisement.IndexOf("\"session_id\"", StringComparison.Ordinal) < 0 &&
                advertisement.IndexOf("\"config_sha256\":\"" + configSha256 + "\"",
                    StringComparison.Ordinal) >= 0 &&
                advertisement.IndexOf("\"runtime\":\"" + AdapterIdentity.HostRuntime + "\"",
                    StringComparison.Ordinal) >= 0,
                "Bootstrap advertisement lost its strict client-owned-session binding.");
        }

        private static void CheckCanonicalCapabilities()
        {
            string[] expected =
            {
                "create_review_marker/v1",
                "read.exact_geometry/v1",
                "read.inventory/v1",
                "translate_dbtext/v1",
            };
            Assert(
                AdapterIdentity.Capabilities.Count == expected.Length,
                "Adapter capability count drifted.");
            for (int index = 0; index < expected.Length; index++)
            {
                Assert(
                    string.Equals(
                        AdapterIdentity.Capabilities[index],
                        expected[index],
                        StringComparison.Ordinal),
                    "Adapter capability order drifted.");
            }

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    NativeCadCapabilities.RequireAutoCadAdapter(
                        new[]
                        {
                            "create_review_marker/v1",
                            "read.exact_geometry/v1",
                            "read.exact_geometry/v1",
                            "translate_dbtext/v1",
                        },
                        "duplicate capabilities");
                },
                "Duplicate adapter capabilities were accepted.");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    NativeCadCapabilities.RequireAutoCadAdapter(
                        new[]
                        {
                            "read.exact_geometry/v1",
                            "create_review_marker/v1",
                            "read.inventory/v1",
                            "translate_dbtext/v1",
                        },
                        "unsorted capabilities");
                },
                "Unsorted adapter capabilities were accepted.");
            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    NativeCadCapabilities.RequireAutoCadAdapter(
                        new[]
                        {
                            "create_review_marker/v1",
                            "read.exact_geometry/v1",
                            "read.inventory/v1",
                            "translate_dbtext/v2",
                        },
                        "drifted capabilities");
                },
                "Drifted adapter capabilities were accepted.");
        }

        private static void CheckFixedNtfsVolumePolicy()
        {
            DriveType[] rejected =
            {
                DriveType.Unknown,
                DriveType.NoRootDirectory,
                DriveType.Removable,
                DriveType.Network,
                DriveType.CDRom,
                DriveType.Ram,
            };
            for (int index = 0; index < rejected.Length; index++)
            {
                DriveType type = rejected[index];
                using (PrivatePathPolicy.UseTestVolumeInfoReader(
                    _ => new PrivatePathPolicy.PrivateVolumeInfo(type, "NTFS")))
                {
                    bool failedClosed = false;
                    try
                    {
                        PrivatePathPolicy.RequireNtfs(SyntheticPrivatePath());
                    }
                    catch (AdapterFailureException)
                    {
                        failedClosed = true;
                    }

                    Assert(
                        failedClosed,
                        "Non-fixed NTFS drive type was accepted: " + type);
                }
            }

            using (PrivatePathPolicy.UseTestVolumeInfoReader(
                _ => new PrivatePathPolicy.PrivateVolumeInfo(DriveType.Fixed, "NTFS")))
            {
                PrivatePathPolicy.RequireNtfs(SyntheticPrivatePath());
            }

            using (PrivatePathPolicy.UseTestVolumeInfoReader(
                _ => new PrivatePathPolicy.PrivateVolumeInfo(DriveType.Fixed, "ReFS")))
            {
                bool rejectedFormat = false;
                try
                {
                    PrivatePathPolicy.RequireNtfs(SyntheticPrivatePath());
                }
                catch (AdapterFailureException)
                {
                    rejectedFormat = true;
                }

                Assert(rejectedFormat, "A non-NTFS fixed drive was accepted.");
            }
        }

        private static string SyntheticPrivatePath()
        {
            return "Z" + ':' + Path.DirectorySeparatorChar +
                "private" + Path.DirectorySeparatorChar + "artifact.json";
        }

        private static void CheckPipeTokenGrammar()
        {
            byte[] allLetters = Base64UrlBytes(new string('A', 32));
            byte[] allDigits = Base64UrlBytes(new string('0', 32));
            byte[] lowDiversity = Base64UrlBytes(RepeatToLength("AA0", 32));
            byte[] sevenDistinct = Base64UrlBytes(
                RepeatToLength("ABCDEF0", 32));
            byte[] eightDistinct = Base64UrlBytes(
                RepeatToLength("ABCDEFG0", 32));
            byte[] finalHyphen = Base64UrlBytes(
                RepeatToLength("ABCDEFG0", 31) + "-");
            byte[] normal = Base64UrlBytes("Ab0Cd1Ef2Gh3Ij4Kl5Mn6Op7Qr8St9Uv");

            int calls = 0;
            string token = NativePipeBridgeServer.RandomPipeToken(
                delegate
                {
                    calls++;
                    return calls == 1 ? lowDiversity : normal;
                });
            Assert(
                calls == 2,
                "Low-diversity pipe token was not rejected and retried.");
            AssertPipeTokenGrammar(token);

            AssertPipeTokenRejected(allLetters, "All-letter pipe token was accepted.");
            AssertPipeTokenRejected(allDigits, "All-digit pipe token was accepted.");
            AssertPipeTokenRejected(
                lowDiversity,
                "Two-distinct-character pipe token was accepted.");
            AssertPipeTokenRejected(
                sevenDistinct,
                "Seven-distinct-character pipe token was accepted.");
            AssertPipeTokenGrammar(
                NativePipeBridgeServer.RandomPipeToken(
                    delegate { return eightDistinct; }));
            AssertPipeTokenRejected(
                finalHyphen,
                "Final-hyphen pipe token was accepted.");
            bool malformedRandomRejected = false;
            try
            {
                NativePipeBridgeServer.RandomPipeToken(
                    delegate { return new byte[23]; });
            }
            catch (AdapterFailureException)
            {
                malformedRandomRejected = true;
            }

            Assert(
                malformedRandomRejected,
                "An invalid pipe-token random source was not rejected.");
            for (int index = 0; index < 128; index++)
            {
                AssertPipeTokenGrammar(NativePipeBridgeServer.RandomPipeToken());
            }
        }

        private static void AssertPipeTokenGrammar(string token)
        {
            Assert(token.Length == 32, "Pipe token length changed.");
            bool letter = false;
            bool digit = false;
            HashSet<char> distinct = new HashSet<char>();
            for (int index = 0; index < token.Length; index++)
            {
                char character = token[index];
                Assert(
                    (character >= 'A' && character <= 'Z') ||
                    (character >= 'a' && character <= 'z') ||
                    (character >= '0' && character <= '9') ||
                    character == '_' ||
                    character == '-',
                    "Pipe token contains a Python-incompatible character.");
                letter |= (character >= 'A' && character <= 'Z') ||
                    (character >= 'a' && character <= 'z');
                digit |= character >= '0' && character <= '9';
                distinct.Add(character);
            }

            Assert(letter && digit, "Pipe token misses a required grammar class.");
            Assert(
                distinct.Count >= 8,
                "Pipe token misses Python's distinct-character minimum.");
            Assert(
                token[token.Length - 1] != '-',
                "Pipe token ends in a Python-disallowed hyphen.");
        }

        private static void AssertPipeTokenRejected(byte[] candidate, string message)
        {
            bool rejected = false;
            try
            {
                NativePipeBridgeServer.RandomPipeToken(
                    delegate { return candidate; });
            }
            catch (AdapterFailureException)
            {
                rejected = true;
            }

            Assert(rejected, message);
        }

        private static byte[] Base64UrlBytes(string candidate)
        {
            Assert(candidate.Length == 32, "Test pipe token must be fixed length.");
            return Convert.FromBase64String(
                candidate.Replace('-', '+').Replace('_', '/'));
        }

        private static string RepeatToLength(string pattern, int length)
        {
            StringBuilder value = new StringBuilder(length);
            while (value.Length < length)
            {
                value.Append(pattern);
            }

            return value.ToString(0, length);
        }

        private static void CheckPrivateOwnerPolicy()
        {
            const string user = "S-1-5-21-100";
            const string system = "S-1-5-18";
            const string administrators = "S-1-5-32-544";
            Assert(
                WindowsPrivateAcl.IsTrustedPrivateOwner(user, user, user),
                "Current user private owner was rejected.");
            Assert(
                WindowsPrivateAcl.IsTrustedPrivateOwner(system, user, user),
                "SYSTEM private owner was rejected.");
            Assert(
                WindowsPrivateAcl.IsTrustedPrivateOwner(
                    administrators,
                    user,
                    administrators),
                "Administrators default owner was rejected.");
            Assert(
                !WindowsPrivateAcl.IsTrustedPrivateOwner(administrators, user, user),
                "Administrators was trusted merely because an elevated user may be a member.");
            Assert(
                !WindowsPrivateAcl.IsTrustedPrivateOwner(
                    "S-1-5-21-999",
                    user,
                    administrators),
                "An unrelated private owner was trusted.");
        }

        private static int CanonicalizeConsoleExport(string path)
        {
            try
            {
                object? raw = CanonicalJson.RequireCanonicalUtf8(
                    File.ReadAllBytes(path),
                    NativeCadProtocolV2.MaxConsoleExportBytes,
                    NativeCadCanonicalJsonProfiles.ConsoleExport);
                Dictionary<string, object?> payload = raw as Dictionary<string, object?> ??
                    throw new InvalidOperationException(
                        "Console-export canonicalization requires an object.");
                byte[] canonical =
                    NativeConsoleArtifactWriter.CanonicalizeConsoleExportPayload(
                        payload);
                Console.WriteLine(CanonicalJson.Serialize(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "canonical_sha256", CanonicalJson.Sha256Hex(canonical) },
                        { "canonical_utf8_bytes", (long)canonical.Length },
                    }));
                return 0;
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine(
                    "Console-export canonicalization rejected: " +
                    exception.Message);
                return 2;
            }
        }

        private static void CheckCommandMetadata()
        {
            Type commands = typeof(NativePluginCommands);
            Dictionary<string, string> expected = new Dictionary<string, string>(
                StringComparer.Ordinal)
            {
                { "BootstrapBridge", "LPF_NATIVE_BRIDGE_BOOTSTRAP" },
                { "ExecuteManifest", "LPF_NATIVE_EXECUTE_MANIFEST" },
                { "ExportManifest", "LPF_NATIVE_EXPORT_MANIFEST" },
            };
            foreach (KeyValuePair<string, string> item in expected)
            {
                MethodInfo method = commands.GetMethod(
                    item.Key,
                    BindingFlags.Public | BindingFlags.Static) ??
                    throw new InvalidOperationException("Missing fixed command: " + item.Key);
                Assert(
                    method.GetParameters().Length == 0,
                    "Fixed CAD command must not accept arbitrary parameters: " +
                    item.Value);
                bool found = false;
                bool expectedSession =
                    string.Equals(item.Key, "BootstrapBridge", StringComparison.Ordinal);
                foreach (CustomAttributeData attribute in method.CustomAttributes)
                {
                    if (attribute.AttributeType.FullName ==
                        "Autodesk.AutoCAD.Runtime.CommandMethodAttribute" &&
                        attribute.ConstructorArguments.Count == 2 &&
                        string.Equals(
                            attribute.ConstructorArguments[0].Value as string,
                            item.Value,
                            StringComparison.Ordinal) &&
                        Convert.ToInt32(
                            attribute.ConstructorArguments[1].Value) ==
                            (expectedSession ? 1 : 0))
                    {
                        found = true;
                    }
                }

                Assert(found, "Missing exact CommandMethod metadata: " + item.Value);
            }

            Assert(
                typeof(NativeExtensionApplication).GetInterface(
                    "Autodesk.AutoCAD.Runtime.IExtensionApplication") != null,
                "Plugin entry does not implement IExtensionApplication.");
        }

        private static void CheckSyntaxStubTestMarker()
        {
            string stub = Path.Combine(
                AppContext.BaseDirectory,
                "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll");
            Assert(File.Exists(stub), "The dedicated syntax test host lacks its stub DLL.");
            Assembly assembly = Assembly.LoadFrom(stub);
            bool marker = false;
            foreach (CustomAttributeData attribute in assembly.CustomAttributes)
            {
                if (attribute.AttributeType.FullName ==
                    "System.Reflection.AssemblyMetadataAttribute" &&
                    attribute.ConstructorArguments.Count == 2 &&
                    string.Equals(
                        attribute.ConstructorArguments[0].Value as string,
                        "LiangPingfaAutoCadApiStubs",
                        StringComparison.Ordinal) &&
                    string.Equals(
                        attribute.ConstructorArguments[1].Value as string,
                        "syntax-only",
                        StringComparison.Ordinal))
                {
                    marker = true;
                }
            }

            Assert(marker, "The stub assembly lacks its syntax-only metadata marker.");
            Type dbObject = assembly.GetType(
                "Autodesk.AutoCAD.DatabaseServices.DBObject") ??
                throw new InvalidOperationException("The DBObject stub is absent.");
            PropertyInfo hasFields = dbObject.GetProperty("HasFields") ??
                throw new InvalidOperationException("The DBObject.HasFields stub is absent.");
            MethodInfo getField = dbObject.GetMethod("GetField") ??
                throw new InvalidOperationException("The DBObject.GetField stub is absent.");
            Assert(
                hasFields.PropertyType == typeof(bool) &&
                getField.ReturnType.FullName ==
                    "Autodesk.AutoCAD.DatabaseServices.ObjectId",
                "The field-detection stub signatures differ from AutoCAD.");
            Type blockTableRecord = assembly.GetType(
                "Autodesk.AutoCAD.DatabaseServices.BlockTableRecord") ??
                throw new InvalidOperationException("The BlockTableRecord stub is absent.");
            PropertyInfo includingErased = blockTableRecord.GetProperty("IncludingErased") ??
                throw new InvalidOperationException(
                    "The documented BlockTableRecord.IncludingErased stub is absent.");
            Assert(
                includingErased.PropertyType == blockTableRecord &&
                includingErased.CanRead &&
                !includingErased.CanWrite &&
                includingErased.GetMethod != null &&
                includingErased.GetMethod.IsPublic,
                "The IncludingErased stub signature differs from AutoCAD.");
            Type document = assembly.GetType(
                "Autodesk.AutoCAD.ApplicationServices.Document") ??
                throw new InvalidOperationException("The Document stub is absent.");
            Type database = assembly.GetType(
                "Autodesk.AutoCAD.DatabaseServices.Database") ??
                throw new InvalidOperationException("The Database stub is absent.");
            Type transactionManager = assembly.GetType(
                "Autodesk.AutoCAD.DatabaseServices.TransactionManager") ??
                throw new InvalidOperationException("The TransactionManager stub is absent.");
            Assert(
                document.GetProperty("Saved") == null &&
                database.GetProperty("FingerprintGuid") != null &&
                database.GetProperty("VersionGuid") != null &&
                transactionManager.GetProperty("TopTransaction") == null,
                "The syntax stub exposes an undocumented clean-state member.");
        }

        private static void CheckStaticSafetyBoundaries()
        {
            string root = Directory.GetCurrentDirectory();
            string adapter = Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter");
            Assert(Directory.Exists(adapter), "Adapter source root is unavailable.");
            string combined = string.Empty;
            foreach (string path in Directory.GetFiles(adapter, "*.cs"))
            {
                combined += File.ReadAllText(path) + "\n";
            }

            foreach (string forbidden in new[]
            {
                "Editor.Command",
                "SendStringToExecute",
                "SendKeys",
                "SendInput",
                "GetSelection",
                "Application.ShowModalDialog",
                "Marshal.GetActiveObject",
                "AcadApplication",
                "RunCommand",
                "LispData",
                "GeometricExtents",
            })
            {
                Assert(
                    combined.IndexOf(forbidden, StringComparison.OrdinalIgnoreCase) < 0,
                    "Forbidden UI/arbitrary command API appears in adapter source: " +
                    forbidden);
            }

            foreach (string required in new[]
            {
                "LIANG_PINGFA_NATIVE_MANIFEST",
                "LIANG_PINGFA_NATIVE_RESULT",
                "LIANG_PINGFA_NATIVE_RUN_ID",
                "LIANG_PINGFA_NATIVE_PRIVATE_ROOT",
                "GetObjectId",
                "StartTransaction",
                "Abort",
                "Commit",
                "SaveAs",
                "ReadDwgFile",
                "ExecuteInCommandContextAsync",
                "CreateNamedPipe",
                "PipeRejectRemoteClients",
                "HasFields",
            })
            {
                Assert(
                    combined.IndexOf(required, StringComparison.Ordinal) >= 0,
                    "Required fixed adapter boundary is absent: " + required);
            }

            string database = File.ReadAllText(Path.Combine(
                adapter,
                "AutodeskCadDatabase.cs"));
            Assert(
                !Regex.IsMatch(
                    database,
                    @"ReadDwgFile\s*\(\s*privatePath\s*,\s*" +
                    @"FileOpenMode\.OpenForReadAndAllShare\s*,\s*false\s*,",
                    RegexOptions.Singleline | RegexOptions.CultureInvariant),
                "Unattended readback must not use dialog-capable allowCPConversion:false.");
            Assert(
                Regex.IsMatch(
                    database,
                    @"ReadDwgFile\s*\(\s*privatePath\s*,\s*" +
                    @"FileOpenMode\.OpenForReadAndAllShare\s*,\s*true\s*,\s*" +
                    @"string\.Empty\s*\)",
                    RegexOptions.Singleline | RegexOptions.CultureInvariant),
                "Fresh private-DWG readback must use documented silent code-page conversion.");
            Assert(
                database.IndexOf("reopened.SaveAs", StringComparison.Ordinal) < 0,
                "The readback database must never be saved after code-page conversion.");
        }

        private static void CheckSdkFreeBridgeContractFakes()
        {
            const string sessionId =
                "native-session-0123456789abcdef0123456789abcdef";
            const string clientNonce =
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq";
            const string challenge =
                "QRSTUVWXYZabcdefghijklmnopqrABCDEFGHIJKLMNO";
            const string bridgeNonce =
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ";
            string expected = IndependentChallengeResponse(
                sessionId,
                clientNonce,
                challenge,
                bridgeNonce);
            Assert(
                string.Equals(
                    BridgeChallengeResponse.Derive(
                        sessionId,
                        clientNonce,
                        challenge,
                        bridgeNonce),
                    expected,
                    StringComparison.Ordinal),
                "Bridge challenge response differs from independent framing.");

            Dictionary<string, object?> allowed =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "protocol_version", "liang-pingfa/native-bridge/v1" },
                    { "id", "0123456789abcdef0123456789abcdef" },
                    { "method", "health" },
                    {
                        "params",
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "session_id", sessionId },
                        }
                    },
                };
            BridgeRequest parsed = BridgeRequest.Parse(allowed);
            Assert(
                string.Equals(parsed.Method, "health", StringComparison.Ordinal),
                "Fake bridge request did not retain the allowlisted method.");
            Assert(
                string.Equals(parsed.SessionId, sessionId, StringComparison.Ordinal),
                "The client-proposed session identifier was not retained.");
            BridgeSessionOwnership ownership = new BridgeSessionOwnership();
            Assert(
                ownership.BindFirstHealthOrRequireSame(parsed),
                "First health did not bind the client-owned session ID.");
            BridgeRequest getSession = new BridgeRequest(
                "fedcba9876543210fedcba9876543210",
                "get_session",
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "session_id", sessionId },
                    { "client_nonce", clientNonce },
                    { "challenge", challenge },
                },
                sessionId);
            Assert(
                !ownership.BindFirstHealthOrRequireSame(getSession),
                "Same-ID get_session was not accepted after health.");
            ownership.MarkSessionDescriptorIssued();
            bool duplicateHandshakeRejected = false;
            try
            {
                ownership.BindFirstHealthOrRequireSame(getSession);
            }
            catch (AdapterFailureException)
            {
                duplicateHandshakeRejected = true;
            }

            Assert(
                duplicateHandshakeRejected,
                "Duplicate get_session did not invalidate the protocol state.");
            BridgeSessionOwnership mismatchedOwnership =
                new BridgeSessionOwnership();
            mismatchedOwnership.BindFirstHealthOrRequireSame(parsed);
            BridgeRequest differentId = new BridgeRequest(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "health",
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    {
                        "session_id",
                        "native-session-ffffffffffffffffffffffffffffffff"
                    },
                },
                "native-session-ffffffffffffffffffffffffffffffff");
            bool differentIdRejected = false;
            try
            {
                mismatchedOwnership.BindFirstHealthOrRequireSame(differentId);
            }
            catch (AdapterFailureException)
            {
                differentIdRejected = true;
            }

            Assert(
                differentIdRejected,
                "A different session ID did not invalidate the protocol state.");
            allowed["method"] = "arbitrary_command";
            bool rejected = false;
            try
            {
                BridgeRequest.Parse(allowed);
            }
            catch (AdapterFailureException)
            {
                rejected = true;
            }

            Assert(rejected, "Fake bridge accepted an arbitrary method.");
        }

        private static void CheckBootstrapDoesNotBlockCommandContext()
        {
            string root = Directory.GetCurrentDirectory();
            string commands = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "NativePluginCommands.cs"));
            string bridge = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "NativePipeBridge.cs"));
            Assert(
                commands.IndexOf(".GetAwaiter().GetResult()", StringComparison.Ordinal) < 0 &&
                commands.IndexOf(".Wait()", StringComparison.Ordinal) < 0 &&
                bridge.IndexOf(".GetAwaiter().GetResult()", StringComparison.Ordinal) < 0 &&
                bridge.IndexOf(".Wait()", StringComparison.Ordinal) < 0,
                "Bootstrap path synchronously waits for a command-context callback.");
            Assert(
                bridge.IndexOf("\"session_id\", server.", StringComparison.Ordinal) < 0,
                "Bootstrap advertisement must not claim a server-generated session ID.");
        }

        private static void CheckQueuedDispatcherRunsAfterCommandReturns()
        {
            Action? queued = null;
            bool commandReturned = false;
            bool callbackRan = false;
            using (AutodeskCommandContextDispatcher.UseTestDispatcher(
                (callback, state) =>
                {
                    queued = () => callback(state).GetAwaiter().GetResult();
                }))
            {
                AutodeskCommandContextDispatcher.Execute(
                    _ =>
                    {
                        Assert(
                            commandReturned,
                            "Queued command-context work ran before bootstrap returned.");
                        callbackRan = true;
                        return System.Threading.Tasks.Task.CompletedTask;
                    },
                    new object());
                Assert(queued != null, "Test dispatcher did not queue work.");
                Assert(!callbackRan, "Queued command-context work ran synchronously.");
                commandReturned = true;
                queued!();
            }

            Assert(callbackRan, "Queued command-context callback did not complete.");
        }

        private static void CheckFieldBackedDbTextPolicy()
        {
            // Two different field expressions can evaluate to the same
            // displayed text.  The narrow profile rejects both before an
            // evaluated TextString can become editable or protected state.
            foreach (string expression in new[]
            {
                "%<\\AcVar Filename \\f \"%tc1\">%",
                "%<\\AcExpr (1+1) \\f \"%tc1\">%",
            })
            {
                bool rejected = false;
                try
                {
                    DbTextFieldPolicy.RequireExactExportable(true);
                }
                catch (CadCoreException exception)
                {
                    rejected = exception.Code == CadCoreErrorCode.InvalidTarget;
                }

                Assert(
                    rejected,
                    "Field-backed DBTEXT was admitted using evaluated text: " +
                    expression);
            }

            // Ordinary DBTEXT remains available to the fixed exact profile.
            DbTextFieldPolicy.RequireExactExportable(false);
        }

        private static void CheckBaseLeftDbTextProfile()
        {
            DbTextAlignmentPolicy.RequireBaseLeftModes(
                Autodesk.AutoCAD.DatabaseServices.TextHorizontalMode.TextLeft,
                Autodesk.AutoCAD.DatabaseServices.TextVerticalMode.TextBase);
            foreach (Autodesk.AutoCAD.DatabaseServices.TextHorizontalMode horizontal in
                new[]
                {
                    Autodesk.AutoCAD.DatabaseServices.TextHorizontalMode.TextCenter,
                    Autodesk.AutoCAD.DatabaseServices.TextHorizontalMode.TextRight,
                    Autodesk.AutoCAD.DatabaseServices.TextHorizontalMode.TextAlign,
                    Autodesk.AutoCAD.DatabaseServices.TextHorizontalMode.TextMid,
                    Autodesk.AutoCAD.DatabaseServices.TextHorizontalMode.TextFit,
                })
            {
                AssertInvalidTarget(
                    () => DbTextAlignmentPolicy.RequireBaseLeftModes(
                        horizontal,
                        Autodesk.AutoCAD.DatabaseServices.TextVerticalMode.TextBase),
                    "Non-BaseLeft horizontal DBTEXT was admitted: " + horizontal);
            }

            foreach (Autodesk.AutoCAD.DatabaseServices.TextVerticalMode vertical in
                new[]
                {
                    Autodesk.AutoCAD.DatabaseServices.TextVerticalMode.TextBottom,
                    Autodesk.AutoCAD.DatabaseServices.TextVerticalMode.TextVerticalMid,
                    Autodesk.AutoCAD.DatabaseServices.TextVerticalMode.TextTop,
                })
            {
                AssertInvalidTarget(
                    () => DbTextAlignmentPolicy.RequireBaseLeftModes(
                        Autodesk.AutoCAD.DatabaseServices.TextHorizontalMode.TextLeft,
                        vertical),
                    "Non-BaseLeft vertical DBTEXT was admitted: " + vertical);
            }

            string root = Directory.GetCurrentDirectory();
            string source = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "AutodeskCadDatabase.cs"));
            string replace = Between(source, "public void ReplaceExact(", "public void EraseExact(");
            string append = Between(source, "public CadEntitySnapshot AppendExact(", "public void PrepareCommit(");
            string export = Between(source, "private static CadEntitySnapshot ExportDbText(", "private static CadEntitySnapshot ExportLine(");
            Assert(
                source.IndexOf("AlignmentPoint", StringComparison.Ordinal) < 0,
                "The adapter must not access DBText.AlignmentPoint in the BaseLeft-only path.");
            Assert(
                replace.IndexOf("DbTextAlignmentPolicy.RequireBaseLeft(text)", StringComparison.Ordinal) >= 0 &&
                replace.IndexOf("text.Position = position;", StringComparison.Ordinal) >= 0,
                "BaseLeft translation does not validate modes and move Position only.");
            Assert(
                append.IndexOf("Position = AutodeskSnapshotExporter.ToPoint(markerOperation.Position)", StringComparison.Ordinal) >= 0 &&
                append.IndexOf("Justify = AttachmentPoint.BaseLeft", StringComparison.Ordinal) >= 0 &&
                append.IndexOf("HorizontalMode = TextHorizontalMode.TextLeft", StringComparison.Ordinal) >= 0 &&
                append.IndexOf("VerticalMode = TextVerticalMode.TextBase", StringComparison.Ordinal) >= 0,
                "Generated BaseLeft marker does not use the fixed Position-only profile.");
            Assert(
                export.IndexOf("DbTextAlignmentPolicy.RequireBaseLeft(text)", StringComparison.Ordinal) >= 0 &&
                export.IndexOf("Binary64Vector position = ToVector(text.Position)", StringComparison.Ordinal) >= 0,
                "BaseLeft export did not retain Position-only logical bounds.");
        }

        private static void CheckSavedDocumentReadGate()
        {
            AutodeskDocumentReadState clean = ReadState(
                true,
                true,
                0,
                "fixture/clean.dwg",
                "fixture/clean.dwg",
                "database-a",
                "version-a",
                GeneratedBinding("clean"));
            clean.RequireAdmissible();
            clean.RequireUnchanged(ReadState(
                true,
                true,
                0,
                "fixture/clean.dwg",
                "fixture/clean.dwg",
                "database-a",
                "version-a",
                GeneratedBinding("clean")));

            foreach (AutodeskDocumentReadState rejected in new[]
            {
                ReadState(false, true, 0, "fixture/a.dwg", "fixture/a.dwg", "db", "v", GeneratedBinding("untitled")),
                ReadState(true, false, 0, "fixture/a.dwg", "fixture/a.dwg", "db", "v", GeneratedBinding("missing")),
                ReadState(true, true, 1, "fixture/a.dwg", "fixture/a.dwg", "db", "v", GeneratedBinding("dbmod")),
                ReadState(true, true, 0, "fixture/a.dwg", "fixture/b.dwg", "db", "v", GeneratedBinding("path-drift")),
            })
            {
                AssertDocumentChanged(
                    rejected.RequireAdmissible,
                    "Dirty or unbound interactive document was admitted.");
            }

            AssertDocumentChanged(
                () => clean.RequireUnchanged(ReadState(
                    true,
                    true,
                    0,
                    "fixture/saved-as.dwg",
                    "fixture/saved-as.dwg",
                    "database-a",
                    "version-a",
                    GeneratedBinding("clean"))),
                "SaveAs/path drift was not rejected.");
            AssertDocumentChanged(
                () => clean.RequireUnchanged(ReadState(
                    true,
                    true,
                    0,
                    "fixture/clean.dwg",
                    "fixture/clean.dwg",
                    "database-a",
                    "version-b",
                    GeneratedBinding("clean"))),
                "A saved revision change during export was not rejected.");
            AssertDocumentChanged(
                () => clean.RequireUnchanged(ReadState(
                    true,
                    true,
                    0,
                    "fixture/clean.dwg",
                    "fixture/clean.dwg",
                    "database-b",
                    "version-a",
                    GeneratedBinding("clean"))),
                "A database replacement during export was not rejected.");

            string root = Directory.GetCurrentDirectory();
            string bridge = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "NativePipeBridge.cs"));
            Assert(
                bridge.IndexOf("AutodeskDocumentReadGate.Capture(document);", StringComparison.Ordinal) >= 0 &&
                bridge.IndexOf("before.RequireUnchanged(AutodeskDocumentReadGate.Capture(document))", StringComparison.Ordinal) >= 0 &&
                bridge.IndexOf("before.DatabaseVersion", StringComparison.Ordinal) >= 0 &&
                bridge.IndexOf("DocumentBecameCurrent += OnDocumentChanged", StringComparison.Ordinal) >= 0 &&
                bridge.IndexOf("DocumentToBeDestroyed += OnDocumentChanged", StringComparison.Ordinal) >= 0 &&
                bridge.IndexOf("private void OnDocumentChanged", StringComparison.Ordinal) >= 0,
                "Bridge does not invalidate on clean-state, SaveAs, switch, or close drift.");
            string database = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "AutodeskCadDatabase.cs"));
            int exporterStart = database.IndexOf(
                "internal static class AutodeskSnapshotExporter",
                StringComparison.Ordinal);
            Assert(
                exporterStart >= 0 &&
                database.Substring(exporterStart).IndexOf(
                    "MarkerPolicyBindingV2",
                    StringComparison.Ordinal) < 0 &&
                bridge.IndexOf(
                    "RequireBinding(),\n                            null,",
                    StringComparison.Ordinal) < 0,
                "Bridge and Core Console snapshot exporters must not receive asymmetric marker policy inputs.");
        }

        private static AutodeskDocumentReadState ReadState(
            bool titled,
            bool fileExists,
            long dbmod,
            string documentPath,
            string databasePath,
            string databaseFingerprint,
            string databaseVersion,
            NativeSourceBindingV2 binding)
        {
            return new AutodeskDocumentReadState(
                titled,
                fileExists,
                dbmod,
                documentPath,
                databasePath,
                databaseFingerprint,
                databaseVersion,
                binding);
        }

        private static void CheckDocumentedDocumentGateApiAllowlist()
        {
            string root = Directory.GetCurrentDirectory();
            string gate = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "AutodeskDocumentReadGate.cs"));
            foreach (Match match in Regex.Matches(
                gate,
                @"\bdocument\.(?<member>[A-Za-z_][A-Za-z0-9_]*)"))
            {
                string member = match.Groups["member"].Value;
                Assert(
                    string.Equals(member, "Database", StringComparison.Ordinal) ||
                    string.Equals(member, "Name", StringComparison.Ordinal),
                    "Document gate uses a non-allowlisted Autodesk Document member: " +
                    member);
            }

            foreach (Match match in Regex.Matches(
                gate,
                @"\bdatabase\.(?<member>[A-Za-z_][A-Za-z0-9_]*)"))
            {
                string member = match.Groups["member"].Value;
                Assert(
                    string.Equals(member, "Filename", StringComparison.Ordinal) ||
                    string.Equals(member, "FingerprintGuid", StringComparison.Ordinal) ||
                    string.Equals(member, "VersionGuid", StringComparison.Ordinal),
                    "Document gate uses a non-allowlisted Autodesk Database member: " +
                    member);
            }

            Assert(
                gate.IndexOf(".Saved", StringComparison.Ordinal) < 0 &&
                gate.IndexOf(".TopTransaction", StringComparison.Ordinal) < 0 &&
                gate.IndexOf(".NumberOfActiveTransactions", StringComparison.Ordinal) < 0,
                "Document gate references an undeclared stub-only clean-state API.");
        }

        private static void CheckConsoleExportCanonicalProfile()
        {
            Dictionary<string, object?> payload =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    {
                        "geometry_json",
                        new string('a', CanonicalJson.MaxStringUtf8Bytes + 1)
                    },
                };
            byte[] actual =
                NativeConsoleArtifactWriter.CanonicalizeConsoleExportPayload(
                    payload);
            byte[] expected = CanonicalJson.SerializeUtf8(
                payload,
                NativeCadCanonicalJsonProfiles.ConsoleExport);
            Assert(
                ByteArraysEqual(actual, expected),
                "Adapter console export does not use the core ConsoleExport profile.");
            Assert(
                string.Equals(
                    CanonicalJson.Sha256Hex(actual),
                    CanonicalJson.Sha256Hex(
                        payload,
                        NativeCadCanonicalJsonProfiles.ConsoleExport),
                    StringComparison.Ordinal),
                "Adapter console export integrity/canonical bytes disagree.");

            AssertThrows<CanonicalJsonException>(
                delegate
                {
                    NativeConsoleArtifactWriter.CanonicalizeConsoleResultPayload(
                        new Dictionary<string, object?>(
                            StringComparer.Ordinal)
                        {
                            {
                                "ordinary",
                                new string(
                                    'a',
                                    CanonicalJson.MaxStringUtf8Bytes + 1)
                            },
                        });
                },
                "Result artifacts must retain the strict 64 KiB string profile.");

            string boundedPadding = new string(
                'p',
                CanonicalJson.MaxStringUtf8Bytes);
            List<object?> padding = new List<object?>();
            for (int index = 0; index < 257; index++)
            {
                padding.Add(boundedPadding);
            }
            AssertThrows<AdapterFailureException>(
                delegate
                {
                    NativeConsoleArtifactWriter.CanonicalizeConsoleExportPayload(
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            {
                                "geometry_json",
                                new string(
                                    'g',
                                    NativeCadProtocolV2.MaxGeometryJsonBytes)
                            },
                            { "padding", padding },
                        });
                },
                "Adapter console export did not enforce its outer file cap.");
        }

        private static void AssertInvalidTarget(Action action, string message)
        {
            try
            {
                action();
            }
            catch (CadCoreException exception)
            {
                if (exception.Code == CadCoreErrorCode.InvalidTarget)
                {
                    return;
                }
            }

            throw new InvalidOperationException(message);
        }

        private static void AssertDocumentChanged(Action action, string message)
        {
            try
            {
                action();
            }
            catch (BridgeDocumentChangedException)
            {
                return;
            }

            throw new InvalidOperationException(message);
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

            throw new InvalidOperationException(message);
        }

        private static string Between(string source, string start, string end)
        {
            int startIndex = source.IndexOf(start, StringComparison.Ordinal);
            int endIndex = source.IndexOf(end, startIndex, StringComparison.Ordinal);
            Assert(startIndex >= 0 && endIndex > startIndex, "Expected adapter method boundary is absent.");
            return source.Substring(startIndex, endIndex - startIndex);
        }

        private static bool ByteArraysEqual(byte[] left, byte[] right)
        {
            if (left.Length != right.Length)
            {
                return false;
            }

            for (int index = 0; index < left.Length; index++)
            {
                if (left[index] != right[index])
                {
                    return false;
                }
            }

            return true;
        }

        private static void CheckRetainedPrivateBindingCache()
        {
            NativeSourceBindingV2 current = GeneratedBinding("initial");
            int hashCalls = 0;
            using (RetainedPrivateDwgBinding binding =
                RetainedPrivateDwgBinding.CreateForTesting(
                    "private-input.dwg",
                    _ =>
                    {
                        hashCalls++;
                        return current;
                    }))
            {
                // A 1,024-operation transaction would capture many staged
                // snapshots. They all consume the retained binding without
                // opening or hashing the DWG again.
                for (int index = 0; index < 1024; index++)
                {
                    Assert(
                        binding.CachedBinding.ExactlyMatches(current),
                        "Cached staged source binding drifted.");
                }

                Assert(hashCalls == 1, "Snapshots rehashed the full DWG.");
                binding.RequireCurrent();
                Assert(hashCalls == 2, "Boundary revalidation was omitted.");

                current = GeneratedBinding("externally-replaced");
                bool rejected = false;
                try
                {
                    binding.RequireCurrent();
                }
                catch (CadCoreException exception)
                {
                    rejected = exception.Code == CadCoreErrorCode.StalePrecondition;
                }

                Assert(rejected, "External private-file drift was not rejected.");
                Assert(
                    hashCalls == 3,
                    "Drift check did not use one explicit boundary hash.");
            }

            string root = Directory.GetCurrentDirectory();
            string source = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "AutodeskCadDatabase.cs"));
            int transactionStart = source.IndexOf(
                "internal sealed class AutodeskCadTransaction",
                StringComparison.Ordinal);
            Assert(transactionStart >= 0, "Autodesk transaction source is absent.");
            string transaction = source.Substring(transactionStart);
            Assert(
                transaction.IndexOf(
                    "privateBinding.CachedBinding",
                    StringComparison.Ordinal) >= 0 &&
                transaction.IndexOf(
                    "NativeSourceBindingCapture.Capture(",
                    StringComparison.Ordinal) < 0,
                "Transaction snapshots do not reuse the retained private binding.");
        }

        private static void CheckErasedPhysicalSequencePolicy()
        {
            string root = Directory.GetCurrentDirectory();
            string database = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "AutodeskCadDatabase.cs"));
            string count = Between(
                database,
                "internal static int CountPhysicalRecordSlots",
                "private static List<ContainerRecord> ReadContainers");
            string export = Between(
                database,
                "private static void ExportContainerEntities",
                "private static CadEntitySnapshot ExportDbText");
            Assert(
                count.IndexOf(
                    "BlockTableRecord erasedInclusiveRecord = record.IncludingErased",
                    StringComparison.Ordinal) >= 0 &&
                export.IndexOf(
                    "container.Record.IncludingErased",
                    StringComparison.Ordinal) >= 0,
                "Physical slot counting/export must enumerate documented IncludingErased records.");
            Assert(
                export.IndexOf(
                    "transaction.GetObject(id, OpenMode.ForRead, true)",
                    StringComparison.Ordinal) >= 0 &&
                export.IndexOf(
                    "if (objectValue.IsErased || id.IsErased)",
                    StringComparison.Ordinal) >= 0 &&
                export.IndexOf(
                    "int currentSequence = sequence;",
                    StringComparison.Ordinal) <
                    export.IndexOf(
                        "if (objectValue.IsErased || id.IsErased)",
                        StringComparison.Ordinal),
                "Erased physical slots must advance sequence but never emit active entities.");
            Assert(
                database.IndexOf(
                    "CountPhysicalRecordSlots(record)",
                    StringComparison.Ordinal) >= 0,
                "Marker appends must reserve their index after every physical slot.");
            int containerCap = database.IndexOf(
                "if (result.Count >= NativeCadProtocolV2.MaxGeometryContainers)",
                StringComparison.Ordinal);
            int openNextRecord = database.IndexOf(
                "transaction.GetObject(id, OpenMode.ForRead, false)",
                StringComparison.Ordinal);
            Assert(
                containerCap >= 0 &&
                openNextRecord >= 0 &&
                containerCap < openNextRecord,
                "Block-table enumeration must reject cap plus one before opening it.");
            Assert(
                database.IndexOf(
                    "new CadContainerPhysicalSlots(",
                    StringComparison.Ordinal) >= 0 &&
                database.IndexOf(
                    "PhysicalSlotCount",
                    StringComparison.Ordinal) >= 0 &&
                database.IndexOf(
                    "expectedPhysicalContainer",
                    StringComparison.Ordinal) >= 0,
                "Adapter exports and rechecks exact per-container physical slot counts.");
        }

        private static void CheckInitialWriteCapabilityBoundary()
        {
            Assert(
                Contains(AdapterIdentity.Capabilities, "translate_dbtext/v1") &&
                Contains(AdapterIdentity.Capabilities, "create_review_marker/v1") &&
                !Contains(
                    AdapterIdentity.Capabilities,
                    "delete_auxiliary_overlay_text/v1"),
                "The AutoCAD adapter advertised an unsupported delete profile.");

            string root = Directory.GetCurrentDirectory();
            string database = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "AutodeskCadDatabase.cs"));
            string reader = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "ManifestProjectionReader.cs"));
            string commands = File.ReadAllText(Path.Combine(
                root,
                "native-cad",
                "src",
                "LiangPingfa.NativeCad.AutoCAD.Adapter",
                "NativePluginCommands.cs"));
            int deleteBranch = reader.IndexOf(
                "\"delete_auxiliary_overlay_text\"",
                StringComparison.Ordinal);
            Assert(
                database.IndexOf(".Erase()", StringComparison.Ordinal) < 0,
                "The AutoCAD adapter must not call DBText.Erase().");
            Assert(
                deleteBranch >= 0 &&
                reader.IndexOf(
                    "LPF_UNSUPPORTED_OPERATION",
                    deleteBranch,
                    StringComparison.Ordinal) >= deleteBranch &&
                reader.IndexOf(
                    "new DeleteAuxiliaryOverlayTextOperationV2",
                    deleteBranch,
                    StringComparison.Ordinal) < 0,
                "Delete manifest parsing must fail with the stable unsupported-operation error.");
            Assert(
                commands.IndexOf(
                    "ManifestProjectionReader.Read(",
                    StringComparison.Ordinal) <
                commands.IndexOf(
                    "NativeCommandRuntime.CreateDatabase(",
                    StringComparison.Ordinal),
                "Manifest preflight must reject delete before database transaction setup.");
        }

        private static void CheckStableBindingCapture()
        {
            string directory = Path.Combine(
                Path.GetTempPath(),
                "lpf-binding-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "private.dwg");
            byte[] original = Encoding.ASCII.GetBytes("AC1032-stable-source");
            byte[] sameSizeReplacement =
                Encoding.ASCII.GetBytes("AC1032-mutant-source");
            try
            {
                File.WriteAllBytes(path, original);
                using (FileStream stable = new FileStream(
                    path,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete))
                {
                    NativeSourceBindingV2 first =
                        NativeSourceBindingCapture.CaptureOpenStream(path, stable);
                    NativeSourceBindingV2 second =
                        NativeSourceBindingCapture.CaptureOpenStream(path, stable);
                    Assert(
                        first.ExactlyMatches(second),
                        "Stable binding capture did not retain matching metadata/hash evidence.");
                }

                AssertBindingCaptureRejects(
                    path,
                    NativeSourceBindingCapture.BindingCaptureFaultPoint.DuringFirstHash,
                    delegate { File.WriteAllBytes(path, sameSizeReplacement); },
                    "Same-size writes during the first hash must be rejected.");
                File.WriteAllBytes(path, original);
                AssertBindingCaptureRejects(
                    path,
                    NativeSourceBindingCapture.BindingCaptureFaultPoint.BetweenHashes,
                    delegate { File.WriteAllBytes(path, sameSizeReplacement); },
                    "Same-size writes between complete hashes must be rejected.");
                File.WriteAllBytes(path, original);
                AssertBindingCaptureRejects(
                    path,
                    NativeSourceBindingCapture.BindingCaptureFaultPoint.AfterSecondHash,
                    delegate
                    {
                        File.WriteAllBytes(path, Encoding.ASCII.GetBytes(
                            "AC1032-size-and-timestamp-drift"));
                        File.SetLastWriteTimeUtc(
                            path,
                            DateTime.UtcNow.AddMinutes(1));
                    },
                    "Timestamp or size drift after hashing must be rejected.");
                File.WriteAllBytes(path, original);
                DateTime originalLastWrite = File.GetLastWriteTimeUtc(path);
                AssertBindingCaptureRejects(
                    path,
                    NativeSourceBindingCapture.BindingCaptureFaultPoint.BetweenHashes,
                    delegate
                    {
                        File.SetLastWriteTimeUtc(
                            path,
                            originalLastWrite.AddMinutes(2));
                    },
                    "Last-write drift without byte drift must be rejected.");

                // A writer already open by AutoCAD prevents the preferred
                // no-write lease. Capture must fall back to host-compatible
                // sharing and still require the two-pass proof.
                File.WriteAllBytes(path, original);
                using (FileStream hostWriter = new FileStream(
                    path,
                    FileMode.Open,
                    FileAccess.ReadWrite,
                    FileShare.ReadWrite | FileShare.Delete))
                {
                    NativeSourceBindingV2 shared =
                        NativeSourceBindingCapture.Capture(path);
                    Assert(
                        shared.ByteSize == original.Length,
                        "Host-sharing fallback did not capture a stable private file.");
                }

                string source = File.ReadAllText(Path.Combine(
                    Directory.GetCurrentDirectory(),
                    "native-cad",
                    "src",
                    "LiangPingfa.NativeCad.AutoCAD.Adapter",
                    "AutodeskCadDatabase.cs"));
                Assert(
                    source.IndexOf(
                        "privateBinding.RequireCurrent();",
                        StringComparison.Ordinal) >= 0 &&
                    source.IndexOf(
                        "finalBindingPublicationValidated",
                        StringComparison.Ordinal) >= 0,
                    "Readback and publication binding revalidation is absent.");
                string commands = File.ReadAllText(Path.Combine(
                    Directory.GetCurrentDirectory(),
                    "native-cad",
                    "src",
                    "LiangPingfa.NativeCad.AutoCAD.Adapter",
                    "NativePluginCommands.cs"));
                Assert(
                    commands.IndexOf(
                        "RequireResultPublicationBinding(",
                        StringComparison.Ordinal) >= 0 &&
                    commands.IndexOf(
                        "result.FinalExport.Snapshot.Source",
                        StringComparison.Ordinal) >= 0 &&
                    commands.IndexOf(
                        "finalBinding.RequireCurrent();",
                        StringComparison.Ordinal) >= 0,
                    "Result publication does not reject post-readback binding drift.");
            }
            finally
            {
                if (Directory.Exists(directory))
                {
                    Directory.Delete(directory, true);
                }
            }
        }

        private static void CheckFileIdentityProjection()
        {
            NativeSourceBindingCapture.ByHandleFileInformation baseline =
                new NativeSourceBindingCapture.ByHandleFileInformation
                {
                    CreationTime = new NativeSourceBindingCapture.FileTime
                    {
                        LowDateTime = 0x7de98115,
                        HighDateTime = 0x112210f4,
                    },
                    LastWriteTime = new NativeSourceBindingCapture.FileTime
                    {
                        LowDateTime = 1,
                        HighDateTime = 2,
                    },
                    VolumeSerialNumber = 0x12345678,
                    FileSizeHigh = 0,
                    FileSizeLow = 42,
                    FileIndexHigh = 0x12345678,
                    FileIndexLow = 0x9abcdef0,
                };
            Assert(
                string.Equals(
                    CanonicalJson.Sha256Hex(new Dictionary<string, object?>(
                    StringComparer.Ordinal)
                    {
                        { "creation_time_100ns", 1234567890123456789UL },
                        { "first", 305419896UL },
                        { "namespace", "windows-file-id" },
                        { "second", 1311768467463790320UL },
                    }),
                    NativeSourceBindingCapture.IdentityFingerprint(baseline),
                    StringComparison.Ordinal),
                "File identity projection must match the frozen Python vector.");

            NativeSourceBindingCapture.ByHandleFileInformation changedCreation = baseline;
            changedCreation.CreationTime.LowDateTime++;
            NativeSourceBindingCapture.ByHandleFileInformation changedVolume = baseline;
            changedVolume.VolumeSerialNumber++;
            NativeSourceBindingCapture.ByHandleFileInformation changedIndex = baseline;
            changedIndex.FileIndexLow++;
            NativeSourceBindingCapture.ByHandleFileInformation changedSizeAndWrite = baseline;
            changedSizeAndWrite.FileSizeLow++;
            changedSizeAndWrite.LastWriteTime.LowDateTime++;

            string identity = NativeSourceBindingCapture.IdentityFingerprint(baseline);
            Assert(
                !string.Equals(
                    identity,
                    NativeSourceBindingCapture.IdentityFingerprint(changedCreation),
                    StringComparison.Ordinal) &&
                !string.Equals(
                    identity,
                    NativeSourceBindingCapture.IdentityFingerprint(changedVolume),
                    StringComparison.Ordinal) &&
                !string.Equals(
                    identity,
                    NativeSourceBindingCapture.IdentityFingerprint(changedIndex),
                    StringComparison.Ordinal),
                "Creation time, volume serial, and file index must identify distinct files.");
            Assert(
                string.Equals(
                    identity,
                    NativeSourceBindingCapture.IdentityFingerprint(changedSizeAndWrite),
                    StringComparison.Ordinal),
                "Size and last-write time must not alter file identity.");
        }

        private static void AssertBindingCaptureRejects(
            string path,
            NativeSourceBindingCapture.BindingCaptureFaultPoint expectedPoint,
            Action mutate,
            string message)
        {
            bool mutated = false;
            AssertThrows<AdapterFailureException>(
                delegate
                {
                    using (FileStream stream = new FileStream(
                        path,
                        FileMode.Open,
                        FileAccess.Read,
                        FileShare.ReadWrite | FileShare.Delete))
                    {
                        NativeSourceBindingCapture.CaptureOpenStreamForTesting(
                            path,
                            stream,
                            point =>
                            {
                                if (!mutated && point == expectedPoint)
                                {
                                    mutated = true;
                                    mutate();
                                }
                            });
                    }
                },
                message);
            Assert(mutated, "Binding fault injection point was not reached.");
        }

        private static NativeSourceBindingV2 GeneratedBinding(string seed)
        {
            return new NativeSourceBindingV2(
                Digest("sha-" + seed),
                128,
                Digest("path-" + seed),
                Digest("identity-" + seed),
                "AC1032");
        }

        private static string Digest(string value)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                byte[] hash = algorithm.ComputeHash(
                    Encoding.UTF8.GetBytes(value));
                StringBuilder result = new StringBuilder(hash.Length * 2);
                foreach (byte item in hash)
                {
                    result.Append(item.ToString("x2"));
                }

                return result.ToString();
            }
        }

        private static string IndependentChallengeResponse(
            string sessionId,
            string clientNonce,
            string challenge,
            string bridgeNonce)
        {
            string[] fields =
            {
                "liang-pingfa/native-bridge/v1",
                "liang-pingfa/native-bridge/challenge-response/v1",
                sessionId,
                clientNonce,
                challenge,
                bridgeNonce,
            };
            using (MemoryStream stream = new MemoryStream())
            using (SHA256 algorithm = SHA256.Create())
            {
                foreach (string field in fields)
                {
                    byte[] encoded = Encoding.ASCII.GetBytes(field);
                    stream.WriteByte((byte)(encoded.Length >> 24));
                    stream.WriteByte((byte)(encoded.Length >> 16));
                    stream.WriteByte((byte)(encoded.Length >> 8));
                    stream.WriteByte((byte)encoded.Length);
                    stream.Write(encoded, 0, encoded.Length);
                }

                byte[] hash = algorithm.ComputeHash(stream.ToArray());
                StringBuilder result = new StringBuilder(hash.Length * 2);
                foreach (byte value in hash)
                {
                    result.Append(value.ToString("x2"));
                }

                return result.ToString();
            }
        }

        private static void Assert(bool condition, string message)
        {
            if (!condition)
            {
                throw new InvalidOperationException(message);
            }
        }

        private static bool Contains(
            IReadOnlyList<string> values,
            string expected)
        {
            foreach (string value in values)
            {
                if (string.Equals(value, expected, StringComparison.Ordinal))
                {
                    return true;
                }
            }

            return false;
        }
    }
}
