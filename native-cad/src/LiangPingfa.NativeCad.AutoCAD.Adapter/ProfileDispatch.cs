// SPDX-License-Identifier: MIT
// Profile boundary for APIs whose Autodesk binary/runtime contracts must not
// be guessed or silently substituted across host generations.

using System;
using System.Threading.Tasks;
using Autodesk.AutoCAD.ApplicationServices;

namespace LiangPingfa.NativeCad.AutoCAD.Adapter
{
    internal static class AutodeskCommandContextDispatcher
    {
        // Syntax-only tests install a queueing dispatcher to prove that pipe
        // work can run after a CAD command returns.  Production builds leave
        // this null and use the reviewed host profile branch below.
        private static readonly object TestDispatcherGate = new object();
        private static Action<Func<object, Task>, object>? testDispatcher;

        internal static void Execute(
            Func<object, Task> callback,
            object state)
        {
            Action<Func<object, Task>, object>? test;
            lock (TestDispatcherGate)
            {
                test = testDispatcher;
            }

            if (test != null)
            {
                test(callback, state);
                return;
            }

#if LPF_AUTOCAD_2024
            // AutoCAD 2024's documented managed dispatcher is retained here
            // as an explicit profile branch rather than a fallback.
            Application.DocumentManager.ExecuteInCommandContextAsync(
                callback,
                state);
#elif LPF_AUTOCAD_2025
            Application.DocumentManager.ExecuteInCommandContextAsync(
                callback,
                state);
#elif LPF_AUTOCAD_2026
            Application.DocumentManager.ExecuteInCommandContextAsync(
                callback,
                state);
#else
#error An explicit reviewed AutoCAD profile is required.
#endif
        }

        /// <summary>
        /// Installs a test-only dispatcher without changing production host
        /// dispatch semantics.  The returned scope always restores the prior
        /// value, even when a queued callback fails.
        /// </summary>
        internal static IDisposable UseTestDispatcher(
            Action<Func<object, Task>, object> dispatcher)
        {
            if (dispatcher == null)
            {
                throw new ArgumentNullException(nameof(dispatcher));
            }

            lock (TestDispatcherGate)
            {
                Action<Func<object, Task>, object>? previous = testDispatcher;
                testDispatcher = dispatcher;
                return new TestDispatcherScope(previous);
            }
        }

        private sealed class TestDispatcherScope : IDisposable
        {
            private readonly Action<Func<object, Task>, object>? previous;
            private bool disposed;

            internal TestDispatcherScope(
                Action<Func<object, Task>, object>? previous)
            {
                this.previous = previous;
            }

            public void Dispose()
            {
                if (disposed)
                {
                    return;
                }

                lock (TestDispatcherGate)
                {
                    testDispatcher = previous;
                }

                disposed = true;
            }
        }
    }
}
