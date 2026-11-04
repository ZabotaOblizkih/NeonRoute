"""
Session Management & History Storage for NeonRoute
Persists chat conversations to C:\\NeonRoute\\history\\chat_<id>.json
Supports session switching, visual chat differentiation, and character state restoration.
Includes Dynamic Token Window, Session Lorebook (<WorldState>), and On-demand Deep Recall (<SceneMemory>).
"""

import os
import re
import json
import time
import uuid
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

from config import (
    HISTORY_DIR, OUTPUTS_DIR, CHARACTERS_DIR, DATA_DIR, MODE_PROFILES
)

from services.user_profile_manager import (
    USER_PROFILES_FILE,
    DEFAULT_USER_PROFILES,
    UserProfileManager,
)
from services.tavern_parser import CharacterCard, TavernParser
from services.memory_db import memory_db
from services.rp_cast_manager import RPCastManager
from services.token_budget_manager import TokenBudgetManager

logger = logging.getLogger("NeonRoute.SessionManager")


from services.engineer_prompts import get_engineer_system_prompt, MINI_ANTIGRAVITY_SYSTEM_PROMPT


from services.context_window_builder import (
    LORE_TRIGGER_PATTERNS,
    RECALL_TRIGGERS,
    STOP_WORDS_RECALL,
    ContextWindowBuilder,
)


DEFAULT_SAMPLING_BY_MODE: Dict[str, Dict[str, Any]] = {
    k: v["sampling"] for k, v in MODE_PROFILES.items()
}


def get_mode_profile(mode: str = "assistant") -> Dict[str, Any]:
    """Возвращает параметры режима (SSOT) с защитой мастер-предохранителем."""
    from utils.config_manager import ConfigManager
    from config import HARDWARE_MAX_CONTEXT_LENGTH, MASTER_MAX_RESPONSE_TOKENS

    norm = (mode or "assistant").lower().strip()
    key = "engineer" if norm.startswith("eng") else ("roleplay" if norm == "roleplay" else "assistant")

    import copy
    profile = copy.deepcopy(MODE_PROFILES.get(key, MODE_PROFILES["assistant"]))
    cfg = ConfigManager.load_config()

    cfg_sampling_key = f"default_sampling_{key}"
    if isinstance(cfg.get(cfg_sampling_key), dict):
        profile["sampling"].update(cfg[cfg_sampling_key])

    cfg_ctx_key = f"context_length_{key}"
    target_ctx = cfg.get(cfg_ctx_key) or (cfg.get("context_length") if key == "roleplay" else None)
    if target_ctx:
        try:
            profile["context_length"] = int(target_ctx)
        except (ValueError, TypeError):
            pass

    # Мастер-предохранитель: аппаратный потолок VRAM и длины ответа
    profile["context_length"] = min(int(profile.get("context_length", 14336)), HARDWARE_MAX_CONTEXT_LENGTH)
    if "sampling" in profile and "max_tokens" in profile["sampling"]:
        profile["sampling"]["max_tokens"] = min(int(profile["sampling"]["max_tokens"]), MASTER_MAX_RESPONSE_TOKENS)

    return profile


def get_default_sampling_params(mode: str = "assistant") -> Dict[str, Any]:
    return dict(get_mode_profile(mode).get("sampling", {}))


