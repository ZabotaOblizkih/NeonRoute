from pathlib import Path

p = Path("ui/dialogs/gallery/translation_mixin.py")
content = p.read_text(encoding="utf-8")

# Заменяем блок сброса и вызова
old_block = """        self.btn_translate_all.setEnabled(False)
        self.btn_translate_all.setText("⏳ Перевод карточки...")

        def _on_all_done(success: bool, res_dict=None):
            self.btn_translate_all.setEnabled(True)
            self.btn_translate_all.setText("🌐 Перевести всю карточку (RU)")"""

new_block = """        self.btn_translate_all.setEnabled(False)
        if hasattr(self.btn_translate_all, "update_step_progress"):
            self.btn_translate_all.update_step_progress(1, 1, "Подготовка...")
        else:
            self.btn_translate_all.setText("⏳ Перевод карточки...")

        def _on_all_done(success: bool, res_dict=None):
            if hasattr(self.btn_translate_all, "reset_state"):
                self.btn_translate_all.reset_state(success=success)
            else:
                self.btn_translate_all.setEnabled(True)
                self.btn_translate_all.setText("🌐 Перевести всю карточку (RU)")"""

if old_block in content:
    content = content.replace(old_block, new_block, 1)
    
    # Обновляем сам вызов таски с передачей on_progress
    old_call = "parent_win._translate_character_card_task(card, on_complete=_on_all_done)"
    new_call = "parent_win._translate_character_card_task(card, on_complete=_on_all_done, on_progress=getattr(self.btn_translate_all, 'update_step_progress', None))"
    content = content.replace(old_call, new_call, 1)
    
    p.write_text(content, encoding="utf-8")
    print("[+] translation_mixin.py успешно обновлен с поддержкой step-progress!")
else:
    print("[!] Целевой блок в translation_mixin.py не найден для замены")
