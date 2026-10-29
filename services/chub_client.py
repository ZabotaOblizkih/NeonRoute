"""
Chub.ai REST API Client for NeonRoute.
Handles character search, metadata parsing, token counting, and Tavern V2 card downloading.
"""

import base64
import io
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image
import requests

from config import CHARACTERS_DIR

logger = logging.getLogger("NeonRoute.ChubClient")

CHUB_API_BASE = "https://api.chub.ai"


def sanitize_card_description(raw_text: str) -> Tuple[str, List[str]]:
    """
    Очищает описание персонажа от промо-ссылок, списков других ботов и метаданных автора.
    Возвращает (чистый_текст_для_LLM, список_извлеченных_ссылок_для_creator_notes).
    """
    if not raw_text:
        return "", []

    url_pat = re.compile(
        r'(https?://[^\s<]+|www\.[^\s<]+|t\.me/|discord\.(?:gg|com)/|patreon\.com/|ko-fi\.com/|boosty\.to/|civitai\.com/|chub\.ai/)',
        re.IGNORECASE
    )
    header_pat = re.compile(
        r'^(more\s+.*cards?|other\s+.*cards?|check\s+out\s+my|my\s+other\s+bots?|'
        r'другие\s+карточки|мои\s+другие\s+боты|другие\s+персонажи|links?:|ссылки:|'
        r'support\s+me|поддержать|credits?:|благодарности:|changelog:)',
        re.IGNORECASE
    )
    meta_pat = re.compile(
        r'^(card\s+made\s+with|bot\s+made\s+with|created\s+by|made\s+by|original\s+bot\s+by|'
        r'prompt\s+by|создано|автор\s+карточки|leave\s+a\s+(?:review|like|star)|'
        r'version\s*\d|v\d+\s*[-:])',
        re.IGNORECASE
    )

    cleaned_lines = []
    extracted_notes = []
    in_junk_block = False

    for line in raw_text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        # 1. Заголовки блоков ссылок ("More Nier Automata cards:")
        if header_pat.match(trimmed):
            in_junk_block = True
            extracted_notes.append(trimmed)
            continue

        # 2. Строки со ссылками
        if url_pat.search(trimmed):
            in_junk_block = True
            extracted_notes.append(trimmed)
            continue

        # 3. Мета-комментарии создателя
        if meta_pat.match(trimmed):
            extracted_notes.append(trimmed)
            continue

        # 4. Элементы списков в блоке промо (например "9S - ")
        if in_junk_block and re.match(r'^[\w\s\-_]+[:-]\s*$', trimmed):
            continue

        # Сброс флага промо при переходе к связному повествовательному тексту
        if len(trimmed) > 30 and trimmed[-1] in ('.', '!', '?', '"'):
            in_junk_block = False

        if in_junk_block and trimmed.startswith(('-', '*', '•')):
            extracted_notes.append(trimmed)
            continue

        in_junk_block = False
        cleaned_lines.append(line)

    while cleaned_lines and cleaned_lines[-1].strip() == "":
        cleaned_lines.pop()
    while cleaned_lines and cleaned_lines[0].strip() == "":
        cleaned_lines.pop(0)

    return "\n".join(cleaned_lines), extracted_notes


