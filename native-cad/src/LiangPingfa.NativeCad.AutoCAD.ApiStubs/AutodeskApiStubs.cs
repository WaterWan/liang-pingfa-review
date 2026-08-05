// SPDX-License-Identifier: MIT
// SYNTAX-ONLY API STUBS -- original project source, not Autodesk source.
// NOT DEPLOYABLE. Every executable member throws; this is not runtime proof.

using System;
using System.Collections;
using System.Collections.Generic;

namespace Autodesk.AutoCAD.Runtime
{
    /// <summary>Syntax-only command flags for a future adapter.</summary>
    [Flags]
    public enum CommandFlags
    {
        Modal = 0,
        Session = 1,
    }

    /// <summary>Syntax-only attribute declaration; it has no host behavior.</summary>
    [AttributeUsage(AttributeTargets.Method, AllowMultiple = true)]
    public sealed class CommandMethodAttribute : Attribute
    {
        public CommandMethodAttribute(string globalName)
        {
            throw Stub.NotSupported();
        }

        public CommandMethodAttribute(string groupName, string globalName, CommandFlags flags)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only extension-application attribute declaration.</summary>
    [AttributeUsage(AttributeTargets.Assembly)]
    public sealed class ExtensionApplicationAttribute : Attribute
    {
        public ExtensionApplicationAttribute(Type type)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only lifecycle interface for a future adapter.</summary>
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
    }

    /// <summary>Syntax-only document collection.</summary>
    public sealed class DocumentCollection : IEnumerable<Document>
    {
        public DocumentCollection()
        {
            throw Stub.NotSupported();
        }

        public Document MdiActiveDocument
        {
            get { throw Stub.NotSupported(); }
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

    /// <summary>Syntax-only database declaration.</summary>
    public sealed class Database : IDisposable
    {
        public Database()
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

        public Handle Handle
        {
            get { throw Stub.NotSupported(); }
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

        public void Erase()
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

        public void TransformBy(Matrix3d transform)
        {
            throw Stub.NotSupported();
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

        public Extents3d GeometricExtents
        {
            get { throw Stub.NotSupported(); }
        }
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

    /// <summary>Syntax-only lightweight polyline declaration.</summary>
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

        public Point3d GetPoint3dAt(int index)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only block table.</summary>
    public sealed class BlockTable : DBObject
    {
        public BlockTable()
        {
            throw Stub.NotSupported();
        }

        public ObjectId this[string name]
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only block table record.</summary>
    public sealed class BlockTableRecord : DBObject
    {
        public BlockTableRecord()
        {
            throw Stub.NotSupported();
        }

        public ObjectId AppendEntity(Entity entity)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only layer table.</summary>
    public sealed class LayerTable : DBObject
    {
        public LayerTable()
        {
            throw Stub.NotSupported();
        }

        public ObjectId this[string name]
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only layer table record.</summary>
    public sealed class LayerTableRecord : DBObject
    {
        public LayerTableRecord()
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only text style table.</summary>
    public sealed class TextStyleTable : DBObject
    {
        public TextStyleTable()
        {
            throw Stub.NotSupported();
        }

        public ObjectId this[string name]
        {
            get { throw Stub.NotSupported(); }
        }
    }

    /// <summary>Syntax-only text style table record.</summary>
    public sealed class TextStyleTableRecord : DBObject
    {
        public TextStyleTableRecord()
        {
            throw Stub.NotSupported();
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

    /// <summary>Syntax-only transformation matrix.</summary>
    public struct Matrix3d
    {
        public static Matrix3d Displacement(Vector3d vector)
        {
            throw Stub.NotSupported();
        }
    }

    /// <summary>Syntax-only geometric extents.</summary>
    public struct Extents3d
    {
        public Extents3d(Point3d minimumPoint, Point3d maximumPoint)
        {
            throw Stub.NotSupported();
        }

        public Point3d MinPoint
        {
            get { throw Stub.NotSupported(); }
        }

        public Point3d MaxPoint
        {
            get { throw Stub.NotSupported(); }
        }
    }
}
