"""
Knowledge Manager Dialog for Elaria's Knowledge Base (NeonRoute).
Strictly built using PyQt-Fluent-Widgets (qfluentwidgets) components.
Provides document viewing, trigger chips preview, folder opening, import, and sync.
"""

import os
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QFrame, QLabel
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    TitleLabel,
    CaptionLabel,
    StrongBodyLabel,
    PrimaryPushButton,
    PushButton,
    TransparentToolButton,
    FluentIcon as FIF,
    SingleDirectionScrollArea,
    SimpleCardWidget,
    PillPushButton,
    FlowLayout,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    IconWidget,
    setTheme,
    Theme
)

from services.knowledge_manager import KnowledgeManager

logger = logging.getLogger("NeonRoute.KnowledgeManagerDialog")


class DocumentCardWidget(SimpleCardWidget):
    """
    Карточка документа базы знаний на базе SimpleCardWidget qfluentwidgets.
    Отображает иконку типа, имя файла, метаданные, чипы извлеченных триггеров и кнопку удаления.
    """

    def __init__(self, doc: Dict[str, Any], on_delete: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.doc = doc
        self.filename = doc.get("filename", "")
        self.on_delete = on_delete

        self.setObjectName("DocumentCardWidget")
        self.setStyleSheet("""
            SimpleCardWidget#DocumentCardWidget {
                background-color: rgba(18, 24, 38, 0.85);
                border: 1px solid rgba(0, 240, 255, 0.18);
                border-radius: 10px;
                padding: 10px;
            }
            SimpleCardWidget#DocumentCardWidget:hover {
                border-color: rgba(0, 240, 255, 0.40);
                background-color: rgba(22, 30, 48, 0.95);
            }
        """)

        self._setup_ui()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 10, 12, 10)
        root_layout.setSpacing(8)

        # 1. Верхняя строка: Иконка + Имя и метаданные + Кнопка удаления
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)

        # Определение иконки файла
        ext = Path(self.filename).suffix.lower()
        icon = FIF.PDF if ext == ".pdf" else FIF.DOCUMENT
        icon_widget = IconWidget(icon, self)
        icon_widget.setFixedSize(24, 24)
        header_layout.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        # Текстовые метаданные
        title_meta_layout = QVBoxLayout()
        title_meta_layout.setSpacing(2)

        name_lbl = StrongBodyLabel(self.filename, self)
        name_lbl.setStyleSheet("color: #F8FAFC; font-weight: 600; font-size: 13px;")
        title_meta_layout.addWidget(name_lbl)

        size_kb = self.doc.get("size_kb", 0.0)
        lines = self.doc.get("line_count", 0)
        pages = self.doc.get("page_count")
        updated = self.doc.get("updated_at", "")
        date_str = updated[:10] if updated else ""

        if pages is not None:
            count_str = f"{pages} стр."
        else:
            count_str = f"{lines} строк"

        meta_text = f"{size_kb:.1f} КБ • {count_str}"
        if date_str:
            meta_text += f" • {date_str}"

        meta_lbl = CaptionLabel(meta_text, self)
        meta_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
        title_meta_layout.addWidget(meta_lbl)

        header_layout.addLayout(title_meta_layout, 1)

        # Кнопка удаления
        btn_delete = TransparentToolButton(FIF.DELETE, self)
        btn_delete.setToolTip("Удалить документ")
        btn_delete.clicked.connect(lambda: self.on_delete(self.filename))
        header_layout.addWidget(btn_delete, 0, Qt.AlignmentFlag.AlignVCenter)

        root_layout.addLayout(header_layout)

        # 2. Нижняя строка: Контейнер триггеров
        triggers: List[str] = self.doc.get("triggers", [])
        if triggers:
            triggers_frame = QWidget(self)
            triggers_layout = FlowLayout(triggers_frame, needAni=False)
            triggers_layout.setContentsMargins(0, 2, 0, 0)
            triggers_layout.setSpacing(6)

            for trig in triggers:
                chip = PillPushButton(trig, triggers_frame)
                chip.setEnabled(False)  # Чип только для отображения
                chip.setFixedHeight(22)
                chip.setStyleSheet("""
                    PillPushButton {
                        background-color: rgba(0, 240, 255, 0.08);
                        color: #00F0FF;
                        border: 1px solid rgba(0, 240, 255, 0.28);
                        border-radius: 11px;
                        padding: 1px 10px;
                        font-size: 11px;
                        font-weight: 500;
                    }
                    PillPushButton:disabled {
                        background-color: rgba(0, 240, 255, 0.08);
                        color: #00F0FF;
                        border: 1px solid rgba(0, 240, 255, 0.28);
                    }
                """)
                triggers_layout.addWidget(chip)

            root_layout.addWidget(triggers_frame)
        else:
            no_trig = CaptionLabel("Триггеры не извлечены (документ пуст или содержит только код/разметку)", self)
            no_trig.setStyleSheet("color: #64748B; font-style: italic; font-size: 11px;")
            root_layout.addWidget(no_trig)


