from pathlib import Path

w_path = Path("ui/models_manager/workers.py")
text = w_path.read_text(encoding="utf-8")

nl = chr(10)
broken_str = f'tail_msg = "{nl}".join(stdout_tail)'
clean_str = 'tail_msg = chr(10).join(stdout_tail)'

if broken_str in text:
    text = text.replace(broken_str, clean_str, 1)
    w_path.write_text(text, encoding="utf-8")
    print("✓ ui/models_manager/workers.py: синтаксис успешно восстановлен через chr(10)")
else:
    # Запасной вариант с учетом возможных пробелов
    import re
    text = re.sub(r'tail_msg = "[\r\n]+"\.join\(stdout_tail\)', clean_str, text)
    w_path.write_text(text, encoding="utf-8")
    print("✓ ui/models_manager/workers.py: синтаксис восстановлен через regex")
