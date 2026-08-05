// SPDX-License-Identifier: MIT
// Project-owned strict JSON implementation for the SDK-free v1 subset.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;

namespace LiangPingfa.NativeCad.Protocol
{
    /// <summary>Raised when input is outside the bounded canonical JSON profile.</summary>
    public sealed class CanonicalJsonException : ArgumentException
    {
        /// <summary>Creates a canonical JSON validation exception.</summary>
        public CanonicalJsonException(string message)
            : base(message)
        {
        }
    }

    /// <summary>
    /// Strict UTF-8 JSON canonicalization shared by generated protocol fixtures
    /// and the transaction core.
    ///
    /// The frozen C# profile is deliberately smaller than Python's general
    /// canonical serializer: null, Boolean values, NFC Unicode strings,
    /// bounded integers, arrays, and objects only. Accepted integers are
    /// Int64 values or nonnegative UInt64 values, emitted in their ordinary
    /// invariant decimal spelling. Decimal, Single, Double, fractional,
    /// exponent, and negative-zero JSON numbers are rejected rather than
    /// silently choosing a spelling that might diverge from Python.
    ///
    /// Geometry binary64 values are protocol strings containing canonical
    /// lowercase bit patterns; they are never JSON floating-point numbers.
    /// Object keys sort by Unicode scalar/code-point order, matching Python
    /// ``sort_keys=True`` rather than UTF-16 ordinal order. The profile rejects
    /// duplicate keys, non-NFC strings, cyclic graphs, invalid Unicode, and
    /// nesting beyond the frozen v1 cap.
    /// </summary>
    public static class CanonicalJson
    {
        /// <summary>Frozen v1 maximum JSON array/object nesting depth.</summary>
        public const int MaxNestingDepth = 128;

        /// <summary>Lowest accepted canonical JSON integer.</summary>
        public const long MinimumInteger = long.MinValue;

        /// <summary>Highest accepted canonical JSON integer.</summary>
        public const ulong MaximumInteger = ulong.MaxValue;

        /// <summary>Maximum ordinary scalar length before NFC validation.</summary>
        public const int MaxStringCodePoints = 64 * 1024;

        /// <summary>Maximum ordinary scalar UTF-8 byte length.</summary>
        public const int MaxStringUtf8Bytes = 64 * 1024;

        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);

        /// <summary>Returns canonical UTF-8 bytes with sorted object keys.</summary>
        public static byte[] SerializeUtf8(object? value)
        {
            return SerializeUtf8(value, CanonicalJsonOptions.Strict);
        }

        /// <summary>
        /// Returns canonical UTF-8 bytes using an explicit exact-path carrier
        /// policy. All omitted paths retain ordinary NFC and 64 KiB limits.
        /// </summary>
        public static byte[] SerializeUtf8(object? value, CanonicalJsonOptions? options)
        {
            return StrictUtf8.GetBytes(Serialize(value, options));
        }

        /// <summary>Returns canonical JSON text with no insignificant whitespace.</summary>
        public static string Serialize(object? value)
        {
            return Serialize(value, CanonicalJsonOptions.Strict);
        }

        /// <summary>
        /// Returns canonical JSON text with no insignificant whitespace using
        /// an explicit exact-path carrier policy.
        /// </summary>
        public static string Serialize(object? value, CanonicalJsonOptions? options)
        {
            StringBuilder builder = new StringBuilder();
            HashSet<object> active = new HashSet<object>(ReferenceComparer.Instance);
            WriteValue(
                value,
                builder,
                0,
                active,
                options ?? CanonicalJsonOptions.Strict,
                new CanonicalJsonPath());
            return builder.ToString();
        }

        /// <summary>Hashes canonical UTF-8 JSON with lowercase SHA-256 hex.</summary>
        public static string Sha256Hex(object? value)
        {
            return Sha256Hex(value, CanonicalJsonOptions.Strict);
        }

        /// <summary>
        /// Hashes canonical UTF-8 JSON using an explicit exact-path carrier
        /// policy. Opaque carrier code points are included unchanged.
        /// </summary>
        public static string Sha256Hex(object? value, CanonicalJsonOptions? options)
        {
            return Sha256Hex(SerializeUtf8(value, options));
        }