class SessionManager:
    _instance: Optional['SessionManager'] = None
    _current_session: Optional[Dict[str, Any]] = None
    _sessions_cache: Dict[str, Dict[str, Any]] = {}
    _greetings_cache: Dict[str, str] = UserProfileManager._greetings_cache
    lm_client: Optional[Any] = None
    cast_manager: Any = RPCastManager

    load_user_profiles = staticmethod(UserProfileManager.load_user_profiles)
    save_user_profiles = staticmethod(UserProfileManager.save_user_profiles)
    get_active_user_persona = staticmethod(UserProfileManager.get_active_user_persona)
    get_cached_greeting_ru = staticmethod(UserProfileManager.get_cached_greeting_ru)
    save_character_greeting_ru = staticmethod(UserProfileManager.save_character_greeting_ru)
    extract_world_facts_from_history = staticmethod(ContextWindowBuilder.extract_world_facts_from_history)
    get_dynamic_art_models_protocol = staticmethod(ContextWindowBuilder.get_dynamic_art_models_protocol)
    build_system_prompt = staticmethod(ContextWindowBuilder.build_system_prompt)
    get_lm_studio_messages = staticmethod(ContextWindowBuilder.get_lm_studio_messages)
    prepare_history_for_llm = staticmethod(ContextWindowBuilder.prepare_history_for_llm)
    build_context_messages = staticmethod(ContextWindowBuilder.build_context_messages)

    def __init__(self, lm_client: Optional[Any] = None, cast_manager: Optional[Any] = None):
        self.lm_client = lm_client
        self.cast_manager = cast_manager or RPCastManager

    @classmethod
    def get_instance(cls, lm_client: Optional[Any] = None) -> 'SessionManager':
        if cls._instance is None:
            cls._instance = cls(lm_client)
        elif lm_client is not None:
            cls._instance.lm_client = lm_client
        return cls._instance

    @classmethod
    def set_active_session(cls, session: Optional[Dict[str, Any]]) -> None:
        cls._current_session = session
        if session and isinstance(session, dict) and "id" in session:
            cls._sessions_cache[str(session["id"])] = session


    @staticmethod
    def generate_session_id() -> str:
        return f"{int(time.time())}_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def generate_title_from_prompt(prompt: str) -> str:
        clean = " ".join(prompt.strip().split())
        if not clean:
            return "Новый диалог"
        return (clean[:20].strip() + "...") if len(clean) > 20 else clean

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return TokenBudgetManager.estimate_text_tokens(text)

    @staticmethod
    def _estimate_message_tokens(m: Dict[str, Any]) -> int:
        return TokenBudgetManager.estimate_message_tokens(m)

    @staticmethod
    def calculate_translation_budget(text: str, max_limit: Optional[int] = None) -> int:
        """Calculates adaptive token budget for translation based on source text length."""
        if not text:
            return 1024
        from utils.config_manager import ConfigManager
        if max_limit is None:
            cfg = ConfigManager.load_config()
            max_limit = int((cfg.get("translation_settings") or {}).get("max_tokens_limit", 4096))
        est = int(SessionManager.estimate_tokens(text) * 1.45) + 150
        return max(1024, min(max_limit, est))

    @staticmethod
    def create_session(
        mode: str = "assistant",
        character_card: Optional[CharacterCard] = None,
        title: Optional[str] = None,
        model_id: str = "",
        is_model_locked: bool = False,
        user_persona_id: Optional[str] = None,
        background_image: Optional[str] = None,
        custom_system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        now = datetime.now()
        now_iso = now.isoformat()
        session_id = str(uuid.uuid4())

        char_data = None
        card_obj = None
        char_id = None
        if character_card:
            if isinstance(character_card, CharacterCard):
                char_data = character_card.to_dict()
                card_obj = character_card
            elif isinstance(character_card, dict):
                card_obj = CharacterCard.from_dict(character_card)
                char_data = card_obj.to_dict()
            else:
                card_obj = None

            if card_obj:
                char_id = getattr(card_obj, "id", None) or f"char_{uuid.uuid4().hex[:8]}"
                if not char_data.get("first_mes_ru"):
                    cached_ru = SessionManager.get_cached_greeting_ru(char_id, card_obj.name)
                    if cached_ru:
                        char_data["first_mes_ru"] = cached_ru
                        card_obj.first_mes_ru = cached_ru
                char_data["id"] = char_id
                logger.info(
                    f"[Card.Loaded] Создана сессия '{session_id}' | "
                    f"Карточка: '{card_obj.name}' | "
                    f"Файл: '{card_obj.source_path or 'В памяти'}' | "
                    f"Архетип: '{card_obj.archetype_label}'"
                )

        is_card_dm = bool(card_obj and card_obj.is_dm)
        sampling = get_default_sampling_params(mode)
        if is_card_dm:
            sampling["is_dm_mode"] = True

        is_custom_titled = bool(title and title.strip().lower() not in ("exported", "character_exported", "exported.json", "новый диалог", "диалог", "undefined"))
        if not is_custom_titled:
            title = "Новый диалог"

        if str(mode).lower() in ("assistant", ""):
            if custom_system_prompt is None:
                try:
                    from utils.config_manager import ConfigManager
                    cfg = ConfigManager.load_config()
                    custom_system_prompt = cfg.get("assistant_custom_prompt_template", "")
                except Exception:
                    custom_system_prompt = ""

        new_sess = {
            "id": session_id,
            "character_id": char_id,
            "title": title,
            "is_custom_titled": is_custom_titled,
            "mode": mode,
            "character_data": char_data,
            "background_image": background_image,
            "model_id": model_id,
            "is_model_locked": is_model_locked,
            "user_persona_id": user_persona_id,
            "created_at": now_iso,
            "updated_at": now_iso,
            "messages": [],
            "chat_history": [],
            "world_facts": [],
            "sampling_params": sampling,
            "is_custom_sampling": False,
            "is_dm_mode": is_card_dm,
            "custom_system_prompt": custom_system_prompt or ""
        }
        SessionManager.set_active_session(new_sess)
        return new_sess

    @staticmethod
    def generate_smart_title(prompt: str, mode: str = "assistant", character_name: str = "") -> str:
        """Формирует емкий заголовок (до 28 символов) по первому запросу пользователя."""
        clean = (prompt or "").strip()
        low = clean.lower()

        # 1. Запрос изображения / арта
        art_triggers = ("!art", "!арт", "!img", "!картинка", "нарисуй", "сгенерируй", "изобрази", "отрисуй", "покажи", "создай арт", "визуализируй")
        if any(low.startswith(trig) for trig in art_triggers) or "[image_prompt" in low:
            subj = re.sub(r'^(?:!art|!арт|!img|!картинка|нарисуй|сгенерируй|изобрази|отрисуй|покажи|создай арт|визуализируй)\s*(?:мне|нам|пожалуйста)?\s*', '', clean, flags=re.IGNORECASE).strip(' :,-')
            subj = re.sub(r'--\w+.*$', '', subj).strip()
            if subj:
                short_s = (subj[:22].rsplit(' ', 1)[0] + "...") if len(subj) > 22 else subj
                return f"🎨 Арт: {short_s}"
            return "🎨 Генерация арта"

        # 2. Инженерный код / документы
        code_words = ("напиши скрипт", "напиши код", "создай файл", "сделай игру", "почини", "excel", "таблиц", "смет", ".py", ".docx", ".xlsx")
        if mode.startswith("eng") or any(w in low for w in code_words):
            first_l = clean.split('\n')[0].strip()
            first_l = re.sub(r'^(?:напиши|создай|сделай)\s*', '', first_l, flags=re.IGNORECASE).strip()
            short_c = (first_l[:22].rsplit(' ', 1)[0] + "...") if len(first_l) > 22 else first_l
            return f"🛠️ {short_c}" if short_c else "🛠️ Инженерная задача"

        # 3. Ролевой чат
        if mode == "roleplay":
            first_l = clean.split('\n')[0].strip().strip('*')
            short_rp = (first_l[:22].rsplit(' ', 1)[0] + "...") if len(first_l) > 22 else first_l
            if character_name and character_name.lower() not in ("персонаж", "unknown"):
                return f"🎭 {character_name}: {short_rp}" if short_rp else f"🎭 Чат с {character_name}"
            return f"🎭 {short_rp}" if short_rp else "🎭 Ролевой диалог"

        # 4. Обычный диалог ассистента
        first_sentence = re.split(r'[.!?\n]', clean)[0].strip()
        first_sentence = re.sub(r'^(?:привет|здравствуй|добрый день|слушай|подскажи|расскажи|помоги|вопрос|как|что такое)\s*[,:]?\s*', '', first_sentence, flags=re.IGNORECASE).strip()
        if not first_sentence:
            first_sentence = clean[:25]
        short_t = (first_sentence[:25].rsplit(' ', 1)[0] + "...") if len(first_sentence) > 25 else first_sentence
        return f"💬 {short_t}" if short_t else "💬 Новый диалог"

    @staticmethod
    def _get_sqlite_conn() -> sqlite3.Connection:
        import config as _cfg
        curr_hist = Path(HISTORY_DIR).resolve()
        mem_db_p = Path(memory_db.db_path).resolve()
        if curr_hist.parent == mem_db_p.parent.parent:
            db_file = mem_db_p
        else:
            curr_hist.mkdir(parents=True, exist_ok=True)
            db_file = curr_hist / "sessions_wal.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_file), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        SessionManager._ensure_sqlite_schema(conn)
        return conn

    @staticmethod
    def _ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'assistant',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                file_mtime REAL NOT NULL DEFAULT 0.0,
                header_json TEXT NOT NULL,
                chat_history_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                session_id TEXT NOT NULL,
                message_idx INTEGER NOT NULL,
                msg_id TEXT NOT NULL,
                role TEXT NOT NULL,
                sender TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                raw_msg_json TEXT NOT NULL,
                PRIMARY KEY (session_id, message_idx)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_sess ON chat_messages(session_id);")

    @staticmethod
    def _save_session_to_sqlite(session: Dict[str, Any], file_mtime: float = 0.0) -> None:
        try:
            session_id = str(session["id"]).strip()
            header_dict = {k: v for k, v in session.items() if k not in ("messages", "chat_history")}
            chat_history_list = session.get("chat_history") if isinstance(session.get("chat_history"), list) else []
            messages_list = session.get("messages") if isinstance(session.get("messages"), list) else []

            with SessionManager._get_sqlite_conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO chat_sessions
                    (id, title, mode, updated_at, created_at, file_mtime, header_json, chat_history_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        str(session.get("title") or "Диалог"),
                        str(session.get("mode") or "assistant"),
                        str(session.get("updated_at") or ""),
                        str(session.get("created_at") or ""),
                        float(file_mtime),
                        json.dumps(header_dict, ensure_ascii=False),
                        json.dumps(chat_history_list, ensure_ascii=False),
                    ),
                )
                conn.execute(
                    "DELETE FROM chat_messages WHERE session_id = ? AND message_idx >= ?",
                    (session_id, len(messages_list)),
                )
                rows_to_insert = []
                for idx, m in enumerate(messages_list):
                    if not isinstance(m, dict):
                        continue
                    mid = str(m.get("id") or f"msg_{idx}")
                    role = str(m.get("role") or ("user" if m.get("is_user") else "assistant"))
                    sender = str(m.get("sender") or role)
                    txt = str(m.get("text") if m.get("text") is not None else m.get("content", ""))
                    ts = str(m.get("timestamp") or "")
                    rows_to_insert.append(
                        (
                            session_id,
                            idx,
                            mid,
                            role,
                            sender,
                            txt,
                            ts,
                            json.dumps(m, ensure_ascii=False),
                        )
                    )
                if rows_to_insert:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO chat_messages
                        (session_id, message_idx, msg_id, role, sender, text, timestamp, raw_msg_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows_to_insert,
                    )
        except Exception as e:
            logger.debug(f"[SessionManager] SQLite write warning for {session.get('id')}: {e}")

    @staticmethod
    def _load_session_from_sqlite(session_id: str, disk_mtime: Optional[float] = None) -> Optional[Dict[str, Any]]:
        try:
            with SessionManager._get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
                row = cur.fetchone()
                if not row:
                    return None
                if disk_mtime is not None and abs(float(disk_mtime) - float(row["file_mtime"] or 0.0)) > 1.0:
                    return None
                data = json.loads(row["header_json"])
                data["chat_history"] = json.loads(row["chat_history_json"]) if row["chat_history_json"] else []
                cur.execute(
                    "SELECT raw_msg_json FROM chat_messages WHERE session_id = ? ORDER BY message_idx ASC",
                    (session_id,),
                )
                data["messages"] = [json.loads(r["raw_msg_json"]) for r in cur.fetchall()]
                return data
        except Exception as e:
            logger.debug(f"[SessionManager] SQLite read fallback for {session_id}: {e}")
            return None

    @staticmethod
    def _delete_session_from_sqlite(session_id: str) -> None:
        try:
            with SessionManager._get_sqlite_conn() as conn:
                conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        except Exception as e:
            logger.debug(f"[SessionManager] SQLite delete warning for {session_id}: {e}")

    @staticmethod
    def save_session(session: Union[Dict[str, Any], str], trigger_memory: bool = False) -> Optional[Path]:
        """Сохраняет состояние сессии в SQLite WAL + быстрое файловое зеркало без блокировок fsync."""
        if isinstance(session, str):
            sess_id = session.strip()
            session_dict = SessionManager._sessions_cache.get(sess_id)
            if not session_dict and SessionManager._current_session and str(SessionManager._current_session.get("id")) == sess_id:
                session_dict = SessionManager._current_session
            if not session_dict:
                session_dict = SessionManager.load_session(sess_id)
            if not session_dict:
                logger.warning(f"[SessionManager] Не удалось найти данные сессии {sess_id} для сохранения.")
                return None
            session = session_dict

        if not session or "id" not in session:
            return None

        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        session["updated_at"] = datetime.now().isoformat()
        if "chat_history" in session and isinstance(session["chat_history"], list):
            for m in session["chat_history"]:
                if isinstance(m, dict):
                    m["content"] = m.get("content") if m.get("content") is not None else m.get("text", "")

        if "messages" in session and isinstance(session["messages"], list):
            for m in session["messages"]:
                if isinstance(m, dict) and not m.get("id"):
                    m["id"] = str(uuid.uuid4())

        session_id = str(session["id"]).strip()
        SessionManager._sessions_cache[session_id] = session

        file_path = HISTORY_DIR / f"chat_{session_id}.json"
        temp_path = HISTORY_DIR / f"chat_{session_id}_{uuid.uuid4().hex[:6]}.tmp"

        session_dir = SessionManager.get_session_dir(session_id)
        session_json_path = session_dir / "session.json"
        temp_session_path = session_dir / f"session_{uuid.uuid4().hex[:6]}.tmp"

        try:
            # 1. Быстрая сериализация зеркала в буфер ОС без аппаратного простоя fsync
            serialized = json.dumps(session, ensure_ascii=False, indent=2)
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(serialized)

            if not temp_path.exists() or temp_path.stat().st_size == 0:
                raise IOError(f"Временный файл сессии {temp_path} пуст или не был создан.")

            replaced = False
            for _ in range(3):
                try:
                    os.replace(temp_path, file_path)
                    replaced = True
                    break
                except PermissionError:
                    time.sleep(0.03)

            if not replaced:
                os.replace(temp_path, file_path)

            # 2. Первичная транзакционная фиксация в SQLite WAL (0.1 мс)
            disk_mtime = file_path.stat().st_mtime if file_path.exists() else time.time()
            SessionManager._save_session_to_sqlite(session, file_mtime=disk_mtime)

            # 3. Дублирование состояния в outputs/sessions/<id>/session.json для HUD-источников
            try:
                with open(temp_session_path, "w", encoding="utf-8") as sf:
                    sf.write(serialized)
                if temp_session_path.exists() and temp_session_path.stat().st_size > 0:
                    for _ in range(3):
                        try:
                            os.replace(temp_session_path, session_json_path)
                            break
                        except PermissionError:
                            time.sleep(0.03)
            except Exception as se:
                logger.debug(f"[SessionManager] Ошибка дублирования session.json: {se}")

            return file_path
        except Exception as e:
            logger.error(f"[SessionManager] Ошибка сохранения сессии {session_id}: {e}", exc_info=True)
            return None
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            if temp_session_path.exists():
                try:
                    temp_session_path.unlink()
                except Exception:
                    pass

    @staticmethod
    def load_session(session_id: str) -> Optional[Dict[str, Any]]:
        file_path = HISTORY_DIR / f"chat_{session_id}.json"
        if not file_path.exists():
            session_json_path = SessionManager.get_session_dir(session_id) / "session.json"
            if session_json_path.exists():
                file_path = session_json_path
            else:
                SessionManager._delete_session_from_sqlite(session_id)
                return None

        try:
            disk_mtime = file_path.stat().st_mtime
            data = SessionManager._load_session_from_sqlite(session_id, disk_mtime=disk_mtime)
            if data is None:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "id" in data:
                    SessionManager._save_session_to_sqlite(data, file_mtime=disk_mtime)
            if data and isinstance(data, dict):
                if "chat_history" in data and isinstance(data["chat_history"], list):
                    for m in data["chat_history"]:
                        if isinstance(m, dict):
                            m["content"] = m.get("content") if m.get("content") is not None else m.get("text", "")

                if "messages" in data and isinstance(data["messages"], list):
                    for m in data["messages"]:
                        if isinstance(m, dict) and not m.get("id"):
                            m["id"] = str(uuid.uuid4())

                if "world_facts" not in data or not isinstance(data.get("world_facts"), list):
                    data["world_facts"] = []

                if "background_image" not in data:
                    data["background_image"] = None

                char_data = data.get("character_data")
                if char_data and isinstance(char_data, dict):
                    # Санация ложных _ru полей в сохраненных данных сессии
                    for field_base in ("description", "personality", "scenario", "mes_example", "first_mes"):
                        ru_val = char_data.get(f"{field_base}_ru", "") or ""
                        orig_val = char_data.get(field_base, "") or ""
                        if ru_val and (ru_val.strip() == orig_val.strip() or not re.search(r'[а-яА-ЯёЁ]{3,}', ru_val)):
                            char_data[f"{field_base}_ru"] = ""

                    # Если в сессии отсутствуют переводы (_ru), подтягиваем их из файла карточки на диске
                    needs_ru = not (
                        char_data.get("description_ru")
                        and char_data.get("personality_ru")
                        and char_data.get("scenario_ru")
                        and char_data.get("first_mes_ru")
                    )
                    if needs_ru:
                        try:
                            card_path = None
                            char_name = char_data.get("name")
                            av_path_str = char_data.get("avatar_path")
                            candidate_files = []
                            if av_path_str:
                                av_p = Path(av_path_str)
                                candidate_files.append(av_p.with_suffix(".json"))
                                candidate_files.append(CHARACTERS_DIR / f"{av_p.stem.replace('_avatar', '')}.json")
                                candidate_files.append(CHARACTERS_DIR / f"{av_p.stem}.json")
                            if char_name:
                                candidate_files.append(CHARACTERS_DIR / f"{char_name}.json")
                                safe_name = "".join(c for c in char_name if c.isalnum() or c in (' ', '_', '-')).strip()
                                if safe_name:
                                    candidate_files.append(CHARACTERS_DIR / f"{safe_name}.json")

                            for cp in candidate_files:
                                if cp.exists() and cp.is_file():
                                    card_path = cp
                                    break

                            if not card_path and CHARACTERS_DIR.exists() and (char_name or char_data.get("id")):
                                target_id = char_data.get("id")
                                for f_cand in CHARACTERS_DIR.glob("*.json"):
                                    if char_name and f_cand.stem.lower() == char_name.lower():
                                        card_path = f_cand
                                        break
                                    try:
                                        with open(f_cand, "r", encoding="utf-8") as jf:
                                            cd_raw = json.load(jf)
                                        target = cd_raw.get("data", cd_raw) if ("spec" in cd_raw or "data" in cd_raw) else cd_raw
                                        if (target_id and target.get("id") == target_id) or (char_name and target.get("name", "").strip().lower() == char_name.strip().lower()):
                                            card_path = f_cand
                                            break
                                    except Exception:
                                        continue

                            if card_path:
                                parsed_card = TavernParser.parse_file(card_path)
                                for field in ("description_ru", "personality_ru", "scenario_ru", "mes_example_ru", "first_mes_ru"):
                                    if not char_data.get(field) and getattr(parsed_card, field, None):
                                        char_data[field] = getattr(parsed_card, field)
                                if not char_data.get("alternate_greetings_ru") and getattr(parsed_card, "alternate_greetings_ru", None):
                                    char_data["alternate_greetings_ru"] = list(parsed_card.alternate_greetings_ru)
                                if not char_data.get("visual_dna") and getattr(parsed_card, "visual_dna", None):
                                    char_data["visual_dna"] = parsed_card.visual_dna
                        except Exception as e:
                            logger.debug(f"Failed to pull card translations from disk: {e}")

                    if not char_data.get("first_mes_ru"):
                        cached_ru = SessionManager.get_cached_greeting_ru(char_data.get("id"), char_data.get("name"))
                        if cached_ru:
                            char_data["first_mes_ru"] = cached_ru

                mode = data.get("mode", "assistant")
                if "sampling_params" not in data or not isinstance(data.get("sampling_params"), dict):
                    data["sampling_params"] = get_default_sampling_params(mode)

                session = data
                if session and session.get("is_roleplay") and session.get("character"):
                    card_dict = session.get("character")
                    card_obj = CharacterCard.from_dict(card_dict) if isinstance(card_dict, dict) else card_dict
                    if card_obj:
                        logger.info(
                            f"[Card.Loaded] Загружена сессия '{session_id}' | "
                            f"Карточка: '{card_obj.name}' | "
                            f"Файл: '{card_obj.source_path or 'В памяти'}' | "
                            f"Архетип: '{card_obj.archetype_label}'"
                        )
                elif session and (session.get("mode") == "roleplay" or session.get("is_roleplay")) and (session.get("character_data") or session.get("character")):
                    card_dict = session.get("character_data") or session.get("character")
                    card_obj = CharacterCard.from_dict(card_dict) if isinstance(card_dict, dict) else card_dict
                    if card_obj:
                        logger.info(
                            f"[Card.Loaded] Загружена сессия '{session_id}' | "
                            f"Карточка: '{card_obj.name}' | "
                            f"Файл: '{card_obj.source_path or 'В памяти'}' | "
                            f"Архетип: '{card_obj.archetype_label}'"
                        )
            if data and isinstance(data, dict):
                SessionManager.set_active_session(data)
            return data
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None

    @staticmethod
    def list_sessions() -> List[Dict[str, Any]]:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        sessions = []

        # Автоочистка сиротских cast_*.json (когда есть cast_<id>.json, но нет chat_<id>.json)
        try:
            from services.rp_cast_manager import RPCastManager
            from services.memory_db import memory_db
            for cast_p in HISTORY_DIR.glob("cast_*.json"):
                cid = cast_p.stem.replace("cast_", "")
                if not (HISTORY_DIR / f"chat_{cid}.json").exists():
                    RPCastManager.cleanup_session(cid)
                    memory_db.clear_rp_session_memory(cid)
                    cast_p.unlink(missing_ok=True)
                    logger.info(f"[SessionManager] Удален сиротский файл каста: {cast_p.name}")
        except Exception as e:
            logger.debug(f"[SessionManager] Ошибка автоочистки сиротских cast_*.json: {e}")

        sqlite_headers = {}
        try:
            with SessionManager._get_sqlite_conn() as conn:
                for r in conn.execute("SELECT id, file_mtime, header_json FROM chat_sessions").fetchall():
                    sqlite_headers[r["id"]] = (float(r["file_mtime"] or 0.0), r["header_json"])
        except Exception:
            pass

        for p in HISTORY_DIR.glob("chat_*.json"):
            try:
                sid_guess = p.stem.replace("chat_", "")
                p_mtime = p.stat().st_mtime
                cached_entry = sqlite_headers.get(sid_guess)
                if cached_entry and abs(p_mtime - cached_entry[0]) <= 1.0:
                    data = json.loads(cached_entry[1])
                else:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "id" in data:
                        SessionManager._save_session_to_sqlite(data, file_mtime=p_mtime)

                session_id = data.get("id", p.stem.replace("chat_", ""))
                if session_id.startswith("test_") or "test_sess" in p.name:
                    continue

                raw_title = data.get("title", "")
                char_data = data.get("character_data")
                char_name = char_data.get("name") if char_data else None

                if not raw_title or raw_title.strip().lower() in ("exported", "exported.json", "undefined"):
                    if char_name and char_name.lower() not in ("exported", "character", "unknown persona"):
                        clean_title = f"{char_name} — Сессия"
                    else:
                        clean_title = "Диалог"
                else:
                    clean_title = raw_title

                sessions.append({
                    "id": session_id,
                    "character_id": data.get("character_id") or (char_data.get("id") if char_data else None),
                    "title": clean_title,
                    "mode": data.get("mode", "assistant"),
                    "type": data.get("type"),
                    "character_data": char_data,
                    "assistant_avatar_path": data.get("assistant_avatar_path"),
                    "model_id": data.get("model_id", ""),
                    "is_model_locked": data.get("is_model_locked", False),
                    "user_persona_id": data.get("user_persona_id"),
                    "background_image": data.get("background_image"),
                    "updated_at": data.get("updated_at", ""),
                    "created_at": data.get("created_at", "")
                })
            except Exception as e:
                logger.debug(f"Skipping unreadable session file {p}: {e}")

        sessions.sort(key=lambda s: s.get("updated_at") or s.get("created_at") or "", reverse=True)
        return sessions

    @staticmethod
    def delete_session(session_id: str) -> bool:
        import shutil
        import stat
        import gc
        from config import WORKSPACE_DIR, OUTPUTS_DIR, AUDIO_DIR
        from services.sandbox_manager import SandboxManager

        sid = str(session_id).strip() if session_id else ""
        if not sid or sid.lower() in ("none", "common", "undefined") or "/" in sid or "\\" in sid or ".." in sid:
            return False

        if SandboxManager.get_active_session_id() == sid:
            SandboxManager.set_active_session_id(None)

        gc.collect()

        def _force_remove_readonly(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                time.sleep(0.05)
                try:
                    func(path)
                except Exception as err:
                    logger.debug(f"[Cleanup] Не удалось удалить заблокированный файл {path}: {err}")

        # Считываем привязанные медиафайлы из chat_{sid}.json перед его удалением
        chat_file = HISTORY_DIR / f"chat_{sid}.json"
        deleted_image_paths = set()
        if chat_file.exists():
            try:
                with open(chat_file, "r", encoding="utf-8") as f:
                    chat_data = json.load(f)
                msgs = chat_data.get("messages", [])
                outputs_res = OUTPUTS_DIR.resolve()
                audio_res = AUDIO_DIR.resolve()
                for m in msgs:
                    img_p = m.get("image_path")
                    if img_p:
                        try:
                            p = Path(img_p).resolve()
                            deleted_image_paths.add(str(img_p))
                            deleted_image_paths.add(str(p))
                            if p.exists() and p.is_file() and (outputs_res in p.parents or audio_res in p.parents):
                                p.unlink(missing_ok=True)
                        except Exception:
                            pass
                    aud_p = m.get("audio_path")
                    if aud_p:
                        try:
                            p = Path(aud_p).resolve()
                            if p.exists() and p.is_file() and (outputs_res in p.parents or audio_res in p.parents):
                                p.unlink(missing_ok=True)
                        except Exception:
                            pass
                    fpaths = m.get("file_paths") or []
                    if isinstance(fpaths, list):
                        for fp in fpaths:
                            try:
                                p = Path(fp).resolve()
                                if p.exists() and p.is_file() and (outputs_res in p.parents or audio_res in p.parents):
                                    p.unlink(missing_ok=True)
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"[Cleanup] Ошибка считывания файлов из chat_{sid}.json: {e}")

        if deleted_image_paths:
            try:
                from ui.components.chat_bubble import _IMAGE_PREVIEW_CACHE, _IMAGE_CACHE_LOCK
                with _IMAGE_CACHE_LOCK:
                    keys_to_del = [k for k in _IMAGE_PREVIEW_CACHE if k[0] in deleted_image_paths]
                    for k in keys_to_del:
                        _IMAGE_PREVIEW_CACHE.pop(k, None)
            except Exception:
                pass

        success = False

        for pattern in (f"chat_{sid}.json", f"chat_{sid}*.tmp", f"chat_{sid}.bak", f"cast_{sid}*.json", f"cast_{sid}*.tmp"):
            for p in HISTORY_DIR.glob(pattern):
                try:
                    p.unlink(missing_ok=True)
                    success = True
                except Exception as e:
                    logger.warning(f"[Cleanup] Ошибка удаления файла истории {p.name}: {e}")

        # Очистка кэша превью аватаров конкретной сессии
        from config import DATA_DIR
        thumbs_dir = DATA_DIR / "cache" / "gallery_thumbs"
        if thumbs_dir.exists():
            for thumb_file in thumbs_dir.glob(f"*{sid}*"):
                try:
                    thumb_file.unlink(missing_ok=True)
                except Exception:
                    pass

        RPCastManager.cleanup_session(sid)

        session_dir = (OUTPUTS_DIR / "sessions" / sid).resolve()
        if session_dir.exists() and session_dir.is_dir() and session_dir.parent == (OUTPUTS_DIR / "sessions").resolve():
            try:
                shutil.rmtree(session_dir, onerror=_force_remove_readonly)
                success = True
            except Exception as e:
                logger.warning(f"[Cleanup] Ошибка удаления папки медиа {session_dir}: {e}")

        project_dir = (WORKSPACE_DIR / "projects" / sid).resolve()
        if project_dir.exists() and project_dir.is_dir() and project_dir.parent == (WORKSPACE_DIR / "projects").resolve():
            try:
                shutil.rmtree(project_dir, onerror=_force_remove_readonly)
                success = True
                logger.info(f"[Cleanup] Удалена рабочая папка проекта инженера: {project_dir}")
            except Exception as e:
                logger.warning(f"[Cleanup] Ошибка удаления рабочей папки проекта {project_dir}: {e}")

        sess_avatars_dir = CHARACTERS_DIR / "session_avatars"
        if sess_avatars_dir.exists():
            for av_file in sess_avatars_dir.glob(f"sess_{sid}_*"):
                try:
                    av_file.unlink(missing_ok=True)
                except Exception:
                    pass

        try:
            SessionManager._sessions_cache.pop(sid, None)
            SessionManager._delete_session_from_sqlite(sid)
            from services.memory_db import memory_db
            memory_db.clear_rp_session_memory(sid)
        except Exception as e:
            logger.debug(f"[Cleanup] Ошибка очистки памяти сессии {sid}: {e}")

        return success

    @staticmethod
    def rename_session(session_id: str, new_title: str) -> bool:
        sess = SessionManager.load_session(session_id)
        if not sess:
            return False
        sess["title"] = new_title
        SessionManager.save_session(sess)
        return True

    @staticmethod
    def set_session_user_persona(session_id: str, persona_id: Optional[str]) -> bool:
        sess = SessionManager.load_session(session_id)
        if not sess:
            return False
        sess["user_persona_id"] = persona_id
        SessionManager.save_session(sess)
        return True

    @staticmethod
    def set_session_background(session_id: str, background_image: Optional[str]) -> bool:
        sess = SessionManager.load_session(session_id)
        if not sess:
            return False
        sess["background_image"] = background_image
        SessionManager.save_session(sess)
        return True

    @staticmethod
    def append_image_to_session_file(session_id: str, image_path: str, text: str = "🎨 Иллюстрация сцены готова:") -> bool:
        """
        Safely appends or updates the last assistant message with an image
        in a background session file on disk when the session is not currently active.
        """
        sess = SessionManager.load_session(session_id)
        if not sess:
            return False
        if "messages" not in sess:
            sess["messages"] = []
        updated = False
        for m in reversed(sess["messages"]):
            if not m.get("is_user") and m.get("role") in ("assistant", None):
                m["image_path"] = image_path
                if not (m.get("text") or "").strip():
                    m["text"] = text
                updated = True
                break
        if not updated:
            sess["messages"].append({
                "role": "assistant",
                "text": text,
                "image_path": image_path,
                "timestamp": datetime.now().isoformat()
            })
        return SessionManager.save_session(sess) is not None

    @staticmethod
    def get_session_dir(session_id: Optional[str] = None) -> Path:
        sid = str(session_id).strip() if session_id else "common"
        path = OUTPUTS_DIR / "sessions" / sid
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def get_session_images_dir(session_id: Optional[str] = None) -> Path:
        path = SessionManager.get_session_dir(session_id) / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run_ephemeral_engineer_worker(
        self,
        task_prompt: str,
        context_hint: str = "",
        is_analysis: bool = False,
        image_path: Optional[str] = None,
        model_id: Optional[str] = None,
        session_history: Optional[List[Dict[str, str]]] = None,
        on_message_callback: Optional[Any] = None,
        on_status_callback: Optional[Any] = None,
        **kwargs
    ) -> dict:
        """Делегирует исполнение в изолированный SubagentEngineer."""
        from services.subagent_engineer import SubagentEngineer
        client = getattr(self, "lm_client", None) or getattr(self, "llm_client", None) if self is not None else None
        return SubagentEngineer.run_worker(
            task_prompt=task_prompt,
            context_hint=context_hint,
            is_analysis=is_analysis,
            image_path=image_path,
            lm_client=client,
            model_id=model_id,
            session_history=session_history,
            on_message_callback=on_message_callback,
            on_status_callback=on_status_callback,
            **kwargs
        )

    # --- ROLEPLAY CAST FORWARDERS ---
    @staticmethod
    def load_cast(session_id: Optional[str], user_name: str = "Игрок", companion_name: str = "Спутница") -> Dict[str, Any]:
        return RPCastManager.load_cast(session_id, user_name=user_name, companion_name=companion_name)

    @staticmethod
    def save_cast(session_id: Optional[str], data: Dict[str, Any]) -> bool:
        return RPCastManager.save_cast(session_id, data)

__all__ = [
    "USER_PROFILES_FILE",
    "DEFAULT_USER_PROFILES",
    "UserProfileManager",
    "SessionManager",
    "LORE_TRIGGER_PATTERNS",
    "RECALL_TRIGGERS",
    "STOP_WORDS_RECALL",
    "ContextWindowBuilder",
    "get_mode_profile",
    "get_default_sampling_params",
]
