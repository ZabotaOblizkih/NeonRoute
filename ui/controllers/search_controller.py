"""
Chat Search Controller (Mixin) for NeonRoute MainWindow.
Manages in-chat text query search, occurrence counting, match highlighting,
and smooth auto-scrolling between matches.
"""

import re
import logging
from typing import TYPE_CHECKING, List, Optional, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QLabel, QLineEdit, QScrollArea, QPushButton
from PyQt6.QtGui import QTextCursor, QTextDocument, QColor

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger("NeonRoute.ChatSearch")


class ChatSearchMixin:
    """
    Миксин внутричатового поиска для MainWindow.
    Изолирует всю механику поиска, подсветки совпадений и навигации.
    """

    def _on_search_clicked(self):
        try:
            self._toggle_search_bar()
        except Exception as e:
            logger.error(f"Error in _on_search_clicked: {e}", exc_info=True)

    def _show_search_bar(self):
        self.search_bar_widget.show_and_focus()

    def _toggle_search_bar(self):
        if self.search_bar_widget.isVisible():
            self._close_search_bar()
        else:
            self._show_search_bar()

    def _close_search_bar(self):
        self.search_bar_widget.setVisible(False)
        self._clear_search_highlight()
        self.input_box.setFocus()

    def _on_search_query_changed(self, query: str):
        query = query.strip()
        self._search_matching_widgets.clear()
        self._current_search_idx = -1

        if not query:
            self.search_bar_widget.set_match_info(0, 0)
            return

        q_lower = query.lower()
        for w in self._get_chat_message_widgets():
            txt = ""
            if hasattr(w, "text_browser") and w.text_browser:
                txt = w.text_browser.toPlainText()
            elif hasattr(w, "raw_text"):
                txt = w.raw_text or ""
            if q_lower in txt.lower():
                self._search_matching_widgets.append(w)

        total = len(self._search_matching_widgets)
        if total > 0:
            self._current_search_idx = 0
            self.search_bar_widget.set_match_info(1, total)
            self._navigate_search_match(0)
        else:
            self.search_bar_widget.set_match_info(0, 0)

    def _clear_search_highlight(self):
        self._search_matching_widgets.clear()
        self._current_search_idx = -1

    def _search_next(self):
        total = len(self._search_matching_widgets)
        if total == 0:
            return
        self._current_search_idx = (self._current_search_idx + 1) % total
        self.search_bar_widget.set_match_info(self._current_search_idx + 1, total)
        self._navigate_search_match(self._current_search_idx)

    def _search_prev(self):
        total = len(self._search_matching_widgets)
        if total == 0:
            return
        self._current_search_idx = (self._current_search_idx - 1) % total
        self.search_bar_widget.set_match_info(self._current_search_idx + 1, total)
        self._navigate_search_match(self._current_search_idx)

    def _navigate_search_match(self, idx: int):
        if 0 <= idx < len(self._search_matching_widgets):
            w = self._search_matching_widgets[idx]
            if hasattr(self, "scroll_area") and self.scroll_area:
                self.scroll_area.ensureWidgetVisible(w)
            query = self.search_bar_widget.search_input.text().strip()
            if query and hasattr(w, "text_browser") and w.text_browser:
                doc = w.text_browser.document()
                found_cur = doc.find(query)
                if not found_cur.isNull():
                    w.text_browser.setTextCursor(found_cur)
