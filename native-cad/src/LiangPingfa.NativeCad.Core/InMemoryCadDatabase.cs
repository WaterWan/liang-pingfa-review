// SPDX-License-Identifier: MIT
// Generated in-memory transaction database with no vendor runtime dependency.

using System;
using System.Collections.Generic;
using LiangPingfa.NativeCad.Protocol;

namespace LiangPingfa.NativeCad.Core
{
    /// <summary>Fault injection points used exclusively by generated core tests.</summary>
    public enum CadFaultPoint
    {
        /// <summary>Before a transaction is admitted.</summary>
        BeginTransaction,

        /// <summary>
        /// After preflight but before a transaction captures its staged
        /// source state. This models an external host change racing the
        /// executor's preflight read.
        /// </summary>
        BeforeTransactionSnapshot,

        /// <summary>Before one staged entity mutation.</summary>
        BeforeMutation,

        /// <summary>After one staged entity mutation and before readback checks.</summary>
        AfterMutation,

        /// <summary>Immediately before the one commit attempt.</summary>
        BeforeCommit,

        /// <summary>Commit failure point; no staged state is published.</summary>
        Commit,

        /// <summary>Generated save/reopen clone failure point.</summary>
        SaveReopen,

        /// <summary>After commit, before the private copy is saved.</summary>
        Save,

        /// <summary>After save, before a fresh private copy is reopened.</summary>
        Reopen,
    }

    /// <summary>Optional generated fault controller; it has no production-host analogue.</summary>
    public sealed class CadFaultInjector
    {
        private readonly HashSet<CadFaultPoint> failures = new HashSet<CadFaultPoint>();

        /// <summary>Optional test callback that can simulate an unplanned staged host drift.</summary>
        public Action<CadFaultPoint, MutableCadDocument?>? Callback { get; set; }

        /// <summary>
        /// Optional post-save transform used only to test a malicious or
        /// defective reopened host state. Production adapters must return a
        /// fresh readback without a transform hook.
        /// </summary>
        public Func<CadDocumentSnapshot, CadDocumentSnapshot>? ReopenedSnapshotTransform
        {
            get;
            set;
        }

        /// <summary>
        /// Optional external-state transform applied after preflight and
        /// immediately before a generated transaction takes its private
        /// staged copy. It exists only to prove stale-state rejection.
        /// </summary>
        public Func<CadDocumentSnapshot, CadDocumentSnapshot>? BeforeTransactionSnapshotTransform
        {
            get;
            set;
        }

        /// <summary>Configures a point to fail closed.</summary>
        public void FailAt(CadFaultPoint point)
        {
            failures.Add(point);
        }

        /// <summary>Removes all configured failures and callbacks.</summary>
        public void Clear()
        {
            failures.Clear();
            Callback = null;
            ReopenedSnapshotTransform = null;
            BeforeTransactionSnapshotTransform = null;
        }

