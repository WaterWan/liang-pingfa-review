// SPDX-License-Identifier: MIT
// Active v2 executor interfaces. Published v1 interfaces remain untouched.

using System.Threading;
using System.Threading.Tasks;

namespace LiangPingfa.NativeBridge.Contracts;

/// <summary>External fixed-command executor for active v2 manifests only.</summary>
public interface IManifestExecutorV2
{
    ValueTask<NativeManifestExecutionResultV2> ExecuteManifestAsync(
        NativeManifestExecutionRequestV2 request,
        CancellationToken cancellationToken);
}

/// <summary>Separate v2 readback exporter bound to one accepted v2 result.</summary>
public interface IReadbackExporterV2
{
    ValueTask<NativeConsoleExportV2> ExportReadbackAsync(
        NativeReadbackRequestV2 request,
        CancellationToken cancellationToken);
}
