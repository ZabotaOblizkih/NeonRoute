"""
ui/dialogs/gguf_catalog_dialog.py
=================================
Модальное окно каталога и загрузки GGUF-моделей для NeonRoute в стиле Windows 11 Fluent Design
(FramelessDialog, FluentTitleBar, Dynamic Adaptive Palette).
Автоматически анализирует конфигурацию ПК (GPU, VRAM, топологию дисплея, RAM и тип KV-кэша),
отображает модели, идеально подходящие под текущую конфигурацию, поддерживает произвольный
поиск по HuggingFace Hub с автовыбором лучшей квантовки и фоновую загрузку с докачкой (.part).
"""

import configparser
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    FluentTitleBar,
    IconWidget,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SwitchButton,
)

import config
from services.layer_budgeter import LayerBudgeter
from services.llama_downloader import download_file
from services.server_config import ServerConfigResolver
from ui.common.palette_utils import extract_adaptive_palette
from ui.common.toast_helper import toast_error, toast_info, toast_success, toast_warning
from utils.config_manager import ConfigManager
from utils.system_monitor import SystemMonitor

logger = logging.getLogger("NeonRoute.GGUFCatalogDialog")


CURATED_GGUF_MODELS: List[Dict[str, Any]] = [
    {
        "id": "qwen2.5-3b-instruct",
        "title": "Qwen 2.5 3B Instruct",
        "family": "Qwen • 3B",
        "repo_id": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "filename": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_mb": 2010.0,
        "modes": ["assistant", "rp"],
        "badge": "⚡ Лёгкая универсальная",
        "desc": "Быстрая и компактная модель для мгновенных ответов Эларии, ноутбуков и видеокарт от 4 ГБ VRAM.",
    },
    {
        "id": "qwen2.5-coder-3b-instruct",
        "title": "Qwen 2.5 Coder 3B Instruct",
        "family": "Qwen • 3B Code",
        "repo_id": "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        "filename": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        "size_mb": 2010.0,
        "modes": ["engineer", "assistant"],
        "badge": "🛠️ Лёгкий Инженер",
        "desc": "Компактная специализированная кодовая модель для режима «Инженер» на видеокартах 4–6 ГБ VRAM.",
    },
    {
        "id": "llama-3.2-3b-instruct",
        "title": "Llama 3.2 3B Instruct",
        "family": "Llama • 3B",
        "repo_id": "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "size_mb": 1930.0,
        "modes": ["assistant", "rp"],
        "badge": "💬 Лёгкая Llama",
        "desc": "Сбалансированная компактная модель от Meta для быстрых диалогов и ролевого чата на бюджетных ПК.",
    },
    {
        "id": "qwen2.5-coder-7b-instruct",
        "title": "Qwen 2.5 Coder 7B Instruct",
        "family": "Qwen • 7B Code",
        "repo_id": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "size_mb": 4470.0,
        "modes": ["engineer", "assistant"],
        "badge": "🛠️ Эталон для Инженера",
        "desc": "Оптимальная модель для генерации Python-кода, автотестов и работы в песочнице на 6–8 ГБ VRAM.",
    },
    {
        "id": "llama-3.1-8b-instruct",
        "title": "Llama 3.1 8B Instruct",
        "family": "Llama • 8B",
        "repo_id": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "filename": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "size_mb": 4690.0,
        "modes": ["assistant", "rp"],
        "badge": "💬 Универсал (Ассистент)",
        "desc": "Надёжная базовая модель от Meta для режима Ассистента, анализа документов и повседневного общения.",
    },
    {
        "id": "l3-8b-stheno-v3.2",
        "title": "Llama 3 Stheno 8B v3.2",
        "family": "Llama • 8B RP",
        "repo_id": "bartowski/L3-8B-Stheno-v3.2-GGUF",
        "filename": "L3-8B-Stheno-v3.2-Q4_K_M.gguf",
        "size_mb": 4690.0,
        "modes": ["rp", "assistant"],
        "badge": "🎭 Лучшая для Ролевого чата",
        "desc": "Специализированная ролевая модель для живого отыгрыша карточек персонажей и художественных сцен.",
    },
    {
        "id": "qwen3-8b",
        "title": "Qwen 3 8B (Thinking)",
        "family": "Qwen • 8B Reasoning",
        "repo_id": "Qwen/Qwen3-8B-GGUF",
        "filename": "Qwen3-8B-Q4_K_M.gguf",
        "size_mb": 4800.0,
        "modes": ["engineer", "assistant"],
        "badge": "🧠 Думающая (<think>)",
        "desc": "Гибридная модель с глубокими рассуждениями <think> для сложной архитектуры, логики и кода.",
    },
    {
        "id": "gemma-3-12b-it",
        "title": "Gemma 3 12B Instruct",
        "family": "Gemma • 12B",
        "repo_id": "bartowski/google_gemma-3-12b-it-GGUF",
        "filename": "google_gemma-3-12b-it-Q4_K_M.gguf",
        "size_mb": 6960.0,
        "modes": ["assistant", "rp", "engineer"],
        "badge": "🔥 Продвинутая 12B",
        "desc": "Мощная модель для видеокарт 12–16 ГБ VRAM (на 8 ГБ работает с частичной выгрузкой слоёв в ОЗУ).",
    },
]

MODE_LABELS: Dict[str, str] = {
    "assistant": "💬 Ассистент",
    "rp": "🎭 Ролевой чат",
    "engineer": "🛠️ Инженер",
}


