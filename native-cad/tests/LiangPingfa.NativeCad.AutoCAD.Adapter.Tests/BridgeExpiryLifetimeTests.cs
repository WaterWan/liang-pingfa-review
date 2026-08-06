// SPDX-License-Identifier: MIT
// SDK-free local named-pipe expiry tests.  These exercise the same lifetime
// object the adapter links into every blocking bridge operation.

using System;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Threading;
using System.Threading.Tasks;
using LiangPingfa.NativeCad.AutoCAD.Adapter;

namespace LiangPingfa.NativeCad.AutoCAD.Adapter.Tests
{
    internal static class BridgeExpiryLifetimeTests
    {
        // Bridge-wide expiry also exercises listener replacement; leave it
        // enough scheduling headroom to reconnect a real Windows pipe.  The
        // request-deadline tests below retain the short injected 250ms bound.
        private const int ShortExpiryMilliseconds = 1000;
        private const int ShortRequestDeadlineMilliseconds = 250;
        private const int CompletionTimeoutMilliseconds = 3000;
        private const int DeadlineSlackMilliseconds = 1500;

        internal static void Run()
        {
            MethodDeadlinesAreConfigured();
            QuickHealthDispatchBlockedResponseExpires();
            QuickDocumentDispatchBlockedResponseExpires();
            QuickInventoryDispatchBlockedResponseExpires();
            QuickGeometryDispatchBlockedResponseExpires();
            BlockedErrorResponseExpires();
            SerializationDelayExpiresBeforeWrite();
            FlushDelayExpiresBeforeCompletion();
            RequestDeadlineJustBeforeExpirySucceeds();
            SessionExpiryCapsMethodDeadline();
            ExpiryCancelsConnectionWait();
            ExpiryCancelsLargeResponseWrite();
            ExpiryCancelsPartialRead();
            ExpiryCancelsDispatchWait();
            ExpiryCancelsErrorWrite();
            RequestJustBeforeExpirySucceeds();
            ReconnectWaitDoesNotResetExpiry();
            ExpiryLeavesNoPipeWorker();
        }

        private static void MethodDeadlinesAreConfigured()
        {
            AssertMethodDeadline("health", 3000);
            AssertMethodDeadline("get_session", 3000);
            AssertMethodDeadline("get_current_document", 5000);
            AssertMethodDeadline("export_inventory", 30000);
            AssertMethodDeadline("export_exact_geometry", 60000);
        }

        private static void QuickHealthDispatchBlockedResponseExpires()
        {
            AssertQuickDispatchThenBlockedWriteExpires(
                "health",
                "success response");
        }

        private static void QuickDocumentDispatchBlockedResponseExpires()
        {
            AssertQuickDispatchThenBlockedWriteExpires(
                "get_current_document",
                "success response");
        }

        private static void QuickInventoryDispatchBlockedResponseExpires()
        {
            AssertQuickDispatchThenBlockedWriteExpires(
                "export_inventory",
                "success response");
        }

        private static void QuickGeometryDispatchBlockedResponseExpires()
        {
            AssertQuickDispatchThenBlockedWriteExpires(
                "export_exact_geometry",
                "success response");
        }

        private static void BlockedErrorResponseExpires()
        {
            AssertQuickDispatchThenBlockedWriteExpires(
                "get_current_document",
                "error response");
        }

        private static void SerializationDelayExpiresBeforeWrite()
        {
            AssertDelayedResponseStageExpires(
                "response serialization",
                DelayUntilCancelledAsync);
        }

        private static void FlushDelayExpiresBeforeCompletion()
        {
            AssertDelayedResponseStageExpires(
                "response flush",
                FlushUntilCancelledAsync);
        }

        private static void RequestDeadlineJustBeforeExpirySucceeds()
        {
            string name = PipeName();
            NamedPipeServerStream? active = null;
            using (NativePipeBridgeServer.BridgeRequestDeadline deadline =
                NewRequestDeadline(1000, long.MaxValue))
            using (CancellationTokenRegistration closeOnDeadline =
                deadline.Token.Register(() => DisposeActivePipe(active)))
            {
                active = CreateServer(name);
                using (NamedPipeClientStream client = CreateClient(name))
                {
                    Task accepted = active.WaitForConnectionAsync(deadline.Token);
                    client.Connect(1000);
                    accepted.GetAwaiter().GetResult();

                    byte[] response = { 0x42 };
                    active.WriteAsync(
                        response,
                        0,
                        response.Length,
                        deadline.Token).GetAwaiter().GetResult();
                    active.FlushAsync(deadline.Token).GetAwaiter().GetResult();
                    Assert(client.ReadByte() == 0x42,
                        "A request immediately before its method deadline did not complete.");
                    Assert(!deadline.Token.IsCancellationRequested,
                        "A successful request reset or prematurely consumed its deadline.");
                }
            }
        }

