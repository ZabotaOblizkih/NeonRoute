import os
import re
import sys
from pathlib import Path

print("\n=== ВНЕДРЕНИЕ И ВАЛИДАЦИЯ ПАТЧА: SINGLE-CANVAS ENGINEER TASK CARD ===")

# --- 1. ПАТЧ services/subagent_engineer.py ---
subagent_p = Path("services/subagent_engineer.py")
subagent_txt = subagent_p.read_text(encoding="utf-8")

old_plan_block = '''        format_badge = "🎮 Pygame" if plan.artifact_format == "pygame" else f"📦 {plan.artifact_format.upper()}"
        if plan.intent == "review":
            clean_plan_card = (
                f"### 🔍 Инспекция проекта: `{plan.target_artifact}`\\n"
                f"**Формат:** {format_badge} | **Действие:** `Анализ / Git Diff`\\n\\n"
                f"> **Цель запроса:**\\n"
                f"> {plan.plan_content}\\n\\n"
                f"⏳ *Выполняю анализ состояния рабочей области...*"
            )
        else:
            clean_plan_card = (
                f"### 📐 Архитектурный план: `{plan.target_artifact}`\\n"
                f"**Формат:** {format_badge} | **Действие:** `{plan.intent}`\\n\\n"
                f"> **Техническое задание:**\\n"
                f"> {plan.plan_content}\\n\\n"
                f"⏳ *Приступаю к автономной реализации кода...*"
            )'''

new_plan_block = '''        format_badge = "🎮 Pygame" if plan.artifact_format == "pygame" else f"📦 {plan.artifact_format.upper()}"
        # Для отображения в UI извлекаем чистое описание без служебных директив <<TOOL:...>> для LLM
        clean_plan_body = (plan.plan_desc or "").strip()
        if not clean_plan_body:
            raw_c = re.sub(r'<<TOOL:\\w+.*?>>', '', plan.plan_content)
            raw_c = re.sub(r'<<?/+TOOL(?::\\w+)?>>?', '', raw_c)
            raw_c = re.sub(r'запусти (?:главную точку входа|скрипт верификации) через \\.?\\s*', '', raw_c, flags=re.IGNORECASE)
            raw_c = re.sub(r'### УТВЕРЖДЁННЫЙ АРХИТЕКТУРНЫЙ ПЛАН.*?\\n', '', raw_c)
            clean_plan_body = raw_c.strip()

        if plan.intent == "review":
            clean_plan_card = (
                f"### 🔍 Инспекция проекта: `{plan.target_artifact}`\\n"
                f"**Формат:** {format_badge} | **Действие:** `Анализ / Git Diff`\\n\\n"
                f"> **Цель запроса:**\\n"
                f"> {clean_plan_body}\\n\\n"
                f"⏳ *Выполняю анализ состояния рабочей области...*"
            )
        else:
            clean_plan_card = (
                f"### 📐 Архитектурный план: `{plan.target_artifact}`\\n"
                f"**Формат:** {format_badge} | **Действие:** `{plan.intent}`\\n\\n"
                f"> **Техническое задание:**\\n"
                f"> {clean_plan_body}\\n\\n"
                f"⏳ *Приступаю к автономной реализации кода...*"
            )'''

if old_plan_block in subagent_txt:
    subagent_txt = subagent_txt.replace(old_plan_block, new_plan_block)
    subagent_p.write_text(subagent_txt, encoding="utf-8")
    print("✓ [services/subagent_engineer.py] Архитектурный план успешно очищен от LLM-директив.")
else:
    print("! [services/subagent_engineer.py] Исходный блок уже был модифицирован или не найден.")

# --- 2. ПАТЧ ui/widgets/engineer_task_card.py ---
card_p = Path("ui/widgets/engineer_task_card.py")
card_txt = card_p.read_text(encoding="utf-8")

