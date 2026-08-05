// SPDX-License-Identifier: MIT
// Exact-path canonical JSON options for the frozen native v1 wire contracts.

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;

namespace LiangPingfa.NativeCad.Protocol
{
    /// <summary>
    /// An immutable absolute JSON path. A segment is either an object key or
    /// a nonnegative array index; field names alone never select a policy.
    /// </summary>
    public sealed class CanonicalJsonPath : IEquatable<CanonicalJsonPath>
    {
        private readonly object[] segments;
        private readonly int hashCode;

        /// <summary>Creates one root-relative canonical JSON path.</summary>
        public CanonicalJsonPath(params object[] pathSegments)
        {
            if (pathSegments == null)
            {
                throw new ArgumentNullException(nameof(pathSegments));
            }

            segments = new object[pathSegments.Length];
            int hash = 17;
            for (int index = 0; index < pathSegments.Length; index++)
            {
                object segment = pathSegments[index] ??
                    throw new CanonicalJsonException("Canonical JSON path contains a null segment.");
                string? key = segment as string;
                if (key != null)
                {
                    CanonicalJson.RequireNfcString(key, nameof(pathSegments));
                    segments[index] = key;
                    hash = (hash * 31) + StringComparer.Ordinal.GetHashCode(key);
                    continue;
                }

                if (segment is int)
                {
                    int arrayIndex = (int)segment;
                    if (arrayIndex < 0)
                    {
                        throw new CanonicalJsonException("Canonical JSON path has a negative array index.");
                    }

                    segments[index] = arrayIndex;
                    hash = (hash * 31) + arrayIndex;
                    continue;
                }

                throw new CanonicalJsonException(
                    "Canonical JSON path segments must be strings or nonnegative Int32 indexes.");
            }

            hashCode = hash;
            Segments = new ReadOnlyCollection<object>(segments);
        }

        /// <summary>Root-relative key/index segments.</summary>
        public IReadOnlyList<object> Segments { get; private set; }

        /// <summary>Returns a child object-key path.</summary>
        public CanonicalJsonPath Append(string key)
        {
            if (key == null)
            {
                throw new ArgumentNullException(nameof(key));
            }

            object[] child = new object[segments.Length + 1];
            Array.Copy(segments, child, segments.Length);
            child[segments.Length] = key;
            return new CanonicalJsonPath(child);
        }

        /// <summary>Returns a child array-index path.</summary>
        public CanonicalJsonPath Append(int index)
        {
            object[] child = new object[segments.Length + 1];
            Array.Copy(segments, child, segments.Length);
            child[segments.Length] = index;
            return new CanonicalJsonPath(child);
        }

        /// <inheritdoc />
        public bool Equals(CanonicalJsonPath? other)
        {
            if (other == null || segments.Length != other.segments.Length)
            {
                return false;
            }

            for (int index = 0; index < segments.Length; index++)
            {
                object left = segments[index];
                object right = other.segments[index];
                string? leftKey = left as string;
                if (leftKey != null)
                {
                    if (!string.Equals(leftKey, right as string, StringComparison.Ordinal))
                    {
                        return false;
                    }
                }
                else if (!(right is int) || (int)left != (int)right)
                {
                    return false;
                }
            }

            return true;
        }

        /// <inheritdoc />
        public override bool Equals(object? other)
        {
            return Equals(other as CanonicalJsonPath);
        }

        /// <inheritdoc />
        public override int GetHashCode()
        {
            return hashCode;
        }
    }

    /// <summary>One exact-path exemption from ordinary outer-string NFC handling.</summary>
    public sealed class CanonicalJsonOpaqueStringRule
    {
        /// <summary>Creates an immutable opaque-carrier byte-bound rule.</summary>
        public CanonicalJsonOpaqueStringRule(
            CanonicalJsonPath path,
            int maximumUtf8Bytes)
        {
            Path = path ?? throw new ArgumentNullException(nameof(path));
            if (maximumUtf8Bytes < 0)
            {
                throw new ArgumentOutOfRangeException(nameof(maximumUtf8Bytes));
            }

            MaximumUtf8Bytes = maximumUtf8Bytes;
        }

