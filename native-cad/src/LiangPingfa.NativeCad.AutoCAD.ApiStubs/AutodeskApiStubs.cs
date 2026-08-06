// SPDX-License-Identifier: MIT
// SYNTAX-ONLY API STUBS -- original project source, not Autodesk source.
// NOT DEPLOYABLE. Every executable member throws; this is not runtime proof.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Autodesk.AutoCAD.Runtime
{
    /// <summary>Syntax-only command flags needed by the adapter source.</summary>
    [Flags]
    public enum CommandFlags
    {
        Modal = 0,
        Session = 1,
    }

    /// <summary>Syntax-only command metadata; it has no host behavior.</summary>
    [AttributeUsage(AttributeTargets.Method, AllowMultiple = true)]
    public sealed class CommandMethodAttribute : Attribute
    {
        public CommandMethodAttribute(string globalName)
        {
            throw Stub.NotSupported();
        }

        public CommandMethodAttribute(string globalName, CommandFlags flags)
        {
            throw Stub.NotSupported();
        }

        public CommandMethodAttribute(string groupName, string globalName, CommandFlags flags)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only extension-application metadata.</summary>
    [AttributeUsage(AttributeTargets.Assembly)]
    public sealed class ExtensionApplicationAttribute : Attribute
    {
        public ExtensionApplicationAttribute(Type type)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only lifecycle contract.</summary>
    public interface IExtensionApplication
    {
        void Initialize();

        void Terminate();
    }

    internal static class Stub
    {
        internal static NotSupportedException NotSupported()
        {
            return new NotSupportedException(
                "This syntax-only Autodesk API stub is not deployable or executable.");
        }
    }
}

namespace Autodesk.AutoCAD.ApplicationServices
{
    using Autodesk.AutoCAD.DatabaseServices;
    using Autodesk.AutoCAD.Runtime;

    /// <summary>Syntax-only application facade.</summary>
    public static class Application
    {
        public static DocumentCollection DocumentManager
        {
            get { throw Stub.NotSupported(); }
        }

        public static object GetSystemVariable(string name)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only document event arguments.</summary>
    public sealed class DocumentCollectionEventArgs : EventArgs
    {
        public DocumentCollectionEventArgs()
        {
            throw Stub.NotSupported();
        }

        public Document Document
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only document collection.</summary>
    public sealed class DocumentCollection : IEnumerable<Document>
    {
        /// <summary>Syntax-only nested async command-context result.</summary>
        public sealed class ExecutionResult
        {
            public ExecutionResult()
            {
                throw Stub.NotSupported();
            }

            public ExecutionResult GetAwaiter()
            {
                throw Stub.NotSupported();
            }
        }

        public DocumentCollection()
        {
            throw Stub.NotSupported();
        }

        public event EventHandler<DocumentCollectionEventArgs> DocumentBecameCurrent
        {
            add { throw Stub.NotSupported(); }
            remove { throw Stub.NotSupported(); }
        }

        public event EventHandler<DocumentCollectionEventArgs> DocumentToBeDestroyed
        {
            add { throw Stub.NotSupported(); }
            remove { throw Stub.NotSupported(); }
        }

        public Document MdiActiveDocument
        {
            get { throw Stub.NotSupported(); }
        }

        public ExecutionResult ExecuteInCommandContextAsync(
            Func<object, Task> callback,
            object data)
        {
            throw Stub.NotSupported();
        }

        public IEnumerator<Document> GetEnumerator()
        {
            throw Stub.NotSupported();
        }

        IEnumerator IEnumerable.GetEnumerator()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only document facade.</summary>
    public sealed class Document
    {
        public Document()
        {
            throw Stub.NotSupported();
        }

        public Database Database
        {
            get { throw Stub.NotSupported(); }
        }

        public string Name
        {
            get { throw Stub.NotSupported(); }
        }

        public string CommandInProgress
        {
            get { throw Stub.NotSupported(); }
        }

        public DocumentLock LockDocument()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only document lock.</summary>
    public sealed class DocumentLock : IDisposable
    {
        public DocumentLock()
        {
            throw Stub.NotSupported();
        }

        public void Dispose()
        {
            throw Stub.NotSupported();
        }
    }
}

namespace Autodesk.AutoCAD.DatabaseServices
{
    using Autodesk.AutoCAD.Geometry;
    using Autodesk.AutoCAD.Runtime;

    /// <summary>Syntax-only object open mode.</summary>
    public enum OpenMode
    {
        ForRead,
        ForWrite,
    }

    /// <summary>Syntax-only DWG input sharing mode.</summary>
    public enum FileOpenMode
    {
        OpenForReadAndAllShare,
    }

    /// <summary>Syntax-only DWG save version.</summary>
    public enum DwgVersion
    {
        Current,
    }

    /// <summary>Syntax-only database declaration.</summary>
    public sealed class Database : IDisposable
    {
        public Database()
        {
            throw Stub.NotSupported();
        }

        public Database(bool buildDefaultDrawing, bool noDocument)
        {
            throw Stub.NotSupported();
        }

        public TransactionManager TransactionManager
        {
            get { throw Stub.NotSupported(); }
        }

        public ObjectId BlockTableId
        {
            get { throw Stub.NotSupported(); }
        }

        public ObjectId LayerTableId
        {
            get { throw Stub.NotSupported(); }
        }

        public ObjectId TextStyleTableId
        {
            get { throw Stub.NotSupported(); }
        }

        public DwgVersion OriginalFileVersion
        {
            get { throw Stub.NotSupported(); }
        }

        public string Filename
        {
            get { throw Stub.NotSupported(); }
        }

        /// <summary>Saved database identity indicator exposed by AutoCAD.</summary>
        public Guid FingerprintGuid
        {
            get { throw Stub.NotSupported(); }
        }

        /// <summary>Saved database revision indicator exposed by AutoCAD.</summary>
        public Guid VersionGuid
        {
            get { throw Stub.NotSupported(); }
        }

        public ObjectId GetObjectId(bool createIfNotFound, Handle handle, int xrefId)
        {
            throw Stub.NotSupported();
        }

        public void ReadDwgFile(
            string fileName,
            FileOpenMode mode,
            bool allowCpConversion,
            string password)
        {
            throw Stub.NotSupported();
        }

        public void CloseInput(bool closeFile)
        {
            throw Stub.NotSupported();
        }

        public void SaveAs(string fileName, DwgVersion version)
        {
            throw Stub.NotSupported();
        }

        public void Dispose()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only transaction manager.</summary>
    public sealed class TransactionManager
    {
        public TransactionManager()
        {
            throw Stub.NotSupported();
        }

        public Transaction StartTransaction()
        {
            throw Stub.NotSupported();
        }

    }

    /// <summary>Syntax-only transaction declaration.</summary>
    public sealed class Transaction : IDisposable
    {
        public Transaction()
        {
            throw Stub.NotSupported();
        }

        public DBObject GetObject(ObjectId id, OpenMode mode)
        {
            throw Stub.NotSupported();
        }

        public DBObject GetObject(ObjectId id, OpenMode mode, bool openErased)
        {
            throw Stub.NotSupported();
        }

        public void AddNewlyCreatedDBObject(DBObject objectValue, bool add)
        {
            throw Stub.NotSupported();
        }

        public void Commit()
        {
            throw Stub.NotSupported();
        }

        public void Abort()
        {
            throw Stub.NotSupported();
        }

        public void Dispose()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only handle value.</summary>
    public struct Handle
    {
        public Handle(long value)
        {
            throw Stub.NotSupported();
        }

        public long Value
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only object identity.</summary>
    public struct ObjectId
    {
        public bool IsNull
        {
            get { throw Stub.NotSupported(); }
        }

        public bool IsErased
        {
            get { throw Stub.NotSupported(); }
        }

        public bool IsValid
        {
            get { throw Stub.NotSupported(); }
        }

        public bool Equals(ObjectId other)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only database object.</summary>
    public class DBObject
    {
        public DBObject()
        {
            throw Stub.NotSupported();
        }

        public ObjectId ObjectId
        {
            get { throw Stub.NotSupported(); }
        }

        public Handle Handle
        {
            get { throw Stub.NotSupported(); }
        }

        public ObjectId OwnerId
        {
            get { throw Stub.NotSupported(); }
        }

        public bool IsErased
        {
            get { throw Stub.NotSupported(); }
        }

        /// <summary>
        /// Autodesk DBObject field-presence API.  The adapter uses this
        /// documented signal to reject field-backed DBTEXT rather than
        /// treating its evaluated TextString as lossless source state.
        /// </summary>
        public bool HasFields
        {
            get { throw Stub.NotSupported(); }
        }

        /// <summary>Syntax-only documented field accessor.</summary>
        public ObjectId GetField()
        {
            throw Stub.NotSupported();
        }

        public void Erase()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only Autodesk field database object.</summary>
    public sealed class Field : DBObject
    {
        public Field()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only drawable entity.</summary>
    public class Entity : DBObject
    {
        public Entity()
        {
            throw Stub.NotSupported();
        }

        public string Layer
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only single-line text entity.</summary>
    public sealed class DBText : Entity
    {
        public DBText()
        {
            throw Stub.NotSupported();
        }

        public string TextString
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public ObjectId TextStyleId
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public double Height
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public double Rotation
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public Point3d Position
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public Point3d AlignmentPoint
        {
            // BaseLeft adapter tests deliberately never access this member:
            // Position is the only supported BaseLeft anchor.
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public AttachmentPoint Justify
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public TextHorizontalMode HorizontalMode
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public TextVerticalMode VerticalMode
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only DBTEXT attachment values.</summary>
    public enum AttachmentPoint
    {
        BaseLeft,
        BaseCenter,
        BaseRight,
        BottomLeft,
        BottomCenter,
        BottomRight,
        MiddleLeft,
        MiddleCenter,
        MiddleRight,
        TopLeft,
        TopCenter,
        TopRight,
    }

    /// <summary>Syntax-only DBTEXT horizontal alignment values.</summary>
    public enum TextHorizontalMode
    {
        TextLeft,
        TextCenter,
        TextRight,
        TextAlign,
        TextMid,
        TextFit,
    }

    /// <summary>Syntax-only DBTEXT vertical alignment values.</summary>
    public enum TextVerticalMode
    {
        TextBase,
        TextBottom,
        TextVerticalMid,
        TextTop,
    }

    /// <summary>Syntax-only line entity.</summary>
    public sealed class Line : Entity
    {
        public Line()
        {
            throw Stub.NotSupported();
        }

        public Point3d StartPoint
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }

        public Point3d EndPoint
        {
            get { throw Stub.NotSupported(); }
            set { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only lightweight polyline.</summary>
    public sealed class Polyline : Entity
    {
        public Polyline()
        {
            throw Stub.NotSupported();
        }

        public int NumberOfVertices
        {
            get { throw Stub.NotSupported(); }
        }

        public bool Closed
        {
            get { throw Stub.NotSupported(); }
        }

        public double Elevation
        {
            get { throw Stub.NotSupported(); }
        }

        public Vector3d Normal
        {
            get { throw Stub.NotSupported(); }
        }

        public Point2d GetPoint2dAt(int index)
        {
            throw Stub.NotSupported();
        }

        public double GetBulgeAt(int index)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only block table.</summary>
    public sealed class BlockTable : DBObject, IEnumerable
    {
        public BlockTable()
        {
            throw Stub.NotSupported();
        }

        public ObjectId this[string name]
        {
            get { throw Stub.NotSupported(); }
        }

        public bool Has(string name)
        {
            throw Stub.NotSupported();
        }

        public IEnumerator GetEnumerator()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only block table record.</summary>
    public sealed class BlockTableRecord : DBObject, IEnumerable
    {
        public const string ModelSpace = "*Model_Space";
        public const string PaperSpace = "*Paper_Space";

        public BlockTableRecord()
        {
            throw Stub.NotSupported();
        }

        public string Name
        {
            get { throw Stub.NotSupported(); }
        }

        public bool IsLayout
        {
            get { throw Stub.NotSupported(); }
        }

        public bool IsFromExternalReference
        {
            get { throw Stub.NotSupported(); }
        }

        public ObjectId LayoutId
        {
            get { throw Stub.NotSupported(); }
        }

        public ObjectId AppendEntity(Entity entity)
        {
            throw Stub.NotSupported();
        }

        /// <summary>
        /// Gets the documented erased-inclusive <see cref="BlockTableRecord"/>
        /// view. Enumerate the returned record to retain physical ObjectId
        /// slots that the default record enumerator omits.
        /// </summary>
        public BlockTableRecord IncludingErased
        {
            get { throw Stub.NotSupported(); }
        }

        public IEnumerator GetEnumerator()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only layout record.</summary>
    public sealed class Layout : DBObject
    {
        public Layout()
        {
            throw Stub.NotSupported();
        }

        public string LayoutName
        {
            get { throw Stub.NotSupported(); }
        }

        public bool ModelType
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only layer table.</summary>
    public sealed class LayerTable : DBObject, IEnumerable
    {
        public LayerTable()
        {
            throw Stub.NotSupported();
        }

        public ObjectId this[string name]
        {
            get { throw Stub.NotSupported(); }
        }

        public bool Has(string name)
        {
            throw Stub.NotSupported();
        }

        public IEnumerator GetEnumerator()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only layer table record.</summary>
    public sealed class LayerTableRecord : DBObject
    {
        public LayerTableRecord()
        {
            throw Stub.NotSupported();
        }

        public string Name
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only text style table.</summary>
    public sealed class TextStyleTable : DBObject, IEnumerable
    {
        public TextStyleTable()
        {
            throw Stub.NotSupported();
        }

        public ObjectId this[string name]
        {
            get { throw Stub.NotSupported(); }
        }

        public bool Has(string name)
        {
            throw Stub.NotSupported();
        }

        public IEnumerator GetEnumerator()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only text style table record.</summary>
    public sealed class TextStyleTableRecord : DBObject
    {
        public TextStyleTableRecord()
        {
            throw Stub.NotSupported();
        }

        public string Name
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only proxy entity declaration.</summary>
    public sealed class ProxyEntity : Entity
    {
        public ProxyEntity()
        {
            throw Stub.NotSupported();
        }
    }
}

namespace Autodesk.AutoCAD.Geometry
{
    using Autodesk.AutoCAD.Runtime;

    /// <summary>Syntax-only two-dimensional point.</summary>
    public struct Point2d
    {
        public Point2d(double x, double y)
        {
            throw Stub.NotSupported();
        }

        public double X
        {
            get { throw Stub.NotSupported(); }
        }

        public double Y
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only three-dimensional point.</summary>
    public struct Point3d
    {
        public Point3d(double x, double y, double z)
        {
            throw Stub.NotSupported();
        }

        public double X
        {
            get { throw Stub.NotSupported(); }
        }

        public double Y
        {
            get { throw Stub.NotSupported(); }
        }

        public double Z
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only three-dimensional vector.</summary>
    public struct Vector3d
    {
        public Vector3d(double x, double y, double z)
        {
            throw Stub.NotSupported();
        }

        public double X
        {
            get { throw Stub.NotSupported(); }
        }

        public double Y
        {
            get { throw Stub.NotSupported(); }
        }

        public double Z
        {
            get { throw Stub.NotSupported(); }
        }
    }
}
