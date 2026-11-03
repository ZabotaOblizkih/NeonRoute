import os
import logging
from pathlib import Path
from typing import Optional, Set
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QColor, QTextFormat, QPainter, QFont, QSyntaxHighlighter, QTextCharFormat, QKeyEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPlainTextEdit, QTextEdit, QTreeWidgetItem, QTreeWidget, QHeaderView, QLabel, QFrame,
    QPushButton, QApplication
)
import qfluentwidgets
from qfluentwidgets import (
    CardWidget, TransparentToolButton, ToolButton, FluentIcon, FluentIcon as FIF,
    TreeWidget, CaptionLabel, SubtitleLabel, SimpleCardWidget, BodyLabel, InfoBadge,
    MessageBoxBase, LineEdit, RoundMenu, Action
)
from services.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)


class InteractiveTitleChip(QPushButton):
    """Интерактивный заголовок-чип песочницы без рамок.
    ЛКМ — открытие директории песочницы.
    ПКМ — переименование сессии.
    """
    left_clicked = pyqtSignal()
    right_clicked = pyqtSignal()

    def __init__(self, text: str = "📁 Песочница", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("ЛКМ: Открыть папку песочницы в Проводнике\nПКМ: Переименовать диалог")
        self.setStyleSheet("""
            InteractiveTitleChip {
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                border-radius: 6px;
                color: #00F0FF;
                font-size: 13px;
                font-weight: bold;
                padding: 4px 10px;
                text-align: left;
            }
            InteractiveTitleChip:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #38BDF8;
            }
            InteractiveTitleChip:pressed {
                background-color: rgba(255, 255, 255, 0.18);
            }
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.left_clicked.emit()
            super().mousePressEvent(event)
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class InputDialog(MessageBoxBase):
    """Диалог ввода комментария к коммиту в стиле Fluent."""
    def __init__(self, title: str, content: str, parent=None):
        super().__init__(parent)
        self.title_label = SubtitleLabel(title, self)
        self.content_label = BodyLabel(content, self)
        self.input_line_edit = LineEdit(self)
        self.input_line_edit.setClearButtonEnabled(True)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.content_label)
        self.viewLayout.addWidget(self.input_line_edit)

        self.yesButton.setText("OK")
        self.cancelButton.setText("Отмена")
        self.widget.setMinimumWidth(380)

    def text(self) -> str:
        return self.input_line_edit.text()

    def setText(self, text: str):
        self.input_line_edit.setText(text)


# Регистрируем InputDialog в qfluentwidgets для совместимости
qfluentwidgets.InputDialog = InputDialog


class LineNumberArea(QWidget):
    """Боковая полоса нумерации строк с маркером ошибок."""
    def __init__(self, editor):
        super().__init__(editor)
        self.code_editor = editor

    def sizeHint(self):
        return self.code_editor.lineNumberAreaSize()

    def paintEvent(self, event):
        self.code_editor.lineNumberAreaPaintEvent(event)


class PythonHighlighter(QSyntaxHighlighter):
    """Легковесная неоновая подсветка синтаксиса Python."""
    def __init__(self, document):
        super().__init__(document)
        self.rules = []
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor("#00F0FF"))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        keywords = [
            "def", "class", "return", "if", "elif", "else", "while", "for",
            "in", "try", "except", "finally", "import", "from", "as", "with",
            "None", "True", "False", "lambda", "pass", "raise"
        ]
        for kw in keywords:
            pattern = rf"\b{kw}\b"
            import re
            self.rules.append((re.compile(pattern), kw_fmt))

        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor("#FBBF24"))
        self.rules.append((re.compile(r"(['\"].*?['\"])"), str_fmt))

        comm_fmt = QTextCharFormat()
        comm_fmt.setForeground(QColor("#64748B"))
        self.rules.append((re.compile(r"#[^\n]*"), comm_fmt))

    def highlightBlock(self, text: str):
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                start, end = match.span()
                self.setFormat(start, end - start, fmt)


class CodeEditorViewer(QPlainTextEdit):
    """Просмотрщик и редактор кода с нумерацией строк, хоткеями и подсветкой ошибок."""
    sig_save_requested = pyqtSignal()
    sig_test_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(False)
        self.line_number_area = LineNumberArea(self)
        self.error_lineno: Optional[int] = None
        self.error_message: str = ""
        self.highlighter = PythonHighlighter(self.document())

        font = QFont("Cascadia Code, Consolas, Courier New", 10)
        self.setFont(font)
        self.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0A0E17;
                color: #CBD5E1;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)

        self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.updateRequest.connect(self.updateLineNumberArea)
        self.updateLineNumberAreaWidth(0)

    def keyPressEvent(self, event: QKeyEvent):
        if (event.modifiers() == Qt.KeyboardModifier.ControlModifier) and event.key() == Qt.Key.Key_S:
            self.sig_save_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_F5 or ((event.modifiers() == Qt.KeyboardModifier.ControlModifier) and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            self.sig_test_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def lineNumberAreaWidth(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 16 + self.fontMetrics().horizontalAdvance('9') * digits

    def updateLineNumberAreaWidth(self, _):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def updateLineNumberArea(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.updateLineNumberAreaWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))

    def lineNumberAreaPaintEvent(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#06090F"))
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_str = str(block_num + 1)
                is_err = (self.error_lineno == (block_num + 1))
                painter.setPen(QColor("#F43F5E") if is_err else QColor("#475569"))
                if is_err:
                    painter.drawText(2, top, 12, self.fontMetrics().height(), Qt.AlignmentFlag.AlignCenter, "❌")
                painter.drawText(
                    0, top, self.line_number_area.width() - 4, self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, line_str
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_num += 1

    def highlight_error_line(self, line_num: int, err_msg: str = ""):
        self.error_lineno = line_num
        self.error_message = err_msg

        selections = []
        if line_num > 0:
            block = self.document().findBlockByNumber(line_num - 1)
            if block.isValid():
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(QColor(244, 63, 94, 60))
                sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                cursor = self.textCursor()
                cursor.setPosition(block.position())
                sel.cursor = cursor
                selections.append(sel)
                self.setTextCursor(cursor)
                self.ensureCursorVisible()

        self.setExtraSelections(selections)
        self.line_number_area.update()

    def clear_error_highlight(self):
        self.error_lineno = None
        self.error_message = ""
        self.setExtraSelections([])
        self.line_number_area.update()

    def start_streaming_mode(self, filename: str):
        """Подготавливает редактор к приёму живого потока токенов."""
        self.setReadOnly(True)
        self.blockSignals(True)
        self.clear()
        self.clear_error_highlight()
        self.blockSignals(False)

    def append_stream_chunk(self, chunk: str):
        """Потокобезопасно добавляет фрагмент кода и прокручивает каретку вниз."""
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(chunk)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def finish_streaming_mode(self, clean_code: Optional[str] = None):
        """Завершает стриминг, синхронизирует финальный чистый код и обновляет подсветку."""
        if clean_code is not None:
            sb = self.verticalScrollBar()
            val = sb.value() if sb else 0
            self.setPlainText(clean_code)
            if sb:
                sb.setValue(val)
        self.setReadOnly(False)


class VerificationStatusWidget(SimpleCardWidget):
    """Нижняя панель верификации и результатов тестирования песочницы."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self.setStyleSheet("""
            VerificationStatusWidget {
                background-color: #0F172A;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(3)

        top_row = QHBoxLayout()
        self.badge_status = InfoBadge.info("Ожидание")
        self.lbl_title = BodyLabel("Статус песочницы: готов к работе", self)
        self.lbl_title.setStyleSheet("font-weight: bold; color: #CBD5E1;")

        top_row.addWidget(self.badge_status)
        top_row.addWidget(self.lbl_title)
        top_row.addStretch(1)

        self.lbl_details = CaptionLabel("Тесты не запускались", self)
        self.lbl_details.setStyleSheet("color: #64748B;")

        layout.addLayout(top_row)
        layout.addWidget(self.lbl_details)

    def set_success(self, text: str = "Все проверки пройдены (AST-синтаксис, импорты, Smoke GUI)."):
        self.badge_status.setText("УСПЕХ")
        self.badge_status.setStyleSheet("background-color: #10B981; color: white; padding: 2px 6px;")
        self.lbl_title.setText("✅ Код стабилен (exit_code == 0)")
        self.lbl_title.setStyleSheet("font-weight: bold; color: #34D399;")
        self.lbl_details.setText(text)

    def set_error(self, err_type: str, details: str, line: int = None):
        self.badge_status.setText("СБОЙ")
        self.badge_status.setStyleSheet("background-color: #EF4444; color: white; padding: 2px 6px;")
        line_str = f" [Строка {line}]" if line and line > 0 else ""
        self.lbl_title.setText(f"❌ Ошибка: {err_type}{line_str}")
        self.lbl_title.setStyleSheet("font-weight: bold; color: #F87171;")
        first_line = details.strip().splitlines()[0] if details else "Скрипт завершился с ошибкой"
        self.lbl_details.setText(first_line[:120])

    def set_running(self, text: str = "Выполняется проверка скрипта в песочнице..."):
        self.badge_status.setText("ТЕСТ")
        self.badge_status.setStyleSheet("background-color: #3B82F6; color: white; padding: 2px 6px;")
        self.lbl_title.setText("⏳ Проверка кода")
        self.lbl_title.setStyleSheet("font-weight: bold; color: #60A5FA;")
        self.lbl_details.setText(text)

    def reset_status(self):
        self.badge_status.setText("Ожидание")
        self.badge_status.setStyleSheet("")
        self.lbl_title.setText("Статус песочницы: готов к работе")
        self.lbl_title.setStyleSheet("font-weight: bold; color: #CBD5E1;")
        self.lbl_details.setText("Тесты не запускались")


class WorkspaceInspectorWidget(CardWidget):
    """Правая панель инспектора песочницы с ручным редактированием, автосинхронизацией и запуском тестов."""
    file_selected = pyqtSignal(str)
    sig_stream_started = pyqtSignal(str)           # filename
    sig_stream_chunk = pyqtSignal(str)             # code_token
    sig_stream_finished = pyqtSignal(str, str)     # filename, final_code
    rename_session_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkspaceInspector")
        self.active_file_path: Optional[Path] = None
        self._known_files: Set[str] = set()
        self.is_dirty: bool = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Шапка панели: интерактивный чип-заголовок + кнопка обновления
        header_layout = QHBoxLayout()
        self.title_btn = InteractiveTitleChip("📁 Песочница", self)
        self.title_btn.left_clicked.connect(self.open_workspace_folder)
        self.title_btn.right_clicked.connect(self._on_right_click_rename)
        self.title_lbl = self.title_btn  # Сохраняем обратную совместимость атрибута
        self.btn_refresh = TransparentToolButton(FluentIcon.SYNC, self)
        self.btn_refresh.setToolTip("Обновить файлы вручную")
        self.btn_refresh.clicked.connect(self.refresh_files)

        header_layout.addWidget(self.title_btn)
        header_layout.addStretch(1)
        header_layout.addWidget(self.btn_refresh)
        main_layout.addLayout(header_layout)

        # Сплиттер: дерево файлов и просмотрщик/редактор кода
        v_splitter = QSplitter(Qt.Orientation.Vertical, self)
        v_splitter.setHandleWidth(2)

        self.file_tree = TreeWidget(self)
        self.file_tree.setHeaderHidden(True)
        self.file_tree.itemClicked.connect(self._on_tree_item_clicked)
        self.file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        v_splitter.addWidget(self.file_tree)

        code_box = QWidget(self)
        code_layout = QVBoxLayout(code_box)
        code_layout.setContentsMargins(0, 4, 0, 0)
        code_layout.setSpacing(4)

        editor_header = QHBoxLayout()
        self.file_name_lbl = CaptionLabel("Файл не выбран", self)
        self.file_name_lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
        self.file_name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.file_name_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_name_lbl.customContextMenuRequested.connect(self._show_file_label_context_menu)
        self.lbl_current_file = self.file_name_lbl

        self.btn_save = TransparentToolButton(FIF.SAVE, self)
        self.btn_save.setToolTip("Сохранить правки (Ctrl+S)")

        self.btn_test = TransparentToolButton(FIF.PLAY, self)
        self.btn_test.setToolTip("Запустить проверку (F5)")

        editor_header.addWidget(self.file_name_lbl)
        editor_header.addStretch(1)
        editor_header.addWidget(self.btn_save)
        editor_header.addWidget(self.btn_test)

        self.editor = CodeEditorViewer(self)

        code_layout.addLayout(editor_header)
        code_layout.addWidget(self.editor, 1)
        v_splitter.addWidget(code_box)

        v_splitter.setSizes([140, 400])
        main_layout.addWidget(v_splitter, 1)

        self.verification_panel = VerificationStatusWidget(self)
        main_layout.addWidget(self.verification_panel)

        # Привязка сигналов стриминга
        self.sig_stream_started.connect(self._on_stream_started)
        self.sig_stream_chunk.connect(self._on_stream_chunk)
        self.sig_stream_finished.connect(self._on_stream_finished)

        # Привязка сигналов ручного редактирования и кнопок
        self.editor.textChanged.connect(self._on_text_modified)
        self.editor.sig_save_requested.connect(self.save_manual_edits)
        self.editor.sig_test_requested.connect(self.run_manual_test)
        self.btn_save.clicked.connect(self.save_manual_edits)
        self.btn_test.clicked.connect(self.run_manual_test)

        # Автоматическое отслеживание изменений файловой системы
        self._watcher = QFileSystemWatcher(self)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(250)
        self._debounce_timer.timeout.connect(self.refresh_files)

        self._watcher.directoryChanged.connect(lambda _: self._debounce_timer.start())
        self._watcher.fileChanged.connect(lambda _: self._debounce_timer.start())
        self._init_watcher()

    def open_workspace_folder(self):
        """Открывает папку текущей песочницы в Проводнике Windows."""
        try:
            ws_dir = SandboxManager.get_workspace_dir()
            ws_dir.mkdir(parents=True, exist_ok=True)
            import os
            os.startfile(str(ws_dir))
        except Exception as e:
            logger.warning(f"Не удалось открыть папку песочницы: {e}")

    def _on_right_click_rename(self):
        """Эмитит сигнал запроса переименования для активной сессии."""
        try:
            sess_id = SandboxManager.get_active_session_id()
            if sess_id:
                self.rename_session_requested.emit(sess_id)
        except Exception as e:
            logger.warning(f"Ошибка запроса переименования: {e}")

    def update_title(self, title: str):
        """Обновляет текст заголовка песочницы."""
        clean = (title or "").strip()
        if not clean or clean.lower() == "диалог":
            clean = "Песочница"
        display_text = f"📁 {clean}" if not clean.startswith("📁") else clean
        if hasattr(self, "title_btn"):
            self.title_btn.setText(display_text)

    @property
    def current_file_path(self) -> Optional[Path]:
        return self.active_file_path

    @current_file_path.setter
    def current_file_path(self, path: Optional[Path]):
        self.active_file_path = path

    def _init_watcher(self):
        """Регистрирует директорию песочницы в наблюдателе (без насильного mkdir)."""
        ws_dir = SandboxManager.get_workspace_dir(create=False)
        if not ws_dir.exists():
            return

        watched = set(self._watcher.directories())
        ws_str = str(ws_dir.resolve())
        if ws_str not in watched:
            self._watcher.addPath(ws_str)

    def refresh_files(self):
        """Сканирует workspace, обновляет дерево и автоматически открывает новые файлы."""
        self._init_watcher()
        ws_dir = SandboxManager.get_workspace_dir()
        if not ws_dir.exists():
            return

        ignored = {".git", "__pycache__", ".pytest_cache", ".neonroute_last_failure.json"}
        current_files = []
        new_files = []

        # Актуализация отслеживаемых подкаталогов
        for root, dirs, _ in os.walk(ws_dir):
            dirs[:] = [d for d in dirs if d not in ignored]
            r_path = Path(root).resolve()
            r_str = str(r_path)
            if r_str not in self._watcher.directories():
                try:
                    self._watcher.addPath(r_str)
                except Exception:
                    pass

        for p in sorted(ws_dir.rglob("*")):
            if any(ign in p.parts for ign in ignored) or p.is_dir():
                continue
            p_str = str(p.resolve())
            current_files.append(p)
            if p_str not in self._known_files:
                new_files.append(p)

        self.file_tree.clear()
        items_map = {}
        target_tree_item = None

        for p in current_files:
            rel = p.relative_to(ws_dir)
            parent_item = self.file_tree
            if len(rel.parts) > 1:
                parent_dir = rel.parts[0]
                if parent_dir not in items_map:
                    dir_item = QTreeWidgetItem(self.file_tree, [f"📁 {parent_dir}"])
                    items_map[parent_dir] = dir_item
                parent_item = items_map[parent_dir]

            icon = "🐍" if p.suffix == ".py" else ("📊" if p.suffix in (".xlsx", ".csv") else ("📝" if p.suffix == ".docx" else "📄"))
            item = QTreeWidgetItem(parent_item, [f"{icon} {p.name}"])
            p_res = str(p.resolve())
            item.setData(0, Qt.ItemDataRole.UserRole, p_res)

            if self.active_file_path and str(self.active_file_path.resolve()) == p_res:
                target_tree_item = item

        self.file_tree.expandAll()

        # Автоматический выбор и отображение:
        if new_files:
            latest_file = new_files[-1]
            self.open_file(latest_file)
        elif self.active_file_path and self.active_file_path.is_file():
            if not self.is_dirty:
                self._reload_active_file()
            if target_tree_item:
                self.file_tree.setCurrentItem(target_tree_item)
        elif current_files and not self.active_file_path:
            py_files = [f for f in current_files if f.suffix == ".py"]
            target = py_files[0] if py_files else current_files[0]
            self.open_file(target)

        self._known_files = {str(p.resolve()) for p in current_files}

    def _on_tree_item_clicked(self, item, _):
        path_str = item.data(0, Qt.ItemDataRole.UserRole)
        if path_str and Path(path_str).is_file():
            self.open_file(Path(path_str))

    def _show_tree_context_menu(self, pos):
        """Контекстное меню ПКМ по элементу дерева файлов песочницы."""
        item = self.file_tree.itemAt(pos)
        if not item:
            return

        self.file_tree.setCurrentItem(item)
        path_str = item.data(0, Qt.ItemDataRole.UserRole)

        file_path = None
        if path_str:
            file_path = Path(path_str)
            file_name = file_path.name
        else:
            raw_text = item.text(0)
            file_name = raw_text.split()[-1] if raw_text else ""

        if not file_name:
            return

        menu = RoundMenu(parent=self.file_tree)

        # 1. Открыть в редакторе (если это файл)
        if file_path and file_path.is_file():
            act_open = Action(FIF.EDIT, "Открыть в редакторе", self)
            act_open.triggered.connect(lambda: self.open_file(file_path))
            menu.addAction(act_open)
            menu.addSeparator()

        # 2. Скопировать имя файла
        act_copy_name = Action(FIF.COPY, "Копировать имя файла", self)
        act_copy_name.triggered.connect(lambda: QApplication.clipboard().setText(file_name))
        menu.addAction(act_copy_name)

        # 3. Скопировать относительный путь (workspace/...)
        if file_path:
            try:
                ws_dir = SandboxManager.get_workspace_dir()
                rel_path = str(file_path.relative_to(ws_dir)).replace("\\", "/")
                act_copy_rel = Action(FIF.DOCUMENT, "Копировать относительный путь", self)
                act_copy_rel.triggered.connect(lambda: QApplication.clipboard().setText(rel_path))
                menu.addAction(act_copy_rel)
            except Exception:
                pass

            # 4. Скопировать полный путь
            act_copy_full = Action(FIF.FOLDER, "Копировать полный путь", self)
            act_copy_full.triggered.connect(lambda: QApplication.clipboard().setText(str(file_path.resolve())))
            menu.addAction(act_copy_full)

        global_pos = self.file_tree.viewport().mapToGlobal(pos)
        menu.exec(global_pos)

    def _show_file_label_context_menu(self, pos):
        """Контекстное меню ПКМ по названию файла над редактором."""
        if not getattr(self, "active_file_path", None):
            return

        menu = RoundMenu(parent=self.file_name_lbl)
        act_copy = Action(FIF.COPY, f"Копировать имя файла ({self.active_file_path.name})", self)
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(self.active_file_path.name))
        menu.addAction(act_copy)

        try:
            ws_dir = SandboxManager.get_workspace_dir()
            rel_path = str(self.active_file_path.relative_to(ws_dir)).replace("\\", "/")
            act_copy_rel = Action(FIF.DOCUMENT, "Копировать относительный путь", self)
            act_copy_rel.triggered.connect(lambda: QApplication.clipboard().setText(rel_path))
            menu.addAction(act_copy_rel)
        except Exception:
            pass

        menu.exec(self.file_name_lbl.mapToGlobal(pos))

    def open_file(self, file_path: Path):
        self.active_file_path = file_path
        self.file_name_lbl.setText(f"📄 {file_path.name}")
        self.file_name_lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
        self.is_dirty = False
        self._reload_active_file()

    def _reload_active_file(self):
        if not self.active_file_path or not self.active_file_path.is_file():
            return
        try:
            self.editor.blockSignals(True)
            content = self.active_file_path.read_text(encoding="utf-8", errors="ignore")
            curr_cursor = self.editor.textCursor()
            curr_pos = curr_cursor.position()
            self.editor.setPlainText(content)
            curr_cursor.setPosition(min(curr_pos, len(content)))
            self.editor.setTextCursor(curr_cursor)
        except Exception as e:
            self.editor.setPlainText(f"# Не удалось прочитать файл: {e}")
        finally:
            self.editor.blockSignals(False)
            self.is_dirty = False

    def _on_text_modified(self):
        """Отслеживание изменений в редакторе (грязный буфер)."""
        if not self.is_dirty and self.active_file_path:
            self.is_dirty = True
            self.lbl_current_file.setText(f"📄 {self.active_file_path.name} *")
            self.lbl_current_file.setStyleSheet("color: #FBBF24; font-weight: bold;")

    def save_manual_edits(self):
        """Сохраняет правки файла, анализирует git diff, запрашивает комментарий и фиксирует коммит."""
        if not self.active_file_path or not self.active_file_path.is_file():
            return

        new_code = self.editor.toPlainText()
        self.active_file_path.write_text(new_code, encoding="utf-8")
        ws_dir = SandboxManager.get_workspace_dir()
        filename = self.active_file_path.name

        line_ranges = SandboxManager.get_changed_line_ranges(ws_dir, filename)

        user_comment = ""
        try:
            dlg = InputDialog("Комментарий к коммиту", f"Изменения в {filename} ({line_ranges}):", self)
            dlg.input_line_edit.setPlaceholderText("Опишите суть изменений (или оставьте пустым)")
            if dlg.exec():
                user_comment = dlg.text().strip()
                SandboxManager.commit_manual_edit(ws_dir, filename, user_comment, line_ranges)
        except Exception:
            SandboxManager.commit_manual_edit(ws_dir, filename, user_comment, line_ranges)

        self.is_dirty = False
        self.lbl_current_file.setText(f"📄 {filename}")
        self.lbl_current_file.setStyleSheet("color: #CBD5E1; font-weight: normal;")
        self.verification_panel.lbl_details.setText(f"Сохранено: {line_ranges}")

    def run_manual_test(self):
        """Изолированный перезапуск проверки файла без обращения к LLM."""
        if not self.active_file_path:
            return
        if self.is_dirty:
            self.save_manual_edits()
        self.mark_running(f"Тестирование {self.active_file_path.name}...")
        ws_dir = SandboxManager.get_workspace_dir()
        success, err_type, err_msg, err_line = SandboxManager.verify_single_file(ws_dir, self.active_file_path.name)
        if success:
            self.mark_success()
        else:
            self.show_error(self.active_file_path.name, err_line, err_msg, err_type=err_type)

    def _on_stream_started(self, filename: str):
        self.is_dirty = False
        self.lbl_current_file.setText(f"📄 {filename} (генерация...)")
        self.lbl_current_file.setStyleSheet("color: #38BDF8; font-weight: bold;")
        self.editor.start_streaming_mode(filename)
        self.verification_panel.set_running(f"Генерация исходного кода {filename}...")

    def _on_stream_chunk(self, chunk: str):
        self.editor.append_stream_chunk(chunk)

    def _on_stream_finished(self, filename: str, final_code: str):
        self.lbl_current_file.setText(f"📄 {filename}")
        self.lbl_current_file.setStyleSheet("color: #CBD5E1; font-weight: normal;")
        self.editor.blockSignals(True)
        self.editor.finish_streaming_mode(final_code)
        self.editor.blockSignals(False)
        self.is_dirty = False

    def show_error(self, filename: str, line_num: int, error_msg: str, err_type: str = "Runtime / Syntax"):
        if filename:
            ws_dir = SandboxManager.get_workspace_dir()
            target = ws_dir / filename
            if target.is_file() and (not self.active_file_path or self.active_file_path.name != filename):
                self.open_file(target)
        if hasattr(self, "verification_panel"):
            self.verification_panel.set_error(err_type, error_msg, line=line_num)
        if hasattr(self, "editor"):
            self.editor.highlight_error_line(line_num or 0, error_msg)

    def mark_success(self):
        if hasattr(self, "verification_panel"):
            self.verification_panel.set_success()
        if hasattr(self, "editor"):
            self.editor.clear_error_highlight()

    def mark_running(self, text: str = "Выполняется верификация..."):
        if hasattr(self, "verification_panel"):
            self.verification_panel.set_running(text)
