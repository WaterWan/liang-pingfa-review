// SPDX-License-Identifier: MIT
// Private-path, source-binding, and Windows ACL checks used by host commands.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using LiangPingfa.NativeCad.Core;
using LiangPingfa.NativeCad.Protocol;
using Microsoft.Win32.SafeHandles;

namespace LiangPingfa.NativeCad.AutoCAD.Adapter
{
    /// <summary>One fixed Core Console context; no CAD command accepts path input.</summary>
    internal sealed class ConsoleCommandContext
    {
        private ConsoleCommandContext(
            string manifestPath,
            string resultPath,
            string runId,
            string privateRoot,
            string runtimePackageFingerprint)
        {
            ManifestPath = manifestPath;
            ResultPath = resultPath;
            RunId = runId;
            PrivateRoot = privateRoot;
            RuntimePackageFingerprint = runtimePackageFingerprint;
        }

        internal string ManifestPath { get; private set; }

        internal string ResultPath { get; private set; }

        internal string RunId { get; private set; }

        internal string PrivateRoot { get; private set; }

        internal string RuntimePackageFingerprint { get; private set; }

        internal void RequireRuntimePackageIntegrity()
        {
            AdapterIdentity.RequireRuntimePackageFingerprint(
                RuntimePackageFingerprint);
        }

        internal static ConsoleCommandContext Require()
        {
            RejectUnexpectedNativeEnvironment(
                new HashSet<string>(StringComparer.Ordinal)
                {
                    AdapterIdentity.ManifestEnvironmentVariable,
                    AdapterIdentity.ResultEnvironmentVariable,
                    AdapterIdentity.RunIdEnvironmentVariable,
                    AdapterIdentity.PrivateRootEnvironmentVariable,
                    AdapterIdentity.RuntimePackageSha256EnvironmentVariable,
                });

            string privateRoot = PrivatePathPolicy.RequirePrivateRoot(
                RequireEnvironment(AdapterIdentity.PrivateRootEnvironmentVariable));
            string manifest = PrivatePathPolicy.RequirePrivateFile(
                RequireEnvironment(AdapterIdentity.ManifestEnvironmentVariable),
                privateRoot,
                ".json");
            string result = PrivatePathPolicy.RequirePrivateNewFile(
                RequireEnvironment(AdapterIdentity.ResultEnvironmentVariable),
                privateRoot,
                ".json");
            string runId = AdapterIdentity.RequireFixedRunId(
                RequireEnvironment(AdapterIdentity.RunIdEnvironmentVariable));
            string runtimePackageFingerprint =
                RequireEnvironment(
                    AdapterIdentity.RuntimePackageSha256EnvironmentVariable);
            AdapterIdentity.RequireRuntimePackageFingerprint(
                runtimePackageFingerprint);
            return new ConsoleCommandContext(
                manifest,
                result,
                runId,
                privateRoot,
                runtimePackageFingerprint);
        }

        internal static string RequireEnvironment(string name)
        {
            string? value = Environment.GetEnvironmentVariable(name);
            if (string.IsNullOrEmpty(value) ||
                value.IndexOf('"') >= 0 ||
                value.IndexOf('\'') >= 0 ||
                ContainsControlCharacter(value))
            {
                throw new AdapterFailureException(
                    "LPF_CONSOLE_CONTEXT",
                    "The fixed Core Console context is unavailable.");
            }

            return value;
        }

        internal static void RejectUnexpectedNativeEnvironment(
            ISet<string> allowed)
        {
            IDictionary values = Environment.GetEnvironmentVariables();
            foreach (DictionaryEntry entry in values)
            {
                string? key = entry.Key as string;
                if (key != null &&
                    key.StartsWith("LIANG_PINGFA_NATIVE_", StringComparison.Ordinal) &&
                    !allowed.Contains(key))
                {
                    throw new AdapterFailureException(
                        "LPF_CONSOLE_CONTEXT",
                        "The Core Console context contains an unapproved native variable.");
                }
            }
        }

        internal static bool ContainsControlCharacter(string value)
        {
            for (int index = 0; index < value.Length; index++)
            {
                if (value[index] < 0x20 || value[index] == 0x7f)
                {
                    return true;
                }
            }

            return false;
        }
    }

    /// <summary>Fixed full-host bootstrap context supplied by a private launcher.</summary>
    internal sealed class BootstrapCommandContext
    {
        private BootstrapCommandContext(
            string nonce,
            string configSha256,
            string outputPath,
            string privateRoot,
            DateTime issuedUtc,
            DateTime expiresUtc,
            string runtimePackageFingerprint)
        {
            Nonce = nonce;
            ConfigSha256 = configSha256;
            OutputPath = outputPath;
            PrivateRoot = privateRoot;
            IssuedUtc = issuedUtc;
            ExpiresUtc = expiresUtc;
            RuntimePackageFingerprint = runtimePackageFingerprint;
        }