        private static void SessionExpiryCapsMethodDeadline()
        {
            long sessionDeadline = AddMilliseconds(
                Stopwatch.GetTimestamp(),
                ShortRequestDeadlineMilliseconds);
            Stopwatch stopwatch = Stopwatch.StartNew();
            using (NativePipeBridgeServer.BridgeRequestDeadline deadline =
                NewRequestDeadline(1000, sessionDeadline))
            {
                Assert(deadline.DeadlineTimestamp == sessionDeadline,
                    "The request deadline did not choose the earlier session expiry.");
                TaskCompletionSource<object?> dispatch =
                    new TaskCompletionSource<object?>();
                Task worker = WaitForDispatchOrExpiryAsync(
                    dispatch.Task,
                    deadline.Token);
                RequireCompletesWithinDeadline(
                    worker,
                    stopwatch,
                    "A session expiry shorter than the method deadline was ignored.");
                Assert(deadline.Token.IsCancellationRequested,
                    "The capped request deadline did not cancel its dispatch wait.");
            }
        }

        private static void ExpiryCancelsConnectionWait()
        {
            NamedPipeServerStream? active = null;
            bool callbackRan = false;
            using (BridgeExpiryLifetime expiry = NewExpiry(
                () =>
                {
                    callbackRan = true;
                    DisposeActivePipe(active);
                }))
            {
                active = CreateServer(PipeName());
                Task worker = WaitForConnectionUntilCancelledAsync(active, expiry.Token);
                Task expiryObserved = WaitForCancellationAsync(expiry.Token);
                RequireCompletes(worker, "No-client connection wait outlived expiry.");
                RequireCompletes(expiryObserved,
                    "No-client connection wait completed before expiry fired.");
                RequireEventually(
                    () => callbackRan,
                    "No-client connection expiry callback did not run.");
                Assert(callbackRan && expiry.IsExpired,
                    "No-client connection wait did not use the expiry source.");
            }
        }

        private static void ExpiryCancelsLargeResponseWrite()
        {
            AssertBlockedWriteExpires("large response write");
        }

        private static void ExpiryCancelsErrorWrite()
        {
            AssertBlockedWriteExpires("error response write");
        }

        private static void ExpiryCancelsPartialRead()
        {
            string name = PipeName();
            NamedPipeServerStream? active = null;
            using (BridgeExpiryLifetime expiry = NewExpiry(
                () => DisposeActivePipe(active)))
            {
                active = CreateServer(name);
                using (NamedPipeClientStream client = CreateClient(name))
                {
                    Task accepted = active.WaitForConnectionAsync(expiry.Token);
                    client.Connect(1000);
                    accepted.GetAwaiter().GetResult();
                    client.WriteByte(0x01);
                    client.Flush();

                    Task reader = ReadExactlyUntilCancelledAsync(
                        active,
                        4,
                        expiry.Token);
                    Task expiryObserved = WaitForCancellationAsync(expiry.Token);
                    RequireCompletes(reader, "Partial frame read outlived expiry.");
                    RequireCompletes(expiryObserved,
                        "Partial frame read completed before expiry fired.");
                    Assert(expiry.IsExpired,
                        "Partial frame read did not retain the original expiry.");
                }
            }
        }

        private static void ExpiryCancelsDispatchWait()
        {
            using (BridgeExpiryLifetime expiry = NewExpiry(() => { }))
            {
                TaskCompletionSource<object?> dispatch =
                    new TaskCompletionSource<object?>();
                Task worker = WaitForDispatchOrExpiryAsync(dispatch.Task, expiry.Token);
                RequireCompletes(worker, "Queued dispatch wait outlived expiry.");
                Assert(expiry.IsExpired,
                    "Queued dispatch did not observe the expiry token.");
            }
        }

