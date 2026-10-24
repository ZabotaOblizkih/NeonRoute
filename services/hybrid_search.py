"""
Hybrid Search Service for Elaria's Knowledge Base (NeonRoute).
Strictly CPU-based execution (0 MB VRAM, <50 ms latency).
Provides:
1. BM25Index: Okapi BM25 sparse search preserving exact error codes, part numbers, articles, and symbols.
2. BuiltinDenseVectorizer / FastEmbed: Dense semantic vector search.
3. HybridSearchEngine: Document chunking (600-900 chars, 100 overlap), RRF reranking, and noise filtering.
"""

import re
import math
import hashlib
import logging
from typing import List, Dict, Any, Tuple, Optional, Set
import numpy as np

# Базовый набор стоп-слов для фильтрации шума в BM25 и Dense векторизаторе
COMMON_STOPWORDS: Set[str] = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все", "она", "так",
    "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "только", "ее", "мне", "было",
    "вот", "от", "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг",
    "ли", "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь", "опять", "уж",
    "вам", "ведь", "там", "потом", "себя", "ничего", "ей", "может", "они", "тут", "где", "есть",
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did"
}


class BM25Index:
    """
    Легковесная реализация алгоритма Okapi BM25 на чистом Python / NumPy без тяжелых зависимостей.
    Обеспечивает точный поиск по артикулам, номерам деталей (буквы + цифры, дефисы),
    кодам ошибок (0x..., ERR_...) и программным идентификаторам.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size: int = 0
        self.avgdl: float = 0.0
        self.doc_lengths: List[int] = []
        # term -> document frequency (count of docs containing term)
        self.df: Dict[str, int] = {}
        # term -> IDF score
        self.idf: Dict[str, float] = {}
        # term -> dict(doc_idx: term_count)
        self.inverted_index: Dict[str, Dict[int, int]] = {}
        # exact full-string cache for exact matching boosts
        self.raw_docs: List[str] = []

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        """
        Токенизация с сохранением точных кодов ошибок, артикулов, номеров деталей и слов.
        """
        if not text:
            return []

        text_lower = text.lower()
        # Заменяем управляющие символы на пробелы
        text_clean = re.sub(r"[\r\n\t]+", " ", text_lower)

        tokens: List[str] = []

        # 1. Шестнадцатеричные коды (например, 0xc0000005, 0x7ffe, 0x1a)
        hex_codes = re.findall(r"0x[0-9a-f]+", text_clean)
        tokens.extend(hex_codes)

        # 2. Идентификаторы с дефисами/подчеркиваниями (ERR_CONNECTION_RESET, A123-456, etc.)
        delimited_codes = re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)+", text_clean)
        tokens.extend(delimited_codes)
        for dc in delimited_codes:
            sub = re.split(r"[-_]", dc)
            tokens.extend([s for s in sub if len(s) >= 2])

        # 3. Составные артикулы и номера деталей с пробелами (например, '02t 311 239 b', 'vag 02t 311')
        part_patterns = re.findall(r"\b[a-z0-9]{1,5}(?:\s+[a-z0-9]{1,5}){2,4}\b", text_clean)
        for pp in part_patterns:
            joined_code = re.sub(r"\s+", "_", pp)
            tokens.append(joined_code)

        # 4. Стандартные слова (русский, английский, цифры)
        words = re.findall(r"[a-zа-яё0-9]+", text_clean)
        for w in words:
            if w not in COMMON_STOPWORDS and (len(w) >= 2 or w in "0123456789abcdef"):
                tokens.append(w)

        return tokens

    def index(self, corpus: List[str]) -> None:
        """
        Индексирует переданный корпус текстов.
        """
        self.corpus_size = len(corpus)
        self.raw_docs = corpus
        self.doc_lengths = []
        self.df = {}
        self.idf = {}
        self.inverted_index = {}

        if self.corpus_size == 0:
            self.avgdl = 0.0
            return

        total_length = 0
        for doc_idx, doc_text in enumerate(corpus):
            tokens = self.tokenize(doc_text)
            doc_len = len(tokens)
            self.doc_lengths.append(doc_len)
            total_length += doc_len

            # Считаем частоту терминов в данном документе
            term_freqs: Dict[str, int] = {}
            for t in tokens:
                term_freqs[t] = term_freqs.get(t, 0) + 1

            for term, count in term_freqs.items():
                self.df[term] = self.df.get(term, 0) + 1
                if term not in self.inverted_index:
                    self.inverted_index[term] = {}
                self.inverted_index[term][doc_idx] = count

        self.avgdl = float(total_length) / float(self.corpus_size) if self.corpus_size > 0 else 0.0

        # Вычисляем Okapi BM25 IDF: ln((N - n + 0.5) / (n + 0.5) + 1.0)
        for term, doc_freq in self.df.items():
            self.idf[term] = math.log((self.corpus_size - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[int, float]]:
        """
        Поиск по индексу BM25.
        :param query: Поисковый запрос
        :param top_k: Максимальное количество результатов
        :return: Список кортежей (doc_idx, score), отсортированных по убыванию
        """
        if self.corpus_size == 0 or not query or not query.strip():
            return []

        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []

        query_lower = query.lower().strip()
        doc_scores: Dict[int, float] = {}

        for term in query_tokens:
            if term not in self.inverted_index:
                continue

            term_idf = self.idf.get(term, 0.0)
            postings = self.inverted_index[term]

            for doc_idx, tf in postings.items():
                doc_len = self.doc_lengths[doc_idx]
                denom = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / (self.avgdl or 1.0)))
                term_score = term_idf * (tf * (self.k1 + 1.0)) / (denom or 1.0)
                doc_scores[doc_idx] = doc_scores.get(doc_idx, 0.0) + term_score

        # Дополнительный бонус за точное вхождение подстроки (артикула, кода ошибки) в текст
        for doc_idx in list(doc_scores.keys()):
            doc_text_lower = self.raw_docs[doc_idx].lower()
            if query_lower in doc_text_lower and len(query_lower) >= 4:
                doc_scores[doc_idx] += 3.0

        if not doc_scores:
            return []

        sorted_results = sorted(doc_scores.items(), key=lambda kv: kv[1], reverse=True)
        return sorted_results[:top_k]

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация индекса в словарь для кэширования."""
        return {
            "k1": self.k1,
            "b": self.b,
            "corpus_size": self.corpus_size,
            "avgdl": self.avgdl,
            "doc_lengths": self.doc_lengths,
            "df": self.df,
            "idf": self.idf,
            "inverted_index": {term: {str(d): c for d, c in post.items()} for term, post in self.inverted_index.items()},
        }

    def from_dict(self, data: Dict[str, Any], corpus: List[str]) -> None:
        """Восстановление индекса из словаря."""
        self.k1 = data.get("k1", 1.5)
        self.b = data.get("b", 0.75)
        self.corpus_size = data.get("corpus_size", len(corpus))
        self.avgdl = data.get("avgdl", 0.0)
        self.doc_lengths = data.get("doc_lengths", [])
        self.df = data.get("df", {})
        self.idf = data.get("idf", {})
        inv = data.get("inverted_index", {})
        self.inverted_index = {term: {int(d): c for d, c in post.items()} for term, post in inv.items()}
        self.raw_docs = corpus