class ChubClient:
    def __init__(self, timeout: float = 14.0):
        self.timeout = timeout
        self.session = requests.Session()
        # Стандартные браузерные заголовки для корректного прохождения WAF
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://chub.ai/",
            "Origin": "https://chub.ai",
            "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site"
        })

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        cyr = len(re.findall(r'[\u0400-\u04FF]', text))
        return int((cyr / 2.6) + ((len(text) - cyr) / 4.0))

    def search_characters(
        self,
        query: str = "",
        page: int = 1,
        sort: str = "download_count",
        nsfw: bool = True,
        max_tokens_limit: Optional[int] = None,
        russian_only: bool = False
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Ищет персонажей через официальный REST API Chub.
        """
        url = f"{CHUB_API_BASE}/api/characters/search"
        params: Dict[str, Any] = {
            "page": max(1, page),
            "sort": sort,
            "sort_asc": "false",
            "nsfw": "true" if nsfw else "false",
            "include_forks": "false"
        }
        if russian_only:
            params["tags"] = "Russian Language"

        # Передаем параметр search ТОЛЬКО если введен непустой поисковый запрос
        q_clean = query.strip()
        if q_clean:
            params["search"] = q_clean

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 200:
                alt_resp = self.session.get(f"{CHUB_API_BASE}/search", params=params, timeout=self.timeout)
                if alt_resp.status_code == 200:
                    resp = alt_resp
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            logger.warning(f"[ChubClient] Ошибка сети: {e}")
            raise ConnectionError("Отсутствует подключение к интернету или сервер Chub.ai недоступен.")
        except Exception as e:
            logger.error(f"[ChubClient] Сбой сети: {e}")
            raise RuntimeError(f"Ошибка сети: {e}")

        # Проверка ответа перед парсингом JSON
        if resp.status_code != 200:
            err_peek = resp.text.strip()[:180]
            logger.error(f"[ChubClient] API вернул HTTP {resp.status_code}: {err_peek}")
            raise RuntimeError(f"Chub.ai вернул код ошибки {resp.status_code}")

        text_content = resp.text.strip()
        content_type = resp.headers.get("Content-Type", "").lower()
        if text_content.startswith("<") or "text/html" in content_type:
            logger.error(f"[ChubClient] Получен HTML вместо JSON (WAF Challenge): {text_content[:150]}")
            raise RuntimeError("Сервер Chub.ai заблокировал запрос защитой Cloudflare.")

        try:
            data = resp.json()
        except Exception as e:
            logger.error(f"[ChubClient] Ошибка парсинга JSON: {e}")
            raise RuntimeError(f"Некорректный формат ответа от сервера: {e}")

        raw_nodes = (
            data.get("nodes")
            or ((data.get("data") or {}).get("nodes") if isinstance(data.get("data"), dict) else None)
            or data.get("characters")
            or (data if isinstance(data, list) else [])
        )
        total_count = (
            data.get("total")
            or ((data.get("data") or {}).get("count") if isinstance(data.get("data"), dict) else None)
            or len(raw_nodes)
        )

        cards = []
        for n in raw_nodes:
            if not isinstance(n, dict):
                continue

            full_path = n.get("fullPath") or n.get("id")
            if not full_path:
                continue

            name = (n.get("name") or "Без имени").strip()
            desc = n.get("description") or n.get("tagline") or ""
            tagline = n.get("tagline") or ""
            star_count = n.get("starCount") or n.get("rating") or 0
            downloads = n.get("downloadCount") or 0
            tokens = n.get("tokens") or n.get("token_count") or self.estimate_tokens(desc)

            if max_tokens_limit and tokens > max_tokens_limit:
                continue

            # Лингвистический анализ: проверяем наличие реального русского текста
            content_sample = f"{desc} {tagline}"
            cyrillic_chars = len(re.findall(r'[\u0400-\u04FF]', content_sample))
            is_russian = cyrillic_chars >= 15 or (cyrillic_chars >= 6 and len(content_sample.strip()) < 60)

            # При включенном фильтре "На русском" отсекаем англоязычные карточки с русскими именами
            if russian_only and not is_russian:
                continue

            avatar_url = n.get("avatar_url") or n.get("max_avatar_url") or f"https://avatars.charhub.io/avatars/{full_path}/avatar.webp"

            cards.append({
                "id": str(n.get("id", "")),
                "full_path": str(full_path),
                "name": name,
                "tagline": tagline.strip() or desc[:120].strip(),
                "description": desc,
                "star_count": int(star_count),
                "download_count": int(downloads),
                "tokens": int(tokens),
                "lang": "ru" if is_russian else "en",
                "avatar_url": avatar_url,
                "topics": n.get("topics") or []
            })

        return cards, total_count

    def download_card(self, full_path: str, card_title: str) -> Optional[Path]:
        """Скачивает карточку и аватар с Chub.ai с корректным маппингом полей Tavern V2."""
        CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c for c in card_title if c.isalnum() or c in (' ', '_', '-')).strip() or f"char_{uuid.uuid4().hex[:6]}"

        dest_json = CHARACTERS_DIR / f"{safe_name}.json"
        if dest_json.exists():
            dest_json = CHARACTERS_DIR / f"{safe_name}_{uuid.uuid4().hex[:4]}.json"

        card_data = None

        # 1. Запрос к Chub API с обязательным параметром ?full=true
        detail_url = f"{CHUB_API_BASE}/api/characters/{full_path}?full=true"
        try:
            r = self.session.get(detail_url, timeout=15.0)
            if r.status_code == 200 and not r.text.strip().startswith("<"):
                meta = r.json()
                node = (meta.get("node") or meta.get("character") or meta) if isinstance(meta, dict) else {}
                defn = (node or {}).get("definition") or {}

                # Проверяем, отданы ли реальные внутренности карточки
                if defn and (defn.get("personality") or defn.get("first_message") or defn.get("description")):
                    topics = node.get("topics") or []
                    
                    defn_desc = str(defn.get("description") or "").strip()
                    defn_pers = str(defn.get("personality") or "").strip()
                    defn_tav_pers = str(defn.get("tavern_personality") or "").strip()

                    # Не подтягиваем веб-страницу node.get("description"), если у персонажа задан defn_pers!
                    if defn_desc:
                        raw_desc = defn_desc
                    elif defn_pers:
                        raw_desc = defn_pers
                    else:
                        raw_desc = str(node.get("description") or "").strip()

                    # Очищаем описание и характер от ссылок и промо-мусора
                    clean_desc, extra_notes = sanitize_card_description(raw_desc)
                    pers_parts = [p for p in (defn_pers, defn_tav_pers) if p]
                    raw_pers = "\n\n".join(dict.fromkeys(pers_parts))
                    clean_pers, pers_notes = sanitize_card_description(raw_pers)

                    # Все ссылки и примечания аккуратно убираем в creator_notes
                    creator_notes_list = []
                    raw_tagline = str(node.get("tagline") or "").strip()
                    if raw_tagline:
                        creator_notes_list.append(raw_tagline)
                    existing_cn = str(defn.get("creator_notes") or node.get("creator_notes") or "").strip()
                    if existing_cn:
                        creator_notes_list.append(existing_cn)
                    creator_notes_list.extend(extra_notes)
                    creator_notes_list.extend(pers_notes)

                    creator_notes = "\n\n".join(dict.fromkeys([n for n in creator_notes_list if n]))

                    depth_prompt = (
                        defn.get("depth_prompt")
                        or node.get("depth_prompt")
                        or defn.get("character_note")
                        or node.get("character_note")
                        or ""
                    )
                    extensions = defn.get("extensions") or node.get("extensions") or {}
                    if not isinstance(extensions, dict):
                        extensions = {}
                    char_book = defn.get("character_book") or node.get("character_book")

                    card_data = {
                        "spec": "chara_card_v2",
                        "spec_version": "2.0",
                        "data": {
                            "name": node.get("name", card_title),
                            "description": clean_desc,
                            "personality": clean_pers,
                            "first_mes": defn.get("first_message", ""),
                            "mes_example": defn.get("example_dialogs", ""),
                            "scenario": defn.get("scenario", ""),
                            "creator_notes": creator_notes,
                            "system_prompt": defn.get("system_prompt", ""),
                            "post_history_instructions": defn.get("post_history_instructions", ""),
                            "depth_prompt": depth_prompt,
                            "character_book": char_book,
                            "extensions": extensions,
                            "alternate_greetings": defn.get("alternate_greetings", []) or [],
                            "tags": topics,
                            "creator": node.get("fullPath", "").split("/")[0] if "/" in node.get("fullPath", "") else ""
                        }
                    }
        except Exception as e:
            logger.debug(f"[ChubClient] Сбой запроса к full=true API: {e}")

        # 2. Гарантированный источник: оригинальный PNG файл из CDN (если API закрыт WAF или поля пусты)
        if not card_data:
            png_urls = [
                f"https://avatars.charhub.io/avatars/{full_path}/chara_card_v2.png",
                f"https://avatars.charhub.io/avatars/{full_path}/avatar.png"
            ]
            for png_url in png_urls:
                try:
                    r_png = self.session.get(png_url, timeout=15.0)
                    if r_png.status_code == 200 and len(r_png.content) > 1000:
                        img_card = Image.open(io.BytesIO(r_png.content))
                        meta_info = getattr(img_card, "text", {}) or {}
                        raw_b64 = meta_info.get("chara") or meta_info.get("ccv3")
                        if not raw_b64 and hasattr(img_card, "info"):
                            raw_b64 = img_card.info.get("chara") or img_card.info.get("ccv3")

                        if raw_b64:
                            if isinstance(raw_b64, bytes):
                                raw_b64 = raw_b64.decode("utf-8", errors="ignore")
                            try:
                                decoded = json.loads(base64.b64decode(raw_b64).decode("utf-8"))
                                card_data = decoded
                            except Exception:
                                card_data = json.loads(raw_b64)
                            if card_data:
                                is_v2 = card_data.get("spec") == "chara_card_v2" or "data" in card_data
                                target_png = card_data.setdefault("data", {}) if is_v2 else card_data

                                raw_png_desc = str(target_png.get("description") or "").strip()
                                raw_png_pers = str(target_png.get("personality") or "").strip()

                                clean_png_desc, desc_notes = sanitize_card_description(raw_png_desc)
                                clean_png_pers, pers_notes = sanitize_card_description(raw_png_pers)

                                target_png["description"] = clean_png_desc
                                target_png["personality"] = clean_png_pers

                                png_extra = desc_notes + pers_notes
                                if png_extra:
                                    existing_notes = str(target_png.get("creator_notes", "") or "").strip()
                                    merged = [existing_notes] if existing_notes else []
                                    merged.extend(png_extra)
                                    target_png["creator_notes"] = "\n\n".join(dict.fromkeys([n for n in merged if n]))

                                logger.info(f"[ChubClient] Полные данные карточки распакованы из PNG: {png_url}")
                                break
                except Exception as e_png:
                    logger.debug(f"[ChubClient] Ошибка извлечения из PNG ({png_url}): {e_png}")

        if not card_data:
            logger.error(f"[ChubClient] Не удалось загрузить карточку для {full_path}")
            return None

        # 3. Скачивание аватара
        dest_avatar = dest_json.with_name(f"{dest_json.stem}_avatar.png")
        avatar_urls = [
            f"https://avatars.charhub.io/avatars/{full_path}/avatar.webp",
            f"https://avatars.charhub.io/avatars/{full_path}/avatar.png"
        ]
        for av_url in avatar_urls:
            try:
                r_img = self.session.get(av_url, timeout=12.0)
                if r_img.status_code == 200 and len(r_img.content) > 500:
                    img = Image.open(io.BytesIO(r_img.content))
                    img.save(dest_avatar, format="PNG")
                    break
            except Exception as e_img:
                logger.debug(f"[ChubClient] Ошибка сохранения аватара: {e_img}")

        # 4. Привязка аватара и запись файла
        is_v2 = card_data.get("spec") == "chara_card_v2" or "data" in card_data
        target = card_data.setdefault("data", {}) if is_v2 else card_data
        if dest_avatar.exists():
            target["avatar_path"] = str(dest_avatar.resolve())

        try:
            with open(dest_json, "w", encoding="utf-8") as f:
                json.dump(card_data, f, ensure_ascii=False, indent=2)
            logger.info(f"[ChubClient] Карточка сохранена: {dest_json.name}")
            return dest_json
        except Exception as e:
            logger.error(f"[ChubClient] Ошибка записи JSON: {e}")
            return None
