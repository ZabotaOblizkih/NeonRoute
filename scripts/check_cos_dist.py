import numpy as np
from fastembed import TextEmbedding

m = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
facts = [
    "Пользователь пишет на Python и Rust",
    "Пользователь предпочитает черный кофе без сахара",
    "Пользователя зовут Роман"
]
norm_facts = []
for f in facts:
    v = np.array(list(m.embed([f]))[0])
    norm_facts.append((f, v / np.linalg.norm(v)))

queries = [
    "На каких языках я программирую?",
    "На каких языках программирования я пишу?",
    "Какой кофе я пью?",
    "Какой кофе я предпочитаю?",
    "Как меня зовут?",
    "Напомни моё имя",
    "Какое расстояние от Земли до Луны?",
    "Какая погода на Марсе?",
    "Расскажи анекдот",
    "Привет, как дела?"
]

print("Cosine distance matrix:")
for q in queries:
    vq = np.array(list(m.embed([q]))[0])
    vq = vq / np.linalg.norm(vq)
    scores = []
    for f, vf in norm_facts:
        cos_sim = float(np.dot(vf, vq))
        cos_dist = 1.0 - cos_sim
        scores.append(f"{f[:20]}..: dist={cos_dist:.4f}")
    print(f"Q: '{q}' -> " + " | ".join(scores))
