import sqlite3
import lancedb
from pathlib import Path

def compare():
    db_path = Path("data/memory.db")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT id, fact, created_at FROM user_memory ORDER BY id ASC;")
    sqlite_facts = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}
    conn.close()

    print(f"SQLite facts count: {len(sqlite_facts)}")
    for fid, (ftext, fdate) in sqlite_facts.items():
        print(f"  SQLite [{fid} | {fdate}]: {ftext}")

    lance_dir = Path("data/vector_memory")
    db = lancedb.connect(str(lance_dir))
    t = db.open_table("facts")
    rows = t.to_arrow().to_pylist()
    print(f"\nLanceDB rows count: {len(rows)}")
    lance_by_id = {}
    for r in rows:
        lid = r.get("id")
        lance_by_id.setdefault(lid, []).append(r.get("text"))

    print(f"LanceDB distinct IDs: {len(lance_by_id)}")
    
    # Check matching with SQLite
    matched = 0
    missing_in_sqlite = 0
    for lid, texts in lance_by_id.items():
        if lid == "init_id":
            continue
        try:
            int_id = int(lid)
            if int_id in sqlite_facts:
                matched += 1
            else:
                missing_in_sqlite += 1
        except ValueError:
            missing_in_sqlite += 1

    print(f"LanceDB records matching SQLite: {matched}")
    print(f"LanceDB records orphaned (not in SQLite): {missing_in_sqlite}")
    
    # Check how many are unique texts
    all_lance_texts = [r.get("text") for r in rows if r.get("id") != "init_id"]
    print(f"Total lance non-init texts: {len(all_lance_texts)}")
    print(f"Unique lance non-init texts: {len(set(all_lance_texts))}")

if __name__ == "__main__":
    compare()