def resolve_target_models_dir() -> Path:
    """
    Определяет актуальную папку для сохранения .gguf моделей:
    1) Секция [PATHS] -> models_dir в server_config.ini
    2) Секция [llama_server] -> models_dir в server_config.ini
    3) Стандартная папка config.LOCAL_MODELS_DIR (models/)
    """
    try:
        ini_cfg = ServerConfigResolver.load_ini()
        custom_dir = str(ini_cfg.get("models_dir") or "").strip()
        if custom_dir:
            p = Path(custom_dir)
            if not p.is_absolute():
                p = config.BASE_DIR / p
            p.mkdir(parents=True, exist_ok=True)
            return p.resolve()

        ini_path = config.SERVER_CONFIG_INI_PATH
        if ini_path.is_file():
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(str(ini_path), encoding="utf-8-sig")
            if parser.has_section("llama_server") and "models_dir" in parser["llama_server"]:
                raw = parser["llama_server"].get("models_dir", "").strip().strip('"').strip("'")
                if raw:
                    p = Path(raw)
                    if not p.is_absolute():
                        p = config.BASE_DIR / p
                    p.mkdir(parents=True, exist_ok=True)
                    return p.resolve()
    except Exception as e:
        logger.debug(f"[GGUFCatalog] Fallback to LOCAL_MODELS_DIR: {e}")

    target = config.LOCAL_MODELS_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def get_system_config_snapshot() -> Dict[str, Any]:
    """Считывает актуальный профиль оборудования и настройки KV-кэша/контекста (Zero-Freeze, без subprocess)."""
    import psutil
    hw = LayerBudgeter.detect_hardware_kv_profile()
    is_pure_compute, os_reserve_mb = LayerBudgeter.detect_gpu_topology()

    vram_total_mb = int(hw.get("vram_total_mb") or 0)
    try:
        ram_total_mb = round(psutil.virtual_memory().total / (1024.0 * 1024.0), 1)
    except Exception:
        ram_total_mb = 16384.0
    vendor = str(hw.get("vendor") or "unknown").lower()
    gpu_name = str(hw.get("gpu_name") or "Unknown GPU")

    app_cfg = ConfigManager.load_config()
    kv_type = str(app_cfg.get("kv_cache_type", "q8_0")).lower()
    ini_cfg = ServerConfigResolver.load_ini()
    default_ctx = int(ini_cfg.get("default_context", 14336) or 14336)
    eff_ctx = min(max(4096, default_ctx), 8192)
    kv_mb = LayerBudgeter.estimate_kv_cache_mb(eff_ctx, cache_type=kv_type)

    if vram_total_mb <= 512:
        usable_vram_mb = 0.0
    else:
        eff_os = os_reserve_mb if vendor == "nvidia" else max(os_reserve_mb, 650)
        safety_mb = 170
        compute_mb = 170
        usable_vram_mb = max(0.0, float(vram_total_mb - eff_os - safety_mb - kv_mb - compute_mb))

    return {
        "gpu_name": gpu_name,
        "vendor": vendor,
        "vram_total_mb": vram_total_mb,
        "ram_total_mb": ram_total_mb,
        "is_pure_compute": is_pure_compute,
        "os_reserve_mb": os_reserve_mb,
        "kv_cache_type": kv_type,
        "kv_cache_mb": kv_mb,
        "usable_vram_mb": usable_vram_mb,
    }


def evaluate_model_for_hardware(
    size_mb: float,
    vram_total_mb: int,
    ram_total_mb: float,
    vendor: str = "nvidia",
    is_primary_display: bool = True,
    kv_cache_type: str = "q8_0",
) -> Tuple[int, float, str, str]:
    """
    Оценивает совместимость модели размера size_mb с конфигурацией пользователя.
    Возвращает кортеж (tier, sort_score, badge_text, detail_text):
      tier == 0: 🟢 Идеально ложится под текущий конфиг (100% в VRAM, либо оптимальная лёгкая для CPU/iGPU)
      tier == 1: 🟡 Пойдёт с частичной выгрузкой слоёв в системную ОЗУ (медленнее)
      tier == 2: 🔴 Слишком тяжёлая для текущего ПК (недостаточно памяти)
    """
    kv_mb = LayerBudgeter.estimate_kv_cache_mb(8192, cache_type=kv_cache_type)
    if vram_total_mb <= 512:
        usable_vram = 0.0
    else:
        os_res = (600 if is_primary_display else 200) if vendor == "nvidia" else 650
        safety = 170
        compute = 150 if size_mb < 5000 else 220
        usable_vram = max(0.0, float(vram_total_mb - os_res - safety - kv_mb - compute))

    safe_ram_for_offload = max(0.0, (ram_total_mb * 0.60) - 1500.0)

    if usable_vram > 0 and size_mb <= usable_vram:
        utilization_gap = usable_vram - size_mb
        return (
            0,
            float(utilization_gap),
            "🟢 Идеально для вашей GPU",
            f"100% в VRAM (~{size_mb / 1024:.1f} ГБ из {usable_vram / 1024:.1f} ГБ доступных)",
        )
    elif vram_total_mb <= 512 and size_mb <= 2300 and size_mb <= safe_ram_for_offload:
        return (
            0,
            float(size_mb),
            "🟢 Оптимально для CPU / iGPU",
            f"Полностью помещается в ОЗУ (~{size_mb / 1024:.1f} ГБ)",
        )
    elif (size_mb - usable_vram) <= safe_ram_for_offload and size_mb <= (usable_vram + safe_ram_for_offload):
        ram_part_gb = max(0.0, size_mb - usable_vram) / 1024.0
        if vram_total_mb <= 512:
            return (
                1,
                float(size_mb),
                "🟡 Нагрузка на CPU (медленно)",
                f"Работа целиком в ОЗУ (~{size_mb / 1024:.1f} ГБ)",
            )
        return (
            1,
            float(size_mb - usable_vram),
            "🟡 Частично в ОЗУ (медленнее)",
            f"В системную ОЗУ выгрузится ~{ram_part_gb:.1f} ГБ весов",
        )
    else:
        return (
            2,
            float(size_mb),
            "🔴 Слишком тяжёлая для ПК",
            "Недостаточно VRAM и ОЗУ для безопасного запуска",
        )


def quant_preference_score(filename: str) -> int:
    """Приоритет выбора квантовки среди файлов, подходящих под бюджет памяти."""
    fn = filename.lower()
    prefs = [
        "q4_k_m",
        "q5_k_m",
        "q4_k_s",
        "q5_k_s",
        "iq4_xs",
        "q4_0",
        "q6_k",
        "q3_k_m",
        "q3_k_s",
        "q8_0",
        "q2_k",
    ]
    for idx, p in enumerate(prefs):
        if p in fn:
            return idx
    return 99


def is_thinking_model_name(name: str) -> bool:
    low = (name or "").lower()
    keywords = getattr(config, "THINKING_MODEL_KEYWORDS", ("r1", "qwq", "qwen3", "gemma-4", "gemma4", "think", "reasoning"))
    return any(k in low for k in keywords)