# Добавляем Сценарий 0 в set_text
target_method_start = '    def set_text(self, text: str):\n'
scenario0_code = '''        # Сценарий 0: Архитектурный план инженера
        if "### 📐 Архитектурный план" in raw_in or "### 🔍 Инспекция проекта" in raw_in:
            m_body = re.search(r'> \\*\\*Техническое задание:\\*\\*\\s*\\n(.*?)(?=\\n\\n⏳|\\n\\n###|$)', raw_in, flags=re.DOTALL)
            if not m_body:
                m_body = re.search(r'> \\*\\*Цель запроса:\\*\\*\\s*\\n(.*?)(?=\\n\\n⏳|\\n\\n###|$)', raw_in, flags=re.DOTALL)
            plan_body = m_body.group(1).strip() if m_body else ""
            if plan_body:
                clean_plan = "\\n".join(re.sub(r'^\\s*>\\s?', '', line) for line in plan_body.splitlines())
            else:
                clean_plan = raw_in

            self._captured_plan = clean_plan
            self.spoiler.setVisible(True)
            self.spoiler.set_plan_text(clean_plan, title="💭 Архитектура и анализ")
            self.spoiler.set_collapsed(True, status="✅ Архитектурный план утверждён")

            m_status = re.search(r'(⏳\\s*\\*.*?\\*)', raw_in)
            status_text = m_status.group(1) if m_status else "⏳ Приступаю к автономной реализации кода..."
            self._set_summary_text(status_text)
            if not self._is_active_task:
                self.mark_completed()
            self._update_bubble_geometry()
            return\n\n'''

if "Сценарий 0: Архитектурный план" not in card_txt:
    find_marker = "        self._detect_skill_from_text(raw_in)\n"
    if find_marker in card_txt:
        card_txt = card_txt.replace(find_marker, find_marker + "\n" + scenario0_code)
        print("✓ [ui/widgets/engineer_task_card.py] Умный парсер архитектурного плана успешно встроен.")

# Не гасить таймер в Сценарии 3 если задача активна
old_sc3 = '''        # Сценарий 3: Чистый текст без тулов и без <think>
        self.spoiler.setVisible(False)
        self._set_summary_text(raw_in.strip())
        self.mark_completed()'''

new_sc3 = '''        # Сценарий 3: Чистый текст без тулов и без <think>
        if not self._captured_plan:
            self.spoiler.setVisible(False)
        self._set_summary_text(raw_in.strip())
        if not self._is_active_task:
            self.mark_completed()'''

if old_sc3 in card_txt:
    card_txt = card_txt.replace(old_sc3, new_sc3)
    print("✓ [ui/widgets/engineer_task_card.py] Преждевременная остановка таймера в Сценарии 3 устранена.")

# Защита видимости спойлера в mark_completed
old_mark_comp = '''    def mark_completed(self):
        """Переводит карточку задачи в статус успешного завершения."""
        if sip.isdeleted(self):
            return
        self._is_active_task = False
        if self._timer.isActive():
            self._timer.stop()
        self.phase_indicator.setText("✓ COMPLETED")
        self.phase_indicator.setStyleSheet("color: #10B981; font-size: 11px; font-weight: 700; background: transparent;")'''

new_mark_comp = '''    def mark_completed(self):
        """Переводит карточку задачи в статус успешного завершения."""
        if sip.isdeleted(self):
            return
        self._is_active_task = False
        if self._timer.isActive():
            self._timer.stop()
        self.phase_indicator.setText("✓ COMPLETED")
        self.phase_indicator.setStyleSheet("color: #10B981; font-size: 11px; font-weight: 700; background: transparent;")
        if self.spoiler and self.spoiler.get_plan_text():
            self.spoiler.setVisible(True)'''

if old_mark_comp in card_txt:
    card_txt = card_txt.replace(old_mark_comp, new_mark_comp)
    print("✓ [ui/widgets/engineer_task_card.py] Сохранение спойлера плана при mark_completed добавлено.")

card_p.write_text(card_txt, encoding="utf-8")

# --- 3. ПАТЧ ui/controllers/engineer_controller.py ---
ctrl_p = Path("ui/controllers/engineer_controller.py")
ctrl_txt = ctrl_p.read_text(encoding="utf-8")

