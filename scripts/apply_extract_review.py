import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 1. Создаем services/engineer_review.py
review_file = PROJECT_ROOT / "services" / "engineer_review.py"
review_code = """\"\"\"
engineer_review.py — Ветка статической инспекции, анализа Git Diff и чекпоинтов NeonRoute.
\"\"\"
import re
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("NeonRoute.ReviewPipeline")

class ReviewPipeline:
    \"\"\"Исполнитель режима review / inspection без генерации исполняемого кода.\"\"\"

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

            m_msg = re.search(r"['\"]([^'\"]+)['\"]", task_prompt)
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
                    f"✅ **Git-чекпоинт успешно зафиксирован**\\n\\n"
                    f"- **Комментарий:** `{commit_msg}`\\n"
                    f"- **Коммит:** `{commit_hash}`\\n"
                    f"- **Песочница:** `{ws_dir.name}`\\n\\n"
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
            "Ты — старший инженер-наставник и системный аналитик NeonRoute. "
            "Твоя задача — внимательно изучить запрос пользователя, текущее состояние файла и данные Git Diff, "
            "после чего дать точный, профессиональный и исчерпывающий ответ на русском языке. "
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать служебные теги <<TOOL:write_file>> или <<TOOL:run_script>>. "
            "Код проекта менять НЕ требуется. Отвечай прямо по существу вопроса."
        )

        diff_block = f"```diff\\n{git_diff_text}\\n```" if git_diff_text else "Текущих изменений в Git не обнаружено (рабочая область чиста)."
        code_block = f"```python\\n{file_code[:3500]}\\n```" if file_code else "Файл не найден на диске."

        review_user_prompt = (
            f"Вопрос пользователя: «{task_prompt}»\\n\\n"
            f"Целевой файл: {target_fn}\\n\\n"
            f"--- ДАННЫЕ GIT DIFF ---\\n{diff_block}\\n\\n"
            f"--- ТЕКУЩИЙ КОД ФАЙЛА (фрагмент) ---\\n{code_block}\\n\\n"
            f"Проанализируй эти данные и ответь пользователю на его вопрос."
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
"""

review_file.write_text(review_code.strip() + "\n", encoding="utf-8")
print(f"[OK] Создан модуль: {review_file.relative_to(PROJECT_ROOT)}")

# 2. Интегрируем ReviewPipeline в services/subagent_engineer.py
sub_file = PROJECT_ROOT / "services" / "subagent_engineer.py"
sub_lines = sub_file.read_text(encoding="utf-8").splitlines()

# Добавляем импорт
import_added = False
for idx, l in enumerate(sub_lines):
    if l.startswith("from services.engineer_stream import"):
        sub_lines.insert(idx + 1, "from services.engineer_review import ReviewPipeline")
        import_added = True
        break

if not import_added:
    print("[FAIL] Не удалось найти место для импорта ReviewPipeline!")
    sys.exit(1)

# Находим блок `if plan.intent == "review":` и заменяем его
review_start = None
review_end = None
for idx, l in enumerate(sub_lines):
    if l.strip().startswith("if plan.intent == \"review\":"):
        review_start = idx
    if review_start is not None and idx > review_start:
        if l.strip().startswith("# === ДВУХФАЗНЫЙ CODE-FIRST TDD ПАЙПЛАЙН"):
            review_end = idx
            break

if review_start is not None and review_end is not None:
    indent = "        "
    delegation_block = [
        f"{indent}# === ВЕТКА ИНСПЕКЦИИ / REVIEW / GIT (Делегировано в ReviewPipeline) ===",
        f"{indent}if plan.intent == \"review\":",
        f"{indent}    return ReviewPipeline.run(",
        f"{indent}        plan=plan,",
        f"{indent}        task_prompt=task_prompt,",
        f"{indent}        ws_dir=ws_dir,",
        f"{indent}        client=client,",
        f"{indent}        target_model=target_model,",
        f"{indent}        safe_send_message=_safe_send_message,",
        f"{indent}        context_length=SubagentEngineer.ENGINEER_CONTEXT_LENGTH,",
        f"{indent}        gpu_ratio=SubagentEngineer.ENGINEER_GPU_RATIO",
        f"{indent}    )",
        ""
    ]
    sub_lines = sub_lines[:review_start] + delegation_block + sub_lines[review_end:]
    sub_file.write_text("\n".join(sub_lines) + "\n", encoding="utf-8")
    print(f"[OK] services/subagent_engineer.py обновлен: вырезано {review_end - review_start} строк review-ветки.")
else:
    print("[FAIL] Не удалось найти границы review-блока в subagent_engineer.py!")
    sys.exit(1)

# 3. Запуск смок-тестов
print("\n>>> Запуск смок-тестов...")
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_subagent_engineer.py", "-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res.returncode)

print("[PASS] Все смок-тесты успешно пройдены!")
