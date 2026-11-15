import sqlite3
import os
from pathlib import Path

def inspect_lancedb():
    print("\n=== LanceDB Detailed Inspection ===")
    lance_dir = Path("data/vector_memory")
    if not lance_dir.exists():
        print("LanceDB dir does not exist.")
        return
    import lancedb
    db = lancedb.connect(str(lance_dir))
    raw_tables = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
    tables = getattr(raw_tables, "tables", raw_tables)
    print("LanceDB tables:", list(tables))
    for t_name in tables:
        t = db.open_table(t_name)
        count = len(t)
        print(f"\n--- Table `{t_name}`: {count} rows ---")
        arrow_table = t.to_arrow()
        rows = arrow_table.to_pylist()
        print(f"Total rows retrieved: {len(rows)}")
        ids = [r.get("id") for r in rows]
        print(f"Sample IDs (first 10): {ids[:10]}")
        print(f"Sample IDs (last 10): {ids[-10:]}")
        texts = [r.get("text") for r in rows]
        print("Sample texts (first 5):")
        for i, txt in enumerate(texts[:5]):
            print(f"  [{ids[i]}] {txt}")
        print("Sample texts (last 5):")
        for i, txt in enumerate(texts[-5:], start=max(0, len(texts)-5)):
            print(f"  [{ids[i]}] {txt}")
        
        # Check unique IDs and texts
        unique_ids = set(ids)
        unique_texts = set(texts)
        print(f"Unique IDs: {len(unique_ids)} / {len(ids)}")
        print(f"Unique texts: {len(unique_texts)} / {len(texts)}")
        
        # Check if vector is valid
        vector_lens = [len(r.get("vector", [])) for r in rows if r.get("vector") is not None]
        print(f"Vector dimensions set: {set(vector_lens)}")

if __name__ == "__main__":
    inspect_lancedb()
