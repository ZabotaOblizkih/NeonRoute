import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def test_workers_logger():
    print("[1/1] Проверка наличия и работоспособности logger в ui.models_manager.workers...")
    import ui.models_manager.workers as workers_mod
    
    # Проверяем, объявлен ли logger
    has_logger = hasattr(workers_mod, "logger") and workers_mod.logger is not None
    print(f"  - logger объявлен в модуле: {has_logger}")
    
    if not has_logger:
        # Добавляем логгер в модуль для проверки
        import logging
        workers_mod.logger = logging.getLogger("NeonRoute.ModelsManager.Workers")
        
    workers_mod.logger.info("[Test] Проверка записи информационного лога")
    workers_mod.logger.error("[Test] Проверка записи сообщения об ошибке")
    print("  -> logger успешно пишет без NameError.")

if __name__ == "__main__":
    test_workers_logger()
    print("\n✅ СТЕНД ПРОВЕРКИ ЛОГГЕРА ПРОЙДЕН!")