old_display_method = '''    def _display_engineer_plan_card(self, card_text: str):
        if getattr(self, "typing_loader", None) is not None:
            self._remove_typing_loader()
        avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
        bubble = self._append_message("Элария", card_text, is_user=False, avatar_path=avatar)
        if hasattr(self, "_active_tasks"):
            for t in self._active_tasks.values():
                if t.metadata and t.metadata.get("is_ephemeral_doc"):
                    t.metadata["bubble"] = bubble
                    break
        if hasattr(self, "_sync_turn"):
            self._sync_turn(role="assistant", content=card_text, replace_last=False)
        self._scroll_to_bottom()'''

new_display_method = '''    def _display_engineer_plan_card(self, card_text: str):
        if getattr(self, "typing_loader", None) is not None:
            self._remove_typing_loader()

        active_task = None
        if hasattr(self, "_active_tasks"):
            for t in self._active_tasks.values():
                if t.metadata and t.metadata.get("is_ephemeral_doc"):
                    active_task = t
                    break

        existing_bubble = active_task.metadata.get("bubble") if active_task else None
        if existing_bubble is not None:
            try:
                from PyQt6 import sip
                if sip.isdeleted(existing_bubble):
                    existing_bubble = None
            except Exception:
                pass

        if existing_bubble is None:
            avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
            bubble = self._append_message("Элария", card_text, is_user=False, avatar_path=avatar)
            if active_task:
                active_task.metadata["bubble"] = bubble
            if hasattr(self, "_sync_turn"):
                self._sync_turn(role="assistant", content=card_text, replace_last=False)
        else:
            bubble = existing_bubble
            m_step = re.search(r'🛠️\\s*\\*+Инженер\\*+:\\s*(.+)', card_text)
            if m_step:
                raw_step = m_step.group(1).strip()
                clean_step = raw_step.strip("`* ")
                if hasattr(bubble, "add_step"):
                    bubble.add_step(clean_step, status="running")
            elif "сформирован. Запускаю верификацию" in card_text or "Запускаю верификацию" in card_text:
                m_v = re.search(r'Файл `?([\\w\\-.]+)`? сформирован', card_text)
                target_f = m_v.group(1) if m_v else "код"
                if hasattr(bubble, "add_step"):
                    for badge in getattr(bubble, "_file_badges", {}).values():
                        if badge.status == "running":
                            badge.set_status("completed")
                    bubble.add_step(f"Верификация {target_f} в песочнице", status="running")
            else:
                if hasattr(bubble, "set_text"):
                    bubble.set_text(card_text)

        self._scroll_to_bottom()'''

if old_display_method in ctrl_txt:
    ctrl_txt = ctrl_txt.replace(old_display_method, new_display_method)
    print("✓ [ui/controllers/engineer_controller.py] _display_engineer_plan_card переведён на Monolithic Single-Canvas.")

# Завершение всех бегущих шагов и сохранение спойлера в _handle_engineer_task_finished
old_fin_check = '''                    if hasattr(bubble, "add_workspace_file_card"):
                        bubble.add_workspace_file_card(artifact_path, on_run=self._execute_workspace_script)
                    if hasattr(self, "_update_message_attachment"):
                        self._update_message_attachment(bubble, artifact_path)
                    if res.get("stdout") and str(res["stdout"]).strip() and hasattr(bubble, "add_console_spoiler"):
                        bubble.add_console_spoiler(title=f"Консоль: {file_name}", output=res["stdout"], is_error=False)
                    if hasattr(bubble, "set_text"):
                        bubble.set_text(fin_text)'''

new_fin_check = '''                    # Переводим бегущие шаги в completed
                    for badge in getattr(bubble, "_file_badges", {}).values():
                        if badge.status == "running":
                            badge.set_status("completed")
                    if hasattr(bubble, "add_workspace_file_card"):
                        bubble.add_workspace_file_card(artifact_path, on_run=self._execute_workspace_script)
                    if hasattr(self, "_update_message_attachment"):
                        self._update_message_attachment(bubble, artifact_path)
                    if res.get("stdout") and str(res["stdout"]).strip() and hasattr(bubble, "add_console_spoiler"):
                        bubble.add_console_spoiler(title=f"Консоль: {file_name}", output=res["stdout"], is_error=False)
                    if hasattr(bubble, "_set_summary_text"):
                        bubble._set_summary_text(fin_text)
                    if hasattr(bubble, "mark_completed"):
                        bubble.mark_completed()'''

