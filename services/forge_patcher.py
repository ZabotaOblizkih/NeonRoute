"""
services/forge_patcher.py
=========================
Сервис инспекции, резервного копирования и применения патчей совместимости
к кодовой базе Stable Diffusion WebUI Forge (lllyasviel).

Обеспечивает:
- Проверку совместимости версии Forge (PATCHED / NEEDS_PATCH / INCOMPATIBLE / NOT_FOUND).
- Сохранение нетронутых оригиналов файлов в <forge_webui>/.neonroute_backup/original/
  перед первым применением патчей (без перезаписи существующего бэкапа).
- Идемпотентное применение 4 ключевых правок:
  1. Добавление эндпоинта /sdapi/v1/free-vram в modules/api/api.py.
  2. Вызов refresh_model_loading_parameters() при смене модели через /sdapi/v1/options.
  3. Дефолтное значение config=None в SDModelItem и get_sd_models() (modules/api/models.py).
  4. Реальная выгрузка весов unload_model_weights() через backend.memory_management.
- Полный откат к заводскому состоянию через restore_backup().
"""

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import textwrap
import time
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger("NeonRoute.ForgePatcher")


def _get_forge_git_head(webui_dir: Optional[Path]) -> Optional[str]:
    """
    Возвращает текущий commit hash (HEAD) репозитория Forge, если webui_dir находится в git.
    Безопасен к отсутствию git в PATH или отсутствию каталога .git.
    """
    if not webui_dir:
        return None
    try:
        w_path = Path(webui_dir).resolve()
        git_dir = w_path / ".git"
        if not git_dir.exists() and (w_path.parent / ".git").exists():
            git_dir = w_path.parent / ".git"
            w_path = w_path.parent
        if not git_dir.exists():
            return None
        proc = subprocess.run(
            ["git", "-C", str(w_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
            if commit and len(commit) >= 7:
                return commit
    except Exception:
        pass
    return None


def _calculate_file_sha256(file_path: Path) -> Optional[str]:
    """Вычисляет контрольную сумму sha256 файла."""
    if not file_path:
        return None
    try:
        p = Path(file_path)
        if not p.is_file():
            return None
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        return None


class ForgePatchStatus(str, Enum):
    PATCHED = "PATCHED"
    NEEDS_PATCH = "NEEDS_PATCH"
    INCOMPATIBLE = "INCOMPATIBLE"
    NOT_FOUND = "NOT_FOUND"


# =========================================================================
# Шаблоны и маркеры файлов
# =========================================================================

# 1. modules/api/api.py
API_ROUTE_UNLOAD = 'self.add_api_route("/sdapi/v1/unload-checkpoint", self.unloadapi, methods=["POST"])'
API_ROUTE_FREE_VRAM = 'self.add_api_route("/sdapi/v1/free-vram", self.free_vram_api, methods=["POST"])'

API_FREE_VRAM_METHOD = '''    def free_vram_api(self, req: dict = None):
        import gc
        from backend import memory_management
        if isinstance(req, dict):
            if "always_offload" in req and req["always_offload"] is not None:
                memory_management.ALWAYS_VRAM_OFFLOAD = bool(req["always_offload"])
            if "inference_memory_mb" in req and req["inference_memory_mb"] is not None:
                memory_management.current_inference_memory = int(req["inference_memory_mb"]) * 1024 * 1024
            if req.get("unload_models", True):
                memory_management.unload_all_models()
        else:
            memory_management.unload_all_models()
        memory_management.soft_empty_cache()
        gc.collect()
        return {"status": "ok"}
'''

API_REFRESH_BLOCK = '''
        if checkpoint_name is not None or "sd_vae" in req:
            try:
                from modules_forge.main_entry import refresh_model_loading_parameters
                refresh_model_loading_parameters()
            except Exception as e:
                print(f"[Forge API] Failed to refresh model parameters: {e}")
'''

API_DELEGATED_REFRESH_BLOCK = '''
        checkpoint_name = req.get("sd_model_checkpoint", None) if isinstance(req, dict) else getattr(req, "sd_model_checkpoint", None)
        if checkpoint_name is None:
            try:
                from modules import shared
                checkpoint_name = getattr(shared.opts, "sd_model_checkpoint", None)
            except Exception:
                checkpoint_name = None
        has_vae = ("sd_vae" in req) if isinstance(req, dict) else hasattr(req, "sd_vae")
        if checkpoint_name is not None or has_vae:
            try:
                from modules_forge.main_entry import refresh_model_loading_parameters
                refresh_model_loading_parameters()
            except Exception as e:
                print(f"[Forge API] Failed to refresh model parameters: {e}")
'''

# 2. modules/api/models.py
MODELS_STOCK_CONFIG = 'config: Optional[str] = Field(title="Config file")'
MODELS_PATCHED_CONFIG = 'config: Optional[str] = Field(default=None, title="Config file")'

# 3. modules/sd_models.py
SD_UNLOAD_DEF = 'def unload_model_weights(sd_model=None, info=None):'
SD_UNLOAD_PATTERN = re.compile(
    r'(def unload_model_weights\(sd_model=None,\s*info=None\):\s*\n)'
    r'(?:[ \t]+pass'
    r'|[ \t]+(?:from\s+[^\n]+\n\s*)*memory_management\.unload_all_models\(\)(?:\s*\n[ \t]*return)?'
    r'|[ \t]+from backend import memory_management[\s\S]*?gc\.collect\(\))',
    re.MULTILINE
)
SD_PATCHED_UNLOAD_BODY = '''    from backend import memory_management
    from modules.sd_models import model_data
    if hasattr(model_data, 'sd_model'):
        model_data.sd_model = None
        model_data.forge_hash = ''
    memory_management.unload_all_models()
    memory_management.soft_empty_cache()
    gc.collect()'''

# 4. Фиксация версий критических библиотек Forge
PINNED_FORGE_LIBRARIES: Dict[str, str] = {
    "fastapi": "0.104.1",
    "starlette": "0.27.0",
    "pydantic": "2.8.2",
    "pydantic-core": "2.20.1",
    "gradio": "4.40.0",
    "gradio-client": "1.2.0",
    "anyio": "3.7.1",
    "typing-extensions": "4.16.0",
}
CONSTRAINTS_FILENAME = "neonroute_constraints.txt"


class ForgePatcher:
    """Сервис проверки и применения патчей совместимости NeonRoute к Forge."""

    @staticmethod
    def find_forge_webui_dir(root_path: Optional[Path | str]) -> Optional[Path]:
        """
        Ищет рабочую директорию webui внутри корня Forge:
        1. <root>/webui_forge_cu121_torch231/webui
        2. <root>/webui
        3. <root> (если modules/api/api.py находится прямо в корне)
        4. Любая подпапка webui*/webui
        """
        if not root_path:
            logger.debug("[ForgePatcher] Каталог webui не обнаружен по пути: None")
            return None
        try:
            root = Path(root_path).resolve()
            if not root.is_dir():
                logger.debug(f"[ForgePatcher] Каталог webui не обнаружен по пути: {root_path}")
                return None

            candidates = [
                root / "webui_forge_cu121_torch231" / "webui",
                root / "webui",
                root,
            ]
            for c in candidates:
                if (c / "modules" / "api" / "api.py").is_file():
                    logger.debug(f"[ForgePatcher] Найден каталог webui: {c}")
                    return c

            # Поиск по шаблону
            for sub in root.glob("webui*/webui"):
                if sub.is_dir() and (sub / "modules" / "api" / "api.py").is_file():
                    logger.debug(f"[ForgePatcher] Найден каталог webui: {sub}")
                    return sub
        except Exception as e:
            logger.debug(f"[ForgePatcher] Ошибка поиска каталога webui в {root_path}: {e}")
            pass
        logger.debug(f"[ForgePatcher] Каталог webui не обнаружен по пути: {root_path}")
        return None

    @classmethod
    def _insert_refresh_in_set_config(cls, api_text: str, refresh_block: Optional[str] = None) -> Optional[str]:
        """
        Устойчиво вставляет refresh_block внутрь метода set_config.

        Поддерживает два сценария структуры set_config:
        1. Прямое сохранение опций: наличие shared.opts.save(...). Блок вставляется перед ближайшим return.
        2. Делегирование: вызов set_config(req) / modules.sysinfo.set_config(req).
           В этом сценарии локальная переменная checkpoint_name может отсутствовать,
           поэтому вставляется безопасный API_DELEGATED_REFRESH_BLOCK:
           - перед return (если return есть после вызова делегата), либо
           - в конец тела метода set_config (с отступом тела метода).

        Возвращает модифицированный api_text или None, если метод или якорные точки не найдены.
        """
        # a) Идемпотентность: если маркер уже присутствует
        if "refresh_model_loading_parameters" in api_text:
            return api_text

        # b) Поиск определения def set_config
        lines = api_text.splitlines(keepends=True)
        def_line_idx = None
        base_indent_len = 0
        base_indent_str = ""

        for idx, line in enumerate(lines):
            match = re.match(r'^([ \t]*)(?:async\s+)?def\s+set_config\b', line)
            if match:
                def_line_idx = idx
                base_indent_str = match.group(1)
                base_indent_len = len(base_indent_str.expandtabs(4))
                break

        if def_line_idx is None:
            return None

        # Определение границ метода: до следующего def/class на том же или меньшем уровне отступа
        end_line_idx = len(lines)
        for idx in range(def_line_idx + 1, len(lines)):
            line = lines[idx]
            match = re.match(r'^([ \t]*)(?:def|class|async\s+def)\b', line)
            if match:
                indent_len = len(match.group(1).expandtabs(4))
                if indent_len <= base_indent_len:
                    end_line_idx = idx
                    break

        # c) Внутри тела set_config проверяем сценарий 1: поиск shared.opts.save(...)
        save_line_idx = None
        for idx in range(def_line_idx + 1, end_line_idx):
            if "shared.opts.save(" in lines[idx]:
                save_line_idx = idx
                break

        if save_line_idx is not None:
            # Сценарий 1: shared.opts.save найден.
            # От строки shared.opts.save(...) ищем ближайший return на том же или более глубоком уровне отступа
            return_line_idx = None
            return_indent_str = None

            for idx in range(save_line_idx, end_line_idx):
                line = lines[idx]
                match = re.match(r'^([ \t]*)return(?:\s.*)?$', line)
                if match:
                    indent_str = match.group(1)
                    indent_len = len(indent_str.expandtabs(4))
                    if indent_len > base_indent_len:
                        return_line_idx = idx
                        return_indent_str = indent_str
                        break

            if return_line_idx is None or return_indent_str is None:
                return None

            block_to_use = refresh_block if refresh_block is not None else API_REFRESH_BLOCK
            newline = "\r\n" if "\r\n" in lines[return_line_idx] else "\n"
            clean_block = textwrap.dedent(block_to_use).strip()
            block_lines = [
                (f"{return_indent_str}{b_line}{newline}" if b_line.strip() else newline)
                for b_line in clean_block.splitlines()
            ]

            insert_pieces = []
            if return_line_idx > 0 and lines[return_line_idx - 1].strip():
                insert_pieces.append(newline)
            insert_pieces.extend(block_lines)
            insert_pieces.append(newline)

            result_lines = lines[:return_line_idx] + insert_pieces + lines[return_line_idx:]
            return "".join(result_lines)

        # d) Сценарий 2: Делегирование в set_config(req) / sysinfo.set_config(req)
        delegate_line_idx = None
        for idx in range(def_line_idx + 1, end_line_idx):
            line = lines[idx]
            if re.search(r'\b(?:sysinfo\.)?set_config\s*\(\s*req\b', line):
                delegate_line_idx = idx
                break

        if delegate_line_idx is None:
            return None

        # В сценарии делегирования всегда используем безопасный API_DELEGATED_REFRESH_BLOCK
        block_to_use = API_DELEGATED_REFRESH_BLOCK
        delegate_indent_str = re.match(r'^([ \t]*)', lines[delegate_line_idx]).group(1) or (base_indent_str + "    ")

        # Ищем return после вызова делегата
        return_line_idx = None
        return_indent_str = None
        for idx in range(delegate_line_idx + 1, end_line_idx):
            line = lines[idx]
            match = re.match(r'^([ \t]*)return(?:\s.*)?$', line)
            if match:
                indent_str = match.group(1)
                indent_len = len(indent_str.expandtabs(4))
                if indent_len > base_indent_len:
                    return_line_idx = idx
                    return_indent_str = indent_str
                    break

        newline = "\r\n" if "\r\n" in lines[delegate_line_idx] else "\n"
        clean_block = textwrap.dedent(block_to_use).strip()

        if return_line_idx is not None and return_indent_str is not None:
            # Вставка перед return
            block_lines = [
                (f"{return_indent_str}{b_line}{newline}" if b_line.strip() else newline)
                for b_line in clean_block.splitlines()
            ]
            insert_pieces = []
            if return_line_idx > 0 and lines[return_line_idx - 1].strip():
                insert_pieces.append(newline)
            insert_pieces.extend(block_lines)
            insert_pieces.append(newline)

            result_lines = lines[:return_line_idx] + insert_pieces + lines[return_line_idx:]
            return "".join(result_lines)
        else:
            # Явного return нет - вставляем в конец тела метода после последней непустой строки
            last_code_line_idx = delegate_line_idx
            for idx in range(end_line_idx - 1, delegate_line_idx, -1):
                if lines[idx].strip():
                    last_code_line_idx = idx
                    break

            block_lines = [
                (f"{delegate_indent_str}{b_line}{newline}" if b_line.strip() else newline)
                for b_line in clean_block.splitlines()
            ]
            insert_pieces = [newline] + block_lines + [newline]
            insert_idx = last_code_line_idx + 1
            result_lines = lines[:insert_idx] + insert_pieces + lines[insert_idx:]
            return "".join(result_lines)

    @classmethod
    def _detect_set_config_scenario(cls, api_text: str) -> str:
        """Определяет структуру метода set_config в api.py."""
        if "refresh_model_loading_parameters" in api_text:
            return "already_patched"
        lines = api_text.splitlines()
        def_idx = None
        base_indent = 0
        for idx, line in enumerate(lines):
            match = re.match(r'^([ \t]*)(?:async\s+)?def\s+set_config\b', line)
            if match:
                def_idx = idx
                base_indent = len(match.group(1).expandtabs(4))
                break
        if def_idx is None:
            return "not_found"
        end_idx = len(lines)
        for idx in range(def_idx + 1, len(lines)):
            match = re.match(r'^([ \t]*)(?:def|class|async\s+def)\b', lines[idx])
            if match and len(match.group(1).expandtabs(4)) <= base_indent:
                end_idx = idx
                break
        body_lines = lines[def_idx + 1:end_idx]
        has_save = any("shared.opts.save(" in l for l in body_lines)
        if has_save:
            return "direct_save"
        has_delegated = any(re.search(r'\b(?:sysinfo\.)?set_config\s*\(\s*req\b', l) for l in body_lines)
        if has_delegated:
            return "delegated"
        return "not_found"

    @classmethod
    def _detect_get_models_config_variant(cls, api_text: str) -> str:
        """Определяет вариант поля config в get_sd_models."""
        if '"config": None' in api_text or "'config': None" in api_text:
            return "literal_none"
        match = re.search(r'def\s+get_sd_models\b[\s\S]*?(?=(?:\n[ \t]*(?:def|class)\b)|\Z)', api_text)
        if match and re.search(r'["\']config["\']\s*:', match.group(0)):
            return "getattr_or_key"
        return "missing"

    @classmethod
    def _has_get_models_config(cls, api_text: str) -> bool:
        """
        Проверяет наличие поля 'config' в get_sd_models:
        - Литерал '"config": None' или "'config': None"
        - Либо присутствие ключа "config" в теле метода get_sd_models (например, getattr(x, 'config', None)).
        """
        if '"config": None' in api_text or "'config': None" in api_text:
            return True
        match = re.search(r'def\s+get_sd_models\b[\s\S]*?(?=(?:\n[ \t]*(?:def|class)\b)|\Z)', api_text)
        if match:
            body = match.group(0)
            if re.search(r'["\']config["\']\s*:', body):
                return True
        return False

    @staticmethod
    def _check_requirements_pinned(content: str, pinned: Dict[str, str]) -> bool:
        """Проверяет, зафиксированы ли в тексте requirements указанные версии библиотек."""
        found_pins = {}
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = re.match(r'^([a-zA-Z0-9_\-]+)\s*==\s*([a-zA-Z0-9_\-\.]+)', stripped)
            if m:
                pkg_name = m.group(1).lower().replace("_", "-")
                found_pins[pkg_name] = m.group(2).strip()

        for k, required_ver in pinned.items():
            norm_k = k.lower().replace("_", "-")
            if norm_k not in found_pins or found_pins[norm_k] != required_ver:
                return False
        return True

    @staticmethod
    def _update_requirements_text(content: str, pinned: Dict[str, str]) -> str:
        """Обновляет или добавляет зафиксированные версии библиотек в requirements-файл."""
        lines = content.splitlines()
        handled = set()
        new_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue

            m = re.match(r'^([a-zA-Z0-9_\-]+)', stripped)
            if m:
                pkg_name = m.group(1).lower().replace("_", "-")
                matched_key = None
                for k in pinned:
                    if k.lower().replace("_", "-") == pkg_name:
                        matched_key = k
                        break

                if matched_key:
                    new_lines.append(f"{matched_key}=={pinned[matched_key]}")
                    handled.add(matched_key)
                    continue

            new_lines.append(line)

        for k, v in pinned.items():
            if k not in handled:
                new_lines.append(f"{k}=={v}")

        return "\n".join(new_lines) + "\n"

    @classmethod
    def get_constraints_path(cls, forge_root: Path | str) -> Optional[Path]:
        """Возвращает путь к файлу ограничений neonroute_constraints.txt в каталоге webui."""
        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            return None
        return webui_dir / CONSTRAINTS_FILENAME

    @classmethod
    def find_forge_site_packages(cls, forge_root: Path | str) -> Optional[Path]:
        """
        Ищет каталог site-packages виртуального или встроенного Python Forge.
        Проверяет стандартные пути портативных сборок и venv:
        1. forge_root / system / python / Lib / site-packages
        2. webui_dir.parent / system / python / Lib / site-packages
        3. forge_root / python / Lib / site-packages
        4. webui_dir / venv / Lib / site-packages
        5. forge_root / venv / Lib / site-packages
        """
        if not forge_root:
            return None
        try:
            root = Path(forge_root).resolve()
            webui_dir = cls.find_forge_webui_dir(forge_root)
            candidates = [
                root / "system" / "python" / "Lib" / "site-packages",
                root / "python" / "Lib" / "site-packages",
            ]
            if webui_dir:
                candidates.extend([
                    webui_dir.parent / "system" / "python" / "Lib" / "site-packages",
                    webui_dir / "venv" / "Lib" / "site-packages",
                    webui_dir.parent / "venv" / "Lib" / "site-packages",
                ])
            candidates.append(root / "venv" / "Lib" / "site-packages")

            for cand in candidates:
                if cand.is_dir():
                    return cand
        except Exception:
            pass
        return None

    @classmethod
    def check_site_packages_pinned(cls, sp_dir: Path, pinned_libs: Dict[str, str]) -> bool:
        """
        Проверяет по метаданным .dist-info в site-packages,
        установлены ли точные версии целевых библиотек.
        """
        if not sp_dir or not sp_dir.is_dir():
            return False
        try:
            installed = {}
            for d in sp_dir.glob("*.dist-info"):
                name_part = d.name[:-len(".dist-info")]
                if "-" in name_part:
                    pkg, ver = name_part.rsplit("-", 1)
                    norm_pkg = pkg.lower().replace("_", "-")
                    installed[norm_pkg] = ver

            for pkg, req_ver in pinned_libs.items():
                norm_pkg = pkg.lower().replace("_", "-")
                if norm_pkg not in installed or installed[norm_pkg] != req_ver:
                    return False
            return True
        except Exception:
            return False

    @classmethod
    def are_libraries_pinned(cls, forge_root: Path | str) -> bool:
        """
        Проверяет, зафиксированы ли версии ключевых библиотек (fastapi, gradio, pydantic и др.)
        в neonroute_constraints.txt и requirements_versions.txt, либо уже установлены
        в эталонном виде в site-packages встроенного Python Forge.
        """
        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            return False

        constraints_file = webui_dir / CONSTRAINTS_FILENAME
        if constraints_file.is_file():
            try:
                c_text = constraints_file.read_text(encoding="utf-8", errors="ignore")
                if cls._check_requirements_pinned(c_text, PINNED_FORGE_LIBRARIES):
                    req_path = webui_dir / "requirements_versions.txt"
                    if req_path.is_file():
                        try:
                            r_text = req_path.read_text(encoding="utf-8", errors="ignore")
                            if not cls._check_requirements_pinned(r_text, PINNED_FORGE_LIBRARIES):
                                return False
                        except Exception:
                            return False
                    return True
            except Exception:
                pass

        # Если файл neonroute_constraints.txt пока отсутствует, проверяем, не является ли
        # сборка эталонным Forge (где целевые версии уже зафиксированы в site-packages)
        sp_dir = cls.find_forge_site_packages(forge_root)
        if sp_dir and cls.check_site_packages_pinned(sp_dir, PINNED_FORGE_LIBRARIES):
            # Окружение полностью эталонное — фиксируем constraints для надежности pip
            try:
                if not constraints_file.is_file():
                    cls.pin_libraries(forge_root)
            except Exception:
                pass
            return True

        return False

    @classmethod
    def pin_libraries(cls, forge_root: Path | str) -> bool:
        """
        Создает neonroute_constraints.txt и обновляет requirements_versions.txt
        для обратимого запрета обновления и фиксации версий критических библиотек.
        """
        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            return False

        try:
            # 1. Запись файла ограничений pip
            constraints_lines = ["# NeonRoute Pinned Dependencies for SD WebUI Forge"]
            for pkg, ver in PINNED_FORGE_LIBRARIES.items():
                constraints_lines.append(f"{pkg}=={ver}")
            constraints_content = "\n".join(constraints_lines) + "\n"

            constraints_file = webui_dir / CONSTRAINTS_FILENAME
            constraints_file.write_text(constraints_content, encoding="utf-8")

            # Также в forge_root, если он отличается от webui_dir
            root_p = Path(forge_root).resolve()
            if root_p != webui_dir and root_p.is_dir():
                (root_p / CONSTRAINTS_FILENAME).write_text(constraints_content, encoding="utf-8")

            # 2. Обновление requirements_versions.txt
            req_path = webui_dir / "requirements_versions.txt"
            old_req = req_path.read_text(encoding="utf-8", errors="ignore") if req_path.is_file() else ""
            new_req = cls._update_requirements_text(old_req, PINNED_FORGE_LIBRARIES)
            req_path.write_text(new_req, encoding="utf-8")

            logger.info(
                f"[ForgePatcher] Зафиксированы версии библиотек ({', '.join(PINNED_FORGE_LIBRARIES.keys())}) "
                f"в {req_path.name} и {CONSTRAINTS_FILENAME}"
            )
            return True
        except Exception as e:
            logger.error(f"[ForgePatcher] Ошибка фиксации версий библиотек: {e}", exc_info=True)
            return False

    @classmethod
    def get_backup_dir(cls, forge_root: Path | str) -> Optional[Path]:
        """Возвращает путь к директории оригиналов .neonroute_backup/original/."""
        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            return None
        return webui_dir / ".neonroute_backup" / "original"

    @classmethod
    def has_backup(cls, forge_root: Path | str) -> bool:
        """Проверяет наличие ранее созданной резервной копии оригинальных файлов."""
        backup_dir = cls.get_backup_dir(forge_root)
        if not backup_dir or not backup_dir.is_dir():
            return False
        api_bak = backup_dir / "modules" / "api" / "api.py"
        models_bak = backup_dir / "modules" / "api" / "models.py"
        sd_bak = backup_dir / "modules" / "sd_models.py"
        return api_bak.is_file() and models_bak.is_file() and sd_bak.is_file()

    @classmethod
    def find_launcher_to_patch(cls, forge_root: Path | str) -> Optional[Path]:
        """
        Ищет webui-user.bat, требующий адаптации под ProcessManager.
        Если в корне уже есть корректно настроенный forge_launcher.bat
        (содержащий --api и %*), возвращает None (доработка лаунчера не требуется).
        """
        if not forge_root:
            return None
        try:
            root = Path(forge_root).resolve()
            if not root.is_dir():
                return None
            webui_dir = cls.find_forge_webui_dir(forge_root)

            # 1. Проверяем наличие валидного forge_launcher.bat в корне или webui_dir
            candidates_forge = [root / "forge_launcher.bat"]
            if webui_dir and webui_dir != root:
                candidates_forge.append(webui_dir / "forge_launcher.bat")

            for cand in candidates_forge:
                if cand.is_file():
                    try:
                        txt = cand.read_text(encoding="utf-8", errors="ignore")
                        if "--api" in txt and ("%*" in txt or "%~1" in txt):
                            # Активный лаунчер forge_launcher.bat уже корректно настроен
                            return None
                    except Exception:
                        pass

            # 2. Ищем webui-user.bat
            candidates_user = []
            if webui_dir:
                candidates_user.append(webui_dir / "webui-user.bat")
            if root != webui_dir:
                candidates_user.append(root / "webui-user.bat")

            for cand in candidates_user:
                if cand.is_file():
                    return cand
        except Exception:
            pass

        return None

    @classmethod
    def is_webui_user_bat_patched(cls, bat_path: Path) -> bool:
        """Проверяет, содержит ли webui-user.bat поддержку аргументов ProcessManager и флаг --api."""
        if not bat_path or not bat_path.is_file():
            return False
        try:
            txt = bat_path.read_text(encoding="utf-8", errors="ignore")
            if "[NeonRoute]" in txt:
                return True
            has_api = "--api" in txt
            has_args = ("%*" in txt or "%~1" in txt)
            return has_api and has_args
        except Exception:
            return False

    @classmethod
    def check_status(cls, forge_root: Path | str) -> Tuple[ForgePatchStatus, str]:
        """
        Инспектирует состояние файлов Forge:
        Возвращает (Status, Описание).
        """
        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            msg = "Файлы SD WebUI Forge не найдены по указанному пути."
            logger.debug(f"[ForgePatcher] Аудит {forge_root}: status={ForgePatchStatus.NOT_FOUND.value} | {msg}")
            return ForgePatchStatus.NOT_FOUND, msg

        api_path = webui_dir / "modules" / "api" / "api.py"
        models_path = webui_dir / "modules" / "api" / "models.py"
        sd_path = webui_dir / "modules" / "sd_models.py"

        if not (api_path.is_file() and models_path.is_file() and sd_path.is_file()):
            msg = "Один или несколько файлов ядра Forge отсутствуют (api.py / models.py / sd_models.py)."
            logger.warning(f"[ForgePatcher] Аудит {webui_dir}: status={ForgePatchStatus.NOT_FOUND.value} | {msg}")
            return ForgePatchStatus.NOT_FOUND, msg

        try:
            api_text = api_path.read_text(encoding="utf-8", errors="ignore")
            models_text = models_path.read_text(encoding="utf-8", errors="ignore")
            sd_text = sd_path.read_text(encoding="utf-8", errors="ignore")

            # 1. Проверяем маркеры пропатченности файлов ядра Python
            has_free_vram_route = API_ROUTE_FREE_VRAM in api_text
            has_free_vram_method = "def free_vram_api(" in api_text
            has_refresh_opts = "refresh_model_loading_parameters" in api_text
            has_get_models_config = cls._has_get_models_config(api_text)
            api_patched = has_free_vram_route and has_free_vram_method and has_refresh_opts and has_get_models_config

            models_patched = (
                MODELS_PATCHED_CONFIG in models_text
                or bool(re.search(r'config:\s*Optional\[str\]\s*=\s*Field\(default=None,\s*title=["\']Config file["\']\)', models_text))
            )
            sd_patched = (
                "backend import memory_management" in sd_text
                and "memory_management.unload_all_models()" in sd_text
                and "model_data.forge_hash = ''" in sd_text
            )

            # 2. Проверяем состояние лаунчера webui-user.bat
            launcher_bat = cls.find_launcher_to_patch(forge_root)
            launcher_needs_patch = bool(launcher_bat and not cls.is_webui_user_bat_patched(launcher_bat))

            # 3. Проверяем якорные места стокового Forge 2.0
            can_insert_refresh = (has_refresh_opts or cls._insert_refresh_in_set_config(api_text, API_REFRESH_BLOCK) is not None)
            has_api_anchor = (API_ROUTE_UNLOAD in api_text or has_free_vram_route) and can_insert_refresh
            has_models_anchor = (
                MODELS_STOCK_CONFIG in models_text
                or bool(re.search(r'config:\s*Optional\[str\]\s*=\s*Field\(title=["\']Config file["\']\)', models_text))
                or models_patched
            )
            has_sd_anchor = sd_patched or bool(SD_UNLOAD_PATTERN.search(sd_text)) or (SD_UNLOAD_DEF in sd_text)

            # Диагностический срез
            set_cfg_scenario = cls._detect_set_config_scenario(api_text)
            models_cfg_variant = cls._detect_get_models_config_variant(api_text)
            models_diag = "models_patched" if models_patched else ("stock_anchor" if has_models_anchor else "incompatible")
            sd_diag = "sd_patched" if sd_patched else ("unload_anchor" if has_sd_anchor else "incompatible")
            launcher_diag = f"{launcher_bat.name}(needs_patch={launcher_needs_patch})" if launcher_bat else "none_or_configured"

            # 4. Проверяем фиксацию версий библиотек Forge
            libs_pinned = cls.are_libraries_pinned(forge_root)
            libs_diag = "pinned" if libs_pinned else "not_pinned"

            if api_patched and models_patched and sd_patched:
                missing_patch = []
                if launcher_needs_patch:
                    missing_patch.append(f"{launcher_bat.name}: флаг --api и передача параметров NeonRoute")
                if not libs_pinned:
                    missing_patch.append("фиксация версий библиотек (fastapi, gradio, pydantic...)")

                if missing_patch:
                    status = ForgePatchStatus.NEEDS_PATCH
                    res_msg = f"Требуется патч совместимости ({', '.join(missing_patch)})."
                    logger.info(
                        f"[ForgePatcher] Аудит {webui_dir}: status={status.value} | "
                        f"api(route={has_free_vram_route}, method={has_free_vram_method}, refresh={has_refresh_opts}/{set_cfg_scenario}, models_cfg={models_cfg_variant}) | "
                        f"models={models_diag} | sd_models={sd_diag} | launcher={launcher_diag} | libs={libs_diag}"
                    )
                    return status, res_msg

                status = ForgePatchStatus.PATCHED
                res_msg = "Патч совместимости активен ✓ (библиотеки зафиксированы, поддерживается VRAM offload и автосмена моделей)."
                logger.debug(
                    f"[ForgePatcher] Аудит {webui_dir}: status={status.value} | "
                    f"api(route={has_free_vram_route}, method={has_free_vram_method}, refresh={has_refresh_opts}/{set_cfg_scenario}, models_cfg={models_cfg_variant}) | "
                    f"models={models_diag} | sd_models={sd_diag} | launcher={launcher_diag} | libs={libs_diag}"
                )
                return status, res_msg

            if has_api_anchor and has_models_anchor and has_sd_anchor:
                missing = []
                if not api_patched:
                    missing.append("API free-vram/refresh")
                if not models_patched:
                    missing.append("models config=None")
                if not sd_patched:
                    missing.append("sd_models unload")
                if launcher_needs_patch:
                    missing.append(f"{launcher_bat.name} (--api/args)")
                if not libs_pinned:
                    missing.append("фиксация версий библиотек")
                status = ForgePatchStatus.NEEDS_PATCH
                res_msg = f"Требуется патч совместимости ({', '.join(missing)})."
                logger.info(
                    f"[ForgePatcher] Аудит {webui_dir}: status={status.value} | "
                    f"api(route={has_free_vram_route}, method={has_free_vram_method}, refresh={has_refresh_opts}/{set_cfg_scenario}, models_cfg={models_cfg_variant}) | "
                    f"models={models_diag} | sd_models={sd_diag} | launcher={launcher_diag} | libs={libs_diag}"
                )
                return status, res_msg

            status = ForgePatchStatus.INCOMPATIBLE
            res_msg = "Нестандартная версия или модифицированный форк Forge (целевые функции не распознаны)."
            logger.warning(
                f"[ForgePatcher] Аудит {webui_dir}: status={status.value} | "
                f"api(route={has_free_vram_route}, method={has_free_vram_method}, refresh={has_refresh_opts}/{set_cfg_scenario}, models_cfg={models_cfg_variant}) | "
                f"models={models_diag} | sd_models={sd_diag} | launcher={launcher_diag} | libs={libs_diag}"
            )
            return status, res_msg
        except Exception as e:
            logger.error(f"[ForgePatcher] Ошибка инспекции: {e}", exc_info=True)
            return ForgePatchStatus.INCOMPATIBLE, f"Ошибка чтения файлов Forge: {e}"

    @classmethod
    def create_backup(cls, forge_root: Path | str, force_refresh: bool = False) -> bool:
        """
        Сохраняет исходные оригиналы файлов Forge в <forge_webui>/.neonroute_backup/original/.
        Если резервная копия уже существует и актуальна по git HEAD — сохраняет её.
        Если ядро Forge обновилось (сменился git commit HEAD) — архивирует устаревший бэкап
        в .neonroute_backup/stale_<timestamp>/ и пересоздаёт свежий снимок актуального ядра.
        """
        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            return False

        backup_dir = webui_dir / ".neonroute_backup" / "original"
        info_file = backup_dir.parent / "backup_info.json"

        if cls.has_backup(forge_root):
            current_head = _get_forge_git_head(webui_dir)
            is_stale = False
            stale_reason = ""
            if current_head and info_file.is_file():
                try:
                    info = json.loads(info_file.read_text(encoding="utf-8"))
                    saved_head = info.get("git_head")
                    if saved_head and saved_head != current_head:
                        is_stale = True
                        stale_reason = f"сменился git commit Forge (бэкап: {saved_head[:8]} != текущий: {current_head[:8]})"
                except Exception:
                    pass

            if not is_stale and not force_refresh:
                logger.info(f"[ForgePatcher] Резервная копия уже существует и актуальна: {backup_dir}")
                return True

            # Архивируем устаревший бэкап
            ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            stale_dir = backup_dir.parent / f"stale_{ts}"
            try:
                if backup_dir.is_dir():
                    shutil.move(str(backup_dir), str(stale_dir))
                    logger.warning(
                        f"[ForgePatcher] Устаревший бэкап Forge ({stale_reason or 'принудительное обновление'}) "
                        f"перенесён в {stale_dir.name}"
                    )
            except Exception as e:
                logger.error(f"[ForgePatcher] Не удалось архивировать устаревший бэкап: {e}")

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "modules" / "api").mkdir(parents=True, exist_ok=True)

            src_api = webui_dir / "modules" / "api" / "api.py"
            src_models = webui_dir / "modules" / "api" / "models.py"
            src_sd = webui_dir / "modules" / "sd_models.py"

            dst_api = backup_dir / "modules" / "api" / "api.py"
            dst_models = backup_dir / "modules" / "api" / "models.py"
            dst_sd = backup_dir / "modules" / "sd_models.py"

            shutil.copy2(src_api, dst_api)
            shutil.copy2(src_models, dst_models)
            shutil.copy2(src_sd, dst_sd)

            backup_files = ["modules/api/api.py", "modules/api/models.py", "modules/sd_models.py"]
            launcher_bat = cls.find_launcher_to_patch(forge_root)
            if launcher_bat and launcher_bat.is_file():
                dst_bat = backup_dir / "webui-user.bat"
                shutil.copy2(launcher_bat, dst_bat)
                backup_files.append("webui-user.bat")

            req_file = webui_dir / "requirements_versions.txt"
            if req_file.is_file():
                dst_req = backup_dir / "requirements_versions.txt"
                shutil.copy2(req_file, dst_req)
                backup_files.append("requirements_versions.txt")

            meta = {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source_webui": str(webui_dir),
                "files": backup_files,
                "git_head": _get_forge_git_head(webui_dir),
                "file_hashes": {
                    rel: _calculate_file_sha256(webui_dir / rel)
                    for rel in backup_files
                    if (webui_dir / rel).is_file()
                },
            }
            if launcher_bat:
                meta["webui_user_bat_path"] = str(launcher_bat)

            (backup_dir.parent / "backup_info.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            logger.info(f"[ForgePatcher] Создана резервная копия оригинальных файлов ({', '.join(backup_files)}) в {backup_dir}")
            return True
        except Exception as e:
            logger.error(f"[ForgePatcher] Сбой создания резервной копии: {e}", exc_info=True)
            return False

    @classmethod
    def apply_patch(cls, forge_root: Path | str) -> Tuple[bool, str]:
        """
        Идемпотентно применяет патчи совместимости к Forge.
        Перед первым изменением автоматически создаёт резервную копию.
        """
        status, msg = cls.check_status(forge_root)
        if status == ForgePatchStatus.PATCHED:
            logger.info(f"[ForgePatcher] Патч уже применён для {forge_root}.")
            return True, "Патч уже применён."
        if status == ForgePatchStatus.NOT_FOUND:
            logger.warning(f"[ForgePatcher] Отказ применения патча: {msg}")
            return False, f"Ошибка: {msg}"
        if status == ForgePatchStatus.INCOMPATIBLE:
            logger.warning(f"[ForgePatcher] Отказ применения патча: {msg}")
            return False, f"Отклонено: {msg}"

        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            logger.error(f"[ForgePatcher] Не удалось определить каталог webui для {forge_root}.")
            return False, "Не удалось определить каталог webui."

        logger.info(f"[ForgePatcher] Старт применения патча для {webui_dir} (текущий статус: {status.value})")

        # 1. Создание резервной копии
        if not cls.has_backup(forge_root):
            if not cls.create_backup(forge_root):
                logger.error(f"[ForgePatcher] Не удалось создать резервную копию оригинальных файлов перед патчингом: {webui_dir}")
                return False, "Не удалось создать резервную копию оригинальных файлов перед патчингом."

        try:
            api_path = webui_dir / "modules" / "api" / "api.py"
            models_path = webui_dir / "modules" / "api" / "models.py"
            sd_path = webui_dir / "modules" / "sd_models.py"

            # -------------------------------------------------------------
            # Подготовка модификаций в памяти (без записи на диск)
            # -------------------------------------------------------------
            api_text = api_path.read_text(encoding="utf-8")
            models_text = models_path.read_text(encoding="utf-8")
            sd_text = sd_path.read_text(encoding="utf-8")

            # Модификация 1: modules/api/api.py
            # А. Маршрут /sdapi/v1/free-vram
            if API_ROUTE_FREE_VRAM not in api_text:
                if API_ROUTE_UNLOAD in api_text:
                    api_text = api_text.replace(
                        API_ROUTE_UNLOAD,
                        f"{API_ROUTE_UNLOAD}\n        {API_ROUTE_FREE_VRAM}"
                    )
                    logger.info("[ForgePatcher] Шаг 1 (api.py): добавлен маршрут /sdapi/v1/free-vram")
                else:
                    logger.error("[ForgePatcher] Шаг 1 (api.py) откат: не удалось найти точку вставки маршрута free-vram в api.py")
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти точку вставки маршрута free-vram в api.py."
            else:
                logger.debug("[ForgePatcher] Шаг 1 (api.py): маршрут /sdapi/v1/free-vram уже присутствует")

            # Б. Метод free_vram_api
            if "def free_vram_api(" not in api_text:
                reload_anchor = "    def reloadapi(self):"
                if reload_anchor in api_text:
                    api_text = api_text.replace(
                        reload_anchor,
                        f"{API_FREE_VRAM_METHOD}\n{reload_anchor}"
                    )
                    logger.info("[ForgePatcher] Шаг 1 (api.py): добавлен метод free_vram_api")
                else:
                    logger.error("[ForgePatcher] Шаг 1 (api.py) откат: не удалось найти точку вставки метода free_vram_api в api.py")
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти точку вставки метода free_vram_api в api.py."
            else:
                logger.debug("[ForgePatcher] Шаг 1 (api.py): метод free_vram_api уже присутствует")

            # В. refresh_model_loading_parameters() в set_config
            if "refresh_model_loading_parameters" in api_text:
                logger.debug("[ForgePatcher] Шаг 1 (api.py): refresh_model_loading_parameters уже присутствует")
            else:
                scenario_before = cls._detect_set_config_scenario(api_text)
                scenario_desc = (
                    "shared.opts.save" if scenario_before == "direct_save"
                    else ("delegated set_config" if scenario_before == "delegated" else scenario_before)
                )
                new_api_text = cls._insert_refresh_in_set_config(api_text, API_REFRESH_BLOCK)
                if new_api_text is None:
                    logger.error("[ForgePatcher] Шаг 1 (api.py) откат: не удалось найти точку вставки refresh_model_loading_parameters в set_config (api.py)")
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти точку вставки refresh_model_loading_parameters в set_config (api.py)."
                api_text = new_api_text
                logger.info(f"[ForgePatcher] Шаг 1 (api.py): вставлен блок refresh_model_loading_parameters (сценарий: {scenario_desc})")

            # Г. "config" в get_sd_models
            if '"config": None' in api_text or "'config': None" in api_text:
                logger.debug('[ForgePatcher] Шаг 1 (api.py): get_sd_models уже содержит литерал "config": None')
            else:
                pattern_old = '"filename": x.filename}'
                pattern_new = '"filename": x.filename, "config": None}'
                if pattern_old in api_text:
                    api_text = api_text.replace(pattern_old, pattern_new, 1)
                    logger.info('[ForgePatcher] Шаг 1 (api.py): get_sd_models обновлен литералом "config": None')
                elif "'filename': x.filename}" in api_text:
                    api_text = api_text.replace("'filename': x.filename}", "'filename': x.filename, 'config': None}", 1)
                    logger.info('[ForgePatcher] Шаг 1 (api.py): get_sd_models обновлен литералом "config": None')
                elif cls._has_get_models_config(api_text):
                    logger.info('[ForgePatcher] Шаг 1 (api.py): в get_sd_models сохранено существующее поле config (getattr)')
                else:
                    logger.debug('[ForgePatcher] Шаг 1 (api.py): get_sd_models не требует изменений')

            # Модификация 2: modules/api/models.py
            models_already_patched = (
                MODELS_PATCHED_CONFIG in models_text
                or bool(re.search(r'config:\s*Optional\[str\]\s*=\s*Field\(default=None,\s*title=["\']Config file["\']\)', models_text))
            )
            if not models_already_patched:
                stock_match = re.search(r'config:\s*Optional\[str\]\s*=\s*Field\(title=["\']Config file["\']\)', models_text)
                if stock_match:
                    models_text = models_text[:stock_match.start()] + MODELS_PATCHED_CONFIG + models_text[stock_match.end():]
                    logger.info("[ForgePatcher] Шаг 2 (models.py): обновлено поле config на default=None")
                elif MODELS_STOCK_CONFIG in models_text:
                    models_text = models_text.replace(MODELS_STOCK_CONFIG, MODELS_PATCHED_CONFIG, 1)
                    logger.info("[ForgePatcher] Шаг 2 (models.py): обновлено поле config на default=None")
                else:
                    logger.error("[ForgePatcher] Шаг 2 (models.py) откат: не удалось найти поле config в SDModelItem (models.py)")
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти поле config в SDModelItem (models.py)."
            else:
                logger.debug("[ForgePatcher] Шаг 2 (models.py): поле config уже пропатчено (default=None)")

            # Модификация 3: modules/sd_models.py
            if "model_data.forge_hash = ''" not in sd_text:
                if SD_UNLOAD_PATTERN.search(sd_text):
                    sd_text = SD_UNLOAD_PATTERN.sub(rf'\1{SD_PATCHED_UNLOAD_BODY}', sd_text, count=1)
                    logger.info("[ForgePatcher] Шаг 3 (sd_models.py): обновлено тело unload_model_weights")
                else:
                    logger.error("[ForgePatcher] Шаг 3 (sd_models.py) откат: не удалось найти тело unload_model_weights в sd_models.py")
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти тело unload_model_weights в sd_models.py."
            else:
                logger.debug("[ForgePatcher] Шаг 3 (sd_models.py): unload_model_weights уже пропатчено")

            # -------------------------------------------------------------
            # Запись модифицированных файлов на диск с гарантированным откатом
            # -------------------------------------------------------------
            try:
                api_path.write_text(api_text, encoding="utf-8")
                models_path.write_text(models_text, encoding="utf-8")
                sd_path.write_text(sd_text, encoding="utf-8")

                # Модификация 4: webui-user.bat (если активен и требует адаптации)
                launcher_bat = cls.find_launcher_to_patch(forge_root)
                if launcher_bat and not cls.is_webui_user_bat_patched(launcher_bat):
                    backup_dir = cls.get_backup_dir(forge_root)
                    if backup_dir:
                        backup_dir.mkdir(parents=True, exist_ok=True)
                        dst_bat = backup_dir / "webui-user.bat"
                        if not dst_bat.is_file():
                            shutil.copy2(launcher_bat, dst_bat)
                    cls._patch_webui_user_bat(launcher_bat)
                    logger.info(f"[ForgePatcher] Шаг 4 (launcher): адаптирован {launcher_bat.name}")
                else:
                    logger.debug("[ForgePatcher] Шаг 4 (launcher): модификация лаунчера не требуется (уже настроен или forge_launcher.bat)")

                # Модификация 5: Фиксация версий библиотек Forge (neonroute_constraints.txt и requirements_versions.txt)
                pinned_ok = cls.pin_libraries(forge_root)
                if not pinned_ok:
                    logger.error("[ForgePatcher] Шаг 5 (libraries) откат: не удалось зафиксировать версии библиотек")
                    cls.restore_backup(forge_root)
                    return False, "Не удалось зафиксировать версии критических библиотек Forge."
                logger.info("[ForgePatcher] Шаг 5 (libraries): зафиксированы версии критических библиотек (fastapi, gradio, pydantic...)")

                # Итоговая верификация
                new_status, new_msg = cls.check_status(forge_root)
                if new_status == ForgePatchStatus.PATCHED:
                    logger.info(f"[ForgePatcher] Патч успешно применен к {webui_dir}")
                    return True, "Патч совместимости успешно применен."
                else:
                    logger.error(f"[ForgePatcher] Откат: патч был применен частично. Итоговый статус: {new_msg}")
                    cls.restore_backup(forge_root)
                    return False, f"Патч был применен частично. Выполнен откат к резервной копии. Итоговый статус: {new_msg}"
            except Exception as write_err:
                logger.error(f"[ForgePatcher] Ошибка записи файлов или патча лаунчера: {write_err}", exc_info=True)
                cls.restore_backup(forge_root)
                return False, f"Ошибка применения патча (выполнен откат): {write_err}"

        except Exception as e:
            logger.error(f"[ForgePatcher] Ошибка применения патча: {e}", exc_info=True)
            cls.restore_backup(forge_root)
            return False, f"Ошибка применения патча (выполнен откат): {e}"

    @classmethod
    def _patch_webui_user_bat(cls, bat_path: Path) -> bool:
        """Применяет патч обработки аргументов ProcessManager и флага --api к webui-user.bat."""
        try:
            content = bat_path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r'^[ \t]*set[ \t]+COMMANDLINE_ARGS[ \t]*=(.*)$', content, re.MULTILINE | re.IGNORECASE)
            if match:
                raw_args = match.group(1).strip()
                if raw_args:
                    fallback_args = raw_args if "--api" in raw_args else f"--api {raw_args}"
                else:
                    fallback_args = "--api"
                patch_block = (
                    ":: [NeonRoute] Startup arguments handling (ProcessManager CLI args + fallback --api)\n"
                    'if not "%~1"=="" (\n'
                    "    set COMMANDLINE_ARGS=%*\n"
                    ") else (\n"
                    "    if not defined COMMANDLINE_ARGS (\n"
                    f"        set COMMANDLINE_ARGS={fallback_args}\n"
                    "    )\n"
                    ")\n"
                    "set _CHECK_API=%COMMANDLINE_ARGS:--api=%\n"
                    'if "%_CHECK_API%"=="%COMMANDLINE_ARGS%" set COMMANDLINE_ARGS=%COMMANDLINE_ARGS% --api\n'
                    "set _CHECK_API=\n"
                    ":: [NeonRoute] Enforce pinned package versions via PIP_CONSTRAINT\n"
                    'if exist "%~dp0neonroute_constraints.txt" (\n'
                    '    set "PIP_CONSTRAINT=%~dp0neonroute_constraints.txt"\n'
                    ')'
                )
                content = content[:match.start()] + patch_block + content[match.end():]
            else:
                patch_block = (
                    ":: [NeonRoute] Startup arguments handling (ProcessManager CLI args + fallback --api)\n"
                    'if not "%~1"=="" (\n'
                    "    set COMMANDLINE_ARGS=%*\n"
                    ") else (\n"
                    "    if not defined COMMANDLINE_ARGS (\n"
                    "        set COMMANDLINE_ARGS=--api\n"
                    "    )\n"
                    ")\n"
                    "set _CHECK_API=%COMMANDLINE_ARGS:--api=%\n"
                    'if "%_CHECK_API%"=="%COMMANDLINE_ARGS%" set COMMANDLINE_ARGS=%COMMANDLINE_ARGS% --api\n'
                    "set _CHECK_API=\n"
                    ":: [NeonRoute] Enforce pinned package versions via PIP_CONSTRAINT\n"
                    'if exist "%~dp0neonroute_constraints.txt" (\n'
                    '    set "PIP_CONSTRAINT=%~dp0neonroute_constraints.txt"\n'
                    ')'
                )
                call_pos = content.lower().find("call webui.bat")
                if call_pos != -1:
                    content = content[:call_pos] + patch_block + "\n" + content[call_pos:]
                else:
                    content = content + "\n\n" + patch_block + "\ncall webui.bat %*\n"

            # Добавляем %* к вызову webui.bat если его не было
            call_match = re.search(r'^[ \t]*call[ \t]+webui\.bat(?![ \t]*%\*)', content, re.MULTILINE | re.IGNORECASE)
            if call_match:
                content = content[:call_match.start()] + "call webui.bat %*" + content[call_match.end():]

            bat_path.write_text(content, encoding="utf-8")
            logger.info(f"[ForgePatcher] Лаунчер {bat_path} успешно адаптирован под ProcessManager.")
            return True
        except Exception as e:
            logger.error(f"[ForgePatcher] Ошибка патчинга {bat_path}: {e}")
            return False

    @classmethod
    def restore_backup(cls, forge_root: Path | str) -> Tuple[bool, str]:
        """
        Восстанавливает оригинальные файлы Forge из резервной копии .neonroute_backup/original/.
        """
        if not cls.has_backup(forge_root):
            logger.warning(f"[ForgePatcher] Резервная копия оригинальных файлов не найдена для {forge_root}")
            return False, "Резервная копия оригинальных файлов не найдена."

        webui_dir = cls.find_forge_webui_dir(forge_root)
        backup_dir = cls.get_backup_dir(forge_root)
        if not webui_dir or not backup_dir:
            logger.error(f"[ForgePatcher] Не удалось разрешить пути к резервной копии для {forge_root}")
            return False, "Не удалось разрешить пути к резервной копии."

        try:
            # Читаем метаданные резервной копии
            meta = {}
            info_file = backup_dir.parent / "backup_info.json"
            if info_file.is_file():
                try:
                    meta = json.loads(info_file.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}

            # Проверка актуальности бэкапа перед восстановлением:
            # Если репозиторий Forge обновился (сменился git commit HEAD),
            # ни в коем случае не затираем новое ядро старыми файлами!
            current_head = _get_forge_git_head(webui_dir)
            saved_head = meta.get("git_head")
            if current_head and saved_head and saved_head != current_head:
                msg = (
                    f"Обнаружено обновление ядра Forge после создания резервной копии "
                    f"(бэкап: {saved_head[:8]} != текущий HEAD: {current_head[:8]}). "
                    f"Откат к устаревшей резервной копии заблокирован во избежание повреждения файлов ядра Forge."
                )
                logger.error(f"[ForgePatcher] {msg}")
                # Архивируем устаревший бэкап
                ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
                stale_dir = backup_dir.parent / f"stale_{ts}"
                try:
                    if backup_dir.is_dir():
                        shutil.move(str(backup_dir), str(stale_dir))
                        logger.warning(f"[ForgePatcher] Устаревший бэкап архивирован в {stale_dir.name}")
                except Exception:
                    pass
                return False, msg

            src_api = backup_dir / "modules" / "api" / "api.py"
            src_models = backup_dir / "modules" / "api" / "models.py"
            src_sd = backup_dir / "modules" / "sd_models.py"

            dst_api = webui_dir / "modules" / "api" / "api.py"
            dst_models = webui_dir / "modules" / "api" / "models.py"
            dst_sd = webui_dir / "modules" / "sd_models.py"

            shutil.copy2(src_api, dst_api)
            shutil.copy2(src_models, dst_models)
            shutil.copy2(src_sd, dst_sd)

            restored_files = ["modules/api/api.py", "modules/api/models.py", "modules/sd_models.py"]
            # Восстанавливаем webui-user.bat если он сохранён в бэкапе
            src_bat = backup_dir / "webui-user.bat"
            if src_bat.is_file():
                dst_bat = cls.find_launcher_to_patch(forge_root)
                if not dst_bat and webui_dir:
                    dst_bat = webui_dir / "webui-user.bat"
                if dst_bat:
                    shutil.copy2(src_bat, dst_bat)
                    restored_files.append("webui-user.bat")

            original_backed_files = meta.get("files", [])

            # Восстанавливаем requirements_versions.txt если он был в оригинальном бэкапе
            src_req = backup_dir / "requirements_versions.txt"
            dst_req = webui_dir / "requirements_versions.txt"
            if src_req.is_file():
                shutil.copy2(src_req, dst_req)
                restored_files.append("requirements_versions.txt")
            elif dst_req.is_file() and "requirements_versions.txt" not in original_backed_files:
                # Если файла не было в оригинальной установке, удаляем созданный патчем
                try:
                    dst_req.unlink()
                except Exception:
                    pass

            # Обратимый откат ограничений: удаляем neonroute_constraints.txt
            constraints_file = webui_dir / CONSTRAINTS_FILENAME
            if constraints_file.is_file():
                try:
                    constraints_file.unlink()
                    logger.info(f"[ForgePatcher] Откат: удален файл ограничений версий {constraints_file}")
                except Exception as del_err:
                    logger.warning(f"[ForgePatcher] Ошибка удаления {constraints_file}: {del_err}")

            root_p = Path(forge_root).resolve()
            if root_p != webui_dir and root_p.is_dir():
                root_constraints = root_p / CONSTRAINTS_FILENAME
                if root_constraints.is_file():
                    try:
                        root_constraints.unlink()
                    except Exception:
                        pass

            logger.info(f"[ForgePatcher] Файлы Forge ({', '.join(restored_files)}) успешно восстановлены из {backup_dir}")
            return True, "Оригинальные файлы Forge успешно восстановлены из резервной копии."
        except Exception as e:
            logger.error(f"[ForgePatcher] Ошибка восстановления из бэкапа: {e}", exc_info=True)
            return False, f"Ошибка восстановления: {e}"
