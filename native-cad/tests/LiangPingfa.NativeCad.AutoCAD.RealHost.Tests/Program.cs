// SPDX-License-Identifier: MIT
// Optional licensed-host gate. Public CI never supplies these variables.

using System;
using System.IO;

namespace LiangPingfa.NativeCad.AutoCAD.RealHost.Tests
{
    internal static class Program
    {
        // Initial real-host qualification requires translation evidence only.
        // Delete remains forbidden until a versioned post-SaveAs compaction
        // contract and matching licensed-host evidence are separately added.
        private const string InitialRequiredOperation = "translate_dbtext/v1";

        private static int Main()
        {
            if (!string.Equals(
                    Environment.GetEnvironmentVariable("LPF_REALHOST_TESTS"),
                    "1",
                    StringComparison.Ordinal))
            {
                Console.WriteLine("SKIP: licensed AutoCAD/Core Console evidence was not supplied.");
                return 0;
            }

            string? fixture = Environment.GetEnvironmentVariable(
                "LPF_REALHOST_PRIVATE_FIXTURE");
            if (string.IsNullOrEmpty(fixture) ||
                !File.Exists(fixture) ||
                !Path.GetFileName(fixture).StartsWith(
                    "liang-pingfa-realhost-",
                    StringComparison.OrdinalIgnoreCase) ||
                !fixture.EndsWith(".dwg", StringComparison.OrdinalIgnoreCase))
            {
                Console.Error.WriteLine(
                    "FAIL: real-host runs require an operator-generated private fixture.");
                return 1;
            }

            // A licensed operator invokes the configured Core Console through
            // the same fixed Python launcher. This gate deliberately does not
            // discover hosts, load drawings, or treat SDK compilation as
            // runtime qualification.
            Console.WriteLine(
                "SKIP: fixture gate passed; run the private operator harness for " +
                InitialRequiredOperation + " evidence.");
            return 0;
        }
    }
}
