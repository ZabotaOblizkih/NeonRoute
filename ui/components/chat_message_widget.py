"""
Knowledge HUD and Chat Message Widget Components for NeonRoute.
Provides:
1. KnowledgeSourceChip: compact neon micro-chip / badge with icon, filename, chunk number, and citation tooltip.
2. KnowledgeHUDWidget: Glassmorphism container displaying chips above assistant response bubble.
3. Re-exports ChatMessageWidget and AssistantChatMessageWidget for unified access.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Union

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QLabel, QLayout, QSizePolicy
)
from qfluentwidgets import (
    FluentIcon as FIF,
    IconWidget,
    ToolTipFilter,
    ToolTipPosition,
)

# Re-exports for clean API and tests
from ui.common.icon_utils import SafeFIF
from ui.components.chat_bubble import ChatMessageWidget
from ui.components.assistant_chat_bubble import AssistantChatMessageWidget
from ui.components.thought_spoiler_widget import ThoughtSpoilerWidget


class KnowledgeSourceChip(QFrame):
    """
    Микро-чип / бейдж источника знаний для HUD-индикатора.
    - Иконка: SafeFIF.INFO для памяти, FIF.DOCUMENT или SafeFIF.BOOK_SHELF для файлов.
    - Текст бейджа: компактное имя файла и номер чанка (#4), либо 🧠 Память #N.
    - Tooltip: всплывающая подсказка с цитатой и релевантностью.
    """
    def __init__(
        self,
        filename: str,
        chunk_id: Any = 0,
        snippet: str = "",
        score: float = 0.0,
        is_memory: bool = False,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.setObjectName("KnowledgeSourceChip")
        self.filename = filename or "Документ"
        self.chunk_id = chunk_id
        self.snippet = snippet or ""
        self.score = float(score)
        self.is_memory = is_memory

        # Вычисление номера чанка
        cid_str = str(chunk_id) if chunk_id is not None else "0"
        if "#c" in cid_str:
            c_num = cid_str.split("#c")[-1]
        elif cid_str.startswith("#"):
            c_num = cid_str[1:]
        else:
            c_num = cid_str

        c_display = f"#{c_num}" if c_num not in ("", "None", "0") else ""

        if self.is_memory:
            icon = SafeFIF.INFO
            badge_text = f"🧠 Память {c_display}".strip() if c_display else "🧠 Память"
            text_color = "#C084FC"
            chip_style = """
                #KnowledgeSourceChip {
                    background-color: rgba(168, 85, 247, 0.12);
                    border: 1px solid rgba(168, 85, 247, 0.35);
                    border-radius: 4px;
                }
                #KnowledgeSourceChip:hover {
                    background-color: rgba(168, 85, 247, 0.22);
                    border: 1px solid rgba(168, 85, 247, 0.60);
                }
            """
        else:
            compact_name = Path(self.filename).name
            badge_text = f"{compact_name} {c_display}".strip() if c_display else compact_name
            ext = Path(self.filename).suffix.lower()
            if ext in (".csv", ".tsv", ".xlsx"):
                icon = FIF.DOCUMENT
            elif ext in (".pdf", ".fb2", ".epub", ".docx"):
                icon = SafeFIF.BOOK_SHELF
            else:
                icon = FIF.DOCUMENT
            text_color = "#38BDF8"
            chip_style = """
                #KnowledgeSourceChip {
                    background-color: rgba(0, 240, 255, 0.08);
                    border: 1px solid rgba(0, 240, 255, 0.25);
                    border-radius: 4px;
                }
                #KnowledgeSourceChip:hover {
                    background-color: rgba(0, 240, 255, 0.16);
                    border: 1px solid rgba(0, 240, 255, 0.50);
                }
            """

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(5)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        icon_w = IconWidget(icon, self)
        icon_w.setFixedSize(12, 12)
        layout.addWidget(icon_w)

        lbl = QLabel(badge_text, self)
        lbl.setStyleSheet(
            f"color: {text_color}; font-size: 11px; font-weight: 600; "
            "font-family: 'Segoe UI', 'Inter', sans-serif; background: transparent;"
        )
        layout.addWidget(lbl)

        self.setStyleSheet(chip_style)

        # Формирование тултипа с цитатой
        clean_snip = self.snippet.strip()
        if len(clean_snip) > 350:
            clean_snip = clean_snip[:350] + "..."
        elif clean_snip and not clean_snip.endswith("..."):
            clean_snip += "..."

        if self.is_memory:
            self.tooltip_text = (
                f"🧠 Долговременная память\n"
                f"Извлечённые факты о вас:\n"
                f"---\n"
                f"{clean_snip}"
            )
        else:
            frag_str = f"Фрагмент #{c_num}" if c_num not in ("", "None") else "Фрагмент #0"
            self.tooltip_text = (
                f"Источник: {self.filename}\n"
                f"{frag_str} (релевантность: {self.score:.0%})\n"
                f"---\n"
                f"{clean_snip}"
            )
        self.setToolTip(self.tooltip_text)
        self.installEventFilter(ToolTipFilter(self, showDelay=150, position=ToolTipPosition.TOP))

    def mousePressEvent(self, event):
        """Безопасная обработка клика по чипу: показывает тултип/цитату без попытки открыть несуществующий файл."""
        try:
            from PyQt6.QtWidgets import QToolTip
            from PyQt6.QtGui import QCursor
            QToolTip.showText(QCursor.pos(), self.tooltip_text, self)
        except Exception:
            pass
        super().mousePressEvent(event)


class KnowledgeHUDWidget(QFrame):
    """
    Компактный HUD-контейнер использованных источников базы знаний.
    Стилизация: полупрозрачный фон Glassmorphism (rgba(10, 13, 20, 0.75)),
    тонкая неоновая рамка Cyan (rgba(0, 240, 255, 0.2)), радиус скругления 6–8px.
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("KnowledgeHUDContainer")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self.hud_layout = QHBoxLayout(self)
        self.hud_layout.setContentsMargins(6, 4, 6, 4)
        self.hud_layout.setSpacing(6)
        self.hud_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.setStyleSheet("""
            #KnowledgeHUDContainer {
                background-color: rgba(10, 13, 20, 0.75);
                border: 1px solid rgba(0, 240, 255, 0.20);
                border-radius: 7px;
            }
        """)
        self.chips: List[KnowledgeSourceChip] = []
        self.setVisible(False)

    def set_sources(self, sources: List[Dict[str, Any]]):
        """Монтирует чипы для переданных источников либо скрывает контейнер при их отсутствии."""
        while self.hud_layout.count():
            item = self.hud_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.chips.clear()

        if not sources:
            self.setVisible(False)
            self.hide()
            return

        seen = set()
        for s in sources:
            fn = s.get("filename", "")
            cid = s.get("chunk_id", 0)
            is_mem = bool(s.get("is_memory", False)) or ("память" in str(fn).lower())
            key = (fn, str(cid))
            if key in seen:
                continue
            seen.add(key)

            chip = KnowledgeSourceChip(
                filename=fn,
                chunk_id=cid,
                snippet=s.get("snippet", ""),
                score=float(s.get("score", 0.0)),
                is_memory=is_mem,
                parent=self
            )
            self.chips.append(chip)
            self.hud_layout.addWidget(chip)

        self.hud_layout.addStretch()
        self.setVisible(True)
        self.show()
