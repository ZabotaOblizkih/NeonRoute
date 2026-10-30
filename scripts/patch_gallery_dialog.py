from pathlib import Path

p = Path("ui/dialogs/characters_gallery_dialog.py")
content = p.read_text(encoding="utf-8")

# 1. Добавляем импорт StepProgressButton
import_marker = "from qfluentwidgets import ("
new_import = "from ui.components.step_progress_button import StepProgressButton\nfrom qfluentwidgets import ("
if "StepProgressButton" not in content:
    content = content.replace(import_marker, new_import, 1)

# 2. Заменяем создание btn_translate_all
old_btn = 'self.btn_translate_all = PushButton(FIF.LANGUAGE, "🌐 Перевести всё (RU)")'
new_btn = 'self.btn_translate_all = StepProgressButton(FIF.LANGUAGE, "🌐 Перевести всё (RU)")'

if old_btn in content:
    content = content.replace(old_btn, new_btn, 1)
    p.write_text(content, encoding="utf-8")
    print("[+] characters_gallery_dialog.py: StepProgressButton успешно внедрен!")
else:
    print("[!] Строка создания btn_translate_all не найдена")
