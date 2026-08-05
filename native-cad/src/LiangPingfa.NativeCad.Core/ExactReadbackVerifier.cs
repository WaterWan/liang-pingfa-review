// SPDX-License-Identifier: MIT
// Exact before -> fixed-manifest delta -> after verifier for generated state.

using System;
using System.Collections.Generic;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.Core
{
    /// <summary>
    /// Verifies that no state changed beyond the three v1 fixed operation
    /// profiles. It is used before the sole commit and again against the
    /// freshly reopened final state.
    /// </summary>
    public static class ExactReadbackVerifier
    {
        /// <summary>Verifies one exact generated transition or fails closed.</summary>
        public static void Verify(
            GeometryExportV2 before,
            CoreManifestV2 manifest,
            GeometryExportV2 after,
            bool requireFinalRevisionTransition)
        {
            if (before == null)
            {
                throw new ArgumentNullException(nameof(before));
            }

            if (manifest == null)
            {
                throw new ArgumentNullException(nameof(manifest));
            }

            if (after == null)
            {
                throw new ArgumentNullException(nameof(after));
            }

            try
            {
                RequireDocumentState(
                    before,
                    manifest,
                    after,
                    requireFinalRevisionTransition);
                RequireOwnersUnchanged(before.Snapshot.Owners, after.Snapshot.Owners);
                Dictionary<string, CadEntitySnapshot> beforeByHandle = ByHandle(before.Snapshot.Entities);
                Dictionary<string, CadEntitySnapshot> afterByHandle = ByHandle(after.Snapshot.Entities);
                HashSet<string> deletes = new HashSet<string>(StringComparer.Ordinal);
                HashSet<string> translations = new HashSet<string>(StringComparer.Ordinal);
                List<CreateReviewMarkerOperationV2> markerOperations =
                    new List<CreateReviewMarkerOperationV2>();

                for (int index = 0; index < manifest.Operations.Count; index++)
                {
                    ManifestOperationV2 operation = manifest.Operations[index];
                    if (operation is TranslateDbTextOperationV2)
                    {
                        TranslateDbTextOperationV2 translate = (TranslateDbTextOperationV2)operation;
                        CadEntitySnapshot beforeEntity = RequireTarget(before, translate.Target);
                        CadEntitySnapshot? afterEntity;
                        if (!afterByHandle.TryGetValue(beforeEntity.Handle, out afterEntity))
                        {
                            Fail("Translated target is absent.");
                        }

                        VerifyTranslate(beforeEntity, afterEntity!, translate);
                        translations.Add(beforeEntity.Handle);
                    }
                    else if (operation is DeleteAuxiliaryOverlayTextOperationV2)
                    {
                        DeleteAuxiliaryOverlayTextOperationV2 delete = (DeleteAuxiliaryOverlayTextOperationV2)operation;
                        CadEntitySnapshot beforeEntity = RequireTarget(before, delete.Target);
                        if (afterByHandle.ContainsKey(beforeEntity.Handle))
                        {
                            Fail("Deleted exact target remains in readback.");
                        }

                        deletes.Add(beforeEntity.Handle);
                    }
                    else if (operation is CreateReviewMarkerOperationV2)
                    {
                        markerOperations.Add((CreateReviewMarkerOperationV2)operation);
                    }
                    else
                    {
                        Fail("Unknown operation is present in readback verification.");
                    }
                }

                Dictionary<string, string> markerHandles = MatchMarkers(
                    beforeByHandle,
                    afterByHandle,
                    markerOperations);
                RequireNoUnplannedChanges(
                    beforeByHandle,
                    afterByHandle,
                    deletes,
                    translations,
                    markerHandles);
                RequireOrder(
                    before.Snapshot.Entities,
                    after.Snapshot.Entities,
                    deletes,
                    markerHandles,
                    markerOperations,
                    after);
                RequireOpaqueRecordsUnchanged(beforeByHandle, afterByHandle);
            }
            catch (CadCoreException)
            {
                throw;
            }
            catch (CanonicalJsonException exception)
            {
                throw new CadCoreException(CadCoreErrorCode.ReadbackMismatch, exception.Message);
            }
        }

        private static void RequireDocumentState(
            GeometryExportV2 before,
            CoreManifestV2 manifest,
            GeometryExportV2 after,
            bool requireFinalRevisionTransition)
        {
            if (!requireFinalRevisionTransition &&
                !after.Snapshot.Source.ExactlyMatches(
                    manifest.ExpectedPrewriteSourceBinding))
            {
                Fail("Readback source binding differs from its manifest phase binding.");
            }
            if (requireFinalRevisionTransition)
            {
                manifest.FinalOutputConstraints.RequireActual(
                    manifest.ExpectedPrewriteOutputCopyBinding,
                    after.Snapshot.Source);
            }

            if (
                !string.Equals(before.Document.TableStateDigest, after.Document.TableStateDigest, StringComparison.Ordinal) ||
                !string.Equals(before.Document.LayoutStateDigest, after.Document.LayoutStateDigest, StringComparison.Ordinal) ||
                !string.Equals(before.Document.BlockStateDigest, after.Document.BlockStateDigest, StringComparison.Ordinal) ||
                !string.Equals(before.Document.DocumentStateDigest, after.Document.DocumentStateDigest, StringComparison.Ordinal) ||
                !string.Equals(before.Document.MarkerLayerFingerprint, after.Document.MarkerLayerFingerprint, StringComparison.Ordinal) ||
                !string.Equals(before.Document.MarkerStyleFingerprint, after.Document.MarkerStyleFingerprint, StringComparison.Ordinal))
            {
                Fail("Protected table/layout/block/document state changed.");
            }
            string expectedStableHost = manifest.ExpectedStableHostBindingDigest;
            if (!string.Equals(
                    before.Snapshot.BindingContext.StableExecutionHostBindingDigest(
                        manifest.MarkerPolicy),
                    expectedStableHost,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    after.Snapshot.BindingContext.StableExecutionHostBindingDigest(
                        manifest.MarkerPolicy),
                    expectedStableHost,
                    StringComparison.Ordinal))
            {
                Fail("Stable host/profile/capability binding changed.");
            }

            if (!requireFinalRevisionTransition &&
                !string.Equals(
                    before.Document.DatabaseInstanceFingerprint,
                    after.Document.DatabaseInstanceFingerprint,
                    StringComparison.Ordinal))
            {
                Fail("Staged readback changed the active database instance.");
            }

            bool sameRevision = string.Equals(
                before.Document.RevisionFingerprint,
                after.Document.RevisionFingerprint,
                StringComparison.Ordinal);
            if (requireFinalRevisionTransition ? sameRevision : !sameRevision)
            {
                Fail("Final revision transition differs from the fixed generated policy.");
            }
        }

        private static void RequireOwnersUnchanged(
            IReadOnlyList<string> before,
            IReadOnlyList<string> after)
        {
            if (before.Count != after.Count)
            {
                Fail("Protected owner state changed.");
            }

            for (int index = 0; index < before.Count; index++)
            {
                if (!string.Equals(before[index], after[index], StringComparison.Ordinal))
                {
                    Fail("Protected owner state changed.");
                }
            }
        }

        private static void VerifyTranslate(
            CadEntitySnapshot before,
            CadEntitySnapshot after,
            TranslateDbTextOperationV2 operation)
        {
            if (before.Kind != NativeEntityKind.DbText ||
                !before.Container.IsDirectModelspace)
            {
                Fail("Translation precondition target is not direct Modelspace DBTEXT.");
            }

            if (!EqualsExceptGeometry(before, after))
            {
                Fail("Translation changed protected DBTEXT fields.");
            }

            TranslatedGeometryV2 expected = TranslatedGeometryV2.From(before, operation.Delta);
            if (!operation.ExpectedAfter.Matches(
                    new CadEntitySnapshot(
                        before.Handle,
                        before.Kind,
                        before.OwnerHandle,
                        before.Container,
                        before.SequenceIndex,
                        before.Layer,
                        before.Text,
                        before.Style,
                        before.HeightBits,
                        before.RotationBits,
                        expected.Position,
                        expected.Bounds,
                        expected.Segments,
                        before.OverlayEvidence)) ||
                !expected.Matches(after))
            {
                Fail("Translation geometry is not exact binary64 allowed delta.");
            }
        }

        private static bool EqualsExceptGeometry(
            CadEntitySnapshot before,
            CadEntitySnapshot after)
        {
            return string.Equals(before.Handle, after.Handle, StringComparison.Ordinal) &&
                before.Kind == after.Kind &&
                string.Equals(before.OwnerHandle, after.OwnerHandle, StringComparison.Ordinal) &&
                before.Container.Equals(after.Container) &&
                before.SequenceIndex == after.SequenceIndex &&
                string.Equals(before.Layer, after.Layer, StringComparison.Ordinal) &&
                string.Equals(before.Text, after.Text, StringComparison.Ordinal) &&
                string.Equals(before.Style, after.Style, StringComparison.Ordinal) &&
                string.Equals(before.HeightBits, after.HeightBits, StringComparison.Ordinal) &&
                string.Equals(before.RotationBits, after.RotationBits, StringComparison.Ordinal) &&
                before.OverlayEvidence.Equals(after.OverlayEvidence);
        }

        private static Dictionary<string, string> MatchMarkers(
            IDictionary<string, CadEntitySnapshot> before,
            IDictionary<string, CadEntitySnapshot> after,
            IReadOnlyList<CreateReviewMarkerOperationV2> operations)
        {
            List<CadEntitySnapshot> additions = new List<CadEntitySnapshot>();
            foreach (KeyValuePair<string, CadEntitySnapshot> pair in after)
            {
                if (!before.ContainsKey(pair.Key))
                {
                    additions.Add(pair.Value);
                }
            }

            if (additions.Count != operations.Count)
            {
                Fail("Marker addition cardinality differs.");
            }

            Dictionary<string, string> matched = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int operationIndex = 0; operationIndex < operations.Count; operationIndex++)
            {
                CreateReviewMarkerOperationV2 operation = operations[operationIndex];
                CadEntitySnapshot? candidate = null;
                for (int additionIndex = 0; additionIndex < additions.Count; additionIndex++)
                {
                    CadEntitySnapshot addition = additions[additionIndex];
                    if (!MarkerMatches(operation, addition))
                    {
                        continue;
                    }

                    if (candidate != null)
                    {
                        Fail("One marker operation has more than one matching addition.");
                    }

                    candidate = addition;
                }

                if (candidate == null)
                {
                    Fail("Marker operation lacks one exact addition.");
                }

                CadEntitySnapshot matchingMarker = candidate!;
                additions.Remove(matchingMarker);
                matched.Add(operation.OperationId, matchingMarker.Handle);
            }

            if (additions.Count != 0)
            {
                Fail("Readback has an unmatched marker addition.");
            }

            return matched;
        }

        private static bool MarkerMatches(
            CreateReviewMarkerOperationV2 operation,
            CadEntitySnapshot marker)
        {
            return marker.Kind == NativeEntityKind.DbText &&
                string.Equals(marker.OwnerHandle, operation.OwnerHandle, StringComparison.Ordinal) &&
                marker.Container.Equals(operation.Container) &&
                marker.SequenceIndex == operation.SequenceIndex &&
                string.Equals(marker.Text, operation.MarkerText, StringComparison.Ordinal) &&
                string.Equals(marker.Layer, operation.Layer, StringComparison.Ordinal) &&
                string.Equals(marker.Style, operation.Style, StringComparison.Ordinal) &&
                string.Equals(marker.HeightBits, operation.HeightBits, StringComparison.Ordinal) &&
                string.Equals(marker.RotationBits, operation.RotationBits, StringComparison.Ordinal) &&
                marker.OverlayEvidence.Equals(operation.OverlayEvidence) &&
                marker.Position.Equals(operation.Position) &&
                marker.Bounds.Minimum.Equals(operation.Position) &&
                marker.Bounds.Maximum.Equals(operation.Position) &&
                marker.Segments.Count == 0 &&
                string.Equals(
                    operation.MarkerFingerprint,
                    CreateReviewMarkerOperationV2.ComputeMarkerFingerprint(operation),
                    StringComparison.Ordinal);
        }

        private static void RequireNoUnplannedChanges(
            IDictionary<string, CadEntitySnapshot> before,
            IDictionary<string, CadEntitySnapshot> after,
            ISet<string> deletes,
            ISet<string> translations,
            IDictionary<string, string> markerHandles)
        {
            HashSet<string> expectedHandles = new HashSet<string>(before.Keys, StringComparer.Ordinal);
            foreach (string deleted in deletes)
            {
                expectedHandles.Remove(deleted);
            }

            foreach (KeyValuePair<string, string> marker in markerHandles)
            {
                expectedHandles.Add(marker.Value);
            }

            if (expectedHandles.Count != after.Count)
            {
                Fail("Readback has an unplanned addition or deletion.");
            }

            foreach (KeyValuePair<string, CadEntitySnapshot> pair in after)
            {
                if (!expectedHandles.Contains(pair.Key))
                {
                    Fail("Readback has an unplanned entity handle.");
                }
            }

            foreach (KeyValuePair<string, CadEntitySnapshot> pair in before)
            {
                string handle = pair.Key;
                if (deletes.Contains(handle) || translations.Contains(handle))
                {
                    continue;
                }

                CadEntitySnapshot? observed;
                if (!after.TryGetValue(handle, out observed) || !pair.Value.ExactlyEquals(observed))
                {
                    Fail("A non-target record changed.");
                }
            }
        }

        private static void RequireOrder(
            IReadOnlyList<CadEntitySnapshot> before,
            IReadOnlyList<CadEntitySnapshot> after,
            ISet<string> deletes,
            IDictionary<string, string> markerHandles,
            IReadOnlyList<CreateReviewMarkerOperationV2> markerOperations,
            GeometryExportV2 afterExport)
        {
            Dictionary<string, List<string>> expected =
                GroupHandles(before, deletes);
            for (int index = 0; index < markerOperations.Count; index++)
            {
                CreateReviewMarkerOperationV2 operation = markerOperations[index];
                string key = operation.Container.SortKey;
                List<string>? records;
                if (!expected.TryGetValue(key, out records))
                {
                    records = new List<string>();
                    expected.Add(key, records);
                }

                records.Add(markerHandles[operation.OperationId]);
            }

            Dictionary<string, List<string>> actual =
                GroupHandles(after, new HashSet<string>(StringComparer.Ordinal));
            RemoveEmpty(expected);
            RemoveEmpty(actual);
            if (expected.Count != actual.Count)
            {
                Fail("Container set changed.");
            }

            foreach (KeyValuePair<string, List<string>> pair in expected)
            {
                List<string>? observed;
                if (!actual.TryGetValue(pair.Key, out observed) ||
                    !SameSequence(pair.Value, observed))
                {
                    Fail("Container entity ordering changed.");
                }
            }

            string recomputed = CanonicalJson.Sha256Hex(afterExport.ContainerSequences);
            if (!string.Equals(recomputed, afterExport.Document.ContainerOrderDigest, StringComparison.Ordinal))
            {
                Fail("Container order digest drifted.");
            }
        }

        private static Dictionary<string, List<string>> GroupHandles(
            IReadOnlyList<CadEntitySnapshot> entities,
            ISet<string> excluded)
        {
            Dictionary<string, List<string>> result =
                new Dictionary<string, List<string>>(StringComparer.Ordinal);
            for (int index = 0; index < entities.Count; index++)
            {
                CadEntitySnapshot entity = entities[index];
                if (excluded.Contains(entity.Handle))
                {
                    continue;
                }

                List<string>? records;
                if (!result.TryGetValue(entity.Container.SortKey, out records))
                {
                    records = new List<string>();
                    result.Add(entity.Container.SortKey, records);
                }

                records.Add(entity.Handle);
            }

            return result;
        }

        private static void RemoveEmpty(Dictionary<string, List<string>> value)
        {
            List<string> remove = new List<string>();
            foreach (KeyValuePair<string, List<string>> pair in value)
            {
                if (pair.Value.Count == 0)
                {
                    remove.Add(pair.Key);
                }
            }

            for (int index = 0; index < remove.Count; index++)
            {
                value.Remove(remove[index]);
            }
        }

        private static bool SameSequence(IReadOnlyList<string> expected, IReadOnlyList<string> actual)
        {
            if (expected.Count != actual.Count)
            {
                return false;
            }

            for (int index = 0; index < expected.Count; index++)
            {
                if (!string.Equals(expected[index], actual[index], StringComparison.Ordinal))
                {
                    return false;
                }
            }

            return true;
        }

        private static void RequireOpaqueRecordsUnchanged(
            IDictionary<string, CadEntitySnapshot> before,
            IDictionary<string, CadEntitySnapshot> after)
        {
            foreach (KeyValuePair<string, CadEntitySnapshot> pair in before)
            {
                if (pair.Value.Kind != NativeEntityKind.Opaque)
                {
                    continue;
                }

                CadEntitySnapshot? observed;
                if (!after.TryGetValue(pair.Key, out observed) || !pair.Value.ExactlyEquals(observed))
                {
                    Fail("Protected opaque record changed.");
                }
            }
        }

        private static Dictionary<string, CadEntitySnapshot> ByHandle(
            IReadOnlyList<CadEntitySnapshot> entities)
        {
            Dictionary<string, CadEntitySnapshot> result =
                new Dictionary<string, CadEntitySnapshot>(StringComparer.Ordinal);
            for (int index = 0; index < entities.Count; index++)
            {
                CadEntitySnapshot entity = entities[index];
                if (result.ContainsKey(entity.Handle))
                {
                    Fail("Export contains duplicate handles.");
                }

                result.Add(entity.Handle, entity);
            }

            return result;
        }

        private static CadEntitySnapshot RequireTarget(GeometryExportV2 export, string targetId)
        {
            CadEntitySnapshot? target = export.FindByTargetId(targetId);
            if (target == null)
            {
                Fail("Manifest target lacks a precondition record.");
            }

            return target!;
        }

        private static void Fail(string message)
        {
            throw new CadCoreException(CadCoreErrorCode.ReadbackMismatch, message);
        }
    }
}
