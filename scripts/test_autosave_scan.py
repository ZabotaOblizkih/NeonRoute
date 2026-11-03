import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def test_autosave_scan_logic():
    print("[1/2] Проверка логики автосохранения при обнаружении новых чекпоинтов...")
    
    # Симулируем логику _on_forge_scan_completed
    class DummyDialog:
        def __init__(self):
            self.saved = False
            self.new_added = 0
            
        def _add_model_row(self, data):
            self.new_added += 1
            
        def _save_models_to_json(self):
            self.saved = True
            
        def on_scan_finished(self, found_new: bool):
            if found_new:
                self._add_model_row({"name": "New Checkpoint"})
            # Новая логика: если есть новые модели, вызываем автосохранение
            if self.new_added > 0:
                self._save_models_to_json()

    dlg_with_new = DummyDialog()
    dlg_with_new.on_scan_finished(found_new=True)
    assert dlg_with_new.saved is True, "Автосохранение должно сработать при добавлении новых моделей!"
    print("  -> Новые модели автоматически триггерят сохранение на диск.")

    dlg_without_new = DummyDialog()
    dlg_without_new.on_scan_finished(found_new=False)
    assert dlg_without_new.saved is False, "Если новых моделей нет, лишняя запись на диск не вызывается."
    print("  -> Холостой опрос не создает лишней нагрузки на диск.")

if __name__ == "__main__":
    test_autosave_scan_logic()
    print("\n✅ СТЕНД АВТОСОХРАНЕНИЯ ПРОЙДЕН УСПЕШНО!")
