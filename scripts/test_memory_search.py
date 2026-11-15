import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.memory_db import memory_db, _get_embedding_engine

def test_search():
    embed_model, table, _ = _get_embedding_engine()
    print("Embed model:", type(embed_model))
    print("Table:", table.name if table else None)
    
    test_queries = [
        "Какой мой любимый язык программирования?",
        "На каком языке я пишу микросервисы?",
        "Какая у меня операционная система?",
        "Какой кофе я пью?",
        "Какая у меня профессия?",
        "Сколько мне лет?",
        "Какой у меня рост?",
        "Что я думаю про танк Т-34?",
        "Кто такая Йеннифэр?",
        "Привет, как дела?"
    ]
    
    for q in test_queries:
        print(f"\nQuery: '{q}'")
        query_vec = list(embed_model.embed([q]))[0].tolist()
        res = table.search(query_vec).limit(5).to_list()
        for r in res:
            dist = r.get("_distance")
            fid = r.get("id")
            txt = r.get("text")
            print(f"  [id={fid}, dist={dist:.4f}] {txt}")
        
        # Test method search_relevant_facts with default max_distance=0.48
        found = memory_db.search_relevant_facts(q, top_k=5, max_distance=0.48)
        print(f"  -> search_relevant_facts (max_distance=0.48) returned {len(found)} facts: {found}")

if __name__ == "__main__":
    test_search()
