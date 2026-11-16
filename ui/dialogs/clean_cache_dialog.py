"""
Clean Cache and Temporary Files Dialog for NeonRoute
Calculates disk usage and safely cleans Forge/Gradio cache, app logs, and generated outputs
using background QThreads without blocking the PyQt6 GUI event loop.
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QCursor
import json
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QPushButton, QProgressBar, QMessageBox, QFrame, QWidget
)

from config import (
    FORGE_ROOT_DIR, HISTORY_DIR, BASE_DIR, LOGS_DIR, DATA_DIR,
    OUTPUTS_DIR, BACKGROUNDS_DIR, CONFIG_PATH, WORKSPACE_DIR,
    DEFAULT_BACKGROUND_PATH, CANONICAL_BACKGROUND_NAME
)

logger = logging.getLogger("NeonRoute.CleanCacheDialog")


def get_protected_background_paths() -> set[Path]:
    """
    Returns a set of resolved absolute Paths for backgrounds that must not be deleted:
    - Default application background (DEFAULT_BACKGROUND_PATH) and canonical file
    - Active background from config.json ('background_path')
    - Any background_image or background_path referenced in any chat history JSON
    """
    protected = set()

    # 1. Защита стандартного системного фона
    try:
        if DEFAULT_BACKGROUND_PATH.exists():
            protected.add(DEFAULT_BACKGROUND_PATH.resolve())
        canonical_p = BACKGROUNDS_DIR / CANONICAL_BACKGROUND_NAME
        if canonical_p.exists():
            protected.add(canonical_p.resolve())
    except Exception as e:
        logger.warning(f"Error resolving default protected backgrounds: {e}")

    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                bg_cfg = cfg.get("background_path")
                if bg_cfg:
                    p = Path(bg_cfg)
                    if not p.is_absolute():
                        p = BASE_DIR / p
                    if p.exists():
                        protected.add(p.resolve())
    except Exception as e:
        logger.warning(f"Error reading config.json for protected backgrounds: {e}")

    try:
        if HISTORY_DIR.exists():
            for chat_file in HISTORY_DIR.glob("chat_*.json"):
                try:
                    with open(chat_file, "r", encoding="utf-8") as f:
                        session_data = json.load(f)
                    for key in ("background_path", "background_image"):
                        bg_val = session_data.get(key)
                        if bg_val:
                            p = Path(bg_val)
                            if not p.is_absolute():
                                p = BASE_DIR / p
                            if p.exists():
                                protected.add(p.resolve())
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Error scanning history for protected backgrounds: {e}")

    return protected


def _get_orphan_cast_files() -> List[Path]:
    """Finds cast_*.json files in history/ that do not have a matching chat_*.json."""
    orphans = []
    try:
        if HISTORY_DIR.exists():
            for cast_file in HISTORY_DIR.glob("cast_*.json"):
                stem = cast_file.stem
                s_id = stem[5:] if stem.startswith("cast_") else ""
                if s_id and not (HISTORY_DIR / f"chat_{s_id}.json").exists():
                    orphans.append(cast_file)
    except Exception as e:
        logger.warning(f"Error finding orphan cast files: {e}")
    return orphans


def _get_orphan_workspace_project_dirs() -> List[Path]:
    """Finds project folders in WORKSPACE_DIR / 'projects' without matching chat_<id>.json in HISTORY_DIR."""
    orphans = []
    try:
        projects_dir = WORKSPACE_DIR / "projects"
        if projects_dir.exists() and projects_dir.is_dir():
            for p in projects_dir.iterdir():
                if p.is_dir():
                    s_id = p.name
                    if not (HISTORY_DIR / f"chat_{s_id}.json").exists():
                        orphans.append(p)
    except Exception as e:
        logger.warning(f"Error finding orphan workspace projects: {e}")
    return orphans


def format_size(size_bytes: int) -> str:
    """Formats bytes into human-readable B, KB, MB, GB."""
    if size_bytes <= 0:
        return "0 MB"
    val = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if val < 1024.0 or unit == 'TB':
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} GB"


def get_dir_size(path: Path) -> int:
    """Calculates total size in bytes of a directory recursively."""
    total = 0
    if not path.exists():
        return 0
    try:
        if path.is_file():
            return path.stat().st_size
        for root, dirs, files in os.walk(str(path)):
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    total += os.path.getsize(fp)
                except (OSError, PermissionError):
                    pass
    except Exception as e:
        logger.debug(f"Error calculating size for {path}: {e}")
    return total


class CacheScanWorker(QThread):
    """Calculates disk sizes for cache categories in background."""
    scan_completed = pyqtSignal(dict)  # category_key -> size_in_bytes

    def __init__(self, targets: Dict[str, List[Path]], parent=None):
        super().__init__(parent)
        self.targets = targets

    def run(self):
        try:
            results = {}
            protected_bgs = get_protected_background_paths()
            for key, paths in self.targets.items():
                total = 0
                if key == "backgrounds":
                    for p in paths:
                        if p.is_file() and p.resolve() not in protected_bgs and p.name not in (CANONICAL_BACKGROUND_NAME, "default_background.jpg"):
                            total += get_dir_size(p)
                        elif p.is_dir():
                            for f in p.rglob("*"):
                                if f.is_file() and f.resolve() not in protected_bgs and f.name not in (CANONICAL_BACKGROUND_NAME, "default_background.jpg"):
                                    total += get_dir_size(f)
                else:
                    for p in paths:
                        total += get_dir_size(p)
                results[key] = total
            self.scan_completed.emit(results)
        except Exception as e:
            logger.error(f"Error in CacheScanWorker: {e}", exc_info=True)
            self.scan_completed.emit({})



class CacheCleanWorker(QThread):
    """Deletes selected cache files and directories in background."""
    progress_updated = pyqtSignal(str)
    clean_completed = pyqtSignal(int, list)  # freed_bytes, list of cleared description strings

    def __init__(self, selected_targets: Dict[str, List[Path]], parent=None):
        super().__init__(parent)
        self.selected_targets = selected_targets

    def run(self):
        total_freed = 0
        cleared_summary = []
        protected_bgs = get_protected_background_paths()

        for category, paths in self.selected_targets.items():
            self.progress_updated.emit(f"Очистка: {category}...")
            cat_freed = 0

            if "backgrounds" in category.lower() or "обои" in category.lower():
                for p in paths:
                    if not p.exists():
                        continue
                    files_to_check = [p] if p.is_file() else list(p.rglob("*"))
                    for f in files_to_check:
                        if f.is_file() and f.resolve() not in protected_bgs and f.name not in (CANONICAL_BACKGROUND_NAME, "default_background.jpg"):
                            try:
                                sz = f.stat().st_size
                                f.unlink(missing_ok=True)
                                cat_freed += sz
                            except Exception as e:
                                logger.warning(f"Failed to delete background {f}: {e}")
                total_freed += cat_freed
                cleared_summary.append(f"{category} ({format_size(cat_freed)})")
                continue

            for p in paths:
                if not p.exists():
                    continue

                cat_freed += get_dir_size(p)

                try:
                    if p.is_file():
                        p.unlink(missing_ok=True)
                    elif p.is_dir():
                        # Delete contents or entire dir and recreate empty
                        current_active_log = (LOGS_DIR / "neonroute.log").resolve()
                        for item in p.iterdir():
                            if item.resolve() == current_active_log:
                                try:
                                    with open(item, "r+", encoding="utf-8") as f_trunc:
                                        f_trunc.truncate(0)
                                except (PermissionError, OSError):
                                    pass
                                continue
                            try:
                                if item.is_file() or item.is_symlink():
                                    item.unlink(missing_ok=True)
                                elif item.is_dir():
                                    shutil.rmtree(item, ignore_errors=True)
                            except (PermissionError, OSError) as e:
                                logger.warning(f"Файл заблокирован процессом Windows, пропуск: {item} ({e})")
                except Exception as e:
                    logger.error(f"Error cleaning {p}: {e}")

            total_freed += cat_freed
            cleared_summary.append(f"{category} ({format_size(cat_freed)})")

        # Компактная очистка и оптимизация версий LanceDB
        try:
            self.progress_updated.emit("Оптимизация базы векторов LanceDB...")
            from services.memory_db import _get_embedding_engine, _LANCE_LOCK
            embed_model, facts_t, rp_t = _get_embedding_engine()
            for tbl in (facts_t, rp_t):
                if tbl:
                    with _LANCE_LOCK:
                        try:
                            if hasattr(tbl, "compact_files"):
                                tbl.compact_files()
                        except Exception as e:
                            logger.debug(f"[CleanCache] compact_files error: {e}")
                        try:
                            if hasattr(tbl, "cleanup_old_versions"):
                                tbl.cleanup_old_versions()
                        except Exception as e:
                            logger.debug(f"[CleanCache] cleanup_old_versions error: {e}")

            # Если в HISTORY_DIR нет ни одного РП-чата — полная очистка таблицы rp_memories
            has_rp_chats = False
            if HISTORY_DIR.exists():
                for cf in HISTORY_DIR.glob("chat_*.json"):
                    try:
                        with open(cf, "r", encoding="utf-8") as f_chat:
                            c_data = json.load(f_chat)
                            if c_data.get("mode") == "roleplay":
                                has_rp_chats = True
                                break
                    except Exception:
                        pass

            if not has_rp_chats:
                import lancedb
                from config import DATA_DIR
                lance_path = DATA_DIR / "vector_memory"
                with _LANCE_LOCK:
                    db = lancedb.connect(str(lance_path))
                    tables = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
                    if "rp_memories" in tables:
                        try:
                            # Удаление директории таблицы или drop_table
                            rp_dir = lance_path / "rp_memories.lance"
                            if rp_dir.exists():
                                shutil.rmtree(rp_dir, ignore_errors=True)
                            else:
                                db.drop_table("rp_memories")
                        except Exception as e_drop:
                            logger.debug(f"[CleanCache] Drop table error: {e_drop}")

                    if embed_model:
                        try:
                            dummy_vec = list(embed_model.embed(["init"]))[0].tolist()
                            db.create_table(
                                "rp_memories",
                                data=[{
                                    "id": "init_id",
                                    "session_id": "init_sess",
                                    "text": "init",
                                    "vector": dummy_vec,
                                    "timestamp": "0"
                                }]
                            )
                        except Exception as e_cr:
                            logger.debug(f"[CleanCache] Recreate table error: {e_cr}")

                cleared_summary.append("Векторная память РП (LanceDB сброшена)")
        except Exception as e_lance:
            logger.debug(f"[CleanCache] Ошибка очистки LanceDB: {e_lance}")

        from ui.components.chat_bubble import _IMAGE_CACHE_LOCK, _IMAGE_PREVIEW_CACHE
        from PyQt6.QtGui import QPixmapCache
        with _IMAGE_CACHE_LOCK:
            _IMAGE_PREVIEW_CACHE.clear()
        QPixmapCache.clear()

        self.clean_completed.emit(total_freed, cleared_summary)


class CleanCacheDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Очистка кэша и временных файлов — NeonRoute")
        self.resize(520, 420)
        self.setMinimumWidth(460)

        # Style with Cyberpunk dark theme
        self.setStyleSheet("""
            QDialog {
                background-color: #0A0D14;
                color: #E2E8F0;
                border: 1px solid #1E283D;
            }
            QLabel {
                color: #E2E8F0;
            }
            QCheckBox {
                color: #E2E8F0;
                font-size: 13px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #1E3A5F;
                border-radius: 4px;
                background-color: #121824;
            }
            QCheckBox::indicator:checked {
                background-color: #00F0FF;
                border: 1px solid #00F0FF;
                image: none;
            }
            QProgressBar {
                border: 1px solid #1E283D;
                border-radius: 4px;
                background-color: #121824;
                text-align: center;
                color: #00F0FF;
            }
            QProgressBar::chunk {
                background-color: #00F0FF;
            }
            QPushButton {
                background-color: #162033;
                border: 1px solid #1E3A5F;
                color: #E2E8F0;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1E2E4A;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#BtnClean {
                background-color: #7F1D1D;
                border: 1px solid #EF4444;
                color: #FCA5A5;
            }
            QPushButton#BtnClean:hover {
                background-color: #DC2626;
                color: #FFFFFF;
            }
        """)

        # Define targets
        from config import BACKGROUNDS_DIR
        self.target_paths: Dict[str, List[Path]] = {
            "forge_cache": [
                FORGE_ROOT_DIR / ".tmp",
                FORGE_ROOT_DIR / ".cache"
            ],
            "app_logs": [
                LOGS_DIR,
                BASE_DIR / "neonroute.log",
                *list(HISTORY_DIR.glob("*.tmp")),
                *list(HISTORY_DIR.glob("*.bak")),
                *_get_orphan_cast_files(),
                WORKSPACE_DIR / "_archive",
                WORKSPACE_DIR / "broken.py",
                *_get_orphan_workspace_project_dirs()
            ],
            "chub_cache": [
                DATA_DIR / "cache" / "chub_avatars",
                DATA_DIR / "cache" / "civitai_thumbs",
                DATA_DIR / "cache" / "gallery_thumbs"
            ],
            "backgrounds": [
                BACKGROUNDS_DIR
            ],
            "outputs": [
                OUTPUTS_DIR
            ]
        }

        self.category_sizes: Dict[str, int] = {}
        self.scan_worker: Optional[CacheScanWorker] = None
        self.clean_worker: Optional[CacheCleanWorker] = None

        self._setup_ui()
        self._start_size_scan()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Header Title
        title_lbl = QLabel("🧹 Очистка временных файлов и кэша", self)
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #00F0FF;")
        main_layout.addWidget(title_lbl)

        desc_lbl = QLabel(
            "Выберите категории данных для безопасного удаления и освобождения места на диске.",
            self
        )
        desc_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        desc_lbl.setWordWrap(True)
        main_layout.addWidget(desc_lbl)

        # Container for Checkboxes
        card = QFrame(self)
        card.setStyleSheet("""
            QFrame {
                background-color: #101622;
                border: 1px solid #1E283D;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)

        # 1. Forge / Gradio Cache (Checked by default)
        row1 = QHBoxLayout()
        self.chk_forge = QCheckBox(f"Временный кэш Forge и Gradio ({FORGE_ROOT_DIR / '.tmp'}, .cache)", card)
        self.chk_forge.setChecked(True)
        row1.addWidget(self.chk_forge, stretch=1)
        self.lbl_size_forge = QLabel("⏳ Подсчет...", card)
        self.lbl_size_forge.setStyleSheet("color: #38BDF8; font-weight: bold;")
        row1.addWidget(self.lbl_size_forge)
        card_layout.addLayout(row1)

        # 2. App logs & temp previews (Checked by default)
        row2 = QHBoxLayout()
        self.chk_logs = QCheckBox("Логи приложения и временные аудиофайлы (logs, temp)", card)
        self.chk_logs.setChecked(True)
        row2.addWidget(self.chk_logs, stretch=1)
        self.lbl_size_logs = QLabel("⏳ Подсчет...", card)
        self.lbl_size_logs.setStyleSheet("color: #38BDF8; font-weight: bold;")
        row2.addWidget(self.lbl_size_logs)
        card_layout.addLayout(row2)

        # 3. Chub.ai avatar cache (Checked by default)
        row_chub = QHBoxLayout()
        self.chk_chub = QCheckBox("Кэш превью каталога Chub.ai (data\\cache\\chub_avatars)", card)
        self.chk_chub.setChecked(True)
        row_chub.addWidget(self.chk_chub, stretch=1)
        self.lbl_size_chub = QLabel("⏳ Подсчет...", card)
        self.lbl_size_chub.setStyleSheet("color: #38BDF8; font-weight: bold;")
        row_chub.addWidget(self.lbl_size_chub)
        card_layout.addLayout(row_chub)

        # 4. Unused backgrounds (Checked by default)
        row_bg = QHBoxLayout()
        self.chk_backgrounds = QCheckBox("Неиспользуемые обои рабочего пространства (backgrounds, кроме активного фона)", card)
        self.chk_backgrounds.setChecked(True)
        row_bg.addWidget(self.chk_backgrounds, stretch=1)
        self.lbl_size_backgrounds = QLabel("⏳ Подсчет...", card)
        self.lbl_size_backgrounds.setStyleSheet("color: #38BDF8; font-weight: bold;")
        row_bg.addWidget(self.lbl_size_backgrounds)
        card_layout.addLayout(row_bg)

        # 5. Generated Images Outputs (UNCHECKED by default for safety!)
        row3 = QHBoxLayout()
        self.chk_outputs = QCheckBox(f"Сгенерированные изображения ({OUTPUTS_DIR})", card)
        self.chk_outputs.setChecked(False)
        self.chk_outputs.setToolTip("Внимание: удалит все ранее сгенерированные изображения в папке outputs!")
        row3.addWidget(self.chk_outputs, stretch=1)
        self.lbl_size_outputs = QLabel("⏳ Подсчет...", card)
        self.lbl_size_outputs.setStyleSheet("color: #F87171; font-weight: bold;")
        row3.addWidget(self.lbl_size_outputs)
        card_layout.addLayout(row3)

        main_layout.addWidget(card)

        # Status and Progress Bar
        self.lbl_status = QLabel("Подсчет занятого дискового пространства...", self)
        self.lbl_status.setStyleSheet("color: #64748B; font-size: 11px;")
        main_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setRange(0, 0)  # indeterminate animation during scan
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Bottom Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_clean = QPushButton("🗑️ Очистить выбранное", self)
        self.btn_clean.setObjectName("BtnClean")
        self.btn_clean.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_clean.clicked.connect(self._on_clean_clicked)
        btn_layout.addWidget(self.btn_clean)

        main_layout.addLayout(btn_layout)

    def _start_size_scan(self):
        """Launches non-blocking size calculation."""
        self.progress_bar.setVisible(True)
        self.scan_worker = CacheScanWorker(self.target_paths, self)
        self.scan_worker.scan_completed.connect(self._on_scan_completed)
        self.scan_worker.start()

    def _on_scan_completed(self, sizes: dict):
        self.category_sizes = sizes
        self.progress_bar.setVisible(False)
        self.lbl_status.setText("Подсчет завершен. Выберите элементы и нажмите 'Очистить'.")

        self.lbl_size_forge.setText(format_size(sizes.get("forge_cache", 0)))
        self.lbl_size_logs.setText(format_size(sizes.get("app_logs", 0)))
        self.lbl_size_chub.setText(format_size(sizes.get("chub_cache", 0)))
        self.lbl_size_backgrounds.setText(format_size(sizes.get("backgrounds", 0)))
        self.lbl_size_outputs.setText(format_size(sizes.get("outputs", 0)))

    def _on_clean_clicked(self):
        # Prepare selected targets
        selected: Dict[str, List[Path]] = {}
        if self.chk_forge.isChecked():
            selected["Кэш Forge/Gradio"] = self.target_paths["forge_cache"]
        if self.chk_logs.isChecked():
            # For history dir, only clean temporary .tmp and .bak files and orphan cast files, never chat_*.json
            paths_to_clean = [
                LOGS_DIR,
                BASE_DIR / "neonroute.log",
                *list(HISTORY_DIR.glob("*.tmp")),
                *list(HISTORY_DIR.glob("*.bak")),
                *_get_orphan_cast_files()
            ]
            selected["Логи и временные файлы"] = paths_to_clean
        if self.chk_chub.isChecked():
            selected["Кэш каталога Chub.ai"] = self.target_paths["chub_cache"]
        if self.chk_backgrounds.isChecked():
            selected["Неиспользуемые обои рабочего пространства"] = self.target_paths["backgrounds"]
        if self.chk_outputs.isChecked():
            # Extra confirmation if deleting user generated images
            reply = QMessageBox.warning(
                self,
                "Подтверждение удаления изображений",
                f"Вы выбрали удаление всех сгенерированных изображений из {OUTPUTS_DIR}.\n\n"
                "Вы уверены, что хотите удалить их?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            selected["Сгенерированные изображения"] = self.target_paths["outputs"]

        if not selected:
            QMessageBox.information(self, "Очистка", "Не выбрано ни одной категории для очистки.")
            return

        # Disable buttons and start progress
        self.btn_clean.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.chk_forge.setEnabled(False)
        self.chk_logs.setEnabled(False)
        self.chk_chub.setEnabled(False)
        self.chk_backgrounds.setEnabled(False)
        self.chk_outputs.setEnabled(False)

        self.progress_bar.setVisible(True)
        self.lbl_status.setText("Выполняется безопасное удаление файлов...")

        self.clean_worker = CacheCleanWorker(selected, self)
        self.clean_worker.progress_updated.connect(lambda msg: self.lbl_status.setText(msg))
        self.clean_worker.clean_completed.connect(self._on_clean_completed)
        self.clean_worker.start()

    def _on_clean_completed(self, freed_bytes: int, summary: list):
        self.progress_bar.setVisible(False)
        self.lbl_status.setText("Очистка успешно завершена.")

        freed_str = format_size(freed_bytes)
        summary_text = "\n• " + "\n• ".join(summary) if summary else ""

        QMessageBox.information(
            self,
            "Очистка завершена",
            f"Очистка успешно выполнена!\nОсвобождено дискового пространства: {freed_str}{summary_text}"
        )
        self.accept()
