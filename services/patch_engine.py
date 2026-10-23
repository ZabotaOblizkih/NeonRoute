import os
import re
import difflib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional

from services.sandbox_manager import SandboxManager

logger = logging.getLogger("NeonRoute.PatchEngine")


@dataclass
class PatchResult:
    success: bool
    new_content: str
    error: str = ""
    applied_count: int = 0
    is_fuzzy: bool = False


class PatchApplier:
    """
    Хирургическая замена блоков кода без перезаписи всего файла.
    Устойчив к огрехам 7B-моделей в пробелах, отступах и переводах строк.
    """

    DIFF_PATTERN = re.compile(
        r'<{3,}\s*SEARCH[\r\n]*(?P<search>.*?)[\r\n]*={3,}[\r\n]*(?P<replace>.*?)[\r\n]*>{3,}(?:\s*REPLACE)?',
        re.DOTALL | re.IGNORECASE
    )

    @classmethod
    def parse_diff(cls, diff_text: str) -> List[Tuple[str, str]]:
        """
        Извлекает пары (search_block, replace_block) из формата:
        <<<<<<< SEARCH
        исходный код
        =======
        заменяющий код
        >>>>>>> REPLACE
        или сокращенных <<<SEARCH ... === ... >>>
        """
        if not diff_text:
            return []

        results = []
        for match in cls.DIFF_PATTERN.finditer(diff_text):
            search_b = match.group('search')
            replace_b = match.group('replace')
            results.append((search_b, replace_b))

        return results

    @classmethod
    def apply_patch(
        cls,
        content: str,
        search_block: str,
        replace_block: str,
        fuzzy_threshold: float = 0.88
    ) -> PatchResult:
        """
        Детерминированное применение патча к content:
        1. Нормализация переносов (\r\n -> \n) и хвостовых пробелов.
        2. Точное совпадение (включая вариант со снятием краевых пустых строк).
        3. Детерминированный сдвиг отступов (Indent-Shift), если блок внутри класса.
        4. Полный отказ от вероятностного угадывания.
        """
        if not search_block and not replace_block:
            return PatchResult(
                success=False,
                new_content=content,
                error="Искомый блок и блок замены пустые."
            )

        has_crlf = "\r\n" in content

        def normalize(text: str) -> str:
            return text.replace("\r\n", "\n")

        norm_content = normalize(content)
        norm_search = normalize(search_block)
        norm_replace = normalize(replace_block)

        # 1. Точное совпадение
        count = norm_content.count(norm_search)
        if count == 1:
            new_content = norm_content.replace(norm_search, norm_replace, 1)
            final_content = new_content.replace("\n", "\r\n") if has_crlf else new_content
            return PatchResult(success=True, new_content=final_content, applied_count=1, is_fuzzy=False)
        elif count > 1:
            return PatchResult(success=False, new_content=content, error="Найдено более одного точного совпадения. Уточните контекст строк.")

        # 2. Точное совпадение без краевых пустых строк
        trimmed_search = norm_search.strip("\n")
        if trimmed_search and norm_content.count(trimmed_search) == 1:
            new_content = norm_content.replace(trimmed_search, norm_replace.strip("\n"), 1)
            final_content = new_content.replace("\n", "\r\n") if has_crlf else new_content
            return PatchResult(success=True, new_content=final_content, applied_count=1, is_fuzzy=False)

        # 3. Детерминированный сдвиг отступов (Indent-Shift)
        shift_res = cls._apply_indent_shift(norm_content, trimmed_search or norm_search, norm_replace.strip("\n"))
        if shift_res.success:
            final_content = shift_res.new_content.replace("\n", "\r\n") if has_crlf else shift_res.new_content
            shift_res.new_content = final_content
            return shift_res

        # 4. Нормализованное построчное сопоставление (устойчивость к кавычкам и пробелам)
        norm_res = cls._apply_normalized_match(norm_content, trimmed_search or norm_search, norm_replace.strip("\n"))
        if norm_res.success:
            final_content = norm_res.new_content.replace("\n", "\r\n") if has_crlf else norm_res.new_content
            norm_res.new_content = final_content
            return norm_res

        # 5. Fuzzy Matcher на базе SequenceMatcher (устойчивость к огрехам 7B-моделей)
        fuzzy_res = cls._apply_fuzzy_match(
            norm_content,
            trimmed_search or norm_search,
            norm_replace.strip("\n"),
            threshold=fuzzy_threshold
        )
        if fuzzy_res.success:
            final_content = fuzzy_res.new_content.replace("\n", "\r\n") if has_crlf else fuzzy_res.new_content
            fuzzy_res.new_content = final_content
            return fuzzy_res

        return PatchResult(
            success=False,
            new_content=content,
            error=shift_res.error or "Искомый блок кода не найден в файле. Сверьте строки SEARCH или перезапишите файл целиком."
        )

    @classmethod
    def _normalize_line(cls, line: str) -> str:
        """Нормализует строку кода: убирает концевые пробелы, приводит кавычки к двойным и сжимает пробелы."""
        s = line.strip()
        s = re.sub(r"['\"]", '"', s)
        s = re.sub(r'\s+', ' ', s)
        return s

    @classmethod
    def _apply_slice_replacement(
        cls,
        content_lines: List[str],
        start_idx: int,
        end_idx: int,
        replace_block: str
    ) -> str:
        """Замещает диапазон строк [start_idx:end_idx] новым кодом с адаптацией отступов оригинала."""
        orig_matched = content_lines[start_idx:end_idx]
        orig_non_empty = next((l for l in orig_matched if l.strip()), None)
        orig_indent = (len(orig_non_empty) - len(orig_non_empty.lstrip())) if orig_non_empty else 0
        orig_indent_str = orig_non_empty[:orig_indent] if orig_non_empty else ""

        rep_lines = replace_block.split("\n")
        rep_non_empty = next((r for r in rep_lines if r.strip()), None)
        rep_indent = (len(rep_non_empty) - len(rep_non_empty.lstrip())) if rep_non_empty else 0

        adjusted_replace = []
        for r in rep_lines:
            if not r.strip():
                adjusted_replace.append("")
            else:
                r_cur_indent = len(r) - len(r.lstrip())
                indent_delta = r_cur_indent - rep_indent
                if indent_delta >= 0:
                    adjusted_replace.append(orig_indent_str + (" " * indent_delta) + r.lstrip())
                else:
                    adjusted_replace.append(orig_indent_str + r.lstrip())

        new_lines = content_lines[:start_idx] + adjusted_replace + content_lines[end_idx:]
        return "\n".join(new_lines)

    @classmethod
    def _apply_normalized_match(
        cls,
        content: str,
        search_block: str,
        replace_block: str
    ) -> PatchResult:
        """Поиск с нормализацией кавычек, краевых пробелов и плотности разделителей."""
        content_lines = content.split("\n")
        s_lines = [s for s in search_block.split("\n")]
        while s_lines and not s_lines[0].strip():
            s_lines.pop(0)
        while s_lines and not s_lines[-1].strip():
            s_lines.pop()

        if not s_lines:
            return PatchResult(success=False, new_content=content, error="Блок SEARCH пуст.")

        norm_content = [cls._normalize_line(l) for l in content_lines]
        norm_search = [cls._normalize_line(l) for l in s_lines]
        n_search = len(norm_search)

        matches = []
        for i in range(len(norm_content) - n_search + 1):
            window = norm_content[i : i + n_search]
            if window == norm_search:
                matches.append(i)

        if len(matches) == 1:
            idx = matches[0]
            new_content = cls._apply_slice_replacement(content_lines, idx, idx + n_search, replace_block)
            return PatchResult(success=True, new_content=new_content, applied_count=1, is_fuzzy=True)

        return PatchResult(success=False, new_content=content, error="Точное совпадение с нормализацией не найдено.")

    @classmethod
    def _apply_fuzzy_match(
        cls,
        content: str,
        search_block: str,
        replace_block: str,
        threshold: float = 0.85
    ) -> PatchResult:
        """Нечёткий поиск скользящим окном через SequenceMatcher с адаптивным окном (+/- 2 строки)."""
        content_lines = content.split("\n")
        s_lines = [s for s in search_block.split("\n")]
        while s_lines and not s_lines[0].strip():
            s_lines.pop(0)
        while s_lines and not s_lines[-1].strip():
            s_lines.pop()

        if not s_lines or len(s_lines) < 2:
            return PatchResult(success=False, new_content=content, error="Блок SEARCH слишком мал для fuzzy matching.")

        norm_content = [cls._normalize_line(l) for l in content_lines]
        norm_search = [cls._normalize_line(l) for l in s_lines]
        search_str = "\n".join(norm_search)
        n_search = len(norm_search)

        best_ratio = 0.0
        best_span = None

        for delta in (0, -1, 1, -2, 2):
            w_size = n_search + delta
            if w_size <= 0:
                continue
            for i in range(len(norm_content) - w_size + 1):
                window_str = "\n".join(norm_content[i : i + w_size])
                ratio = difflib.SequenceMatcher(None, window_str, search_str).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_span = (i, i + w_size)

        if best_ratio >= threshold and best_span is not None:
            start_i, end_i = best_span
            new_content = cls._apply_slice_replacement(content_lines, start_i, end_i, replace_block)
            logger.info(f"🔍 [PatchApplier] Применён Fuzzy Match (схожесть: {best_ratio:.2%}) на строках {start_i+1}-{end_i}.")
            return PatchResult(success=True, new_content=new_content, applied_count=1, is_fuzzy=True)

        return PatchResult(success=False, new_content=content, error="Fuzzy matching не преодолел порог схожести.")

    @classmethod
    def _apply_indent_shift(
        cls,
        content: str,
        search_block: str,
        replace_block: str
    ) -> PatchResult:
        """
        Детерминированно сопоставляет блок с учетом постоянного сдвига отступов.
        """
        content_lines = content.split("\n")
        search_lines = [s for s in search_block.split("\n")]
        n_search = len(search_lines)

        if n_search == 0 or len(content_lines) < n_search:
            return PatchResult(success=False, new_content=content, error="Искомый блок кода не найден в файле.")

        # Базовый относительный отступ блока SEARCH
        non_empty_search = [s for s in search_lines if s.strip()]
        if not non_empty_search:
            return PatchResult(success=False, new_content=content, error="Блок SEARCH не содержит значащих строк.")

        first_search_indent = len(non_empty_search[0]) - len(non_empty_search[0].lstrip())
        rel_search = []
        for s in search_lines:
            if not s.strip():
                rel_search.append("")
            else:
                s_ind = len(s) - len(s.lstrip())
                rel_search.append(s[min(s_ind, first_search_indent):])

        matches = []
        for i in range(len(content_lines) - n_search + 1):
            window = content_lines[i : i + n_search]
            first_non_empty_w = next((w for w in window if w.strip()), None)
            if not first_non_empty_w:
                continue

            target_indent = first_non_empty_w[: len(first_non_empty_w) - len(first_non_empty_w.lstrip())]
            is_match = True

            for w_line, r_line in zip(window, rel_search):
                if not r_line:
                    if w_line.strip():
                        is_match = False
                        break
                else:
                    if w_line != (target_indent + r_line):
                        is_match = False
                        break

            if is_match:
                matches.append((i, target_indent))

        if len(matches) == 1:
            start_idx, target_indent = matches[0]
            rep_lines = replace_block.split("\n")
            first_rep_non_empty = next((r for r in rep_lines if r.strip()), None)
            first_rep_indent = (len(first_rep_non_empty) - len(first_rep_non_empty.lstrip())) if first_rep_non_empty else 0

            adjusted_replace = []
            for r in rep_lines:
                if not r.strip():
                    adjusted_replace.append("")
                else:
                    r_ind = len(r) - len(r.lstrip())
                    rel_r = r[min(r_ind, first_rep_indent):]
                    adjusted_replace.append(target_indent + rel_r)

            new_lines = content_lines[:start_idx] + adjusted_replace + content_lines[start_idx + n_search:]
            return PatchResult(success=True, new_content="\n".join(new_lines), applied_count=1, is_fuzzy=False)

        elif len(matches) > 1:
            return PatchResult(success=False, new_content=content, error="Найдено более одного совпадения с учетом отступов. Уточните контекст строк.")

        return PatchResult(success=False, new_content=content, error="Искомый блок кода не найден в файле.")

    @classmethod
    def apply_diff_text(cls, content: str, diff_text: str) -> PatchResult:
        """
        Парсит все блоки diff_text и применяет их последовательно к content.
        """
        pairs = cls.parse_diff(diff_text)
        if not pairs:
            return PatchResult(
                success=False,
                new_content=content,
                error="Не найдены валидные маркеры SEARCH/REPLACE в тексте патча."
            )

        current_content = content
        applied_total = 0
        has_fuzzy = False

        for search_b, replace_b in pairs:
            res = cls.apply_patch(current_content, search_b, replace_b)
            if not res.success:
                return PatchResult(
                    success=False,
                    new_content=content,  # откат
                    error=res.error,
                    applied_count=applied_total
                )
            current_content = res.new_content
            applied_total += res.applied_count
            if res.is_fuzzy:
                has_fuzzy = True

        return PatchResult(
            success=True,
            new_content=current_content,
            applied_count=applied_total,
            is_fuzzy=has_fuzzy
        )


