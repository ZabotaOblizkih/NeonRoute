import re
from pathlib import Path

p = Path("services/lm_studio.py")
content = p.read_text(encoding="utf-8")

# 1. Обновляем метод is_model_loaded
new_is_model_loaded = '''    def is_model_loaded(self, model_name: Optional[str] = None) -> bool:
        """Проверяет готовность целевой модели в VRAM по состоянию жизненного цикла клиента."""
        if getattr(self, "_is_model_unloaded", False):
            return False

        target = model_name or getattr(self, "_last_loaded_model_id", None)
        last_id = getattr(self, "_last_loaded_model_id", None)

        # Если локальный last_id пуст (новый инстанс), опрашиваем живой сервер /models
        if not last_id:
            try:
                active = self.get_models()
                if active:
                    last_id = active[0]
                    self._last_loaded_model_id = last_id
                    if not getattr(self, "_current_loaded_ctx", 0):
                        self._current_loaded_ctx = 14336
            except Exception:
                pass

        if not target or not last_id:
            return False

        t_clean = str(target).lower().strip()
        l_clean = str(last_id).lower().strip()
        if t_clean == l_clean:
            return True

        if Path(t_clean).stem.lower() == Path(l_clean).stem.lower():
            return True

        def _clean_stem(s: str) -> str:
            name = s.replace("\\", "/").rsplit("/", 1)[-1]
            for ext in (".gguf", "-gguf", "_gguf"):
                if name.endswith(ext):
                    name = name[:-len(ext)]
            return name

        c1 = _clean_stem(t_clean)
        c2 = _clean_stem(l_clean)
        if c1 == c2 or c1 in c2 or c2 in c1:
            return True

        return False'''

content = re.sub(
    r'    def is_model_loaded\(self, model_name: Optional\[str\] = None\) -> bool:[\s\S]*?(?=\n    def check_health)',
    new_is_model_loaded + "\n",
    content,
    count=1
)

# 2. Обновляем блок проверки в ensure_model_loaded
old_check_pattern = r'            # Защита от частых перезагрузок \(Reload Loop\):[\s\S]*?return True\n'
new_check_code = '''            # Защита от частых перезагрузок (Reload Loop):
            # Если модель уже активна на сервере и доступна
            is_healthy, _ = self.check_health()
            if is_healthy and self.is_model_loaded(model_name):
                # Для автономного сервера без process_manager или при достаточном контексте
                has_pm_headless = bool(self.process_manager and hasattr(self.process_manager, "has_headless_server") and self.process_manager.has_headless_server())
                current_ctx = getattr(self, "_current_loaded_ctx", 0) or target_ctx
                if not has_pm_headless or current_ctx >= target_ctx:
                    self._is_model_unloaded = False
                    self._last_loaded_model_id = model_name
                    self._current_loaded_ctx = current_ctx
                    self._current_loaded_gpu = target_gpu
                    return True
'''

content = re.sub(old_check_pattern, new_check_code, content, count=1)

p.write_text(content, encoding="utf-8")
print("[+] services/lm_studio.py успешно обновлен!")