class _GGUFDownloadWorker(QThread):
    """
    Долгоживущий фоновый поток скачивания .gguf модели (parent=None).
    Не уничтожается при закрытии модального окна каталога, позволяя загрузке
    завершиться в фоне и отображать актуальный прогресс при повторном открытии окна.
    """

    progress_signal = pyqtSignal(str, int, int, float)  # filename, downloaded_bytes, total_bytes, speed_mb_s
    finished_signal = pyqtSignal(bool, str, str, str)   # success, filename, dest_path_str, message

    def __init__(self, url: str, dest_path: Path, model_title: str):
        super().__init__(parent=None)
        self.url = url
        self.dest_path = dest_path
        self.filename = dest_path.name
        self.model_title = model_title
        self._cancel_event = threading.Event()
        self.last_downloaded: int = 0
        self.last_total: int = 0
        self.last_speed_mb: float = 0.0

    def cancel(self):
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self):
        start_ts = time.monotonic()
        last_emit_ts = 0.0
        initial_bytes = 0
        part_path = self.dest_path.with_suffix(self.dest_path.suffix + ".part")
        if part_path.exists():
            try:
                initial_bytes = part_path.stat().st_size
            except Exception:
                initial_bytes = 0

        def _on_prog(cur: int, tot: int):
            nonlocal last_emit_ts
            now = time.monotonic()
            self.last_downloaded = cur
            self.last_total = tot
            if now - last_emit_ts >= 0.12 or (tot > 0 and cur >= tot):
                elapsed = max(0.001, now - start_ts)
                delta_bytes = max(0, cur - initial_bytes)
                speed_mb = (delta_bytes / (1024.0 * 1024.0)) / elapsed
                self.last_speed_mb = speed_mb
                last_emit_ts = now
                self.progress_signal.emit(self.filename, cur, tot, speed_mb)

        try:
            download_file(
                url=self.url,
                dest_path=self.dest_path,
                progress_callback=_on_prog,
                timeout=60,
                max_retries=3,
                retry_delay=2.0,
                cancel_event=self._cancel_event,
            )
            self.finished_signal.emit(
                True,
                self.filename,
                str(self.dest_path),
                f"Модель «{self.model_title}» ({self.filename}) успешно скачана в папку models!",
            )
        except InterruptedError as ie:
            self.finished_signal.emit(False, self.filename, "", str(ie))
        except Exception as e:
            logger.error(f"[GGUFCatalog] Ошибка загрузки {self.filename}: {e}")
            self.finished_signal.emit(False, self.filename, "", f"Ошибка загрузки {self.filename}: {e}")


_ACTIVE_DOWNLOAD_WORKER: Optional[_GGUFDownloadWorker] = None
_GLOBAL_REFRESH_CALLBACKS: List[Callable[[], None]] = []


def get_active_download_worker() -> Optional[_GGUFDownloadWorker]:
    global _ACTIVE_DOWNLOAD_WORKER
    if _ACTIVE_DOWNLOAD_WORKER is not None and _ACTIVE_DOWNLOAD_WORKER.isRunning():
        return _ACTIVE_DOWNLOAD_WORKER
    return None


def _on_global_download_finished(success: bool, filename: str, dest_path_str: str, message: str):
    global _ACTIVE_DOWNLOAD_WORKER
    _ACTIVE_DOWNLOAD_WORKER = None

    for cb in list(_GLOBAL_REFRESH_CALLBACKS):
        try:
            cb()
        except Exception:
            pass

    if "PYTEST_CURRENT_TEST" not in os.environ:
        app_win = QApplication.activeWindow()
        if app_win is not None and isinstance(app_win, QWidget):
            if success:
                toast_success(app_win, "Модель загружена", message)
            else:
                if "отменена" in (message or "").lower():
                    toast_info(app_win, "Загрузка приостановлена", f"Загрузка {filename} остановлена (прогресс .part сохранён).")
                else:
                    toast_error(app_win, "Ошибка загрузки модели", message)


