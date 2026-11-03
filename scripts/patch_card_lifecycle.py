from pathlib import Path

card_p = Path("ui/widgets/engineer_task_card.py")
card_txt = card_p.read_text(encoding="utf-8")

# 1. Безопасный mark_completed с hasattr
old_mark_comp = '''        if self.spoiler and self.spoiler.get_plan_text():
            self.spoiler.setVisible(True)'''

new_mark_comp = '''        if hasattr(self, "spoiler") and self.spoiler and hasattr(self.spoiler, "get_plan_text") and self.spoiler.get_plan_text():
            self.spoiler.setVisible(True)'''

if old_mark_comp in card_txt:
    card_txt = card_txt.replace(old_mark_comp, new_mark_comp)

# 2. В _init_ui убираем преждевременный вызов mark_completed в шапке
old_timer_block = '''        # Таймер реального времени выполнения
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_timer_tick)
        if self._is_active_task:
            self._timer.start()
        else:
            self.mark_completed()
            if self._saved_elapsed_time:
                self.timer_lbl.setText(self._saved_elapsed_time)'''

new_timer_block = '''        # Таймер реального времени выполнения
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_timer_tick)
        if self._is_active_task:
            self._timer.start()'''

if old_timer_block in card_txt:
    card_txt = card_txt.replace(old_timer_block, new_timer_block)

# 3. Финализация архивной карточки в конце __init__
old_init_end = '''        for fp in self.file_paths:
            if fp.exists():
                self.add_workspace_file_card(fp)'''

new_init_end = '''        for fp in self.file_paths:
            if fp.exists():
                self.add_workspace_file_card(fp)

        # Финализация архивного сообщения из истории
        if not self._is_active_task:
            self.mark_completed()
            if self._saved_elapsed_time:
                self.timer_lbl.setText(self._saved_elapsed_time)'''

if old_init_end in card_txt and new_init_end not in card_txt:
    card_txt = card_txt.replace(old_init_end, new_init_end)

card_p.write_text(card_txt, encoding="utf-8")
print("✓ [ui/widgets/engineer_task_card.py] Защита от порядка инициализации успешно внедрена.")
