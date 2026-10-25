"""
Civitai / Civitai Red Catalog Browser & Chunked Downloader for NeonRoute.
Provides responsive grid browsing, tag/architecture filtering (SD 1.5 / SDXL / Pony),
and safe background streaming downloads directly into SD Forge models directory.
"""

import hashlib
import io
import time
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QPainterPath, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QListWidget, QListWidgetItem,
    QTextEdit, QFrame, QSplitter, QMessageBox, QProgressBar, QWidget
)

from config import DATA_DIR

CIVITAI_CACHE_DIR = DATA_DIR / "cache" / "civitai_thumbs"
CIVITAI_CACHE_DIR.mkdir(parents=True, exist_ok=True)

CIVITAI_API_BASE = "https://civitai.com/api/v1"


def render_civitai_poster(
    image_path: Optional[str] = None,
    name: str = "?",
    width: int = 130,
    height: int = 150,
    radius: int = 8
) -> QPixmap:
    """Генерирует вертикальный постер модели со скругленными углами."""
    pix = QPixmap(width, height)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(width), float(height), float(radius), float(radius))
    painter.setClipPath(path)

    loaded = False
    if image_path:
        p = Path(image_path)
        if p.exists() and p.is_file():
            src_pix = QPixmap(str(p))
            if not src_pix.isNull():
                scaled = src_pix.scaled(
                    width, height,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                x = (scaled.width() - width) // 2
                y = (scaled.height() - height) // 2
                painter.drawPixmap(0, 0, scaled, x, y, width, height)
                loaded = True

    if not loaded:
        painter.fillRect(0, 0, width, height, QColor("#121826"))
        initials = (name[:2] if name else "?").upper().strip()
        painter.setPen(QColor("#00F0FF"))
        font = painter.font()
        font.setPointSize(max(13, int(height * 0.18)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, 0, width, height, Qt.AlignmentFlag.AlignCenter, initials)

    painter.setClipping(False)
    painter.setPen(QColor("rgba(0, 240, 255, 0.35)"))
    painter.drawRoundedRect(0, 0, width - 1, height - 1, radius, radius)
    painter.end()
    return pix


class CivitaiSearchWorker(QThread):
    results_ready = pyqtSignal(list, int, int, object)  # cards, total_pages, total_items, next_cursor
    error_occurred = pyqtSignal(str)

    def __init__(self, query: str, model_type: Optional[str], base_model: Optional[str], sort: str, nsfw: bool, page: int, cursor: Optional[Any] = None):
        super().__init__()
        self.query = query.strip()
        self.model_type = model_type
        self.base_model = base_model
        self.sort = sort
        self.nsfw = nsfw
        self.page = max(1, page)
        self.cursor = cursor
        self._is_aborted = False

    def abort(self):
        self._is_aborted = True
        self.requestInterruption()

    def run(self):
        if self._is_aborted or self.isInterruptionRequested():
            return

        url = f"{CIVITAI_API_BASE}/models"
        params: Dict[str, Any] = {
            "limit": 20,
        }
        if self.sort:
            params["sort"] = self.sort
            if self.sort in ("Most Downloaded", "Highest Rated"):
                params["period"] = "AllTime"

        # Civitai строго запрещает 'page' при наличии 'query':
        if self.query:
            params["query"] = self.query
            if self.cursor is not None:
                params["cursor"] = self.cursor
        else:
            params["page"] = self.page

        if self.model_type:
            params["types"] = self.model_type
        if self.base_model:
            params["baseModels"] = self.base_model

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }

        if self._is_aborted or self.isInterruptionRequested():
            return

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=14.0)
            if self._is_aborted or self.isInterruptionRequested():
                return
            if resp.status_code != 200:
                err_detail = ""
                try:
                    err_json = resp.json()
                    err_detail = err_json.get("message") or err_json.get("error") or str(err_json)
                except Exception:
                    err_detail = resp.text[:200]
                raise RuntimeError(f"Civitai вернул HTTP {resp.status_code}: {err_detail}")
            data = resp.json()
        except requests.exceptions.RequestException as e:
            if not (self._is_aborted or self.isInterruptionRequested()):
                self.error_occurred.emit(f"Ошибка подключения: {e}")
            return
        except Exception as e:
            if not (self._is_aborted or self.isInterruptionRequested()):
                self.error_occurred.emit(f"Ошибка API Civitai: {e}")
            return

        if self._is_aborted or self.isInterruptionRequested():
            return

        items = data.get("items") or []
        meta = data.get("metadata") or {}
        next_cursor = meta.get("nextCursor")
        total_pages = meta.get("totalPages", 1) if not self.query else 0
        total_items = meta.get("totalItems", len(items)) if not self.query else len(items)

        cards = []
        for it in items:
            if self._is_aborted or self.isInterruptionRequested():
                return
            if not isinstance(it, dict):
                continue

            # Клиентский фильтр 18+: если чекбокс выключен, скрываем NSFW
            if not self.nsfw and it.get("nsfw", False):
                continue

            versions = it.get("modelVersions") or []
            primary_ver = versions[0] if versions else {}
            files = primary_ver.get("files") or []
            primary_file = next((f for f in files if f.get("primary")), files[0] if files else {})

            images = primary_ver.get("images") or []
            img_url = images[0].get("url") if images else None
            gen_meta = images[0].get("meta") if (images and isinstance(images[0].get("meta"), dict)) else {}

            size_kb = primary_file.get("sizeKB") or 0
            size_mb = size_kb / 1024.0

            file_hashes = primary_file.get("hashes") if isinstance(primary_file.get("hashes"), dict) else {}
            expected_sha256 = file_hashes.get("SHA256") or file_hashes.get("sha256")

            cards.append({
                "id": str(it.get("id", "")),
                "name": it.get("name", "Без названия").strip(),
                "type": it.get("type", "Checkpoint"),
                "nsfw": it.get("nsfw", False),
                "tags": it.get("tags") or [],
                "version_name": primary_ver.get("name", "v1.0"),
                "version_id": str(primary_ver.get("id", "")),
                "base_model": primary_ver.get("baseModel", "SD 1.5"),
                "trained_words": primary_ver.get("trainedWords") or [],
                "filename": primary_file.get("name", f"model_{it.get('id')}.safetensors"),
                "size_mb": size_mb,
                "download_url": primary_file.get("downloadUrl", ""),
                "sha256": expected_sha256,
                "preview_url": img_url,
                "meta": gen_meta,
                "description": it.get("description") or ""
            })

        if not (self._is_aborted or self.isInterruptionRequested()):
            self.results_ready.emit(cards, total_pages, total_items, next_cursor)


