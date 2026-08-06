// SPDX-License-Identifier: MIT
// Read-only local named-pipe bridge. CAD access is dispatched onto Autodesk's
// command context; pipe workers only authenticate, frame, and marshal data.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using LiangPingfa.NativeCad.Core;
using LiangPingfa.NativeCad.Protocol;
using Microsoft.Win32.SafeHandles;

namespace LiangPingfa.NativeCad.AutoCAD.Adapter
{
    /// <summary>Single active bridge lifecycle; a new bootstrap replaces no live bridge.</summary>
    internal static class NativeBridgeHost
    {
        private static readonly object Gate = new object();
        private static NativePipeBridgeServer? server;

        internal static void Start(
            Document document,
            BootstrapCommandContext context)
        {
            if (document == null)
            {
                throw new ArgumentNullException(nameof(document));
            }

            // A read-only advertisement must never claim that an untitled or
            // dirty interactive drawing is equivalent to saved disk bytes.
            AutodeskDocumentReadGate.Capture(document);
            lock (Gate)
            {
                if (server != null)
                {
                    server.Stop();
                    server = null;
                }

                NativePipeBridgeServer candidate =
                    new NativePipeBridgeServer(document, context);
                try
                {
                    candidate.Start();
                    NativeBridgeAdvertisement.Write(
                        context.OutputPath,
                        context,
                        candidate);
                    server = candidate;
                }
                catch
                {
                    candidate.Stop();
                    throw;
                }
            }
        }

        internal static void Stop()
        {
            lock (Gate)
            {
                if (server != null)
                {
                    server.Stop();
                    server = null;
                }
            }
        }
    }

    /// <summary>One current-document, one-user, one-session pipe server.</summary>
    internal sealed class NativePipeBridgeServer
    {
        private const int MaxRequestBytes = 64 * 1024;
        private const int MaxControlResponseBytes = 256 * 1024;
        private const int MaxInventoryResponseBytes = 256 * 1024;
        private const int MaxGeometryResponseBytes = 32 * 1024 * 1024;
        private const int PipeBufferBytes = 64 * 1024;
        private readonly Document document;
        private readonly BootstrapCommandContext bootstrap;
        private readonly CancellationTokenSource cancellation =
            new CancellationTokenSource();
        private readonly BridgeExpiryLifetime expiryLifetime;
        private readonly CancellationTokenSource lifetimeCancellation;
        private readonly string bridgeNonce;
        private readonly string pipeName;
        private readonly string pluginFingerprint;
        private readonly DateTime expiresUtc;
        private readonly object stateGate = new object();
        // The Python preparation client is deliberately the only session-ID
        // generator. Bootstrap starts an unbound pipe; its first valid
        // health request atomically binds this server to that proposed ID.
        private readonly BridgeSessionOwnership sessionOwnership =
            new BridgeSessionOwnership();
        private NativeGeometryBindingContextV2? binding;
        private GeometryExportV2? initialExport;
        private NamedPipeServerStream? activePipe;
        private Task? worker;
        private bool invalidated;
        private bool stopped;
        // This is deliberately private-only: it distinguishes expected host
        // teardown from expiry without exposing host or request data.
        private BridgeTerminationReason terminationReason;

        internal NativePipeBridgeServer(
            Document document,
            BootstrapCommandContext bootstrap)
        {
            this.document = document ?? throw new ArgumentNullException(nameof(document));
            this.bootstrap = bootstrap ?? throw new ArgumentNullException(nameof(bootstrap));
            bridgeNonce = RandomBase64Url(32);
            pipeName = @"\\.\pipe\" + AdapterIdentity.BridgePipePrefix +
                RandomPipeToken();
            pluginFingerprint = AdapterIdentity.AssemblyFingerprint();
            expiresUtc = bootstrap.ExpiresUtc;
            expiryLifetime = new BridgeExpiryLifetime(expiresUtc, OnExpiry);
            lifetimeCancellation = CancellationTokenSource.CreateLinkedTokenSource(
                cancellation.Token,
                expiryLifetime.Token);
        }

        internal string PipeName
        {
            get { return pipeName; }
        }

        internal DateTime ExpiresUtc
        {
            get { return expiresUtc; }
        }

        internal int ProcessId
        {
            get { return Process.GetCurrentProcess().Id; }
        }

        internal string PluginFingerprint
        {
            get { return pluginFingerprint; }
        }

        // Kept private so host shutdown and expiry remain operational
        // diagnostics rather than protocol-visible data.
        private bool ExpiredForPrivateDiagnostics
        {
            get
            {
                lock (stateGate)
                {
                    return terminationReason == BridgeTerminationReason.Expired;
                }
            }
        }

        internal void Start()
        {
            NamedPipeServerStream? firstPipe = null;
            bool becameCurrentSubscribed = false;
            bool destroySubscribed = false;
            lock (stateGate)
            {
                if (worker != null || stopped)
                {
                    throw new AdapterFailureException(
                        "LPF_BRIDGE",
                        "The bridge lifecycle is invalid.");
                }
            }

            try
            {
                ThrowIfExpired();
                // Creating the one allowed pipe instance is the startup
                // readiness boundary.  It needs no drawing access and can
                // therefore complete before the bootstrap command returns.
                firstPipe = NativePipeFactory.Create(pipeName, true);
                lock (stateGate)
                {
                    activePipe = firstPipe;
                }

                Application.DocumentManager.DocumentBecameCurrent += OnDocumentChanged;
                becameCurrentSubscribed = true;
                Application.DocumentManager.DocumentToBeDestroyed += OnDocumentChanged;
                destroySubscribed = true;

                NamedPipeServerStream startedPipe = firstPipe ??
                    throw new AdapterFailureException(
                        "LPF_BRIDGE",
                        "The bridge pipe did not start.");
                Task started = Task.Run(
                    () => ServeAsync(startedPipe, lifetimeCancellation.Token));
                lock (stateGate)
                {
                    worker = started;
                }
            }
            catch
            {
                if (destroySubscribed)
                {
                    Application.DocumentManager.DocumentToBeDestroyed -= OnDocumentChanged;
                }
                if (becameCurrentSubscribed)
                {
                    Application.DocumentManager.DocumentBecameCurrent -= OnDocumentChanged;
                }

                if (firstPipe != null)
                {
                    firstPipe.Dispose();
                }

                lock (stateGate)
                {
                    activePipe = null;
                }
                ReleaseLifetimeResources();

                throw;
            }
        }

        internal void Stop()
        {
            NamedPipeServerStream? pipe;
            lock (stateGate)
            {
                if (stopped)
                {
                    return;
                }

                stopped = true;
                invalidated = true;
                terminationReason = BridgeTerminationReason.HostShutdown;
                cancellation.Cancel();
                pipe = activePipe;
            }

            try
            {
                if (pipe != null)
                {
                    pipe.Dispose();
                }
            }
            finally
            {
                Application.DocumentManager.DocumentBecameCurrent -= OnDocumentChanged;
                Application.DocumentManager.DocumentToBeDestroyed -= OnDocumentChanged;
                if (worker == null)
                {
                    ReleaseLifetimeResources();
                }
            }
        }

