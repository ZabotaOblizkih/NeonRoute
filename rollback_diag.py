import re
from pathlib import Path

# 1. Откат ui/controllers/chat_flow_controller.py
p_chat = Path("ui/controllers/chat_flow_controller.py")
if p_chat.exists():
    txt = p_chat.read_text(encoding="utf-8")
    # Удаляем любой блок диагностики между def _on_llm_response_received и docstring
    pattern = r"def _on_llm_response_received\(self,\s*text:\s*str\):[\s\S]*?(?=\"\"\"\s*\n\s*Единый диспетчер)"
    if re.search(pattern, txt):
        txt = re.sub(pattern, "def _on_llm_response_received(self, text: str):\n        ", txt, count=1)
        p_chat.write_text(txt, encoding="utf-8")
        print("✓ chat_flow_controller.py успешно очищен от инъекции.")
    else:
        print("! Блок инъекции в chat_flow_controller.py не совпал с шаблоном, проверяем синтаксис...")

# 2. Откат ui/controllers/assistant_flow_controller.py
p_asst = Path("ui/controllers/assistant_flow_controller.py")
if p_asst.exists():
    txt2 = p_asst.read_text(encoding="utf-8")
    pattern2 = r"def _handle_assistant_llm_response\(self,\s*text:\s*str\):[\s\S]*?(?=\"\"\"\s*Обрабатывает ответ Эларии)"
    if re.search(pattern2, txt2):
        txt2 = re.sub(pattern2, "def _handle_assistant_llm_response(self, text: str):\n        ", txt2, count=1)
        p_asst.write_text(txt2, encoding="utf-8")
        print("✓ assistant_flow_controller.py успешно очищен от инъекции.")
    else:
        print("- assistant_flow_controller.py уже чист.")

# 3. Удаление временных файлов дампа, если успели создаться
for f in ["last_llm_raw_dump.txt", "root_llm_dump.txt", "inject_diag_hook.py", "inject_root_diag.py"]:
    p = Path(f)
    if p.exists():
        p.unlink()
        print(f"✓ Временный файл {f} удален.")
