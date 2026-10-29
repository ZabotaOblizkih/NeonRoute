import os
from pathlib import Path
from typing import Optional, Set
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QColor, QTextFormat, QPainter, QFont, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPlainTextEdit, QTreeWidgetItem, QTreeWidget, QHeaderView, QLabel
)
from qfluentwidgets import (
    CardWidget, TransparentToolButton, FluentIcon,
    TreeWidget, CaptionLabel, SubtitleLabel
)
from services.sandbox_manager import SandboxManager


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
    """Просмотрщик кода с нумерацией строк и точечной подсветкой сбоев."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
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
                sel = QPlainTextEdit.ExtraSelection()
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


class WorkspaceInspectorWidget(CardWidget):
    """Правая панель инспектора песочницы с автоматической синхронизацией файлов."""
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkspaceInspector")
        self.active_file_path: Optional[Path] = None
        self._known_files: Set[str] = set()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Шапка панели: заголовок + кнопка ручного обновления (сохранена)
        header_layout = QHBoxLayout()
        self.title_lbl = SubtitleLabel("📁 Песочница", self)
        self.title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #00F0FF;")
        self.btn_refresh = TransparentToolButton(FluentIcon.SYNC, self)
        self.btn_refresh.setToolTip("Обновить файлы вручную")
        self.btn_refresh.clicked.connect(self.refresh_files)

        header_layout.addWidget(self.title_lbl)
        header_layout.addStretch(1)
        header_layout.addWidget(self.btn_refresh)
        main_layout.addLayout(header_layout)

        # Сплиттер: дерево файлов и просмотрщик кода
        v_splitter = QSplitter(Qt.Orientation.Vertical, self)
        v_splitter.setHandleWidth(2)

        self.file_tree = TreeWidget(self)
        self.file_tree.setHeaderHidden(True)
        self.file_tree.itemClicked.connect(self._on_tree_item_clicked)
        v_splitter.addWidget(self.file_tree)

        code_box = QWidget(self)
        code_layout = QVBoxLayout(code_box)
        code_layout.setContentsMargins(0, 4, 0, 0)
        code_layout.setSpacing(4)

        self.file_name_lbl = CaptionLabel("Файл не выбран", self)
        self.file_name_lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
        self.editor = CodeEditorViewer(self)

        code_layout.addWidget(self.file_name_lbl)
        code_layout.addWidget(self.editor, 1)
        v_splitter.addWidget(code_box)

        v_splitter.setSizes([140, 400])
        main_layout.addWidget(v_splitter, 1)

        # Автоматическое отслеживание изменений файловой системы
        self._watcher = QFileSystemWatcher(self)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(250)
        self._debounce_timer.timeout.connect(self.refresh_files)

        self._watcher.directoryChanged.connect(lambda _: self._debounce_timer.start())
        self._watcher.fileChanged.connect(lambda _: self._debounce_timer.start())
        self._init_watcher()

    def _init_watcher(self):
        """Регистрирует директорию песочницы в наблюдателе."""
        ws_dir = SandboxManager.get_workspace_dir()
        if not ws_dir.exists():
            try:
                ws_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
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
        # 1. Если появился новый файл — автоматически открываем его в редакторе
        if new_files:
            latest_file = new_files[-1]
            self.open_file(latest_file)
        # 2. Если файл уже открыт — перезагружаем его содержимое (если модель изменила код)
        elif self.active_file_path and self.active_file_path.is_file():
            self._reload_active_file()
            if target_tree_item:
                self.file_tree.setCurrentItem(target_tree_item)
        # 3. Если ничего не открыто, но есть файлы — открываем первый скрипт
        elif current_files and not self.active_file_path:
            py_files = [f for f in current_files if f.suffix == ".py"]
            target = py_files[0] if py_files else current_files[0]
            self.open_file(target)

        self._known_files = {str(p.resolve()) for p in current_files}

    def _on_tree_item_clicked(self, item, _):
        path_str = item.data(0, Qt.ItemDataRole.UserRole)
        if path_str and Path(path_str).is_file():
            self.open_file(Path(path_str))

    def open_file(self, file_path: Path):
        self.active_file_path = file_path
        self.file_name_lbl.setText(f"📄 {file_path.name}")
        self._reload_active_file()

    def _reload_active_file(self):
        if not self.active_file_path or not self.active_file_path.is_file():
            return
        try:
            content = self.active_file_path.read_text(encoding="utf-8", errors="ignore")
            curr_cursor = self.editor.textCursor()
            curr_pos = curr_cursor.position()
            self.editor.setPlainText(content)
            curr_cursor.setPosition(min(curr_pos, len(content)))
            self.editor.setTextCursor(curr_cursor)
        except Exception as e:
            self.editor.setPlainText(f"# Не удалось прочитать файл: {e}")

    def show_error(self, filename: str, line_num: int, error_msg: str):
        ws_dir = SandboxManager.get_workspace_dir()
        target = ws_dir / filename
        if target.is_file() and (not self.active_file_path or self.active_file_path.name != filename):
            self.open_file(target)
        self.editor.highlight_error_line(line_num, error_msg)

    def mark_success(self):
        self.editor.clear_error_highlight()