        internal string Nonce { get; private set; }

        internal string ConfigSha256 { get; private set; }

        internal string OutputPath { get; private set; }

        internal string PrivateRoot { get; private set; }

        internal DateTime IssuedUtc { get; private set; }

        internal DateTime ExpiresUtc { get; private set; }

        internal string RuntimePackageFingerprint { get; private set; }

        internal static BootstrapCommandContext Require()
        {
            ConsoleCommandContext.RejectUnexpectedNativeEnvironment(
                new HashSet<string>(StringComparer.Ordinal)
                {
                    AdapterIdentity.BootstrapNonceEnvironmentVariable,
                    AdapterIdentity.BootstrapOutputEnvironmentVariable,
                    AdapterIdentity.PrivateRootEnvironmentVariable,
                    AdapterIdentity.BootstrapExpiryEnvironmentVariable,
                    AdapterIdentity.BootstrapConfigSha256EnvironmentVariable,
                    AdapterIdentity.BootstrapRuntimePackageSha256EnvironmentVariable,
                });

            string root = PrivatePathPolicy.RequirePrivateRoot(
                ConsoleCommandContext.RequireEnvironment(
                    AdapterIdentity.PrivateRootEnvironmentVariable));
            string output = PrivatePathPolicy.RequirePrivateNewFile(
                ConsoleCommandContext.RequireEnvironment(
                    AdapterIdentity.BootstrapOutputEnvironmentVariable),
                root,
                ".json");
            string nonce = ConsoleCommandContext.RequireEnvironment(
                AdapterIdentity.BootstrapNonceEnvironmentVariable);
            if (nonce.Length < 43 || nonce.Length > 128)
            {
                throw new AdapterFailureException(
                    "LPF_BOOTSTRAP_CONTEXT",
                    "The bootstrap nonce is invalid.");
            }

            for (int index = 0; index < nonce.Length; index++)
            {
                char character = nonce[index];
                if (!((character >= 'A' && character <= 'Z') ||
                    (character >= 'a' && character <= 'z') ||
                    (character >= '0' && character <= '9') ||
                    character == '_' ||
                    character == '-'))
                {
                    throw new AdapterFailureException(
                        "LPF_BOOTSTRAP_CONTEXT",
                        "The bootstrap nonce is invalid.");
                }
            }

            string expiry = ConsoleCommandContext.RequireEnvironment(
                AdapterIdentity.BootstrapExpiryEnvironmentVariable);
            string configSha256 = ConsoleCommandContext.RequireEnvironment(
                AdapterIdentity.BootstrapConfigSha256EnvironmentVariable);
            string runtimePackageFingerprint =
                ConsoleCommandContext.RequireEnvironment(
                    AdapterIdentity.BootstrapRuntimePackageSha256EnvironmentVariable);
            if (configSha256.Length != 64)
            {
                throw new AdapterFailureException(
                    "LPF_BOOTSTRAP_CONTEXT",
                    "The bootstrap config fingerprint is invalid.");
            }
            for (int index = 0; index < configSha256.Length; index++)
            {
                char character = configSha256[index];
                if (!((character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f')))
                {
                    throw new AdapterFailureException(
                        "LPF_BOOTSTRAP_CONTEXT",
                        "The bootstrap config fingerprint is invalid.");
                }
            }
            AdapterIdentity.RequireRuntimePackageFingerprint(
                runtimePackageFingerprint);

            DateTime issuedUtc = DateTime.UtcNow;
            DateTime expiresUtc;
            if (!DateTime.TryParseExact(
                    expiry,
                    "yyyy-MM-dd'T'HH:mm:ss'Z'",
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out expiresUtc) ||
                expiresUtc <= issuedUtc ||
                expiresUtc > issuedUtc.AddMinutes(5))
            {
                throw new AdapterFailureException(
                    "LPF_BOOTSTRAP_CONTEXT",
                    "The bootstrap expiry is invalid.");
            }

            return new BootstrapCommandContext(
                nonce,
                configSha256,
                output,
                root,
                issuedUtc,
                expiresUtc,
                runtimePackageFingerprint);
        }
    }

    /// <summary>Strict local-NTFS lexical containment checks for private command files.</summary>
    internal static class PrivatePathPolicy
    {
        /// <summary>
        /// A tiny testable projection of the drive properties which determine
        /// whether a private artifact can live on a volume.  Drive letters
        /// alone are not a locality guarantee: mapped remote shares have one.
        /// </summary>
        internal sealed class PrivateVolumeInfo
        {
            internal PrivateVolumeInfo(DriveType driveType, string driveFormat)
            {
                DriveType = driveType;
                DriveFormat = driveFormat ?? string.Empty;
            }

            internal DriveType DriveType { get; private set; }

            internal string DriveFormat { get; private set; }
        }

        private static Func<string, PrivateVolumeInfo> volumeInfoReader =
            ReadVolumeInfo;

        /// <summary>Overrides volume discovery only for deterministic SDK-free tests.</summary>
        internal static IDisposable UseTestVolumeInfoReader(
            Func<string, PrivateVolumeInfo> replacement)
        {
            if (replacement == null)
            {
                throw new ArgumentNullException(nameof(replacement));
            }

            Func<string, PrivateVolumeInfo> previous = volumeInfoReader;
            volumeInfoReader = replacement;
            return new RestoreVolumeInfoReader(previous);
        }

        internal static string RequirePrivateRoot(string raw)
        {
            string root = RequireNormalLocalPath(raw);
            if (!Directory.Exists(root))
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "The private workspace root is unavailable.");
            }

            RequireNoReparseComponents(root, true);
            RequireNtfs(root);
            WindowsPrivateAcl.RequireCurrentUserAndSystemOnly(root);
            return TrimTrailingDirectorySeparator(root);
        }

        internal static string RequirePrivateFile(
            string raw,
            string root,
            string extension)
        {
            string path = RequirePrivatePath(raw, root, extension);
            if (!File.Exists(path))
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "A required private file is unavailable.");
            }

