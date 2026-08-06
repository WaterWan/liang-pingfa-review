// SPDX-License-Identifier: MIT
// The only Autodesk database mapping in this repository. It exposes the
// fixed vendor-neutral core, not arbitrary CAD commands or object mutation.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using LiangPingfa.NativeCad.Core;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.AutoCAD.Adapter
{
    /// <summary>
    /// Maps one current private document (or one owned fresh readback
    /// database) to the vendor-neutral transaction contract.
    /// </summary>
    internal sealed class AutodeskCadDatabase : ICadDatabase, IDisposable
    {
        private readonly Document? document;
        private readonly Database database;
        private readonly string privatePath;
        private readonly string privateRoot;
        private readonly RetainedPrivateDwgBinding privateBinding;
        private readonly NativeGeometryBindingContextV2 bindingContext;
        private readonly MarkerPolicyBindingV2? markerPolicy;
        private readonly bool ownsDatabase;
        private bool finalBindingPublicationValidated;
        private bool disposed;

        internal AutodeskCadDatabase(
            Document? document,
            Database database,
            string privatePath,
            string privateRoot,
            RetainedPrivateDwgBinding privateBinding,
            NativeGeometryBindingContextV2 bindingContext,
            MarkerPolicyBindingV2? markerPolicy,
            bool ownsDatabase)
        {
            this.document = document;
            this.database = database ?? throw new ArgumentNullException(nameof(database));
            this.privatePath = PrivatePathPolicy.RequirePrivateFile(
                privatePath,
                privateRoot,
                ".dwg");
            this.privateRoot = PrivatePathPolicy.RequirePrivateRoot(privateRoot);
            this.privateBinding = privateBinding ??
                throw new ArgumentNullException(nameof(privateBinding));
            if (!string.Equals(
                    this.privatePath,
                    this.privateBinding.PrivatePath,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new AdapterFailureException(
                    "LPF_SOURCE_BINDING",
                    "The retained private DWG lease names another file.");
            }

            this.bindingContext = bindingContext ??
                throw new ArgumentNullException(nameof(bindingContext));
            this.markerPolicy = markerPolicy;
            this.ownsDatabase = ownsDatabase;
        }

        internal Database Database
        {
            get { return database; }
        }

        internal string PrivatePath
        {
            get { return privatePath; }
        }

        /// <summary>
        /// Returns the one retained input binding used by every staged
        /// snapshot. Call <see cref="RequireCurrentPrivateBinding"/> only at
        /// an explicit file-security boundary.
        /// </summary>
        internal NativeSourceBindingV2 CachedPrivateBinding
        {
            get { return privateBinding.CachedBinding; }
        }

        internal void RequireCurrentPrivateBinding()
        {
            ThrowIfDisposed();
            privateBinding.RequireCurrent();
        }

        public CadDocumentSnapshot ReadSnapshot()
        {
            ThrowIfDisposed();
            if (ownsDatabase && !finalBindingPublicationValidated)
            {
                // The reopened database is about to feed the committed
                // result/readback boundary. Rehash once here, never per
                // transaction snapshot.
                privateBinding.RequireCurrent();
                finalBindingPublicationValidated = true;
            }

            using (DocumentLockScope documentLock = DocumentLockScope.Acquire(document))
            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                try
                {
                    return AutodeskSnapshotExporter.Export(
                        database,
                        transaction,
                        privateBinding.CachedBinding,
                        bindingContext);
                }
                finally
                {
                    transaction.Abort();
                }
            }
        }

        public ICadTransaction BeginTransaction()
        {
            ThrowIfDisposed();
            if (ownsDatabase)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.TransactionConflict,
                    "Fresh readback databases are read-only transaction sources.");
            }

            DocumentLockScope lockScope = DocumentLockScope.Acquire(document);
            try
            {
                // Security boundary: reject a private file replacement or
                // byte drift immediately before the write transaction starts.
                privateBinding.RequireCurrent();
                Transaction transaction = database.TransactionManager.StartTransaction();
                return new AutodeskCadTransaction(
                    database,
                    transaction,
                    lockScope,
                    privateBinding,
                    bindingContext,
                    markerPolicy);
            }
            catch
            {
                lockScope.Dispose();
                throw;
            }
        }

        public ICadDatabase SaveAndReopen(
            FinalOutputConstraintsV2 finalOutputConstraints)
        {
            ThrowIfDisposed();
            if (finalOutputConstraints == null)
            {
                throw new ArgumentNullException(nameof(finalOutputConstraints));
            }

            // The adapter never receives a public/source pathname. SaveAs is
            // constrained to the currently opened private workspace copy.
            PrivatePathPolicy.RequirePrivateFile(privatePath, privateRoot, ".dwg");
            // Security boundary: revalidate the held prewrite binding
            // immediately before SaveAs. Staged snapshots use its cached
            // value and therefore do not repeatedly hash the full DWG.
            privateBinding.RequireCurrent();
            NativeSourceBindingV2 before = privateBinding.CachedBinding;
            if (!string.Equals(
                    before.PathFingerprint,
                    finalOutputConstraints.AuthorizedPrivatePathFingerprint,
                    StringComparison.Ordinal) ||
                !string.Equals(
                    AdapterIdentity.HashUtf8(privateRoot),
                    finalOutputConstraints.AuthorizedPrivateRootFingerprint,
                    StringComparison.Ordinal))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.SaveFailed,
                    "The current document is not the authorized private output copy.");
            }

            try
            {
                database.SaveAs(privatePath, database.OriginalFileVersion);
            }
            catch (Exception exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.SaveFailed,
                    "The private DWG could not be saved: " + exception.Message);
            }

            RetainedPrivateDwgBinding? finalBinding = null;
            try
            {
                // SaveAs may replace the file object. Retire the old lease
                // and establish one new held final binding from the saved
                // pathname before opening the fresh readback database.
                privateBinding.Dispose();
                finalBinding = RetainedPrivateDwgBinding.Open(
                    privatePath,
                    privateRoot);
                NativeSourceBindingV2 saved = finalBinding.CachedBinding;
                finalOutputConstraints.RequireActual(before, saved);

                Database? reopened = null;
                try
                {
                    reopened = new Database(false, true);
                    // Autodesk documents allowCPConversion=true as the
                    // silent fallback when NLS files for the drawing's code
                    // page are unavailable.  false is dialog-capable and is
                    // therefore unsafe in unattended Core Console.  This
                    // database is readback-only: any silent conversion drift
                    // is rejected by ExactReadbackVerifier before publication
                    // and must never be saved back to the private DWG.
                    reopened.ReadDwgFile(
                        privatePath,
                        FileOpenMode.OpenForReadAndAllShare,
                        true,
                        string.Empty);
                    reopened.CloseInput(true);
                    AutodeskCadDatabase result = new AutodeskCadDatabase(
                        null,
                        reopened,
                        privatePath,
                        privateRoot,
                        finalBinding,
                        bindingContext,
                        markerPolicy,
                        true);
                    finalBinding = null;
                    return result;
                }
                catch (Exception exception)
                {
                    if (reopened != null)
                    {
                        reopened.Dispose();
                    }

                    throw new CadCoreException(
                        CadCoreErrorCode.ReopenFailed,
                        "The saved private DWG could not be reopened: " +
                        exception.Message);
                }
            }
            catch (CadCoreException)
            {
                throw;
            }
            catch (Exception exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.SaveFailed,
                    "The saved private DWG does not satisfy its output binding: " +
                    exception.Message);
            }
            finally
            {
                if (finalBinding != null)
                {
                    finalBinding.Dispose();
                }
            }
        }

        public void Dispose()
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            if (ownsDatabase)
            {
                database.Dispose();
            }

            privateBinding.Dispose();
        }

        private void ThrowIfDisposed()
        {
            if (disposed)
            {
                throw new ObjectDisposedException(nameof(AutodeskCadDatabase));
            }
        }
    }

    /// <summary>One active Autodesk transaction exposed through exact core checks.</summary>
    internal sealed class AutodeskCadTransaction : ICadTransaction
    {
        private readonly Database database;
        private readonly Transaction transaction;
        private readonly DocumentLockScope documentLock;
        private readonly RetainedPrivateDwgBinding privateBinding;
        private readonly NativeGeometryBindingContextV2 bindingContext;
        private readonly MarkerPolicyBindingV2? markerPolicy;
        private bool active = true;
        private bool disposed;

        internal AutodeskCadTransaction(
            Database database,
            Transaction transaction,
            DocumentLockScope documentLock,
            RetainedPrivateDwgBinding privateBinding,
            NativeGeometryBindingContextV2 bindingContext,
            MarkerPolicyBindingV2? markerPolicy)
        {
            this.database = database ?? throw new ArgumentNullException(nameof(database));
            this.transaction = transaction ?? throw new ArgumentNullException(nameof(transaction));
            this.documentLock = documentLock ?? throw new ArgumentNullException(nameof(documentLock));
            this.privateBinding = privateBinding ??
                throw new ArgumentNullException(nameof(privateBinding));
            this.bindingContext = bindingContext ??
                throw new ArgumentNullException(nameof(bindingContext));
            this.markerPolicy = markerPolicy;
        }

        public bool IsActive
        {
            get { return active && !disposed; }
        }

        public CadDocumentSnapshot CaptureSnapshot()
        {
            RequireActive();
            return AutodeskSnapshotExporter.Export(
                database,
                transaction,
                privateBinding.CachedBinding,
                bindingContext);
        }

        public void ReplaceExact(
            CadDocumentSnapshot expectedState,
            CadEntitySnapshot expectedTarget,
            CadEntitySnapshot replacement)
        {
            RequireActive();
            RequireExactState(expectedState, expectedTarget);
            if (replacement == null ||
                replacement.Kind != NativeEntityKind.DbText ||
                !replacement.Container.IsDirectModelspace ||
                !string.Equals(replacement.Handle, expectedTarget.Handle, StringComparison.Ordinal) ||
                !string.Equals(replacement.OwnerHandle, expectedTarget.OwnerHandle, StringComparison.Ordinal) ||
                !string.Equals(replacement.Layer, expectedTarget.Layer, StringComparison.Ordinal) ||
                !string.Equals(replacement.Text, expectedTarget.Text, StringComparison.Ordinal) ||
                !string.Equals(replacement.Style, expectedTarget.Style, StringComparison.Ordinal) ||
                !string.Equals(replacement.HeightBits, expectedTarget.HeightBits, StringComparison.Ordinal) ||
                !string.Equals(replacement.RotationBits, expectedTarget.RotationBits, StringComparison.Ordinal))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "A DBTEXT replacement changes fields outside the fixed translation profile.");
            }

            ObjectId id = ResolveObjectId(expectedTarget.Handle);
            DBText? text = transaction.GetObject(id, OpenMode.ForWrite, false) as DBText;
            if (text == null || text.IsErased ||
                !text.OwnerId.Equals(ResolveObjectId(expectedTarget.OwnerHandle)))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "The exact DBTEXT target is unavailable.");
            }
            DbTextAlignmentPolicy.RequireBaseLeft(text);
            RequireNonXrefOwner(text.OwnerId);

            Point3d position = AutodeskSnapshotExporter.ToPoint(replacement.Position);
            if (Binary64.ParseBits(replacement.Position.Z) !=
                Binary64.ParseBits(expectedTarget.Position.Z))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "DBTEXT translation must not change Z.");
            }

            text.Position = position;
        }

        public void EraseExact(
            CadDocumentSnapshot expectedState,
            CadEntitySnapshot expectedTarget)
        {
            // ICadTransaction retains the vendor-neutral delete member for
            // the in-memory/ODA-compatible core contract. AutoCAD must not
            // erase here: its SaveAs/reopen behavior compacts erased slots
            // and violates v2's gap-preserving physical-slot contract.
            throw new CadCoreException(
                CadCoreErrorCode.CapabilityDenied,
                "delete_auxiliary_overlay_text is unsupported by the AutoCAD adapter.");
        }

        public CadEntitySnapshot AppendExact(
            CadDocumentSnapshot expectedState,
            MarkerAppendRequestV2 request)
        {
            RequireActive();
            RequireExactState(expectedState, null);
            if (request == null)
            {
                throw new ArgumentNullException(nameof(request));
            }

            CreateReviewMarkerOperationV2 markerOperation = request.Operation;
            if (!markerOperation.Container.IsDirectModelspace ||
                string.IsNullOrEmpty(markerOperation.Layer) ||
                string.IsNullOrEmpty(markerOperation.MarkerText) ||
                string.IsNullOrEmpty(markerOperation.Style) ||
                !markerOperation.OverlayEvidence.UnsupportedData ||
                markerOperation.OverlayEvidence.UniqueContent ||
                markerOperation.OverlayEvidence.LeftPanel ||
                markerOperation.OverlayEvidence.CorrespondingRightAbsent ||
                markerOperation.OverlayEvidence.VisibleInterference)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Only one policy-derived direct Modelspace marker may be appended.");
            }

            ObjectId owner = ResolveObjectId(markerOperation.OwnerHandle);
            BlockTableRecord? record =
                transaction.GetObject(owner, OpenMode.ForWrite, false) as BlockTableRecord;
            if (record == null || record.IsFromExternalReference ||
                !AutodeskSnapshotExporter.IsDirectModelspace(
                    transaction,
                    record,
                    markerOperation.Container))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Marker owner is not the exact direct Modelspace record.");
            }

            CadContainerPhysicalSlots? expectedPhysicalContainer =
                expectedState.FindContainer(markerOperation.Container);
            int physicalNextSequence =
                AutodeskSnapshotExporter.CountPhysicalRecordSlots(record);
            if (expectedPhysicalContainer == null ||
                !string.Equals(
                    expectedPhysicalContainer.OwnerHandle,
                    markerOperation.OwnerHandle,
                    StringComparison.Ordinal) ||
                expectedPhysicalContainer.PhysicalSlotCount !=
                    markerOperation.SequenceIndex ||
                physicalNextSequence !=
                    expectedPhysicalContainer.PhysicalSlotCount)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Marker sequence no longer matches the physical Modelspace order.");
            }

            LayerTable? layers =
                transaction.GetObject(database.LayerTableId, OpenMode.ForRead, false) as LayerTable;
            TextStyleTable? styles =
                transaction.GetObject(database.TextStyleTableId, OpenMode.ForRead, false) as TextStyleTable;
            if (layers == null || styles == null ||
                !layers.Has(markerOperation.Layer) ||
                !styles.Has(markerOperation.Style))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.CapabilityDenied,
                    "Marker layer/style must be pre-existing exact resources.");
            }

            ObjectId styleId = styles[markerOperation.Style];
            DBText marker = new DBText
            {
                Layer = markerOperation.Layer,
                TextString = markerOperation.MarkerText,
                TextStyleId = styleId,
                Height = Binary64.ParseBits(markerOperation.HeightBits),
                Rotation = Binary64.ParseBits(markerOperation.RotationBits),
                Position = AutodeskSnapshotExporter.ToPoint(markerOperation.Position),
                Justify = AttachmentPoint.BaseLeft,
                HorizontalMode = TextHorizontalMode.TextLeft,
                VerticalMode = TextVerticalMode.TextBase,
            };
            ObjectId markerId = record.AppendEntity(marker);
            transaction.AddNewlyCreatedDBObject(marker, true);
            if (markerId.IsNull ||
                markerId.IsErased ||
                !marker.ObjectId.Equals(markerId) ||
                !marker.OwnerId.Equals(owner))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "The host did not retain the appended marker identity.");
            }

            CadEntitySnapshot actual = request.WithActualHandle(
                AutodeskSnapshotExporter.HandleText(marker.Handle));
            if (!request.Matches(actual))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "The host assigned marker fields outside the audited append request.");
            }

            return actual;
        }

        public void PrepareCommit()
        {
            RequireActive();
        }

        public void CommitExact(CadDocumentSnapshot expectedState)
        {
            RequireActive();
            RequireExactState(expectedState, null);
            try
            {
                transaction.Commit();
                active = false;
            }
            catch (Exception exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.CommitFailed,
                    "The private Autodesk transaction could not commit: " +
                    exception.Message);
            }
        }

        public void Abort()
        {
            if (!IsActive)
            {
                return;
            }

            try
            {
                transaction.Abort();
            }
            finally
            {
                active = false;
            }
        }

        public void Dispose()
        {
            if (disposed)
            {
                return;
            }

            try
            {
                Abort();
            }
            finally
            {
                transaction.Dispose();
                documentLock.Dispose();
                disposed = true;
            }
        }

        private void RequireExactState(
            CadDocumentSnapshot expectedState,
            CadEntitySnapshot? expectedTarget)
        {
            if (expectedState == null)
            {
                throw new ArgumentNullException(nameof(expectedState));
            }

            CadDocumentSnapshot current = CaptureSnapshot();
            if (!AutodeskSnapshotExporter.SnapshotsExactlyEqual(current, expectedState))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "The Autodesk transaction state drifted before mutation.");
            }

            if (expectedTarget != null)
            {
                CadEntitySnapshot? currentTarget =
                    current.FindByHandle(expectedTarget.Handle);
                if (currentTarget == null ||
                    !currentTarget.ExactlyEquals(expectedTarget))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.StalePrecondition,
                        "The Autodesk transaction target drifted before mutation.");
                }
            }
        }

        private ObjectId ResolveObjectId(string handle)
        {
            CadHandle.Require(handle, nameof(handle));
            ulong parsed;
            if (!ulong.TryParse(
                    handle,
                    NumberStyles.AllowHexSpecifier,
                    CultureInfo.InvariantCulture,
                    out parsed))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "An Autodesk handle is invalid.");
            }

            ObjectId id;
            try
            {
                id = database.GetObjectId(
                    false,
                    new Handle(unchecked((long)parsed)),
                    0);
            }
            catch (Exception exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "An audited Autodesk handle cannot be resolved: " +
                    exception.Message);
            }

            if (id.IsNull || id.IsErased || !id.IsValid)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "An audited Autodesk object identifier is unavailable.");
            }

            return id;
        }

        private void RequireNonXrefOwner(ObjectId owner)
        {
            BlockTableRecord? record =
                transaction.GetObject(owner, OpenMode.ForRead, false) as BlockTableRecord;
            if (record == null || record.IsErased || record.IsFromExternalReference)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "An externally referenced owner cannot enter edit scope.");
            }
        }

        private void RequireActive()
        {
            if (!IsActive)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.TransactionConflict,
                    "The Autodesk transaction is no longer active.");
            }
        }
    }

    /// <summary>Owns a document lock only when the host has not entered a command context.</summary>
    internal sealed class DocumentLockScope : IDisposable
    {
        private readonly DocumentLock? value;
        private bool disposed;

        private DocumentLockScope(DocumentLock? value)
        {
            this.value = value;
        }

        internal static DocumentLockScope Acquire(Document? document)
        {
            if (document == null)
            {
                return new DocumentLockScope(null);
            }

            string? inProgress = document.CommandInProgress;
            if (!string.IsNullOrEmpty(inProgress))
            {
                return new DocumentLockScope(null);
            }

            return new DocumentLockScope(document.LockDocument());
        }

        public void Dispose()
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            if (value != null)
            {
                value.Dispose();
            }
        }
    }

    /// <summary>
    /// Initial narrow field policy: exact v2 exports fail closed whenever a
    /// DBTEXT owns AutoCAD field data.  The carrier does not model field
    /// expressions, evaluator IDs, or dependencies, so evaluated text alone
    /// cannot be used as editable/protected state.
    /// </summary>
    internal static class DbTextFieldPolicy
    {
        internal static void RequireExactExportable(bool hasFields)
        {
            if (hasFields)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Field-backed DBTEXT is not losslessly representable by the exact profile.");
            }
        }
    }

    /// <summary>
    /// The v2 carrier models only ordinary BaseLeft DBTEXT. Autodesk defines
    /// that profile by its horizontal and vertical modes; Position is its sole
    /// canonical anchor. All modes with an alignment anchor fail closed.
    /// </summary>
    internal static class DbTextAlignmentPolicy
    {
        internal static void RequireBaseLeft(DBText text)
        {
            if (text == null)
            {
                throw new ArgumentNullException(nameof(text));
            }

            DbTextFieldPolicy.RequireExactExportable(text.HasFields);
            if (text.Justify != AttachmentPoint.BaseLeft)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Only BaseLeft DBTEXT is representable by the exact profile.");
            }

            RequireBaseLeftModes(text.HorizontalMode, text.VerticalMode);
        }

        internal static void RequireBaseLeftModes(
            TextHorizontalMode horizontal,
            TextVerticalMode vertical)
        {
            if (horizontal != TextHorizontalMode.TextLeft ||
                vertical != TextVerticalMode.TextBase)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Only TextLeft/TextBase DBTEXT is representable by the exact profile.");
            }
        }
    }

    /// <summary>Stable host context made from documented process and assembly inputs.</summary>
    internal static class AutodeskHostBinding
    {
        internal static string CurrentExecutableFingerprint()
        {
            using (Process process = Process.GetCurrentProcess())
            {
                if (process.MainModule == null ||
                    string.IsNullOrEmpty(process.MainModule.FileName))
                {
                    throw new AdapterFailureException(
                        "LPF_HOST_BINDING",
                        "The host executable cannot be identified.");
                }

                return AdapterIdentity.HashFile(process.MainModule.FileName);
            }
        }

        internal static NativeGeometryBindingContextV2 Create(
            string sessionId,
            string pluginFingerprint)
        {
            using (Process process = Process.GetCurrentProcess())
            {
                long creation = process.StartTime.ToUniversalTime().ToFileTimeUtc();
                string executable = CurrentExecutableFingerprint();
                string instance = CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "creation_time_100ns", creation.ToString(CultureInfo.InvariantCulture) },
                        { "pid", (long)process.Id },
                        { "windows_session_id", (long)process.SessionId },
                    });
                return new NativeGeometryBindingContextV2(
                    sessionId,
                    AdapterIdentity.AdapterId,
                    AdapterIdentity.Profile,
                    AdapterIdentity.PluginVersion,
                    AdapterIdentity.PluginId,
                    AdapterIdentity.PluginVersion,
                    pluginFingerprint,
                    NativeCadCapabilities.AutoCadAdapter,
                    "autocad",
                    AdapterIdentity.HostRelease,
                    AdapterIdentity.HostRuntime,
                    "full_host",
                    process.Id,
                    process.SessionId,
                    instance,
                    creation.ToString(CultureInfo.InvariantCulture),
                    executable);
            }
        }
    }

    /// <summary>Maps Autodesk table/object state to deterministic core snapshots.</summary>
    internal static class AutodeskSnapshotExporter
    {
        internal static CadDocumentSnapshot Export(
            Database database,
            Transaction transaction,
            NativeSourceBindingV2 source,
            NativeGeometryBindingContextV2 binding,
            string? hostDatabaseIdentity = null,
            string? hostSavedRevision = null)
        {
            if (database == null || transaction == null || source == null || binding == null)
            {
                throw new ArgumentNullException("Autodesk snapshot input is unavailable.");
            }

            BlockTable? blockTable =
                transaction.GetObject(database.BlockTableId, OpenMode.ForRead, false) as BlockTable;
            if (blockTable == null)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.TransactionFailure,
                    "The Autodesk block table is unavailable.");
            }

            List<ContainerRecord> containers = ReadContainers(transaction, blockTable);
            List<string> owners = new List<string>();
            List<CadContainerPhysicalSlots> physicalContainers =
                new List<CadContainerPhysicalSlots>();
            List<object?> containerState = new List<object?>();
            for (int index = 0; index < containers.Count; index++)
            {
                owners.Add(containers[index].OwnerHandle);
                physicalContainers.Add(
                    new CadContainerPhysicalSlots(
                        containers[index].Container,
                        containers[index].OwnerHandle,
                        containers[index].PhysicalSlotCount));
                containerState.Add(containers[index].ToStableState());
            }

            List<object?> opaqueProviderState = new List<object?>();
            List<CadEntitySnapshot> entities = new List<CadEntitySnapshot>();
            for (int index = 0; index < containers.Count; index++)
            {
                ExportContainerEntities(
                    transaction,
                    containers[index],
                    entities,
                    opaqueProviderState);
            }

            entities.Sort(CadDocumentSnapshot.CompareEntityOrder);
            CadDocumentTables tables = ReadTables(
                database,
                transaction,
                containerState,
                opaqueProviderState);
            string databaseFingerprint = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "owners", ToWireArray(owners) },
                    { "protected_tables", tables.ToStateWireValue() },
                    { "host_database_identity", hostDatabaseIdentity ?? string.Empty },
                });
            List<object?> entityState = new List<object?>();
            for (int index = 0; index < entities.Count; index++)
            {
                entityState.Add(entities[index].ToWireValue());
            }

            string revision = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "database_instance", databaseFingerprint },
                    { "entities", entityState },
                    { "protected_tables", tables.ToStateWireValue() },
                    { "host_saved_revision", hostSavedRevision ?? string.Empty },
                });
            return new CadDocumentSnapshot(
                databaseFingerprint,
                revision,
                owners,
                physicalContainers,
                entities,
                tables,
                source,
                binding);
        }

        internal static bool SnapshotsExactlyEqual(
            CadDocumentSnapshot left,
            CadDocumentSnapshot right)
        {
            if (left == null || right == null)
            {
                return false;
            }

            return string.Equals(
                ExactCadExporter.Export(left).ExportDigest,
                ExactCadExporter.Export(right).ExportDigest,
                StringComparison.Ordinal);
        }

        internal static string HandleText(Handle handle)
        {
            return unchecked((ulong)handle.Value).ToString(
                "X",
                CultureInfo.InvariantCulture);
        }

        internal static Point3d ToPoint(Binary64Vector value)
        {
            return new Point3d(
                Binary64.ParseBits(value.X),
                Binary64.ParseBits(value.Y),
                Binary64.ParseBits(value.Z));
        }

        internal static bool IsDirectModelspace(
            Transaction transaction,
            BlockTableRecord record,
            CadContainer expected)
        {
            if (!record.IsLayout)
            {
                return false;
            }

            Layout? layout = transaction.GetObject(
                record.LayoutId,
                OpenMode.ForRead,
                false) as Layout;
            return layout != null &&
                layout.ModelType &&
                expected.IsDirectModelspace &&
                string.Equals(
                    expected.LayoutHandle,
                    HandleText(layout.Handle),
                    StringComparison.Ordinal);
        }

        internal static int CountPhysicalRecordSlots(BlockTableRecord record)
        {
            int count = 0;
            // Autodesk documents IncludingErased as a read-only
            // BlockTableRecord view, not as an IEnumerable-valued property.
            // Enumerating that returned record is what retains erased slots.
            BlockTableRecord erasedInclusiveRecord = record.IncludingErased;
            foreach (ObjectId ignored in erasedInclusiveRecord)
            {
                if (count >= NativeCadProtocolV2.MaxPhysicalSlotCount)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "A Modelspace physical sequence exceeds the fixed limit.");
                }

                count++;
            }

            return count;
        }

        private static List<ContainerRecord> ReadContainers(
            Transaction transaction,
            BlockTable blockTable)
        {
            List<ContainerRecord> result = new List<ContainerRecord>();
            foreach (ObjectId id in blockTable)
            {
                // Fail before opening or scanning cap + 1. Empty records are
                // still v2 containers, so they cannot bypass this boundary.
                if (result.Count >= NativeCadProtocolV2.MaxGeometryContainers)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "The Autodesk drawing exceeds the v2 geometry container limit.");
                }

                if (id.IsNull || id.IsErased || !id.IsValid)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "A block-table owner is invalid.");
                }

                BlockTableRecord? record =
                    transaction.GetObject(id, OpenMode.ForRead, false) as BlockTableRecord;
                if (record == null || record.IsErased)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "A block-table owner is unavailable.");
                }

                CadContainer container;
                if (record.IsLayout)
                {
                    Layout? layout = transaction.GetObject(
                        record.LayoutId,
                        OpenMode.ForRead,
                        false) as Layout;
                    if (layout == null || layout.IsErased)
                    {
                        throw new CadCoreException(
                            CadCoreErrorCode.InvalidTarget,
                            "A layout record is unavailable.");
                    }

                    container = new CadContainer(
                        layout.ModelType
                            ? NativeSpaceKind.Modelspace
                            : NativeSpaceKind.Paperspace,
                        HandleText(layout.Handle),
                        null,
                        new string[0]);
                }
                else
                {
                    container = new CadContainer(
                        NativeSpaceKind.Block,
                        null,
                        HandleText(record.Handle),
                        new string[0]);
                }

                result.Add(new ContainerRecord(
                    record,
                    HandleText(record.Handle),
                    container,
                    CountPhysicalRecordSlots(record)));
            }

            if (result.Count == 0)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "The Autodesk drawing has no block-table owners.");
            }

            result.Sort(
                delegate(ContainerRecord left, ContainerRecord right)
                {
                    return string.CompareOrdinal(
                        left.Container.SortKey,
                        right.Container.SortKey);
                });
            return result;
        }

        private static void ExportContainerEntities(
            Transaction transaction,
            ContainerRecord container,
            IList<CadEntitySnapshot> destination,
            IList<object?> opaqueProviderState)
        {
            int sequence = 0;
            // BlockTableRecord's normal iterator omits erased ObjectIds.
            // Physical v2 sequence indices instead retain every slot so a
            // deletion cannot collapse later entity or marker positions.
            BlockTableRecord erasedInclusiveRecord =
                container.Record.IncludingErased;
            foreach (ObjectId id in erasedInclusiveRecord)
            {
                DBObject objectValue = transaction.GetObject(id, OpenMode.ForRead, true);
                if (objectValue == null)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "A block record contains an unreadable object.");
                }

                int currentSequence = sequence;
                sequence++;
                if (currentSequence > NativeCadProtocolV2.MaxGeometrySequenceIndex)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "A physical entity sequence exceeds the fixed limit.");
                }

                if (objectValue.IsErased || id.IsErased)
                {
                    // Preserve a physical deletion gap when the host exposes
                    // erased slots through the block-record iterator.
                    continue;
                }

                Entity? entity = objectValue as Entity;
                if (entity == null)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "A non-entity block-record object has no stable provider.");
                }

                CadEntitySnapshot exported;
                DBText? text = entity as DBText;
                if (text != null)
                {
                    exported = ExportDbText(
                        transaction,
                        text,
                        container,
                        currentSequence);
                }
                else
                {
                    Line? line = entity as Line;
                    if (line != null)
                    {
                        exported = ExportLine(line, container, currentSequence);
                    }
                    else
                    {
                        Polyline? polyline = entity as Polyline;
                        if (polyline != null)
                        {
                            exported = ExportPolyline(
                                polyline,
                                container,
                                currentSequence);
                        }
                        else
                        {
                            if (entity is ProxyEntity)
                            {
                                throw new CadCoreException(
                                    CadCoreErrorCode.InvalidTarget,
                                    "Proxy/custom Autodesk entities require a registered stable provider.");
                            }

                            OpaqueEntityProjection projection;
                            if (!OpaqueEntityProviderRegistry.TryProject(
                                    entity,
                                    transaction,
                                    out projection))
                            {
                                throw new CadCoreException(
                                    CadCoreErrorCode.InvalidTarget,
                                    "An unsupported Autodesk entity has no deterministic opaque provider.");
                            }

                            opaqueProviderState.Add(projection.ToWireValue());
                            exported = new CadEntitySnapshot(
                                HandleText(entity.Handle),
                                NativeEntityKind.Opaque,
                                container.OwnerHandle,
                                container.Container,
                                currentSequence,
                                null,
                                null,
                                null,
                                Binary64.ToBits(0d),
                                Binary64.ToBits(0d),
                                ZeroVector(),
                                ZeroBounds(),
                                new CadSegment[0],
                                UnsupportedOverlayEvidence());
                        }
                    }
                }

                destination.Add(exported);
            }

            if (sequence != container.PhysicalSlotCount)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "The erased-inclusive container slot count drifted during export.");
            }
        }

        private static CadEntitySnapshot ExportDbText(
            Transaction transaction,
            DBText text,
            ContainerRecord container,
            int sequence)
        {
            // The v2 exact geometry carrier intentionally has no lossy field
            // expression representation.  A field's evaluated TextString is
            // therefore never sufficient state for export or later editing.
            DbTextAlignmentPolicy.RequireBaseLeft(text);
            if (text.TextStyleId.IsNull || text.TextStyleId.IsErased ||
                !text.TextStyleId.IsValid)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "DBTEXT has no stable text style.");
            }

            TextStyleTableRecord? style = transaction.GetObject(
                text.TextStyleId,
                OpenMode.ForRead,
                false) as TextStyleTableRecord;
            if (style == null || style.IsErased ||
                string.IsNullOrEmpty(text.Layer) ||
                string.IsNullOrEmpty(text.TextString) ||
                string.IsNullOrEmpty(style.Name))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "DBTEXT lacks exact supported fields.");
            }

            Binary64Vector position = ToVector(text.Position);
            // Host glyph extents are driver-dependent. Logical DBTEXT bounds
            // intentionally bind only its canonical insertion point.
            CadBounds bounds = new CadBounds(position, position);
            return new CadEntitySnapshot(
                HandleText(text.Handle),
                NativeEntityKind.DbText,
                container.OwnerHandle,
                container.Container,
                sequence,
                text.Layer,
                text.TextString,
                style.Name,
                Binary64.ToBits(text.Height),
                Binary64.ToBits(text.Rotation),
                position,
                bounds,
                new CadSegment[0],
                OverlayEvidenceProviderRegistry.GetEvidence(
                    text,
                    container.Container));
        }

        private static CadEntitySnapshot ExportLine(
            Line line,
            ContainerRecord container,
            int sequence)
        {
            if (string.IsNullOrEmpty(line.Layer))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "LINE lacks a stable layer.");
            }

            Binary64Vector start = ToVector(line.StartPoint);
            Binary64Vector end = ToVector(line.EndPoint);
            return new CadEntitySnapshot(
                HandleText(line.Handle),
                NativeEntityKind.Line,
                container.OwnerHandle,
                container.Container,
                sequence,
                line.Layer,
                null,
                null,
                Binary64.ToBits(0d),
                Binary64.ToBits(0d),
                start,
                BoundsFor(new[] { start, end }),
                new[] { new CadSegment(start, end) },
                UnsupportedOverlayEvidence());
        }

        private static CadEntitySnapshot ExportPolyline(
            Polyline polyline,
            ContainerRecord container,
            int sequence)
        {
            if (string.IsNullOrEmpty(polyline.Layer) ||
                polyline.NumberOfVertices < 2 ||
                Binary64.ToBits(polyline.Normal.X) != Binary64.ToBits(0d) ||
                Binary64.ToBits(polyline.Normal.Y) != Binary64.ToBits(0d) ||
                Binary64.ToBits(polyline.Normal.Z) != Binary64.ToBits(1d))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Only planar simple LWPOLYLINE is supported.");
            }

            List<Binary64Vector> points = new List<Binary64Vector>();
            for (int index = 0; index < polyline.NumberOfVertices; index++)
            {
                if (Binary64.ToBits(polyline.GetBulgeAt(index)) != Binary64.ToBits(0d))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "Only zero-bulge LWPOLYLINE segments are supported.");
                }

                Point2d point = polyline.GetPoint2dAt(index);
                points.Add(new Binary64Vector(
                    Binary64.ToBits(point.X),
                    Binary64.ToBits(point.Y),
                    Binary64.ToBits(polyline.Elevation)));
            }

            List<CadSegment> segments = new List<CadSegment>();
            for (int index = 1; index < points.Count; index++)
            {
                segments.Add(new CadSegment(points[index - 1], points[index]));
            }

            if (polyline.Closed)
            {
                segments.Add(new CadSegment(points[points.Count - 1], points[0]));
            }

            return new CadEntitySnapshot(
                HandleText(polyline.Handle),
                NativeEntityKind.LwPolyline,
                container.OwnerHandle,
                container.Container,
                sequence,
                polyline.Layer,
                null,
                null,
                Binary64.ToBits(0d),
                Binary64.ToBits(0d),
                points[0],
                BoundsFor(points),
                segments,
                UnsupportedOverlayEvidence());
        }

        private static CadDocumentTables ReadTables(
            Database database,
            Transaction transaction,
            IReadOnlyList<object?> containerState,
            IReadOnlyList<object?> opaqueProviderState)
        {
            LayerTable? layerTable = transaction.GetObject(
                database.LayerTableId,
                OpenMode.ForRead,
                false) as LayerTable;
            TextStyleTable? styleTable = transaction.GetObject(
                database.TextStyleTableId,
                OpenMode.ForRead,
                false) as TextStyleTable;
            if (layerTable == null || styleTable == null)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "The Autodesk layer/style table is unavailable.");
            }

            Dictionary<string, string> layers = new Dictionary<string, string>(
                StringComparer.Ordinal);
            foreach (ObjectId id in layerTable)
            {
                LayerTableRecord? record = transaction.GetObject(
                    id,
                    OpenMode.ForRead,
                    false) as LayerTableRecord;
                if (record == null || record.IsErased || string.IsNullOrEmpty(record.Name))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "A layer table record is unavailable.");
                }

                layers.Add(record.Name, CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "handle", HandleText(record.Handle) },
                        { "name", record.Name },
                        { "provider", "autocad-layer/v1" },
                    }));
            }

            Dictionary<string, string> styles = new Dictionary<string, string>(
                StringComparer.Ordinal);
            foreach (ObjectId id in styleTable)
            {
                TextStyleTableRecord? record = transaction.GetObject(
                    id,
                    OpenMode.ForRead,
                    false) as TextStyleTableRecord;
                if (record == null || record.IsErased || string.IsNullOrEmpty(record.Name))
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "A text style table record is unavailable.");
                }

                styles.Add(record.Name, CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "handle", HandleText(record.Handle) },
                        { "name", record.Name },
                        { "provider", "autocad-text-style/v1" },
                    }));
            }

            List<object?> layerState = ToTokenMap(layers);
            List<object?> styleState = ToTokenMap(styles);
            string tableState = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "layers", layerState },
                    { "opaque_provider_records", opaqueProviderState },
                    { "styles", styleState },
                });
            string layoutState = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "containers", containerState },
                });
            string blockState = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "containers", containerState },
                    { "provider", "autocad-block-table/v1" },
                });
            return new CadDocumentTables(
                tableState,
                layoutState,
                blockState,
                null,
                null,
                layers,
                styles);
        }

        private static Binary64Vector ToVector(Point3d point)
        {
            return new Binary64Vector(
                Binary64.ToBits(point.X),
                Binary64.ToBits(point.Y),
                Binary64.ToBits(point.Z));
        }

        private static Binary64Vector ZeroVector()
        {
            return new Binary64Vector(
                Binary64.ToBits(0d),
                Binary64.ToBits(0d),
                Binary64.ToBits(0d));
        }

        private static CadBounds ZeroBounds()
        {
            Binary64Vector zero = ZeroVector();
            return new CadBounds(zero, zero);
        }

        private static CadBounds BoundsFor(IReadOnlyList<Binary64Vector> points)
        {
            if (points == null || points.Count == 0)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "A supported entity has no logical points.");
            }

            double minimumX = Binary64.ParseBits(points[0].X);
            double minimumY = Binary64.ParseBits(points[0].Y);
            double minimumZ = Binary64.ParseBits(points[0].Z);
            double maximumX = minimumX;
            double maximumY = minimumY;
            double maximumZ = minimumZ;
            for (int index = 1; index < points.Count; index++)
            {
                minimumX = Math.Min(minimumX, Binary64.ParseBits(points[index].X));
                minimumY = Math.Min(minimumY, Binary64.ParseBits(points[index].Y));
                minimumZ = Math.Min(minimumZ, Binary64.ParseBits(points[index].Z));
                maximumX = Math.Max(maximumX, Binary64.ParseBits(points[index].X));
                maximumY = Math.Max(maximumY, Binary64.ParseBits(points[index].Y));
                maximumZ = Math.Max(maximumZ, Binary64.ParseBits(points[index].Z));
            }

            return new CadBounds(
                new Binary64Vector(
                    Binary64.ToBits(minimumX),
                    Binary64.ToBits(minimumY),
                    Binary64.ToBits(minimumZ)),
                new Binary64Vector(
                    Binary64.ToBits(maximumX),
                    Binary64.ToBits(maximumY),
                    Binary64.ToBits(maximumZ)));
        }

        private static OverlayEvidence UnsupportedOverlayEvidence()
        {
            return new OverlayEvidence(false, false, false, false, true);
        }

        private static List<object?> ToWireArray(IReadOnlyList<string> values)
        {
            List<object?> result = new List<object?>();
            for (int index = 0; index < values.Count; index++)
            {
                result.Add(values[index]);
            }

            return result;
        }

        private static List<object?> ToTokenMap(
            IReadOnlyDictionary<string, string> values)
        {
            List<string> keys = new List<string>(values.Keys);
            keys.Sort(StringComparer.Ordinal);
            List<object?> result = new List<object?>();
            for (int index = 0; index < keys.Count; index++)
            {
                result.Add(new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "name", keys[index] },
                    { "fingerprint", values[keys[index]] },
                });
            }

            return result;
        }

        private sealed class ContainerRecord
        {
            internal ContainerRecord(
                BlockTableRecord record,
                string ownerHandle,
                CadContainer container,
                int physicalSlotCount)
            {
                Record = record;
                OwnerHandle = ownerHandle;
                Container = container;
                PhysicalSlotCount = physicalSlotCount;
            }

            internal BlockTableRecord Record { get; private set; }

            internal string OwnerHandle { get; private set; }

            internal CadContainer Container { get; private set; }

            internal int PhysicalSlotCount { get; private set; }

            internal Dictionary<string, object?> ToStableState()
            {
                return new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "container", Container.ToKeyWireValue() },
                    { "owner_handle", OwnerHandle },
                };
            }
        }
    }

    /// <summary>Explicit stable opaque-object projection; no runtime type text is fingerprinted.</summary>
    internal sealed class OpaqueEntityProjection
    {
        internal OpaqueEntityProjection(string providerId, string stableDigest)
        {
            CanonicalJson.RequireNfcString(providerId, nameof(providerId));
            CanonicalJson.RequireSha256(stableDigest, nameof(stableDigest));
            ProviderId = providerId;
            StableDigest = stableDigest;
        }

        internal string ProviderId { get; private set; }

        internal string StableDigest { get; private set; }

        internal Dictionary<string, object?> ToWireValue()
        {
            return new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                { "provider_id", ProviderId },
                { "stable_digest", StableDigest },
            };
        }
    }

    /// <summary>
    /// Provider registration is deliberately empty in public source. A
    /// licensed operator may compile a reviewed provider, but missing object
    /// enablers and unknown objects always fail before edit scope is admitted.
    /// </summary>
    internal static class OpaqueEntityProviderRegistry
    {
        internal static bool TryProject(
            Entity entity,
            Transaction transaction,
            out OpaqueEntityProjection projection)
        {
            projection = null!;
            return false;
        }
    }

    /// <summary>Default overlay evidence is conservative; semantic proof cannot be guessed from CAD text.</summary>
    internal static class OverlayEvidenceProviderRegistry
    {
        internal static OverlayEvidence GetEvidence(
            DBText text,
            CadContainer container)
        {
            return new OverlayEvidence(false, false, false, false, true);
        }
    }
}
