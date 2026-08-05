// SPDX-License-Identifier: MIT
// Project-owned DTOs and wire helpers for active native v2 artifacts.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace LiangPingfa.NativeCad.Protocol
{
    /// <summary>Active names, bounds, and wire tokens used by the SDK-free core.</summary>
    public static class NativeCadProtocolV2
    {
        /// <summary>Canonical local bridge protocol namespace.</summary>
        public const string BridgeVersion = "liang-pingfa/native-bridge/v1";

        /// <summary>Exact active adapter configuration namespace.</summary>
        public const string AdapterConfigSchemaVersion =
            "liang-pingfa/native-adapter-config/v2";

        /// <summary>Exact active session descriptor namespace.</summary>
        public const string SessionSchemaVersion =
            "liang-pingfa/native-bridge-session/v2";

        /// <summary>Exact active geometry export namespace.</summary>
        public const string GeometrySchemaVersion =
            "liang-pingfa/native-geometry-export/v2";

        /// <summary>Exact active audit namespace.</summary>
        public const string AuditSchemaVersion =
            "liang-pingfa/native-audit/v2";

        /// <summary>Exact active intent namespace.</summary>
        public const string IntentSchemaVersion =
            "liang-pingfa/native-edit-intent/v2";

        /// <summary>Exact active plan namespace.</summary>
        public const string PlanSchemaVersion =
            "liang-pingfa/native-edit-plan/v2";

        /// <summary>Exact active manifest namespace.</summary>
        public const string ManifestSchemaVersion =
            "liang-pingfa/native-edit-manifest/v2";

        /// <summary>Exact active console result namespace.</summary>
        public const string ConsoleResultSchemaVersion =
            "liang-pingfa/native-console-result/v2";

        /// <summary>Exact active console export namespace.</summary>
        public const string ConsoleExportSchemaVersion =
            "liang-pingfa/native-console-export/v2";

        /// <summary>Exact active verification namespace.</summary>
        public const string VerificationSchemaVersion =
            "liang-pingfa/native-verification/v2";

        /// <summary>Maximum entities allowed by the frozen geometry semantics.</summary>
        public const int MaxGeometryEntities = 2000;

        /// <summary>Maximum aggregate simple segments allowed by frozen geometry semantics.</summary>
        public const int MaxGeometrySegments = 10000;

        /// <summary>Maximum raw UTF-8 bytes in an embedded geometry carrier.</summary>
        public const int MaxGeometryJsonBytes = 16 * 1024 * 1024;

        /// <summary>Maximum raw UTF-8 bytes in an embedded inventory carrier.</summary>
        public const int MaxInventoryJsonBytes = 64 * 1024;

        /// <summary>
        /// Fixed hard reader ceiling for one native-console-result/v2
        /// envelope. This remains aligned with the Python Core Console
        /// reader; do not increase it to accommodate a larger operation set.
        /// </summary>
        public const int MaxConsoleResultBytes = 256 * 1024;

        /// <summary>
        /// Reserved bytes below <see cref="MaxConsoleResultBytes"/> for
        /// framing and terminal handling. Manifest admission uses this
        /// smaller canonical-envelope ceiling.
        /// </summary>
        public const int ConsoleResultHeadroomBytes = 16 * 1024;

        /// <summary>Maximum canonical successful result bytes before framing.</summary>
        public const int MaxConsoleResultCanonicalBytes =
            MaxConsoleResultBytes - ConsoleResultHeadroomBytes;

        /// <summary>
        /// Shared v2 native operation limit. It supports the validated
        /// 623-operation scenario while retaining substantial result-headroom.
        /// </summary>
        public const int MaxNativeOperations = 1024;

        /// <summary>
        /// Maximum full private manifest/envelope bytes. It accommodates one
        /// exact 16 MiB carrier plus its canonical outer contract fields.
        /// </summary>
        public const int MaxManifestDocumentBytes = 32 * 1024 * 1024;

        /// <summary>Maximum absolute X/Y translation in the fixed profile.</summary>
        public const double MaxTranslation = 1000000d;

        /// <summary>Required fixed marker prefix.</summary>
        public const string MarkerTextPrefix = "LPF-REVIEW-";

        /// <summary>Fixed active private-record cardinality declaration.</summary>
        public const string PrivateRecordCardinality = "explicit_private";
    }

    /// <summary>Shared canonical result-budget predicate for adapters and core tests.</summary>
    public static class NativeConsoleResultBudgetV2
    {
        /// <summary>Returns whether a canonical result payload preserves framing headroom.</summary>
        public static bool FitsCanonicalPayloadBytes(long byteCount)
        {
            return byteCount >= 0 &&
                byteCount <= NativeCadProtocolV2.MaxConsoleResultCanonicalBytes;
        }
    }

    /// <summary>Wire-visible supported entity kinds.</summary>
    public enum NativeEntityKind
    {
        /// <summary>Single line text.</summary>
        DbText,

        /// <summary>One finite line segment.</summary>
        Line,

        /// <summary>Simple lightweight polyline.</summary>
        LwPolyline,

        /// <summary>Protected record without modeled semantic payload.</summary>
        Opaque,
    }

    /// <summary>Wire-visible container kinds.</summary>
    public enum NativeSpaceKind
    {
        /// <summary>Direct Modelspace only.</summary>
        Modelspace,

        /// <summary>Paperspace container.</summary>
        Paperspace,

        /// <summary>Nested block container.</summary>
        Block,
    }

    /// <summary>The three v1 operations and no arbitrary command escape hatch.</summary>
    public enum NativeOperationKind
    {
        /// <summary>Translate one direct Modelspace DBTEXT.</summary>
        TranslateDbText,

        /// <summary>Delete one eligible direct Modelspace overlay DBTEXT.</summary>
        DeleteAuxiliaryOverlayText,

        /// <summary>Append one policy-derived review DBTEXT marker.</summary>
        CreateReviewMarker,
    }

    /// <summary>Converts closed enums to their exact snake-case schema token.</summary>
    public static class NativeWireNames
    {
        /// <summary>Returns a v1 entity token.</summary>
        public static string EntityKind(NativeEntityKind kind)
        {
            switch (kind)
            {
                case NativeEntityKind.DbText:
                    return "DBTEXT";
                case NativeEntityKind.Line:
                    return "LINE";
                case NativeEntityKind.LwPolyline:
                    return "LWPOLYLINE";
                case NativeEntityKind.Opaque:
                    return "OPAQUE";
                default:
                    throw new CanonicalJsonException("Unknown native entity kind.");
            }
        }

        /// <summary>Returns a v1 space token.</summary>
        public static string SpaceKind(NativeSpaceKind kind)
        {
            switch (kind)
            {
                case NativeSpaceKind.Modelspace:
                    return "modelspace";
                case NativeSpaceKind.Paperspace:
                    return "paperspace";
                case NativeSpaceKind.Block:
                    return "block";
                default:
                    throw new CanonicalJsonException("Unknown native space kind.");
            }
        }

        /// <summary>Returns a v1 operation token.</summary>
        public static string OperationKind(NativeOperationKind kind)
        {
            switch (kind)
            {
                case NativeOperationKind.TranslateDbText:
                    return "translate_dbtext";
                case NativeOperationKind.DeleteAuxiliaryOverlayText:
                    return "delete_auxiliary_overlay_text";
                case NativeOperationKind.CreateReviewMarker:
                    return "create_review_marker";
                default:
                    throw new CanonicalJsonException("Unknown native operation kind.");
            }
        }
    }

    /// <summary>Validates uppercase canonical CAD handle spellings.</summary>
    public static class CadHandle
    {
        private static readonly Regex Pattern = new Regex(
            "^[0-9A-F]{1,16}$",
            RegexOptions.CultureInvariant);

        /// <summary>Rejects a noncanonical handle.</summary>
        public static void Require(string handle, string parameterName)
        {
            CanonicalJson.RequireNfcString(handle, parameterName);
            if (!Pattern.IsMatch(handle))
            {
                throw new CanonicalJsonException("CAD handle must be uppercase hexadecimal.");
            }
        }
    }

    /// <summary>Exact finite binary64 bit-string helpers used for all geometry scalars.</summary>
    public static class Binary64
    {
        /// <summary>Returns a finite canonical scalar from sixteen lowercase hexadecimal bits.</summary>
        public static double ParseBits(string bits)
        {
            CanonicalJson.RequireNfcString(bits, nameof(bits));
            if (bits.Length != 16)
            {
                throw new CanonicalJsonException("binary64 bit string has an invalid length.");
            }

            for (int index = 0; index < bits.Length; index++)
            {
                char character = bits[index];
                if (!((character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f')))
                {
                    throw new CanonicalJsonException("binary64 bit string must be lowercase hexadecimal.");
                }
            }

            ulong raw;
            if (!ulong.TryParse(
                bits,
                NumberStyles.AllowHexSpecifier,
                CultureInfo.InvariantCulture,
                out raw))
            {
                throw new CanonicalJsonException("binary64 bit string is invalid.");
            }

            double value = BitConverter.Int64BitsToDouble(unchecked((long)raw));
            if (double.IsNaN(value) || double.IsInfinity(value))
            {
                throw new CanonicalJsonException("binary64 scalar is non-finite.");
            }

            if (value == 0d && !string.Equals(bits, "0000000000000000", StringComparison.Ordinal))
            {
                throw new CanonicalJsonException("binary64 zero must use the positive canonical bit pattern.");
            }

            return value;
        }

        /// <summary>Returns canonical lowercase bits for one finite scalar.</summary>
        public static string ToBits(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value))
            {
                throw new CanonicalJsonException("binary64 scalar is non-finite.");
            }

            if (value == 0d)
            {
                return "0000000000000000";
            }

            ulong raw = unchecked((ulong)BitConverter.DoubleToInt64Bits(value));
            return raw.ToString("x16", CultureInfo.InvariantCulture);
        }

        /// <summary>
        /// Adds a finite scalar while preserving a zero-axis bit pattern and
        /// rejecting rounded nonzero no-ops or overflow.
        /// </summary>
        public static string Translate(string originalBits, string deltaBits)
        {
            double original = ParseBits(originalBits);
            double delta = ParseBits(deltaBits);
            if (delta == 0d)
            {
                return originalBits;
            }

            double translated = original + delta;
            if (double.IsNaN(translated) || double.IsInfinity(translated))
            {
                throw new CanonicalJsonException("binary64 translation is non-finite.");
            }

            string translatedBits = ToBits(translated);
            if (string.Equals(translatedBits, originalBits, StringComparison.Ordinal))
            {
                throw new CanonicalJsonException("nonzero binary64 translation is not representable.");
            }

            return translatedBits;
        }
    }

    /// <summary>Immutable x/y/z binary64 bit vector.</summary>
    public sealed class Binary64Vector
    {
        /// <summary>Creates one finite canonical vector.</summary>
        public Binary64Vector(string x, string y, string z)
        {
            Binary64.ParseBits(x);
            Binary64.ParseBits(y);
            Binary64.ParseBits(z);
            X = x;
            Y = y;
            Z = z;
        }

        /// <summary>X bit string.</summary>
        public string X { get; private set; }

        /// <summary>Y bit string.</summary>
        public string Y { get; private set; }

        /// <summary>Z bit string.</summary>
        public string Z { get; private set; }

        /// <summary>Returns the exact schema vector array.</summary>
        public List<object> ToWireValue()
        {
            return new List<object> { X, Y, Z };
        }

        /// <summary>Returns a bit-preserving vector translated by the supplied delta.</summary>
        public Binary64Vector Translate(Binary64Vector delta)
        {
            if (delta == null)
            {
                throw new ArgumentNullException(nameof(delta));
            }

            return new Binary64Vector(
                Binary64.Translate(X, delta.X),
                Binary64.Translate(Y, delta.Y),
                Binary64.Translate(Z, delta.Z));
        }

        /// <inheritdoc />
        public override bool Equals(object? other)
        {
            Binary64Vector? candidate = other as Binary64Vector;
            return candidate != null &&
                string.Equals(X, candidate.X, StringComparison.Ordinal) &&
                string.Equals(Y, candidate.Y, StringComparison.Ordinal) &&
                string.Equals(Z, candidate.Z, StringComparison.Ordinal);
        }

        /// <inheritdoc />
        public override int GetHashCode()
        {
            return StringComparer.Ordinal.GetHashCode(X) ^
                StringComparer.Ordinal.GetHashCode(Y) ^
                StringComparer.Ordinal.GetHashCode(Z);
        }
    }

    /// <summary>Immutable exact bounding box.</summary>
    public sealed class CadBounds
    {
        /// <summary>Creates ordered finite extents.</summary>
        public CadBounds(Binary64Vector minimum, Binary64Vector maximum)
        {
            if (minimum == null)
            {
                throw new ArgumentNullException(nameof(minimum));
            }

            if (maximum == null)
            {
                throw new ArgumentNullException(nameof(maximum));
            }

            if (Binary64.ParseBits(minimum.X) > Binary64.ParseBits(maximum.X) ||
                Binary64.ParseBits(minimum.Y) > Binary64.ParseBits(maximum.Y) ||
                Binary64.ParseBits(minimum.Z) > Binary64.ParseBits(maximum.Z))
            {
                throw new CanonicalJsonException("CAD bounds are inverted.");
            }

            Minimum = minimum;
            Maximum = maximum;
        }

        /// <summary>Minimum corner.</summary>
        public Binary64Vector Minimum { get; private set; }

        /// <summary>Maximum corner.</summary>
        public Binary64Vector Maximum { get; private set; }

        /// <summary>Returns the exact schema object.</summary>
        public Dictionary<string, object> ToWireValue()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "minimum", Minimum.ToWireValue() },
                { "maximum", Maximum.ToWireValue() },
            };
        }

        /// <summary>Returns bounds translated by the exact vector.</summary>
        public CadBounds Translate(Binary64Vector delta)
        {
            return new CadBounds(Minimum.Translate(delta), Maximum.Translate(delta));
        }
    }

    /// <summary>Immutable segment used for LINE and simple LWPOLYLINE exports.</summary>
    public sealed class CadSegment
    {
        /// <summary>Creates one finite segment.</summary>
        public CadSegment(Binary64Vector start, Binary64Vector end)
        {
            Start = start ?? throw new ArgumentNullException(nameof(start));
            End = end ?? throw new ArgumentNullException(nameof(end));
        }

        /// <summary>Start vector.</summary>
        public Binary64Vector Start { get; private set; }

        /// <summary>End vector.</summary>
        public Binary64Vector End { get; private set; }

        /// <summary>Returns the exact schema object.</summary>
        public Dictionary<string, object> ToWireValue()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "start", Start.ToWireValue() },
                { "end", End.ToWireValue() },
            };
        }

        /// <summary>Returns a bit-exact translated segment.</summary>
        public CadSegment Translate(Binary64Vector delta)
        {
            return new CadSegment(Start.Translate(delta), End.Translate(delta));
        }
    }

    /// <summary>Immutable overlay eligibility evidence exactly mirrored in v1 geometry.</summary>
    public sealed class OverlayEvidence
    {
        /// <summary>Creates one evidence tuple.</summary>
        public OverlayEvidence(
            bool uniqueContent,
            bool leftPanel,
            bool correspondingRightAbsent,
            bool visibleInterference,
            bool unsupportedData)
        {
            UniqueContent = uniqueContent;
            LeftPanel = leftPanel;
            CorrespondingRightAbsent = correspondingRightAbsent;
            VisibleInterference = visibleInterference;
            UnsupportedData = unsupportedData;
        }

        /// <summary>Whether content is audited as unique.</summary>
        public bool UniqueContent { get; private set; }

        /// <summary>Whether it belongs to the left panel.</summary>
        public bool LeftPanel { get; private set; }

        /// <summary>Whether its corresponding right item is absent.</summary>
        public bool CorrespondingRightAbsent { get; private set; }

        /// <summary>Whether the overlay visibly interferes.</summary>
        public bool VisibleInterference { get; private set; }

        /// <summary>Whether unsupported data blocks eligibility.</summary>
        public bool UnsupportedData { get; private set; }

        /// <summary>Returns whether an exact delete is permitted by v1 evidence.</summary>
        public bool IsEligibleOverlay()
        {
            return UniqueContent &&
                LeftPanel &&
                CorrespondingRightAbsent &&
                VisibleInterference &&
                !UnsupportedData;
        }

        /// <summary>Returns the exact schema object.</summary>
        public Dictionary<string, object> ToWireValue()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "unique_content", UniqueContent },
                { "left_panel", LeftPanel },
                { "corresponding_right_absent", CorrespondingRightAbsent },
                { "visible_interference", VisibleInterference },
                { "unsupported_data", UnsupportedData },
            };
        }

        /// <inheritdoc />
        public override bool Equals(object? other)
        {
            OverlayEvidence? candidate = other as OverlayEvidence;
            return candidate != null &&
                UniqueContent == candidate.UniqueContent &&
                LeftPanel == candidate.LeftPanel &&
                CorrespondingRightAbsent == candidate.CorrespondingRightAbsent &&
                VisibleInterference == candidate.VisibleInterference &&
                UnsupportedData == candidate.UnsupportedData;
        }

        /// <inheritdoc />
        public override int GetHashCode()
        {
            return (UniqueContent ? 1 : 0) |
                (LeftPanel ? 2 : 0) |
                (CorrespondingRightAbsent ? 4 : 0) |
                (VisibleInterference ? 8 : 0) |
                (UnsupportedData ? 16 : 0);
        }
    }
}
