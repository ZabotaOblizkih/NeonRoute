import sys
from pathlib import Path

# Корень проекта
project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=== 1. ИСХОДНЫЙ КОД СБОРКИ MESSAGES В services/roleplay_flow_controller.py ===")
p_rfc = Path("services/roleplay_flow_controller.py")
lines = p_rfc.read_text(encoding="utf-8").splitlines()
# Выводим строки 190..275 перед вызовом llm_kwargs
for i in range(190, min(len(lines), 276)):
    print(f"{i+1:4d}: {lines[i]}")

print("\n" + "=" * 80)
print("=== 2. СИМУЛЯЦИЯ ПОЛНОГО МАССИВА MESSAGES ДЛЯ СЕССИИ 'Isekai RPG' ===")
print("=" * 80)

from services.tavern_parser import TavernParser
from services.context_window_builder import ContextWindowBuilder
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.rp_cast_manager import RPCastManager

card = TavernParser.parse_file("characters/Isekai RPG.json")

# Собираем системный промпт через боевой ContextWindowBuilder
sys_prompt = ContextWindowBuilder.build_system_prompt(
    character_card=card,
    user_name="Игрок",
    is_roleplay=True,
    is_engineer=False
)

# Проверяем, как формируется история диалога на старте:
# В типичном начале чата: [system_prompt, first_mes (assistant), user_message]
# Либо first_mes не попадает в messages? Проверим!
print(f"Длина сгенерированного System Prompt: {len(sys_prompt)} символов.")

first_mes_text = getattr(card, "first_mes_ru", None) or getattr(card, "first_mes", "") or ""
print(f"Длина first_mes из карточки: {len(first_mes_text)} символов.")

# Посмотрим, как в UI/Контроллере выглядит chat_history на старте
# Смотрим вызов _build_messages или аналогичного метода
for m_name in ("_build_llm_messages", "_prepare_llm_messages", "_build_messages", "_prepare_messages"):
    for l in lines:
        if m_name in l:
            print(f"Найден метод в коде: {l.strip()}")
