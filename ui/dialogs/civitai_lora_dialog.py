"""
Civitai LoRA Search & Installation Dialog for NeonRoute.
Provides dedicated character LoRA discovery, SD 1.5 / SDXL / Pony compatibility evaluation,
and streaming downloads directly to Forge LoRA storage with automatic registry updates.
"""

import re
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QListWidget, QListWidgetItem,
    QTextEdit, QFrame, QSplitter, QMessageBox, QProgressBar, QWidget
)

from services.lora_manager import LoraManager, DEFAULT_FORGE_LORA_DIR
from ui.dialogs.civitai_catalog_dialog import (
    render_civitai_poster,
    CivitaiSearchWorker,
    CivitaiThumbnailsBatchWorker,
    CivitaiDownloadWorker
)


class CivitaiLoraDialog(QDialog):
    """
    Модальный диалог поиска и установки персонажных LoRA с Civitai
    с проверкой совместимости архитектур.
    """
    lora_installed = pyqtSignal(str, str, list)  # file_stem, character_name, trained_words

    @staticmethod
    def evaluate_compatibility(lora_base_model: str, active_forge_arch: str = "SD 1.5") -> dict:
        """
        Сравнивает семейство базовой модели LoRA с текущим активным чекпоинтом Forge.
        Возвращает словарь с флагом is_compatible, стилем бейджа и поясняющим текстом.
        """
        l_base = (lora_base_model or "").lower()
        a_base = (active_forge_arch or "").lower()

        is_sd15_lora = any(k in l_base for k in ["sd 1.5", "sd 1.4", "sd15"])
        is_sdxl_lora = any(k in l_base for k in ["sdxl", "pony", "illustrious"])

        is_current_sd15 = "1.5" in a_base
        is_current_sdxl = "xl" in a_base or "pony" in a_base

        if (is_sd15_lora and is_current_sd15) or (is_sdxl_lora and is_current_sdxl):
            return {
                "compatible": True,
                "badge": f"✅ {lora_base_model} (Совместимо)",
                "color": "#10B981",
                "warn_text": ""
            }
        else:
            return {
                "compatible": False,
                "badge": f"⚠️ {lora_base_model} (Несовместимо)",
                "color": "#F59E0B",
                "warn_text": (
                    f"Внимание: данная LoRA создана для архитектуры {lora_base_model}, "
                    f"в то время как ваш активный чекпоинт в Forge работает на {active_forge_arch}.\n"
                    f"Генерация артов с этой LoRA в текущем режиме вызовет визуальные артефакты или сбой."
                )
            }

    def __init__(
        self,
        character_name: str = "",
        initial_query: str = "",
        active_architecture: str = "SD 1.5",
        parent=None
    ):
        super().__init__(parent)
        self.character_name = character_name.strip()
        if not initial_query and self.character_name:
            clean_q = re.sub(r'[\(\[].*?[\)\]]', '', self.character_name)
            clean_q = clean_q.split('-')[0].split('—')[0].strip()
            self.initial_query = clean_q
        else:
            self.initial_query = initial_query.strip()

        self.active_architecture = active_architecture or "SD 1.5"

        title_suffix = f" — {self.character_name}" if self.character_name else ""
        self.setWindowTitle(f"⚡ Поиск LoRA на Civitai{title_suffix}")
        self.resize(1180, 750)
        self.setMinimumSize(960, 600)

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
        self.input_search.setText(self.initial_query)
        self.input_search.setPlaceholderText("Имя персонажа или название LoRA...")
        self.input_search.returnPressed.connect(lambda: self._on_search_clicked(reset_page=True))
        filter_bar.addWidget(self.input_search, stretch=3)

        self.combo_arch = QComboBox()
        self.combo_arch.addItem("Все архитектуры", None)
        self.combo_arch.addItem("🟢 SD 1.5", "SD 1.5")
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
        self.list_results.setGridSize(QSize(150, 215))
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

        self.lbl_title = QLabel("Выберите LoRA")
        self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")
        self.lbl_title.setWordWrap(True)
        header_text_box.addWidget(self.lbl_title)

        # Бейдж совместимости в шапке
        self.lbl_compat_badge = QLabel("Совместимость: --")
        self.lbl_compat_badge.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: bold;")
        header_text_box.addWidget(self.lbl_compat_badge)

        self.lbl_meta = QLabel("Архитектура: -- | Размер: --")
        self.lbl_meta.setStyleSheet("color: #38BDF8; font-size: 11px;")
        header_text_box.addWidget(self.lbl_meta)

        self.lbl_triggers = QLabel("Триггеры: нет")
        self.lbl_triggers.setStyleSheet("color: #A78BFA; font-size: 11px;")
        self.lbl_triggers.setWordWrap(True)
        header_text_box.addWidget(self.lbl_triggers)

        preview_header.addLayout(header_text_box, stretch=1)
        rp_layout.addLayout(preview_header)

        # Предупреждение о несовместимости (если несовместимо)
        self.lbl_warn_box = QLabel()
        self.lbl_warn_box.setWordWrap(True)
        self.lbl_warn_box.setStyleSheet(
            "background-color: #2D1B0D; color: #F59E0B; border: 1px solid #F59E0B; "
            "border-radius: 6px; padding: 6px; font-size: 11px;"
        )
        self.lbl_warn_box.setVisible(False)
        rp_layout.addWidget(self.lbl_warn_box)

        self.text_preview = QTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setPlaceholderText("Описание LoRA, ключевые теги и рекомендации автора...")
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

        self.btn_download = QPushButton("📥 Скачать LoRA в Forge")
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
        splitter.setSizes([740, 440])
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
        base_arch = self.combo_arch.currentData()
        sort = self.combo_sort.currentData()
        nsfw = self.chk_nsfw.isChecked()

        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.btn_search.setEnabled(False)
        self.btn_prev_page.setEnabled(False)
        self.btn_next_page.setEnabled(False)

        self._search_worker = CivitaiSearchWorker(
            query=query,
            model_type="LORA",
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
            comp_info = self.evaluate_compatibility(c.get("base_model", ""), self.active_architecture)

            item_text = f"{short_name}\n{comp_info['badge']} • {sz_str}"
            placeholder = render_civitai_poster(None, name=c_name, width=130, height=150)
            item = QListWidgetItem(QIcon(placeholder), item_text)
            item.setToolTip(f"{c_name}\nАрхитектура: {c.get('base_model', '')}\n{comp_info['badge']}\nРазмер: {sz_str}")
            self.list_results.addItem(item)

        if cards:
            self._thumb_worker = CivitaiThumbnailsBatchWorker(cards)
            self._thumb_worker.thumb_loaded.connect(self._on_thumb_loaded)
            self._thumb_worker.start()

            self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")
            self.list_results.setCurrentRow(0)
        else:
            self.lbl_title.setText("LoRA не найдены")
            self.lbl_title.setStyleSheet("color: #94A3B8; font-size: 14px; font-weight: bold;")
            self.lbl_compat_badge.setText("Совместимость: --")
            self.lbl_compat_badge.setStyleSheet("color: #94A3B8; font-size: 11px;")
            self.lbl_meta.setText("Попробуйте изменить поисковый запрос или фильтр архитектур.")
            self.lbl_warn_box.setVisible(False)
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
        self.lbl_title.setText("🔌 Ошибка каталога Civitai")
        self.lbl_title.setStyleSheet("color: #FF007F; font-size: 14px; font-weight: bold;")
        self.lbl_compat_badge.setText("")
        self.lbl_meta.setText("Сбой запроса")
        self.lbl_warn_box.setVisible(False)
        self.text_preview.setPlainText(f"Не удалось получить список LoRA с Civitai:\n{err}")
        self.btn_download.setEnabled(False)

    def _on_card_selected(self, current: Optional[QListWidgetItem]):
        row = self.list_results.currentRow()
        if row < 0 or row >= len(self.cards_data):
            return

        c = self.cards_data[row]
        self.lbl_title.setText(f"{c['name']} ({c['version_name']})")
        self.lbl_title.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")

        sz_str = f"{c['size_mb'] / 1024.0:.2f} GB" if c['size_mb'] > 1024 else f"{c['size_mb']:.0f} MB"
        self.lbl_meta.setText(f"Архитектура: {c['base_model']} | Размер: {sz_str}")

        # Бейдж совместимости
        comp_info = self.evaluate_compatibility(c.get("base_model", ""), self.active_architecture)
        self.lbl_compat_badge.setText(comp_info["badge"])
        self.lbl_compat_badge.setStyleSheet(f"color: {comp_info['color']}; font-weight: bold; font-size: 11px;")

        if not comp_info["compatible"]:
            self.lbl_warn_box.setText(comp_info["warn_text"])
            self.lbl_warn_box.setVisible(True)
        else:
            self.lbl_warn_box.setVisible(False)

        triggers = c.get("trained_words") or []
        if triggers:
            self.lbl_triggers.setText(f"🏷️ Триггер-слова: {', '.join(triggers[:6])}")
        else:
            self.lbl_triggers.setText("🏷️ Триггер-слова: не указаны автором")

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
        self.btn_download.setText(f"📥 Скачать LoRA ({sz_str})")

    def _on_download_clicked(self):
        row = self.list_results.currentRow()
        if row < 0 or row >= len(self.cards_data):
            return

        c = self.cards_data[row]
        comp_info = self.evaluate_compatibility(c.get("base_model", ""), self.active_architecture)

        if not comp_info["compatible"]:
            resp = QMessageBox.warning(
                self,
                "Несовпадение архитектуры",
                f"{comp_info['warn_text']}\n\nВы действительно хотите скачать этот файл?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if resp != QMessageBox.StandardButton.Yes:
                return

        filename = c["filename"]
        dest_file = DEFAULT_FORGE_LORA_DIR / filename
        DEFAULT_FORGE_LORA_DIR.mkdir(parents=True, exist_ok=True)

        self.btn_download.setEnabled(False)
        self.btn_cancel_dl.setVisible(True)
        self.download_box.setVisible(True)
        self.dl_status_lbl.setText("Подключение к серверу загрузки...")
        self.dl_progress_bar.setValue(0)

        self._download_worker = CivitaiDownloadWorker(
            download_url=c["download_url"],
            dest_path=dest_file
        )
        self._download_worker.progress_updated.connect(self._on_download_progress)
        self._download_worker.finished_successfully.connect(self._on_download_success)
        self._download_worker.error_occurred.connect(self._on_download_error)
        self._download_worker.start()

    def _on_download_progress(self, percent: int, downloaded_mb: float, speed_mbps: float):
        self.dl_progress_bar.setValue(percent)
        self.dl_status_lbl.setText(f"Загрузка LoRA: {percent}% • {downloaded_mb:.1f} MB ({speed_mbps:.1f} MB/s)")

    def _on_cancel_download_clicked(self):
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.abort()
            self._download_worker.wait(300)
            self._reset_dl_ui()

    def _on_download_success(self, saved_path: Path):
        self._reset_dl_ui()
        row = self.list_results.currentRow()
        c = self.cards_data[row] if (0 <= row < len(self.cards_data)) else {}

        trained_words = c.get("trained_words") or []
        file_stem = saved_path.stem
        char_name = self.character_name or file_stem

        # Регистрация LoRA в lora_registry.json
        LoraManager.register_character_lora(char_name, file_stem, trained_words)
        self.lora_installed.emit(file_stem, char_name, trained_words)
        self.accept()

    def _on_download_error(self, err: str):
        self._reset_dl_ui()
        QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось завершить загрузку LoRA:\n{err}")

    def _reset_dl_ui(self):
        self.btn_download.setEnabled(True)
        self.btn_cancel_dl.setVisible(False)
        self.download_box.setVisible(False)
        self.dl_progress_bar.setValue(0)

    def closeEvent(self, event):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.abort()
            self._thumb_worker.wait(150)
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.abort()
            self._download_worker.wait(200)
        super().closeEvent(event)