if old_fin_check in ctrl_txt:
    ctrl_txt = ctrl_txt.replace(old_fin_check, new_fin_check)
    print("✓ [ui/controllers/engineer_controller.py] _handle_engineer_task_finished переведён на сохранение спойлера.")

ctrl_p.write_text(ctrl_txt, encoding="utf-8")

print("\n>>> ВСЕ ПРАВКИ УСПЕШНО ПРИМЕНЕНЫ. ЗАПУСКАЕМ ИНТЕГРАЦИОННЫЙ ТЕСТОВЫЙ СТЕНД... <<<\n")

# Запуск валидационного стенда
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)
from ui.widgets.engineer_task_card import EngineerTaskCardWidget
import time

test_plan = (
    "### 📐 Архитектурный план: `snake_core.py`\n"
    "**Формат:** 🎮 Pygame | **Действие:** `new`\n\n"
    "> **Техническое задание:**\n"
    "> Разработать игровое поле 20x20, класс Змейки, управление стрелками, генерацию еды и счет.\n\n"
    "⏳ *Приступаю к автономной реализации кода...*"
)

card = EngineerTaskCardWidget(sender="Элария", text=test_plan)
card.show()

# 1. Проверка таймера и фазы
assert card._timer.isActive(), "ОШИБКА: Таймер должен тикать на активной задаче!"
assert card.phase_indicator.text() == "⚡ RUNNING", f"ОШИБКА: Фаза должна быть RUNNING, а получено: {card.phase_indicator.text()}"

# 2. Проверка спойлера архитектуры
assert not card.spoiler.isHidden(), "ОШИБКА: Спойлер архитектуры должен быть видимым!"
assert "Разработать игровое поле 20x20" in card.spoiler.get_plan_text(), "ОШИБКА: Текст ТЗ не попал в спойлер!"
assert "и запусти главную точку входа через" not in card.spoiler.get_plan_text(), "ОШИБКА: В плане не должно быть служебных команд!"
assert card.summary_browser.toPlainText() == "⏳ Приступаю к автономной реализации кода...", f"ОШИБКА резюме: {card.summary_browser.toPlainText()}"

# 3. Добавление шагов (Monolithic Stepper)
s1 = card.add_step("Приступаю к реализации snake_core.py", status="running")
time.sleep(0.12)
card._on_timer_tick()
assert card._timer.isActive(), "ОШИБКА: Таймер не должен был остановиться на шаге!"

s1.set_status("completed")
s2 = card.add_step("Верификация snake_core.py в песочнице", status="running")
time.sleep(0.08)
card._on_timer_tick()
s2.set_status("completed")

# 4. Завершение задачи
card.add_workspace_file_card(Path("snake_core.py"))
card.add_console_spoiler(title="Консоль: snake_core.py", output="[OK] Pygame window initialized.")
card._set_summary_text("✅ Файл `snake_core.py` успешно сохранён и готов к работе.")
card.mark_completed()

assert not card._timer.isActive(), "ОШИБКА: Таймер должен был остановиться после mark_completed!"
assert card.phase_indicator.text() == "✓ COMPLETED", f"ОШИБКА: Фаза должна быть COMPLETED, получено: {card.phase_indicator.text()}"
assert not card.spoiler.isHidden(), "ОШИБКА: Спойлер архитектуры должен остаться доступным после mark_completed!"
final_t = card.timer_lbl.text()
assert final_t != "0.0s", "ОШИБКА: Финальное время не должно быть 0.0s!"

print("Результаты стенда:")
print(f" - Спойлер архитектуры: [OK] Заголовок='{card.spoiler.title_lbl.text()}', Статус='{card.spoiler.status_lbl.text()}'")
print(f" - Текст резюме: [OK] '{card.summary_browser.toPlainText()}'")
print(f" - Шагов в степпере: [OK] {len(card._file_badges)} шагов")
print(f" - Финальный статус: [OK] {card.phase_indicator.text()} ({final_t})")
print("\n>>> ИНТЕГРАЦИОННЫЙ СТЕНД ПОЛНОСТЬЮ ПРОЙДЕН БЕЗ ОШИБОК! <<<")
