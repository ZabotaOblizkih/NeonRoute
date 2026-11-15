import sqlite3
import os
from pathlib import Path

def inspect_sqlite():
    db_path = Path("data/memory.db")
    print("=== SQLite Database Inspection ===")
    print("DB exists:", db_path.exists())
    if not db_path.exists():
        return
    print("DB size:", db_path.stat().st_size, "bytes")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view');")
    items = cursor.fetchall()
    print("Tables and views:", items)
    for name, item_type in items:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM `{name}`;")
            count = cursor.fetchone()[0]
            print(f"  {item_type} `{name}`: {count} rows")
            if count > 0:
                cursor.execute(f"SELECT * FROM `{name}` LIMIT 20;")
                rows = cursor.fetchall()
                cols = [d[0] for d in cursor.description]
                print(f"    Columns: {cols}")
                for r in rows:
                    print(f"    Row: {r}")
        except Exception as e:
            print(f"  Error inspecting {name}: {e}")
    conn.close()

def inspect_lancedb():
    print("\n=== LanceDB Inspection ===")
    lance_dir = Path("data/vector_memory")
    print("LanceDB dir exists:", lance_dir.exists())
    if not lance_dir.exists():
        return
    try:
        import lancedb
        db = lancedb.connect(str(lance_dir))
        raw_tables = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
        tables = getattr(raw_tables, "tables", raw_tables)
        print("LanceDB tables:", list(tables))
        for t_name in tables:
            try:
                t = db.open_table(t_name)
                count = len(t)
                print(f"  Table `{t_name}`: {count} rows")
                sample = t.head(20).to_list()
                for s in sample:
                    vec = s.get("vector", [])
                    vec_len = len(vec) if vec is not None else 0
                    sample_copy = {k: v for k, v in s.items() if k != "vector"}
                    sample_copy["vector_dim"] = vec_len
                    print(f"    Sample: {sample_copy}")
            except Exception as e:
                print(f"  Error inspecting LanceDB table {t_name}: {e}")
    except Exception as e:
        print(f"LanceDB load error: {e}")

if __name__ == "__main__":
    inspect_sqlite()
    inspect_lancedb()
