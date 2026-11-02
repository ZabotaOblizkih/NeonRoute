from pathlib import Path
import re

file_path = Path("services/roleplay_flow_controller.py")
if not file_path.exists():
    print(f"❌ Файл {file_path} не найден.")
    exit(1)

content = file_path.read_text(encoding="utf-8")

# Ищем место вызова build_depth_anchor
target_pattern = r'(depth_anchor\s*=\s*RoleplayPromptBuilder\.build_depth_anchor\([\s\S]*?\))'
match = re.search(target_pattern, content)

log_statement = '\n            logger.info(f"[RPFlow.DepthAnchor] ⚓ Активный Depth-0 якорь (кастомный={bool(custom_anchor)}):\\n{depth_anchor.strip()}")'

if match:
    # Проверяем, не добавлен ли лог уже
    if "[RPFlow.DepthAnchor]" in content:
        print("ℹ️ Логирование якоря уже присутствует в roleplay_flow_controller.py.")
    else:
        new_block = match.group(1) + log_statement
        updated_content = content[:match.start()] + new_block + content[match.end():]
        file_path.write_text(updated_content, encoding="utf-8")
        print("✅ Логирование якоря успешно добавлено в roleplay_flow_controller.py!")
else:
    print("❌ Не удалось найти вызов build_depth_anchor для вставки лога.")
