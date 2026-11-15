import os
import time
import uuid
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("NeonRoute.Utils")

def atomic_write_json(target_path: Path, data: Any, retries: int = 5, encoding: Optional[str] = None) -> bool:
    """Атомарная запись JSON с уникальным .tmp, fsync и защитой от WinError 32."""
    target_path = Path(target_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.{uuid.uuid4().hex[:8]}.tmp")
    file_encoding = encoding or ("utf-8-sig" if target_path.name == "art_director_models.json" else "utf-8")
    try:
        with open(temp_path, "w", encoding=file_encoding) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        for attempt in range(retries):
            try:
                os.replace(temp_path, target_path)
                return True
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.04 * (2 ** attempt))
    except Exception as e:
        logger.error(f"Atomic write failed for {target_path}: {e}")
        return False
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return False


def get_python_cli_executable() -> str:
    """
    Возвращает путь к консольному исполняемому файлу python.exe.
    Если приложение запущено через pythonw.exe, находит соседний python.exe,
    гарантируя корректное выполнение CLI-подпроцессов (pip, pytest, py_compile, скриптов).
    """
    import sys
    exe = sys.executable
    if not exe:
        return "python"
    p = Path(exe)
    if p.name.lower() == "pythonw.exe":
        candidate = p.with_name("python.exe")
        if candidate.is_file():
            return str(candidate.resolve())
    return str(p.resolve())

