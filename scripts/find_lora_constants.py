from pathlib import Path

lora_file = Path("services/art_director/lora_manager.py")
txt = lora_file.read_text(encoding="utf-8")
print(f"[*] services/art_director/lora_manager.py содержит LORA_REGISTRY_PATH: {'LORA_REGISTRY_PATH' in txt}")
print(f"[*] services/art_director/lora_manager.py содержит DEFAULT_FORGE_LORA_DIR: {'DEFAULT_FORGE_LORA_DIR' in txt}")

# Проверяем также config.py
cfg_txt = Path("config.py").read_text(encoding="utf-8")
print(f"[*] config.py содержит DEFAULT_FORGE_LORA_DIR: {'DEFAULT_FORGE_LORA_DIR' in cfg_txt}")
