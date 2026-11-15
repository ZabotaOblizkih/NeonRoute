from fastembed import TextEmbedding
import numpy as np

model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
q_raw = list(model.embed(["Ты знаешь что-нибудь про Т-34?"]))[0]
f1_raw = list(model.embed(["Пользователь интересуется техническими характеристиками танка Т-34"]))[0]
f2_raw = list(model.embed(["Пользователь интересуется передаточными отношениями коробки передач Т-34"]))[0]

l2_dist_1 = float(np.linalg.norm(q_raw - f1_raw))
l2_dist_2 = float(np.linalg.norm(q_raw - f2_raw))

print(f"Raw L2 distance Fact 12: {l2_dist_1:.4f}")
print(f"Raw L2 distance Fact 13: {l2_dist_2:.4f}")
print(f"Old threshold was: 0.48")
print(f"Did Fact 12 pass old threshold (<= 0.48)? {l2_dist_1 <= 0.48}")
print(f"Did Fact 13 pass old threshold (<= 0.48)? {l2_dist_2 <= 0.48}")
