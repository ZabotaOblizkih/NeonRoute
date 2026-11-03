import torch

# 1. Скалярный тензор shape []
t_scalar = torch.tensor(1.5, dtype=torch.float32).to(torch.float8_e4m3fn)
b_scalar = t_scalar.flatten().view(torch.uint8).numpy().tobytes()
assert len(b_scalar) == 1, "Скаляр FP8 обязан занимать ровно 1 байт"

# 2. Многомерный тензор shape [2, 3, 4]
t_multi = torch.randn(2, 3, 4, dtype=torch.float32).to(torch.float8_e4m3fn)
b_multi = t_multi.flatten().view(torch.uint8).numpy().tobytes()
assert len(b_multi) == 24, "Тензор 2x3x4 FP8 обязан занимать ровно 24 байта"

print("✓ Преобразование .flatten().view(torch.uint8).numpy().tobytes() работает на 100%!")
