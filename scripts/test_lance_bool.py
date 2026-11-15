import lancedb
from pathlib import Path

lance_path = Path("data/vector_memory")
db = lancedb.connect(str(lance_path))
table = db.open_table("facts")

print("Type of table:", type(table))
print("repr(table):", repr(table))
try:
    print("len(table):", len(table))
except Exception as e:
    print("len(table) failed:", e)

try:
    print("bool(table):", bool(table))
except Exception as e:
    print("bool(table) failed:", e)

if table:
    print("if table: is TRUE")
else:
    print("if table: is FALSE")
