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

import json
import logging
import re
import shutil
import textwrap
import time
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger("NeonRoute.ForgePatcher")


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
            return None
        try:
            root = Path(root_path).resolve()
            if not root.is_dir():
                return None

            candidates = [
                root / "webui_forge_cu121_torch231" / "webui",
                root / "webui",
                root,
            ]
            for c in candidates:
                if (c / "modules" / "api" / "api.py").is_file():
                    return c

            # Поиск по шаблону
            for sub in root.glob("webui*/webui"):
                if sub.is_dir() and (sub / "modules" / "api" / "api.py").is_file():
                    return sub
        except Exception:
            pass
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
            return ForgePatchStatus.NOT_FOUND, "Файлы SD WebUI Forge не найдены по указанному пути."

        api_path = webui_dir / "modules" / "api" / "api.py"
        models_path = webui_dir / "modules" / "api" / "models.py"
        sd_path = webui_dir / "modules" / "sd_models.py"

        if not (api_path.is_file() and models_path.is_file() and sd_path.is_file()):
            return ForgePatchStatus.NOT_FOUND, "Один или несколько файлов ядра Forge отсутствуют (api.py / models.py / sd_models.py)."

        try:
            api_text = api_path.read_text(encoding="utf-8", errors="ignore")
            models_text = models_path.read_text(encoding="utf-8", errors="ignore")
            sd_text = sd_path.read_text(encoding="utf-8", errors="ignore")

            # 1. Проверяем маркеры пропатченности файлов ядра Python
            has_free_vram_route = API_ROUTE_FREE_VRAM in api_text
            has_free_vram_method = "def free_vram_api(" in api_text
            has_refresh_opts = "refresh_model_loading_parameters" in api_text
            has_get_models_config = '"config": None' in api_text or "'config': None" in api_text
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

            if api_patched and models_patched and sd_patched:
                if launcher_needs_patch:
                    return (
                        ForgePatchStatus.NEEDS_PATCH,
                        f"Требуется патч совместимости ({launcher_bat.name}: флаг --api и передача параметров NeonRoute)."
                    )
                return ForgePatchStatus.PATCHED, "Патч совместимости активен ✓ (поддерживается VRAM offload и автосмена моделей)."

            # 3. Проверяем якорные места стокового Forge 2.0
            can_insert_refresh = (has_refresh_opts or cls._insert_refresh_in_set_config(api_text, API_REFRESH_BLOCK) is not None)
            has_api_anchor = (API_ROUTE_UNLOAD in api_text or has_free_vram_route) and can_insert_refresh
            has_models_anchor = (
                MODELS_STOCK_CONFIG in models_text
                or bool(re.search(r'config:\s*Optional\[str\]\s*=\s*Field\(title=["\']Config file["\']\)', models_text))
                or models_patched
            )
            has_sd_anchor = sd_patched or bool(SD_UNLOAD_PATTERN.search(sd_text)) or (SD_UNLOAD_DEF in sd_text)

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
                return ForgePatchStatus.NEEDS_PATCH, f"Требуется патч совместимости ({', '.join(missing)})."

            return ForgePatchStatus.INCOMPATIBLE, "Нестандартная версия или модифицированный форк Forge (целевые функции не распознаны)."
        except Exception as e:
            logger.error(f"[ForgePatcher] Ошибка инспекции: {e}", exc_info=True)
            return ForgePatchStatus.INCOMPATIBLE, f"Ошибка чтения файлов Forge: {e}"

    @classmethod
    def create_backup(cls, forge_root: Path | str) -> bool:
        """
        Сохраняет исходные оригиналы файлов Forge в <forge_webui>/.neonroute_backup/original/.
        Если резервная копия уже существует — НЕ перезаписывает её.
        """
        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            return False

        backup_dir = webui_dir / ".neonroute_backup" / "original"
        if cls.has_backup(forge_root):
            logger.info(f"[ForgePatcher] Резервная копия уже существует: {backup_dir}")
            return True

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

            meta = {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source_webui": str(webui_dir),
                "files": backup_files,
            }
            if launcher_bat:
                meta["webui_user_bat_path"] = str(launcher_bat)

            (backup_dir.parent / "backup_info.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            logger.info(f"[ForgePatcher] Создана резервная копия оригинальных файлов в {backup_dir}")
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
            return True, "Патч уже применён."
        if status == ForgePatchStatus.NOT_FOUND:
            return False, f"Ошибка: {msg}"
        if status == ForgePatchStatus.INCOMPATIBLE:
            return False, f"Отклонено: {msg}"

        webui_dir = cls.find_forge_webui_dir(forge_root)
        if not webui_dir:
            return False, "Не удалось определить каталог webui."

        # 1. Создание резервной копии
        if not cls.has_backup(forge_root):
            if not cls.create_backup(forge_root):
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
                else:
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти точку вставки маршрута free-vram в api.py."

            # Б. Метод free_vram_api
            if "def free_vram_api(" not in api_text:
                reload_anchor = "    def reloadapi(self):"
                if reload_anchor in api_text:
                    api_text = api_text.replace(
                        reload_anchor,
                        f"{API_FREE_VRAM_METHOD}\n{reload_anchor}"
                    )
                else:
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти точку вставки метода free_vram_api в api.py."

            # В. refresh_model_loading_parameters() в set_config
            new_api_text = cls._insert_refresh_in_set_config(api_text, API_REFRESH_BLOCK)
            if new_api_text is None:
                cls.restore_backup(forge_root)
                return False, "Не удалось найти точку вставки refresh_model_loading_parameters в set_config (api.py)."
            api_text = new_api_text

            # Г. "config": None в get_sd_models
            if '"config": None' not in api_text and "'config': None" not in api_text:
                pattern_old = '"filename": x.filename}'
                pattern_new = '"filename": x.filename, "config": None}'
                if pattern_old in api_text:
                    api_text = api_text.replace(pattern_old, pattern_new, 1)

            # Модификация 2: modules/api/models.py
            if not (
                MODELS_PATCHED_CONFIG in models_text
                or re.search(r'config:\s*Optional\[str\]\s*=\s*Field\(default=None,\s*title=["\']Config file["\']\)', models_text)
            ):
                stock_match = re.search(r'config:\s*Optional\[str\]\s*=\s*Field\(title=["\']Config file["\']\)', models_text)
                if stock_match:
                    models_text = models_text[:stock_match.start()] + MODELS_PATCHED_CONFIG + models_text[stock_match.end():]
                elif MODELS_STOCK_CONFIG in models_text:
                    models_text = models_text.replace(MODELS_STOCK_CONFIG, MODELS_PATCHED_CONFIG, 1)
                else:
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти поле config в SDModelItem (models.py)."

            # Модификация 3: modules/sd_models.py
            if "model_data.forge_hash = ''" not in sd_text:
                if SD_UNLOAD_PATTERN.search(sd_text):
                    sd_text = SD_UNLOAD_PATTERN.sub(rf'\1{SD_PATCHED_UNLOAD_BODY}', sd_text, count=1)
                else:
                    cls.restore_backup(forge_root)
                    return False, "Не удалось найти тело unload_model_weights в sd_models.py."

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

                # Итоговая верификация
                new_status, new_msg = cls.check_status(forge_root)
                if new_status == ForgePatchStatus.PATCHED:
                    logger.info(f"[ForgePatcher] Патч успешно применен к {webui_dir}")
                    return True, "Патч совместимости успешно применен."
                else:
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
                    "set _CHECK_API="
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
            return False, "Резервная копия оригинальных файлов не найдена."

        webui_dir = cls.find_forge_webui_dir(forge_root)
        backup_dir = cls.get_backup_dir(forge_root)
        if not webui_dir or not backup_dir:
            return False, "Не удалось разрешить пути к резервной копии."

        try:
            src_api = backup_dir / "modules" / "api" / "api.py"
            src_models = backup_dir / "modules" / "api" / "models.py"
            src_sd = backup_dir / "modules" / "sd_models.py"

            dst_api = webui_dir / "modules" / "api" / "api.py"
            dst_models = webui_dir / "modules" / "api" / "models.py"
            dst_sd = webui_dir / "modules" / "sd_models.py"

            shutil.copy2(src_api, dst_api)
            shutil.copy2(src_models, dst_models)
            shutil.copy2(src_sd, dst_sd)

            # Восстанавливаем webui-user.bat если он сохранён в бэкапе
            src_bat = backup_dir / "webui-user.bat"
            if src_bat.is_file():
                dst_bat = cls.find_launcher_to_patch(forge_root)
                if not dst_bat and webui_dir:
                    dst_bat = webui_dir / "webui-user.bat"
                if dst_bat:
                    shutil.copy2(src_bat, dst_bat)

            logger.info(f"[ForgePatcher] Файлы Forge успешно восстановлены из {backup_dir}")
            return True, "Оригинальные файлы Forge успешно восстановлены из резервной копии."
        except Exception as e:
            logger.error(f"[ForgePatcher] Ошибка восстановления из бэкапа: {e}", exc_info=True)
            return False, f"Ошибка восстановления: {e}"
