import sys, inspect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import services.memory_db as mdb
print(inspect.getsource(mdb.MemoryDB.__init__))