        private static void RequestJustBeforeExpirySucceeds()
        {
            string name = PipeName();
            NamedPipeServerStream? active = null;
            using (BridgeExpiryLifetime expiry = new BridgeExpiryLifetime(
                DateTime.UtcNow.AddMilliseconds(1000),
                () => DisposeActivePipe(active)))
            {
                active = CreateServer(name);
                using (NamedPipeClientStream client = CreateClient(name))
                {
                    Task accepted = active.WaitForConnectionAsync(expiry.Token);
                    client.Connect(1000);
                    accepted.GetAwaiter().GetResult();

                    byte[] response = { 0x42 };
                    active.WriteAsync(
                        response,
                        0,
                        response.Length,
                        expiry.Token).GetAwaiter().GetResult();
                    active.FlushAsync(expiry.Token).GetAwaiter().GetResult();
                    Assert(client.ReadByte() == 0x42,
                        "A request immediately before expiry did not complete.");
                    Assert(!expiry.IsExpired,
                        "A successful request reset or prematurely consumed expiry.");
                }
            }
        }

        private static void ReconnectWaitDoesNotResetExpiry()
        {
            string name = PipeName();
            NamedPipeServerStream? active = null;
            bool callbackRan = false;
            using (BridgeExpiryLifetime expiry = NewExpiry(
                () =>
                {
                    callbackRan = true;
                    DisposeActivePipe(active);
                }))
            {
                active = CreateServer(name);
                using (NamedPipeClientStream client = CreateClient(name))
                {
                    Task accepted = active.WaitForConnectionAsync(expiry.Token);
                    client.Connect(1000);
                    accepted.GetAwaiter().GetResult();
                }

                // Retire the first peer before creating the reconnect
                // listener.  Otherwise some Windows pipe implementations can
                // report the old peer's disconnect as the new listener's
                // completion before the shared expiry callback runs.
                active.Dispose();
                active = CreateServer(name);
                Task reconnect = WaitForConnectionUntilCancelledAsync(
                    active,
                    expiry.Token);
                Task expiryObserved = WaitForCancellationAsync(expiry.Token);
                RequireCompletes(reconnect, "Reconnect wait reset the expiry deadline.");
                RequireCompletes(
                    expiryObserved,
                    "Reconnect wait completed before the shared expiry fired.");
                RequireEventually(
                    () => callbackRan,
                    "Reconnect expiry callback did not run.");
                Assert(callbackRan && expiry.IsExpired,
                    "Reconnect path did not retain the original expiry source.");
            }
        }

        private static void ExpiryLeavesNoPipeWorker()
        {
            // Create the listener before arming the short expiry.  On a
            // saturated test worker, arming first can fire before the local
            // callback has a stream to retire, which tests scheduling rather
            // than the bridge lifetime behavior.
            NamedPipeServerStream? active = CreateServer(PipeName());
            bool pipeDisposed = false;
            using (BridgeExpiryLifetime expiry = NewExpiry(
                () =>
                {
                    pipeDisposed = true;
                    DisposeActivePipe(active);
                }))
            {
                Task worker = WaitForConnectionUntilCancelledAsync(active, expiry.Token);
                Task expiryObserved = WaitForCancellationAsync(expiry.Token);
                RequireCompletes(worker, "Expiry left a named-pipe worker running.");
                RequireCompletes(expiryObserved,
                    "Pipe worker completed before expiry fired.");
                RequireEventually(
                    () => pipeDisposed,
                    "Pipe worker expiry callback did not run.");
                Assert(worker.IsCompleted && !worker.IsFaulted && pipeDisposed,
                    "Expiry did not retire the pipe worker and active stream.");
            }
        }

        private static void AssertBlockedWriteExpires(string description)
        {
            string name = PipeName();
            NamedPipeServerStream? active = null;
            using (BridgeExpiryLifetime expiry = NewExpiry(
                () => DisposeActivePipe(active)))
            {
                active = CreateServer(name);
                using (NamedPipeClientStream client = CreateClient(name))
                {
                    Task accepted = active.WaitForConnectionAsync(expiry.Token);
                    client.Connect(1000);
                    accepted.GetAwaiter().GetResult();

                    // The peer remains connected but deliberately never reads
                    // this maximum-size bridge response.
                    byte[] response = new byte[32 * 1024 * 1024];
                    Task writer = WriteUntilCancelledAsync(
                        active,
                        response,
                        expiry.Token);
                    Thread.Sleep(50);
                    Assert(!writer.IsCompleted,
                        "A " + description + " did not block before expiry.");
                    RequireCompletes(writer, "A " + description + " outlived expiry.");
                    Assert(expiry.IsExpired,
                        "A " + description + " did not use the expiry token.");
                }
            }
        }

