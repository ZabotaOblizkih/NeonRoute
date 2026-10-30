from pathlib import Path

# 1. Инспекция extract_cast_async (все условия раннего выхода и запуск потока)
p_ext = Path("services/rp_cast_extractor.py")
if p_ext.exists():
    lines = p_ext.read_text(encoding="utf-8").splitlines()
    print("=== 1. ЛОГИКА ВЫХОДА В RPCastExtractor.extract_cast_async ===")
    start = False
    for i, l in enumerate(lines):
        if "def extract_cast_async(" in l:
            start = True
        if start and i < 260:
            print(f"{i+1:4d}: {l}")
        if start and "threading.Thread" in l:
            print(f"{i+1:4d}: {l}")
            # захватим еще 10 строк после старта потока
            for j in range(i+1, min(len(lines), i+15)):
                print(f"{j+1:4d}: {lines[j]}")
            break

# 2. Инспекция метода завершения реплики в roleplay_flow_controller
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines = p_rp.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. МЕТОД, ВЫЗЫВАЮЩИЙ _trigger_roleplay_memory_extraction ===")
    for i in range(410, min(len(lines), 475)):
        print(f"{i+1:4d}: {lines[i]}")

# 3. Проверка подключения сигналов воркера генерации к этому методу
print("\n=== 3. К ЧЕМУ ПОДКЛЮЧЕН ЗАВЕРШАЮЩИЙ МЕТОД В КОНТРОЛЛЕРЕ ===")
for i, l in enumerate(lines):
    if "finished.connect" in l or "generation_finished" in l or "_on_turn_finished" in l or "_on_generation_finished" in l:
        print(f"{i+1:4d}: {l}")
