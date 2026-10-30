from pathlib import Path

p = Path("services/lm_studio.py")
content = p.read_text(encoding="utf-8")

# 1. Обновляем is_model_loaded: если локальный last_id пуст, опрашиваем живой сервер /models
old_is_loaded = """        target = model_name or getattr(self, "_last_loaded_model_id", None)
        last_id = getattr(self, "_last_loaded_model_id", None)
        if not target or not last_id:
            return False"""

new_is_loaded = """        target = model_name or getattr(self, "_last_loaded_model_id", None)
        last_id = getattr(self, "_last_loaded_model_id", None)
        if not last_id:
            # Если локальный кэш пуст (новый инстанс), проверяем активные модели на работающем сервере
            active_models = self.get_models()
            if active_models:
                last_id = active_models[0]
                self._last_loaded_model_id = last_id
                self._current_loaded_ctx = getattr(self, "_current_loaded_ctx", 0) or 14336
        if not target or not last_id:
            return False"""

if old_is_loaded in content:
    content = content.replace(old_is_loaded, new_is_loaded, 1)
    p.write_text(content, encoding="utf-8")
    print("[+] services/lm_studio.py: is_model_loaded теперь автоматически подхватывает запущенную модель из /models!")
else:
    print("[!] Шаблон is_model_loaded не найден")