class _HFSearchWorker(QThread):
    """Фоновый поток поиска GGUF-репозиториев и их квантовок через HuggingFace Hub API."""

    results_ready = pyqtSignal(str, list, str)

    def __init__(self, query: str, hw_snap: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.query = query.strip()
        self.hw_snap = hw_snap
        self._aborted = False

    def abort(self):
        self._aborted = True

    def run(self):
        if not self.query or self._aborted:
            return
        try:
            from huggingface_hub import HfApi

            api = HfApi()
            raw_models = list(
                api.list_models(
                    search=self.query,
                    filter="gguf",
                    sort="downloads",
                    limit=10,
                )
            )
            if self._aborted:
                return

            vram = int(self.hw_snap.get("vram_total_mb") or 0)
            ram = float(self.hw_snap.get("ram_total_mb") or 16384.0)
            vendor = str(self.hw_snap.get("vendor") or "nvidia")
            is_prim = not bool(self.hw_snap.get("is_pure_compute", False))
            kv_type = str(self.hw_snap.get("kv_cache_type") or "q8_0")

            items: List[Dict[str, Any]] = []
            for m in raw_models:
                if self._aborted:
                    return
                if getattr(m, "gated", False) or getattr(m, "private", False):
                    continue
                try:
                    info = api.model_info(m.id, files_metadata=True)
                except Exception:
                    continue

                variants: List[Dict[str, Any]] = []
                for s in (info.siblings or []):
                    fn = getattr(s, "rfilename", "") or ""
                    fn_low = fn.lower()
                    if not fn_low.endswith(".gguf") or "mmproj" in fn_low:
                        continue
                    if "-of-00" in fn_low:
                        continue
                    sz_bytes = int(getattr(s, "size", 0) or 0)
                    sz_mb = round(sz_bytes / (1024.0 * 1024.0), 1)
                    if sz_mb <= 15.0:
                        continue

                    tier, sort_score, badge, detail = evaluate_model_for_hardware(
                        sz_mb, vram, ram, vendor, is_prim, kv_type
                    )
                    variants.append(
                        {
                            "filename": fn,
                            "size_mb": sz_mb,
                            "tier": tier,
                            "sort_score": sort_score,
                            "q_pref": quant_preference_score(fn),
                            "badge": badge,
                            "detail": detail,
                        }
                    )

                if not variants:
                    continue

                variants.sort(key=lambda v: (v["tier"], v["q_pref"], v["sort_score"]))
                best = variants[0]

                repo_short = m.id.split("/")[-1]
                low_repo = m.id.lower()
                if any(k in low_repo for k in ("coder", "code", "starcoder", "deepseek-coder", "qwen3", "r1")):
                    modes = ["engineer", "assistant"]
                elif any(k in low_repo for k in ("rp", "stheno", "lumimaid", "magnum", "roleplay", "story", "mytho")):
                    modes = ["rp", "assistant"]
                else:
                    modes = ["assistant", "rp", "engineer"]

                downloads_cnt = int(getattr(m, "downloads", 0) or 0)
                items.append(
                    {
                        "id": m.id,
                        "title": repo_short,
                        "family": f"HF • {m.id.split('/')[0]} • ⬇ {downloads_cnt:,}".replace(",", " "),
                        "repo_id": m.id,
                        "filename": best["filename"],
                        "size_mb": best["size_mb"],
                        "modes": modes,
                        "badge": "🔎 HuggingFace GGUF",
                        "desc": f"Репозиторий: {m.id} ({len(variants)} вар. квантования)",
                        "variants": variants,
                        "tier": best["tier"],
                        "sort_score": best["sort_score"],
                        "hw_badge": best["badge"],
                        "hw_detail": best["detail"],
                    }
                )

            items.sort(key=lambda it: (it["tier"], it["sort_score"]))
            if not self._aborted:
                self.results_ready.emit(self.query, items, "")
        except Exception as e:
            if not self._aborted:
                self.results_ready.emit(self.query, [], str(e))


class _ModelCatalogCard(QFrame):
    """Карточка отдельной GGUF-модели с индикатором совместимости, выбором квантовки и кнопкой загрузки."""

    download_requested = pyqtSignal(dict)
    cancel_requested = pyqtSignal()

    def __init__(
        self,
        model_data: Dict[str, Any],
        palette: Dict[str, str],
        installed_files: set,
        models_dir: Path,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.model_data = dict(model_data)
        self.palette = palette
        self.installed_files = installed_files
        self.models_dir = models_dir
        self.setProperty("class", "CatalogModelCard")

        self._variants: List[Dict[str, Any]] = list(self.model_data.get("variants") or [])
        self._init_ui()
        self._refresh_install_state()

    def minimumSizeHint(self) -> QSize:
        return QSize(460, 96)

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.lbl_title = QLabel(self.model_data["title"], self)
        self.lbl_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #F8FAFC;")
        top_row.addWidget(self.lbl_title)

        lbl_fam = CaptionLabel(self.model_data.get("family", ""), self)
        lbl_fam.setStyleSheet(
            "color: #94A3B8; background-color: rgba(255,255,255,0.06); "
            "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
        )
        top_row.addWidget(lbl_fam)

        if is_thinking_model_name(self.model_data["title"]) or is_thinking_model_name(self.model_data["filename"]):
            lbl_think = CaptionLabel("🧠 <think>", self)
            lbl_think.setStyleSheet(
                "color: #C084FC; background-color: rgba(168, 85, 247, 0.16); "
                "border: 1px solid rgba(168, 85, 247, 0.35); border-radius: 4px; padding: 2px 6px; font-size: 11px;"
            )
            top_row.addWidget(lbl_think)

        top_row.addStretch(1)

        self.lbl_hw_badge = CaptionLabel(self.model_data.get("hw_badge", ""), self)
        self._apply_hw_badge_style(int(self.model_data.get("tier", 0)))
        top_row.addWidget(self.lbl_hw_badge, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lay.addLayout(top_row)

        lbl_desc = CaptionLabel(self.model_data.get("desc", ""), self)
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #CBD5E1; font-size: 11.5px;")
        lay.addWidget(lbl_desc)

        if len(self._variants) > 1:
            q_row = QHBoxLayout()
            q_row.setSpacing(8)
            q_lbl = CaptionLabel("Квантовка (.gguf):", self)
            q_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
            q_row.addWidget(q_lbl)

            self.combo_quant = ComboBox(self)
            for v in self._variants:
                icon = "🟢" if v["tier"] == 0 else ("🟡" if v["tier"] == 1 else "🔴")
                self.combo_quant.addItem(f"{icon} {v['filename']} ({v['size_mb'] / 1024:.2f} ГБ)")
            self.combo_quant.currentIndexChanged.connect(self._on_quant_changed)
            q_row.addWidget(self.combo_quant, 1)
            lay.addLayout(q_row)
        else:
            self.combo_quant = None

        btm_row = QHBoxLayout()
        btm_row.setSpacing(10)

        modes_str = " • ".join(MODE_LABELS.get(m, m) for m in self.model_data.get("modes", []))
        self.lbl_meta = CaptionLabel("", self)
        self.lbl_meta.setWordWrap(True)
        self.lbl_meta.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.lbl_meta.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: 500;")
        self._modes_str = modes_str
        self._update_meta_text()
        btm_row.addWidget(self.lbl_meta, 1)

        self.btn_download = PrimaryPushButton(FIF.DOWNLOAD, "Скачать", self)
        self.btn_download.setFixedHeight(30)
        self.btn_download.setMinimumWidth(115)
        self.btn_download.clicked.connect(self._on_download_clicked)
        btm_row.addWidget(self.btn_download, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.btn_cancel_dl = PushButton("Отмена", self)
        self.btn_cancel_dl.setFixedHeight(30)
        self.btn_cancel_dl.setMinimumWidth(80)
        self.btn_cancel_dl.hide()
        self.btn_cancel_dl.clicked.connect(lambda: self.cancel_requested.emit())
        btm_row.addWidget(self.btn_cancel_dl, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lay.addLayout(btm_row)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 3px;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #00F0FF;
                border-radius: 3px;
            }
        """)
        self.progress_bar.hide()
        lay.addWidget(self.progress_bar)

    def _apply_hw_badge_style(self, tier: int):
        if tier == 0:
            fg, bg, brd = "#10B981", "rgba(16, 185, 129, 0.14)", "rgba(16, 185, 129, 0.40)"
        elif tier == 1:
            fg, bg, brd = "#F59E0B", "rgba(245, 158, 11, 0.14)", "rgba(245, 158, 11, 0.40)"
        else:
            fg, bg, brd = "#F87171", "rgba(248, 113, 113, 0.14)", "rgba(248, 113, 113, 0.40)"
        self.lbl_hw_badge.setStyleSheet(
            f"color: {fg}; background-color: {bg}; border: 1px solid {brd}; "
            f"border-radius: 5px; padding: 2px 8px; font-weight: 600; font-size: 11px;"
        )

    def _update_meta_text(self):
        sz_gb = float(self.model_data.get("size_mb", 0.0)) / 1024.0
        detail = self.model_data.get("hw_detail", "")
        fn = self.model_data.get("filename", "")
        self.lbl_meta.setText(f"{self._modes_str}  |  📦 {sz_gb:.2f} ГБ ({fn})  •  {detail}")

    def _on_quant_changed(self, idx: int):
        if 0 <= idx < len(self._variants):
            v = self._variants[idx]
            self.model_data["filename"] = v["filename"]
            self.model_data["size_mb"] = v["size_mb"]
            self.model_data["tier"] = v["tier"]
            self.model_data["hw_badge"] = v["badge"]
            self.model_data["hw_detail"] = v["detail"]
            self.lbl_hw_badge.setText(v["badge"])
            self._apply_hw_badge_style(int(v["tier"]))
            self._update_meta_text()
            self._refresh_install_state()

    def _refresh_install_state(self):
        fn = self.model_data.get("filename", "")
        fn_low = fn.lower()
        stem_low = Path(fn).stem.lower()
        is_installed = (
            fn_low in self.installed_files
            or stem_low in self.installed_files
            or (self.models_dir / fn).is_file()
        )
        part_file = self.models_dir / f"{fn}.part"

        self.progress_bar.hide()
        self.btn_cancel_dl.hide()
        self.btn_download.show()

        if is_installed:
            self.btn_download.setEnabled(False)
            self.btn_download.setText("✅ Установлена")
        elif part_file.is_file() and part_file.stat().st_size > 0:
            part_mb = part_file.stat().st_size / (1024.0 * 1024.0)
            self.btn_download.setEnabled(True)
            self.btn_download.setText(f"Докачать ({part_mb:.0f} МБ уже на диске)")
        else:
            self.btn_download.setEnabled(True)
            sz_gb = float(self.model_data.get("size_mb", 0.0)) / 1024.0
            self.btn_download.setText(f"Скачать ({sz_gb:.1f} ГБ)")

    def set_downloading_state(self, downloaded_bytes: int, total_bytes: int, speed_mb_s: float):
        self.progress_bar.show()
        self.btn_cancel_dl.show()
        self.btn_download.setEnabled(False)
        dl_mb = downloaded_bytes / (1024.0 * 1024.0)
        if total_bytes > 0:
            tot_mb = total_bytes / (1024.0 * 1024.0)
            pct = min(100, int((downloaded_bytes / total_bytes) * 100))
            self.progress_bar.setValue(pct)
            self.btn_download.setText(f"⏳ {pct}% ({dl_mb:.0f}/{tot_mb:.0f} МБ • {speed_mb_s:.1f} МБ/с)")
        else:
            self.btn_download.setText(f"⏳ {dl_mb:.0f} МБ ({speed_mb_s:.1f} МБ/с)")

    def _on_download_clicked(self):
        self.download_requested.emit(dict(self.model_data))


class GGUFCatalogDialog(FramelessDialog):
    """
    Окно каталога и загрузки GGUF-моделей с автоматическим отбором под конфигурацию ПК
    и произвольным поиском по HuggingFace Hub.
    """

    model_downloaded = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        on_models_changed: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent=parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self._on_models_changed = on_models_changed
        if on_models_changed and on_models_changed not in _GLOBAL_REFRESH_CALLBACKS:
            _GLOBAL_REFRESH_CALLBACKS.append(on_models_changed)

        self.setWindowTitle("Каталог и загрузка моделей (GGUF) — NeonRoute")
        self.setObjectName("GGUFCatalogDialog")
        self.resize(780, 680)
        self.setMinimumSize(660, 560)
        self.setModal(True)

        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.reject)

        self._current_palette: Dict[str, str] = {}
        self.hw_snap = get_system_config_snapshot()
        self.models_dir = resolve_target_models_dir()
        self._current_mode_filter = "all"
        self._only_ideal_fit = True
        self._search_results: Optional[List[Dict[str, Any]]] = None
        self._search_worker: Optional[_HFSearchWorker] = None
        self._cards_by_filename: Dict[str, _ModelCatalogCard] = {}

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(450)
        self._search_debounce.timeout.connect(self._trigger_search)

        self._setup_ui()
        self.update_adaptive_palette()
        self._rebuild_cards()
        self._attach_to_active_download_if_running()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        parent_win = getattr(self, "_parent_ref", None) or self.parent()
        p = parent_win
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = getattr(p, "_parent_ref", None) or (p.parent() if hasattr(p, "parent") else None)
        return None

    def update_adaptive_palette(self):
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            GGUFCatalogDialog#GGUFCatalogDialog,
            GGUFCatalogDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#GGUFCatalogBody {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #GGUFCatalogBody QWidget {{
                background-color: transparent;
            }}
            QFrame#hwBannerCard, QFrame#activeDlBanner {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame.CatalogModelCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame.CatalogModelCard:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QLabel#catalogTitle {{
                color: {palette['group_title']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#catalogSub {{
                color: {palette['text_secondary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
            }}
            LineEdit {{
                background-color: {palette['card']};
                color: {palette['text_primary']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                min-height: 28px;
            }}
            LineEdit:hover, LineEdit:focus {{
                border-color: {palette['accent']};
            }}
            ComboBox {{
                background-color: {palette['bg']};
                color: {palette['text_primary']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11.5px;
                min-height: 24px;
            }}
            PushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-weight: 500;
                font-size: 12px;
                padding: 5px 14px;
                min-height: 20px;
            }}
            PushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PrimaryPushButton {{
                background-color: {palette['accent']};
                border: 1px solid {palette['accent']};
                border-radius: 6px;
                color: #0A0D14;
                font-weight: 600;
                font-size: 12px;
                padding: 5px 16px;
                min-height: 20px;
            }}
            PrimaryPushButton:hover {{
                background-color: {palette['group_title']};
                border-color: {palette['group_title']};
            }}
            PrimaryPushButton:disabled {{
                background-color: rgba(16, 185, 129, 0.18);
                border: 1px solid rgba(16, 185, 129, 0.45);
                color: #10B981;
            }}
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
        """)

    def _setup_ui(self):
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_lay.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("GGUFCatalogBody")
        main_lay = QVBoxLayout(self.body_widget)
        main_lay.setContentsMargins(22, 12, 22, 18)
        main_lay.setSpacing(10)

        hdr_box = QVBoxLayout()
        hdr_box.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        hdr_icon = IconWidget(FIF.DOWNLOAD, self.body_widget)
        hdr_icon.setFixedSize(20, 20)
        title_row.addWidget(hdr_icon)

        lbl_title = QLabel("📦 КАТАЛОГ И ЗАГРУЗКА МОДЕЛЕЙ (GGUF)", self.body_widget)
        lbl_title.setObjectName("catalogTitle")
        title_row.addWidget(lbl_title)
        title_row.addStretch()
        hdr_box.addLayout(title_row)

        lbl_sub = QLabel(
            "Сначала отображаются модели, которые идеально подходят под ваш текущий конфиг GPU и памяти",
            self.body_widget,
        )
        lbl_sub.setObjectName("catalogSub")
        hdr_box.addWidget(lbl_sub)
        main_lay.addLayout(hdr_box)

        self.hw_banner = QFrame(self.body_widget)
        self.hw_banner.setObjectName("hwBannerCard")
        hw_lay = QHBoxLayout(self.hw_banner)
        hw_lay.setContentsMargins(14, 8, 14, 8)
        hw_lay.setSpacing(10)

        gpu_name = self.hw_snap["gpu_name"]
        vram_mb = self.hw_snap["vram_total_mb"]
        usable_mb = self.hw_snap["usable_vram_mb"]
        ram_gb = self.hw_snap["ram_total_mb"] / 1024.0
        kv_type = self.hw_snap["kv_cache_type"].upper()

        if vram_mb > 512:
            hw_txt = (
                f"🖥️ <b>{gpu_name}</b> ({vram_mb} МБ VRAM)  •  "
                f"Доступно под 100% GPU: <b>~{usable_mb / 1024:.1f} ГБ</b> (KV: {kv_type})  •  "
                f"ОЗУ: {ram_gb:.1f} ГБ"
            )
        else:
            hw_txt = f"🖥️ <b>CPU / iGPU режим</b>  •  ОЗУ: <b>{ram_gb:.1f} ГБ</b>  •  Подбор лёгких моделей до ~2.2 ГБ"

        self.lbl_hw_info = BodyLabel(hw_txt, self.hw_banner)
        self.lbl_hw_info.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_hw_info.setStyleSheet("color: #E2E8F0; font-size: 12px;")
        hw_lay.addWidget(self.lbl_hw_info)
        hw_lay.addStretch()

        lbl_folder = CaptionLabel(f"📁 {self.models_dir.name}/", self.hw_banner)
        lbl_folder.setToolTip(f"Папка сохранения моделей: {self.models_dir}")
        lbl_folder.setStyleSheet("color: #38BDF8; font-weight: 600;")
        hw_lay.addWidget(lbl_folder)
        main_lay.addWidget(self.hw_banner)

        self.active_dl_banner = QFrame(self.body_widget)
        self.active_dl_banner.setObjectName("activeDlBanner")
        dl_lay = QHBoxLayout(self.active_dl_banner)
        dl_lay.setContentsMargins(14, 8, 14, 8)
        dl_lay.setSpacing(10)

        self.lbl_active_dl = CaptionLabel("", self.active_dl_banner)
        self.lbl_active_dl.setStyleSheet("color: #00F0FF; font-weight: 600; font-size: 11.5px;")
        dl_lay.addWidget(self.lbl_active_dl, 1)

        self.btn_banner_cancel = PushButton("Остановить", self.active_dl_banner)
        self.btn_banner_cancel.setFixedHeight(26)
        self.btn_banner_cancel.clicked.connect(self._cancel_active_download)
        dl_lay.addWidget(self.btn_banner_cancel)
        self.active_dl_banner.hide()
        main_lay.addWidget(self.active_dl_banner)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_input = LineEdit(self.body_widget)
        self.search_input.setPlaceholderText(
            "🔍 Поиск любой модели на HuggingFace (например: qwen 2.5, llama 3, deepseek, mistral)..."
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.returnPressed.connect(self._trigger_search)
        search_row.addWidget(self.search_input, 1)

        self.btn_search = PrimaryPushButton(FIF.SEARCH, "Найти", self.body_widget)
        self.btn_search.clicked.connect(self._trigger_search)
        search_row.addWidget(self.btn_search)

        self.btn_reset_search = PushButton("Рекомендуемые", self.body_widget)
        self.btn_reset_search.setToolTip("Вернуться к базовой витрине проверенных моделей под ваш ПК")
        self.btn_reset_search.clicked.connect(self._reset_to_curated)
        search_row.addWidget(self.btn_reset_search)

        main_lay.addLayout(search_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self.seg_modes = SegmentedWidget(self.body_widget)
        self.seg_modes.addItem("all", "Все режимы")
        self.seg_modes.addItem("assistant", "💬 Ассистент")
        self.seg_modes.addItem("rp", "🎭 Ролевой чат")
        self.seg_modes.addItem("engineer", "🛠️ Инженер")
        self.seg_modes.setCurrentItem("all")
        self.seg_modes.currentItemChanged.connect(self._on_mode_filter_changed)
        filter_row.addWidget(self.seg_modes)

        filter_row.addStretch(1)

        lbl_ideal = CaptionLabel("Оптимизированные:", self.body_widget)
        lbl_ideal.setStyleSheet("color: #CBD5E1; font-size: 11.5px;")
        lbl_ideal.setToolTip("Показывать только модели, на 100% помещающиеся в VRAM вашей видеокарты")
        filter_row.addWidget(lbl_ideal)

        self.sw_only_ideal = SwitchButton(self.body_widget)
        self.sw_only_ideal.setOnText("Да")
        self.sw_only_ideal.setOffText("Все")
        self.sw_only_ideal.setChecked(True)
        self.sw_only_ideal.setToolTip("Включено: только 100% VRAM (без выгрузки в системную ОЗУ)\nВыключено: все совместимые модели")
        self.sw_only_ideal.checkedChanged.connect(self._on_ideal_toggle_changed)
        filter_row.addWidget(self.sw_only_ideal)

        main_lay.addLayout(filter_row)

        self.lbl_section_status = CaptionLabel("", self.body_widget)
        self.lbl_section_status.setStyleSheet("color: #94A3B8; font-size: 11px;")
        main_lay.addWidget(self.lbl_section_status)

        self.scroll_area = QScrollArea(self.body_widget)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.cards_container = QWidget(self.scroll_area)
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 2, 6, 2)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.scroll_area.setWidget(self.cards_container)

        main_lay.addWidget(self.scroll_area, 1)

        footer_row = QHBoxLayout()
        footer_row.setSpacing(10)
        lbl_foot_hint = CaptionLabel(
            "💡 При закрытии окна активная загрузка продолжится в фоне автоматически.",
            self.body_widget,
        )
        lbl_foot_hint.setStyleSheet("color: #64748B; font-size: 11px;")
        footer_row.addWidget(lbl_foot_hint)
        footer_row.addStretch()

        self.btn_close = PushButton("Готово", self.body_widget)
        self.btn_close.clicked.connect(self.accept)
        footer_row.addWidget(self.btn_close)

        main_lay.addLayout(footer_row)
        root_lay.addWidget(self.body_widget)

    def _get_installed_identifiers(self) -> set:
        installed = set()
        try:
            discovered = ServerConfigResolver.discover_gguf_models()
            for k, p in discovered.items():
                installed.add(k.lower())
                installed.add(p.name.lower())
                installed.add(p.stem.lower())
        except Exception:
            pass
        if self.models_dir.is_dir():
            for f in self.models_dir.glob("*.gguf"):
                if f.is_file():
                    installed.add(f.name.lower())
                    installed.add(f.stem.lower())
        return installed

    def _get_evaluated_curated_models(self) -> List[Dict[str, Any]]:
        vram = int(self.hw_snap.get("vram_total_mb") or 0)
        ram = float(self.hw_snap.get("ram_total_mb") or 16384.0)
        vendor = str(self.hw_snap.get("vendor") or "nvidia")
        is_prim = not bool(self.hw_snap.get("is_pure_compute", False))
        kv_type = str(self.hw_snap.get("kv_cache_type") or "q8_0")

        evaluated: List[Dict[str, Any]] = []
        for m in CURATED_GGUF_MODELS:
            item = dict(m)
            tier, sort_score, badge, detail = evaluate_model_for_hardware(
                float(item["size_mb"]), vram, ram, vendor, is_prim, kv_type
            )
            item["tier"] = tier
            item["sort_score"] = sort_score
            item["hw_badge"] = badge
            item["hw_detail"] = detail
            evaluated.append(item)

        evaluated.sort(key=lambda x: (x["tier"], x["sort_score"]))
        return evaluated

    def _clear_cards_layout(self):
        self._cards_by_filename.clear()
        while self.cards_layout.count() > 0:
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild_cards(self):
        self._clear_cards_layout()
        installed = self._get_installed_identifiers()

        source_list = (
            self._search_results
            if self._search_results is not None
            else self._get_evaluated_curated_models()
        )

        filtered: List[Dict[str, Any]] = []
        for item in source_list:
            if self._current_mode_filter != "all":
                if self._current_mode_filter not in item.get("modes", []):
                    continue
            if self._only_ideal_fit and int(item.get("tier", 0)) > 0:
                continue
            filtered.append(item)

        relaxed_notice = ""
        if not filtered and source_list and self._only_ideal_fit:
            filtered = [
                it for it in source_list
                if (self._current_mode_filter == "all" or self._current_mode_filter in it.get("modes", []))
                and int(it.get("tier", 0)) <= 1
            ]
            if filtered:
                relaxed_notice = " (показаны модели с частичной выгрузкой в ОЗУ)"

        if self._search_results is not None:
            q_txt = self.search_input.text().strip()
            self.lbl_section_status.setText(
                f"🔎 Результаты поиска по запросу «{q_txt}»: подходящих моделей — {len(filtered)}{relaxed_notice}"
            )
        else:
            mode_title = MODE_LABELS.get(self._current_mode_filter, "всех режимов")
            self.lbl_section_status.setText(
                f"✨ Подобрано для вашей системы ({mode_title}): {len(filtered)} мод.{relaxed_notice}"
            )

        if not filtered:
            empty_lbl = BodyLabel(
                "По выбранному фильтру подходящих моделей не найдено. "
                "Попробуйте отключить фильтр «Оптимизированные» или изменить запрос.",
                self.cards_container,
            )
            empty_lbl.setWordWrap(True)
            empty_lbl.setStyleSheet("color: #94A3B8; padding: 24px;")
            self.cards_layout.addWidget(empty_lbl)
        else:
            for item in filtered:
                card = _ModelCatalogCard(
                    model_data=item,
                    palette=self._current_palette,
                    installed_files=installed,
                    models_dir=self.models_dir,
                    parent=self.cards_container,
                )
                card.download_requested.connect(self._start_model_download)
                card.cancel_requested.connect(self._cancel_active_download)
                self._cards_by_filename[item["filename"]] = card
                self.cards_layout.addWidget(card)

        self.cards_layout.addStretch()
        self._sync_active_download_to_cards()

    def _on_mode_filter_changed(self, route_key: str):
        self._current_mode_filter = route_key or "all"
        self._rebuild_cards()

    def _on_ideal_toggle_changed(self, checked: bool):
        self._only_ideal_fit = bool(checked)
        self._rebuild_cards()

    def _on_search_text_changed(self, text: str):
        if not text.strip():
            self._search_debounce.stop()
            if self._search_results is not None:
                self._search_results = None
                self._rebuild_cards()
            return
        self._search_debounce.start()

    def _reset_to_curated(self):
        self._search_debounce.stop()
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self._search_results = None
        self._rebuild_cards()

    def _trigger_search(self):
        self._search_debounce.stop()
        query = self.search_input.text().strip()
        if not query:
            self._reset_to_curated()
            return

        if self._search_worker is not None:
            try:
                self._search_worker.results_ready.disconnect(self._on_search_results_ready)
            except Exception:
                pass
            if self._search_worker.isRunning():
                self._search_worker.abort()

        self.btn_search.setEnabled(False)
        self.btn_search.setText("Поиск...")
        self.lbl_section_status.setText(f"⏳ Поиск GGUF-моделей «{query}» на HuggingFace и проверка размеров под ваш ПК...")

        self._search_worker = _HFSearchWorker(query=query, hw_snap=self.hw_snap, parent=None)
        self._search_worker.results_ready.connect(self._on_search_results_ready)
        self._search_worker.start()

    def _on_search_results_ready(self, query: str, items: list, err_msg: str):
        self.btn_search.setEnabled(True)
        self.btn_search.setText("Найти")
        if err_msg:
            toast_warning(self, "Поиск HuggingFace", f"Не удалось выполнить поиск: {err_msg}")
            return
        self._search_results = list(items)
        self._rebuild_cards()

    def _start_model_download(self, model_data: Dict[str, Any]):
        global _ACTIVE_DOWNLOAD_WORKER
        active = get_active_download_worker()
        if active is not None:
            toast_warning(
                self,
                "Загрузка уже идёт",
                f"Сейчас уже скачивается «{active.model_title}». Дождитесь завершения или нажмите «Остановить».",
            )
            return

        repo_id = model_data["repo_id"]
        filename = model_data["filename"]
        size_mb = float(model_data.get("size_mb", 0.0))
        title = model_data.get("title", filename)

        dest_path = self.models_dir / Path(filename).name
        part_path = dest_path.with_suffix(dest_path.suffix + ".part")
        already_bytes = part_path.stat().st_size if part_path.is_file() else 0
        needed_bytes = max(0, int(size_mb * 1024 * 1024) - already_bytes) + (300 * 1024 * 1024)

        try:
            usage = shutil.disk_usage(str(self.models_dir))
            if usage.free < needed_bytes:
                free_gb = usage.free / (1024 ** 3)
                req_gb = needed_bytes / (1024 ** 3)
                toast_error(
                    self,
                    "Недостаточно места на диске",
                    f"Свободно {free_gb:.1f} ГБ, а для безопасной загрузки требуется ~{req_gb:.1f} ГБ.",
                )
                return
        except Exception as e:
            logger.debug(f"[GGUFCatalog] disk_usage check skipped: {e}")

        url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
        worker = _GGUFDownloadWorker(url=url, dest_path=dest_path, model_title=title)
        worker.finished_signal.connect(_on_global_download_finished)
        _ACTIVE_DOWNLOAD_WORKER = worker

        self._connect_worker_signals(worker)
        worker.start()
        self._sync_active_download_to_cards()

    def _connect_worker_signals(self, worker: _GGUFDownloadWorker):
        try:
            worker.progress_signal.disconnect(self._on_worker_progress)
        except Exception:
            pass
        try:
            worker.finished_signal.disconnect(self._on_worker_finished_ui)
        except Exception:
            pass
        worker.progress_signal.connect(self._on_worker_progress)
        worker.finished_signal.connect(self._on_worker_finished_ui)

    def _disconnect_worker_signals(self, worker: Optional[_GGUFDownloadWorker]):
        if worker is None:
            return
        try:
            worker.progress_signal.disconnect(self._on_worker_progress)
        except Exception:
            pass
        try:
            worker.finished_signal.disconnect(self._on_worker_finished_ui)
        except Exception:
            pass

    def _attach_to_active_download_if_running(self):
        active = get_active_download_worker()
        if active is not None:
            self._connect_worker_signals(active)
            self._sync_active_download_to_cards()

    def _sync_active_download_to_cards(self):
        active = get_active_download_worker()
        if active is None:
            self.active_dl_banner.hide()
            return

        self.active_dl_banner.show()
        self._on_worker_progress(
            active.filename,
            active.last_downloaded,
            active.last_total,
            active.last_speed_mb,
        )

    def _on_worker_progress(self, filename: str, cur: int, tot: int, speed_mb: float):
        dl_mb = cur / (1024.0 * 1024.0)
        if tot > 0:
            tot_mb = tot / (1024.0 * 1024.0)
            pct = min(100, int((cur / tot) * 100))
            self.lbl_active_dl.setText(
                f"⏳ Скачивание {filename}: {pct}% ({dl_mb:.0f} / {tot_mb:.0f} МБ • {speed_mb:.1f} МБ/с)"
            )
        else:
            self.lbl_active_dl.setText(f"⏳ Скачивание {filename}: {dl_mb:.0f} МБ ({speed_mb:.1f} МБ/с)")

        card = self._cards_by_filename.get(filename)
        if card is not None:
            card.set_downloading_state(cur, tot, speed_mb)

    def _on_worker_finished_ui(self, success: bool, filename: str, dest_path_str: str, message: str):
        self.active_dl_banner.hide()
        self._rebuild_cards()
        if success and dest_path_str:
            self.model_downloaded.emit(dest_path_str)

    def _cancel_active_download(self):
        active = get_active_download_worker()
        if active is not None:
            active.cancel()

    def _cleanup_on_close(self):
        if self._search_worker is not None:
            try:
                self._search_worker.results_ready.disconnect(self._on_search_results_ready)
            except Exception:
                pass
            if self._search_worker.isRunning():
                self._search_worker.abort()

        if self._on_models_changed and self._on_models_changed in _GLOBAL_REFRESH_CALLBACKS:
            try:
                _GLOBAL_REFRESH_CALLBACKS.remove(self._on_models_changed)
            except ValueError:
                pass

        active = get_active_download_worker()
        if active is not None:
            self._disconnect_worker_signals(active)
            if "PYTEST_CURRENT_TEST" not in os.environ:
                parent_win = getattr(self, "_parent_ref", None) or self.parent() or QApplication.activeWindow()
                if parent_win is not None and isinstance(parent_win, QWidget):
                    toast_info(
                        parent_win,
                        "Загрузка продолжается в фоне",
                        f"Модель «{active.model_title}» продолжает скачиваться. Откройте каталог снова для просмотра прогресса.",
                    )

    def reject(self):
        self._cleanup_on_close()
        super().reject()

    def accept(self):
        self._cleanup_on_close()
        super().accept()

    def closeEvent(self, event):
        self._cleanup_on_close()
        super().closeEvent(event)