        /// <summary>The only path at which this exemption applies.</summary>
        public CanonicalJsonPath Path { get; private set; }

        /// <summary>Maximum raw carrier UTF-8 byte count.</summary>
        public int MaximumUtf8Bytes { get; private set; }
    }

    /// <summary>
    /// Immutable path-aware canonicalization policy. All paths not named by a
    /// rule remain ordinary bounded NFC strings.
    /// </summary>
    public sealed class CanonicalJsonOptions
    {
        private readonly IReadOnlyDictionary<CanonicalJsonPath, int> opaqueStringRules;

        /// <summary>Creates an options instance from exact opaque-carrier paths.</summary>
        public CanonicalJsonOptions(IEnumerable<CanonicalJsonOpaqueStringRule>? rules = null)
        {
            Dictionary<CanonicalJsonPath, int> copied =
                new Dictionary<CanonicalJsonPath, int>();
            if (rules != null)
            {
                foreach (CanonicalJsonOpaqueStringRule rule in rules)
                {
                    if (rule == null)
                    {
                        throw new CanonicalJsonException("Canonical JSON opaque rule may not be null.");
                    }

                    if (copied.ContainsKey(rule.Path))
                    {
                        throw new CanonicalJsonException(
                            "Canonical JSON opaque rules contain a duplicate path.");
                    }

                    copied.Add(rule.Path, rule.MaximumUtf8Bytes);
                }
            }

            opaqueStringRules =
                new ReadOnlyDictionary<CanonicalJsonPath, int>(copied);
        }

        /// <summary>Strict canonical JSON without opaque carrier exemptions.</summary>
        public static CanonicalJsonOptions Strict { get; } = new CanonicalJsonOptions();

        /// <summary>Configured exact-path opaque-carrier rules.</summary>
        public IReadOnlyDictionary<CanonicalJsonPath, int> OpaqueStringRules
        {
            get { return opaqueStringRules; }
        }

        internal bool TryGetOpaqueStringMaximum(
            CanonicalJsonPath path,
            out int maximumUtf8Bytes)
        {
            return opaqueStringRules.TryGetValue(path, out maximumUtf8Bytes);
        }
    }

    /// <summary>
    /// Frozen native-v1 carrier profiles. These are deliberately schema
    /// context profiles, not name-based global exemptions.
    /// </summary>
    public static class NativeCadCanonicalJsonProfiles
    {
        /// <summary>Read-only bridge response carriers.</summary>
        public static CanonicalJsonOptions BridgeResponse { get; } =
            new CanonicalJsonOptions(
                new[]
                {
                    new CanonicalJsonOpaqueStringRule(
                        new CanonicalJsonPath("result", "geometry_json"),
                        NativeCadProtocolV2.MaxGeometryJsonBytes),
                    new CanonicalJsonOpaqueStringRule(
                        new CanonicalJsonPath("result", "inventory_json"),
                        NativeCadProtocolV2.MaxInventoryJsonBytes),
                });

        /// <summary>One private v1 manifest precondition carrier.</summary>
        public static CanonicalJsonOptions Manifest { get; } =
            new CanonicalJsonOptions(
                new[]
                {
                    new CanonicalJsonOpaqueStringRule(
                        new CanonicalJsonPath("preconditions_geometry_json"),
                        NativeCadProtocolV2.MaxGeometryJsonBytes),
                });

        /// <summary>One post-save v1 console-export geometry carrier.</summary>
        public static CanonicalJsonOptions ConsoleExport { get; } =
            new CanonicalJsonOptions(
                new[]
                {
                    new CanonicalJsonOpaqueStringRule(
                        new CanonicalJsonPath("geometry_json"),
                        NativeCadProtocolV2.MaxGeometryJsonBytes),
                });
    }
}
