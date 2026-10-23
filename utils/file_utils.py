import os
import time
import uuid
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("NeonRoute.Utils")

def atomic_write_json(target_path: Path, data: Any, retries: int = 5) -> bool:
    """Атомарная запись JSON с уникальным .tmp, fsync и защитой от WinError 32."""
    target_path = Path(target_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
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