        private async Task ServeAsync(
            NamedPipeServerStream firstPipe,
            CancellationToken cancellationToken)
        {
            NamedPipeServerStream? pendingPipe = firstPipe;
            bool firstInstance = false;
            try
            {
                while (!cancellationToken.IsCancellationRequested && !IsInvalidated())
                {
                    NamedPipeServerStream pipe = pendingPipe ??
                        NativePipeFactory.Create(pipeName, firstInstance);
                    pendingPipe = null;
                    firstInstance = false;
                    using (pipe)
                    {
                        SetActivePipe(pipe);
                        try
                        {
                            await pipe.WaitForConnectionAsync(cancellationToken)
                                .ConfigureAwait(false);
                            NativePipeClientIdentity.Verify(pipe.SafePipeHandle);
                            await ServeClientAsync(pipe, cancellationToken)
                                .ConfigureAwait(false);
                        }
                        catch (OperationCanceledException)
                        {
                            return;
                        }
                        catch (IOException)
                        {
                            // A disconnect only retires this pipe instance;
                            // the same one-use session can continue until
                            // expiry if no protocol/auth failure occurred.
                        }
                        catch
                        {
                            Invalidate();
                            return;
                        }
                        finally
                        {
                            ClearActivePipe(pipe);
                        }
                    }
                }
            }
            catch (OperationCanceledException)
            {
                // Stop owns the cancellation path and has already disposed
                // the active pipe when necessary.
            }
            catch
            {
                // The detached worker never leaks an unobserved task
                // exception.  The pipe is invalidated and retired instead.
                Invalidate();
            }
            finally
            {
                if (pendingPipe != null)
                {
                    pendingPipe.Dispose();
                }

                Invalidate();
                ReleaseLifetimeResources();
            }
        }

        private async Task ServeClientAsync(
            NamedPipeServerStream pipe,
            CancellationToken cancellationToken)
        {
            while (!cancellationToken.IsCancellationRequested && !IsInvalidated())
            {
                BridgeRequest request;
                try
                {
                    request = await ReadRequestAsync(pipe, cancellationToken)
                        .ConfigureAwait(false);
                }
                catch (EndOfStreamException)
                {
                    return;
                }

                // Request parsing has now authenticated the frame envelope and
                // allowlisted its method.  Capture one deadline here, before
                // dispatch, and retain it through canonical response
                // serialization, every pipe write, and the final flush.
                // Creating a later CTS would silently grant a slow peer a
                // second timeout budget.
                using (BridgeRequestDeadline deadline =
                    CreateRequestDeadline(request.Method, cancellationToken))
                using (CancellationTokenRegistration deadlineRegistration =
                    deadline.Token.Register(() => CloseAndInvalidate(pipe)))
                {
                    Dictionary<string, object?> response;
                    try
                    {
                        try
                        {
                            response = await DispatchRequestAsync(
                                request,
                                deadline.Token).ConfigureAwait(false);
                        }
                        catch (BridgeDocumentChangedException)
                        {
                            await WriteErrorAsync(
                                pipe,
                                request,
                                "DOCUMENT_CHANGED",
                                deadline.Token).ConfigureAwait(false);
                            Invalidate();
                            return;
                        }
                        catch (BridgeSessionExpiredException)
                        {
                            if (IsExpired())
                            {
                                CloseAndInvalidate(pipe);
                                return;
                            }

                            await WriteErrorAsync(
                                pipe,
                                request,
                                "SESSION_EXPIRED",
                                deadline.Token).ConfigureAwait(false);
                            Invalidate();
                            return;
                        }
                        catch (AdapterFailureException)
                        {
                            await WriteErrorAsync(
                                pipe,
                                request,
                                "SESSION_INVALID",
                                deadline.Token).ConfigureAwait(false);
                            Invalidate();
                            return;
                        }
                        catch
                        {
                            await WriteErrorAsync(
                                pipe,
                                request,
                                "INTERNAL_ERROR",
                                deadline.Token).ConfigureAwait(false);
                            Invalidate();
                            return;
                        }

                        // WriteFrameAsync performs synchronous canonical
                        // serialization as well as asynchronous I/O.  It
                        // checks this exact token on both sides of each stage.
                        await WriteFrameAsync(
                            pipe,
                            response,
                            ResponseMaximum(request.Method),
                            deadline.Token).ConfigureAwait(false);
                    }
                    catch (OperationCanceledException)
                        when (deadline.Token.IsCancellationRequested)
                    {
                        // A deadline may expire while serializing, writing,
                        // or flushing.  The registration already closed the
                        // pipe; never spend a new timeout attempting an error.
                        CloseAndInvalidate(pipe);
                        return;
                    }
                    catch (ObjectDisposedException)
                        when (deadline.Token.IsCancellationRequested)
                    {
                        CloseAndInvalidate(pipe);
                        return;
                    }
                    catch (IOException)
                        when (deadline.Token.IsCancellationRequested)
                    {
                        CloseAndInvalidate(pipe);
                        return;
                    }
                    catch (BridgeSessionExpiredException)
                    {
                        CloseAndInvalidate(pipe);
                        return;
                    }
                }
            }
        }

        private async Task<BridgeRequest> ReadRequestAsync(
            NamedPipeServerStream pipe,
            CancellationToken outerToken)
        {
            // A malformed frame has no validated method (and may not have a
            // usable request ID), so it retains the fixed control deadline.
            using (BridgeRequestDeadline deadline =
                CreateControlDeadline(outerToken))
            {
                byte[] header = await ReadExactlyAsync(
                    pipe,
                    4,
                    deadline.Token).ConfigureAwait(false);
                int length = (header[0] << 24) |
                    (header[1] << 16) |
                    (header[2] << 8) |
                    header[3];
                if (length < 1 || length > MaxRequestBytes)
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_FRAME",
                        "A bridge request frame exceeds its fixed bound.");
                }

