"""
engineer_review.py — Ветка статической инспекции, анализа Git Diff и чекпоинтов NeonRoute.
"""
import re
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("NeonRoute.ReviewPipeline")

class ReviewPipeline:
    """Исполнитель режима review / inspection без генерации исполняемого кода."""

    @staticmethod
    def run(
        plan: Any,
        task_prompt: str,
        ws_dir: Path,
        client: Any,
        target_model: Optional[str],
        safe_send_message: Callable[[str, str], None],
        context_length: int = 28672,
        gpu_ratio: float = 0.90
    ) -> Dict[str, Any]:
        from services.git_sandbox import GitSandbox

        target_fn = getattr(plan, "target_artifact", None) or "main.py"
        target_file = ws_dir / target_fn

        # 0. Перехват прямых запросов на создание Git-коммита
        task_lower = (task_prompt or "").lower()
        commit_keywords = [
            "коммит", "комит", "закоммит", "закомит", "commit",
            "зафиксируй в гит", "сохрани в гит", "чекпоинт", "сделай гит"
        ]
        if any(k in task_lower for k in commit_keywords):
            logger.info(f"⚡ [ReviewPipeline] Запрос на создание Git-коммита: '{task_prompt}'")

            m_msg = re.search(r'''['"]([^'"]+)['"]''', task_prompt)
            if m_msg:
                commit_msg = m_msg.group(1).strip()
            else:
                commit_msg = getattr(plan, "plan_content", "") or "Manual checkpoint via chat"
                for prefix in ("Сделать коммит к игре", "Сделать коммит текущего файла в Git", "Сделать коммит"):
                    if commit_msg.startswith(prefix):
                        commit_msg = commit_msg.replace(prefix, "").strip(": ").strip()
                if not commit_msg or len(commit_msg) < 3:
                    commit_msg = f"Checkpoint: update {target_fn}"

            ok = GitSandbox.checkpoint(ws_dir, commit_msg)
            commit_hash = "HEAD"
            try:
                res_hash = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(ws_dir),
                    capture_output=True,
                    text=True,
                    encoding="utf-8"
                )
                if res_hash.returncode == 0 and res_hash.stdout.strip():
                    commit_hash = res_hash.stdout.strip()
            except Exception:
                pass

            if ok:
                report_msg = (
                    f"✅ **Git-чекпоинт успешно зафиксирован**\n\n"
                    f"- **Комментарий:** `{commit_msg}`\n"
                    f"- **Коммит:** `{commit_hash}`\n"
                    f"- **Песочница:** `{ws_dir.name}`\n\n"
                    f"Все файлы рабочей области сохранены в истории репозитория."
                )
            else:
                report_msg = (
                    f"ℹ️ **Git**: Рабочая область чиста. Нет новых изменений для коммита "
                    f"(текущий коммит: `{commit_hash}`)."
                )

            safe_send_message("assistant", report_msg)
            return {
                "success": True,
                "target_artifact": target_fn,
                "verification_passed": True,
                "turns": 1,
                "message": report_msg
            }

        # 1. Получаем Git Diff текущей рабочей области
        git_diff_text = ""
        try:
            res_diff = subprocess.run(
                ["git", "diff", "HEAD", "--", target_fn],
                cwd=str(ws_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )
            git_diff_text = res_diff.stdout.strip()
            if not git_diff_text:
                res_last = subprocess.run(
                    ["git", "diff", "HEAD~1", "HEAD", "--", target_fn],
                    cwd=str(ws_dir),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore"
                )
                git_diff_text = res_last.stdout.strip()
        except Exception as e:
            git_diff_text = f"Ошибка получения git diff: {e}"

        # 2. Читаем текущее содержимое файла
        file_code = ""
        if target_file.is_file():
            try:
                file_code = target_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                file_code = ""

        # 3. Формируем контекст для аналитического ответа
        review_system_prompt = (
            "### РОЛЬ И ЦЕЛЬ\n"
            "Ты — старший инженер-наставник и системный аналитик NeonRoute.\n"
            "Твоя задача — внимательно изучить запрос пользователя, текущее состояние файла и данные Git Diff, "
            "после чего дать точный, профессиональный и исчерпывающий ответ на русском языке.\n\n"
            "### ПРАВИЛА АНАЛИЗА\n"
            "1. Ссылайся на конкретные функции, методы и строки кода при анализе.\n"
            "2. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать служебные теги <<TOOL:write_file>> или <<TOOL:run_script>>. "
            "Код проекта менять НЕ требуется. Отвечай прямо по существу вопроса."
        )

        diff_block = f"```diff\n{git_diff_text}\n```" if git_diff_text else "Текущих изменений в Git не обнаружено (рабочая область чиста)."
        code_block = f"```python\n{file_code[:12000]}\n```" if file_code else "Файл не найден на диске."

        review_user_prompt = (
            "### ЗАПРОС НА АУДИТ\n"
            f"Вопрос пользователя: «{task_prompt}»\n"
            f"Целевой файл: {target_fn}\n\n"
            f"### ДАННЫЕ GIT DIFF\n{diff_block}\n\n"
            f"### ТЕКУЩИЙ КОД ФАЙЛА (фрагмент)\n{code_block}\n\n"
            f"Проанализируй эти данные со ссылками на конкретные функции/строки и ответь пользователю на его вопрос."
        )

        try:
            review_messages = [
                {"role": "system", "content": review_system_prompt},
                {"role": "user", "content": review_user_prompt}
            ]
            ans_resp = client.chat_completion(
                messages=review_messages,
                model=target_model,
                temperature=0.3,
                max_tokens=1500,
                context_length=context_length,
                gpu_ratio=gpu_ratio,
                stream=False,
                emit_signal=False
            )
            ans_text = str(ans_resp or "").strip()
            safe_send_message("assistant", ans_text)
            return {
                "success": True,
                "target_artifact": target_fn,
                "verification_passed": True,
                "turns": 1,
                "message": "Инспекция завершена успешно."
            }
        except Exception as e:
            logger.error(f"Review cycle error: {e}")
            safe_send_message("assistant", f"⚠️ Произошла ошибка при анализе: {e}")
            return {"success": False, "error": str(e)}