class CivitaiThumbnailsBatchWorker(QThread):
    thumb_loaded = pyqtSignal(int, str)

    def __init__(self, cards: List[Dict[str, Any]]):
        super().__init__()
        self.cards = list(cards)
        self._is_aborted = False
        self._executor: Optional[ThreadPoolExecutor] = None

    def abort(self):
        self._is_aborted = True
        self.requestInterruption()
        if self._executor:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def _fetch_single(self, idx: int, url: Optional[str]):
        if self._is_aborted or self.isInterruptionRequested() or not url:
            return
        cache_key = hashlib.md5(url.encode("utf-8")).hexdigest()
        cache_path = CIVITAI_CACHE_DIR / f"{cache_key}.png"

        if not cache_path.exists():
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = requests.get(url, headers=headers, timeout=6.0)
                if resp.status_code == 200 and len(resp.content) > 300:
                    img = Image.open(io.BytesIO(resp.content))
                    img.save(cache_path, format="PNG")
            except Exception:
                return

        if cache_path.exists() and not (self._is_aborted or self.isInterruptionRequested()):
            self.thumb_loaded.emit(idx, str(cache_path))

    def run(self):
        try:
            self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="CivitaiThumb")
            futures = []
            for idx, c in enumerate(self.cards):
                if self._is_aborted:
                    break
                futures.append(self._executor.submit(self._fetch_single, idx, c.get("preview_url")))

            for f in futures:
                if self._is_aborted:
                    break
                try:
                    f.result()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if self._executor:
                try:
                    self._executor.shutdown(wait=False)
                except Exception:
                    pass



