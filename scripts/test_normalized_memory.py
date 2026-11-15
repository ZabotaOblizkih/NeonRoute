import numpy as np
from fastembed import TextEmbedding
import lancedb
import tempfile
import shutil

tmp = tempfile.mkdtemp()
try:
    db = lancedb.connect(tmp)
    embed_model = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    
    def get_norm_vec(text):
        v = list(embed_model.embed([text]))[0]
        v = np.array(v, dtype=np.float32)
        norm = np.linalg.norm(v)
        if norm > 0:
            v = v / norm
        return v.tolist()

    t = db.create_table("test_norm", data=[
        {"id": "1", "text": "Пользователь пишет на Python и Rust", "vector": get_norm_vec("Пользователь пишет на Python и Rust")},
        {"id": "2", "text": "Пользователь любит черный кофе без сахара", "vector": get_norm_vec("Пользователь любит черный кофе без сахара")},
        {"id": "3", "text": "Пользователя зовут Роман", "vector": get_norm_vec("Пользователя зовут Роман")}
    ])

    queries = [
        "На каких языках я пишу код?",
        "Какой кофе я предпочитаю?",
        "Как меня зовут?",
        "Какая погода на Марсе?"
    ]

    for q in queries:
        qv = get_norm_vec(q)
        res = t.search(qv).limit(3).to_list()
        print(f"\nQuery: '{q}'")
        for r in res:
            dist = r.get("_distance")
            match = dist <= 0.48
            print(f"  [dist={dist:.4f} | match_0.48={match}] {r.get('text')}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