            FileAttributes attributes = File.GetAttributes(path);
            if ((attributes & FileAttributes.Directory) != 0 ||
                (attributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "A private file has an unsupported type.");
            }

            WindowsPrivateAcl.RequireCurrentUserAndSystemOnly(path);
            return path;
        }

        internal static string RequirePrivateNewFile(
            string raw,
            string root,
            string extension)
        {
            string path = RequirePrivatePath(raw, root, extension);
            if (File.Exists(path) || Directory.Exists(path))
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "The private output file already exists.");
            }

            return path;
        }

        internal static string RequirePrivatePath(
            string raw,
            string root,
            string extension)
        {
            string path = RequireNormalLocalPath(raw);
            string normalizedRoot = TrimTrailingDirectorySeparator(
                RequireNormalLocalPath(root));
            if (!path.StartsWith(
                    normalizedRoot + Path.DirectorySeparatorChar,
                    StringComparison.OrdinalIgnoreCase) ||
                string.Equals(path, normalizedRoot, StringComparison.OrdinalIgnoreCase) ||
                !path.EndsWith(extension, StringComparison.OrdinalIgnoreCase))
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "A command path escaped the private workspace.");
            }

            RequireNoReparseComponents(path, false);
            RequireNtfs(path);
            return path;
        }

        internal static string RequireNormalLocalPath(string raw)
        {
            if (string.IsNullOrEmpty(raw) ||
                raw.IndexOf('"') >= 0 ||
                raw.IndexOf('\'') >= 0 ||
                ConsoleCommandContext.ContainsControlCharacter(raw) ||
                raw.StartsWith(@"\\", StringComparison.Ordinal) ||
                raw.StartsWith(@"\\?\", StringComparison.Ordinal) ||
                raw.StartsWith(@"\\.\", StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "A private path is not a normal local path.");
            }

            if (raw.IndexOf("..", StringComparison.Ordinal) >= 0 ||
                raw.IndexOf("./", StringComparison.Ordinal) >= 0 ||
                raw.IndexOf(@".\", StringComparison.Ordinal) >= 0)
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "A private path contains lexical traversal.");
            }

            string full;
            try
            {
                full = Path.GetFullPath(raw);
            }
            catch (Exception exception)
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "A private path cannot be normalized.") { Source = exception.Source };
            }

            string root = Path.GetPathRoot(full) ?? string.Empty;
            if (root.Length != 3 || root[1] != ':' || root[2] != Path.DirectorySeparatorChar ||
                !char.IsLetter(root[0]))
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "A private path is not on a local drive.");
            }

            // Recheck the actual volume every time a path crosses an adapter
            // boundary. A mapped remote NTFS share has a normal-looking drive
            // letter and filesystem name, but is not private local storage.
            RequireNtfs(full);
            return full;
        }

        internal static void RequireNoReparseComponents(string fullPath, bool finalIsDirectory)
        {
            string root = Path.GetPathRoot(fullPath) ?? string.Empty;
            string current = root;
            string[] components = fullPath.Substring(root.Length).Split(
                new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                StringSplitOptions.RemoveEmptyEntries);
            int limit = finalIsDirectory ? components.Length : components.Length - 1;
            for (int index = 0; index < limit; index++)
            {
                current = Path.Combine(current, components[index]);
                if (!Directory.Exists(current))
                {
                    throw new AdapterFailureException(
                        "LPF_PRIVATE_PATH",
                        "A private path component is unavailable.");
                }

                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new AdapterFailureException(
                        "LPF_PRIVATE_PATH",
                        "A private path contains a reparse point.");
                }
            }
        }

        internal static void RequireNtfs(string fullPath)
        {
            string root = Path.GetPathRoot(fullPath) ?? string.Empty;
            try
            {
                PrivateVolumeInfo drive = volumeInfoReader(root);
                if (drive == null ||
                    drive.DriveType != DriveType.Fixed ||
                    !string.Equals(
                        drive.DriveFormat,
                        "NTFS",
                        StringComparison.OrdinalIgnoreCase))
                {
                    throw new AdapterFailureException(
                        "LPF_PRIVATE_PATH",
                        "Private command files require a local NTFS volume.");
                }
            }
            catch (AdapterFailureException)
            {
                throw;
            }
            catch (Exception exception)
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_PATH",
                    "The private volume cannot be verified.") { Source = exception.Source };
            }
        }

        private static PrivateVolumeInfo ReadVolumeInfo(string root)
        {
            DriveInfo drive = new DriveInfo(root);
            return new PrivateVolumeInfo(drive.DriveType, drive.DriveFormat);
        }

        private sealed class RestoreVolumeInfoReader : IDisposable
        {
            private Func<string, PrivateVolumeInfo>? previous;

            internal RestoreVolumeInfoReader(Func<string, PrivateVolumeInfo> previous)
            {
                this.previous = previous;
            }

            public void Dispose()
            {
                if (previous != null)
                {
                    volumeInfoReader = previous;
                    previous = null;
                }
            }
        }

        internal static string TrimTrailingDirectorySeparator(string path)
        {
            string root = Path.GetPathRoot(path) ?? string.Empty;
            while (path.Length > root.Length &&
                (path[path.Length - 1] == Path.DirectorySeparatorChar ||
                 path[path.Length - 1] == Path.AltDirectorySeparatorChar))
            {
                path = path.Substring(0, path.Length - 1);
            }

            return path;
        }
    }

    /// <summary>Captures source bytes and stable Windows file identity through an open handle.</summary>
    internal static class NativeSourceBindingCapture
    {
        internal static NativeSourceBindingV2 Capture(string privatePath)
        {
            string path = PrivatePathPolicy.RequireNormalLocalPath(privatePath);
            using (FileStream stream = OpenReadLease(path))
            {
                return CaptureOpenStream(path, stream);
            }
        }

        /// <summary>
        /// Captures exact file bytes through an already-retained file lease.
        /// The caller owns stream lifetime; this method never opens a second
        /// pathname while establishing the initial transaction binding.
        /// </summary>
        internal static NativeSourceBindingV2 CaptureOpenStream(
            string privatePath,
            FileStream stream)
        {
            return CaptureOpenStreamCore(privatePath, stream, null);
        }

        /// <summary>
        /// SDK-free fault seam for proving that a writable shared lease does
        /// not accept same-size replacement or timestamp drift. Production
        /// calls never supply a callback.
        /// </summary>
        internal static NativeSourceBindingV2 CaptureOpenStreamForTesting(
            string privatePath,
            FileStream stream,
            Action<BindingCaptureFaultPoint> fault)
        {
            if (fault == null)
            {
                throw new ArgumentNullException(nameof(fault));
            }

            return CaptureOpenStreamCore(privatePath, stream, fault);
        }

        private static NativeSourceBindingV2 CaptureOpenStreamCore(
            string privatePath,
            FileStream stream,
            Action<BindingCaptureFaultPoint>? fault)
        {
            if (stream == null)
            {
                throw new ArgumentNullException(nameof(stream));
            }

            string path = PrivatePathPolicy.RequireNormalLocalPath(privatePath);
            ByHandleFileInformation before = GetFileInformation(stream.SafeFileHandle);
            if ((before.FileAttributes & FileAttributeReparsePoint) != 0)
            {
                throw new AdapterFailureException(
                    "LPF_SOURCE_BINDING",
                    "A source file is a reparse point.");
            }

            // AutoCAD can require FILE_SHARE_WRITE for an open document. A
            // no-write lease is preferred when the host permits it; otherwise
            // the two complete hashes plus metadata samples below are the
            // trusted-local-session proof that the shared file was stable.
            HashPass first = HashCompleteFile(stream, 1, fault);
            ByHandleFileInformation afterFirst = GetFileInformation(stream.SafeFileHandle);
            InvokeFault(fault, BindingCaptureFaultPoint.BetweenHashes);
            HashPass second = HashCompleteFile(stream, 2, fault);
            ByHandleFileInformation afterSecond = GetFileInformation(stream.SafeFileHandle);
            InvokeFault(fault, BindingCaptureFaultPoint.AfterSecondHash);
            ByHandleFileInformation final = GetFileInformation(stream.SafeFileHandle);
            if (!SameStableMetadata(before, afterFirst) ||
                !SameStableMetadata(before, afterSecond) ||
                !SameStableMetadata(before, final) ||
                !string.Equals(first.Sha256, second.Sha256, StringComparison.Ordinal) ||
                first.ByteSize < 6)
            {
                throw new AdapterFailureException(
                    "LPF_SOURCE_BINDING",
                    "The source file changed while it was bound.");
            }

            return new NativeSourceBindingV2(
                first.Sha256,
                first.ByteSize,
                HashPath(path),
                IdentityFingerprint(final),
                first.HeaderToken);
        }

        internal static string HashPath(string path)
        {
            string normalized = path.Normalize(NormalizationForm.FormC);
            return Hex(Sha256Bytes(new UTF8Encoding(false, true).GetBytes(normalized)));
        }

        internal static string IdentityFingerprint(ByHandleFileInformation information)
        {
            // This frozen projection is the public cross-language file
            // identity carrier. Size and last-write time intentionally stay
            // out of it: SameStableMetadata independently uses both to prove
            // that the two hash passes observed stable content.
            Dictionary<string, object?> identity =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "creation_time_100ns", FileTimeValue(information.CreationTime) },
                    { "first", (ulong)information.VolumeSerialNumber },
                    { "namespace", "windows-file-id" },
                    { "second", FileIndex(information) },
                };
            return CanonicalJson.Sha256Hex(identity);
        }

        internal static string Hex(byte[] bytes)
        {
            StringBuilder result = new StringBuilder(bytes.Length * 2);
            for (int index = 0; index < bytes.Length; index++)
            {
                result.Append(bytes[index].ToString("x2", CultureInfo.InvariantCulture));
            }

            return result.ToString();
        }

        internal static byte[] Sha256Bytes(byte[] bytes)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                return algorithm.ComputeHash(bytes);
            }
        }

        private static FileStream OpenReadLease(string path)
        {
            try
            {
                // Prefer denying concurrent writers while still allowing the
                // host's existing read/delete handles.
                return OpenReadLease(path, FileShare.Read | FileShare.Delete);
            }
            catch (IOException)
            {
                // A live AutoCAD document can require writer sharing. The
                // capture protocol remains fail-closed through two full
                // hashes and metadata equality, rather than assuming an
                // exclusive lease is available.
                return OpenReadLease(
                    path,
                    FileShare.ReadWrite | FileShare.Delete);
            }
        }

        private static FileStream OpenReadLease(string path, FileShare share)
        {
            return new FileStream(
                path,
                FileMode.Open,
                FileAccess.Read,
                share,
                64 * 1024,
                FileOptions.SequentialScan);
        }

        private static HashPass HashCompleteFile(
            FileStream stream,
            int pass,
            Action<BindingCaptureFaultPoint>? fault)
        {
            stream.Position = 0;
            long byteSize = 0;
            byte[] header = new byte[6];
            int headerRead = 0;
            using (SHA256 algorithm = SHA256.Create())
            {
                byte[] buffer = new byte[64 * 1024];
                int read;
                bool firstChunk = true;
                while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    if (headerRead < header.Length)
                    {
                        int copied = Math.Min(header.Length - headerRead, read);
                        Buffer.BlockCopy(buffer, 0, header, headerRead, copied);
                        headerRead += copied;
                    }

                    algorithm.TransformBlock(buffer, 0, read, null, 0);
                    byteSize += read;
                    if (firstChunk)
                    {
                        InvokeFault(
                            fault,
                            pass == 1
                                ? BindingCaptureFaultPoint.DuringFirstHash
                                : BindingCaptureFaultPoint.DuringSecondHash);
                        firstChunk = false;
                    }
                }

                algorithm.TransformFinalBlock(new byte[0], 0, 0);
                string sha256 = Hex(algorithm.Hash ?? throw new AdapterFailureException(
                    "LPF_SOURCE_BINDING",
                    "A source hash is unavailable."));
                if (headerRead != header.Length)
                {
                    throw new AdapterFailureException(
                        "LPF_SOURCE_BINDING",
                        "A source file does not have a DWG header.");
                }

                string headerToken;
                try
                {
                    headerToken = Encoding.ASCII.GetString(header);
                }
                catch (Exception exception)
                {
                    throw new AdapterFailureException(
                        "LPF_SOURCE_BINDING",
                        "A source header is invalid.") { Source = exception.Source };
                }

                if (headerToken.Length != 6 ||
                    headerToken[0] != 'A' ||
                    headerToken[1] != 'C')
                {
                    throw new AdapterFailureException(
                        "LPF_SOURCE_BINDING",
                        "A source file is not a supported DWG.");
                }

                return new HashPass(sha256, byteSize, headerToken);
            }
        }

        private static void InvokeFault(
            Action<BindingCaptureFaultPoint>? fault,
            BindingCaptureFaultPoint point)
        {
            if (fault != null)
            {
                fault(point);
            }
        }

        private sealed class HashPass
        {
            internal HashPass(string sha256, long byteSize, string headerToken)
            {
                Sha256 = sha256;
                ByteSize = byteSize;
                HeaderToken = headerToken;
            }

            internal string Sha256 { get; private set; }

            internal long ByteSize { get; private set; }

            internal string HeaderToken { get; private set; }
        }

        internal enum BindingCaptureFaultPoint
        {
            DuringFirstHash,
            BetweenHashes,
            DuringSecondHash,
            AfterSecondHash,
        }

        private static int ReadAtMost(Stream stream, byte[] buffer)
        {
            int offset = 0;
            while (offset < buffer.Length)
            {
                int read = stream.Read(buffer, offset, buffer.Length - offset);
                if (read == 0)
                {
                    break;
                }

                offset += read;
            }

            return offset;
        }

        private static ulong FileTimeValue(FileTime value)
        {
            return ((ulong)value.HighDateTime << 32) | value.LowDateTime;
        }

        private static ulong FileIndex(ByHandleFileInformation value)
        {
            return ((ulong)value.FileIndexHigh << 32) | value.FileIndexLow;
        }

        private static long FileSize(ByHandleFileInformation value)
        {
            ulong size = ((ulong)value.FileSizeHigh << 32) | value.FileSizeLow;
            if (size > long.MaxValue)
            {
                throw new AdapterFailureException(
                    "LPF_SOURCE_BINDING",
                    "A source file is too large.");
            }

            return (long)size;
        }

        private static bool SameStableMetadata(
            ByHandleFileInformation left,
            ByHandleFileInformation right)
        {
            return left.VolumeSerialNumber == right.VolumeSerialNumber &&
                left.FileIndexHigh == right.FileIndexHigh &&
                left.FileIndexLow == right.FileIndexLow &&
                FileTimeValue(left.CreationTime) == FileTimeValue(right.CreationTime) &&
                FileTimeValue(left.LastWriteTime) == FileTimeValue(right.LastWriteTime) &&
                FileSize(left) == FileSize(right);
        }

        private static ByHandleFileInformation GetFileInformation(
            SafeFileHandle handle)
        {
            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(handle, out information))
            {
                throw new AdapterFailureException(
                    "LPF_SOURCE_BINDING",
                    "A source file identity is unavailable.");
            }

            return information;
        }

        [Flags]
        private enum FileAttributesNative : uint
        {
            ReparsePoint = 0x00000400,
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct FileTime
        {
            internal uint LowDateTime;
            internal uint HighDateTime;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct ByHandleFileInformation
        {
            internal uint FileAttributes;
            internal FileTime CreationTime;
            internal FileTime LastAccessTime;
            internal FileTime LastWriteTime;
            internal uint VolumeSerialNumber;
            internal uint FileSizeHigh;
            internal uint FileSizeLow;
            internal uint NumberOfLinks;
            internal uint FileIndexHigh;
            internal uint FileIndexLow;
        }

        private const uint FileAttributeReparsePoint = 0x00000400;

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle file,
            out ByHandleFileInformation information);
    }

    /// <summary>
    /// Retains one validated private DWG file lease and its initial exact
    /// source binding for an entire command/database lifetime.  Transaction
    /// snapshots consume <see cref="CachedBinding"/> in O(1); full-file
    /// hashing is reserved for explicit security boundaries.
    /// </summary>
    internal sealed class RetainedPrivateDwgBinding : IDisposable
    {
        private readonly string privatePath;
        private readonly Func<string, NativeSourceBindingV2> captureCurrent;
        private readonly FileStream? retainedFile;
        private readonly NativeSourceBindingV2 cachedBinding;
        private bool disposed;

        private RetainedPrivateDwgBinding(
            string privatePath,
            NativeSourceBindingV2 cachedBinding,
            Func<string, NativeSourceBindingV2> captureCurrent,
            FileStream? retainedFile)
        {
            this.privatePath = privatePath;
            this.cachedBinding = cachedBinding ??
                throw new ArgumentNullException(nameof(cachedBinding));
            this.captureCurrent = captureCurrent ??
                throw new ArgumentNullException(nameof(captureCurrent));
            this.retainedFile = retainedFile;
        }

        internal string PrivatePath
        {
            get { return privatePath; }
        }

        /// <summary>Initial or post-save exact binding reused by snapshots.</summary>
        internal NativeSourceBindingV2 CachedBinding
        {
            get
            {
                ThrowIfDisposed();
                return cachedBinding;
            }
        }

        /// <summary>
        /// Opens and retains the private DWG file before database or
        /// transaction access. The initial full hash is computed from that
        /// same held file handle, not a separate pathname open.
        /// </summary>
        internal static RetainedPrivateDwgBinding Open(
            string privatePath,
            string privateRoot)
        {
            string path = PrivatePathPolicy.RequirePrivateFile(
                privatePath,
                privateRoot,
                ".dwg");
            FileStream? file = null;
            try
            {
                file = new FileStream(
                    path,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete,
                    64 * 1024,
                    FileOptions.SequentialScan);
                NativeSourceBindingV2 binding =
                    NativeSourceBindingCapture.CaptureOpenStream(path, file);
                return new RetainedPrivateDwgBinding(
                    path,
                    binding,
                    NativeSourceBindingCapture.Capture,
                    file);
            }
            catch
            {
                if (file != null)
                {
                    file.Dispose();
                }

                throw;
            }
        }

        /// <summary>
        /// Test-only constructor for a counting hash provider. Production
        /// callers must use <see cref="Open"/> so an actual file lease is
        /// retained while the initial binding is captured.
        /// </summary>
        internal static RetainedPrivateDwgBinding CreateForTesting(
            string privatePath,
            Func<string, NativeSourceBindingV2> capture)
        {
            if (capture == null)
            {
                throw new ArgumentNullException(nameof(capture));
            }

            return new RetainedPrivateDwgBinding(
                privatePath,
                capture(privatePath),
                capture,
                null);
        }

        /// <summary>
        /// Rehashes only at a security boundary and rejects identity/content
        /// drift. A successful revalidation does not replace the cached
        /// binding: staged in-memory snapshots retain the same prewrite
        /// source carrier until SaveAs establishes a new final lease.
        /// </summary>
        internal NativeSourceBindingV2 RequireCurrent()
        {
            ThrowIfDisposed();
            NativeSourceBindingV2 current = captureCurrent(privatePath);
            if (!cachedBinding.ExactlyMatches(current))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "The retained private DWG binding changed.");
            }

            return current;
        }

        public void Dispose()
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            if (retainedFile != null)
            {
                retainedFile.Dispose();
            }
        }

        private void ThrowIfDisposed()
        {
            if (disposed)
            {
                throw new ObjectDisposedException(
                    nameof(RetainedPrivateDwgBinding));
            }
        }
    }

    /// <summary>
    /// Small fail-closed DACL reader for private files/roots. It accepts
    /// current-user and LocalSystem entries, plus Administrators only when
    /// TokenOwner makes it the process default file owner; it never impersonates.
    /// </summary>
    internal static class WindowsPrivateAcl
    {
        private const uint OwnerSecurityInformation = 0x00000001;
        private const uint DaclSecurityInformation = 0x00000004;
        private const uint SeFileObject = 1;
        private const int AclSizeInformation = 2;
        private const byte AccessAllowedAceType = 0;
        private const uint TokenQuery = 0x0008;
        private const int TokenOwnerInformation = 4;
        private const string LocalSystemSid = "S-1-5-18";
        private const string BuiltinAdministratorsSid = "S-1-5-32-544";

        internal static string CurrentUserSid()
        {
            using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
            {
                SecurityIdentifier? sid = identity.User;
                if (sid == null)
                {
                    throw new AdapterFailureException(
                        "LPF_PRIVATE_ACL",
                        "The current Windows identity is unavailable.");
                }

                return sid.Value;
            }
        }

        internal static void RequireCurrentUserAndSystemOnly(string path)
        {
            IntPtr owner;
            IntPtr group;
            IntPtr dacl;
            IntPtr sacl;
            IntPtr securityDescriptor;
            uint status = GetNamedSecurityInfo(
                path,
                SeFileObject,
                OwnerSecurityInformation | DaclSecurityInformation,
                out owner,
                out group,
                out dacl,
                out sacl,
                out securityDescriptor);
            if (status != 0 || securityDescriptor == IntPtr.Zero ||
                owner == IntPtr.Zero || dacl == IntPtr.Zero)
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_ACL",
                    "A private path security descriptor is unavailable.");
            }

            try
            {
                string current = CurrentUserSid();
                string ownerSid = SidString(owner);
                if (!IsTrustedPrivateOwner(
                        ownerSid,
                        current,
                        CurrentTokenDefaultOwnerSid()))
                {
                    throw new AdapterFailureException(
                        "LPF_PRIVATE_ACL",
                        "A private path owner is not trusted.");
                }

                AclSizeInformationValue information;
                if (!GetAclInformation(
                        dacl,
                        out information,
                        Marshal.SizeOf(typeof(AclSizeInformationValue)),
                        AclSizeInformation) ||
                    information.AceCount == 0)
                {
                    throw new AdapterFailureException(
                        "LPF_PRIVATE_ACL",
                        "A private path DACL is unavailable.");
                }

                for (uint index = 0; index < information.AceCount; index++)
                {
                    IntPtr ace;
                    if (!GetAce(dacl, index, out ace) || ace == IntPtr.Zero ||
                        Marshal.ReadByte(ace) != AccessAllowedAceType)
                    {
                        throw new AdapterFailureException(
                            "LPF_PRIVATE_ACL",
                            "A private path DACL has an unsupported ACE.");
                    }

                    IntPtr sid = IntPtr.Add(ace, 8);
                    string aceSid = SidString(sid);
                    if (!string.Equals(aceSid, current, StringComparison.Ordinal) &&
                        !string.Equals(aceSid, LocalSystemSid, StringComparison.Ordinal))
                    {
                        throw new AdapterFailureException(
                            "LPF_PRIVATE_ACL",
                            "A private path DACL grants an untrusted SID.");
                    }
                }
            }
            finally
            {
                LocalFree(securityDescriptor);
            }
        }

        /// <summary>
        /// Match the Python retained-handle owner policy exactly. Membership
        /// in Administrators and elevation do not make that SID trusted; only
        /// the token's actual default file owner does.
        /// </summary>
        internal static bool IsTrustedPrivateOwner(
            string ownerSid,
            string currentUserSid,
            string tokenDefaultOwnerSid)
        {
            return string.Equals(ownerSid, currentUserSid, StringComparison.Ordinal) ||
                string.Equals(ownerSid, LocalSystemSid, StringComparison.Ordinal) ||
                (string.Equals(
                    ownerSid,
                    BuiltinAdministratorsSid,
                    StringComparison.Ordinal) &&
                 string.Equals(
                    tokenDefaultOwnerSid,
                    BuiltinAdministratorsSid,
                    StringComparison.Ordinal));
        }

        internal static string CurrentTokenDefaultOwnerSid()
        {
            IntPtr token;
            if (!OpenProcessToken(GetCurrentProcess(), TokenQuery, out token) ||
                token == IntPtr.Zero)
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_ACL",
                    "The current Windows token is unavailable.");
            }

            try
            {
                uint required = 0;
                GetTokenInformation(
                    token,
                    TokenOwnerInformation,
                    IntPtr.Zero,
                    0,
                    out required);
                if (required == 0)
                {
                    throw new AdapterFailureException(
                        "LPF_PRIVATE_ACL",
                        "The current token default owner is unavailable.");
                }

                IntPtr buffer = Marshal.AllocHGlobal(unchecked((int)required));
                try
                {
                    if (!GetTokenInformation(
                            token,
                            TokenOwnerInformation,
                            buffer,
                            required,
                            out required))
                    {
                        throw new AdapterFailureException(
                            "LPF_PRIVATE_ACL",
                            "The current token default owner cannot be read.");
                    }

                    IntPtr owner = Marshal.ReadIntPtr(buffer);
                    if (owner == IntPtr.Zero)
                    {
                        throw new AdapterFailureException(
                            "LPF_PRIVATE_ACL",
                            "The current token default owner is unavailable.");
                    }

                    return SidString(owner);
                }
                finally
                {
                    Marshal.FreeHGlobal(buffer);
                }
            }
            finally
            {
                CloseHandle(token);
            }
        }

        private static string SidString(IntPtr sid)
        {
            IntPtr value;
            if (!ConvertSidToStringSid(sid, out value) || value == IntPtr.Zero)
            {
                throw new AdapterFailureException(
                    "LPF_PRIVATE_ACL",
                    "A Windows SID is unavailable.");
            }

            try
            {
                string? result = Marshal.PtrToStringUni(value);
                if (string.IsNullOrEmpty(result))
                {
                    throw new AdapterFailureException(
                        "LPF_PRIVATE_ACL",
                        "A Windows SID is unavailable.");
                }

                return result;
            }
            finally
            {
                LocalFree(value);
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct AclSizeInformationValue
        {
            internal uint AceCount;
            internal uint AclBytesInUse;
            internal uint AclBytesFree;
        }

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetNamedSecurityInfo(
            string objectName,
            uint objectType,
            uint securityInformation,
            out IntPtr owner,
            out IntPtr group,
            out IntPtr dacl,
            out IntPtr sacl,
            out IntPtr securityDescriptor);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool GetAclInformation(
            IntPtr acl,
            out AclSizeInformationValue information,
            int informationLength,
            int informationClass);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool GetAce(
            IntPtr acl,
            uint index,
            out IntPtr ace);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool ConvertSidToStringSid(
            IntPtr sid,
            out IntPtr stringSid);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool OpenProcessToken(
            IntPtr processHandle,
            uint desiredAccess,
            out IntPtr tokenHandle);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool GetTokenInformation(
            IntPtr tokenHandle,
            int tokenInformationClass,
            IntPtr tokenInformation,
            uint tokenInformationLength,
            out uint returnLength);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr LocalFree(IntPtr memory);
    }
}