class CivitaiDownloadWorker(QThread):
    progress_updated = pyqtSignal(int, float, float)  # (percent, downloaded_mb, speed_mbps)
    finished_successfully = pyqtSignal(Path)
    error_occurred = pyqtSignal(str)

    def __init__(self, download_url: str, dest_path: Path, api_token: Optional[str] = None, expected_sha256: Optional[str] = None):
        super().__init__()
        self.download_url = download_url
        self.dest_path = dest_path
        self.api_token = api_token.strip() if api_token else None
        self.expected_sha256 = expected_sha256.strip().lower() if expected_sha256 else None
        self._is_aborted = False

    def abort(self):
        self._is_aborted = True
        self.requestInterruption()

    def run(self):
        url = self.download_url
        if self.api_token:
            delim = "&" if "?" in url else "?"
            url = f"{url}{delim}token={self.api_token}"

        temp_part = self.dest_path.with_name(f"{self.dest_path.name}.part")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        try:
            # 1. Проверка расширения файла (строго .safetensors)
            if self.dest_path.suffix.lower() != ".safetensors":
                raise ValueError(
                    f"Запрещенное расширение файла: {self.dest_path.suffix}. "
                    f"Разрешены строго безопасные веса .safetensors (.ckpt и .pt заблокированы)."
                )

            # 2. Проверка домена URL по белому списку (*.civitai.com) для защиты от SSRF
            parsed_url = urllib.parse.urlparse(url)
            hostname = (parsed_url.hostname or "").lower()
            if not (hostname == "civitai.com" or hostname.endswith(".civitai.com")):
                raise ValueError(f"SSRF защита: домен '{hostname}' не входит в белый список разрешенных хостов (*.civitai.com).")

            self.dest_path.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(url, headers=headers, stream=True, timeout=20.0, allow_redirects=True) as resp:
                if resp.status_code in (401, 403):
                    raise RuntimeError("Требуется авторизация Civitai. Укажите API-токен.")
                resp.raise_for_status()

                # Валидация хоста после перенаправлений (редиректов)
                final_hostname = (urllib.parse.urlparse(resp.url).hostname or "").lower()
                if not (final_hostname == "civitai.com" or final_hostname.endswith(".civitai.com")):
                    raise ValueError(f"SSRF защита: итоговый хост '{final_hostname}' не входит в белый список (*.civitai.com).")

                total_size = int(resp.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 1024 * 1024  # 1 MB chunk (zero RAM bloating)

                start_time = time.time()
                last_time = start_time
                last_downloaded = 0

                hasher = hashlib.sha256()

                with open(temp_part, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if self._is_aborted:
                            f.close()
                            temp_part.unlink(missing_ok=True)
                            self.error_occurred.emit("Загрузка отменена пользователем.")
                            return

                        if chunk:
                            f.write(chunk)
                            hasher.update(chunk)
                            downloaded += len(chunk)

                            now = time.time()
                            if now - last_time >= 0.4:
                                duration = now - last_time
                                speed_mbps = ((downloaded - last_downloaded) / (1024 * 1024)) / max(0.01, duration)
                                percent = int((downloaded / total_size) * 100) if total_size > 0 else 0
                                self.progress_updated.emit(percent, downloaded / (1024 * 1024), speed_mbps)
                                last_time = now
                                last_downloaded = downloaded

            # 3. Верификация SHA-256 хеша при наличии ожидаемого значения
            if self.expected_sha256:
                calc_hash = hasher.hexdigest().lower()
                if calc_hash != self.expected_sha256:
                    if temp_part.exists():
                        temp_part.unlink(missing_ok=True)
                    raise ValueError(
                        f"Ошибка целостности: не совпадает контрольная сумма SHA-256.\n"
                        f"Ожидалось: {self.expected_sha256}\nПолучено: {calc_hash}"
                    )

            if temp_part.exists():
                if self.dest_path.exists():
                    self.dest_path.unlink()
                temp_part.rename(self.dest_path)
                self.finished_successfully.emit(self.dest_path)

        except Exception as e:
            if temp_part.exists():
                temp_part.unlink(missing_ok=True)
            self.error_occurred.emit(str(e))


class CivitaiCatalogDialog(QDialog):
    model_installed = pyqtSignal(dict)  # emitted when file is saved and ready for router

    def __init__(self, target_dir: Optional[Path] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🌐 Каталог Civitai / Civitai Red — Модели и LoRA")
        self.resize(1200, 750)
        self.setMinimumSize(980, 600)

        self.target_dir = target_dir or Path(r"C:\neyroGPD\models\Stable-diffusion")
        self.cards_data: List[Dict[str, Any]] = []
        self.current_page: int = 1
        self.total_pages: int = 1
        self.current_cursor: Optional[Any] = None
        self.next_cursor: Optional[Any] = None
        self.cursor_stack: List[Optional[Any]] = []

        self._search_worker: Optional[CivitaiSearchWorker] = None
        self._thumb_worker: Optional[CivitaiThumbnailsBatchWorker] = None
        self._download_worker: Optional[CivitaiDownloadWorker] = None
        self._cached_thumb_paths: Dict[int, str] = {}

        self._setup_ui()
        self._on_search_clicked(reset_page=True)

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog { background-color: #0E131F; color: #CBD5E1; border: 1px solid #1E273A; }
            QLabel { color: #94A3B8; font-size: 11px; font-weight: bold; }
            QLineEdit, QComboBox, QTextEdit {
                background-color: #0A0D14; color: #F8FAFC; border: 1px solid #1E2B42;
                border-radius: 6px; padding: 6px; font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border: 1px solid #00F0FF; }
            QListWidget {
                background-color: #0A0D15; border: 1px solid #1E283D; border-radius: 8px;
                padding: 6px; outline: none;
            }
            QListWidget::item {
                background-color: #121826; color: #E2E8F0; border: 1px solid #1E293B;
                border-radius: 8px; padding: 4px; font-size: 11px; text-align: center;
            }
            QListWidget::item:hover { background-color: #182236; border-color: #00F0FF; }
            QListWidget::item:selected {
                background-color: #16253D; border: 1.5px solid #00F0FF; color: #00F0FF; font-weight: bold;
            }
            QPushButton {
                background-color: #172133; color: #E2E8F0; border: 1px solid #283954;
                border-radius: 6px; padding: 7px 14px; font-size: 12px; font-weight: 600;
            }
            QPushButton:hover { background-color: #202D45; border-color: #00F0FF; color: #00F0FF; }
            QPushButton#btn_download {
                background-color: #004D54; border: 1px solid #00F0FF; color: #00F0FF; font-weight: bold; font-size: 13px;
            }
            QPushButton#btn_download:hover { background-color: #00737D; color: #FFFFFF; }
            QPushButton#btn_cancel_dl {
                background-color: #3F1419; border: 1px solid #EF4444; color: #FCA5A5; font-weight: bold;
            }
            QPushButton#btn_cancel_dl:hover { background-color: #5C1D24; color: #FFFFFF; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 1. Верхняя панель фильтров
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Поиск модели (например: realistic, cyber, anime, pony)...")
        self.input_search.returnPressed.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.input_search, stretch=3)

        self.combo_type = QComboBox()
        self.combo_type.addItem("Все типы", None)
        self.combo_type.addItem("📦 Чекпоинты (Checkpoint)", "Checkpoint")
        self.combo_type.addItem("⚡ LoRA", "LORA")
        self.combo_type.currentIndexChanged.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.combo_type, stretch=1)

        self.combo_arch = QComboBox()
        self.combo_arch.addItem("Все архитектуры", None)
        self.combo_arch.addItem("🟢 SD 1.5 (Оптимально)", "SD 1.5")
        self.combo_arch.addItem("🟣 SDXL 1.0", "SDXL 1.0")
        self.combo_arch.addItem("🐴 Pony", "Pony")
        self.combo_arch.currentIndexChanged.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.combo_arch, stretch=1)

        self.combo_sort = QComboBox()
        self.combo_sort.addItem("🔥 Популярные", "Most Downloaded")
        self.combo_sort.addItem("⭐ Рейтинг", "Highest Rated")
        self.combo_sort.addItem("🕒 Новые", "Newest")
        self.combo_sort.currentIndexChanged.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.combo_sort, stretch=1)

        self.chk_nsfw = QCheckBox("🔞 18+ (Civitai Red)")
        self.chk_nsfw.setChecked(True)
        self.chk_nsfw.toggled.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.chk_nsfw)

        self.btn_search = QPushButton("🔍 Найти")
        self.btn_search.clicked.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.btn_search)

        layout.addLayout(filter_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 2. Основная рабочая область
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Сетка карточек слева
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.list_results = QListWidget()
        self.list_results.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_results.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_results.setMovement(QListWidget.Movement.Static)
        self.list_results.setSpacing(8)
        self.list_results.setIconSize(QSize(130, 150))
        self.list_results.setGridSize(QSize(150, 205))
        self.list_results.setWordWrap(True)
        self.list_results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_results.currentItemChanged.connect(self._on_card_selected)
        left_layout.addWidget(self.list_results, stretch=1)

        # Панель страниц
        pag_layout = QHBoxLayout()
        pag_layout.setSpacing(8)

        self.btn_prev_page = QPushButton("◀ Назад")
        self.btn_prev_page.setFixedWidth(90)
        self.btn_prev_page.setEnabled(False)
        self.btn_prev_page.clicked.connect(self._on_prev_page_clicked)
        pag_layout.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("Стр. 1 из 1")
        self.lbl_page_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_page_info.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: bold;")
        pag_layout.addWidget(self.lbl_page_info, stretch=1)

        self.btn_next_page = QPushButton("Вперёд ▶")
        self.btn_next_page.setFixedWidth(90)
        self.btn_next_page.setEnabled(False)
        self.btn_next_page.clicked.connect(self._on_next_page_clicked)
        pag_layout.addWidget(self.btn_next_page)

        left_layout.addLayout(pag_layout)
        splitter.addWidget(left_container)

        # Правая панель информации и скачивания
        right_panel = QFrame()
        rp_layout = QVBoxLayout(right_panel)
        rp_layout.setContentsMargins(12, 12, 12, 12)
        rp_layout.setSpacing(8)

        preview_header = QHBoxLayout()
        preview_header.setSpacing(10)

        self.poster_preview_lbl = QLabel()
        self.poster_preview_lbl.setFixedSize(90, 110)
        preview_header.addWidget(self.poster_preview_lbl)

        header_text_box = QVBoxLayout()
        header_text_box.setSpacing(4)

        self.lbl_title = QLabel("Выберите модель")
        self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")
        self.lbl_title.setWordWrap(True)
        header_text_box.addWidget(self.lbl_title)

        self.lbl_meta = QLabel("Архитектура: -- | Размер: --")
        self.lbl_meta.setStyleSheet("color: #38BDF8; font-size: 11px;")
        header_text_box.addWidget(self.lbl_meta)

        self.lbl_triggers = QLabel("Триггеры: нет")
        self.lbl_triggers.setStyleSheet("color: #A78BFA; font-size: 11px;")
        self.lbl_triggers.setWordWrap(True)
        header_text_box.addWidget(self.lbl_triggers)

        preview_header.addLayout(header_text_box, stretch=1)
        rp_layout.addLayout(preview_header)

        self.text_preview = QTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setPlaceholderText("Описание модели, рекомендованные параметры и промпты...")
        rp_layout.addWidget(self.text_preview, stretch=1)

        # Блок прогресса скачивания
        self.download_box = QWidget()
        dl_layout = QVBoxLayout(self.download_box)
        dl_layout.setContentsMargins(0, 0, 0, 0)
        dl_layout.setSpacing(4)

        self.dl_status_lbl = QLabel("Готов к скачиванию")
        self.dl_status_lbl.setStyleSheet("color: #38BDF8; font-size: 11px;")
        dl_layout.addWidget(self.dl_status_lbl)

        self.dl_progress_bar = QProgressBar()
        self.dl_progress_bar.setFixedHeight(8)
        self.dl_progress_bar.setRange(0, 100)
        self.dl_progress_bar.setValue(0)
        self.dl_progress_bar.setTextVisible(False)
        dl_layout.addWidget(self.dl_progress_bar)

        self.download_box.setVisible(False)
        rp_layout.addWidget(self.download_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_download = QPushButton("📥 Скачать модель в Forge")
        self.btn_download.setObjectName("btn_download")
        self.btn_download.setFixedHeight(38)
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self._on_download_clicked)
        btn_row.addWidget(self.btn_download, stretch=3)

        self.btn_cancel_dl = QPushButton("✕ Отмена")
        self.btn_cancel_dl.setObjectName("btn_cancel_dl")
        self.btn_cancel_dl.setFixedHeight(38)
        self.btn_cancel_dl.setVisible(False)
        self.btn_cancel_dl.clicked.connect(self._on_cancel_download_clicked)
        btn_row.addWidget(self.btn_cancel_dl, stretch=1)

        rp_layout.addLayout(btn_row)

        splitter.addWidget(right_panel)
        splitter.setSizes([760, 420])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, stretch=1)

    def _on_search_clicked(self, reset_page: bool = True):
        if reset_page:
            self.current_page = 1
            self.current_cursor = None
            self.next_cursor = None
            self.cursor_stack.clear()

        query = self.input_search.text().strip()
        m_type = self.combo_type.currentData()
        base_arch = self.combo_arch.currentData()
        sort = self.combo_sort.currentData()
        nsfw = self.chk_nsfw.isChecked()

        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.btn_search.setEnabled(False)
        self.btn_prev_page.setEnabled(False)
        self.btn_next_page.setEnabled(False)

        if self._search_worker and self._search_worker.isRunning():
            try:
                self._search_worker.results_ready.disconnect()
                self._search_worker.error_occurred.disconnect()
            except Exception:
                pass
            self._search_worker.abort()
            self._search_worker.quit()
            self._search_worker.wait(200)
            if self._search_worker.isRunning():
                try:
                    self._search_worker.setParent(None)
                except Exception:
                    pass

        self._search_worker = CivitaiSearchWorker(
            query=query,
            model_type=m_type,
            base_model=base_arch,
            sort=sort,
            nsfw=nsfw,
            page=self.current_page,
            cursor=self.current_cursor
        )
        self._search_worker.results_ready.connect(self._on_results_ready)
        self._search_worker.error_occurred.connect(self._on_search_error)
        self._search_worker.start()

    def _on_prev_page_clicked(self):
        query = self.input_search.text().strip()
        if query:
            if self.cursor_stack:
                self.current_cursor = self.cursor_stack.pop()
                self.current_page = max(1, self.current_page - 1)
                self._on_search_clicked(reset_page=False)
        else:
            if self.current_page > 1:
                self.current_page -= 1
                self._on_search_clicked(reset_page=False)

    def _on_next_page_clicked(self):
        query = self.input_search.text().strip()
        if query:
            if self.next_cursor is not None:
                self.cursor_stack.append(self.current_cursor)
                self.current_cursor = self.next_cursor
                self.current_page += 1
                self._on_search_clicked(reset_page=False)
        else:
            if self.current_page < self.total_pages:
                self.current_page += 1
                self._on_search_clicked(reset_page=False)

    def _on_results_ready(self, cards: list, total_pages: int, total_items: int, next_cursor: Optional[Any]):
        self.progress_bar.setVisible(False)
        self.btn_search.setEnabled(True)
        self.cards_data = cards
        self.list_results.clear()
        self._cached_thumb_paths.clear()

        self.next_cursor = next_cursor
        query = self.input_search.text().strip()

        if query:
            self.lbl_page_info.setText(f"Стр. {self.current_page}")
            self.btn_prev_page.setEnabled(len(self.cursor_stack) > 0)
            self.btn_next_page.setEnabled(bool(self.next_cursor))
        else:
            self.total_pages = max(1, total_pages)
            self.lbl_page_info.setText(f"Стр. {self.current_page} из {self.total_pages} (найдено: {total_items})")
            self.btn_prev_page.setEnabled(self.current_page > 1)
            self.btn_next_page.setEnabled(self.current_page < self.total_pages or bool(self.next_cursor))

        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.abort()
            self._thumb_worker.wait(150)

        for c in cards:
            c_name = c["name"]
            short_name = c_name[:15] + ("…" if len(c_name) > 15 else "")
            sz_str = f"{c['size_mb'] / 1024.0:.1f} GB" if c['size_mb'] > 1024 else f"{c['size_mb']:.0f} MB"
            type_tag = "📦" if c["type"] == "Checkpoint" else "⚡"

            item_text = f"{short_name}\n{type_tag} {c['base_model']} • {sz_str}"
            placeholder = render_civitai_poster(None, name=c_name, width=130, height=150)
            item = QListWidgetItem(QIcon(placeholder), item_text)
            self.list_results.addItem(item)

        if cards:
            self._thumb_worker = CivitaiThumbnailsBatchWorker(cards)
            self._thumb_worker.thumb_loaded.connect(self._on_thumb_loaded)
            self._thumb_worker.start()

            self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")
            self.list_results.setCurrentRow(0)
        else:
            self.lbl_title.setText("Ничего не найдено")
            self.lbl_title.setStyleSheet("color: #94A3B8; font-size: 14px; font-weight: bold;")
            self.lbl_meta.setText("Попробуйте изменить запрос или фильтры архитектуры.")
            self.poster_preview_lbl.setPixmap(render_civitai_poster(None, name="?", width=90, height=110))
            self.text_preview.clear()
            self.btn_download.setEnabled(False)

    def _on_thumb_loaded(self, row_idx: int, img_path: str):
        self._cached_thumb_paths[row_idx] = img_path
        item = self.list_results.item(row_idx)
        if item and row_idx < len(self.cards_data):
            c_name = self.cards_data[row_idx]["name"]
            poster = render_civitai_poster(img_path, name=c_name, width=130, height=150)
            item.setIcon(QIcon(poster))

        if self.list_results.currentRow() == row_idx and row_idx < len(self.cards_data):
            c_name = self.cards_data[row_idx]["name"]
            preview_pix = render_civitai_poster(img_path, name=c_name, width=90, height=110)
            self.poster_preview_lbl.setPixmap(preview_pix)

    def _on_search_error(self, err: str):
        self.progress_bar.setVisible(False)
        self.btn_search.setEnabled(True)
        self.list_results.clear()
        self.lbl_title.setText("🔌 Ошибка загрузки каталога")
        self.lbl_title.setStyleSheet("color: #FF007F; font-size: 14px; font-weight: bold;")
        self.lbl_meta.setText("Сбой запроса")
        self.text_preview.setPlainText(f"Не удалось получить данные с Civitai:\n{err}")
        self.btn_download.setEnabled(False)

    def _on_card_selected(self, current: Optional[QListWidgetItem]):
        row = self.list_results.currentRow()
        if row < 0 or row >= len(self.cards_data):
            return

        c = self.cards_data[row]
        self.lbl_title.setText(f"{c['name']} ({c['version_name']})")
        self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")

        sz_str = f"{c['size_mb'] / 1024.0:.2f} GB" if c['size_mb'] > 1024 else f"{c['size_mb']:.0f} MB"
        self.lbl_meta.setText(f"Тип: {c['type']} | Архитектура: {c['base_model']} | Размер: {sz_str}")

        triggers = c.get("trained_words") or []
        if triggers:
            self.lbl_triggers.setText(f"🏷️ Триггер-слова: {', '.join(triggers[:6])}")
        else:
            self.lbl_triggers.setText("🏷️ Триггер-слова: не требуются (стандартный чекпоинт)")

        # Рекомендованные параметры из метаданных превью
        meta = c.get("meta") or {}
        sampler = meta.get("sampler") or "DPM++ 2M Karras"
        steps = meta.get("steps") or 25
        cfg = meta.get("cfgScale") or 6.0

        desc_parts = [
            f"⚙️ Рекомендованные автором параметры:\n• Сэмплер: {sampler}\n• Шаги: {steps}\n• CFG Scale: {cfg}",
            f"📄 Имя файла: {c['filename']}",
            f"📖 Описание:\n{c.get('description', '')}"
        ]
        self.text_preview.setPlainText("\n\n".join(desc_parts).strip())

        cached_path = self._cached_thumb_paths.get(row)
        pix = render_civitai_poster(cached_path, name=c["name"], width=90, height=110)
        self.poster_preview_lbl.setPixmap(pix)

        self.btn_download.setEnabled(bool(c.get("download_url")))
        self.btn_download.setText(f"📥 Скачать {c['type']} ({sz_str})")

    def _on_download_clicked(self):
        row = self.list_results.currentRow()
        if row < 0 or row >= len(self.cards_data):
            return

        c = self.cards_data[row]
        filename = c["filename"]

        # Папка назначения: checkpoints или lora
        if c["type"] == "LORA":
            save_dir = self.target_dir.parent / "Lora" if self.target_dir else Path(r"C:\neyroGPD\models\Lora")
        else:
            save_dir = self.target_dir

        dest_file = save_dir / filename

        if dest_file.suffix.lower() != ".safetensors":
            QMessageBox.critical(
                self,
                "Запрещенный формат модели",
                f"Загрузка файлов с расширением '{dest_file.suffix}' заблокирована в целях безопасности.\n\n"
                f"Разрешены строго безопасные веса в формате .safetensors. Файлы .ckpt и .pt запрещены."
            )
            return

        self.btn_download.setEnabled(False)
        self.btn_cancel_dl.setVisible(True)
        self.download_box.setVisible(True)
        self.dl_status_lbl.setText("Подключение к серверу загрузки...")
        self.dl_progress_bar.setValue(0)

        self._download_worker = CivitaiDownloadWorker(
            download_url=c["download_url"],
            dest_path=dest_file,
            expected_sha256=c.get("sha256")
        )
        self._download_worker.progress_updated.connect(self._on_download_progress)
        self._download_worker.finished_successfully.connect(self._on_download_success)
        self._download_worker.error_occurred.connect(self._on_download_error)
        self._download_worker.start()

    def _on_download_progress(self, percent: int, downloaded_mb: float, speed_mbps: float):
        self.dl_progress_bar.setValue(percent)
        self.dl_status_lbl.setText(f"Загрузка: {percent}% • {downloaded_mb:.1f} MB ({speed_mbps:.1f} MB/s)")

    def _on_cancel_download_clicked(self):
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.abort()
            self._download_worker.wait(300)
            self._reset_dl_ui()

    def _on_download_success(self, saved_path: Path):
        self._reset_dl_ui()
        row = self.list_results.currentRow()
        c = self.cards_data[row] if (0 <= row < len(self.cards_data)) else {}

        meta = c.get("meta") or {}
        triggers = c.get("trained_words") or []
        desc_text = f"{c.get('name', '')}. Теги: {', '.join(triggers[:8])}" if triggers else c.get('name', '')

        model_payload = {
            "id": c.get("id"),
            "name": c.get("name"),
            "filename": saved_path.name,
            "path": str(saved_path),
            "type": c.get("type"),
            "triggers": triggers,
            "description": desc_text,
            "guidance": f"Trigger words: {', '.join(triggers[:5])}" if triggers else "",
            "sampler": meta.get("sampler", "DPM++ 2M Karras"),
            "steps": int(meta.get("steps", 25)),
            "cfg_scale": float(meta.get("cfgScale", 6.0))
        }

        self.model_installed.emit(model_payload)
        QMessageBox.information(
            self,
            "Загрузка завершена",
            f"Модель успешно сохранена в папку Forge:\n{saved_path.name}\n\nОна автоматически зарегистрирована в роутере!"
        )
        self.accept()

    def _on_download_error(self, err: str):
        self._reset_dl_ui()
        QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось завершить загрузку файла:\n{err}")

    def _reset_dl_ui(self):
        self.btn_download.setEnabled(True)
        self.btn_cancel_dl.setVisible(False)
        self.download_box.setVisible(False)
        self.dl_progress_bar.setValue(0)

    def _stop_all_workers(self):
        """Безопасная остановка всех рабочих потоков диалога Civitai."""
        workers = [self._search_worker, self._thumb_worker, self._download_worker]
        for worker in workers:
            if worker is None:
                continue

            # 1) Отключение сигналов от UI
            for sig_name in (
                "results_ready", "error_occurred",
                "thumb_loaded",
                "progress_updated", "finished_successfully"
            ):
                sig = getattr(worker, sig_name, None)
                if sig is not None:
                    try:
                        sig.disconnect()
                    except Exception:
                        pass

            # 2) Вызов abort() и quit()
            if hasattr(worker, "abort"):
                try:
                    worker.abort()
                except Exception:
                    pass
            try:
                worker.requestInterruption()
            except Exception:
                pass
            try:
                worker.quit()
            except Exception:
                pass

            # 3) Если поток все еще isRunning(), wait(300). Если не успел — отвязать parent (setParent(None))
            try:
                if worker.isRunning():
                    worker.wait(300)
            except Exception:
                pass

            try:
                if worker.isRunning():
                    worker.setParent(None)
            except Exception:
                pass

        self._search_worker = None
        self._thumb_worker = None
        self._download_worker = None

    def closeEvent(self, event):
        self._stop_all_workers()
        super().closeEvent(event)

    def reject(self):
        self._stop_all_workers()
        super().reject()

    def accept(self):
        self._stop_all_workers()
        super().accept()