        /// <summary>Hashes exact bytes with lowercase SHA-256 hex.</summary>
        public static string Sha256Hex(byte[] bytes)
        {
            if (bytes == null)
            {
                throw new ArgumentNullException(nameof(bytes));
            }

            using (SHA256 algorithm = SHA256.Create())
            {
                byte[] digest = algorithm.ComputeHash(bytes);
                StringBuilder result = new StringBuilder(digest.Length * 2);
                for (int index = 0; index < digest.Length; index++)
                {
                    result.Append(digest[index].ToString("x2", CultureInfo.InvariantCulture));
                }

                return result.ToString();
            }
        }

        /// <summary>
        /// Parses strict UTF-8 JSON text, rejecting duplicate keys and
        /// non-canonical Unicode. Call <see cref="RequireCanonicalText"/> when
        /// the original text itself must have canonical ordering/escaping.
        /// </summary>
        public static object? Parse(string text)
        {
            return Parse(text, CanonicalJsonOptions.Strict);
        }

        /// <summary>
        /// Parses strict JSON with an explicit exact-path carrier policy.
        /// Only strings at configured complete paths bypass outer NFC.
        /// </summary>
        public static object? Parse(string text, CanonicalJsonOptions? options)
        {
            if (text == null)
            {
                throw new ArgumentNullException(nameof(text));
            }

            Parser parser = new Parser(text, options ?? CanonicalJsonOptions.Strict);
            object? result = parser.ParseDocument();
            return result;
        }

        /// <summary>
        /// Decodes one bounded UTF-8 JSON payload without accepting a BOM or
        /// replacement characters, then applies duplicate-key and NFC checks.
        /// </summary>
        public static object? ParseUtf8(byte[] payload, int maximumBytes)
        {
            return ParseUtf8(payload, maximumBytes, CanonicalJsonOptions.Strict);
        }

        /// <summary>
        /// Decodes a bounded UTF-8 payload using an explicit exact-path
        /// carrier policy before any outer-string normalization occurs.
        /// </summary>
        public static object? ParseUtf8(
            byte[] payload,
            int maximumBytes,
            CanonicalJsonOptions? options)
        {
            if (payload == null)
            {
                throw new ArgumentNullException(nameof(payload));
            }

            if (maximumBytes < 1 || payload.Length == 0 || payload.Length > maximumBytes)
            {
                throw new CanonicalJsonException("JSON payload exceeds its fixed byte limit.");
            }

            if (
                payload.Length >= 3 &&
                payload[0] == 0xef &&
                payload[1] == 0xbb &&
                payload[2] == 0xbf)
            {
                throw new CanonicalJsonException("UTF-8 BOM is forbidden.");
            }

            try
            {
                return Parse(StrictUtf8.GetString(payload), options);
            }
            catch (DecoderFallbackException exception)
            {
                throw new CanonicalJsonException("JSON payload is not strict UTF-8: " + exception.Message);
            }
        }

        /// <summary>Decodes and proves the exact canonical UTF-8 spelling.</summary>
        public static object? RequireCanonicalUtf8(byte[] payload, int maximumBytes)
        {
            return RequireCanonicalUtf8(
                payload,
                maximumBytes,
                CanonicalJsonOptions.Strict);
        }

        /// <summary>
        /// Decodes and proves the exact canonical UTF-8 spelling with an
        /// explicit exact-path carrier policy.
        /// </summary>
        public static object? RequireCanonicalUtf8(
            byte[] payload,
            int maximumBytes,
            CanonicalJsonOptions? options)
        {
            CanonicalJsonOptions effectiveOptions =
                options ?? CanonicalJsonOptions.Strict;
            object? parsed = ParseUtf8(payload, maximumBytes, effectiveOptions);
            byte[] canonical = SerializeUtf8(parsed, effectiveOptions);
            if (canonical.Length != payload.Length)
            {
                throw new CanonicalJsonException("JSON UTF-8 payload is not canonical.");
            }

            for (int index = 0; index < canonical.Length; index++)
            {
                if (canonical[index] != payload[index])
                {
                    throw new CanonicalJsonException("JSON UTF-8 payload is not canonical.");
                }
            }

            return parsed;
        }

        /// <summary>
        /// Parses text and proves that it equals the one permitted canonical
        /// spelling. This makes duplicate-key rejection useful at every raw
        /// JSON boundary rather than only after a DTO has been constructed.
        /// </summary>
        public static object? RequireCanonicalText(string text)
        {
            return RequireCanonicalText(text, CanonicalJsonOptions.Strict);
        }

