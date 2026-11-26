import os
import sys
import time
import gc
import shutil
import zipfile
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import logging

logger = logging.getLogger("NeonRoute.GitSandbox")

class GitSandboxManager:
    """
    Отказоустойчивый локальный менеджер версионирования песочницы.
    Оснащён защитой от блокировок Windows, деградацией на ZIP и безопасным baseline.
    """

    @staticmethod
    def get_git_executable() -> Optional[str]:
        bundled_candidates = [
            Path("tools/git/cmd/git.exe").resolve(),
            Path("tools/git/bin/git.exe").resolve(),
            Path("tools/git/git.exe").resolve(),
        ]
        for candidate in bundled_candidates:
            if candidate.is_file():
                return str(candidate)
        return shutil.which("git")

    @classmethod
    def is_git_available(cls) -> bool:
        return cls.get_git_executable() is not None

    @classmethod
    def _get_safe_env(cls, ws_dir: Optional[Path] = None) -> dict:
        env = os.environ.copy()
        # Предотвращение любых блокирующих интерактивных запросов Git
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_PAGER"] = "cat"
        env["GIT_ASKPASS"] = "echo"
        if ws_dir is not None:
            # Блокируем поиск .git вверх по дереву папок (защита от коммитов в родительский репозиторий)
            env["GIT_CEILING_DIRECTORIES"] = str(Path(ws_dir).resolve().parent)
        return env

    @classmethod
    def _cleanup_stale_lock(cls, ws_dir: Path):
        lock_file = ws_dir / ".git" / "index.lock"
        if lock_file.exists():
            try:
                gc.collect()
                time.sleep(0.05)
                lock_file.unlink()
                logger.info(f"[GitSandbox] Удалён зависший lock-файл: {lock_file}")
            except Exception as e:
                logger.warning(f"[GitSandbox] Не удалось удалить index.lock: {e}")

    @classmethod
    def _run_git(cls, ws_dir: Path, args: list[str], timeout: int = 15, retries: int = 2) -> Tuple[bool, str]:
        git_bin = cls.get_git_executable()
        if not git_bin:
            return False, "Git executable not found"

        cls._cleanup_stale_lock(ws_dir)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [git_bin] + args

        for attempt in range(retries + 1):
            try:
                res = subprocess.run(
                    cmd,
                    cwd=str(ws_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=cls._get_safe_env(ws_dir),
                    creationflags=flags,
                    timeout=timeout
                )
                if res.returncode == 0:
                    return True, res.stdout.strip()

                # Если ошибка связана с блокировкой файлов Windows, делаем короткую паузу
                err_text = res.stdout or ""
                if "unlink of file" in err_text or "Permission denied" in err_text or "index.lock" in err_text:
                    if attempt < retries:
                        gc.collect()
                        time.sleep(0.15 * (attempt + 1))
                        cls._cleanup_stale_lock(ws_dir)
                        continue

                return False, err_text.strip()
            except subprocess.TimeoutExpired:
                return False, f"Git command timed out: {' '.join(args)}"
            except Exception as e:
                return False, f"Git invocation error: {e}"
        return False, "Execution failed after retries"

    @classmethod
    def init_repo(cls, ws_dir: Path) -> bool:
        ws_dir = Path(ws_dir).resolve()
        ws_dir.mkdir(parents=True, exist_ok=True)

        # Строгий .gitignore: защищает от раздувания и не даёт git clean удалить логи ошибок
        gitignore = ws_dir / ".gitignore"
        ignore_rules = [
            "__pycache__/",
            "*.py[cod]",
            ".pytest_cache/",
            "*.tmp",
            "*.log",
            ".venv/",
            ".backups/",
            "backups/",
            ".neonroute_last_failure.json"
        ]
        try:
            if not gitignore.exists():
                gitignore.write_text("\n".join(ignore_rules) + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning(f"[GitSandbox] Ошибка записи .gitignore: {e}")

        if not cls.is_git_available():
            logger.info("[GitSandbox] Git не обнаружен. Песочница переключена в режим ZIP-снапшотов.")
            (ws_dir / ".backups").mkdir(exist_ok=True)
            return True

        git_dir = ws_dir / ".git"
        is_valid_git = (
            git_dir.is_dir()
            and (git_dir / "HEAD").is_file()
            and (git_dir / "objects").is_dir()
            and (git_dir / "refs").is_dir()
        )
        if git_dir.exists() and not is_valid_git:
            logger.warning(f"[GitSandbox] Обнаружен повреждённый каталог .git в {ws_dir}, пересоздаём репозиторий.")
            shutil.rmtree(git_dir, ignore_errors=True)
            is_valid_git = (git_dir / "HEAD").is_file()

        if not is_valid_git:
            ok, out = cls._run_git(ws_dir, ["init", "-b", "main"])
            if not ok:
                cls._run_git(ws_dir, ["init"])

            cls._run_git(ws_dir, ["config", "user.name", "NeonRoute Engineer"])
            cls._run_git(ws_dir, ["config", "user.email", "engineer@neonroute.local"])
            cls._run_git(ws_dir, ["config", "core.longpaths", "true"])
            cls._run_git(ws_dir, ["add", "-A"])
            cls._run_git(ws_dir, ["commit", "-m", "Initial commit: workspace baseline", "--allow-empty"])
            logger.info("[GitSandbox] Локальный Git-репозиторий успешно инициализирован.")
        return True

    @classmethod
    def ensure_baseline(cls, ws_dir: Path, task_title: str):
        """Фиксирует текущее состояние файлов пользователя до того, как Инженер внесёт правки."""
        ws_dir = Path(ws_dir).resolve()
        cls.init_repo(ws_dir)
        if cls.is_git_available():
            cls._run_git(ws_dir, ["add", "-A"])
            cls._run_git(ws_dir, ["commit", "-m", f"Baseline before task: {task_title[:40]}"])
        else:
            cls._zip_checkpoint(ws_dir, "baseline")

    @classmethod
    def checkpoint(cls, ws_dir: Path, message: str) -> bool:
        ws_dir = Path(ws_dir).resolve()
        if not cls.is_git_available():
            return cls._zip_checkpoint(ws_dir, message)

        cls._run_git(ws_dir, ["add", "-A"])
        ok, out = cls._run_git(ws_dir, ["commit", "-m", message])
        if not ok and "nothing to commit" not in out.lower():
            logger.warning(f"[GitSandbox] Чекпоинт пропущен или сбой: {out}")
            return False
        return True

    @classmethod
    def create_checkpoint(cls, ws_dir: Path, message: str) -> bool:
        """Алиас для обратной совместимости."""
        return cls.checkpoint(ws_dir, message)

    @classmethod
    def rollback(cls, ws_dir: Path) -> bool:
        ws_dir = Path(ws_dir).resolve()
        if not cls.is_git_available():
            return cls._zip_rollback(ws_dir)

        # Освобождение дескрипторов Windows перед удалением/перезаписью
        gc.collect()
        time.sleep(0.1)
        cls._cleanup_stale_lock(ws_dir)

        ok_reset, out_reset = cls._run_git(ws_dir, ["reset", "--hard", "HEAD"])
        ok_clean, out_clean = cls._run_git(ws_dir, ["clean", "-fd"])
        success = ok_reset and ok_clean
        if success:
            logger.info("[GitSandbox] Рабочая область атомарно возвращена к последнему стабильному чекпоинту.")
        else:
            logger.warning(f"[GitSandbox] Предупреждение при откате: {out_reset} | {out_clean}")
        return success

    @classmethod
    def get_working_diff(cls, ws_dir: Path, max_lines: int = 35) -> str:
        """
        Возвращает компактный git diff текущих изменений рабочей директории
        относительно последнего стабильного коммита (HEAD).
        Учитывает как модифицированные, так и новые (untracked) файлы.
        """
        ws_dir = Path(ws_dir).resolve()
        if not cls.is_git_available() or not (ws_dir / ".git" / "HEAD").is_file():
            return ""

        try:
            # 1. Помечаем новые файлы (intent-to-add), чтобы они попали в diff
            cls._run_git(ws_dir, ["add", "-N", "."])

            # 2. Снимаем diff относительно HEAD (или базовый diff, если коммитов мало)
            ok, out = cls._run_git(ws_dir, ["diff", "HEAD", "--no-color"])
            if not ok or not out.strip():
                ok, out = cls._run_git(ws_dir, ["diff", "--no-color"])

            if not ok or not out.strip():
                return ""

            raw_lines = out.splitlines()
            clean_lines = []

            # 3. Фильтруем технический шум git (хеши индексов)
            for line in raw_lines:
                if line.startswith("index ") or line.startswith("diff --git"):
                    continue
                clean_lines.append(line)

            # 4. Ограничиваем объём диффа, чтобы не перегружать контекст 7B-модели
            if len(clean_lines) > max_lines:
                truncated = clean_lines[:max_lines]
                truncated.append(f"\n[...остальные строки diff скрыты ({len(clean_lines) - max_lines} строк)...]")
                return "\n".join(truncated)

            return "\n".join(clean_lines)
        except Exception as e:
            logger.warning(f"[GitSandbox] Не удалось извлечь git diff: {e}")
            return ""

    @classmethod
    def create_backup_bundle(cls, ws_dir: Path, label: str = "manual") -> Optional[Path]:
        ws_dir = Path(ws_dir).resolve()
        backups_dir = ws_dir.parent / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in label)[:30]

        if cls.is_git_available():
            cls.checkpoint(ws_dir, f"Pre-backup checkpoint: {label}")
            bundle_path = backups_dir / f"backup_{ts}_{safe_label}.bundle"
            ok, out = cls._run_git(ws_dir, ["bundle", "create", str(bundle_path), "--all"])
            if ok and bundle_path.exists():
                logger.info(f"[GitSandbox] Офлайн-бэкап создан: {bundle_path}")
                return bundle_path
            logger.warning(f"[GitSandbox] Сбой создания bundle: {out}. Запуск fallback на ZIP...")

        zip_path = backups_dir / f"backup_{ts}_{safe_label}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(ws_dir):
                    if ".git" in root or ".backups" in root:
                        continue
                    for file in files:
                        p = Path(root) / file
                        zf.write(p, p.relative_to(ws_dir))
            logger.info(f"[GitSandbox] ZIP-бэкап создан: {zip_path}")
            return zip_path
        except Exception as e:
            logger.error(f"[GitSandbox] Ошибка создания ZIP-бэкапа: {e}")
            return None

    @classmethod
    def record_failure(cls, ws_dir: Path, task_title: str, turn: int, error_msg: str, recipe: str = ""):
        import json
        ws_dir = Path(ws_dir).resolve()
        info = {
            "timestamp": datetime.now().isoformat(),
            "task": task_title,
            "failed_turn": turn,
            "error": str(error_msg)[:1000],
            "recipe": str(recipe)[:500]
        }
        try:
            (ws_dir / ".neonroute_last_failure.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[GitSandbox] Не удалось сохранить журнал сбоя: {e}")

    @classmethod
    def pop_last_failure(cls, ws_dir: Path) -> Optional[dict]:
        import json
        ws_dir = Path(ws_dir).resolve()
        fail_file = ws_dir / ".neonroute_last_failure.json"
        if fail_file.is_file():
            try:
                data = json.loads(fail_file.read_text(encoding="utf-8"))
                fail_file.unlink(missing_ok=True)
                return data
            except Exception:
                pass
        return None

    # --- ZIP Fallback Helpers ---
    @classmethod
    def _zip_checkpoint(cls, ws_dir: Path, message: str) -> bool:
        snap_dir = ws_dir / ".backups"
        snap_dir.mkdir(exist_ok=True)
        latest_zip = snap_dir / "latest_checkpoint.zip"
        try:
            with zipfile.ZipFile(latest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(ws_dir):
                    if ".backups" in root or ".git" in root:
                        continue
                    for file in files:
                        p = Path(root) / file
                        zf.write(p, p.relative_to(ws_dir))
            return True
        except Exception as e:
            logger.error(f"[GitSandbox] Ошибка ZIP-чекпоинта: {e}")
            return False

    @classmethod
    def _zip_rollback(cls, ws_dir: Path) -> bool:
        latest_zip = ws_dir / ".backups" / "latest_checkpoint.zip"
        if not latest_zip.is_file():
            return False
        try:
            for item in ws_dir.iterdir():
                if item.name in (".backups", ".git", ".neonroute_last_failure.json"):
                    continue
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            with zipfile.ZipFile(latest_zip, "r") as zf:
                zf.extractall(ws_dir)
            return True
        except Exception as e:
            logger.error(f"[GitSandbox] Ошибка ZIP-отката: {e}")
            return False
