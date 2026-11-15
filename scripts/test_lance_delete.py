import lancedb
import tempfile
import shutil

tmp = tempfile.mkdtemp()
try:
    db = lancedb.connect(tmp)
    t = db.create_table('test_tbl', data=[
        {'id': 'init_id', 'text': 'init', 'vector': [0.1]*10},
        {'id': '1', 'text': 'fact 1', 'vector': [0.2]*10},
        {'id': '2', 'text': 'fact 2', 'vector': [0.3]*10}
    ])
    print('Initial count:', len(t))
    
    # Test 1: id != "init_id"
    try:
        t.delete('id != "init_id"')
        print('After delete id != "init_id":', len(t))
    except Exception as e:
        print('Delete with id != "init_id" failed:', e)

    # Test 2: id != 'init_id'
    try:
        t.delete("id != 'init_id'")
        print("After delete id != 'init_id':", len(t))
    except Exception as e:
        print("Delete with id != 'init_id' failed:", e)

    # Test 3: id = "1"
    t.add([{'id': '3', 'text': 'fact 3', 'vector': [0.4]*10}])
    print('After re-add count:', len(t))
    try:
        t.delete('id = "3"')
        print('After delete id = "3":', len(t))
    except Exception as e:
        print('Delete with id = "3" failed:', e)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