        private static void AssertQuickDispatchThenBlockedWriteExpires(
            string method,
            string responseKind)
        {
            string name = PipeName();
            NamedPipeServerStream? active = CreateServer(name);
            bool pipeClosed = false;
            Stopwatch stopwatch = Stopwatch.StartNew();
            using (NativePipeBridgeServer.BridgeRequestDeadline deadline =
                NewRequestDeadline(ShortRequestDeadlineMilliseconds, long.MaxValue))
            using (CancellationTokenRegistration closeOnDeadline =
                deadline.Token.Register(
                    () =>
                    {
                        pipeClosed = true;
                        DisposeActivePipe(active);
                    }))
            {
                using (NamedPipeClientStream client = CreateClient(name))
                {
                    Task accepted = active.WaitForConnectionAsync(deadline.Token);
                    client.Connect(1000);
                    accepted.GetAwaiter().GetResult();

                    Task dispatch = Task.FromResult(method);
                    RequireCompletes(dispatch,
                        "The " + method + " dispatch did not complete quickly.");

                    // The connected peer deliberately stops reading.  A
                    // method CTS released after dispatch would leave this
                    // generated named-pipe worker blocked until session
                    // expiry; the original request token must close it.
                    // Match the actual response cap: control, document,
                    // inventory, and error frames are bounded at 256 KiB;
                    // only exact geometry may use the 32 MiB cap.
                    int responseBytes = string.Equals(
                        method,
                        "export_exact_geometry",
                        StringComparison.Ordinal)
                        ? 32 * 1024 * 1024
                        : 256 * 1024;
                    byte[] response = new byte[responseBytes];
                    Task writer = WriteUntilCancelledAsync(
                        active,
                        response,
                        deadline.Token);
                    Thread.Sleep(50);
                    Assert(!writer.IsCompleted,
                        "The " + method + " " + responseKind +
                        " did not block before its deadline.");
                    RequireCompletesWithinDeadline(
                        writer,
                        stopwatch,
                        "The " + method + " " + responseKind +
                        " outlived its request deadline.");
                    Assert(deadline.Token.IsCancellationRequested && pipeClosed,
                        "The " + method + " " + responseKind +
                        " did not cancel and close its pipe.");
                }
            }
        }

        private static void AssertDelayedResponseStageExpires(
            string description,
            Func<CancellationToken, Task> delayedStage)
        {
            string name = PipeName();
            NamedPipeServerStream? active = CreateServer(name);
            bool pipeClosed = false;
            Stopwatch stopwatch = Stopwatch.StartNew();
            using (NativePipeBridgeServer.BridgeRequestDeadline deadline =
                NewRequestDeadline(ShortRequestDeadlineMilliseconds, long.MaxValue))
            using (CancellationTokenRegistration closeOnDeadline =
                deadline.Token.Register(
                    () =>
                    {
                        pipeClosed = true;
                        DisposeActivePipe(active);
                    }))
            {
                using (NamedPipeClientStream client = CreateClient(name))
                {
                    Task accepted = active.WaitForConnectionAsync(deadline.Token);
                    client.Connect(1000);
                    accepted.GetAwaiter().GetResult();

                    Task stage = delayedStage(deadline.Token);
                    RequireCompletesWithinDeadline(
                        stage,
                        stopwatch,
                        "Delayed " + description +
                        " outlived its request deadline.");
                    Assert(deadline.Token.IsCancellationRequested && pipeClosed,
                        "Delayed " + description +
                        " did not close the active named pipe.");
                }
            }
        }

        private static NativePipeBridgeServer.BridgeRequestDeadline
            NewRequestDeadline(
                int methodTimeoutMilliseconds,
                long sessionDeadlineTimestamp)
        {
            return new NativePipeBridgeServer.BridgeRequestDeadline(
                methodTimeoutMilliseconds,
                sessionDeadlineTimestamp,
                CancellationToken.None,
                CancellationToken.None);
        }

        private static void AssertMethodDeadline(
            string method,
            int expectedMilliseconds)
        {
            Assert(
                NativePipeBridgeServer.BridgeRequestDeadline
                    .MethodTimeoutMilliseconds(method) == expectedMilliseconds,
                "The " + method + " method timeout is not configured as expected.");
        }

