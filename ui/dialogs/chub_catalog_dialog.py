"""
Chub.ai Catalog Browser Dialog for NeonRoute.
Grid View poster gallery layout with background caching and smooth keyboard navigation.
"""

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, List, Dict, Any

from PIL import Image
import requests

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QPainterPath, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QListWidget, QListWidgetItem,
    QTextEdit, QFrame, QSplitter, QMessageBox, QProgressBar, QWidget
)

from config import DATA_DIR
from services.chub_client import ChubClient

AVATAR_CACHE_DIR = DATA_DIR / "cache" / "chub_avatars"
AVATAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def render_square_poster(
    image_path: Optional[str] = None,
    name: str = "?",
    width: int = 120,
    height: int = 120,
    radius: int = 8
) -> QPixmap:
    """Генерирует квадратный постер со скругленными углами и центром кадрирования."""
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
        font.setPointSize(max(13, int(height * 0.22)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, 0, width, height, Qt.AlignmentFlag.AlignCenter, initials)

    painter.setClipping(False)
    painter.setPen(QColor("rgba(0, 240, 255, 0.35)"))
    painter.drawRoundedRect(0, 0, width - 1, height - 1, radius, radius)
    painter.end()
    return pix


class ChubSearchWorker(QThread):
    results_ready = pyqtSignal(list, int)
    error_occurred = pyqtSignal(str)

    def __init__(self, query: str, sort: str, nsfw: bool, max_tokens: Optional[int], page: int = 1, russian_only: bool = False):
        super().__init__()
        self.query = query
        self.sort = sort
        self.nsfw = nsfw
        self.max_tokens = max_tokens
        self.page = page
        self.russian_only = russian_only

    def run(self):
        try:
            client = ChubClient()
            cards, total = client.search_characters(
                query=self.query,
                page=self.page,
                sort=self.sort,
                nsfw=self.nsfw,
                max_tokens_limit=self.max_tokens,
                russian_only=self.russian_only
            )
            self.results_ready.emit(cards, total)
        except Exception as e:
            self.error_occurred.emit(str(e))


class ChubDownloadWorker(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, full_path: str, card_title: str):
        super().__init__()
        self.full_path = full_path
        self.card_title = card_title

    def run(self):
        try:
            client = ChubClient()
            path = client.download_card(self.full_path, self.card_title)
            self.finished.emit(path)
        except Exception as e:
            self.error.emit(str(e))


class ChubAvatarsBatchWorker(QThread):
    avatar_loaded = pyqtSignal(int, str)

    def __init__(self, cards: List[Dict[str, Any]], session: requests.Session):
        super().__init__()
        self.cards = list(cards)
        self.session = session
        self._is_aborted = False
        self._executor: Optional[ThreadPoolExecutor] = None

    def abort(self):
        self._is_aborted = True
        if self._executor:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def _fetch_single_avatar(self, idx: int, card: Dict[str, Any]):
        if self._is_aborted:
            return

        url = card.get("avatar_url")
        if not url:
            return

        cache_key = hashlib.md5(url.encode("utf-8")).hexdigest()
        cache_path = AVATAR_CACHE_DIR / f"{cache_key}.png"

        if not cache_path.exists():
            try:
                resp = self.session.get(url, timeout=5.0)
                if resp.status_code == 200 and len(resp.content) > 300:
                    img = Image.open(io.BytesIO(resp.content))
                    img.save(cache_path, format="PNG")
            except Exception:
                return

        if cache_path.exists() and not self._is_aborted:
            self.avatar_loaded.emit(idx, str(cache_path))

    def run(self):
        try:
            self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ChubAvatarWorker")
            futures = []
            for idx, card in enumerate(self.cards):
                if self._is_aborted:
                    break
                futures.append(self._executor.submit(self._fetch_single_avatar, idx, card))

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



class ChubCatalogDialog(QDialog):
    card_downloaded = pyqtSignal(Path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🌐 Каталог Chub.ai — Сетка персонажей")
        self.resize(1180, 720)
        self.setMinimumSize(960, 580)

        self.cards_data: List[Dict[str, Any]] = []
        self.current_page: int = 1
        self.total_pages: int = 1
        self.total_items: int = 0

        self._search_worker: Optional[ChubSearchWorker] = None
        self._download_worker: Optional[ChubDownloadWorker] = None
        self._avatar_worker: Optional[ChubAvatarsBatchWorker] = None
        self._cached_avatar_paths: Dict[int, str] = {}

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
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 1. Верхняя панель фильтров
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Поиск по имени, автору или тегам (cyberpunk, fantasy, elaria)...")
        self.input_search.returnPressed.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.input_search, stretch=3)

        self.combo_sort = QComboBox()
        self.combo_sort.addItem("🔥 Популярные (Загрузки)", "download_count")
        self.combo_sort.addItem("⭐ Рейтинг (Звезды)", "star_count")
        self.combo_sort.addItem("🕒 Новые", "created_at")
        self.combo_sort.currentIndexChanged.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.combo_sort, stretch=1)

        self.combo_tokens = QComboBox()
        self.combo_tokens.addItem("Все токены", None)
        self.combo_tokens.addItem("🟢 До 1500 (Оптимально)", 1500)
        self.combo_tokens.addItem("🟡 До 2500 (Нормально)", 2500)
        self.combo_tokens.currentIndexChanged.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.combo_tokens, stretch=1)

        self.chk_russian = QCheckBox("🇷🇺 На русском")
        self.chk_russian.setChecked(False)
        self.chk_russian.toggled.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.chk_russian)

        self.chk_nsfw = QCheckBox("18+ (NSFW)")
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

        # 2. Двухпанельный сплиттер (Слева: Сетка карточек, Справа: Узкое превью)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Левая часть: Сетка (IconMode Grid)
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.list_results = QListWidget()
        self.list_results.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_results.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_results.setMovement(QListWidget.Movement.Static)
        self.list_results.setSpacing(8)
        self.list_results.setIconSize(QSize(120, 120))
        self.list_results.setGridSize(QSize(140, 178))
        self.list_results.setWordWrap(True)
        self.list_results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_results.currentItemChanged.connect(self._on_card_selected)
        left_layout.addWidget(self.list_results, stretch=1)

        # Нижняя плашка перелистывания страниц
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

        # Правая часть: Карточка детального просмотра (Компактная ширина)
        right_panel = QFrame()
        rp_layout = QVBoxLayout(right_panel)
        rp_layout.setContentsMargins(12, 12, 12, 12)
        rp_layout.setSpacing(8)

        preview_header = QHBoxLayout()
        preview_header.setSpacing(10)

        self.avatar_preview_lbl = QLabel()
        self.avatar_preview_lbl.setFixedSize(80, 80)
        preview_header.addWidget(self.avatar_preview_lbl)

        header_text_box = QVBoxLayout()
        header_text_box.setSpacing(4)

        self.lbl_title = QLabel("Выберите карточку")
        self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")
        self.lbl_title.setWordWrap(True)
        header_text_box.addWidget(self.lbl_title)

        self.lbl_meta = QLabel("Токены: -- | Рейтинг: --")
        self.lbl_meta.setStyleSheet("color: #38BDF8; font-size: 11px;")
        header_text_box.addWidget(self.lbl_meta)

        preview_header.addLayout(header_text_box, stretch=1)
        rp_layout.addLayout(preview_header)

        self.text_preview = QTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setPlaceholderText("Описание и сценарий выбранного персонажа...")
        rp_layout.addWidget(self.text_preview, stretch=1)

        self.btn_download = QPushButton("📥 Скачать в коллекцию")
        self.btn_download.setObjectName("btn_download")
        self.btn_download.setFixedHeight(36)
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self._on_download_clicked)
        rp_layout.addWidget(self.btn_download)

        splitter.addWidget(right_panel)
        splitter.setSizes([780, 380])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, stretch=1)

    def _on_search_clicked(self, reset_page: bool = True):
        if reset_page:
            self.current_page = 1

        query = self.input_search.text().strip()
        sort = self.combo_sort.currentData()
        nsfw = self.chk_nsfw.isChecked()
        max_tok = self.combo_tokens.currentData()
        russian_only = self.chk_russian.isChecked()

        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.btn_search.setEnabled(False)
        self.btn_prev_page.setEnabled(False)
        self.btn_next_page.setEnabled(False)

        self._search_worker = ChubSearchWorker(
            query=query,
            sort=sort,
            nsfw=nsfw,
            max_tokens=max_tok,
            page=self.current_page,
            russian_only=russian_only
        )
        self._search_worker.results_ready.connect(self._on_results_ready)
        self._search_worker.error_occurred.connect(self._on_search_error)
        self._search_worker.start()

    def _on_prev_page_clicked(self):
        if self.current_page > 1:
            self.current_page -= 1
            self._on_search_clicked(reset_page=False)

    def _on_next_page_clicked(self):
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._on_search_clicked(reset_page=False)

    def _on_results_ready(self, cards: list, total: int):
        self.progress_bar.setVisible(False)
        self.btn_search.setEnabled(True)
        self.cards_data = cards
        self.list_results.clear()
        self._cached_avatar_paths.clear()

        self.total_items = total
        page_size = max(1, len(cards)) if cards else 40
        self.total_pages = max(1, (total + page_size - 1) // page_size)

        self.lbl_page_info.setText(f"Стр. {self.current_page} из {self.total_pages} (всего: {total})")
        self.btn_prev_page.setEnabled(self.current_page > 1)
        self.btn_next_page.setEnabled(self.current_page < self.total_pages)

        if self._avatar_worker and self._avatar_worker.isRunning():
            self._avatar_worker.abort()
            self._avatar_worker.wait(150)

        for c in cards:
            tok = c.get("tokens", 0)
            badge = "🟢" if tok <= 1500 else ("🟡" if tok <= 2500 else "🔴")
            c_name = c.get("name", "Персонаж").strip()
            short_name = c_name[:15] + ("…" if len(c_name) > 15 else "")
            stars = c.get("star_count", 0)
            lang_badge = "🇷🇺" if c.get("lang") == "ru" else "🇬🇧"

            item_text = f"{short_name}\n{badge} {tok}t • {lang_badge} • ★{stars}"
            placeholder = render_square_poster(None, name=c_name, width=120, height=120)
            item = QListWidgetItem(QIcon(placeholder), item_text)
            self.list_results.addItem(item)

        if cards:
            client = ChubClient()
            self._avatar_worker = ChubAvatarsBatchWorker(cards, client.session)
            self._avatar_worker.avatar_loaded.connect(self._on_avatar_loaded)
            self._avatar_worker.start()

            self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")
            self.list_results.setCurrentRow(0)
        else:
            self.lbl_title.setText("Ничего не найдено")
            self.lbl_title.setStyleSheet("color: #94A3B8; font-size: 14px; font-weight: bold;")
            self.lbl_meta.setText("Измените запрос или фильтры токенов.")
            self.avatar_preview_lbl.setPixmap(render_square_poster(None, name="?", width=80, height=80))
            self.text_preview.clear()
            self.btn_download.setEnabled(False)

    def _on_avatar_loaded(self, row_idx: int, img_path: str):
        self._cached_avatar_paths[row_idx] = img_path
        item = self.list_results.item(row_idx)
        if item and row_idx < len(self.cards_data):
            c_name = self.cards_data[row_idx]["name"]
            poster = render_square_poster(img_path, name=c_name, width=120, height=120)
            item.setIcon(QIcon(poster))

        if self.list_results.currentRow() == row_idx and row_idx < len(self.cards_data):
            c_name = self.cards_data[row_idx]["name"]
            preview_pix = render_square_poster(img_path, name=c_name, width=80, height=80)
            self.avatar_preview_lbl.setPixmap(preview_pix)

    def _on_search_error(self, err: str):
        self.progress_bar.setVisible(False)
        self.btn_search.setEnabled(True)
        self.list_results.clear()

        err_lower = err.lower()
        if "подключение к интернету" in err_lower or "connection" in err_lower or "timeout" in err_lower:
            self.lbl_title.setText("🔌 Нет подключения к интернету")
            self.lbl_title.setStyleSheet("color: #FF007F; font-size: 14px; font-weight: bold;")
            self.lbl_meta.setText("Сетевое соединение недоступно")
            self.text_preview.setPlainText(
                "Не удалось связаться с сервером Chub.ai.\n\n"
                "• Проверьте подключение к сети.\n"
                "• Если используется VPN или прокси, проверьте их активность.\n"
                "• Нажмите «🔍 Найти» после восстановления связи."
            )
        else:
            self.lbl_title.setText("⚠️ Ошибка каталога")
            self.lbl_title.setStyleSheet("color: #FFE600; font-size: 14px; font-weight: bold;")
            self.lbl_meta.setText("Сбой обработки запроса")
            self.text_preview.setPlainText(f"Не удалось получить данные с сервера:\n{err}")

        self.btn_download.setEnabled(False)

    def _on_card_selected(self, current: Optional[QListWidgetItem]):
        row = self.list_results.currentRow()
        if row < 0 or row >= len(self.cards_data):
            return

        c = self.cards_data[row]
        self.lbl_title.setText(c["name"])
        self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")
        tok = c["tokens"]
        lang_str = "🇷🇺 Русский язык" if c.get("lang") == "ru" else "🇬🇧 Английский язык"
        self.lbl_meta.setText(f"{lang_str} | ~{tok} tok | ★ {c['star_count']} | 📥 {c['download_count']}")

        preview_parts = []
        if c.get("tagline"):
            preview_parts.append(f"📌 {c['tagline']}")
        if c.get("topics"):
            preview_parts.append(f"🏷️ Теги: {', '.join(c['topics'][:8])}")
        if c.get("description"):
            preview_parts.append(f"📖 Описание:\n{c['description']}")

        self.text_preview.setPlainText("\n\n".join(preview_parts).strip())

        cached_path = self._cached_avatar_paths.get(row)
        pix = render_square_poster(cached_path, name=c["name"], width=80, height=80)
        self.avatar_preview_lbl.setPixmap(pix)
        self.btn_download.setEnabled(True)

    def _on_download_clicked(self):
        row = self.list_results.currentRow()
        if row < 0 or row >= len(self.cards_data):
            return

        c = self.cards_data[row]
        self.btn_download.setEnabled(False)
        self.btn_download.setText("⏳ Скачивание и регистрация...")

        self._download_worker = ChubDownloadWorker(c["full_path"], c["name"])
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

    def _on_download_finished(self, saved_path: Optional[Path]):
        self.btn_download.setEnabled(True)
        self.btn_download.setText("📥 Скачать в коллекцию")
        if saved_path and saved_path.exists():
            self.card_downloaded.emit(saved_path)
            QMessageBox.information(self, "Успешно", f"Карточка сохранена в коллекцию:\n{saved_path.name}")
            self.accept()
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось скачать карточку с Chub.ai.")

    def _on_download_error(self, err: str):
        self.btn_download.setEnabled(True)
        self.btn_download.setText("📥 Скачать в коллекцию")
        QMessageBox.critical(self, "Ошибка загрузки", f"{err}")

    def closeEvent(self, event):
        if self._avatar_worker and self._avatar_worker.isRunning():
            self._avatar_worker.abort()
            self._avatar_worker.wait(200)
        super().closeEvent(event)
