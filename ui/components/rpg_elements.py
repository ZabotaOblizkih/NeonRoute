"""
Modular RPG UI Elements Component for NeonRoute.
Parses roleplay markup (<stats>, <thought>, <choices>, <meters>, <check>, <terminal>, <sms>)
and renders glassmorphic Cyberpunk widgets and formatted HTML blocks.
"""

import re
import html
from typing import Dict, Any, Optional, Tuple, List
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QLabel,
    QPushButton, QTextBrowser
)


class RPGBundle:
    def __init__(
        self,
        clean_text: str,
        stats: Optional[Any] = None,
        thought: Optional[str] = None,
        choices: Optional[List[str]] = None,
        meters: Optional[Dict[str, Any]] = None
    ):
        self.clean_text = clean_text
        self.stats = stats
        self.thought = thought
        self.choices = choices
        self.meters = meters


class RPGContentParser:
    THOUGHT_RE = re.compile(r'<(thought|think)>\s*(.*?)\s*</\1>', re.DOTALL | re.IGNORECASE)
    CHOICES_RE = re.compile(r'<(choices|options)>\s*(.*?)\s*</\1>', re.DOTALL | re.IGNORECASE)
    STATS_RE = re.compile(r'<(stats|status)>\s*(.*?)\s*</\1>', re.DOTALL | re.IGNORECASE)
    METERS_RE = re.compile(r'<(meters|affection)>\s*(.*?)\s*</\1>', re.DOTALL | re.IGNORECASE)
    CHECK_RE = re.compile(r'<check>\s*(.*?)\s*</check>', re.DOTALL | re.IGNORECASE)
    TERMINAL_RE = re.compile(r'<terminal>\s*(.*?)\s*</terminal>', re.DOTALL | re.IGNORECASE)
    SMS_RE = re.compile(r'<(sms|phone)>\s*(.*?)\s*</\1>', re.DOTALL | re.IGNORECASE)

    @classmethod
    def parse(cls, text: str) -> RPGBundle:
        if not text or ("<" not in text and "```" not in text):
            return RPGBundle(clean_text=text)

        norm = text.replace('\u2028', '\n').replace('\u2029', '\n').replace('\r\n', '\n')

        thought_text = None
        m_th = cls.THOUGHT_RE.search(norm)
        if m_th:
            thought_text = m_th.group(2).strip()
            norm = cls.THOUGHT_RE.sub('', norm)

        choices_list = None
        m_ch = cls.CHOICES_RE.search(norm)
        if m_ch:
            raw_c = m_ch.group(2).strip()
            lines = [ln.strip() for ln in raw_c.splitlines() if ln.strip()]
            if lines:
                choices_list = lines
            norm = cls.CHOICES_RE.sub('', norm)

        stats_data = None
        m_st = cls.STATS_RE.search(norm)
        if m_st:
            raw_st = m_st.group(2).strip()
            stats_data = cls._parse_key_values(raw_st)
            norm = cls.STATS_RE.sub('', norm)

        meters_data = None
        m_mt = cls.METERS_RE.search(norm)
        if m_mt:
            raw_mt = m_mt.group(2).strip()
            meters_data = cls._parse_meters(raw_mt)
            norm = cls.METERS_RE.sub('', norm)

        clean_text = norm.strip()
        bundle = RPGBundle(clean_text, stats_data, thought_text, choices_list, meters_data)

        # Fallback: Detect raw markdown code blocks containing RPG stats if <stats> tag was omitted
        if not bundle.stats and "```" in bundle.clean_text:
            code_block_pattern = re.compile(r'```(?:[a-zA-Z0-9_-]+)?\s*\n?(.*?)```', re.DOTALL)
            stat_markers = (
                "physical status:", "inventory:", "equipment:", "level:", "lvl:",
                "уровень:", "hp:", "health:", "здоровье:", "class:", "класс:",
                "battle power:", "bp:", "gold:", "золото:", "threat level:",
                "reputation:", "skills:", "spells:", "abilities:", "objectives:"
            )
            for match in code_block_pattern.finditer(bundle.clean_text):
                block_content = match.group(1).strip()
                block_lower = block_content.lower()
                matches_count = sum(1 for marker in stat_markers if marker in block_lower)
                if matches_count >= 2:
                    bundle.stats = block_content
                    # Clean out the code block from visible chat text
                    start_idx, end_idx = match.span()
                    bundle.clean_text = (bundle.clean_text[:start_idx] + bundle.clean_text[end_idx:]).strip()
                    break

        return bundle

    @classmethod
    def format_inline_rpg_blocks(cls, escaped_html: str) -> str:
        """Formats inline XML blocks safely AFTER html.escape to prevent double-escaping bugs."""
        # 1. Check (D&D d20)
        def _check_sub(m):
            raw_body = m.group(1).strip().replace('\n', '<br>')
            is_success = "успех" in raw_body.lower() or "success" in raw_body.lower()
            is_fail = "провал" in raw_body.lower() or "failure" in raw_body.lower()
            color = "#10B981" if is_success else ("#EF4444" if is_fail else "#F59E0B")
            badge = "✔ УСПЕХ" if is_success else ("✖ ПРОВАЛ" if is_fail else "🎲 ПРОВЕРКА")
            return (
                f"<div style='background:rgba(15, 23, 42, 0.9); border:1px solid {color}; "
                f"border-left:4px solid {color}; border-radius:6px; padding:6px 10px; margin:6px 0;'>"
                f"<div style='color:{color}; font-weight:bold; font-size:11px; margin-bottom:3px;'>🎲 {badge}</div>"
                f"<div style='color:#E2E8F0; font-size:11px; line-height:1.3;'>{raw_body}</div></div>"
            )
        escaped_html = re.sub(r'&lt;check&gt;\s*(.*?)\s*&lt;/check&gt;', _check_sub, escaped_html, flags=re.DOTALL | re.IGNORECASE)

        # 2. Terminal
        def _term_sub(m):
            raw_body = m.group(1).strip().replace('\n', '<br>')
            return (
                f"<div style='background:#050D0A; border:1px solid #10B981; border-radius:6px; "
                f"padding:8px 12px; margin:6px 0; font-family:Consolas, monospace;'>"
                f"<div style='color:#34D399; font-weight:bold; font-size:10px; margin-bottom:4px;'>📟 ТЕРМИНАЛ СВЯЗИ</div>"
                f"<div style='color:#A7F3D0; font-size:11px; line-height:1.3;'>{raw_body}</div></div>"
            )
        escaped_html = re.sub(r'&lt;terminal&gt;\s*(.*?)\s*&lt;/terminal&gt;', _term_sub, escaped_html, flags=re.DOTALL | re.IGNORECASE)

        # 3. SMS / Phone
        def _sms_sub(m):
            raw_body = m.group(2).strip().replace('\n', '<br>')
            return (
                f"<div style='background:rgba(10, 16, 28, 0.95); border:1px solid rgba(0, 240, 255, 0.4); "
                f"border-left:3px solid #00F0FF; border-radius:6px; padding:8px 12px; margin:6px 0;'>"
                f"<div style='color:#00F0FF; font-weight:bold; font-size:10px; margin-bottom:3px;'>📱 ВХОДЯЩЕЕ СООБЩЕНИЕ</div>"
                f"<div style='color:#F1F5F9; font-size:11px; line-height:1.3;'>{raw_body}</div></div>"
            )
        escaped_html = re.sub(r'&lt;(sms|phone)&gt;\s*(.*?)\s*&lt;/\1&gt;', _sms_sub, escaped_html, flags=re.DOTALL | re.IGNORECASE)

        return escaped_html

    @staticmethod
    def _parse_key_values(raw_text: str) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        for line in raw_text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            key, val = k.strip(), v.strip().rstrip(',')
            if val.startswith('[') and val.endswith(']'):
                inner = val[1:-1].strip()
                items = []
                if inner:
                    for raw_item in re.split(r',\s*(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)', inner):
                        c = raw_item.strip().strip('\'"').strip()
                        if c:
                            items.append(c)
                data[key] = items
            else:
                data[key] = val.strip('\'"')
        return data

    @staticmethod
    def _parse_meters(raw_text: str) -> Dict[str, Dict[str, Any]]:
        meters: Dict[str, Dict[str, Any]] = {}
        for line in raw_text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            key = k.strip()
            val = v.strip()
            pct_m = re.search(r'(\d+)\s*%', val)
            pct = int(pct_m.group(1)) if pct_m else None
            if pct is None:
                slash_m = re.search(r'(\d+)\s*/\s*(\d+)', val)
                if slash_m:
                    num, den = float(slash_m.group(1)), float(slash_m.group(2))
                    pct = int((num / den) * 100) if den > 0 else 0
            if pct is None:
                filled = val.count('█') + val.count('#')
                empty = val.count('░') + val.count('-')
                if (filled + empty) > 0:
                    pct = int((filled / (filled + empty)) * 100)
            note_m = re.search(r'\((.*?)\)', val)
            note = note_m.group(1).strip() if note_m else ""
            meters[key] = {"pct": min(100, max(0, pct if pct is not None else 50)), "note": note, "raw": val}
        return meters


