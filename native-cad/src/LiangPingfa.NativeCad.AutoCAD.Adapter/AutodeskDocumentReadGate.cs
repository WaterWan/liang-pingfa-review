// SPDX-License-Identifier: MIT
// Read-only bridge admission for a live full-host document. This deliberately
// observes state only; it never saves, prompts, or otherwise changes a user DWG.

using System;
using System.Globalization;
using System.IO;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using LiangPingfa.NativeCad.Core;

namespace LiangPingfa.NativeCad.AutoCAD.Adapter
{
    /// <summary>Narrow host seam for documented document/database clean-state APIs.</summary>
    internal interface IAutodeskDocumentReadHost
    {
        AutodeskDocumentReadState Capture(Document document);
    }

    /// <summary>
    /// Immutable evidence that the live database is the named, clean revision
    /// represented by its currently bound disk bytes.
    /// </summary>
    internal sealed class AutodeskDocumentReadState
    {
        internal AutodeskDocumentReadState(
            bool titled,
            bool fileExists,
            long dbmod,
            string documentPath,
            string databasePath,
            string databaseFingerprint,
            string databaseVersion,
            NativeSourceBindingV2 diskBinding)
        {
            Titled = titled;
            FileExists = fileExists;
            Dbmod = dbmod;
            DocumentPath = documentPath ?? string.Empty;
            DatabasePath = databasePath ?? string.Empty;
            DatabaseFingerprint = databaseFingerprint ?? string.Empty;
            DatabaseVersion = databaseVersion ?? string.Empty;
            DiskBinding = diskBinding ?? throw new ArgumentNullException(nameof(diskBinding));
        }

        internal bool Titled { get; private set; }

        internal bool FileExists { get; private set; }

        internal long Dbmod { get; private set; }

        internal string DocumentPath { get; private set; }

        internal string DatabasePath { get; private set; }

        internal string DatabaseFingerprint { get; private set; }

        internal string DatabaseVersion { get; private set; }

        internal NativeSourceBindingV2 DiskBinding { get; private set; }

        internal void RequireAdmissible()
        {
            if (!Titled ||
                !FileExists ||
                Dbmod != 0 ||
                string.IsNullOrEmpty(DocumentPath) ||
                string.IsNullOrEmpty(DatabasePath) ||
                !string.Equals(
                    DocumentPath,
                    DatabasePath,
                    StringComparison.OrdinalIgnoreCase) ||
                string.IsNullOrEmpty(DatabaseFingerprint) ||
                string.IsNullOrEmpty(DatabaseVersion))
            {
                throw new BridgeDocumentChangedException();
            }
        }

        /// <summary>
        /// Reject any save, SaveAs, document replacement, dirty transition, or
        /// byte drift observed while a read snapshot was being assembled.
        /// </summary>
        internal void RequireUnchanged(AutodeskDocumentReadState current)
        {
            RequireAdmissible();
            if (current == null)
            {
                throw new ArgumentNullException(nameof(current));
            }

            current.RequireAdmissible();
            if (!string.Equals(DocumentPath, current.DocumentPath, StringComparison.OrdinalIgnoreCase) ||
                !string.Equals(DatabasePath, current.DatabasePath, StringComparison.OrdinalIgnoreCase) ||
                !string.Equals(DatabaseFingerprint, current.DatabaseFingerprint, StringComparison.Ordinal) ||
                !string.Equals(DatabaseVersion, current.DatabaseVersion, StringComparison.Ordinal) ||
                !DiskBinding.ExactlyMatches(current.DiskBinding))
            {
                throw new BridgeDocumentChangedException();
            }
        }
    }

    /// <summary>
    /// Captures all live-document eligibility signals in one narrow host
    /// abstraction. DBMOD and DWGTITLED are documented AutoCAD variables.
    /// Database GUIDs remain session-drift evidence only; no undocumented
    /// Document clean-state member or profile-variable transaction counter is
    /// required for the read-only admission gate.
    /// </summary>
    internal static class AutodeskDocumentReadGate
    {
        private static readonly object Gate = new object();
        private static IAutodeskDocumentReadHost host = new AutodeskDocumentReadHost();

        internal static AutodeskDocumentReadState Capture(Document document)
        {
            if (document == null)
            {
                throw new BridgeDocumentChangedException();
            }

            AutodeskDocumentReadState state;
            lock (Gate)
            {
                state = host.Capture(document);
            }

            state.RequireAdmissible();
            return state;
        }

        /// <summary>SDK-free tests can supply deterministic host observations.</summary>
        internal static IDisposable UseTestHost(IAutodeskDocumentReadHost replacement)
        {
            if (replacement == null)
            {
                throw new ArgumentNullException(nameof(replacement));
            }

            lock (Gate)
            {
                IAutodeskDocumentReadHost previous = host;
                host = replacement;
                return new RestoreHost(previous);
            }
        }

        private sealed class AutodeskDocumentReadHost : IAutodeskDocumentReadHost
        {
            public AutodeskDocumentReadState Capture(Document document)
            {
                Database database = document.Database ??
                    throw new BridgeDocumentChangedException();
                string documentPath = NormalizePath(document.Name);
                string databasePath = NormalizePath(database.Filename);
                bool fileExists = !string.IsNullOrEmpty(documentPath) &&
                    File.Exists(documentPath);
                long titled = ReadIntegerSystemVariable("DWGTITLED");
                long dbmod = ReadIntegerSystemVariable("DBMOD");
                string fingerprint = RequireIndicator(database.FingerprintGuid);
                string version = RequireIndicator(database.VersionGuid);

                // NativeSourceBindingCapture hashes through an open read
                // handle and rejects a file that changes during that lease.
                NativeSourceBindingV2 binding = fileExists
                    ? NativeSourceBindingCapture.Capture(documentPath)
                    : EmptyBinding();
                return new AutodeskDocumentReadState(
                    titled == 1,
                    fileExists,
                    dbmod,
                    documentPath,
                    databasePath,
                    fingerprint,
                    version,
                    binding);
            }

            private static long ReadIntegerSystemVariable(string name)
            {
                try
                {
                    return Convert.ToInt64(
                        Application.GetSystemVariable(name),
                        CultureInfo.InvariantCulture);
                }
                catch (Exception)
                {
                    throw new BridgeDocumentChangedException();
                }
            }

            private static string NormalizePath(string value)
            {
                if (string.IsNullOrEmpty(value))
                {
                    return string.Empty;
                }

                try
                {
                    return PrivatePathPolicy.RequireNormalLocalPath(value);
                }
                catch (Exception)
                {
                    return string.Empty;
                }
            }

            private static string RequireIndicator(object value)
            {
                string indicator = Convert.ToString(
                    value,
                    CultureInfo.InvariantCulture) ?? string.Empty;
                return indicator.Length == 0 ? string.Empty : indicator;
            }

            private static NativeSourceBindingV2 EmptyBinding()
            {
                return new NativeSourceBindingV2(
                    new string('0', 64),
                    0,
                    new string('0', 64),
                    new string('0', 64),
                    "AC0000");
            }
        }

        private sealed class RestoreHost : IDisposable
        {
            private IAutodeskDocumentReadHost? previous;

            internal RestoreHost(IAutodeskDocumentReadHost previous)
            {
                this.previous = previous;
            }

            public void Dispose()
            {
                lock (Gate)
                {
                    if (previous != null)
                    {
                        host = previous;
                        previous = null;
                    }
                }
            }
        }
    }
}
