from pathlib import Path
import re

# 1. Исправляем models/convert_sdxl_to_fp8.py
m_path = Path("models/convert_sdxl_to_fp8.py")
m_text = m_path.read_text(encoding="utf-8")

# Заменяем однострочный print с \n на два чистых print без слэшей
pattern_broken = re.compile(
    r'print\(f"\[!\] КРИТИЧЕСКАЯ ОШИБКА ПРИ СОХРАНЕНИИ ФАЙЛА:[\r\n]+.*?sys\.exit\(1\)',
    re.DOTALL
)

clean_error_block = """print("[!] КРИТИЧЕСКАЯ ОШИБКА ПРИ СОХРАНЕНИИ ФАЙЛА:", flush=True)
    print(err_tb, flush=True)
    sys.exit(1)"""

if pattern_broken.search(m_text):
    m_text = pattern_broken.sub(clean_error_block, m_text, count=1)
    m_path.write_text(m_text, encoding="utf-8")
    print("✓ models/convert_sdxl_to_fp8.py: синтаксис print успешно восстановлен")
else:
    print("! Блок ошибки в convert_sdxl_to_fp8.py не найден по регулярке")

# 2. Проверяем ui/models_manager_dialog.py на разорванные строки
d_path = Path("ui/models_manager_dialog.py")
d_text = d_path.read_text(encoding="utf-8")

# Убираем любые разорванные кавычки в районе _on_fp8_failed
d_text = re.sub(
    r'logger\.error\(f"\[ModelsManagerDialog\] Сбой сжатия FP8:[\r\n]+.*?"\)',
    'logger.error(f"[ModelsManagerDialog] Сбой сжатия FP8: " + str(err_msg))',
    d_text
)

d_text = re.sub(
    r'f"Не удалось сжать чекпоинт в FP8\.[\r\n]+.*?"',
    'chr(10).join(["Не удалось сжать чекпоинт в FP8.", "", "Детали ошибки:", str(err_msg), "", "(Полный лог сохранен в logs/neonroute.log)"])',
    d_text
)

d_path.write_text(d_text, encoding="utf-8")
print("✓ ui/models_manager_dialog.py: форматирование MessageBox защищено через chr(10)")