class RPGThoughtWidget(QFrame):
    def __init__(self, thought_text: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.thought_text = thought_text
        self.is_expanded = True
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("RPGThoughtFrame")
        self.setStyleSheet("""
            QFrame#RPGThoughtFrame {
                background-color: rgba(22, 17, 38, 0.88);
                border: 1px solid rgba(167, 139, 250, 0.35);
                border-left: 3px solid #A78BFA;
                border-radius: 8px;
                margin-top: 2px;
                margin-bottom: 4px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self.toggle_btn = QPushButton(self)
        self.toggle_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._update_header()
        self.toggle_btn.clicked.connect(self._toggle_collapse)
        layout.addWidget(self.toggle_btn)

        self.text_browser = QTextBrowser(self)
        self.text_browser.setReadOnly(True)
        self.text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_browser.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
                color: #DDD6FE;
                font-family: 'Inter', 'Segoe UI', sans-serif;
                font-style: italic;
                font-size: 11px;
                line-height: 1.35;
                padding: 0px;
            }
        """)
        esc_t = html.escape(self.thought_text).replace('\n', '<br>')
        self.text_browser.setHtml(f"<div>{esc_t}</div>")
        doc = self.text_browser.document()
        doc.setTextWidth(600.0)
        h = max(26, min(int(doc.size().height()) + 8, 220))
        self.text_browser.setFixedHeight(h)
        layout.addWidget(self.text_browser)

    def _update_header(self):
        arr = "▼" if self.is_expanded else "▶"
        self.toggle_btn.setText(f"{arr}  💭 Внутренние мысли")
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #A78BFA;
                text-align: left;
                font-size: 11px;
                font-weight: bold;
                border: none;
                padding: 2px 4px;
            }
            QPushButton:hover { color: #C4B5FD; }
        """)

    def _toggle_collapse(self):
        self.is_expanded = not self.is_expanded
        self.text_browser.setVisible(self.is_expanded)
        self._update_header()


class RPGChoicesWidget(QFrame):
    choice_selected = pyqtSignal(str)

    def __init__(self, choices: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.choices = choices or []
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("RPGChoicesFrame")
        self.setStyleSheet("""
            QFrame#RPGChoicesFrame {
                background-color: rgba(10, 16, 28, 0.92);
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-left: 3px solid #38BDF8;
                border-radius: 8px;
                margin-top: 6px;
                margin-bottom: 4px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        hdr = QLabel("🧭 ВАРИАНТЫ ДЕЙСТВИЙ (нажмите для выбора):", self)
        hdr.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: bold; background: transparent;")
        layout.addWidget(hdr)

        for raw_choice in self.choices:
            btn = QPushButton(raw_choice, self)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(18, 28, 46, 0.8);
                    color: #F1F5F9;
                    border: 1px solid rgba(56, 189, 248, 0.35);
                    border-radius: 6px;
                    padding: 6px 12px;
                    text-align: left;
                    font-size: 11px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background-color: rgba(0, 240, 255, 0.20);
                    border-color: #00F0FF;
                    color: #00F0FF;
                }
                QPushButton:pressed {
                    background-color: rgba(0, 240, 255, 0.35);
                }
            """)
            btn.clicked.connect(lambda _, ch=raw_choice: self._on_btn_clicked(ch))
            layout.addWidget(btn)

    def _on_btn_clicked(self, choice_text: str):
        for btn in self.findChildren(QPushButton):
            btn.setEnabled(False)
        clean_choice = re.sub(r'^\s*[\d]+[.)\-]\s*', '', choice_text).strip()
        self.choice_selected.emit(clean_choice)


class RPGStatusWidget(QFrame):
    KEY_ICONS = {
        "class": "👤", "класс": "👤", "level": "⭐", "уровень": "⭐",
        "gold": "💰", "золото": "💰", "exp": "📈", "опыт": "📈",
        "bp": "⚡", "location": "📍", "локация": "📍",
        "threat level": "⚠️", "угроза": "⚠️", "reputation": "👑",
        "inventory": "🎒", "инвентарь": "🎒", "equipment": "🛡️",
        "снаряжение": "🛡️", "abilities": "🐾", "способности": "🐾",
        "skill": "🎯", "skills": "🎯", "spells": "🔮", "relationships": "🤝", "objectives": "📜"
    }

    def __init__(self, stats_data: Any, meters_data: Optional[Dict[str, Any]] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        if isinstance(stats_data, str):
            self.stats_data = RPGContentParser._parse_key_values(stats_data)
            if not self.stats_data:
                self.stats_data = {"Status": stats_data}
        else:
            self.stats_data = stats_data or {}
        self.meters_data = meters_data or {}
        self.is_expanded = True
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("RPGStatusHUD")
        self.setStyleSheet("""
            QFrame#RPGStatusHUD {
                background-color: rgba(10, 16, 28, 0.94);
                border: 1px solid rgba(0, 240, 255, 0.28);
                border-left: 3px solid #00F0FF;
                border-radius: 8px;
                margin-top: 6px;
                margin-bottom: 4px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self.toggle_btn = QPushButton(self)
        self.toggle_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._update_header()
        self.toggle_btn.clicked.connect(self._toggle_collapse)
        layout.addWidget(self.toggle_btn)

        self.content_browser = QTextBrowser(self)
        self.content_browser.setReadOnly(True)
        self.content_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_browser.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
                color: #CBD5E1;
                font-family: 'Inter', 'Segoe UI', sans-serif;
                font-size: 11px;
                padding: 0px;
            }
        """)
        self._render_content()
        layout.addWidget(self.content_browser)

    def _update_header(self):
        arr = "▼" if self.is_expanded else "▶"
        lvl = self.stats_data.get("Level") or self.stats_data.get("level") or ""
        cls_ = self.stats_data.get("Class") or self.stats_data.get("class") or ""
        badge = f" • {cls_} (Lvl {lvl})" if (cls_ and lvl) else ""
        self.toggle_btn.setText(f"{arr}  ⚔️ СТАТУС & ИНВЕНТАРЬ{badge}")
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #00F0FF;
                text-align: left;
                font-size: 11px;
                font-weight: bold;
                border: none;
                padding: 2px 4px;
            }
            QPushButton:hover { color: #38BDF8; }
        """)

    def _toggle_collapse(self):
        self.is_expanded = not self.is_expanded
        self.content_browser.setVisible(self.is_expanded)
        self._update_header()

    def update_data(self, stats: Any, meters: Optional[Dict[str, Any]] = None):
        if isinstance(stats, str):
            self.stats_data = RPGContentParser._parse_key_values(stats)
            if not self.stats_data:
                self.stats_data = {"Status": stats}
        else:
            self.stats_data = stats or {}
        if meters:
            self.meters_data = meters
        self._update_header()
        self._render_content()

    def update_status(self, stats: Any, meters: Optional[Dict[str, Any]] = None):
        """Backward compatibility alias for update_data."""
        self.update_data(stats, meters)

    def _render_content(self):
        blocks = []
        scalars, lists = [], []

        for k, v in self.stats_data.items():
            if isinstance(v, list):
                lists.append((k, v))
            else:
                scalars.append((k, str(v)))

        # 1. Grid of Scalar Stats
        if scalars:
            cells = []
            for k, val in scalars:
                icon = self.KEY_ICONS.get(k.lower(), "🔹")
                cells.append(
                    f"<div style='display:inline-block; margin-right:12px; margin-bottom:4px;'>"
                    f"<span style='color:#64748B;'>{icon} {html.escape(k)}:</span> "
                    f"<strong style='color:#38BDF8;'>{html.escape(val)}</strong></div>"
                )
            blocks.append(f"<div style='margin-bottom:6px;'>{''.join(cells)}</div>")

        # 2. Graphical Progress Bars for Meters / Affection
        if self.meters_data:
            meter_rows = []
            for m_name, m_info in self.meters_data.items():
                pct = m_info.get("pct", 50)
                note = html.escape(m_info.get("note", ""))
                note_str = f" <span style='color:#34D399; font-size:10px;'>({note})</span>" if note else ""
                bar_color = "#EC4899" if any(w in m_name.lower() for w in ("любовь", "привяз", "влеч", "love")) else "#00F0FF"
                meter_rows.append(
                    f"<table width='100%' cellpadding='0' cellspacing='0' style='margin-bottom:5px;'>"
                    f"<tr><td style='color:#E2E8F0; font-size:11px; font-weight:600;'>📊 {html.escape(m_name)}</td>"
                    f"<td align='right' style='color:{bar_color}; font-size:11px; font-weight:bold;'>{pct}%{note_str}</td></tr>"
                    f"<tr><td colspan='2' style='padding-top:2px;'>"
                    f"<table width='100%' cellpadding='0' cellspacing='0' style='background-color:#1E293B; height:6px; border-radius:3px;'>"
                    f"<tr><td width='{pct}%' style='background-color:{bar_color}; height:6px; border-radius:3px;'></td>"
                    f"<td width='{100 - pct}%'></td></tr></table></td></tr></table>"
                )
            blocks.append(f"<div style='margin-top:4px; margin-bottom:6px;'>{''.join(meter_rows)}</div>")

        # 3. Lists & Chips
        if lists:
            list_rows = []
            for k, items in lists:
                icon = self.KEY_ICONS.get(k.lower(), "📦")
                if not items:
                    chips = "<span style='color:#475569; font-style:italic;'>пусто</span>"
                else:
                    chip_tags = [
                        f"<span style='background:rgba(0, 240, 255, 0.12); color:#00F0FF; "
                        f"border:1px solid rgba(0, 240, 255, 0.35); border-radius:4px; "
                        f"padding:1px 6px; margin-right:4px; font-weight:500; font-size:11px;'>{html.escape(str(itm))}</span>"
                        for itm in items
                    ]
                    chips = " ".join(chip_tags)
                list_rows.append(
                    f"<div style='margin-top:3px; margin-bottom:4px;'>"
                    f"<span style='color:#94A3B8; font-weight:600;'>{icon} {html.escape(k)}:</span> {chips}</div>"
                )
            blocks.append("".join(list_rows))

        full_html = f"<div style='font-family:\"Inter\",\"Segoe UI\",sans-serif; line-height:1.35;'>{''.join(blocks)}</div>"
        self.content_browser.setHtml(full_html)
        doc = self.content_browser.document()
        doc.setTextWidth(600.0)
        h = int(doc.size().height()) + 8
        self.content_browser.setFixedHeight(max(30, min(h, 280)))


def extract_rpg_status(text: str) -> Tuple[str, Optional[Any]]:
    bundle = RPGContentParser.parse(text)
    return bundle.clean_text, bundle.stats


# Backward compatibility alias
RPGStatusParser = RPGContentParser
RPGStatusParser.extract = staticmethod(extract_rpg_status)