        private static async Task DelayUntilCancelledAsync(
            CancellationToken cancellationToken)
        {
            try
            {
                await Task.Delay(Timeout.Infinite, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
        }

        private static async Task FlushUntilCancelledAsync(
            CancellationToken cancellationToken)
        {
            // A few host pipe implementations report a blocked FlushAsync
            // only when their handle is closed.  This models that pending
            // flush while retaining the same request token and pipe callback.
            await DelayUntilCancelledAsync(cancellationToken).ConfigureAwait(false);
        }

        private static long AddMilliseconds(long timestamp, int milliseconds)
        {
            long ticks = (long)Math.Ceiling(
                milliseconds * Stopwatch.Frequency / 1000d);
            return ticks > long.MaxValue - timestamp
                ? long.MaxValue
                : timestamp + ticks;
        }

        private static BridgeExpiryLifetime NewExpiry(Action onExpired)
        {
            return new BridgeExpiryLifetime(
                DateTime.UtcNow.AddMilliseconds(ShortExpiryMilliseconds),
                onExpired);
        }

        private static NamedPipeServerStream CreateServer(string name)
        {
            return new NamedPipeServerStream(
                name,
                PipeDirection.InOut,
                1,
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous,
                4096,
                4096);
        }

        private static NamedPipeClientStream CreateClient(string name)
        {
            return new NamedPipeClientStream(
                ".",
                name,
                PipeDirection.InOut,
                PipeOptions.Asynchronous);
        }

        private static string PipeName()
        {
            return "liang-pingfa-expiry-test-" +
                Guid.NewGuid().ToString("N");
        }

        private static async Task WaitForConnectionUntilCancelledAsync(
            NamedPipeServerStream pipe,
            CancellationToken cancellationToken)
        {
            try
            {
                await pipe.WaitForConnectionAsync(cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
            catch (ObjectDisposedException)
            {
            }
            catch (IOException)
            {
            }
        }

        private static async Task ReadExactlyUntilCancelledAsync(
            Stream stream,
            int length,
            CancellationToken cancellationToken)
        {
            byte[] buffer = new byte[length];
            int offset = 0;
            try
            {
                while (offset < buffer.Length)
                {
                    int read = await stream.ReadAsync(
                        buffer,
                        offset,
                        buffer.Length - offset,
                        cancellationToken).ConfigureAwait(false);
                    if (read == 0)
                    {
                        return;
                    }

                    offset += read;
                }
            }
            catch (OperationCanceledException)
            {
            }
            catch (ObjectDisposedException)
            {
            }
            catch (IOException)
            {
            }
        }

        private static async Task WriteUntilCancelledAsync(
            Stream stream,
            byte[] response,
            CancellationToken cancellationToken)
        {
            try
            {
                await stream.WriteAsync(
                    response,
                    0,
                    response.Length,
                    cancellationToken).ConfigureAwait(false);
                await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
            catch (ObjectDisposedException)
            {
            }
            catch (IOException)
            {
            }
        }

        private static async Task WaitForDispatchOrExpiryAsync(
            Task dispatch,
            CancellationToken cancellationToken)
        {
            Task winner = await Task.WhenAny(
                dispatch,
                Task.Delay(Timeout.Infinite, cancellationToken)).ConfigureAwait(false);
            Assert(!ReferenceEquals(winner, dispatch),
                "A stalled dispatch completed before expiry.");
        }

        private static async Task WaitForCancellationAsync(
            CancellationToken cancellationToken)
        {
            try
            {
                await Task.Delay(Timeout.Infinite, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
        }

        private static void DisposeActivePipe(NamedPipeServerStream? pipe)
        {
            if (pipe != null)
            {
                pipe.Dispose();
            }
        }

        private static void RequireCompletes(Task task, string message)
        {
            Assert(task.Wait(CompletionTimeoutMilliseconds), message);
            task.GetAwaiter().GetResult();
        }

        private static void RequireCompletesWithinDeadline(
            Task task,
            Stopwatch stopwatch,
            string message)
        {
            RequireCompletes(task, message);
            Assert(
                stopwatch.ElapsedMilliseconds <=
                    ShortRequestDeadlineMilliseconds + DeadlineSlackMilliseconds,
                message + " (elapsed " + stopwatch.ElapsedMilliseconds +
                "ms exceeded the short injected deadline bound.)");
        }

        private static void RequireEventually(
            Func<bool> condition,
            string message)
        {
            Assert(
                SpinWait.SpinUntil(condition, CompletionTimeoutMilliseconds),
                message);
        }

        private static void Assert(bool condition, string message)
        {
            if (!condition)
            {
                throw new InvalidOperationException(message);
            }
        }
    }
}
