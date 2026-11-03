import re
from pathlib import Path

d_path = Path("ui/models_manager_dialog.py")
d_text = d_path.read_text(encoding="utf-8")

# 1. Заменяем _update_fp8_button_state на сверхбыстрый O(1) вариант без I/O
old_upd_pattern = re.compile(
    r'def _update_fp8_button_state\(self\):.*?self\.btn_convert_fp8\.setToolTip\("Конвертирует UNet модели SDXL в нативный FP8\. Освобождает 4 ГБ VRAM\."\)',
    re.DOTALL
)

fast_upd_code = """def _update_fp8_button_state(self):
        \"\"\"
        Обновляет доступность кнопки конвертации FP8 (O(1) без блокирующего сетевого и дискового I/O).
        Кнопка активна, только если выбранный чекпоинт является файлом .safetensors
        и еще не содержит явных маркеров квантования.
        \"\"\"
        if not hasattr(self, "btn_convert_fp8"):
            return
        if self._fp8_worker is not None and self._fp8_worker.isRunning():
            self.btn_convert_fp8.setEnabled(False)
            return

        ckpt = self.combo_checkpoint.currentText().strip()
        is_safetensors = ckpt.lower().endswith(".safetensors")
        is_already_fp8 = any(k in ckpt.lower() for k in ("_fp8", "-fp8", "e4m3fn", "_quant"))

        self.btn_convert_fp8.setEnabled(bool(is_safetensors and not is_already_fp8))
        self.btn_convert_fp8.setToolTip("Конвертирует UNet модели SDXL в нативный FP8. Освобождает 4 ГБ VRAM.")"""

if old_upd_pattern.search(d_text):
    d_text = old_upd_pattern.sub(fast_upd_code, d_text, count=1)
    print("✓ _update_fp8_button_state успешно переведен на нулевую задержку")
else:
    print("! Шаблон _update_fp8_button_state не найден или уже обновлен")

# 2. Добавляем диалог-предохранитель размера (< 4.5 GB) прямо в _on_start_fp8_conversion
old_ckpt_check = """        # 2. Разрешение пути к исходному файлу чекпоинта
        ckpt_path = self._resolve_selected_checkpoint_path(ckpt_name)
        if not ckpt_path or not ckpt_path.is_file():
            InfoBar.error("Ошибка", f"Файл чекпоинта '{ckpt_name}' не найден на диске.", parent=self, position=InfoBarPosition.TOP)
            return"""

new_ckpt_check = """        # 2. Разрешение пути к исходному файлу чекпоинта
        ckpt_path = self._resolve_selected_checkpoint_path(ckpt_name)
        if not ckpt_path or not ckpt_path.is_file():
            InfoBar.error("Ошибка", f"Файл чекпоинта '{ckpt_name}' не найден на диске.", parent=self, position=InfoBarPosition.TOP)
            return

        # Pre-flight Size Guard: проверка на случай случайного повторного сжатия уже компактной модели
        try:
            file_size_gb = ckpt_path.stat().st_size / (1024 ** 3)
            if file_size_gb < 4.5:
                reply = QMessageBox.question(
                    self,
                    "Модель уже оптимизирована",
                    f"Чекпоинт '{ckpt_name}' уже имеет компактный размер ({file_size_gb:.1f} ГБ, типичный SDXL FP16 ~6.6 ГБ).\\n\\n"
                    f"Похоже, модель уже сжата в FP8.\\nВы действительно хотите запустить сжатие повторно?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        except Exception as e:
            logger.debug(f"Pre-flight size check error: {e}")"""

if old_ckpt_check in d_text:
    d_text = d_text.replace(old_ckpt_check, new_ckpt_check, 1)
    print("✓ Предохранитель размера успешно встроен в _on_start_fp8_conversion")
else:
    print("! Точка вставки проверки размера не найдена")

d_path.write_text(d_text, encoding="utf-8")
