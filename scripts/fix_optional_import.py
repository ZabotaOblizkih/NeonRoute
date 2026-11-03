from pathlib import Path

d_path = Path("ui/models_manager_dialog.py")
text = d_path.read_text(encoding="utf-8")

# Убираем преждевременное объявление перед импортами
text = text.replace("\n_GLOBAL_TORCH_PYTHON: Optional[str] = None", "")

# Добавляем сразу после импорта typing
old_typing = "from typing import List, Dict, Any, Optional"
new_typing = "from typing import List, Dict, Any, Optional\n\n_GLOBAL_TORCH_PYTHON: Optional[str] = None"

if old_typing in text:
    text = text.replace(old_typing, new_typing, 1)
    d_path.write_text(text, encoding="utf-8")
    print("✓ ui/models_manager_dialog.py: _GLOBAL_TORCH_PYTHON корректно помещен после импорта typing")
else:
    print("! Строка импорта typing не найдена")