        internal void Reach(CadFaultPoint point, MutableCadDocument? document)
        {
            Action<CadFaultPoint, MutableCadDocument?>? callback = Callback;
            if (callback != null)
            {
                callback(point, document);
            }

            if (failures.Contains(point))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.FaultInjected,
                    "Generated in-memory fault reached: " + point + ".");
            }
        }
    }

    /// <summary>Read/write database interface deliberately independent of a proprietary host.</summary>
    public interface ICadDatabase
    {
        /// <summary>Returns the current immutable snapshot.</summary>
        CadDocumentSnapshot ReadSnapshot();

        /// <summary>Begins one isolated staged transaction.</summary>
        ICadTransaction BeginTransaction();

        /// <summary>
        /// Saves the committed private copy and returns a freshly reopened
        /// database. A future host adapter may implement this as SaveAs to a
        /// private file followed by Database.ReadDwgFile or a separate
        /// readback process.
        /// </summary>
        ICadDatabase SaveAndReopen(
            FinalOutputConstraintsV2 finalOutputConstraints);
    }

    /// <summary>One staged transaction interface used by the manifest executor.</summary>
    public interface ICadTransaction : IDisposable
    {
        /// <summary>
        /// Returns whether this transaction still owns active staged state.
        /// Callers must abort only an active transaction and must always
        /// dispose it, including after a successful commit.
        /// </summary>
        bool IsActive { get; }

        /// <summary>
        /// Captures one immutable view from this transaction's active
        /// consistency boundary. A future AutoCAD adapter must build this
        /// export while its DocumentLock and host Transaction remain active;
        /// it must never reuse a database snapshot captured before the
        /// transaction started.
        /// </summary>
        CadDocumentSnapshot CaptureSnapshot();

        /// <summary>
        /// Replaces one target only if the entire staged state and that
        /// target still exactly match the supplied transaction-local
        /// snapshots at the instant immediately before mutation.
        /// </summary>
        void ReplaceExact(
            CadDocumentSnapshot expectedState,
            CadEntitySnapshot expectedTarget,
            CadEntitySnapshot replacement);

        /// <summary>
        /// Erases one target only if the entire staged state and that target
        /// still exactly match the supplied transaction-local snapshots at
        /// the instant immediately before mutation.
        /// </summary>
        void EraseExact(
            CadDocumentSnapshot expectedState,
            CadEntitySnapshot expectedTarget);

        /// <summary>
        /// Appends one policy-derived marker only if the entire staged state
        /// still exactly matches the supplied transaction-local snapshot at
        /// the instant immediately before mutation.  The host/allocator
        /// returns the authoritative assigned entity record; callers never
        /// predict its handle from exported entities.
        /// </summary>
        CadEntitySnapshot AppendExact(
            CadDocumentSnapshot expectedState,
            MarkerAppendRequestV2 request);

        /// <summary>Runs the final pre-commit hook before exact staged readback validation.</summary>
        void PrepareCommit();

        /// <summary>
        /// Commits staged state exactly once only if it still matches the
        /// verified expected state. This closes the final gap between staged
        /// readback and the irreversible host commit.
        /// </summary>
        void CommitExact(CadDocumentSnapshot expectedState);

        /// <summary>Aborts staged state and leaves the database unchanged.</summary>
        void Abort();
    }

    /// <summary>
    /// Supplies actual append handles for the generated database.  It models
    /// a host handseed/allocator and deliberately accepts independent
    /// reserved non-entity handles, so no caller infers the next handle from
    /// the maximum exported entity.
    /// </summary>
    public interface IActualCadHandleAllocator
    {
        /// <summary>Allocates one canonical unoccupied actual handle.</summary>
        string Allocate(IReadOnlyCollection<string> occupiedHandles);
    }

    /// <summary>
    /// Testable sequential allocator whose starting handseed and retired or
    /// non-entity reservations are explicit allocator state, never inferred
    /// from geometry exports.
    /// </summary>
    public sealed class SequentialActualCadHandleAllocator :
        IActualCadHandleAllocator
    {
        private readonly HashSet<string> reserved =
            new HashSet<string>(StringComparer.Ordinal);
        private ulong next;

        /// <summary>Creates an allocator at an explicit host-provided seed.</summary>
        public SequentialActualCadHandleAllocator(
            ulong initialHandle = 0x100UL,
            IEnumerable<string>? reservedHandles = null)
        {
            next = initialHandle;
            if (reservedHandles == null)
            {
                return;
            }

            foreach (string handle in reservedHandles)
            {
                CadHandle.Require(handle, nameof(reservedHandles));
                reserved.Add(handle);
            }
        }

        /// <inheritdoc />
        public string Allocate(IReadOnlyCollection<string> occupiedHandles)
        {
            if (occupiedHandles == null)
            {
                throw new ArgumentNullException(nameof(occupiedHandles));
            }

            HashSet<string> occupied = new HashSet<string>(
                occupiedHandles,
                StringComparer.Ordinal);
            while (true)
            {
                if (next == ulong.MaxValue)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.InvalidTarget,
                        "The actual marker handle allocator is exhausted.");
                }

                string candidate = next.ToString(
                    "X",
                    System.Globalization.CultureInfo.InvariantCulture);
                next++;
                if (occupied.Contains(candidate) || reserved.Contains(candidate))
                {
                    continue;
                }

                reserved.Add(candidate);
                return candidate;
            }
        }
    }

    /// <summary>Mutable private copy behind one transaction; never a drawing-file model.</summary>
    public sealed class MutableCadDocument
    {
        private readonly string databaseInstanceFingerprint;
        private string revisionFingerprint;
        private readonly List<string> owners;
        private readonly NativeSourceBindingV2 source;
        private readonly NativeGeometryBindingContextV2 bindingContext;
        private readonly List<CadContainerPhysicalSlots> containers;
        private readonly List<CadEntitySnapshot> entities;
        private CadDocumentTables tables;

        internal MutableCadDocument(CadDocumentSnapshot sourceSnapshot)
        {
            if (sourceSnapshot == null)
            {
                throw new ArgumentNullException(nameof(sourceSnapshot));
            }

            databaseInstanceFingerprint = sourceSnapshot.DatabaseInstanceFingerprint;
            revisionFingerprint = sourceSnapshot.RevisionFingerprint;
            owners = new List<string>(sourceSnapshot.Owners);
            source = sourceSnapshot.Source;
            bindingContext = sourceSnapshot.BindingContext;
            containers = new List<CadContainerPhysicalSlots>(
                sourceSnapshot.Containers);
            entities = new List<CadEntitySnapshot>(sourceSnapshot.Entities);
            tables = sourceSnapshot.Tables;
        }

        /// <summary>Returns a staged record by exact handle.</summary>
        public CadEntitySnapshot? FindByHandle(string handle)
        {
            for (int index = 0; index < entities.Count; index++)
            {
                if (string.Equals(entities[index].Handle, handle, StringComparison.Ordinal))
                {
                    return entities[index];
                }
            }

            return null;
        }

        /// <summary>
        /// Returns every handle that is occupied by the staged model,
        /// including declared owners that are not exported entities.  Actual
        /// marker allocation uses this collision guard rather than deriving a
        /// value from entity ordering or the maximum entity handle.
        /// </summary>
        internal IReadOnlyCollection<string> OccupiedHandles()
        {
            HashSet<string> handles = new HashSet<string>(
                owners,
                StringComparer.Ordinal);
            for (int index = 0; index < entities.Count; index++)
            {
                handles.Add(entities[index].Handle);
            }

            return handles;
        }

        /// <summary>Replaces an existing exact handle without creating a record.</summary>
        public void Replace(CadEntitySnapshot entity)
        {
            if (entity == null)
            {
                throw new ArgumentNullException(nameof(entity));
            }

            for (int index = 0; index < entities.Count; index++)
            {
                if (string.Equals(entities[index].Handle, entity.Handle, StringComparison.Ordinal))
                {
                    entities[index] = entity;
                    return;
                }
            }

            throw new CadCoreException(CadCoreErrorCode.InvalidTarget, "Staged replacement target is absent.");
        }

        /// <summary>Removes an exact handle without renumbering surviving records.</summary>
        public void Erase(string handle)
        {
            for (int index = 0; index < entities.Count; index++)
            {
                if (string.Equals(entities[index].Handle, handle, StringComparison.Ordinal))
                {
                    entities.RemoveAt(index);
                    return;
                }
            }

            throw new CadCoreException(CadCoreErrorCode.InvalidTarget, "Staged erase target is absent.");
        }

        /// <summary>Appends a unique record and restores canonical global record ordering.</summary>
        public void Append(CadEntitySnapshot entity)
        {
            if (entity == null)
            {
                throw new ArgumentNullException(nameof(entity));
            }

            if (FindByHandle(entity.Handle) != null)
            {
                throw new CadCoreException(CadCoreErrorCode.InvalidTarget, "Generated handle already exists.");
            }

            int containerIndex = FindContainerIndex(entity.Container);
            if (containerIndex < 0)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Generated marker container is absent.");
            }

            CadContainerPhysicalSlots physical = containers[containerIndex];
            if (!string.Equals(
                    physical.OwnerHandle,
                    entity.OwnerHandle,
                    StringComparison.Ordinal) ||
                entity.SequenceIndex != physical.PhysicalSlotCount)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Generated marker does not append at the exact physical slot.");
            }

            entities.Add(entity);
            entities.Sort(CadDocumentSnapshot.CompareEntityOrder);
            containers[containerIndex] = physical.WithPhysicalSlotCount(
                physical.PhysicalSlotCount + 1);
        }

        private int FindContainerIndex(CadContainer container)
        {
            for (int index = 0; index < containers.Count; index++)
            {
                if (containers[index].Container.Equals(container))
                {
                    return index;
                }
            }

            return -1;
        }

        /// <summary>
        /// Changes table state only for a generated fault test. A real adapter
        /// must not call this hook; the verifier is expected to reject it.
        /// </summary>
        public void ReplaceTablesForFaultInjection(CadDocumentTables replacement)
        {
            tables = replacement ?? throw new ArgumentNullException(nameof(replacement));
        }

        /// <summary>
        /// Changes owner state only for a generated fault test. Production
        /// operations have no owner mutation capability and exact readback
        /// must reject every owner addition, removal, reorder, or replacement.
        /// </summary>
        public void ReplaceOwnersForFaultInjection(IEnumerable<string> replacement)
        {
            if (replacement == null)
            {
                throw new ArgumentNullException(nameof(replacement));
            }

            owners.Clear();
            foreach (string owner in replacement)
            {
                owners.Add(owner);
            }
        }

        /// <summary>
        /// Changes only physical slot state for generated readback fault
        /// tests. Normal mutation paths retain counts on replace/erase and
        /// advance exactly one count on append.
        /// </summary>
        public void ReplaceContainersForFaultInjection(
            IEnumerable<CadContainerPhysicalSlots> replacement)
        {
            if (replacement == null)
            {
                throw new ArgumentNullException(nameof(replacement));
            }

            containers.Clear();
            foreach (CadContainerPhysicalSlots container in replacement)
            {
                containers.Add(container);
            }
        }

        /// <summary>
        /// Deliberately reorders records only for a generated fault test.
        /// Snapshot reconstruction will fail closed if the sequence becomes
        /// noncanonical, which models a host order drift.
        /// </summary>
        public void ReverseOrderForFaultInjection()
        {
            entities.Reverse();
        }

        /// <summary>
        /// Restores canonical ordering after a generated sequence-index drift.
        /// This is test-only: normal v1 operations cannot alter record order.
        /// </summary>
        public void SortForFaultInjection()
        {
            entities.Sort(CadDocumentSnapshot.CompareEntityOrder);
        }

        /// <summary>
        /// Replaces the staged revision token only for stale-state fault
        /// tests. Normal v1 operations cannot change a revision before commit.
        /// </summary>
        public void ReplaceRevisionForFaultInjection(string replacement)
        {
            CanonicalJson.RequireSha256(replacement, nameof(replacement));
            revisionFingerprint = replacement;
        }

        /// <summary>Returns a fully validated immutable staged snapshot.</summary>
        public CadDocumentSnapshot ToSnapshot()
        {
            return new CadDocumentSnapshot(
                databaseInstanceFingerprint,
                revisionFingerprint,
                owners,
                containers,
                entities,
                tables,
                source,
                bindingContext);
        }
    }

    /// <summary>Generated transaction implementation with one active writer and commit-once semantics.</summary>
    public sealed class InMemoryCadDatabase : ICadDatabase
    {
        private readonly object gate = new object();
        private readonly CadFaultInjector faults;
        private readonly IActualCadHandleAllocator markerHandleAllocator;
        private CadDocumentSnapshot snapshot;
        private bool transactionActive;
        private InMemoryCadTransaction? activeTransaction;
        private int beginTransactionCount;
        private int commitCount;
        private int abortCount;
        private int saveReopenCount;

        /// <summary>Creates a database from generated immutable state.</summary>
        public InMemoryCadDatabase(
            CadDocumentSnapshot initialSnapshot,
            CadFaultInjector? faultInjector = null,
            IActualCadHandleAllocator? actualHandleAllocator = null)
        {
            snapshot = initialSnapshot ?? throw new ArgumentNullException(nameof(initialSnapshot));
            faults = faultInjector ?? new CadFaultInjector();
            markerHandleAllocator = actualHandleAllocator ??
                new SequentialActualCadHandleAllocator();
        }

        /// <summary>Number of successfully published commits.</summary>
        public int CommitCount
        {
            get
            {
                lock (gate)
                {
                    return commitCount;
                }
            }
        }

        /// <summary>Number of transaction admissions attempted after preflight.</summary>
        public int BeginTransactionCount
        {
            get
            {
                lock (gate)
                {
                    return beginTransactionCount;
                }
            }
        }

        /// <summary>Number of explicit save/reopen boundary invocations.</summary>
        public int SaveReopenCount
        {
            get
            {
                lock (gate)
                {
                    return saveReopenCount;
                }
            }
        }

        /// <summary>Number of active staged transactions explicitly aborted.</summary>
        public int AbortCount
        {
            get
            {
                lock (gate)
                {
                    return abortCount;
                }
            }
        }

        /// <summary>Shared generated fault injector.</summary>
        public CadFaultInjector Faults
        {
            get { return faults; }
        }

        /// <summary>Returns an immutable current snapshot.</summary>
        public CadDocumentSnapshot ReadSnapshot()
        {
            lock (gate)
            {
                return snapshot;
            }
        }

        /// <summary>Begins exactly one active transaction.</summary>
        public ICadTransaction BeginTransaction()
        {
            lock (gate)
            {
                faults.Reach(CadFaultPoint.BeginTransaction, null);
                if (transactionActive)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.TransactionConflict,
                        "Only one generated transaction may be active.");
                }

                beginTransactionCount++;

                // Model a host change that occurs after the executor's
                // out-of-transaction preflight read but before this
                // transaction owns its staged view. The executor must capture
                // and reject this exact state before any mutation.
                faults.Reach(CadFaultPoint.BeforeTransactionSnapshot, null);
                CadDocumentSnapshot transactionSnapshot = snapshot;
                Func<CadDocumentSnapshot, CadDocumentSnapshot>? transform =
                    faults.BeforeTransactionSnapshotTransform;
                if (transform != null)
                {
                    transactionSnapshot = transform(transactionSnapshot) ??
                        throw new CadCoreException(
                            CadCoreErrorCode.FaultInjected,
                            "Generated pre-transaction state transform returned no snapshot.");
                    snapshot = transactionSnapshot;
                }

                transactionActive = true;
                activeTransaction = new InMemoryCadTransaction(
                    this,
                    new MutableCadDocument(transactionSnapshot));
                return activeTransaction;
            }
        }

        /// <summary>
        /// Simulates saving the private committed state and reopening it as a
        /// new generated database. It does not access a file system, but keeps
        /// save and reopen failures distinct for the core's host-neutral
        /// boundary semantics.
        /// </summary>
        public ICadDatabase SaveAndReopen(
            FinalOutputConstraintsV2 finalOutputConstraints)
        {
            if (finalOutputConstraints == null)
            {
                throw new ArgumentNullException(
                    nameof(finalOutputConstraints));
            }

            lock (gate)
            {
                if (transactionActive)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.TransactionConflict,
                        "Cannot save/reopen while a generated transaction is active.");
                }

                saveReopenCount++;
                try
                {
                    faults.Reach(CadFaultPoint.Save, null);
                    faults.Reach(CadFaultPoint.SaveReopen, null);
                }
                catch (CadCoreException exception)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.SaveFailed,
                        "Committed generated state could not be saved: " +
                        exception.Message);
                }

                NativeSourceBindingV2 actualFinalBinding =
                    CreateActualFinalBinding(snapshot, finalOutputConstraints);
                CadDocumentSnapshot clone = new CadDocumentSnapshot(
                    snapshot.DatabaseInstanceFingerprint,
                    snapshot.RevisionFingerprint,
                    snapshot.Owners,
                    snapshot.Containers,
                    snapshot.Entities,
                    snapshot.Tables,
                    actualFinalBinding,
                    snapshot.BindingContext);
                try
                {
                    faults.Reach(CadFaultPoint.Reopen, null);
                    Func<CadDocumentSnapshot, CadDocumentSnapshot>? transform =
                        faults.ReopenedSnapshotTransform;
                    if (transform != null)
                    {
                        clone = transform(clone) ??
                            throw new CadCoreException(
                                CadCoreErrorCode.ReopenFailed,
                                "Generated reopened snapshot is absent.");
                    }
                }
                catch (CadCoreException exception)
                {
                    if (exception.Code == CadCoreErrorCode.ReopenFailed)
                    {
                        throw;
                    }

                    throw new CadCoreException(
                        CadCoreErrorCode.ReopenFailed,
                        "Saved generated state could not be reopened: " +
                        exception.Message);
                }
                catch (Exception exception)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.ReopenFailed,
                        "Saved generated state could not be reopened: " +
                        exception.Message);
                }

                return new InMemoryCadDatabase(clone);
            }
        }

        /// <summary>
        /// Compatibility helper for generated callers. The core itself uses
        /// <see cref="SaveAndReopen"/> through <see cref="ICadDatabase"/>.
        /// </summary>
        public InMemoryCadDatabase SaveReopenClone()
        {
            InMemoryCadDatabase? reopened = SaveAndReopen(
                FinalOutputConstraintsV2.ForGeneratedSource(snapshot.Source))
                as InMemoryCadDatabase;
            if (reopened == null)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.ReopenFailed,
                    "Generated save/reopen did not return an in-memory database.");
            }

            return reopened;
        }

        private static NativeSourceBindingV2 CreateActualFinalBinding(
            CadDocumentSnapshot committed,
            FinalOutputConstraintsV2 constraints)
        {
            NativeSourceBindingV2 prewrite = committed.Source;
            string actualSha256 = CanonicalJson.Sha256Hex(
                new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    { "prewrite_sha256", prewrite.Sha256 },
                    { "committed_revision", committed.RevisionFingerprint },
                    { "entities", committed.Entities.Count },
                });
            long actualSize = Math.Max(6L, prewrite.ByteSize + 1L);
            if (actualSize > constraints.MaxByteSize)
            {
                actualSize = constraints.MaxByteSize;
            }
            string actualIdentity = constraints.FileIdentityTransitionPolicy ==
                FileIdentityTransitionPolicyV2.SameIdentityRequired
                ? prewrite.FileIdentityFingerprint
                : CanonicalJson.Sha256Hex(
                    new Dictionary<string, object?>(StringComparer.Ordinal)
                    {
                        { "prewrite_identity", prewrite.FileIdentityFingerprint },
                        { "committed_revision", committed.RevisionFingerprint },
                    });
            NativeSourceBindingV2 actual = new NativeSourceBindingV2(
                actualSha256,
                actualSize,
                constraints.AuthorizedPrivatePathFingerprint,
                actualIdentity,
                constraints.RequiredDwgHeaderSignature);
            constraints.RequireActual(prewrite, actual);
            return actual;
        }

        internal void BeforeMutation(MutableCadDocument document)
        {
            faults.Reach(CadFaultPoint.BeforeMutation, document);
        }

        internal void AfterMutation(MutableCadDocument document)
        {
            faults.Reach(CadFaultPoint.AfterMutation, document);
        }

        internal string AllocateActualMarkerHandle(MutableCadDocument document)
        {
            if (document == null)
            {
                throw new ArgumentNullException(nameof(document));
            }

            lock (gate)
            {
                return markerHandleAllocator.Allocate(document.OccupiedHandles());
            }
        }

        internal void PrepareCommit(InMemoryCadTransaction transaction)
        {
            lock (gate)
            {
                EnsureActive(transaction);
                faults.Reach(CadFaultPoint.BeforeCommit, transaction.Document);
            }
        }

        internal void Commit(
            InMemoryCadTransaction transaction,
            CadDocumentSnapshot expectedState)
        {
            lock (gate)
            {
                EnsureActive(transaction);

                try
                {
                    faults.Reach(CadFaultPoint.Commit, transaction.Document);
                }
                catch (CadCoreException exception)
                {
                    throw new CadCoreException(CadCoreErrorCode.CommitFailed, exception.Message);
                }

                CadDocumentSnapshot staged;
                try
                {
                    staged = transaction.Document.ToSnapshot();
                    RequireExactState(expectedState, staged);
                }
                catch (CanonicalJsonException exception)
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.StalePrecondition,
                        "Staged state changed before commit: " + exception.Message);
                }

                string nextRevision = staged.DeriveNextRevision();
                snapshot = staged.WithRevision(nextRevision);
                transactionActive = false;
                activeTransaction = null;
                commitCount++;
            }
        }

        internal void Abort(InMemoryCadTransaction transaction)
        {
            lock (gate)
            {
                if (!ReferenceEquals(transaction, activeTransaction))
                {
                    return;
                }

                transactionActive = false;
                activeTransaction = null;
                abortCount++;
            }
        }

        private void EnsureActive(InMemoryCadTransaction transaction)
        {
            if (!transactionActive || !ReferenceEquals(transaction, activeTransaction))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.TransactionFailure,
                    "Generated transaction is no longer active.");
            }
        }

        /// <summary>
        /// Compares complete canonical exports rather than a revision token
        /// alone. This binds owners, ordered records and containers, protected
        /// table state, opaque records, source/binding values, and every
        /// document digest at the conditional-mutation boundary.
        /// </summary>
        internal static void RequireExactState(
            CadDocumentSnapshot expected,
            CadDocumentSnapshot observed)
        {
            if (expected == null)
            {
                throw new ArgumentNullException(nameof(expected));
            }

            if (observed == null)
            {
                throw new ArgumentNullException(nameof(observed));
            }

            byte[] expectedJson = ExactCadExporter.Export(expected).ToCanonicalJsonUtf8();
            byte[] observedJson = ExactCadExporter.Export(observed).ToCanonicalJsonUtf8();
            if (expectedJson.Length != observedJson.Length)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Staged state no longer matches its exact prewrite snapshot.");
            }

            for (int index = 0; index < expectedJson.Length; index++)
            {
                if (expectedJson[index] != observedJson[index])
                {
                    throw new CadCoreException(
                        CadCoreErrorCode.StalePrecondition,
                        "Staged state no longer matches its exact prewrite snapshot.");
                }
            }
        }
    }

    /// <summary>One staged in-memory transaction and its commit/abort lifecycle.</summary>
    public sealed class InMemoryCadTransaction : ICadTransaction
    {
        private readonly InMemoryCadDatabase database;
        private bool completed;
        private bool disposed;
        private bool prepared;

        internal InMemoryCadTransaction(InMemoryCadDatabase owner, MutableCadDocument document)
        {
            database = owner ?? throw new ArgumentNullException(nameof(owner));
            Document = document ?? throw new ArgumentNullException(nameof(document));
        }

        internal MutableCadDocument Document { get; private set; }

        /// <inheritdoc />
        public bool IsActive
        {
            get { return !completed && !disposed; }
        }

        /// <summary>Captures immutable staged state under this transaction.</summary>
        public CadDocumentSnapshot CaptureSnapshot()
        {
            EnsureOpen();
            return Document.ToSnapshot();
        }

        /// <summary>Stages one exact conditional replacement.</summary>
        public void ReplaceExact(
            CadDocumentSnapshot expectedState,
            CadEntitySnapshot expectedTarget,
            CadEntitySnapshot replacement)
        {
            if (expectedTarget == null)
            {
                throw new ArgumentNullException(nameof(expectedTarget));
            }

            if (replacement == null)
            {
                throw new ArgumentNullException(nameof(replacement));
            }

            if (!string.Equals(
                    expectedTarget.Handle,
                    replacement.Handle,
                    StringComparison.Ordinal))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.InvalidTarget,
                    "Conditional replacement changed its target handle.");
            }

            EnsureOpen();
            database.BeforeMutation(Document);
            RequireExactCurrentState(expectedState);
            RequireExactCurrentTarget(expectedTarget);
            Document.Replace(replacement);
            database.AfterMutation(Document);
        }

        /// <summary>Stages one exact conditional erase.</summary>
        public void EraseExact(
            CadDocumentSnapshot expectedState,
            CadEntitySnapshot expectedTarget)
        {
            if (expectedTarget == null)
            {
                throw new ArgumentNullException(nameof(expectedTarget));
            }

            EnsureOpen();
            database.BeforeMutation(Document);
            RequireExactCurrentState(expectedState);
            RequireExactCurrentTarget(expectedTarget);
            Document.Erase(expectedTarget.Handle);
            database.AfterMutation(Document);
        }

        /// <summary>Stages one exact conditional append.</summary>
        public CadEntitySnapshot AppendExact(
            CadDocumentSnapshot expectedState,
            MarkerAppendRequestV2 request)
        {
            if (request == null)
            {
                throw new ArgumentNullException(nameof(request));
            }

            EnsureOpen();
            database.BeforeMutation(Document);
            RequireExactCurrentState(expectedState);
            string actualHandle = database.AllocateActualMarkerHandle(Document);
            CadEntitySnapshot entity = request.WithActualHandle(actualHandle);
            Document.Append(entity);
            database.AfterMutation(Document);
            return entity;
        }

        /// <summary>Runs pre-commit fault hooks before exact verification.</summary>
        public void PrepareCommit()
        {
            EnsureOpen();
            database.PrepareCommit(this);
            prepared = true;
        }

        /// <summary>Publishes the exactly verified staged state once.</summary>
        public void CommitExact(CadDocumentSnapshot expectedState)
        {
            if (expectedState == null)
            {
                throw new ArgumentNullException(nameof(expectedState));
            }

            EnsureOpen();
            try
            {
                if (!prepared)
                {
                    PrepareCommit();
                }

                database.Commit(this, expectedState);
                completed = true;
            }
            catch (CadCoreException)
            {
                throw;
            }
            catch (Exception exception)
            {
                throw new CadCoreException(CadCoreErrorCode.CommitFailed, exception.Message);
            }
        }

        /// <summary>Discards staged state.</summary>
        public void Abort()
        {
            if (completed)
            {
                return;
            }

            database.Abort(this);
            completed = true;
        }

        /// <summary>Aborts uncommitted state on disposal.</summary>
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
                disposed = true;
            }
        }

        private void RequireExactCurrentState(CadDocumentSnapshot expectedState)
        {
            if (expectedState == null)
            {
                throw new ArgumentNullException(nameof(expectedState));
            }

            try
            {
                InMemoryCadDatabase.RequireExactState(
                    expectedState,
                    Document.ToSnapshot());
            }
            catch (CadCoreException)
            {
                throw;
            }
            catch (CanonicalJsonException exception)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Staged state cannot be revalidated before mutation: " +
                    exception.Message);
            }
        }

        private void RequireExactCurrentTarget(CadEntitySnapshot expectedTarget)
        {
            CadEntitySnapshot? current = Document.FindByHandle(expectedTarget.Handle);
            if (current == null || !expectedTarget.ExactlyEquals(current))
            {
                throw new CadCoreException(
                    CadCoreErrorCode.StalePrecondition,
                    "Staged target no longer has its exact precondition.");
            }
        }

        private void EnsureOpen()
        {
            if (completed || disposed)
            {
                throw new CadCoreException(
                    CadCoreErrorCode.TransactionFailure,
                    "Generated transaction is already completed.");
            }
        }
    }
}