class BuiltinDenseVectorizer:
    """
    Встроенный CPU-векторизатор на основе частотно-морфологических subword n-грамм
    и семантических ядер (0 МБ VRAM, время инференса < 0.5 мс).
    Обеспечивает устойчивость к перефразированию, синонимам и опечаткам.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    @classmethod
    def get_semantic_root(cls, w: str) -> Optional[str]:
        """Определяет семантическое ядро понятия по корню слова."""
        w_low = w.lower()
        if w_low.startswith(("охлажд", "охлад", "кулер", "вентилят", "термопаст", "нагрев", "темпер", "тепло", "cool", "fan")):
            return "sem_cooling"
        if w_low.startswith(("процес", "чип", "кристалл", "ядро")) or w_low in ("cpu", "intel", "amd"):
            return "sem_cpu"
        if w_low.startswith(("видеокарт", "видеочип", "график", "видеопамят")) or w_low in ("gpu", "vram", "rtx", "gtx"):
            return "sem_gpu"
        if w_low.startswith(("памят", "озу", "оперативк")) or w_low in ("ram", "ddr", "memory"):
            return "sem_ram"
        if w_low.startswith(("тормоз", "колодк", "суппорт", "диск")) or w_low in ("brake", "brakes"):
            return "sem_brakes"
        if w_low.startswith(("двигател", "мотор", "двс", "поршен", "цилиндр")) or w_low in ("engine", "motor"):
            return "sem_engine"
        if w_low.startswith(("коробк", "кпп", "трансмисс", "передач")) or w_low in ("transmission", "gearbox"):
            return "sem_transmission"
        if w_low.startswith(("настройк", "конфигур", "параметр", "опци")) or w_low in ("config", "settings", "setup"):
            return "sem_config"
        if w_low.startswith(("инструкц", "руководств", "мануал", "гайд")) or w_low in ("guide", "manual"):
            return "sem_manual"
        if w_low.startswith(("ошибк", "сбой", "вылет", "исключен")) or w_low in ("error", "exception", "crash", "fault", "bug"):
            return "sem_error"
        if w_low.startswith(("музык", "нот", "гармон", "аккорд", "фортепиан")) or w_low in ("music", "piano"):
            return "sem_music"
        if w_low.startswith(("исправлен", "починит", "устранен", "решен")) or w_low in ("fix", "resolve", "patch"):
            return "sem_fix"
        if w_low.startswith(("установк", "инсталляц", "развертыван", "монтаж")) or w_low in ("install", "installation"):
            return "sem_install"
        return None

    def _hash_to_dim(self, feature: str) -> Tuple[int, int]:
        """Преобразует строку в индекс признака и знак (-1 или +1) через murmur/md5."""
        h = int(hashlib.md5(feature.encode("utf-8")).hexdigest()[:8], 16)
        dim_idx = h % self.dim
        sign = 1 if ((h >> 8) & 1) == 0 else -1
        return dim_idx, sign

    def embed_text(self, text: str) -> np.ndarray:
        """
        Генерирует нормализованный dense-вектор размерности dim для текста на CPU.
        """
        vec = np.zeros(self.dim, dtype=np.float32)
        if not text or not text.strip():
            return vec

        text_lower = text.lower()
        words = re.findall(r"[a-zа-яё0-9]+", text_lower)
        if not words:
            return vec

        for w in words:
            if len(w) < 2 or w in COMMON_STOPWORDS:
                continue

            # 1. Смысловой стем слова
            stem = w[:5]
            idx, sign = self._hash_to_dim(f"word_{stem}")
            vec[idx] += 1.0 * sign

            # 2. Семантический кластер синонимов
            root = self.get_semantic_root(w)
            if root:
                r_idx, r_sign = self._hash_to_dim(f"root_{root}")
                vec[r_idx] += 4.0 * r_sign

            # 3. Символьные 3-граммы для устойчивости к опечаткам
            if len(w) >= 3:
                for i in range(len(w) - 2):
                    tri = w[i:i + 3]
                    t_idx, t_sign = self._hash_to_dim(f"tri_{tri}")
                    vec[t_idx] += 0.3 * t_sign

        # L2-нормализация вектора
        norm = np.linalg.norm(vec)
        if norm > 1e-6:
            vec /= norm
        return vec

    def embed_batch(self, texts: List[str]) -> np.ndarray:
        """Пакетное кодирование списка текстов в матрицу векторов."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self.embed_text(t) for t in texts])