                byte[] payload = await ReadExactlyAsync(
                    pipe,
                    length,
                    deadline.Token).ConfigureAwait(false);
                object? raw = CanonicalJson.RequireCanonicalUtf8(
                    payload,
                    MaxRequestBytes,
                    CanonicalJsonOptions.Strict);
                return BridgeRequest.Parse(raw);
            }
        }

        private async Task<Dictionary<string, object?>> DispatchRequestAsync(
            BridgeRequest request,
            CancellationToken requestDeadline)
        {
            requestDeadline.ThrowIfCancellationRequested();
            BindRequestSession(request);
            ThrowIfExpired();
            if (string.Equals(request.Method, "health", StringComparison.Ordinal))
            {
                // Health is deliberately static protocol/identity data.  It
                // must not acquire a document lock, snapshot a database, or
                // wait on AutoCAD's command context during bootstrap.
                requestDeadline.ThrowIfCancellationRequested();
                return BridgeResponse.Success(request.Id, BuildHealth());
            }

            TaskCompletionSource<Dictionary<string, object?>> completion =
                new TaskCompletionSource<Dictionary<string, object?>>();
            try
            {
                AutodeskCommandContextDispatcher.Execute(
                    delegate(object state)
                    {
                        try
                        {
                            NativePipeBridgeServer server =
                                (NativePipeBridgeServer)state;
                            completion.TrySetResult(
                                server.HandleInCommandContext(request));
                        }
                        catch (Exception exception)
                        {
                            completion.TrySetException(exception);
                        }

                        return Task.CompletedTask;
                    },
                    this);
            }
            catch (Exception exception)
            {
                completion.TrySetException(exception);
            }
            Task winner = await Task.WhenAny(
                completion.Task,
                Task.Delay(Timeout.Infinite, requestDeadline)).ConfigureAwait(false);
            if (winner != completion.Task)
            {
                Invalidate();
                if (IsExpired())
                {
                    throw new BridgeSessionExpiredException();
                }

                throw new AdapterFailureException(
                    "LPF_PIPE_TIMEOUT",
                    "The bridge command dispatch exceeded its fixed deadline.");
            }

            Dictionary<string, object?> response =
                await completion.Task.ConfigureAwait(false);
            requestDeadline.ThrowIfCancellationRequested();
            ThrowIfExpired();
            return response;
        }

        private Dictionary<string, object?> HandleInCommandContext(
            BridgeRequest request)
        {
            if (IsInvalidated())
            {
                throw new AdapterFailureException(
                    "LPF_BRIDGE",
                    "The bridge session is invalid.");
            }

            if (IsExpired())
            {
                throw new BridgeSessionExpiredException();
            }

            if (!ReferenceEquals(
                    Application.DocumentManager.MdiActiveDocument,
                    document))
            {
                throw new BridgeDocumentChangedException();
            }

            if (string.Equals(request.Method, "get_session", StringComparison.Ordinal))
            {
                GeometryExportV2 initial = CaptureCurrentExport();
                lock (stateGate)
                {
                    if (initialExport != null)
                    {
                        invalidated = true;
                        throw new AdapterFailureException(
                            "LPF_BRIDGE",
                            "The bridge session handshake was duplicated.");
                    }

                    initialExport = initial;
                    sessionOwnership.MarkSessionDescriptorIssued();
                }

                return BridgeResponse.Success(request.Id, BuildSession(request, initial));
            }

            RequireSessionDescriptorIssued();
            GeometryExportV2 current = CaptureCurrentExport();
            GeometryExportV2 initialExportSnapshot = RequireInitialExport();
            if (!string.Equals(
                    current.Document.RevisionFingerprint,
                    initialExportSnapshot.Document.RevisionFingerprint,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    current.Document.DatabaseInstanceFingerprint,
                    initialExportSnapshot.Document.DatabaseInstanceFingerprint,
                    StringComparison.Ordinal) ||
                !current.Snapshot.Source.ExactlyMatches(
                    initialExportSnapshot.Snapshot.Source))
            {
                throw new BridgeDocumentChangedException();
            }

            if (string.Equals(
                    request.Method,
                    "get_current_document",
                    StringComparison.Ordinal))
            {
                return BridgeResponse.Success(
                    request.Id,
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "kind", "document" },
                        { "current_document", DocumentWireValue(current) },
                    });
            }

            RequireExpectedRevision(request, current);
            if (string.Equals(
                    request.Method,
                    "export_inventory",
                    StringComparison.Ordinal))
            {
                Dictionary<string, object?> inventory =
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        {
                            "schema_version",
                            "liang-pingfa/native-inventory-export/v2"
                        },
                        {
                            "document_revision_fingerprint",
                            current.Document.RevisionFingerprint
                        },
                        {
                            "inventory_digest",
                            CanonicalJson.Sha256Hex(
                                new Dictionary<string, object?>(
                                    StringComparer.Ordinal)
                                {
                                    {
                                        "complete_geometry_digest",
                                        current.Document.CompleteGeometryDigest
                                    },
                                    {
                                        "protected_state_digest",
                                        current.Document.ProtectedStateDigest
                                    },
                                })
                        },
                    };
                return BridgeResponse.Success(
                    request.Id,
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "kind", "inventory" },
                        {
                            "inventory_json",
                            CanonicalJson.Serialize(inventory)
                        },
                    });
            }

            if (string.Equals(
                    request.Method,
                    "export_exact_geometry",
                    StringComparison.Ordinal))
            {
                return BridgeResponse.Success(
                    request.Id,
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "kind", "geometry" },
                        {
                            "geometry_json",
                            new UTF8Encoding(false, true).GetString(
                                current.ToCanonicalJsonUtf8())
                        },
                    });
            }

            throw new AdapterFailureException(
                "LPF_PIPE_METHOD",
                "The bridge method is not allowlisted.");
        }

        private GeometryExportV2 CaptureCurrentExport()
        {
            using (DocumentLockScope lockScope = DocumentLockScope.Acquire(document))
            {
                // Recheck under the document lease immediately before opening
                // the database transaction. This is read-only: it neither
                // prompts nor saves the interactive document.
                AutodeskDocumentReadState before =
                    AutodeskDocumentReadGate.Capture(document);
                CadDocumentSnapshot snapshot;
                using (Transaction transaction =
                    document.Database.TransactionManager.StartTransaction())
                {
                    try
                    {
                        snapshot = AutodeskSnapshotExporter.Export(
                            document.Database,
                            transaction,
                            before.DiskBinding,
                            RequireBinding(),
                            before.DatabaseFingerprint,
                            before.DatabaseVersion);
                    }
                    finally
                    {
                        transaction.Abort();
                    }
                }

                // The second gate catches changes made while entities were
                // read. Its disk binding, database identity, and saved
                // revision indicator must all still match the first one.
                before.RequireUnchanged(AutodeskDocumentReadGate.Capture(document));
                GeometryExportV2 export = ExactCadExporter.Export(snapshot);
                // The bridge serializes this same portable contract in the
                // geometry export; Core Console compares it after retargeting
                // only source binding to its private copy.
                if (!export.PortablePrewriteProjection.Matches(export))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.TransactionFailure,
                        "Bridge portable prewrite projection is inconsistent.");
                }

                return export;
            }
        }

        /// <summary>
        /// Binds the server only after a client-generated ID reaches its
        /// first health request.  The binding and the host context are one
        /// state transition so a server can never authenticate against an
        /// unrelated server-generated ID.
        /// </summary>
        private void BindRequestSession(BridgeRequest request)
        {
            if (request == null)
            {
                throw new ArgumentNullException(nameof(request));
            }

            lock (stateGate)
            {
                if (invalidated || stopped)
                {
                    throw new AdapterFailureException(
                        "LPF_BRIDGE",
                        "The bridge session is invalid.");
                }

                bool newlyBound = sessionOwnership.BindFirstHealthOrRequireSame(
                    request);
                if (newlyBound)
                {
                    // BridgeRequest has already checked the canonical wire
                    // spelling.  Python owns generation; C# accepts and
                    // commits exactly that first proposal.
                    binding = AutodeskHostBinding.Create(
                        request.SessionId,
                        pluginFingerprint);
                }
            }
        }

        private NativeGeometryBindingContextV2 RequireBinding()
        {
            lock (stateGate)
            {
                if (binding == null || !sessionOwnership.IsBound)
                {
                    throw new AdapterFailureException(
                        "LPF_BRIDGE",
                        "The bridge has no client-owned session binding.");
                }

                return binding;
            }
        }

        private void RequireSessionDescriptorIssued()
        {
            lock (stateGate)
            {
                if (!sessionOwnership.SessionDescriptorIssued ||
                    initialExport == null)
                {
                    throw new AdapterFailureException(
                        "LPF_BRIDGE",
                        "The bridge session handshake is incomplete.");
                }
            }
        }

        private GeometryExportV2 RequireInitialExport()
        {
            lock (stateGate)
            {
                if (initialExport == null)
                {
                    throw new AdapterFailureException(
                        "LPF_BRIDGE",
                        "The initial bridge document is unavailable.");
                }

                return initialExport;
            }
        }

        private Dictionary<string, object?> BuildHealth()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "kind", "health" },
                { "protocol_major", 1L },
                { "protocol_minor", 0L },
                { "adapter", AdapterWireValue() },
                { "plugin", PluginWireValue() },
                { "host", HostWireValue() },
                { "capabilities", CapabilitiesWireValue() },
            };
        }

        private Dictionary<string, object?> BuildSession(
            BridgeRequest request,
            GeometryExportV2 current)
        {
            string clientNonce = request.RequireStringParameter("client_nonce");
            string challenge = request.RequireStringParameter("challenge");
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "kind", "session" },
                { "bridge_nonce", bridgeNonce },
                {
                    "challenge_response",
                    BridgeChallengeResponse.Derive(
                        RequireBoundSessionId(),
                        clientNonce,
                        challenge,
                        bridgeNonce)
                },
                { "adapter", AdapterWireValue() },
                { "plugin", PluginWireValue() },
                { "host", HostWireValue() },
                { "capabilities", CapabilitiesWireValue() },
                { "current_document", DocumentWireValue(current) },
            };
        }

        private Dictionary<string, object?> DocumentWireValue(GeometryExportV2 export)
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "saved", true },
                { "path_fingerprint", export.Snapshot.Source.PathFingerprint },
                {
                    "file_identity_fingerprint",
                    export.Snapshot.Source.FileIdentityFingerprint
                },
                { "sha256", export.Snapshot.Source.Sha256 },
                { "byte_size", export.Snapshot.Source.ByteSize },
                {
                    "dwg_header_signature",
                    export.Snapshot.Source.DwgHeaderSignature
                },
                {
                    "database_instance_fingerprint",
                    export.Document.DatabaseInstanceFingerprint
                },
                { "revision_fingerprint", export.Document.RevisionFingerprint },
            };
        }

        private Dictionary<string, object?> AdapterWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "id", AdapterIdentity.AdapterId },
                { "profile", AdapterIdentity.Profile },
                { "version", AdapterIdentity.PluginVersion },
            };
        }

        private Dictionary<string, object?> PluginWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "id", AdapterIdentity.PluginId },
                { "version", AdapterIdentity.PluginVersion },
                { "fingerprint", pluginFingerprint },
            };
        }

        private Dictionary<string, object?> HostWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "product", "autocad" },
                { "release", AdapterIdentity.HostRelease },
                { "runtime", AdapterIdentity.HostRuntime },
                { "mode", "full_host" },
            };
        }

        private List<object?> CapabilitiesWireValue()
        {
            return NativeCadCapabilities.ToWireValue(
                AdapterIdentity.Capabilities);
        }

        private string RequireBoundSessionId()
        {
            lock (stateGate)
            {
                return sessionOwnership.RequireBoundSessionId();
            }
        }

        private void RequireExpectedRevision(
            BridgeRequest request,
            GeometryExportV2 current)
        {
            string expected = request.RequireStringParameter(
                "expected_document_revision");
            CanonicalJson.RequireSha256(expected, "expectedDocumentRevision");
            if (!string.Equals(
                    expected,
                    current.Document.RevisionFingerprint,
                    StringComparison.Ordinal))
            {
                throw new BridgeDocumentChangedException();
            }
        }

        private async Task WriteErrorAsync(
            NamedPipeServerStream pipe,
            BridgeRequest request,
            string code,
            CancellationToken cancellationToken)
        {
            ThrowIfExpired();
            await WriteFrameAsync(
                pipe,
                BridgeResponse.Error(request.Id, code),
                MaxControlResponseBytes,
                cancellationToken).ConfigureAwait(false);
        }

        private async Task WriteFrameAsync(
            Stream stream,
            Dictionary<string, object?> value,
            int maximum,
            CancellationToken cancellationToken)
        {
            ThrowIfExpired();
            cancellationToken.ThrowIfCancellationRequested();
            byte[] payload = CanonicalJson.SerializeUtf8(
                value,
                NativeCadCanonicalJsonProfiles.BridgeResponse);
            // Canonical serialization is synchronous, so it cannot be
            // interrupted mid-object.  Checking the original request token
            // immediately after it completes prevents a late frame write.
            ThrowIfExpired();
            cancellationToken.ThrowIfCancellationRequested();
            if (payload.Length == 0 || payload.Length > maximum)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_FRAME",
                    "A bridge response exceeds its fixed bound.");
            }

            byte[] header =
            {
                (byte)(payload.Length >> 24),
                (byte)(payload.Length >> 16),
                (byte)(payload.Length >> 8),
                (byte)payload.Length,
            };
            ThrowIfExpired();
            cancellationToken.ThrowIfCancellationRequested();
            await stream.WriteAsync(header, 0, header.Length, cancellationToken)
                .ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
            await stream.WriteAsync(payload, 0, payload.Length, cancellationToken)
                .ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
            await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
        }

        private static async Task<byte[]> ReadExactlyAsync(
            Stream stream,
            int length,
            CancellationToken cancellationToken)
        {
            byte[] result = new byte[length];
            int offset = 0;
            while (offset < result.Length)
            {
                int read = await stream.ReadAsync(
                    result,
                    offset,
                    result.Length - offset,
                    cancellationToken).ConfigureAwait(false);
                if (read == 0)
                {
                    throw new EndOfStreamException();
                }

                offset += read;
            }

            return result;
        }

        private BridgeRequestDeadline CreateControlDeadline(
            CancellationToken outer)
        {
            return new BridgeRequestDeadline(
                BridgeRequestDeadline.ControlTimeoutMilliseconds,
                expiryLifetime.DeadlineTimestamp,
                outer,
                lifetimeCancellation.Token);
        }

        private BridgeRequestDeadline CreateRequestDeadline(
            string method,
            CancellationToken outer)
        {
            return new BridgeRequestDeadline(
                BridgeRequestDeadline.MethodTimeoutMilliseconds(method),
                expiryLifetime.DeadlineTimestamp,
                outer,
                lifetimeCancellation.Token);
        }

        private void CloseAndInvalidate(NamedPipeServerStream pipe)
        {
            Invalidate();
            try
            {
                pipe.Dispose();
            }
            catch (IOException)
            {
                // The deadline path is fail-closed; an already-retired pipe
                // has the required effect.
            }
        }

        internal sealed class BridgeRequestDeadline : IDisposable
        {
            internal const int ControlTimeoutMilliseconds = 3000;

            private readonly CancellationTokenSource cancellation;
            private readonly long deadlineTimestamp;
            private bool disposed;

            internal BridgeRequestDeadline(
                int methodTimeoutMilliseconds,
                long sessionDeadlineTimestamp,
                CancellationToken outer,
                CancellationToken hostLifetime)
            {
                if (methodTimeoutMilliseconds <= 0)
                {
                    throw new ArgumentOutOfRangeException(
                        nameof(methodTimeoutMilliseconds));
                }

                long issuedTimestamp = Stopwatch.GetTimestamp();
                long methodDeadline = AddMilliseconds(
                    issuedTimestamp,
                    methodTimeoutMilliseconds);
                // This is the one request deadline.  A host shutdown can
                // still cancel the linked source earlier, but no stage gets a
                // new relative timer after dispatch.
                deadlineTimestamp = Math.Min(
                    methodDeadline,
                    sessionDeadlineTimestamp);
                cancellation = CancellationTokenSource.CreateLinkedTokenSource(
                    outer,
                    hostLifetime);
                ScheduleAbsoluteCancellation();
            }

            internal CancellationToken Token
            {
                get
                {
                    CancelIfDeadlinePassed();
                    return cancellation.Token;
                }
            }

            internal long DeadlineTimestamp
            {
                get { return deadlineTimestamp; }
            }

            internal static int MethodTimeoutMilliseconds(string method)
            {
                if (string.Equals(method, "health", StringComparison.Ordinal) ||
                    string.Equals(method, "get_session", StringComparison.Ordinal))
                {
                    return 3000;
                }
                else if (string.Equals(
                    method,
                    "get_current_document",
                    StringComparison.Ordinal))
                {
                    return 5000;
                }
                else if (string.Equals(
                    method,
                    "export_inventory",
                    StringComparison.Ordinal))
                {
                    return 30000;
                }
                else if (string.Equals(
                    method,
                    "export_exact_geometry",
                    StringComparison.Ordinal))
                {
                    return 60000;
                }

                throw new AdapterFailureException(
                    "LPF_PIPE_METHOD",
                    "A bridge method is not allowlisted.");
            }

            public void Dispose()
            {
                if (disposed)
                {
                    return;
                }

                disposed = true;
                cancellation.Dispose();
            }

            private void ScheduleAbsoluteCancellation()
            {
                long remainingTicks = deadlineTimestamp - Stopwatch.GetTimestamp();
                if (remainingTicks <= 0)
                {
                    cancellation.Cancel();
                    return;
                }

                double remainingMilliseconds = Math.Ceiling(
                    remainingTicks * 1000d / Stopwatch.Frequency);
                cancellation.CancelAfter(TimeSpan.FromMilliseconds(
                    Math.Min(
                        (double)int.MaxValue,
                        Math.Max(1d, remainingMilliseconds))));
            }

            private void CancelIfDeadlinePassed()
            {
                if (!cancellation.IsCancellationRequested &&
                    Stopwatch.GetTimestamp() >= deadlineTimestamp)
                {
                    cancellation.Cancel();
                }
            }

            private static long AddMilliseconds(
                long timestamp,
                int milliseconds)
            {
                long ticks = (long)Math.Ceiling(
                    milliseconds * Stopwatch.Frequency / 1000d);
                return ticks > long.MaxValue - timestamp
                    ? long.MaxValue
                    : timestamp + ticks;
            }
        }

        private static int ResponseMaximum(string method)
        {
            if (string.Equals(method, "export_exact_geometry", StringComparison.Ordinal))
            {
                return MaxGeometryResponseBytes;
            }

            if (string.Equals(method, "export_inventory", StringComparison.Ordinal))
            {
                return MaxInventoryResponseBytes;
            }

            return MaxControlResponseBytes;
        }

        private bool IsExpired()
        {
            return expiryLifetime.IsExpired;
        }

        private void ThrowIfExpired()
        {
            if (IsExpired())
            {
                throw new BridgeSessionExpiredException();
            }
        }

        private bool IsInvalidated()
        {
            lock (stateGate)
            {
                return invalidated || stopped;
            }
        }

        private void Invalidate()
        {
            lock (stateGate)
            {
                invalidated = true;
            }
        }

        private void OnExpiry()
        {
            NamedPipeServerStream? pipe;
            lock (stateGate)
            {
                invalidated = true;
                terminationReason = BridgeTerminationReason.Expired;
                pipe = activePipe;
            }

            // A few host/.NET Framework pipe implementations do not honour a
            // cancellation token while an overlapped operation is pending.
            // Closing the one active stream makes every blocked wait/read/
            // write/flush observe the original session deadline.
            if (pipe != null)
            {
                try
                {
                    pipe.Dispose();
                }
                catch (IOException)
                {
                    // The expiry path is fail-closed; a concurrently closed
                    // pipe is already retired.
                }
            }
        }

        private void ReleaseLifetimeResources()
        {
            lifetimeCancellation.Dispose();
            expiryLifetime.Dispose();
        }

        private void SetActivePipe(NamedPipeServerStream pipe)
        {
            lock (stateGate)
            {
                activePipe = pipe;
            }

            if (IsExpired())
            {
                pipe.Dispose();
            }
        }

        private void ClearActivePipe(NamedPipeServerStream pipe)
        {
            lock (stateGate)
            {
                if (ReferenceEquals(activePipe, pipe))
                {
                    activePipe = null;
                }
            }
        }

        private void OnDocumentChanged(
            object? sender,
            DocumentCollectionEventArgs eventArguments)
        {
            Invalidate();
        }

        private static string RandomBase64Url(int bytes)
        {
            byte[] value = new byte[bytes];
            using (RandomNumberGenerator random = RandomNumberGenerator.Create())
            {
                random.GetBytes(value);
            }

            return Convert.ToBase64String(value)
                .TrimEnd('=')
                .Replace('+', '-')
                .Replace('/', '_');
        }

        // The fixed 32-character base64url token has 192 input bits. Each
        // unmodified candidate is accepted only when it meets the complete
        // Python local-pipe predicate; rejection sampling never patches
        // random output or substitutes a non-CSPRNG character.
        internal static string RandomPipeToken()
        {
            using (RandomNumberGenerator random = RandomNumberGenerator.Create())
            {
                return RandomPipeToken(
                    delegate
                    {
                        byte[] value = new byte[24];
                        random.GetBytes(value);
                        return value;
                    });
            }
        }

        /// <summary>
        /// Bounded deterministic seam for SDK-free tests. Production passes
        /// only CSPRNG bytes and never patches a candidate's random contents.
        /// </summary>
        internal static string RandomPipeToken(Func<byte[]> nextBytes)
        {
            if (nextBytes == null)
            {
                throw new ArgumentNullException(nameof(nextBytes));
            }

            const int maximumAttempts = 16;
            for (int attempt = 0; attempt < maximumAttempts; attempt++)
            {
                byte[] value = nextBytes();
                if (value == null || value.Length != 24)
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_TOKEN",
                        "The pipe token random source is invalid.");
                }

                string candidate = Convert.ToBase64String(value)
                    .TrimEnd('=')
                    .Replace('+', '-')
                    .Replace('/', '_');
                if (IsPythonCompatiblePipeToken(candidate))
                {
                    return candidate;
                }
            }

            throw new AdapterFailureException(
                "LPF_PIPE_TOKEN",
                "The pipe token random source cannot produce a valid token.");
        }

        /// <summary>
        /// Mirrors Python's <c>validate_pipe_name</c> token predicate after
        /// its final-hyphen split.  Base64url provides the alphabet and
        /// fixed candidate length, but retain those checks here so this is a
        /// complete and auditable grammar boundary.
        /// </summary>
        private static bool IsPythonCompatiblePipeToken(string candidate)
        {
            if (candidate == null || candidate.Length < 16 || candidate.Length > 128)
            {
                return false;
            }

            int tokenStart = candidate.LastIndexOf('-') + 1;
            bool hasLetter = false;
            bool hasDigit = false;
            HashSet<char> distinct = new HashSet<char>();
            for (int index = 0; index < candidate.Length; index++)
            {
                char character = candidate[index];
                bool letter = (character >= 'A' && character <= 'Z') ||
                    (character >= 'a' && character <= 'z');
                bool digit = character >= '0' && character <= '9';
                if (!(letter || digit || character == '_' || character == '-'))
                {
                    return false;
                }

                if (index >= tokenStart)
                {
                    hasLetter |= letter;
                    hasDigit |= digit;
                    distinct.Add(character);
                }
            }

            return hasLetter && hasDigit && distinct.Count >= 8;
        }
    }

    /// <summary>
    /// One boot-monotonic expiry source captured at bridge construction.
    /// It is never recreated for a client reconnect or a request.
    /// </summary>
    internal sealed class BridgeExpiryLifetime : IDisposable
    {
        private readonly CancellationTokenSource expiry =
            new CancellationTokenSource();
        private readonly long deadlineTimestamp;
        private readonly CancellationTokenRegistration expiryRegistration;
        private bool disposed;

        internal BridgeExpiryLifetime(DateTime expiresUtc, Action onExpired)
        {
            if (onExpired == null)
            {
                throw new ArgumentNullException(nameof(onExpired));
            }

            // Capture both values once.  UTC remains the wire-advertised
            // expiry; Stopwatch supplies the local, boot-monotonic deadline.
            DateTime issuedUtc = DateTime.UtcNow;
            long issuedTimestamp = Stopwatch.GetTimestamp();
            double remainingMilliseconds =
                (expiresUtc - issuedUtc).TotalMilliseconds;
            long delayTicks = remainingMilliseconds <= 0
                ? 0
                : (long)Math.Ceiling(
                    remainingMilliseconds * Stopwatch.Frequency / 1000d);
            deadlineTimestamp = delayTicks > long.MaxValue - issuedTimestamp
                ? long.MaxValue
                : issuedTimestamp + delayTicks;
            expiryRegistration = expiry.Token.Register(onExpired);

            if (remainingMilliseconds <= 0)
            {
                expiry.Cancel();
            }
            else
            {
                double bounded = Math.Min(
                    remainingMilliseconds,
                    (double)int.MaxValue);
                expiry.CancelAfter(
                    TimeSpan.FromMilliseconds(Math.Max(1d, Math.Ceiling(bounded))));
            }
        }

        internal CancellationToken Token
        {
            get { return expiry.Token; }
        }

        internal long DeadlineTimestamp
        {
            get { return deadlineTimestamp; }
        }

        internal bool IsExpired
        {
            get
            {
                if (!expiry.IsCancellationRequested &&
                    Stopwatch.GetTimestamp() >= deadlineTimestamp)
                {
                    expiry.Cancel();
                }

                return expiry.IsCancellationRequested;
            }
        }

        public void Dispose()
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            expiryRegistration.Dispose();
            expiry.Dispose();
        }
    }

    internal enum BridgeTerminationReason
    {
        None,
        HostShutdown,
        Expired,
    }

    /// <summary>Strict parsed request; no JSON method can become a host command.</summary>
    internal sealed class BridgeRequest
    {
        internal BridgeRequest(
            string id,
            string method,
            Dictionary<string, object?> parameters,
            string sessionId)
        {
            Id = id;
            Method = method;
            Parameters = parameters;
            SessionId = sessionId;
        }

        internal string Id { get; private set; }

        internal string Method { get; private set; }

        internal Dictionary<string, object?> Parameters { get; private set; }

        /// <summary>
        /// Canonical client-proposed session ID.  The server state machine,
        /// rather than request parsing, owns first-request binding.
        /// </summary>
        internal string SessionId { get; private set; }

        internal static BridgeRequest Parse(object? raw)
        {
            Dictionary<string, object?> envelope = RequireObject(raw);
            RequireKeys(envelope, "protocol_version", "id", "method", "params");
            RequireLiteral(
                RequireString(envelope, "protocol_version"),
                NativeCadProtocolV2.BridgeVersion);
            string id = RequireString(envelope, "id");
            if (id.Length != 32)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "A bridge request identifier is invalid.");
            }

            for (int index = 0; index < id.Length; index++)
            {
                if (!((id[index] >= '0' && id[index] <= '9') ||
                    (id[index] >= 'a' && id[index] <= 'f')))
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_REQUEST",
                        "A bridge request identifier is invalid.");
                }
            }

            string method = RequireString(envelope, "method");
            Dictionary<string, object?> parameters = RequireObject(
                RequireValue(envelope, "params"));
            if (string.Equals(method, "health", StringComparison.Ordinal) ||
                string.Equals(method, "get_current_document", StringComparison.Ordinal))
            {
                RequireKeys(parameters, "session_id");
            }
            else if (string.Equals(method, "get_session", StringComparison.Ordinal))
            {
                RequireKeys(parameters, "session_id", "client_nonce", "challenge");
                RequireNonce(RequireString(parameters, "client_nonce"));
                RequireNonce(RequireString(parameters, "challenge"));
            }
            else if (string.Equals(method, "export_inventory", StringComparison.Ordinal) ||
                string.Equals(method, "export_exact_geometry", StringComparison.Ordinal))
            {
                RequireKeys(parameters, "session_id", "expected_document_revision");
            }
            else
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_METHOD",
                    "A bridge method is not allowlisted.");
            }

            string sessionId = RequireSessionId(
                RequireString(parameters, "session_id"));
            return new BridgeRequest(id, method, parameters, sessionId);
        }

        internal string RequireStringParameter(string key)
        {
            return RequireString(Parameters, key);
        }

        private static void RequireNonce(string value)
        {
            if (value.Length < 43 || value.Length > 128)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "A bridge nonce is invalid.");
            }

            for (int index = 0; index < value.Length; index++)
            {
                char character = value[index];
                if (!((character >= 'A' && character <= 'Z') ||
                    (character >= 'a' && character <= 'z') ||
                    (character >= '0' && character <= '9') ||
                    character == '_' ||
                    character == '-'))
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_REQUEST",
                        "A bridge nonce is invalid.");
                }
            }
        }

        private static string RequireSessionId(string value)
        {
            const string Prefix = "native-session-";
            if (value.Length != Prefix.Length + 32 ||
                !value.StartsWith(Prefix, StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "A bridge session identifier is invalid.");
            }

            for (int index = Prefix.Length; index < value.Length; index++)
            {
                char character = value[index];
                if (!((character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f')))
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_REQUEST",
                        "A bridge session identifier is invalid.");
                }
            }

            return value;
        }

        private static Dictionary<string, object?> RequireObject(object? value)
        {
            Dictionary<string, object?>? result = value as Dictionary<string, object?>;
            if (result == null)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "A bridge request object is invalid.");
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
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "A bridge request field is missing.");
            }

            return value;
        }

        private static string RequireString(
            IDictionary<string, object?> values,
            string key)
        {
            string? value = RequireValue(values, key) as string;
            if (value == null)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "A bridge request string is invalid.");
            }

            return value;
        }

        private static void RequireKeys(
            IDictionary<string, object?> values,
            params string[] keys)
        {
            if (values.Count != keys.Length)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "A bridge request has extra fields.");
            }

            for (int index = 0; index < keys.Length; index++)
            {
                if (!values.ContainsKey(keys[index]))
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_REQUEST",
                        "A bridge request field is missing.");
                }
            }
        }

        private static void RequireLiteral(string value, string expected)
        {
            if (!string.Equals(value, expected, StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "A bridge request fixed field is invalid.");
            }
        }
    }

    /// <summary>
    /// The small protocol state machine for one client-owned session ID.
    /// <see cref="NativePipeBridgeServer"/> invokes it while holding its
    /// lifecycle lock, so adopting the first health proposal and constructing
    /// the host binding are one atomic server transition.
    /// </summary>
    internal sealed class BridgeSessionOwnership
    {
        private string? sessionId;
        private bool sessionDescriptorIssued;

        internal bool IsBound
        {
            get { return sessionId != null; }
        }

        internal bool SessionDescriptorIssued
        {
            get { return sessionDescriptorIssued; }
        }

        /// <summary>
        /// Accepts the first canonical health proposal or requires the same
        /// client-owned ID on every later request. Returns true only when the
        /// caller must create the one host binding for a newly adopted ID.
        /// </summary>
        internal bool BindFirstHealthOrRequireSame(BridgeRequest request)
        {
            if (request == null)
            {
                throw new ArgumentNullException(nameof(request));
            }

            if (sessionId == null)
            {
                if (!string.Equals(request.Method, "health", StringComparison.Ordinal))
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_REQUEST",
                        "The first bridge request must be health.");
                }

                sessionId = request.SessionId;
                return true;
            }

            if (!string.Equals(sessionId, request.SessionId, StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "The bridge session identifier changed.");
            }

            if (sessionDescriptorIssued &&
                string.Equals(request.Method, "get_session", StringComparison.Ordinal))
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "The bridge session handshake was duplicated.");
            }

            return false;
        }

        /// <summary>Marks the one successful descriptor-producing request.</summary>
        internal void MarkSessionDescriptorIssued()
        {
            if (sessionId == null || sessionDescriptorIssued)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_REQUEST",
                    "The bridge session handshake was duplicated.");
            }

            sessionDescriptorIssued = true;
        }

        internal string RequireBoundSessionId()
        {
            if (sessionId == null)
            {
                throw new AdapterFailureException(
                    "LPF_BRIDGE",
                    "The bridge has no client-owned session binding.");
            }

            return sessionId;
        }
    }

    internal static class BridgeResponse
    {
        internal static Dictionary<string, object?> Success(
            string requestId,
            Dictionary<string, object?> result)
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "protocol_version", NativeCadProtocolV2.BridgeVersion },
                { "id", requestId },
                { "result", result },
            };
        }

        internal static Dictionary<string, object?> Error(
            string requestId,
            string code)
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "protocol_version", NativeCadProtocolV2.BridgeVersion },
                { "id", requestId },
                {
                    "error",
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "code", code },
                    }
                },
            };
        }
    }

    /// <summary>Domain-separated SHA-256 handshake transcript shared with Python.</summary>
    internal static class BridgeChallengeResponse
    {
        internal static string Derive(
            string sessionId,
            string clientNonce,
            string challenge,
            string bridgeNonce)
        {
            string[] fields =
            {
                NativeCadProtocolV2.BridgeVersion,
                "liang-pingfa/native-bridge/challenge-response/v1",
                sessionId,
                clientNonce,
                challenge,
                bridgeNonce,
            };
            using (MemoryStream stream = new MemoryStream())
            {
                for (int index = 0; index < fields.Length; index++)
                {
                    byte[] encoded = Encoding.ASCII.GetBytes(fields[index]);
                    byte[] length =
                    {
                        (byte)(encoded.Length >> 24),
                        (byte)(encoded.Length >> 16),
                        (byte)(encoded.Length >> 8),
                        (byte)encoded.Length,
                    };
                    stream.Write(length, 0, length.Length);
                    stream.Write(encoded, 0, encoded.Length);
                }

                return CanonicalJson.Sha256Hex(stream.ToArray());
            }
        }
    }

    /// <summary>Private bootstrap advertisement; it is not a public artifact or a host claim.</summary>
    internal static class NativeBridgeAdvertisement
    {
        internal static void Write(
            string path,
            BootstrapCommandContext context,
            NativePipeBridgeServer server)
        {
            Dictionary<string, object?> payload =
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "schema_version", "liang-pingfa/native-bridge-bootstrap/v1" },
                    { "nonce", context.Nonce },
                    { "pipe", server.PipeName },
                    { "pid", (long)server.ProcessId },
                    {
                        "protocol_version",
                        NativeCadProtocolV2.BridgeVersion
                    },
                    { "protocol_major", 1L },
                    { "protocol_minor", 0L },
                    { "mode", "read_only" },
                    {
                        "adapter",
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "id", AdapterIdentity.AdapterId },
                            { "profile", AdapterIdentity.Profile },
                            { "version", AdapterIdentity.PluginVersion },
                        }
                    },
                    {
                        "plugin",
                        new Dictionary<string, object?>(StringComparer.Ordinal)
                        {
                            { "id", AdapterIdentity.PluginId },
                            { "version", AdapterIdentity.PluginVersion },
                            { "fingerprint", server.PluginFingerprint },
                        }
                    },
                    {
                        "capabilities",
                        CapabilitiesWireValue()
                    },
                    {
                        "expires_at",
                        server.ExpiresUtc.ToString(
                            "yyyy-MM-dd'T'HH:mm:ss'Z'",
                            CultureInfo.InvariantCulture)
                    },
                };
            payload.Add(
                "integrity",
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "algorithm", "SHA-256" },
                    { "sha256", CanonicalJson.Sha256Hex(payload) },
                });
            byte[] bytes = CanonicalJson.SerializeUtf8(payload);
            using (FileStream stream = new FileStream(
                path,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None,
                4096,
                FileOptions.WriteThrough))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
            PrivatePathPolicy.RequirePrivateFile(
                path,
                context.PrivateRoot,
                ".json");
        }

        private static List<object?> CapabilitiesWireValue()
        {
            return NativeCadCapabilities.ToWireValue(
                AdapterIdentity.Capabilities);
        }
    }

    /// <summary>Secure Win32 pipe construction with explicit user/System DACL and remote rejection.</summary>
    internal static class NativePipeFactory
    {
        private const uint PipeAccessDuplex = 0x00000003;
        private const uint FileFlagFirstPipeInstance = 0x00080000;
        private const uint FileFlagOverlapped = 0x40000000;
        private const uint PipeTypeByte = 0x00000000;
        private const uint PipeReadModeByte = 0x00000000;
        private const uint PipeWait = 0x00000000;
        private const uint PipeRejectRemoteClients = 0x00000008;
        private const int InvalidHandleValue = -1;

        internal static NamedPipeServerStream Create(
            string fullPipeName,
            bool firstInstance)
        {
            string currentSid = WindowsPrivateAcl.CurrentUserSid();
            string sddl = "D:P(A;;GA;;;" + currentSid + ")(A;;GA;;;SY)";
            IntPtr descriptor;
            uint descriptorLength;
            if (!ConvertStringSecurityDescriptorToSecurityDescriptor(
                    sddl,
                    1,
                    out descriptor,
                    out descriptorLength) ||
                descriptor == IntPtr.Zero)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_ACL",
                    "The bridge pipe security descriptor cannot be created.");
            }

            try
            {
                SecurityAttributes attributes = new SecurityAttributes
                {
                    Length = Marshal.SizeOf(typeof(SecurityAttributes)),
                    SecurityDescriptor = descriptor,
                    InheritHandle = false,
                };
                uint openMode = PipeAccessDuplex | FileFlagOverlapped;
                if (firstInstance)
                {
                    openMode |= FileFlagFirstPipeInstance;
                }

                IntPtr raw = CreateNamedPipe(
                    fullPipeName,
                    openMode,
                    PipeTypeByte | PipeReadModeByte | PipeWait |
                        PipeRejectRemoteClients,
                    1,
                    64 * 1024,
                    64 * 1024,
                    0,
                    ref attributes);
                if (raw == IntPtr.Zero || raw.ToInt64() == InvalidHandleValue)
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_ACL",
                        "The bridge pipe cannot be created as one local instance.");
                }

                SafePipeHandle handle = new SafePipeHandle(raw, true);
                try
                {
                    return new NamedPipeServerStream(
                        PipeDirection.InOut,
                        true,
                        false,
                        handle);
                }
                catch
                {
                    handle.Dispose();
                    throw;
                }
            }
            finally
            {
                LocalFree(descriptor);
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SecurityAttributes
        {
            internal int Length;
            internal IntPtr SecurityDescriptor;
            [MarshalAs(UnmanagedType.Bool)]
            internal bool InheritHandle;
        }

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(
            string stringSecurityDescriptor,
            uint stringSdRevision,
            out IntPtr securityDescriptor,
            out uint securityDescriptorSize);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateNamedPipe(
            string name,
            uint openMode,
            uint pipeMode,
            uint maximumInstances,
            uint outputBufferSize,
            uint inputBufferSize,
            uint defaultTimeout,
            ref SecurityAttributes securityAttributes);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr LocalFree(IntPtr memory);
    }

    /// <summary>Rejects remote, cross-SID, and cross-logon-session pipe clients without impersonation.</summary>
    internal static class NativePipeClientIdentity
    {
        private const uint ProcessQueryLimitedInformation = 0x1000;
        private const uint TokenQuery = 0x0008;
        private const int TokenUser = 1;
        private const int TokenSessionId = 12;

        internal static void Verify(SafePipeHandle pipe)
        {
            uint clientPid;
            if (!GetNamedPipeClientProcessId(pipe, out clientPid) || clientPid == 0)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_CLIENT",
                    "The pipe client PID is unavailable.");
            }

            IntPtr process = OpenProcess(
                ProcessQueryLimitedInformation,
                false,
                clientPid);
            if (process == IntPtr.Zero)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_CLIENT",
                    "The pipe client cannot be inspected.");
            }

            try
            {
                IntPtr token;
                if (!OpenProcessToken(process, TokenQuery, out token) ||
                    token == IntPtr.Zero)
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_CLIENT",
                        "The pipe client token is unavailable.");
                }

                try
                {
                    string sid = ReadTokenSid(token);
                    int session = ReadTokenSession(token);
                    int currentSession;
                    using (Process current = Process.GetCurrentProcess())
                    {
                        currentSession = current.SessionId;
                    }

                    if (!string.Equals(
                            sid,
                            WindowsPrivateAcl.CurrentUserSid(),
                            StringComparison.Ordinal) ||
                        session != currentSession)
                    {
                        throw new AdapterFailureException(
                            "LPF_PIPE_CLIENT",
                            "The pipe client identity/session is not authorized.");
                    }
                }
                finally
                {
                    CloseHandle(token);
                }
            }
            finally
            {
                CloseHandle(process);
            }
        }

        private static string ReadTokenSid(IntPtr token)
        {
            IntPtr buffer = ReadTokenInformation(token, TokenUser);
            try
            {
                IntPtr sid = Marshal.ReadIntPtr(buffer);
                IntPtr sidString;
                if (sid == IntPtr.Zero ||
                    !ConvertSidToStringSid(sid, out sidString) ||
                    sidString == IntPtr.Zero)
                {
                    throw new AdapterFailureException(
                        "LPF_PIPE_CLIENT",
                        "The pipe client SID is unavailable.");
                }

                try
                {
                    string? value = Marshal.PtrToStringUni(sidString);
                    if (string.IsNullOrEmpty(value))
                    {
                        throw new AdapterFailureException(
                            "LPF_PIPE_CLIENT",
                            "The pipe client SID is unavailable.");
                    }

                    return value;
                }
                finally
                {
                    LocalFree(sidString);
                }
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }

        private static int ReadTokenSession(IntPtr token)
        {
            IntPtr buffer = ReadTokenInformation(token, TokenSessionId);
            try
            {
                return Marshal.ReadInt32(buffer);
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }

        private static IntPtr ReadTokenInformation(IntPtr token, int informationClass)
        {
            int required;
            GetTokenInformation(token, informationClass, IntPtr.Zero, 0, out required);
            if (required < 1)
            {
                throw new AdapterFailureException(
                    "LPF_PIPE_CLIENT",
                    "The pipe client token information is unavailable.");
            }

            IntPtr buffer = Marshal.AllocHGlobal(required);
            if (!GetTokenInformation(
                    token,
                    informationClass,
                    buffer,
                    required,
                    out required))
            {
                Marshal.FreeHGlobal(buffer);
                throw new AdapterFailureException(
                    "LPF_PIPE_CLIENT",
                    "The pipe client token information is unavailable.");
            }

            return buffer;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetNamedPipeClientProcessId(
            SafePipeHandle pipe,
            out uint clientProcessId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(
            uint desiredAccess,
            [MarshalAs(UnmanagedType.Bool)] bool inheritHandle,
            uint processId);

        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool OpenProcessToken(
            IntPtr processHandle,
            uint desiredAccess,
            out IntPtr tokenHandle);

        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetTokenInformation(
            IntPtr tokenHandle,
            int tokenInformationClass,
            IntPtr tokenInformation,
            int tokenInformationLength,
            out int returnLength);

        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool ConvertSidToStringSid(
            IntPtr sid,
            out IntPtr stringSid);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr LocalFree(IntPtr memory);
    }

    internal sealed class BridgeDocumentChangedException : Exception
    {
    }

    internal sealed class BridgeSessionExpiredException : Exception
    {
    }
}
