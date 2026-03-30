"""
sqlite_vec helper — vector search via direct sqlite3 connection to PocketBase db.
Graceful fallback if sqlite_vec extension is not available.
"""
import os, json, sqlite3, struct, sys

_VEC_AVAILABLE = None
_VEC_EXT_PATH = None


def get_db_path() -> str:
    """Return path to PocketBase data.db."""
    return os.path.join(os.path.dirname(__file__), 'pb_data', 'data.db')


def _find_vec_extension() -> str | None:
    """Find sqlite_vec extension binary for current platform."""
    base = os.path.join(os.path.dirname(__file__), 'sqlite_vec_bin')
    if not os.path.isdir(base):
        return None

    # Try common extension names
    for name in ['vec0.dylib', 'vec0.so', 'vec0.dll', 'vec0']:
        path = os.path.join(base, name)
        if os.path.isfile(path):
            return path
    return None


def _load_vec(conn: sqlite3.Connection) -> bool:
    """Load sqlite_vec extension into connection. Returns True on success."""
    global _VEC_AVAILABLE, _VEC_EXT_PATH

    if _VEC_AVAILABLE is False:
        return False

    if _VEC_EXT_PATH is None:
        _VEC_EXT_PATH = _find_vec_extension()

    if not _VEC_EXT_PATH:
        _VEC_AVAILABLE = False
        return False

    try:
        conn.enable_load_extension(True)
        conn.load_extension(_VEC_EXT_PATH)
        _VEC_AVAILABLE = True
        return True
    except Exception as e:
        print(f"   ⚠️  Cannot load sqlite_vec: {e}", file=sys.stderr)
        _VEC_AVAILABLE = False
        return False


def is_available() -> bool:
    """Check if sqlite_vec is usable."""
    if _VEC_AVAILABLE is not None:
        return _VEC_AVAILABLE
    db_path = get_db_path()
    if not os.path.isfile(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        result = _load_vec(conn)
        conn.close()
        return result
    except Exception:
        return False


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize embedding to bytes for sqlite_vec."""
    return struct.pack(f'{len(embedding)}f', *embedding)


def create_vec_table(db_path: str | None = None):
    """Create the company_embeddings virtual table."""
    db_path = db_path or get_db_path()
    conn = sqlite3.connect(db_path)
    if not _load_vec(conn):
        conn.close()
        print("   ⚠️  sqlite_vec not available — skipping vector table creation", file=sys.stderr)
        return False

    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS company_embeddings USING vec0(
            company_id TEXT PRIMARY KEY,
            embedding FLOAT[768]
        )
    """)
    conn.commit()
    conn.close()
    return True


def insert_embedding(db_path: str | None, company_id: str, embedding: list[float]):
    """Insert or replace an embedding for a company."""
    db_path = db_path or get_db_path()
    conn = sqlite3.connect(db_path)
    if not _load_vec(conn):
        conn.close()
        return

    blob = _serialize_embedding(embedding)
    # Delete existing if any, then insert
    conn.execute("DELETE FROM company_embeddings WHERE company_id = ?", (company_id,))
    conn.execute("INSERT INTO company_embeddings (company_id, embedding) VALUES (?, ?)",
                 (company_id, blob))
    conn.commit()
    conn.close()


def search_similar(db_path: str | None, query_embedding: list[float], top_k: int = 5) -> list[dict]:
    """Search for similar companies by embedding. Returns list of {company_id, distance}."""
    db_path = db_path or get_db_path()

    if not is_available():
        return []

    conn = sqlite3.connect(db_path)
    if not _load_vec(conn):
        conn.close()
        return []

    blob = _serialize_embedding(query_embedding)
    rows = conn.execute(
        "SELECT company_id, distance FROM company_embeddings WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (blob, top_k)
    ).fetchall()
    conn.close()

    return [{'company_id': row[0], 'distance': row[1]} for row in rows]