class KnowledgeManagerDialog(FramelessDialog):
    """
    Модальное окно управления базой знаний Эларии.
    Построено исключительно на компонентах PyQt-Fluent-Widgets (qfluentwidgets).
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setWindowTitle("Знания Эларии")
        self.resize(780, 560)
        self.setMinimumSize(640, 460)

        # Стилизация темного Fluent-окна
        self.setStyleSheet("""
            KnowledgeManagerDialog {
                background-color: #0E131F;
                border: 1px solid rgba(0, 240, 255, 0.22);
                border-radius: 12px;
            }
        """)

        # Fluent TitleBar
        self.titleBar = FluentTitleBar(self)
        self.titleBar.setTitle("")
        self.setTitleBar(self.titleBar)

        self._setup_ui()
        self.reload_documents()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 40, 24, 20)
        root_layout.setSpacing(14)

        # 1. Заголовок и счетчик + кнопка закрытия [ ✕ ]
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(4)

        self.title_label = TitleLabel("Знания Эларии", self)
        self.title_label.setStyleSheet("color: #00F0FF; font-size: 20px; font-weight: 700;")
        header_text_layout.addWidget(self.title_label)

        self.caption_label = CaptionLabel("0 документов • 0 триггеров", self)
        self.caption_label.setStyleSheet("color: #94A3B8; font-size: 12px;")
        header_text_layout.addWidget(self.caption_label)

        header_layout.addLayout(header_text_layout, 1)

        # Кнопка закрытия [ ✕ ]
        self.close_btn = TransparentToolButton(FIF.CLOSE, self)
        self.close_btn.setToolTip("Закрыть")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)
        self.btn_close = self.close_btn  # Дополнительный алиас
        header_layout.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        root_layout.addLayout(header_layout)

        # 2. Тулбар действий
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)

        self.btn_add = PrimaryPushButton(FIF.ADD, "Добавить документ", self)
        self.btn_add.setToolTip("Импортировать файл в базу знаний")
        self.btn_add.clicked.connect(self._on_add_document_clicked)
        toolbar_layout.addWidget(self.btn_add)

        self.btn_open_folder = PushButton(FIF.FOLDER, "Открыть папку", self)
        self.btn_open_folder.setToolTip("Открыть проводник с папкой knowledge_base/")
        self.btn_open_folder.clicked.connect(self._on_open_folder_clicked)
        toolbar_layout.addWidget(self.btn_open_folder)

        self.btn_sync = TransparentToolButton(FIF.SYNC, self)
        self.btn_sync.setToolTip("Синхронизировать")
        self.btn_sync.clicked.connect(self._on_sync_clicked)
        toolbar_layout.addWidget(self.btn_sync)

        toolbar_layout.addStretch(1)
        root_layout.addLayout(toolbar_layout)

        # 3. Список документов (ScrollArea)
        self.scroll_area = SingleDirectionScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                background-color: rgba(14, 19, 31, 0.6);
            }
        """)

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(10, 10, 10, 10)
        self.cards_layout.setSpacing(10)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.cards_container)
        root_layout.addWidget(self.scroll_area, 1)

    def reload_documents(self):
        """Перезагружает и отображает список карточек документов из реестра."""
        # Очистка существующих карточек
        while self.cards_layout.count() > 0:
            child = self.cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        documents = KnowledgeManager.get_documents()
        total_triggers = sum(len(d.get("triggers", [])) for d in documents)

        self.caption_label.setText(
            f"Проиндексировано документов: {len(documents)} • Ключевых триггеров: {total_triggers}"
        )

        if not documents:
            empty_frame = QWidget(self.cards_container)
            empty_layout = QVBoxLayout(empty_frame)
            empty_layout.setContentsMargins(20, 60, 20, 60)
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            empty_icon = IconWidget(FIF.BOOK_SHELF, empty_frame)
            empty_icon.setFixedSize(48, 48)
            empty_layout.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignCenter)

            empty_lbl = CaptionLabel("База знаний пуста.", empty_frame)
            empty_lbl.setStyleSheet("color: #CBD5E1; font-size: 13px; font-weight: 600; margin-top: 8px;")
            empty_layout.addWidget(empty_lbl, 0, Qt.AlignmentFlag.AlignCenter)

            sub_lbl = CaptionLabel("Нажмите «Добавить документ» или скопируйте файлы в папку knowledge_base/", empty_frame)
            sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
            empty_layout.addWidget(sub_lbl, 0, Qt.AlignmentFlag.AlignCenter)

            self.cards_layout.addWidget(empty_frame)
            return

        # Добавляем карточки документов
        for doc in documents:
            card = DocumentCardWidget(doc, on_delete=self._on_delete_document, parent=self.cards_container)
            self.cards_layout.addWidget(card)

    def _on_add_document_clicked(self):
        """Открытие диалога выбора файлов для добавления в базу знаний."""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите документы для базы знаний Эларии",
            "",
            "Все поддерживаемые документы (*.pdf *.docx *.xlsx *.txt *.md *.json *.yaml *.yml *.toml *.csv *.py *.js *.ts *.cpp *.cs *.rs *.sql *.sh *.bat *.fb2);;Документы Office (*.pdf *.docx *.xlsx);;Исходный код и конфиги (*.py *.js *.ts *.cpp *.cs *.rs *.sql *.json *.yaml *.toml);;Все файлы (*.*)"
        )
        if not file_paths:
            return

        added_count = 0
        for f_path in file_paths:
            if KnowledgeManager.import_file(f_path):
                added_count += 1

        self.reload_documents()

        if added_count > 0:
            InfoBar.success(
                title="Документы добавлены",
                content=f"Успешно импортировано и проиндексировано файлов: {added_count}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3000
            )
        else:
            InfoBar.error(
                title="Ошибка импорта",
                content="Не удалось импортировать выбранные файлы.",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3000
            )

    def _on_open_folder_clicked(self):
        """Открывает системную папку knowledge_base/."""
        k_dir = KnowledgeManager.get_knowledge_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(k_dir)))

    def _on_sync_clicked(self):
        """Ручной запуск сканирования и синхронизации реестра."""
        try:
            reg = KnowledgeManager.scan_and_sync()
            self.reload_documents()
            doc_count = reg.get("total_documents", len(reg.get("documents", {})))
            InfoBar.success(
                title="Синхронизация завершена",
                content=f"База знаний актуализирована. Документов в реестре: {doc_count}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500
            )
        except Exception as e:
            logger.error(f"[KnowledgeManagerDialog] Ошибка синхронизации: {e}", exc_info=True)
            InfoBar.error(
                title="Ошибка синхронизации",
                content=str(e),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )

    def _on_delete_document(self, filename: str):
        """Удаление документа с подтверждением пользователя."""
        box = MessageBox(
            "Удаление документа",
            f"Вы действительно хотите удалить документ «{filename}» из базы знаний?",
            self
        )
        box.yesButton.setText("Удалить")
        box.cancelButton.setText("Отмена")

        if box.exec():
            success = KnowledgeManager.delete_file(filename)
            self.reload_documents()
            if success:
                InfoBar.success(
                    title="Удалено",
                    content=f"Документ «{filename}» удален из базы знаний.",
                    parent=self,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=2500
                )
            else:
                InfoBar.error(
                    title="Ошибка удаления",
                    content=f"Не удалось удалить документ «{filename}».",
                    parent=self,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=3000
                )