class HybridSearchEngine:
    """
    Трехконтурный гибридный поисковый движок:
    Контур 1: BM25 Sparse поиск по точным артикулам, номерам деталей и кодам ошибок.
    Контур 2: Dense семантический векторный поиск (BuiltinDenseVectorizer или FastEmbed).
    Контур 3: RRF (Reciprocal Rank Fusion) и переранжирование с фильтрацией шума.
    Строго 0 МБ VRAM, CPU latency < 50 мс.
    """

    def __init__(
        self,
        dense_vectorizer: Optional[Any] = None,
        k1: float = 1.5,
        b: float = 0.75,
        alpha: float = 0.5,
        rrf_k: int = 60
    ):
        self.k1 = k1
        self.b = b
        self.alpha = alpha  # Вес Dense (1 - alpha для Sparse)
        self.rrf_k = rrf_k
        self.bm25 = BM25Index(k1=k1, b=b)
        self.dense_vectorizer = dense_vectorizer or BuiltinDenseVectorizer()
        self.chunks: List[Dict[str, Any]] = []
        self.dense_matrix: Optional[np.ndarray] = None

    @classmethod
    def chunk_text(
        cls,
        text: str,
        filename: str = "document",
        min_chunk: int = 600,
        max_chunk: int = 900,
        overlap: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Нарезка документа на семантические чанки (размер 600–900 символов с перекрытием 100 символов).
        Сохраняет целостность параграфов, предложений и строк.
        """
        if not text or not text.strip():
            return []

        clean_text = text.strip()

        # Если текст уже меньше или равен max_chunk, возвращаем один чанк целиком
        if len(clean_text) <= max_chunk:
            return [{
                "chunk_id": f"{filename}#c0",
                "filename": filename,
                "chunk_index": 0,
                "text": clean_text,
                "char_count": len(clean_text)
            }]

        paragraphs = clean_text.split("\n\n")
        raw_chunks: List[str] = []
        current_chunk = ""

        for p in paragraphs:
            p_strip = p.strip()
            if not p_strip:
                continue

            if not current_chunk:
                current_chunk = p_strip
            elif len(current_chunk) + 2 + len(p_strip) <= max_chunk:
                current_chunk += "\n\n" + p_strip
            else:
                # Если текущий чанк достиг достаточного размера (min_chunk)
                if len(current_chunk) >= min_chunk:
                    raw_chunks.append(current_chunk)
                    # Формируем перекрытие (последние ~overlap символов)
                    overlap_slice = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                    sp_idx = overlap_slice.find(" ")
                    if sp_idx != -1 and sp_idx < len(overlap_slice) - 10:
                        overlap_slice = overlap_slice[sp_idx + 1:]
                    current_chunk = (overlap_slice + "\n\n" + p_strip).strip()
                else:
                    current_chunk += "\n\n" + p_strip

            # Если отдельный абзац превышает max_chunk, нарезаем его по предложениям
            while len(current_chunk) > max_chunk:
                split_pos = current_chunk.rfind(". ", min_chunk, max_chunk)
                if split_pos == -1:
                    split_pos = current_chunk.rfind("\n", min_chunk, max_chunk)
                if split_pos == -1:
                    split_pos = current_chunk.rfind(" ", min_chunk, max_chunk)
                if split_pos == -1:
                    split_pos = max_chunk
                else:
                    split_pos += 1  # Захватываем точку или пробел

                chunk_slice = current_chunk[:split_pos].strip()
                raw_chunks.append(chunk_slice)

                overlap_slice = chunk_slice[-overlap:] if len(chunk_slice) > overlap else chunk_slice
                sp_idx = overlap_slice.find(" ")
                if sp_idx != -1 and sp_idx < len(overlap_slice) - 10:
                    overlap_slice = overlap_slice[sp_idx + 1:]
                current_chunk = (overlap_slice + " " + current_chunk[split_pos:]).strip()

        if current_chunk:
            # Если остаточный фрагмент слишком короткий и помещается в предыдущий чанк, объединяем
            if raw_chunks and len(current_chunk) < 300 and len(raw_chunks[-1]) + len(current_chunk) + 2 <= max_chunk + 150:
                raw_chunks[-1] = (raw_chunks[-1] + "\n\n" + current_chunk).strip()
            else:
                raw_chunks.append(current_chunk)

        # Формируем финальный список чанков
        result: List[Dict[str, Any]] = []
        for i, c_text in enumerate(raw_chunks):
            result.append({
                "chunk_id": f"{filename}#c{i}",
                "filename": filename,
                "chunk_index": i,
                "text": c_text,
                "char_count": len(c_text)
            })

        return result

    def index_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """
        Индексация списка чанков:
        1. BM25 инвертированный индекс.
        2. Dense матрица вложений.
        """
        self.chunks = chunks
        if not chunks:
            self.bm25.index([])
            self.dense_matrix = np.zeros((0, 256), dtype=np.float32)
            return

        texts = [c.get("text", "") for c in chunks]

        # 1. Построение BM25 индекса
        self.bm25.index(texts)

        # 2. Построение Dense матрицы векторов
        if hasattr(self.dense_vectorizer, "embed_batch"):
            self.dense_matrix = self.dense_vectorizer.embed_batch(texts)
        elif hasattr(self.dense_vectorizer, "embed"):
            # Поддержка FastEmbed TextEmbedding.embed()
            raw_vecs = list(self.dense_vectorizer.embed(texts))
            self.dense_matrix = np.vstack(raw_vecs) if raw_vecs else np.zeros((0, 256), dtype=np.float32)
        else:
            vecs = [self.dense_vectorizer.embed_text(t) for t in texts]
            self.dense_matrix = np.vstack(vecs) if vecs else np.zeros((0, 256), dtype=np.float32)

    def search(
        self,
        query: str,
        top_k: int = 3,
        threshold: float = 0.1,
        alpha: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Трехконтурный поиск: BM25 + Dense + RRF & Weighted Linear Fusion.
        :param query: Поисковый запрос
        :param top_k: Количество возвращаемых чанков (по умолчанию 3)
        :param threshold: Порог отсева нерелевантного шума
        :param alpha: Коэффициент баланса Sparse/Dense (по умолчанию self.alpha = 0.5)
        :return: Список наиболее релевантных словарей чанков с полями score, bm25_score, dense_score
        """
        if not self.chunks or not query or not query.strip():
            return []

        use_alpha = self.alpha if alpha is None else alpha
        num_chunks = len(self.chunks)

        # --- Контур 1: Sparse BM25 поиск ---
        bm25_hits = self.bm25.search(query, top_k=num_chunks)
        bm25_scores = np.zeros(num_chunks, dtype=np.float32)
        bm25_ranks: Dict[int, int] = {}
        for rank, (idx, sc) in enumerate(bm25_hits, 1):
            bm25_scores[idx] = sc
            bm25_ranks[idx] = rank

        # Нормализация BM25 скоров в диапазон [0, 1]
        max_bm25 = bm25_scores.max() if num_chunks > 0 else 0.0
        norm_bm25 = (bm25_scores / max_bm25) if max_bm25 > 1e-6 else bm25_scores

        # --- Контур 2: Dense семантический векторный поиск ---
        if hasattr(self.dense_vectorizer, "embed_text"):
            query_vec = self.dense_vectorizer.embed_text(query)
        elif hasattr(self.dense_vectorizer, "embed"):
            q_list = list(self.dense_vectorizer.embed([query]))
            query_vec = q_list[0] if q_list else np.zeros(256, dtype=np.float32)
            q_norm = np.linalg.norm(query_vec)
            if q_norm > 1e-6:
                query_vec = query_vec / q_norm
        else:
            query_vec = np.zeros(256, dtype=np.float32)
        if self.dense_matrix is not None and self.dense_matrix.shape[0] == num_chunks:
            # Косинусное сходство (dot product нормализованных векторов)
            dense_scores = np.dot(self.dense_matrix, query_vec)
            dense_scores = np.clip(dense_scores, 0.0, 1.0)
        else:
            dense_scores = np.zeros(num_chunks, dtype=np.float32)

        # Ранжирование Dense
        dense_sorted_indices = np.argsort(-dense_scores)
        dense_ranks: Dict[int, int] = {}
        for rank, idx in enumerate(dense_sorted_indices, 1):
            if dense_scores[idx] > 1e-4:
                dense_ranks[int(idx)] = rank

        # --- Контур 3: RRF & Weighted Fusion ---
        candidates: List[Dict[str, Any]] = []

        for idx in range(num_chunks):
            s_bm25 = float(norm_bm25[idx])
            s_dense = float(dense_scores[idx])

            # RRF (Reciprocal Rank Fusion)
            # score_rrf = 1/(k + rank_bm25) + 1/(k + rank_dense)
            r_bm25 = bm25_ranks.get(idx, 9999)
            r_dense = dense_ranks.get(idx, 9999)

            rrf_score = 0.0
            if r_bm25 < 9999:
                rrf_score += 1.0 / (self.rrf_k + r_bm25)
            if r_dense < 9999:
                rrf_score += 1.0 / (self.rrf_k + r_dense)

            # Взвешенная линейная комбинация
            linear_score = (1.0 - use_alpha) * s_bm25 + use_alpha * s_dense

            # Итоговый скор: синтез RRF и линейной комбинации
            final_score = round(0.5 * linear_score + 15.0 * rrf_score, 4)

            # Отсев шума по порогу threshold
            if (s_bm25 > 0.05 or s_dense > 0.15) and final_score >= threshold:
                chunk_copy = dict(self.chunks[idx])
                chunk_copy["score"] = final_score
                chunk_copy["bm25_score"] = round(s_bm25, 4)
                chunk_copy["dense_score"] = round(s_dense, 4)
                chunk_copy["rrf_score"] = round(rrf_score, 4)
                candidates.append(chunk_copy)

        # Сортировка по убыванию итогового скора
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates[:top_k]

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация состояния движка и всех чанков для кэша на диске."""
        return {
            "version": 1,
            "k1": self.k1,
            "b": self.b,
            "alpha": self.alpha,
            "rrf_k": self.rrf_k,
            "total_chunks": len(self.chunks),
            "chunks": self.chunks,
            "bm25_index": self.bm25.to_dict(),
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Восстановление состояния движка из сериализованных данных."""
        self.k1 = data.get("k1", 1.5)
        self.b = data.get("b", 0.75)
        self.alpha = data.get("alpha", 0.5)
        self.rrf_k = data.get("rrf_k", 60)
        self.chunks = data.get("chunks", [])

        texts = [c.get("text", "") for c in self.chunks]
        if "bm25_index" in data and texts:
            self.bm25.from_dict(data["bm25_index"], texts)
        else:
            self.bm25.index(texts)

        # Восстановление Dense матрицы
        if texts:
            if hasattr(self.dense_vectorizer, "embed_batch"):
                self.dense_matrix = self.dense_vectorizer.embed_batch(texts)
            else:
                vecs = [self.dense_vectorizer.embed_text(t) for t in texts]
                self.dense_matrix = np.vstack(vecs)
        else:
            self.dense_matrix = np.zeros((0, 256), dtype=np.float32)