class SelfHealingEngine:
    """
    Транзакционный координатор правок:
    - Перед модификацией файла создаёт snapshot (копию содержимого в памяти или временный .bak).
    - Применяет патч через PatchApplier.
    - Проводит preflight_lint. Если синтаксис сломан — делает мгновенный rollback на диске.
    - При успехе — удаляет временный бэкап и фиксирует результат.
    """

    @classmethod
    def patch_file_transactional(
        cls,
        filename: str,
        search_block: str = "",
        replace_block: str = "",
        diff_text: str = ""
    ) -> PatchResult:
        """
        Выполняет транзакционное применение патча к файлу:
        1. Чтение и валидация пути через SandboxManager.
        2. Snapshot оригинального содержимого.
        3. Применение патча (дифф-текст или search/replace блок).
        4. Pre-flight linting (если файл Python .py).
           При сбое -> немедленный rollback и возврат ошибки.
        5. Фиксация изменений на диске при успехе.
        """
        try:
            target_path = SandboxManager.validate_read_file_path(filename)
        except Exception as e:
            return PatchResult(
                success=False,
                new_content="",
                error=f"Ошибка валидации пути файла: {e}"
            )

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                original_content = f.read()
        except Exception as e:
            return PatchResult(
                success=False,
                new_content="",
                error=f"Ошибка чтения файла {filename}: {e}"
            )

        # Создаём резервный snapshot-файл .bak
        bak_path = target_path.with_suffix(target_path.suffix + ".bak")
        try:
            with open(bak_path, "w", encoding="utf-8") as f_bak:
                f_bak.write(original_content)
        except Exception as e:
            logger.warning(f"Не удалось создать .bak файл: {e}")

        temp_path = target_path.with_suffix(target_path.suffix + ".tmp")

        def rollback(err_msg: str) -> PatchResult:
            try:
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(original_content)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception as _rb_err:
                logger.critical(f"Ошибка при rollback файла {target_path}: {_rb_err}")

            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

            if bak_path.exists():
                try:
                    bak_path.unlink()
                except Exception:
                    pass

            return PatchResult(
                success=False,
                new_content=original_content,
                error=err_msg
            )

        # Применяем патч
        if diff_text and diff_text.strip():
            patch_res = PatchApplier.apply_diff_text(original_content, diff_text)
        elif search_block or replace_block:
            patch_res = PatchApplier.apply_patch(original_content, search_block, replace_block)
        else:
            return rollback("Не переданы параметры для замены (search_block / replace_block или diff_text).")

        if not patch_res.success:
            return rollback(patch_res.error)

        # Preflight linting для .py файлов
        if target_path.suffix.lower() in (".py", ".pyw"):
            from services.code_engine import ExecutionSandbox
            is_valid, lint_msg = ExecutionSandbox.preflight_lint(patch_res.new_content)
            if not is_valid:
                return rollback(f"Ошибка синтаксиса после патча ({lint_msg}). Выполнен автоматический rollback.")

        # Записываем во временный файл с принудительным сбросом буфера перед атомарным замещением
        content = patch_res.new_content
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())  # Принудительно сбрасываем данные из буфера OS на накопитель
            os.replace(temp_path, target_path)
        except Exception as e:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            return rollback(f"Ошибка сохранения изменений на диск: {e}")

        # Удаляем временный бэкап при успехе
        if bak_path.exists():
            try:
                bak_path.unlink()
            except Exception:
                pass

        return patch_res


__all__ = ["PatchResult", "PatchApplier", "SelfHealingEngine"]
