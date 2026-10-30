import sys
import json
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

from services.session_manager import SessionManager

print("=" * 80)
print(" ДИАГНОСТИКА: НАСТРОЙКИ ПЕРСОНЫ ИГРОКА (USER PERSONA)")
print("=" * 80)

# 1. Активная персона для ролевого чата из настроек
active_p = SessionManager.get_active_user_persona(is_roleplay=True)
print("\n[1. ГЛОБАЛЬНО АКТИВНАЯ ПЕРСОНА (Ролевой чат)]:")
print(f"• ID:          {active_p.get('id')}")
print(f"• Имя (name):  {active_p.get('name') or '<НЕ УКАЗАНО (пусто)>'}")
print(f"• Описание:    {(active_p.get('description') or '')[:100]}...")

# 2. Проверяем привязку персоны в последней сессии
possible_sessions = list((project_root / "outputs" / "sessions").glob("*/session.json"))
if possible_sessions:
    latest_sess_file = max(possible_sessions, key=lambda f: f.stat().st_mtime)
    s_data = json.loads(latest_sess_file.read_text(encoding="utf-8"))
    sess_persona_id = s_data.get("user_persona_id")
    print("\n[2. ПЕРСОНА В ПОСЛЕДНЕЙ АКТИВНОЙ СЕССИИ]:")
    print(f"• Сессия:          {latest_sess_file.parent.name}")
    print(f"• user_persona_id: {sess_persona_id or '<Не привязана (null)>'}")
    
    # Резолв персоны для этой конкретной сессии
    sess_p = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=sess_persona_id)
    resolved_name = sess_p.get("name") or "Игрок"
    print(f"• Результирующее имя для якоря: '{resolved_name}'")

print("=" * 80)
