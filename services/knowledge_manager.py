"""
Knowledge Manager Service for Elaria's Knowledge Base (NeonRoute).
Extracts 8-10 triggers on CPU with strictly 0 MB VRAM, maintains registry.json,
and provides seamless file import, sync, and safe deletion.
"""

import os
import re
import json
import shutil
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional, Set, Union, Tuple

from config import KNOWLEDGE_BASE_DIR, KNOWLEDGE_REGISTRY_PATH
from utils.file_utils import atomic_write_json
from services.document_parser import DocumentParser, ParsedDocument
from services.hybrid_search import is_conversational_query
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("NeonRoute.KnowledgeManager")

RU_STOPWORDS: Set[str] = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все", "она", "так",
    "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "только", "ее", "мне", "было",
    "вот", "от", "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг",
    "ли", "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь", "опять", "уж",
    "вам", "ведь", "там", "потом", "себя", "ничего", "ей", "может", "они", "тут", "где", "есть",
    "надо", "ней", "для", "мы", "тебя", "их", "чем", "была", "сам", "чтоб", "без", "будто", "чего",
    "раз", "тоже", "себе", "под", "будет", "ж", "тогда", "кто", "этот", "того", "потому", "этого",
    "какой", "совсем", "ним", "здесь", "этом", "один", "почти", "мой", "тем", "чтобы", "нее", "сейчас",
    "были", "куда", "зачем", "всех", "никогда", "можно", "при", "наконец", "два", "об", "другой",
    "хоть", "после", "над", "больше", "тот", "через", "эти", "нас", "про", "всего", "них", "какая",
    "много", "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой", "перед", "иногда",
    "лучше", "чуть", "том", "нельзя", "такой", "им", "более", "всегда", "конечно", "всю", "между",
    "также", "очень", "который", "которая", "которые", "которое", "которых", "которому", "которыми",
    "котором", "своей", "своих", "своим", "своими", "своего", "своему", "своем", "таким", "такие",
    "таких", "таком", "такому", "такой", "такая", "какие", "каком", "каких", "какому", "какая",
    "поэтому", "однако", "либо", "кроме", "против", "около", "возле", "среди", "вокруг", "сквозь",
    "благодаря", "согласно", "вопреки", "вместо", "кстати", "далее", "например", "просто", "вообще",
    "вместе", "хотя", "будем", "будут", "будете", "будешь", "буду", "стал", "стала", "стали",
    "стало", "стать", "является", "являются", "каждый", "каждая", "каждое", "каждому", "путем",
    "числе", "включая", "следует", "могут", "может", "могли", "могла", "могло", "данный", "данная",
    "данное", "данные", "данных", "данным", "данными", "данном", "своем", "своей", "своих", "этом",
    "этой", "этих", "этому", "этим", "этими", "некоторое", "некоторые", "некоторых"
}

EN_STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "up", "about", "into", "over", "after", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "shall", "should", "may", "might", "must",
    "can", "could", "that", "which", "who", "whom", "this", "these", "those", "am", "it", "its",
    "as", "if", "then", "else", "when", "where", "why", "how", "all", "any", "both", "each", "few",
    "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than",
    "too", "very", "just", "now", "here", "there", "their", "theirs", "they", "them", "what",
    "again", "further", "once", "such", "between", "during", "before", "while", "above", "below",
    "under", "again", "each", "both", "either", "neither", "also", "well", "through", "down", "out"
}

CODE_STOPWORDS: Set[str] = {
    "def", "class", "import", "from", "return", "self", "none", "true", "false", "try", "except",
    "finally", "with", "as", "lambda", "pass", "raise", "while", "yield", "async", "await",
    "function", "const", "let", "var", "args", "kwargs", "null", "undefined", "http", "https",
    "print", "type", "name", "file", "line", "code", "text", "data", "bool", "int", "str", "float",
    "list", "dict", "tuple", "set", "true", "false", "len", "open", "read", "write", "close",
    "index", "item", "items", "keys", "values", "json", "path", "main", "init", "self"
}

ALL_STOPWORDS: Set[str] = RU_STOPWORDS | EN_STOPWORDS | CODE_STOPWORDS

RU_STEM_SUFFIXES = (
    "оваться", "иваться", "ываться", "ование", "евание",
    "ность", "ности", "остью", "овать", "евать", "ивать", "ывать",
    "овами", "евами", "скими", "ского", "скому", "ском", "ских",
    "ская", "ское", "ские", "ской", "скую",
    "ными", "ного", "ному", "ном", "ных", "ная", "ное", "ные", "ной", "ную",
    "ющий", "ющая", "ющее", "ющие", "вший", "вшая", "вшее", "вшие",
    "ться", "ется", "ится", "ются", "ятся", "лись", "лась", "лось", "лись",
    "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ях", "ах",
    "ов", "ев", "ей", "ом", "ем", "ям", "ам",
    "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий", "ой", "ей",
    "ть", "ти", "ла", "ли", "ло", "ся", "сь",
    "ка", "ки", "ку", "ке", "ко", "ок", "ек", "ик",
    "ы", "и", "а", "я", "о", "е", "у", "ю"
)