        /// <summary>
        /// Parses text and proves its canonical spelling with an explicit
        /// exact-path carrier policy.
        /// </summary>
        public static object? RequireCanonicalText(
            string text,
            CanonicalJsonOptions? options)
        {
            CanonicalJsonOptions effectiveOptions =
                options ?? CanonicalJsonOptions.Strict;
            object? parsed = Parse(text, effectiveOptions);
            string canonical = Serialize(parsed, effectiveOptions);
            if (!string.Equals(text, canonical, StringComparison.Ordinal))
            {
                throw new CanonicalJsonException("JSON text is not canonical.");
            }

            return parsed;
        }

        /// <summary>Validates one ordinary scalar as bounded, valid NFC Unicode.</summary>
        public static void RequireNfcString(string value, string parameterName)
        {
            if (value == null)
            {
                throw new ArgumentNullException(parameterName);
            }

            int codePoints = CountCodePoints(value);
            if (codePoints > MaxStringCodePoints)
            {
                throw new CanonicalJsonException("JSON string exceeds the fixed code-point limit.");
            }

            int byteCount;
            try
            {
                byteCount = StrictUtf8.GetByteCount(value);
            }
            catch (EncoderFallbackException exception)
            {
                throw new CanonicalJsonException("JSON string is not valid Unicode: " + exception.Message);
            }

            if (byteCount > MaxStringUtf8Bytes)
            {
                throw new CanonicalJsonException("JSON string exceeds the fixed UTF-8 limit.");
            }

            string normalized;
            try
            {
                normalized = value.Normalize(NormalizationForm.FormC);
            }
            catch (ArgumentException exception)
            {
                throw new CanonicalJsonException("JSON string cannot be normalized: " + exception.Message);
            }

            if (!string.Equals(value, normalized, StringComparison.Ordinal))
            {
                throw new CanonicalJsonException("JSON string is not NFC.");
            }
        }

        /// <summary>
        /// Validates one exact outer embedded-JSON carrier. Unlike ordinary
        /// scalars it deliberately does not apply NFC or the 64 KiB limits.
        /// </summary>
        internal static void RequireOpaqueString(
            string value,
            int maximumUtf8Bytes,
            string parameterName)
        {
            if (value == null)
            {
                throw new ArgumentNullException(parameterName);
            }

            if (maximumUtf8Bytes < 0)
            {
                throw new ArgumentOutOfRangeException(nameof(maximumUtf8Bytes));
            }

            int byteCount;
            try
            {
                byteCount = StrictUtf8.GetByteCount(value);
            }
            catch (EncoderFallbackException exception)
            {
                throw new CanonicalJsonException(
                    "Opaque embedded JSON is not valid Unicode: " + exception.Message);
            }

            if (byteCount > maximumUtf8Bytes)
            {
                throw new CanonicalJsonException(
                    "Opaque embedded JSON exceeds its fixed UTF-8 byte limit.");
            }
        }

