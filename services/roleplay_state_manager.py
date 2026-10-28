"""
services/roleplay_state_manager.py
Детерминированный перехват метаданных ЛитРПГ/статуса и санитизация текста.
"""

import re
import ast
from typing import Dict, Any, List, Optional
from services.rp_cast_manager import RPCastManager


class RoleplayStateManager:
    cleanup_session = staticmethod(RPCastManager.cleanup_session)
    load_cast = staticmethod(RPCastManager.load_cast)
    save_cast = staticmethod(RPCastManager.save_cast)

    @staticmethod
    def parse_explicit_status(text: str) -> Dict[str, Any]:
        """Извлекает структурированные данные из блоков <stats> или [STATUS]."""
        res: Dict[str, Any] = {"location": None, "inventory": [], "quest": None, "stats": {}}
        if not text:
            return res

        block = ""
        m_tag = re.search(r'<stats>(.*?)</stats>', text, flags=re.DOTALL | re.IGNORECASE)
        if m_tag:
            block = m_tag.group(1)
        else:
            m_bracket = re.search(r'\[(?:STATUS|СТАТУС)[:\s]+(.*?)\]', text, flags=re.DOTALL | re.IGNORECASE)
            if m_bracket:
                block = m_bracket.group(1)

        if not block:
            return res

        lines = [l.strip() for l in block.splitlines() if l.strip() and not l.strip().startswith("```")]
        for l in lines:
            # 1. Локация
            m_loc = re.match(r'^(?:Местоположение|Локация|Location)\s*:\s*(.+)$', l, flags=re.IGNORECASE)
            if m_loc:
                val = m_loc.group(1).strip().strip('"\'')
                if val and val not in ("???", "Н/А", "None", "null"):
                    res["location"] = val
                continue

            # 2. Инвентарь
            m_inv = re.match(r'^(?:Инвентарь|Inventory)\s*:\s*(.+)$', l, flags=re.IGNORECASE)
            if m_inv:
                raw_inv = m_inv.group(1).strip()
                try:
                    parsed = ast.literal_eval(raw_inv)
                    if isinstance(parsed, list):
                        res["inventory"].extend([str(x).strip() for x in parsed if str(x).strip()])
                except Exception:
                    res["inventory"].extend([x.strip().strip('"\'') for x in raw_inv.strip("[]").split(",") if x.strip()])
                continue

            # 3. Экипировка
            m_eq = re.match(r'^(?:Оборудование|Экипировка|Equipment)\s*:\s*(.+)$', l, flags=re.IGNORECASE)
            if m_eq:
                raw_eq = m_eq.group(1).strip()
                try:
                    parsed = ast.literal_eval(raw_eq)
                    if isinstance(parsed, list):
                        for eq in parsed:
                            item_name = f"{str(eq).strip()} (экипировано)"
                            if item_name not in res["inventory"]:
                                res["inventory"].append(item_name)
                except Exception:
                    pass
                continue

            # 4. Цели / Квест
            m_q = re.match(r'^(?:Цели|Цель|Квест|Objectives?)\s*:\s*(.+)$', l, flags=re.IGNORECASE)
            if m_q:
                q_val = m_q.group(1).strip().strip('"\'[]')
                if q_val and q_val not in ("???", "Н/А", "None"):
                    res["quest"] = q_val
                continue

            # 5. Характеристики ЛитРПГ
            for st_key in ("Класс", "Class", "Уровень", "Level", "Золото", "Gold", "BP", "Battle Power", "Репутация", "Reputation", "Уровень угрозы", "Threat level"):
                m_st = re.match(rf'^{st_key}\s*:\s*(.+)$', l, flags=re.IGNORECASE)
                if m_st:
                    v = m_st.group(1).strip().strip('"\'')
                    if v and not v.startswith("???"):
                        res["stats"][st_key] = v
                    break

        return res

    @classmethod
    def process_model_turn(cls, session_id: Optional[str], response_text: str) -> str:
        """Перехватывает статус-блоки в cast_data и очищает чат ТОЛЬКО от сервисных тегов, сохраняя разметку для UI."""
        if not response_text:
            return response_text

        # 1. Детерминированный перехват явного статуса в каст сессии
        if session_id:
            try:
                parsed = cls.parse_explicit_status(response_text)
                if parsed["location"] or parsed["inventory"] or parsed["quest"] or parsed["stats"]:
                    cast = RPCastManager.load_cast(session_id)
                    mod = False
                    if parsed["location"]:
                        cast["current_location"] = parsed["location"]
                        mod = True
                    if parsed["inventory"]:
                        cast["inventory"] = parsed["inventory"]
                        mod = True
                    if parsed["quest"]:
                        cast["active_quest"] = parsed["quest"]
                        mod = True
                    if parsed["stats"]:
                        cast.setdefault("stats", {}).update(parsed["stats"])
                        mod = True
                    if mod:
                        RPCastManager.save_cast(session_id, cast)
            except Exception:
                pass

        # 2. Очистка ТОЛЬКО бэкенд-тегов в скобках [STATUS], [SCENE], [IMAGE_PROMPT]
        # ВАЖНО: теги <stats>, <thought>, <meters>, <choices>, <check>, <terminal> НЕ УДАЛЯЮТСЯ!
        # Их парсит и превращает в красивые виджеты ui/components/rp/rp_chat_bubble.py (RPGContentParser).
        cleaned = re.sub(
            r'\[(?:СЦЕНА|SCENE|СТАТУС|STATUS|ШРАМ|SCAR|КВЕСТ|QUEST|ВЕХА|MILESTONE|IMAGE_PROMPT)[:\s]+[^\]\n]+\]',
            '',
            response_text,
            flags=re.IGNORECASE
        )
        cleaned = re.sub(
            r'(?:^|\n)\s*[-*•]?\s*(?:СЦЕНА|SCENE)[:\s]+[^\n]+',
            '',
            cleaned,
            flags=re.IGNORECASE
        )
        return cleaned.strip()
