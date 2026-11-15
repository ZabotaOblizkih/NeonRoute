import sys
import sqlite3
from pathlib import Path
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

conn = sqlite3.connect("data/memory.db")
cur = conn.cursor()
cur.execute('SELECT id, fact, created_at FROM user_memory WHERE fact LIKE "%Т-34%"')
rows = cur.fetchall()
print("=== SQLite Т-34 facts ===")
for r in rows:
    print(f"ID {r[0]}: {r[1]} ({r[2]})")

query = "Ты знаешь что-нибудь про Т-34?"
print(f'\nQuery: "{query}"')

from services.memory_db import memory_db, _get_embedding_engine, _normalize_l2

embed_model, table, _ = _get_embedding_engine()

qv = list(embed_model.embed([query]))[0]
qv = _normalize_l2(qv.tolist() if hasattr(qv, "tolist") else qv)

raw_hits = table.search(qv).metric("cosine").limit(10).to_list()
print("\n=== LanceDB search results (all raw) ===")
for h in raw_hits:
    print(f"ID {h['id']}: dist={h.get('_distance'):.4f} | \"{h.get('text')}\"")

relevant_hits = memory_db.search_relevant_facts(query, top_k=5)
print("\n=== memory_db.search_relevant_facts(query) ===")
print("Threshold in search_relevant_facts:", memory_db.search_relevant_facts.__defaults__)
print("Returned:", relevant_hits)