EN_STEM_SUFFIXES = (
    "ization", "action", "ations", "ation", "ments", "ment", "ingly",
    "able", "ible", "ness", "ship", "ings", "ting", "ning", "king",
    "ping", "sing", "ding", "ling", "ally", "ized", "ated",
    "ing", "ies", "ied", "ful", "less", "est", "ous", "ive",
    "ed", "ly", "es", "s"
)


class KnowledgeManager:
    """
    Фоновый сервис управления базой знаний Эларии.
    Работает строго на CPU (0 МБ VRAM), извлекает 8-10 ключевых триггеров из документов
    и ведет единый синхронизированный реестр registry.json.
    """

    SUPPORTED_EXTENSIONS = DocumentParser.SUPPORTED_EXTENSIONS

    _instance: Optional["KnowledgeManager"] = None
    _embedding_model: Optional[Any] = None

    @classmethod
    def get_instance(cls) -> "KnowledgeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get_embedding_model(cls) -> Any:
        if cls._embedding_model is None:
            try:
                from services import memory_db
                existing = getattr(memory_db, "_EMBED_MODEL", None)
                if existing not in (None, False) and type(existing).__name__ != "_FastEmbedMock":
                    cls._embedding_model = existing
                else:
                    from fastembed import TextEmbedding
                    if getattr(TextEmbedding, "__name__", "") == "_FastEmbedMock":
                        return None
                    cls._embedding_model = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
                    if existing is None:
                        memory_db._EMBED_MODEL = cls._embedding_model
            except Exception as e:
                logger.warning(f"[KnowledgeManager] Не удалось инициализировать FastEmbed: {e}")
        return cls._embedding_model

    @property
    def embedding_model(self) -> Any:
        return self.get_embedding_model()

    @classmethod
    def get_knowledge_dir(cls, custom_dir: Optional[Path] = None) -> Path:
        """Возвращает путь к директории базы знаний knowledge_base/."""
        target = Path(custom_dir) if custom_dir else KNOWLEDGE_BASE_DIR
        target.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def get_registry_path(cls, custom_path: Optional[Path] = None, custom_dir: Optional[Path] = None) -> Path:
        """Возвращает путь к реестру registry.json."""
        if custom_path:
            return Path(custom_path)
        k_dir = cls.get_knowledge_dir(custom_dir)
        return k_dir / "registry.json"

    _search_engine_cache: Dict[str, Any] = {}

    @classmethod
    def get_search_index_path(cls, custom_path: Optional[Path] = None, custom_dir: Optional[Path] = None) -> Path:
        """Возвращает путь к поисковому индексу search_index.json."""
        if custom_path:
            return Path(custom_path)
        k_dir = cls.get_knowledge_dir(custom_dir)
        return k_dir / "search_index.json"

    @classmethod
    def get_search_engine(
        cls,
        custom_dir: Optional[Path] = None,
        custom_index_path: Optional[Path] = None,
        reload: bool = False
    ) -> Any:
        """
        Возвращает инициализированный HybridSearchEngine.
        Загружает закэшированный индекс search_index.json с диска для мгновенного старта приложения.
        """
        from services.hybrid_search import HybridSearchEngine
        idx_path = cls.get_search_index_path(custom_index_path, custom_dir)
        cache_key = str(idx_path.resolve())

        if not reload and cache_key in cls._search_engine_cache:
            return cls._search_engine_cache[cache_key]

        engine = HybridSearchEngine(dense_vectorizer=cls.get_embedding_model())
        if idx_path.exists():
            try:
                with open(idx_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                engine.from_dict(data)
                cls._search_engine_cache[cache_key] = engine
                return engine
            except Exception as e:
                logger.warning(f"[KnowledgeManager] Ошибка чтения search_index.json: {e}")

        cls._search_engine_cache[cache_key] = engine
        return engine

    @classmethod
    def _get_stem(cls, word_lower: str) -> str:
        """Легковесный эвристический стемминг на CPU без внешних библиотек."""
        if len(word_lower) <= 3:
            return word_lower

        # Проверка кириллицы
        if re.search(r"[а-яё]", word_lower):
            for suff in RU_STEM_SUFFIXES:
                if word_lower.endswith(suff) and len(word_lower) - len(suff) >= 3:
                    return word_lower[:-len(suff)]
            return word_lower

        # Проверка латиницы
        for suff in EN_STEM_SUFFIXES:
            if word_lower.endswith(suff) and len(word_lower) - len(suff) >= 3:
                return word_lower[:-len(suff)]
        return word_lower

    @classmethod
    def extract_triggers(cls, text: str, max_triggers: int = 10) -> List[str]:
        """
        Быстрое извлечение 8-10 ключевых триггеров на CPU:
        - Очистка от стоп-слов RU/EN/Code.
        - Частотно-семантическая фильтрация терминов и лемм без вызова LLM (0 МБ VRAM).
        - Определение акронимов, заголовков и доменных терминов.
        :param text: Исходный текст документа
        :param max_triggers: Максимальное количество триггеров (по умолчанию 10)
        :return: Список из 8-10 строк (триггерных слов/терминов)
        """
        if not text or not text.strip():
            return []

        # 1. Извлечение заголовков Markdown (#, ##, ###)
        headings = set()
        for line in text.splitlines():
            line_str = line.strip()
            if line_str.startswith("#"):
                clean_h = re.sub(r"^#+\s*", "", line_str)
                for h_word in re.findall(r"[a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9_-]{2,}", clean_h):
                    headings.add(h_word.lower())

        # 2. Удаление блоков кода, ссылок, URL, base64 / шестнадцатеричных строк
        clean_text = re.sub(r"```[\s\S]*?```", " ", text)
        clean_text = re.sub(r"https?://\S+", " ", clean_text)
        clean_text = re.sub(r"[0-9a-fA-F]{16,}", " ", clean_text)

        # 3. Токенизация слов
        raw_words = re.findall(r"[a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9_-]{2,}", clean_text)
        if not raw_words:
            return []

        # 4. Кластеризация и подсчет весов
        stem_scores = defaultdict(float)
        stem_surface_forms = defaultdict(lambda: Counter())

        for raw_word in raw_words:
            w_lower = raw_word.lower()
            if w_lower in ALL_STOPWORDS:
                continue
            if len(w_lower) < 3:
                continue
            if w_lower.isdigit():
                continue

            stem = cls._get_stem(w_lower)
            if stem in ALL_STOPWORDS:
                continue

            # Базовый вес частоты
            weight = 1.0

            # Бонус за присутствие в заголовках
            if w_lower in headings or stem in headings:
                weight += 2.5

            # Бонус за акроним (все буквы заглавные, например VRAM, API, PDF)
            if raw_word.isupper() and len(raw_word) >= 2:
                weight += 2.0

            # Бонус за CamelCase / PascalCase
            elif any(c.isupper() for c in raw_word[1:]):
                weight += 1.8

            # Бонус за капитализацию
            elif raw_word[0].isupper():
                weight += 0.8

            stem_scores[stem] += weight
            # Сохраняем исходное начертание для выбора лучшей поверхностной формы
            stem_surface_forms[stem][raw_word] += 1

        if not stem_scores:
            return []

        # 5. Сортировка по итоговой релевантности
        sorted_stems = sorted(stem_scores.items(), key=lambda kv: kv[1], reverse=True)

        triggers: List[str] = []
        seen_words: Set[str] = set()

        for stem, _ in sorted_stems:
            forms = stem_surface_forms[stem]
            # Выбираем наиболее часто используемую форму слова
            best_form = forms.most_common(1)[0][0]

            # Нормализация: если это не акроним и не CamelCase, приводим к lowercase
            if not best_form.isupper() and not any(c.isupper() for c in best_form[1:]):
                best_form = best_form.lower()

            norm_key = best_form.lower()
            if norm_key not in seen_words:
                seen_words.add(norm_key)
                triggers.append(best_form)

            if len(triggers) >= max_triggers:
                break

        return triggers

    @classmethod
    def _compute_file_hash(cls, path: Path) -> str:
        """Вычисляет быстрый SHA-256 хэш файла на CPU."""
        sha = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    sha.update(chunk)
            return sha.hexdigest()
        except Exception as e:
            logger.debug(f"Ошибка вычисления хэша для {path}: {e}")
            return ""

    @classmethod
    def load_registry(cls, registry_path: Optional[Path] = None, custom_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Загружает registry.json или возвращает базовую структуру при отсутствии."""
        reg_path = cls.get_registry_path(registry_path, custom_dir)
        if reg_path.exists():
            try:
                with open(reg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        if "documents" not in data:
                            data["documents"] = {}
                        return data
            except Exception as e:
                logger.warning(f"Ошибка чтения реестра {reg_path}: {e}")

        return {
            "version": 1,
            "last_synced_at": None,
            "total_documents": 0,
            "documents": {}
        }

    @classmethod
    def scan_and_sync(
        cls,
        custom_dir: Optional[Path] = None,
        custom_registry: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Сканирует knowledge_base/ на наличие поддерживаемых файлов (.pdf, .txt, .log, .py, .json, .md).
        - Для новых/измененных файлов: парсинг через DocumentParser, извлечение 8-10 триггеров, обновление registry.json.
        - Для удаленных файлов: безопасное удаление записей из реестра.
        :return: Обновленный словарь реестра
        """
        k_dir = cls.get_knowledge_dir(custom_dir)
        reg_path = cls.get_registry_path(custom_registry, custom_dir)

        registry_data = cls.load_registry(reg_path, k_dir)
        current_docs: Dict[str, Any] = registry_data.get("documents", {})

        # Поиск всех поддерживаемых файлов в папке знаний
        found_files: Dict[str, Path] = {}
        for item in k_dir.iterdir():
            if item.is_file() and item.name not in ("registry.json", "search_index.json"):
                if item.suffix.lower() in cls.SUPPORTED_EXTENSIONS:
                    found_files[item.name] = item

        # 1. Проверяем новые или изменившиеся файлы
        newly_indexed_files: Set[str] = set()
        for filename, file_path in found_files.items():
            try:
                stat = file_path.stat()
                file_mtime = stat.st_mtime
                file_size = stat.st_size
                existing = current_docs.get(filename)

                needs_indexing = False
                file_hash = ""

                if not existing:
                    needs_indexing = True
                else:
                    prev_mtime = existing.get("mtime", 0.0)
                    prev_size = existing.get("size_bytes", 0)
                    if abs(file_mtime - prev_mtime) > 1e-4 or file_size != prev_size:
                        needs_indexing = True

                if needs_indexing:
                    file_hash = cls._compute_file_hash(file_path)
                    # Если хэш совпадает, парсинг не нужен
                    if existing and existing.get("hash") == file_hash and file_hash:
                        existing["mtime"] = file_mtime
                        continue

                    logger.info(f"[KnowledgeManager] Индексация документа: {filename}")
                    parsed: Optional[ParsedDocument] = DocumentParser.parse_file(file_path)

                    if parsed:
                        triggers = cls.extract_triggers(parsed.content, max_triggers=10)
                        current_docs[filename] = {
                            "filename": filename,
                            "relative_path": filename,
                            "path": str(file_path.resolve()),
                            "mtime": file_mtime,
                            "hash": file_hash,
                            "size_bytes": file_size,
                            "size_kb": parsed.size_kb,
                            "line_count": parsed.line_count,
                            "page_count": parsed.page_count,
                            "updated_at": datetime.now().isoformat(),
                            "triggers": triggers,
                            "status": "parsed"
                        }
                        newly_indexed_files.add(filename)
                    else:
                        current_docs[filename] = {
                            "filename": filename,
                            "relative_path": filename,
                            "path": str(file_path.resolve()),
                            "mtime": file_mtime,
                            "hash": file_hash,
                            "size_bytes": file_size,
                            "size_kb": round(file_size / 1024.0, 2),
                            "line_count": 0,
                            "page_count": None,
                            "updated_at": datetime.now().isoformat(),
                            "triggers": [],
                            "status": "error"
                        }
            except Exception as e:
                logger.error(f"[KnowledgeManager] Ошибка при обработке {filename}: {e}", exc_info=True)

        # 2. Удаление из реестра файлов, которых больше нет на диске
        deleted_keys = [fname for fname in current_docs if fname not in found_files]
        for fname in deleted_keys:
            logger.info(f"[KnowledgeManager] Удаление устаревшей записи из реестра: {fname}")
            del current_docs[fname]

        registry_data["documents"] = current_docs
        registry_data["total_documents"] = len(current_docs)
        registry_data["last_synced_at"] = datetime.now().isoformat()

        # Атомарная запись на диск
        atomic_write_json(reg_path, registry_data)
        logger.info(f"[KnowledgeManager] Синхронизация завершена: {len(current_docs)} документов в реестре.")

        # 3. Синхронизация чанков и поискового индекса search_index.json
        try:
            from services.hybrid_search import HybridSearchEngine
            search_idx_path = cls.get_search_index_path(None, custom_dir)
            existing_chunks_by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

            if search_idx_path.exists():
                try:
                    with open(search_idx_path, "r", encoding="utf-8") as f:
                        old_idx = json.load(f)
                    for ch in old_idx.get("chunks", []):
                        fn = ch.get("filename")
                        if fn and fn in current_docs:
                            existing_chunks_by_file[fn].append(ch)
                except Exception as ex:
                    logger.debug(f"[KnowledgeManager] Старый search_index.json не прочитан: {ex}")

            all_chunks: List[Dict[str, Any]] = []
            for fname, doc_meta in current_docs.items():
                if doc_meta.get("status") != "parsed":
                    continue
                if fname in existing_chunks_by_file and fname not in newly_indexed_files:
                    all_chunks.extend(existing_chunks_by_file[fname])
                else:
                    fpath = k_dir / fname
                    if fpath.exists():
                        parsed_doc = DocumentParser.parse_file(fpath, max_chars=None)
                        if parsed_doc and parsed_doc.content:
                            doc_chunks = HybridSearchEngine.chunk_text(parsed_doc.content, filename=fname)
                            all_chunks.extend(doc_chunks)

            engine = HybridSearchEngine(dense_vectorizer=cls.get_embedding_model())
            engine.index_chunks(all_chunks)
            atomic_write_json(search_idx_path, engine.to_dict())
            cls._search_engine_cache[str(search_idx_path.resolve())] = engine
            logger.info(f"[KnowledgeManager] Поисковый индекс search_index.json обновлен: {len(all_chunks)} чанков.")
        except Exception as se:
            logger.error(f"[KnowledgeManager] Ошибка обновления поискового индекса: {se}", exc_info=True)

        return registry_data

    @classmethod
    def import_file(
        cls,
        src_path: Union[str, Path],
        custom_dir: Optional[Path] = None,
        custom_registry: Optional[Path] = None
    ) -> bool:
        """
        Копирование файла в knowledge_base/ с последующей мгновенной индексацией.
        :param src_path: Исходный путь к файлу
        :return: True при успехе, False при ошибке
        """
        if not src_path:
            return False

        try:
            source = Path(src_path).resolve()
            if not source.exists() or not source.is_file():
                logger.warning(f"[KnowledgeManager] Исходный файл не найден: {src_path}")
                return False

            if source.suffix.lower() not in cls.SUPPORTED_EXTENSIONS:
                logger.warning(f"[KnowledgeManager] Неподдерживаемый формат: {source.suffix}")
                return False

            k_dir = cls.get_knowledge_dir(custom_dir)
            destination = k_dir / source.name

            # Безопасное копирование
            shutil.copy2(source, destination)

            # Мгновенная индексация
            cls.scan_and_sync(custom_dir=k_dir, custom_registry=custom_registry)
            return True
        except Exception as e:
            logger.error(f"[KnowledgeManager] Ошибка импорта файла {src_path}: {e}", exc_info=True)
            return False

    @classmethod
    def delete_file(
        cls,
        filename: str,
        custom_dir: Optional[Path] = None,
        custom_registry: Optional[Path] = None
    ) -> bool:
        """
        Безопасное удаление файла из папки знаний knowledge_base/ и реестра registry.json.
        :param filename: Имя файла (например, 'manual.pdf')
        :return: True при успешном удалении, иначе False
        """
        if not filename:
            return False

        try:
            k_dir = cls.get_knowledge_dir(custom_dir)
            reg_path = cls.get_registry_path(custom_registry, custom_dir)
            target = k_dir / Path(filename).name

            if target.exists():
                try:
                    target.unlink()
                except Exception as fe:
                    logger.warning(f"[KnowledgeManager] Не удалось удалить файл {target}: {fe}")

            registry_data = cls.load_registry(reg_path, k_dir)
            docs = registry_data.get("documents", {})
            name_key = Path(filename).name

            if name_key in docs:
                del docs[name_key]
                registry_data["total_documents"] = len(docs)
                registry_data["last_synced_at"] = datetime.now().isoformat()
                atomic_write_json(reg_path, registry_data)
                logger.info(f"[KnowledgeManager] Документ {name_key} удален из папки и реестра.")

                # Очистка чанков удаленного файла из поискового индекса
                try:
                    search_idx_path = cls.get_search_index_path(None, custom_dir)
                    if search_idx_path.exists():
                        with open(search_idx_path, "r", encoding="utf-8") as f:
                            s_data = json.load(f)
                        all_ch = s_data.get("chunks", [])
                        filtered_ch = [c for c in all_ch if c.get("filename") != name_key]
                        if len(filtered_ch) != len(all_ch):
                            from services.hybrid_search import HybridSearchEngine
                            eng = HybridSearchEngine(dense_vectorizer=cls.get_embedding_model())
                            eng.index_chunks(filtered_ch)
                            atomic_write_json(search_idx_path, eng.to_dict())
                            cls._search_engine_cache[str(search_idx_path.resolve())] = eng
                except Exception as se:
                    logger.debug(f"[KnowledgeManager] Ошибка очистки search_index при удалении: {se}")

                return True
            else:
                # Если файла не было в реестре, но он удален с диска
                atomic_write_json(reg_path, registry_data)
                return True
        except Exception as e:
            logger.error(f"[KnowledgeManager] Ошибка при удалении файла {filename}: {e}", exc_info=True)
            return False

    @classmethod
    def get_documents(
        cls,
        custom_dir: Optional[Path] = None,
        custom_registry: Optional[Path] = None
    ) -> List[Dict[str, Any]]:
        """Возвращает список всех зарегистрированных документов."""
        reg = cls.load_registry(custom_registry, custom_dir)
        docs = reg.get("documents", {})
        return list(docs.values())

    GENERIC_STEMS: Set[str] = {
        "base", "core", "main", "test", "data", "view",
        "flow", "app", "model", "route", "util", "utils", "user",
        "doc", "docs", "file", "index", "init", "setup", "log"
    }

    @classmethod
    def _fuse_contextual_query(cls, query: str, previous_query: Optional[str]) -> str:
        """Склеивает запрос с контекстом ТОЛЬКО при явных маркерах коррекции или анафоры."""
        if not previous_query or not previous_query.strip():
            return query
        q_lower = query.lower().strip()

        # Маркеры явной коррекции прошлого ответа
        corrective_markers = [
            "не то", "не этот", "не эта", "не эти", "другой", "другое", 
            "другие", "дальше", "следующ", "вместо", "перепутал", "не от", "именно от"
        ]
        is_corrective = any(m in q_lower for m in corrective_markers)

        # Маркеры анафоры (прямая ссылка на объект из прошлого сообщения)
        anaphoric_markers = [
            "а в нем", "а у него", "а к нему", "покажи его", "покажи ее", 
            "его код", "ее код", "у этого", "в этом"
        ]
        is_anaphoric = any(m in q_lower for m in anaphoric_markers)

        # Если это самостоятельный запрос без маркеров связи — не засоряем его старыми темами
        if not (is_corrective or is_anaphoric):
            return query

        prev_clean = re.sub(r"[^\w\s-]", " ", previous_query.lower())
        prev_tokens = [w for w in prev_clean.split() if w not in ALL_STOPWORDS and len(w) >= 3 and not w.isdigit()]
        if not prev_tokens:
            return query

        added_context = " ".join(dict.fromkeys(prev_tokens[:3]))
        return f"{query} {added_context}"

    @classmethod
    def _find_cross_references(cls, text: str, registered_filenames: Set[str]) -> List[str]:
        """Обнаруживает упоминания зарегистрированных файлов с защитой от ложных generic-совпадений."""
        if not text or not registered_filenames:
            return []
        found: List[str] = []

        # 1. Поиск по точному имени файла с расширением (100% доверие)
        file_matches = re.findall(r'[\w\-]+\.(?:py|md|pdf|json|txt|log)', text, re.IGNORECASE)
        for fm in file_matches:
            for reg_fn in registered_filenames:
                if fm.lower() == reg_fn.lower() and reg_fn not in found:
                    found.append(reg_fn)

        # 2. Поиск по стэму: только не-generic имена и только выделенные как сущности
        for reg_fn in registered_filenames:
            stem = Path(reg_fn).stem
            stem_lower = stem.lower()
            if stem_lower in cls.GENERIC_STEMS or len(stem) < 4 or reg_fn in found:
                continue

            # Требуем оформление: в кавычках/бэктиках (`config`, "config") или после "модуль/файл config"
            entity_pattern = rf'(?:[`"\'«](\b{re.escape(stem)}\b)[`"\'»]|(?:модул[ьяеи]|файл[ае]?|модуле)\s+(\b{re.escape(stem)}\b))'
            if re.search(entity_pattern, text, re.IGNORECASE):
                found.append(reg_fn)
        return found

    @classmethod
    def find_relevant_documents(
        cls,
        query: str,
        threshold: float = 0.3,
        custom_dir: Optional[Path] = None,
        custom_registry: Optional[Path] = None,
        custom_index_path: Optional[Path] = None,
        exclude_chunk_ids: Optional[Set[str]] = None,
        previous_query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Ищет релевантные документы с использованием гибридного поиска (BM25 + Dense + RRF)
        и частотно-семантического анализа триггеров на CPU (0 МБ VRAM).
        - Выполняет трехконтурный поиск по семантическим чанкам.
        - Сопоставляет триггеры и заголовки документов из registry.json.
        - Возвращает список подходящих документов с добавленными полями relevance_score,
          chunk_id, content_snippet и matched_triggers.
        """
        if not query or not query.strip():
            return []

        engine = cls.get_search_engine(custom_dir, custom_index_path)

        # Gatekeeper: отсекаем светскую беседу (small talk) без затрагивания базы знаний
        bm25_idx = getattr(engine, "bm25", None) if engine else None
        if is_conversational_query(query, bm25_index=bm25_idx):
            return []

        # 1. Гибридный поиск по чанкам (BM25 + Dense + RRF) с поддержкой Query Fusion и exclude_chunk_ids
        search_query = cls._fuse_contextual_query(query, previous_query)
        exclude_set = set(exclude_chunk_ids) if exclude_chunk_ids else set()

        chunks_by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        try:
            # Динамический оверсэмплинг против голодания после фильтрации exclude_set
            oversample_k = min(50, 15 + len(exclude_set) * 2)
            raw_chunk_hits = engine.search(search_query, top_k=oversample_k, threshold=0.05)
            chunk_hits = [ch for ch in raw_chunk_hits if ch.get("chunk_id") not in exclude_set][:15]
            for ch in chunk_hits:
                fn = ch.get("filename")
                if fn:
                    chunks_by_file[fn].append(ch)
        except Exception as se:
            logger.debug(f"[KnowledgeManager] Ошибка поиска по чанкам: {se}")

        # 2. Токенизация и нормализация запроса пользователя для триггерного сопоставления
        clean_q = re.sub(r"```[\s\S]*?```", " ", query)
        clean_q = re.sub(r"https?://\S+", " ", clean_q)
        raw_tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_-]{2,}", clean_q)

        query_words: Set[str] = set()
        for tok in raw_tokens:
            w = tok.lower()
            if w in ALL_STOPWORDS or w.isdigit() or len(w) < 2:
                continue
            query_words.add(w)

        # 3. Загрузка документов из реестра
        registry_data = cls.load_registry(custom_registry, custom_dir)
        documents: Dict[str, Any] = registry_data.get("documents", {})
        if not documents:
            return []

        matched_results: List[Dict[str, Any]] = []
        processed_files: Set[str] = set()

        for filename, doc in documents.items():
            triggers = [t.lower() for t in doc.get("triggers", [])]
            if not triggers and doc.get("status") != "parsed" and filename not in chunks_by_file:
                continue

            trigger_stems: Dict[str, str] = {cls._get_stem(t): t for t in triggers}
            filename_base = Path(filename).stem.lower()
            filename_stem = cls._get_stem(filename_base)

            matched_trigs: List[str] = []
            score_points = 0.0

            for q_word in query_words:
                q_stem = cls._get_stem(q_word)

                if q_word in triggers:
                    score_points += 1.0
                    matched_trigs.append(q_word)
                elif q_stem in trigger_stems:
                    score_points += 0.85
                    matched_trigs.append(trigger_stems[q_stem])
                elif any(q_stem in t or t in q_stem for t in trigger_stems if len(q_stem) >= 3 and len(t) >= 3):
                    score_points += 0.6
                    for t_s, orig_t in trigger_stems.items():
                        if (q_stem in t_s or t_s in q_stem) and orig_t not in matched_trigs:
                            matched_trigs.append(orig_t)
                            break

                if q_word == filename_base or (len(q_word) >= 3 and q_word in filename_base):
                    score_points += 1.2
                elif q_stem == filename_stem or (len(q_stem) >= 3 and q_stem in filename_base):
                    score_points += 0.9

            norm_score = round(score_points / max(1.0, float(len(query_words))), 3) if query_words else 0.0

            file_chunks = chunks_by_file.get(filename, [])
            best_chunk = file_chunks[0] if file_chunks else None
            chunk_score = best_chunk.get("score", 0.0) if best_chunk else 0.0

            # Gatekeeper: если нет совпадения по триггерам/имени файла, требуем от чанка либо BM25 > 0,
            # либо высокой семантической уверенности (dense_score >= dense_gate)
            dense_gate = engine._get_dense_gatekeeper_threshold() if hasattr(engine, "_get_dense_gatekeeper_threshold") else 0.75
            b_dense = best_chunk.get("dense_score", 0.0) if best_chunk else 0.0
            if score_points <= 0.0:
                if not best_chunk:
                    continue
                b_bm25 = best_chunk.get("bm25_score", 0.0)
                if b_bm25 <= 1e-5 and b_dense < dense_gate:
                    continue

            # Синтез скоров гибридного поиска, семантической близости и триггерного фильтра
            semantic_boost = round(b_dense * 0.6, 3) if b_dense >= dense_gate else 0.0
            combined_score = max(norm_score, chunk_score, semantic_boost)

            if combined_score >= threshold or (score_points >= 1.0 and norm_score >= 0.2) or chunk_score >= threshold:
                doc_entry = dict(doc)
                doc_entry["relevance_score"] = min(1.0, round(combined_score, 3))
                doc_entry["matched_triggers"] = list(dict.fromkeys(matched_trigs))
                if best_chunk:
                    MAX_SNIPPET_BUDGET = 8500
                    MAX_CHUNKS_PER_FILE = 6
                    selected_chunks = []
                    accumulated_chars = 0
                    for ch in file_chunks[:MAX_CHUNKS_PER_FILE]:
                        txt = ch.get("text", "").strip()
                        if not txt:
                            continue
                        if accumulated_chars + len(txt) > MAX_SNIPPET_BUDGET and selected_chunks:
                            break
                        selected_chunks.append(ch)
                        accumulated_chars += len(txt)

                    snippets = [c.get("text", "").strip() for c in selected_chunks]
                    doc_entry["content_snippet"] = "\n\n---\n\n".join(snippets) if snippets else best_chunk.get("text")
                    doc_entry["matched_chunks"] = selected_chunks
                    doc_entry["chunk_id"] = selected_chunks[0].get("chunk_id") if selected_chunks else best_chunk.get("chunk_id")
                matched_results.append(doc_entry)
                processed_files.add(filename)

        # 4. Проверяем документы, найденные через чанки, но отсутствовавшие в цикле по триггерам
        for fn, f_chunks in chunks_by_file.items():
            if fn not in processed_files and fn in documents:
                doc = documents[fn]
                best_ch = f_chunks[0]
                doc_entry = dict(doc)
                doc_entry["relevance_score"] = min(1.0, round(best_ch.get("score", 0.0), 3))
                doc_entry["matched_triggers"] = []
                doc_entry["chunk_id"] = best_ch.get("chunk_id")
                MAX_SNIPPET_BUDGET = 8500
                MAX_CHUNKS_PER_FILE = 6
                selected_chunks = []
                accumulated_chars = 0
                for ch in f_chunks[:MAX_CHUNKS_PER_FILE]:
                    txt = ch.get("text", "").strip()
                    if not txt:
                        continue
                    if accumulated_chars + len(txt) > MAX_SNIPPET_BUDGET and selected_chunks:
                        break
                    selected_chunks.append(ch)
                    accumulated_chars += len(txt)

                snippets = [c.get("text", "").strip() for c in selected_chunks]
                doc_entry["content_snippet"] = "\n\n---\n\n".join(snippets) if snippets else best_ch.get("text")
                doc_entry["matched_chunks"] = selected_chunks
                doc_entry["chunk_id"] = selected_chunks[0].get("chunk_id") if selected_chunks else best_ch.get("chunk_id")
                matched_results.append(doc_entry)
                processed_files.add(fn)

        # 5. Cross-Reference Linker: авто-добор чанков из упомянутых в результатах файлов
        try:
            registered_files = set(documents.keys())
            cross_candidates: List[Tuple[str, float]] = []
            for m_doc in matched_results[:2]:
                snippet_text = m_doc.get("content_snippet", "")
                parent_score = m_doc.get("relevance_score", 0.7)
                for ref_fn in cls._find_cross_references(snippet_text, registered_files):
                    if ref_fn not in processed_files and not any(ref_fn == c[0] for c in cross_candidates):
                        cross_candidates.append((ref_fn, parent_score))

            # Добираем до 2 файлов по перекрестным ссылкам
            for ref_fn, parent_score in cross_candidates[:2]:
                doc = documents.get(ref_fn, {})
                engine = cls.get_search_engine(custom_dir, custom_index_path)
                ref_hits = engine.search(f"{query} {Path(ref_fn).stem}", top_k=8, threshold=0.01)
                valid_ref_chunks = [
                    ch for ch in ref_hits 
                    if ch.get("filename") == ref_fn and ch.get("chunk_id") not in exclude_set
                ][:2]

                if valid_ref_chunks:
                    ref_snippets = [c.get("text", "").strip() for c in valid_ref_chunks if c.get("text")]
                    doc_entry = dict(doc)
                    # Скор дочернего документа кэпируется чуть ниже родительского
                    doc_entry["relevance_score"] = round(max(0.1, parent_score - 0.05), 3)
                    doc_entry["matched_triggers"] = ["cross_reference"]
                    doc_entry["chunk_id"] = valid_ref_chunks[0].get("chunk_id")
                    doc_entry["content_snippet"] = "\n\n---\n\n".join(ref_snippets)
                    doc_entry["matched_chunks"] = valid_ref_chunks
                    doc_entry["is_cross_reference"] = True
                    matched_results.append(doc_entry)
                    processed_files.add(ref_fn)
        except Exception as ce:
            logger.debug(f"[KnowledgeManager] Ошибка Cross-Reference Linker: {ce}")

        matched_results.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
        return matched_results

    @classmethod
    def get_document_knowledge_snippet(
        cls,
        filename: str,
        max_chars: int = 6000,
        query: Optional[str] = None,
        custom_dir: Optional[Path] = None,
        custom_index_path: Optional[Path] = None
    ) -> Optional[str]:
        """
        Извлекает текст документа (строго на CPU, 0 МБ VRAM).
        - Если передан query, извлекает наиболее релевантные чанки документа через гибридный поиск.
        - Если query не передан, извлекает текст через DocumentParser с ограничением max_chars.
        """
        if not filename:
            return None

        try:
            k_dir = cls.get_knowledge_dir(custom_dir)
            target = k_dir / Path(filename).name
            if not target.exists() or not target.is_file():
                logger.warning(f"[KnowledgeManager] Файл для сниппета не найден: {target}")
                return None

            # Если передан запрос, извлекаем релевантные чанки данного документа
            if query and query.strip():
                try:
                    engine = cls.get_search_engine(custom_dir, custom_index_path)
                    hits = engine.search(query, top_k=10, threshold=0.05)
                    file_chunks = [h for h in hits if h.get("filename") == Path(filename).name]
                    if file_chunks:
                        combined_texts = []
                        total_len = 0
                        for ch in file_chunks:
                            txt = ch.get("text", "").strip()
                            if total_len + len(txt) > max_chars:
                                remaining = max_chars - total_len
                                if remaining > 50:
                                    combined_texts.append(txt[:remaining])
                                break
                            combined_texts.append(txt)
                            total_len += len(txt) + 4
                        if combined_texts:
                            return "\n\n---\n\n".join(combined_texts)
                except Exception as se:
                    logger.debug(f"[KnowledgeManager] Ошибка поиска чанков для сниппета {filename}: {se}")

            parsed = DocumentParser.parse_file(target, max_chars=max_chars)
            if not parsed or not parsed.content:
                return None
            return parsed.content
        except Exception as e:
            logger.error(f"[KnowledgeManager] Ошибка получения сниппета {filename}: {e}", exc_info=True)
            return None

    @classmethod
    def search_chunks(
        cls,
        query: str,
        top_k: int = 3,
        threshold: float = 0.1,
        custom_dir: Optional[Path] = None,
        custom_index_path: Optional[Path] = None
    ) -> List[Dict[str, Any]]:
        """
        Прямой гибридный поиск по чанкам базы знаний (CPU, 0 МБ VRAM).
        Возвращает топ-2..3 наиболее точных чанков с указанием filename, chunk_id, score, text.
        """
        if not query or not query.strip():
            return []

        engine = cls.get_search_engine(custom_dir, custom_index_path)
        # Gatekeeper: отсекаем светскую беседу (small talk) без затрагивания базы знаний
        bm25_idx = getattr(engine, "bm25", None) if engine else None
        if is_conversational_query(query, bm25_index=bm25_idx):
            return []
        return engine.search(query, top_k=top_k, threshold=threshold)



class KnowledgeSyncWorker(QThread):
    """
    Фоновый рабочий поток для синхронизации базы знаний на CPU без блокировки GUI.
    """
    sync_finished = pyqtSignal(dict)
    sync_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            result = KnowledgeManager.scan_and_sync()
            self.sync_finished.emit(result)
        except Exception as e:
            logger.error(f"[KnowledgeSyncWorker] Ошибка фоновой синхронизации базы знаний: {e}", exc_info=True)
            self.sync_error.emit(str(e))

