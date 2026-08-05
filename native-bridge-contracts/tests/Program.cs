// SPDX-License-Identifier: MIT
// Reflection snapshots for frozen v1 and active v2 public contract surfaces.

using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using LiangPingfa.NativeBridge.Contracts;

static class Program
{
    private static int Main()
    {
        try
        {
            AssertEqual(
                "NativeSourceBindingV1(System.String Sha256,System.Int64 ByteSize,System.String PathFingerprint,System.String FileIdentityFingerprint)|Sha256:System.String|ByteSize:System.Int64|PathFingerprint:System.String|FileIdentityFingerprint:System.String",
                Snapshot(typeof(NativeSourceBindingV1)));
            AssertEqual(
                "NativeSourceBindingV2(System.String Format,System.String Sha256,System.Int64 ByteSize,System.String PathFingerprint,System.String FileIdentityFingerprint,System.String DwgHeaderSignature)|Format:System.String|Sha256:System.String|ByteSize:System.Int64|PathFingerprint:System.String|FileIdentityFingerprint:System.String|DwgHeaderSignature:System.String",
                Snapshot(typeof(NativeSourceBindingV2)));
            AssertEqual(
                "ExecuteManifestAsync(LiangPingfa.NativeBridge.Contracts.NativeManifestExecutionRequestV1,System.Threading.CancellationToken):System.Threading.Tasks.ValueTask`1[[LiangPingfa.NativeBridge.Contracts.NativeManifestExecutionResultV1, LiangPingfa.NativeBridge.Contracts, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null]]",
                InterfaceSnapshot(typeof(IManifestExecutorV1)));
            AssertEqual(
                "ExecuteManifestAsync(LiangPingfa.NativeBridge.Contracts.NativeManifestExecutionRequestV2,System.Threading.CancellationToken):System.Threading.Tasks.ValueTask`1[[LiangPingfa.NativeBridge.Contracts.NativeManifestExecutionResultV2, LiangPingfa.NativeBridge.Contracts, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null]]",
                InterfaceSnapshot(typeof(IManifestExecutorV2)));
            AssertEqual(
                "ExportReadbackAsync(LiangPingfa.NativeBridge.Contracts.NativeReadbackRequestV1,System.Threading.CancellationToken):System.Threading.Tasks.ValueTask`1[[LiangPingfa.NativeBridge.Contracts.NativePrivateGeometryExportV1, LiangPingfa.NativeBridge.Contracts, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null]]",
                InterfaceSnapshot(typeof(IReadbackExporterV1)));
            AssertEqual(
                "ExportReadbackAsync(LiangPingfa.NativeBridge.Contracts.NativeReadbackRequestV2,System.Threading.CancellationToken):System.Threading.Tasks.ValueTask`1[[LiangPingfa.NativeBridge.Contracts.NativeConsoleExportV2, LiangPingfa.NativeBridge.Contracts, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null]]",
                InterfaceSnapshot(typeof(IReadbackExporterV2)));
            AssertEqual(
                string.Concat(
                    "d2c375a5439439c756fe84deb48378f",
                    "8e9ff66cb6274716318fa408ff2df8e53"),
                PublicSurfaceHash(version: 1));
            AssertEqual(
                string.Concat(
                    "1ff224ac1fb08f2927c24c646d25642c",
                    "60af98eff35d260aa2f383ecf0e443ea"),
                PublicSurfaceHash(version: 2));

            Console.WriteLine("PASS: v1/v2 public API reflection snapshots match.");
            return 0;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception.Message);
            return 1;
        }
    }

    private static string Snapshot(Type type)
    {
        ConstructorInfo constructor = type.GetConstructors(
            BindingFlags.Public | BindingFlags.Instance).Single();
        string parameters = string.Join(
            ",",
            constructor.GetParameters().Select(parameter =>
                parameter.ParameterType.FullName + " " + parameter.Name));
        string properties = string.Join(
            "|",
            type.GetProperties(BindingFlags.Public | BindingFlags.Instance)
                .OrderBy(property => property.MetadataToken)
                .Select(property => property.Name + ":" + property.PropertyType.FullName));
        return type.Name + "(" + parameters + ")|" + properties;
    }

    private static string InterfaceSnapshot(Type type)
    {
        return string.Join(
            "|",
            type.GetMethods()
                .OrderBy(method => method.MetadataToken)
                .Select(method =>
                    method.Name + "(" + string.Join(
                        ",",
                        method.GetParameters().Select(parameter =>
                            parameter.ParameterType.FullName)) + "):" +
                    (method.ReturnType.FullName ?? method.ReturnType.Name)));
    }

    private static void AssertEqual(string expected, string actual)
    {
        if (!string.Equals(expected, actual, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                "Public API surface snapshot drifted.\nExpected: " + expected +
                "\nActual:   " + actual);
        }
    }

    private static string PublicSurfaceHash(int version)
    {
        Assembly assembly = typeof(NativeBridgeProtocolV1).Assembly;
        string suffix = "V" + version.ToString();
        string surface = string.Join(
            "\n",
            assembly.GetExportedTypes()
                .Where(type => type.Name.EndsWith(suffix, StringComparison.Ordinal))
                .OrderBy(type => type.FullName, StringComparer.Ordinal)
                .Select(TypeSurface));
        byte[] hash = SHA256.HashData(Encoding.UTF8.GetBytes(surface));
        return Convert.ToHexString(hash).ToLowerInvariant();
    }

    private static string TypeSurface(Type type)
    {
        string constructors = string.Join(
            ",",
            type.GetConstructors(BindingFlags.Public | BindingFlags.Instance)
                .OrderBy(constructor => constructor.MetadataToken)
                .Select(constructor => "(" + string.Join(
                    ",",
                    constructor.GetParameters().Select(parameter =>
                        parameter.ParameterType.FullName + " " + parameter.Name)) + ")"));
        string properties = string.Join(
            ",",
            type.GetProperties(BindingFlags.Public | BindingFlags.Instance |
                               BindingFlags.Static)
                .OrderBy(property => property.MetadataToken)
                .Select(property => property.Name + ":" +
                    (property.PropertyType.FullName ?? property.PropertyType.Name)));
        string fields = string.Join(
            ",",
            type.GetFields(BindingFlags.Public | BindingFlags.Static |
                           BindingFlags.Instance)
                .OrderBy(field => field.MetadataToken)
                .Select(field => field.Name + ":" +
                    (field.FieldType.FullName ?? field.FieldType.Name)));
        string methods = string.Join(
            ",",
            type.GetMethods(BindingFlags.Public | BindingFlags.Instance |
                            BindingFlags.Static | BindingFlags.DeclaredOnly)
                .OrderBy(method => method.MetadataToken)
                .Select(method => method.Name + "(" + string.Join(
                    ",",
                    method.GetParameters().Select(parameter =>
                        parameter.ParameterType.FullName)) + "):" +
                    (method.ReturnType.FullName ?? method.ReturnType.Name)));
        return (type.FullName ?? type.Name) + "|" + constructors + "|" +
            properties + "|" + fields + "|" + methods;
    }
}
