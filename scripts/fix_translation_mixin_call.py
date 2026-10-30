from pathlib import Path

p = Path("ui/dialogs/gallery/translation_mixin.py")
content = p.read_text(encoding="utf-8")

old_call = "parent_win._translate_character_card_task(card, on_complete=_on_all_done, on_progress=getattr(self.btn_translate_all, 'update_step_progress', None))"
new_call = """translate_fn = getattr(parent_win, "_translate_character_card_task", None)
        if translate_fn:
            progress_cb = getattr(self.btn_translate_all, "update_step_progress", None)
            try:
                translate_fn(card, on_complete=_on_all_done, on_progress=progress_cb)
            except TypeError:
                translate_fn(card, on_complete=_on_all_done)"""

if old_call in content:
    content = content.replace(old_call, new_call, 1)
    p.write_text(content, encoding="utf-8")
    print("[+] translation_mixin.py: Добавлен совместимый вызов task с fallback по TypeError!")
else:
    print("[!] Строка вызова не найдена в translation_mixin.py")