        /// <summary>Validates a fixed lowercase SHA-256 hex token.</summary>
        public static void RequireSha256(string value, string parameterName)
        {
            RequireNfcString(value, parameterName);
            if (value.Length != 64)
            {
                throw new CanonicalJsonException("SHA-256 token has an invalid length.");
            }

            for (int index = 0; index < value.Length; index++)
            {
                char character = value[index];
                if (!((character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f')))
                {
                    throw new CanonicalJsonException("SHA-256 token is not lowercase hexadecimal.");
                }
            }
        }

        private static void WriteValue(
            object? value,
            StringBuilder builder,
            int depth,
            HashSet<object> active,
            CanonicalJsonOptions options,
            CanonicalJsonPath path)
        {
            // depth counts containers already entered on the root-to-value
            // path. A scalar at depth 128 is valid; entering a 129th array
            // or object is not, including when that container is empty.
            if (depth > MaxNestingDepth)
            {
                throw new CanonicalJsonException("JSON nesting exceeds the fixed limit.");
            }

            int opaqueMaximum;
            bool isOpaqueCarrier =
                options.TryGetOpaqueStringMaximum(path, out opaqueMaximum);
            if (value == null)
            {
                if (isOpaqueCarrier)
                {
                    throw new CanonicalJsonException("Opaque embedded JSON must be a string.");
                }

                builder.Append("null");
                return;
            }

            if (value is string)
            {
                WriteString(
                    (string)value,
                    builder,
                    isOpaqueCarrier ? (int?)opaqueMaximum : null);
                return;
            }

            if (isOpaqueCarrier)
            {
                throw new CanonicalJsonException("Opaque embedded JSON must be a string.");
            }

            if (value is bool)
            {
                builder.Append((bool)value ? "true" : "false");
                return;
            }

            if (value is byte)
            {
                builder.Append(((byte)value).ToString(CultureInfo.InvariantCulture));
                return;
            }

            if (value is sbyte)
            {
                builder.Append(((sbyte)value).ToString(CultureInfo.InvariantCulture));
                return;
            }

            if (value is short)
            {
                builder.Append(((short)value).ToString(CultureInfo.InvariantCulture));
                return;
            }

            if (value is ushort)
            {
                builder.Append(((ushort)value).ToString(CultureInfo.InvariantCulture));
                return;
            }

            if (value is int)
            {
                builder.Append(((int)value).ToString(CultureInfo.InvariantCulture));
                return;
            }

            if (value is uint)
            {
                builder.Append(((uint)value).ToString(CultureInfo.InvariantCulture));
                return;
            }

            if (value is long)
            {
                builder.Append(((long)value).ToString(CultureInfo.InvariantCulture));
                return;
            }

            if (value is ulong)
            {
                builder.Append(((ulong)value).ToString(CultureInfo.InvariantCulture));
                return;
            }

            if (value is decimal || value is float || value is double)
            {
                throw new CanonicalJsonException(
                    "Canonical JSON accepts integers only; decimal and floating values are unsupported.");
            }

            IDictionary? dictionary = value as IDictionary;
            if (dictionary != null)
            {
                RequireContainerDepth(depth);
                WriteObject(dictionary, builder, depth, active, options, path);
                return;
            }

            IEnumerable? sequence = value as IEnumerable;
            if (sequence != null)
            {
                RequireContainerDepth(depth);
                WriteArray(sequence, builder, depth, active, options, path);
                return;
            }

            throw new CanonicalJsonException(
                "JSON value uses an unsupported CLR type: " + value.GetType().FullName + ".");
        }

        /// <summary>
        /// Requires that a container can be entered beneath the supplied
        /// number of already-open containers. This is shared by parser and
        /// serializer so empty and nonempty arrays/objects use identical
        /// root-to-container depth semantics.
        /// </summary>
        private static void RequireContainerDepth(int parentDepth)
        {
            if (parentDepth >= MaxNestingDepth)
            {
                throw new CanonicalJsonException("JSON nesting exceeds the fixed limit.");
            }
        }

        private static void WriteObject(
            IDictionary dictionary,
            StringBuilder builder,
            int depth,
            HashSet<object> active,
            CanonicalJsonOptions options,
            CanonicalJsonPath path)
        {
            EnterContainer(dictionary, active);
            try
            {
                List<KeyValuePair<string, object?>> values = new List<KeyValuePair<string, object?>>();
                foreach (DictionaryEntry entry in dictionary)
                {
                    string? key = entry.Key as string;
                    if (key == null)
                    {
                        throw new CanonicalJsonException("JSON object key is not a string.");
                    }

                    RequireNfcString(key, "key");
                    values.Add(new KeyValuePair<string, object?>(key, entry.Value));
                }

                values.Sort(CompareKeys);
                for (int index = 1; index < values.Count; index++)
                {
                    if (string.Equals(values[index - 1].Key, values[index].Key, StringComparison.Ordinal))
                    {
                        throw new CanonicalJsonException("JSON object contains a duplicate key.");
                    }
                }

                builder.Append('{');
                for (int index = 0; index < values.Count; index++)
                {
                    if (index != 0)
                    {
                        builder.Append(',');
                    }

                    WriteString(values[index].Key, builder, null);
                    builder.Append(':');
                    WriteValue(
                        values[index].Value,
                        builder,
                        depth + 1,
                        active,
                        options,
                        path.Append(values[index].Key));
                }

                builder.Append('}');
            }
            finally
            {
                active.Remove(dictionary);
            }
        }

        private static int CompareKeys(KeyValuePair<string, object?> left, KeyValuePair<string, object?> right)
        {
            return CompareUnicodeScalars(left.Key, right.Key);
        }

        /// <summary>
        /// Compares already-validated NFC strings in Unicode scalar order.
        /// .NET ordinal comparisons use UTF-16 code units, which would put an
        /// astral key before some BMP keys and diverge from Python's code-point
        /// ordering.
        /// </summary>
        private static int CompareUnicodeScalars(string left, string right)
        {
            int leftIndex = 0;
            int rightIndex = 0;
            while (leftIndex < left.Length && rightIndex < right.Length)
            {
                int leftScalar = ReadUnicodeScalar(left, ref leftIndex);
                int rightScalar = ReadUnicodeScalar(right, ref rightIndex);
                if (leftScalar != rightScalar)
                {
                    return leftScalar < rightScalar ? -1 : 1;
                }
            }

            if (leftIndex == left.Length && rightIndex == right.Length)
            {
                return 0;
            }

            return leftIndex == left.Length ? -1 : 1;
        }

        private static int ReadUnicodeScalar(string value, ref int index)
        {
            char first = value[index++];
            if (char.IsHighSurrogate(first))
            {
                if (index >= value.Length || !char.IsLowSurrogate(value[index]))
                {
                    throw new CanonicalJsonException("JSON string contains an unpaired high surrogate.");
                }

                char second = value[index++];
                return char.ConvertToUtf32(first, second);
            }

            if (char.IsLowSurrogate(first))
            {
                throw new CanonicalJsonException("JSON string contains an unpaired low surrogate.");
            }

            return first;
        }

        private static void WriteArray(
            IEnumerable sequence,
            StringBuilder builder,
            int depth,
            HashSet<object> active,
            CanonicalJsonOptions options,
            CanonicalJsonPath path)
        {
            EnterContainer(sequence, active);
            try
            {
                builder.Append('[');
                int index = 0;
                foreach (object item in sequence)
                {
                    if (index != 0)
                    {
                        builder.Append(',');
                    }

                    WriteValue(
                        item,
                        builder,
                        depth + 1,
                        active,
                        options,
                        path.Append(index));
                    index++;
                }

                builder.Append(']');
            }
            finally
            {
                active.Remove(sequence);
            }
        }

        private static void EnterContainer(object value, HashSet<object> active)
        {
            if (!active.Add(value))
            {
                throw new CanonicalJsonException("JSON value contains a cycle.");
            }
        }

        private static void WriteString(
            string value,
            StringBuilder builder,
            int? opaqueMaximumUtf8Bytes)
        {
            if (opaqueMaximumUtf8Bytes.HasValue)
            {
                RequireOpaqueString(
                    value,
                    opaqueMaximumUtf8Bytes.Value,
                    "value");
            }
            else
            {
                RequireNfcString(value, "value");
            }

            builder.Append('"');
            for (int index = 0; index < value.Length; index++)
            {
                char character = value[index];
                switch (character)
                {
                    case '"':
                        builder.Append("\\\"");
                        break;
                    case '\\':
                        builder.Append("\\\\");
                        break;
                    case '\b':
                        builder.Append("\\b");
                        break;
                    case '\f':
                        builder.Append("\\f");
                        break;
                    case '\n':
                        builder.Append("\\n");
                        break;
                    case '\r':
                        builder.Append("\\r");
                        break;
                    case '\t':
                        builder.Append("\\t");
                        break;
                    default:
                        if (character < 0x20)
                        {
                            builder.Append("\\u");
                            builder.Append(((int)character).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            builder.Append(character);
                        }

                        break;
                }
            }

            builder.Append('"');
        }

        private static int CountCodePoints(string value)
        {
            int count = 0;
            for (int index = 0; index < value.Length; index++)
            {
                char character = value[index];
                if (char.IsHighSurrogate(character))
                {
                    if (index + 1 >= value.Length || !char.IsLowSurrogate(value[index + 1]))
                    {
                        throw new CanonicalJsonException("JSON string contains an unpaired high surrogate.");
                    }

                    index++;
                }
                else if (char.IsLowSurrogate(character))
                {
                    throw new CanonicalJsonException("JSON string contains an unpaired low surrogate.");
                }

                count++;
            }

            return count;
        }

        private sealed class ReferenceComparer : IEqualityComparer<object>
        {
            internal static readonly ReferenceComparer Instance = new ReferenceComparer();

            public new bool Equals(object left, object right)
            {
                return ReferenceEquals(left, right);
            }

            public int GetHashCode(object value)
            {
                return RuntimeHelpers.GetHashCode(value);
            }
        }

        private sealed class Parser
        {
            private readonly string text;
            private readonly CanonicalJsonOptions options;
            private int index;

            internal Parser(string source, CanonicalJsonOptions canonicalOptions)
            {
                text = source;
                options = canonicalOptions ??
                    throw new ArgumentNullException(nameof(canonicalOptions));
            }

            internal object? ParseDocument()
            {
                SkipWhitespace();
                object? result = ParseValue(0, new CanonicalJsonPath());
                SkipWhitespace();
                if (index != text.Length)
                {
                    throw Error("JSON contains trailing data.");
                }

                return result;
            }

            private object? ParseValue(int depth, CanonicalJsonPath path)
            {
                // depth counts containers already entered on the
                // root-to-value path. Validate container entry below, before
                // descending recursively, so an empty 129th container cannot
                // bypass the cap.
                if (depth > MaxNestingDepth)
                {
                    throw Error("JSON nesting exceeds the fixed limit.");
                }

                if (index >= text.Length)
                {
                    throw Error("JSON ends before a value.");
                }

                char next = text[index];
                int opaqueMaximum;
                bool isOpaqueCarrier =
                    options.TryGetOpaqueStringMaximum(path, out opaqueMaximum);
                if (isOpaqueCarrier && next != '"')
                {
                    throw Error("Opaque embedded JSON must be a string.");
                }

                if (next == '{')
                {
                    RequireContainerDepth(depth);
                    return ParseObject(depth + 1, path);
                }

                if (next == '[')
                {
                    RequireContainerDepth(depth);
                    return ParseArray(depth + 1, path);
                }

                if (next == '"')
                {
                    return ParseString(
                        isOpaqueCarrier ? (int?)opaqueMaximum : null);
                }

                if (next == 't')
                {
                    RequireLiteral("true");
                    return true;
                }

                if (next == 'f')
                {
                    RequireLiteral("false");
                    return false;
                }

                if (next == 'n')
                {
                    RequireLiteral("null");
                    return null;
                }

                if (next == '-' || (next >= '0' && next <= '9'))
                {
                    return ParseNumber();
                }

                throw Error("JSON contains an invalid value token.");
            }

            private Dictionary<string, object?> ParseObject(
                int depth,
                CanonicalJsonPath path)
            {
                Require('{');
                SkipWhitespace();
                Dictionary<string, object?> result = new Dictionary<string, object?>(StringComparer.Ordinal);
                if (TryConsume('}'))
                {
                    return result;
                }

                while (true)
                {
                    if (index >= text.Length || text[index] != '"')
                    {
                        throw Error("JSON object key is missing.");
                    }

                    string key = ParseString(null);
                    if (result.ContainsKey(key))
                    {
                        throw Error("JSON object contains a duplicate key.");
                    }

                    SkipWhitespace();
                    Require(':');
                    SkipWhitespace();
                    result.Add(key, ParseValue(depth, path.Append(key)));
                    SkipWhitespace();
                    if (TryConsume('}'))
                    {
                        return result;
                    }

                    Require(',');
                    SkipWhitespace();
                }
            }

            private List<object?> ParseArray(int depth, CanonicalJsonPath path)
            {
                Require('[');
                SkipWhitespace();
                List<object?> result = new List<object?>();
                if (TryConsume(']'))
                {
                    return result;
                }

                while (true)
                {
                    result.Add(ParseValue(depth, path.Append(result.Count)));
                    SkipWhitespace();
                    if (TryConsume(']'))
                    {
                        return result;
                    }

                    Require(',');
                    SkipWhitespace();
                }
            }

            private string ParseString(int? opaqueMaximumUtf8Bytes)
            {
                Require('"');
                StringBuilder result = new StringBuilder();
                while (index < text.Length)
                {
                    char character = text[index++];
                    if (character == '"')
                    {
                        string value = result.ToString();
                        if (opaqueMaximumUtf8Bytes.HasValue)
                        {
                            RequireOpaqueString(
                                value,
                                opaqueMaximumUtf8Bytes.Value,
                                "value");
                        }
                        else
                        {
                            RequireNfcString(value, "value");
                        }

                        return value;
                    }

                    if (character < 0x20)
                    {
                        throw Error("JSON string contains an unescaped control character.");
                    }

                    if (character != '\\')
                    {
                        result.Append(character);
                        continue;
                    }

                    if (index >= text.Length)
                    {
                        throw Error("JSON string ends in an escape.");
                    }

                    char escape = text[index++];
                    switch (escape)
                    {
                        case '"':
                        case '\\':
                        case '/':
                            result.Append(escape);
                            break;
                        case 'b':
                            result.Append('\b');
                            break;
                        case 'f':
                            result.Append('\f');
                            break;
                        case 'n':
                            result.Append('\n');
                            break;
                        case 'r':
                            result.Append('\r');
                            break;
                        case 't':
                            result.Append('\t');
                            break;
                        case 'u':
                            result.Append(ParseUnicodeEscape());
                            break;
                        default:
                            throw Error("JSON string has an invalid escape.");
                    }
                }

                throw Error("JSON string is unterminated.");
            }

            private char ParseUnicodeEscape()
            {
                if (index + 4 > text.Length)
                {
                    throw Error("JSON Unicode escape is truncated.");
                }

                int value = 0;
                for (int offset = 0; offset < 4; offset++)
                {
                    char character = text[index++];
                    int digit;
                    if (character >= '0' && character <= '9')
                    {
                        digit = character - '0';
                    }
                    else if (character >= 'a' && character <= 'f')
                    {
                        digit = character - 'a' + 10;
                    }
                    else if (character >= 'A' && character <= 'F')
                    {
                        digit = character - 'A' + 10;
                    }
                    else
                    {
                        throw Error("JSON Unicode escape is invalid.");
                    }

                    value = (value << 4) | digit;
                }

                return (char)value;
            }

            private object ParseNumber()
            {
                int start = index;
                bool negative = TryConsume('-');
                if (negative && index >= text.Length)
                {
                    throw Error("JSON number is truncated.");
                }

                if (TryConsume('0'))
                {
                    if (negative)
                    {
                        throw Error("Canonical JSON integer cannot use negative zero.");
                    }

                    if (index < text.Length && text[index] >= '0' && text[index] <= '9')
                    {
                        throw Error("JSON number has a leading zero.");
                    }
                }
                else
                {
                    RequireDigits();
                }

                if (TryConsume('.'))
                {
                    throw Error("Canonical JSON accepts integers only.");
                }

                if (index < text.Length && (text[index] == 'e' || text[index] == 'E'))
                {
                    throw Error("Canonical JSON accepts integers only.");
                }

                string token = text.Substring(start, index - start);
                if (negative)
                {
                    long signed;
                    if (!long.TryParse(
                        token,
                        NumberStyles.AllowLeadingSign,
                        CultureInfo.InvariantCulture,
                        out signed))
                    {
                        throw Error("Canonical JSON integer is outside the Int64 range.");
                    }

                    return signed;
                }

                long integer;
                if (long.TryParse(
                    token,
                    NumberStyles.None,
                    CultureInfo.InvariantCulture,
                    out integer))
                {
                    return integer;
                }

                ulong unsigned;
                if (!ulong.TryParse(
                    token,
                    NumberStyles.None,
                    CultureInfo.InvariantCulture,
                    out unsigned))
                {
                    throw Error("Canonical JSON integer is outside the UInt64 range.");
                }

                return unsigned;
            }

            private void RequireDigits()
            {
                if (index >= text.Length || text[index] < '0' || text[index] > '9')
                {
                    throw Error("JSON number requires a digit.");
                }

                while (index < text.Length && text[index] >= '0' && text[index] <= '9')
                {
                    index++;
                }
            }

            private void RequireLiteral(string literal)
            {
                if (index + literal.Length > text.Length ||
                    !string.Equals(text.Substring(index, literal.Length), literal, StringComparison.Ordinal))
                {
                    throw Error("JSON literal is invalid.");
                }

                index += literal.Length;
            }

            private void Require(char expected)
            {
                if (index >= text.Length || text[index] != expected)
                {
                    throw Error("JSON token is invalid.");
                }

                index++;
            }

            private bool TryConsume(char expected)
            {
                if (index >= text.Length || text[index] != expected)
                {
                    return false;
                }

                index++;
                return true;
            }

            private void SkipWhitespace()
            {
                while (index < text.Length)
                {
                    char value = text[index];
                    if (value != ' ' && value != '\n' && value != '\r' && value != '\t')
                    {
                        return;
                    }

                    index++;
                }
            }

            private CanonicalJsonException Error(string message)
            {
                return new CanonicalJsonException(message + " Offset: " + index.ToString(CultureInfo.InvariantCulture) + ".");
            }
        }
    }
}
