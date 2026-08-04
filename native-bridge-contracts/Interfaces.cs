// SPDX-License-Identifier: MIT
// Interfaces only: implementations remain separately installed and licensed.

using System.Threading;
using System.Threading.Tasks;

namespace LiangPingfa.NativeBridge.Contracts;

/// <summary>Strictly read-only bridge surface; it exposes no mutation RPC.</summary>
public interface IReadOnlyNativeBridgeV1
{
    ValueTask<NativeHealthResponseV1> HealthAsync(
        NativeSessionOnlyParametersV1 parameters,
        CancellationToken cancellationToken);

    ValueTask<NativeSessionHandshakeResponseV1> GetSessionAsync(
        NativeSessionHandshakeParametersV1 parameters,
        CancellationToken cancellationToken);

    ValueTask<NativeCurrentDocumentResponseV1> GetCurrentDocumentAsync(
        NativeSessionOnlyParametersV1 parameters,
        CancellationToken cancellationToken);

    ValueTask<NativeInventoryResponseV1> ExportInventoryAsync(
        NativeDocumentBoundParametersV1 parameters,
        CancellationToken cancellationToken);

    ValueTask<NativeExactGeometryResponseV1> ExportExactGeometryAsync(
        NativeDocumentBoundParametersV1 parameters,
        CancellationToken cancellationToken);
}

/// <summary>External fixed-command manifest executor contract, not an implementation.</summary>
public interface IManifestExecutorV1
{
    ValueTask<NativeManifestExecutionResultV1> ExecuteManifestAsync(
        NativeManifestExecutionRequestV1 request,
        CancellationToken cancellationToken);
}

/// <summary>External fixed-command post-save export contract, not an implementation.</summary>
public interface IReadbackExporterV1
{
    ValueTask<NativePrivateGeometryExportV1> ExportReadbackAsync(
        NativeReadbackRequestV1 request,
        CancellationToken cancellationToken);
}
