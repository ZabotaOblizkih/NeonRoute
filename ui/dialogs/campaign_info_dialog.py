"""
Campaign Info & State Dialog for NeonRoute.
Directly bound to native RPCastManager (history/cast_<session_id>.json).
5 Tabs:
1. 🧠 Память, сюжет и статус (Quest, Hero & Companion condition / mutations, Intimacy Milestones, World State)
2. 🎒 Инвентарь и снаряжение (Items directly bound to cast_data["inventory"])
3. 📖 Сценарий и завязка (Scenario and card prompt)
4. 👥 Второстепенные персонажи (NPCs, vital_status, presence, visual_dna, facts)
5. 📍 Локация и атмосфера (current_location, last_visual_prompt)

Modernized with QFluentWidgets, SegmentedWidget and Glassmorphism styling under Agentrules.md.
"""

import os
import re
import json
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QStackedWidget, QTextBrowser, QWidget, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox, QFrame,
    QGraphicsDropShadowEffect
)

from qfluentwidgets import (
    SegmentedWidget,
    PushButton,
    PrimaryPushButton,
    TransparentToolButton,
    LineEdit,
    TextEdit,
    CheckBox,
    ComboBox,
    ToolTipFilter,
    ToolTipPosition,
    FluentIcon as FIF,
)

from config import CHARACTERS_DIR
from services.rp_cast_manager import RPCastManager
from services.session_manager import SessionManager

try:
    from config import LOREBOOKS_DIR
except ImportError:
    LOREBOOKS_DIR = CHARACTERS_DIR.parent / "lorebooks"
LOREBOOKS_DIR.mkdir(parents=True, exist_ok=True)

# Facade Re-export (Zero-Breaking-Changes)
from ui.dialogs.campaign_subdialogs import (
    EntityConditionDialog,
    CastEditDialog,
    SelectLorebookDialog,
)

__all__ = [
    "CampaignInfoDialog",
    "EntityConditionDialog",
    "CastEditDialog",
    "SelectLorebookDialog",
]


class CampaignInfoDialog(QDialog):
    """Дашборд мастера: 5 вкладок (Память и статус, Инвентарь, Лор, Каст, Локация)."""
    def __init__(self, character_card: Any, current_session: Optional[Dict[str, Any]] = None, parent=None):
        super().__init__(parent)
        self.card = character_card
        self.session = current_session or {}
        self.session_id = self.session.get("id", "")
        card_name = getattr(self.card, "name", "Модуль") or "Модуль"

        self.setWindowTitle(f"Сводка кампании — {card_name}")
        self.resize(840, 630)
        self.setMinimumSize(740, 520)

        # Drop shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.setGraphicsEffect(shadow)

        self.setStyleSheet("""
            QDialog {
                background-color: rgba(14, 19, 31, 0.96);
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-radius: 14px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }
            QListWidget {
                background-color: rgba(10, 15, 26, 0.75);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 6px;
            }
            QListWidget::item {
                padding: 6px 10px;
                border-radius: 6px;
                margin-bottom: 2px;
                border: 1px solid transparent;
            }
            QListWidget::item:hover {
                background-color: rgba(0, 240, 255, 0.08);
                color: #38BDF8;
            }
            QListWidget::item:selected {
                background-color: rgba(0, 240, 255, 0.18);
                color: #00F0FF;
                font-weight: bold;
                border: 1px solid rgba(0, 240, 255, 0.35);
            }
            QTextBrowser {
                background-color: rgba(10, 15, 26, 0.85);
                color: #E2E8F0;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 10px;
                font-family: 'Consolas', 'Segoe UI', monospace;
                font-size: 12px;
                line-height: 1.4;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        # Header bar
        header = QHBoxLayout()
        header.setSpacing(10)
        is_env = getattr(self.card, "is_environment", False) if self.card else False
        self.icon_lbl = QLabel("🌐" if is_env else "📜", self)
        self.icon_lbl.setStyleSheet("font-size: 20px;")
        header.addWidget(self.icon_lbl)

        title_prefix = "Сводка окружения и мира" if is_env else "Сводка кампании"
        self.title_lbl = QLabel(f"{title_prefix}: {card_name}", self)
        self.title_lbl.setStyleSheet("color: #00F0FF; font-size: 15px; font-weight: 800; letter-spacing: 0.5px;")
        header.addWidget(self.title_lbl, 1)

        self.btn_choose_lore = PushButton("Выбрать лор", self, icon=FIF.BOOK_SHELF)
        self.btn_choose_lore.setToolTip("Подключить модуль правил или лорбук к этой сессии")
        self.btn_choose_lore.installEventFilter(ToolTipFilter(self.btn_choose_lore, showDelay=300, position=ToolTipPosition.BOTTOM))
        self.btn_choose_lore.clicked.connect(self._open_select_lore_dialog)
        header.addWidget(self.btn_choose_lore)

        self.btn_refresh = PushButton("Обновить", self, icon=FIF.SYNC)
        self.btn_refresh.setToolTip("Перечитать данные с диска")
        self.btn_refresh.installEventFilter(ToolTipFilter(self.btn_refresh, showDelay=300, position=ToolTipPosition.BOTTOM))
        self.btn_refresh.clicked.connect(self.load_data)
        header.addWidget(self.btn_refresh)

        layout.addLayout(header)

        # Segmented Control & Stacked Widget
        self.segmented_bar = SegmentedWidget(self)
        self.stacked_widget = QStackedWidget(self)

        tab_state = self._create_state_tab()
        tab_inv = self._create_inventory_tab()
        tab_lore = self._create_lore_tab()
        tab_cast = self._create_cast_tab()
        tab_loc = self._create_location_tab()

        self._tabs_map = [
            ("tab_state", "🧠 Память и статус", tab_state),
            ("tab_inv", "🎒 Инвентарь", tab_inv),
            ("tab_lore", "📖 Сценарий", tab_lore),
            ("tab_cast", "👥 Каст и NPC", tab_cast),
            ("tab_loc", "📍 Локация", tab_loc),
        ]

        for route_key, label_text, widget in self._tabs_map:
            self.stacked_widget.addWidget(widget)
            self.segmented_bar.addItem(
                routeKey=route_key,
                text=label_text,
                onClick=lambda checked=False, w=widget: self.stacked_widget.setCurrentWidget(w)
            )

        self.segmented_bar.setCurrentItem("tab_state")
        self.tabs = self.stacked_widget  # Backwards compatibility

        layout.addWidget(self.segmented_bar)
        layout.addWidget(self.stacked_widget, 1)

        # Bottom Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()
        btn_close = PushButton("Закрыть", self)
        btn_close.clicked.connect(self.accept)
        bottom_bar.addWidget(btn_close)
        layout.addLayout(bottom_bar)

    def showEvent(self, event):
        super().showEvent(event)
        self.load_data()

    def _get_actor_names(self):
        sess_persona_id = self.session.get("user_persona_id") if isinstance(self.session, dict) else None
        persona = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=sess_persona_id)
        u_name = persona.get("name") or "Игрок"
        c_name = getattr(self.card, "name", "Спутница") or "Спутница"
        return u_name, c_name

    def _open_select_lore_dialog(self):
        cur_lore = self.session.get("active_lorebook")
        dlg = SelectLorebookDialog(current_lorebook=cur_lore, parent=self)
        if dlg.exec():
            chosen = dlg.selected_lorebook
            self.session["active_lorebook"] = chosen
            try:
                SessionManager.save_session(self.session)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка сохранения", f"Не удалось сохранить сессию: {e}")
            self.load_data()

    def load_data(self):
        """Reloads all 5 tabs directly from cast_<session_id>.json and updates active lorebook."""
        u_name, c_name = self._get_actor_names()
        cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)

        # Update active lorebook button style & text
        active_lore = self.session.get("active_lorebook")
        if active_lore:
            lore_stem = Path(active_lore).stem
            self.btn_choose_lore.setText(f"📚 {lore_stem}")
            self.btn_choose_lore.setStyleSheet("PushButton { background-color: rgba(14, 53, 71, 0.8); border: 1px solid #00F0FF; color: #00F0FF; font-weight: bold; }")
        else:
            self.btn_choose_lore.setText("Выбрать лор")
            self.btn_choose_lore.setStyleSheet("")

        # Tab 1: State & Milestones
        is_env = getattr(self.card, "is_environment", False) if self.card else False
        if hasattr(self, "row_c_widget"):
            self.row_c_widget.setVisible(not is_env)
        if hasattr(self, "title_lbl"):
            card_name = getattr(self.card, "name", "Без имени") or "Без имени"
            title_prefix = "Сводка окружения и мира" if is_env else "Сводка кампании"
            self.title_lbl.setText(f"{title_prefix}: {card_name}")
        if hasattr(self, "icon_lbl"):
            self.icon_lbl.setText("🌐" if is_env else "📜")

        # Focus companion of the party
        party = RPCastManager.get_party_companions(cast_data)
        focus_comp_name = cast_data.get("focus_companion") or c_name or "Спутница"
        self._current_focus_name = focus_comp_name
        if hasattr(self, "lbl_c_title"):
            self.lbl_c_title.setText(f"🩺 СОСТОЯНИЕ ({focus_comp_name}):")

        if hasattr(self, "combo_focus_comp"):
            if len(party) > 1 and not is_env:
                self.combo_focus_comp.blockSignals(True)
                self.combo_focus_comp.clear()
                cur_focus = cast_data.get("focus_companion", "")
                selected_idx = 0
                for idx, (eid, ent) in enumerate(party.items()):
                    p_name = ent.get("canonical_name", eid)
                    self.combo_focus_comp.addItem(f"🛡️ {p_name}", userData=p_name)
                    if p_name.lower().strip() == cur_focus.lower().strip():
                        selected_idx = idx
                self.combo_focus_comp.setCurrentIndex(selected_idx)
                self.combo_focus_comp.blockSignals(False)
                self.combo_focus_comp.setVisible(True)
                if hasattr(self, "row_focus_widget"):
                    self.row_focus_widget.setVisible(True)
            else:
                self.combo_focus_comp.setVisible(False)
                if hasattr(self, "row_focus_widget"):
                    self.row_focus_widget.setVisible(False)

        q_title = cast_data.get("active_quest", "")
        self.lbl_q_status.setText(f" [АКТИВНО: {q_title}]" if q_title else " [СВОБОДНО]")
        self.lbl_p_status.setText(self._format_entity_summary(cast_data.get("player_state", {})))
        self.lbl_c_status.setText(self._format_entity_summary(cast_data.get("companion_state", {})))

        self.list_milestones.clear()
        rel_ctx = cast_data.get("relationship_context") or {}
        found = rel_ctx.get("foundation")
        bond = rel_ctx.get("current_bond")
        tone = rel_ctx.get("dynamic_tone") or cast_data.get("relationship_state", "")

        if found:
            self.list_milestones.addItem(QListWidgetItem(f"🏛️ Фундамент: {found}"))
        if bond and bond != found:
            self.list_milestones.addItem(QListWidgetItem(f"🔗 Статус союза: {bond}"))
        elif not found and bond:
            self.list_milestones.addItem(QListWidgetItem(f"🔗 Статус союза: {bond}"))
        if tone:
            self.list_milestones.addItem(QListWidgetItem(f"🎭 Тон момента: {tone}"))

        legacy_ms = [m for m in cast_data.get("intimacy_milestones", []) if m not in (found, bond)]
        for item in legacy_ms:
            self.list_milestones.addItem(QListWidgetItem(f"• {item}"))

        self.list_world_state.clear()
        for item in cast_data.get("world_state", []):
            self.list_world_state.addItem(QListWidgetItem(f"• {item}"))

        self.list_stats.clear()
        for s_name, s_val in cast_data.get("stats", {}).items():
            self.list_stats.addItem(QListWidgetItem(f"📈 {s_name}: {s_val}"))

        # Tab 2: Inventory
        self.inventory_items = list(cast_data.get("inventory", []))
        self._refresh_inventory_ui()

        # Tab 3: Scenario, First Message & Lorebook
        desc = getattr(self.card, "description_ru", "") or getattr(self.card, "description", "")
        scen = getattr(self.card, "scenario_ru", "") or getattr(self.card, "scenario", "")
        first_mes = getattr(self.card, "first_mes_ru", "") or getattr(self.card, "first_mes", "")

        sections = []
        if desc.strip():
            sections.append(f"### ОПИСАНИЕ ПЕРСОНАЖА / МИРА:\n{desc.strip()}")
        if scen.strip():
            sections.append(f"### СЦЕНАРИЙ:\n{scen.strip()}")
        if first_mes.strip():
            sections.append(f"### ЗАВЯЗКА (ПЕРВОЕ СООБЩЕНИЕ):\n{first_mes.strip()}")

        # Embedded Lore Entries
        card_lore = getattr(self.card, "lore_entries", None) or []
        if card_lore:
            lines = [f"\n### ВСТРОЕННЫЙ ЛОР КАРТОЧКИ ({len(card_lore)} статей):"]
            for idx, entry in enumerate(card_lore, 1):
                keys = getattr(entry, "keys", [])
                content_txt = getattr(entry, "content", "")
                comment = getattr(entry, "comment", "") or f"Статья {idx}"
                keys_str = f" [Ключи: {', '.join(keys)}]" if keys else " [Константа]"
                lines.append(f"\n#### 📖 {comment}{keys_str}\n{content_txt.strip()}")
            sections.append("\n".join(lines))

        # External Lorebook
        if active_lore:
            lore_path = LOREBOOKS_DIR / active_lore
            if lore_path.exists():
                try:
                    with open(lore_path, "r", encoding="utf-8") as lf:
                        l_data = json.load(lf)
                    l_title = l_data.get("name") or lore_path.stem
                    raw_entries = l_data.get("entries", {})
                    items = list(raw_entries.values()) if isinstance(raw_entries, dict) else (raw_entries if isinstance(raw_entries, list) else [])
                    lines = [f"\n### ВНЕШНИЙ МОДУЛЬ ПРАВИЛ / ЛОРБУК: {l_title} ({len(items)} статей)\nФайл: {active_lore}"]
                    for idx, entry in enumerate(items, 1):
                        keys = entry.get("keys") or entry.get("key") or []
                        keys_str = f" [Ключи: {', '.join(keys)}]" if isinstance(keys, list) and keys else (f" [Ключи: {keys}]" if keys else " [Константа]")
                        comment = entry.get("comment") or entry.get("name") or f"Статья {idx}"
                        content_txt = entry.get("content") or ""
                        lines.append(f"\n#### 📚 {comment}{keys_str}\n{content_txt.strip()}")
                    sections.append("\n".join(lines))
                except Exception as e:
                    sections.append(f"\n### ВНЕШНИЙ ЛОРБУК: {active_lore}\n(Ошибка чтения файла: {e})")

        content = "\n\n".join(sections)
        if hasattr(self, "tb_lore"):
            self.tb_lore.setPlainText(content.strip())

        # Tab 4: Cast
        self.cast_entities = dict(cast_data.get("entities", {}))
        self._refresh_cast_ui()
        self.tb_npc_details.clear()

        # Tab 5: Location & Atmosphere
        env = cast_data.get("environment") or {}
        self.loc_name_input.setText(cast_data.get("current_location", ""))
        self.cb_is_enclosed.setChecked(bool(env.get("is_enclosed", False)))
        self.loc_ambience_input.setText(env.get("scene_ambience", ""))
        self.world_baseline_input.setText(env.get("world_baseline", "Земной мир, естественное освещение"))
        self.loc_visual_input.setPlainText(cast_data.get("last_visual_prompt", "") or "")

    # --- 1. ПАМЯТЬ, СЮЖЕТ И СТАТУС ---
    def _create_state_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 8)
        v.setSpacing(8)

        comp_name = getattr(self.card, "name", "Спутница") or "Спутница"

        # Soft Card: Active Quest
        card_q = QFrame(w)
        card_q.setObjectName("QuestCard")
        card_q.setStyleSheet("""
            QFrame#QuestCard {
                background-color: rgba(20, 28, 45, 0.65);
                border: 1px solid rgba(0, 240, 255, 0.20);
                border-radius: 10px;
            }
        """)
        row_q = QHBoxLayout(card_q)
        row_q.setContentsMargins(12, 6, 12, 6)
        row_q.setSpacing(10)
        row_q.addWidget(QLabel("🎯 ТЕКУЩАЯ ЦЕЛЬ:", card_q))
        self.lbl_q_status = QLabel(" [СВОБОДНО]", card_q)
        self.lbl_q_status.setStyleSheet("color: #38BDF8; font-weight: 700;")
        row_q.addWidget(self.lbl_q_status, 1)

        btn_q_done = PushButton("Завершить", card_q, icon=FIF.ACCEPT)
        btn_q_done.clicked.connect(self._complete_quest)
        row_q.addWidget(btn_q_done)

        btn_q_edit = PushButton("Изменить", card_q, icon=FIF.EDIT)
        btn_q_edit.clicked.connect(self._edit_quest)
        row_q.addWidget(btn_q_edit)
        v.addWidget(card_q)

        # Soft Card: Hero State
        card_p = QFrame(w)
        card_p.setObjectName("PlayerCard")
        card_p.setStyleSheet("""
            QFrame#PlayerCard {
                background-color: rgba(20, 28, 45, 0.65);
                border: 1px solid rgba(0, 240, 255, 0.20);
                border-radius: 10px;
            }
        """)
        row_p = QHBoxLayout(card_p)
        row_p.setContentsMargins(12, 6, 12, 6)
        row_p.setSpacing(10)
        row_p.addWidget(QLabel("🛡️ СОСТОЯНИЕ ГЕРОЯ:", card_p))
        self.lbl_p_status = QLabel("", card_p)
        self.lbl_p_status.setStyleSheet("color: #E2E8F0; font-size: 11px;")
        row_p.addWidget(self.lbl_p_status, 1)

        btn_p_edit = PushButton("Изменить", card_p, icon=FIF.EDIT)
        btn_p_edit.clicked.connect(lambda: self._edit_entity("player", "Герой"))
        row_p.addWidget(btn_p_edit)
        v.addWidget(card_p)

        # Party Focus Companion Switcher
        self.row_focus_widget = QFrame(w)
        self.row_focus_widget.setObjectName("FocusRow")
        self.row_focus_widget.setStyleSheet("""
            QFrame#FocusRow {
                background-color: rgba(16, 23, 38, 0.50);
                border: 1px solid rgba(56, 189, 248, 0.20);
                border-radius: 8px;
            }
        """)
        row_focus = QHBoxLayout(self.row_focus_widget)
        row_focus.setContentsMargins(12, 6, 12, 6)
        row_focus.setSpacing(10)

        self.lbl_focus_comp = QLabel("👥 АКТИВНЫЙ СПУТНИК В ФОКУСЕ:", self.row_focus_widget)
        self.lbl_focus_comp.setStyleSheet("color: #38BDF8; font-weight: 700;")
        row_focus.addWidget(self.lbl_focus_comp)

        self.combo_focus_comp = ComboBox(self.row_focus_widget)
        self.combo_focus_comp.currentIndexChanged.connect(self._on_focus_companion_changed)
        row_focus.addWidget(self.combo_focus_comp, 1)

        self.row_focus_widget.setVisible(False)
        self.combo_focus_comp.setVisible(False)
        v.addWidget(self.row_focus_widget)

        # Soft Card: Companion State
        is_env = getattr(self.card, "is_environment", False) if self.card else False
        self.row_c_widget = QFrame(w)
        self.row_c_widget.setObjectName("CompanionCard")
        self.row_c_widget.setStyleSheet("""
            QFrame#CompanionCard {
                background-color: rgba(20, 28, 45, 0.65);
                border: 1px solid rgba(0, 240, 255, 0.20);
                border-radius: 10px;
            }
        """)
        row_c = QHBoxLayout(self.row_c_widget)
        row_c.setContentsMargins(12, 6, 12, 6)
        row_c.setSpacing(10)

        self.lbl_c_title = QLabel(f"🩺 СОСТОЯНИЕ ({comp_name}):", self.row_c_widget)
        row_c.addWidget(self.lbl_c_title)
        self.lbl_c_status = QLabel("", self.row_c_widget)
        self.lbl_c_status.setStyleSheet("color: #E2E8F0; font-size: 11px;")
        row_c.addWidget(self.lbl_c_status, 1)

        btn_c_edit = PushButton("Изменить", self.row_c_widget, icon=FIF.EDIT)
        btn_c_edit.clicked.connect(lambda: self._edit_entity("companion", getattr(self, "_current_focus_name", comp_name)))
        row_c.addWidget(btn_c_edit)
        v.addWidget(self.row_c_widget)

        if is_env:
            self.row_c_widget.setVisible(False)

        # Milestones
        v.addWidget(QLabel("💖 ВЕХИ БЛИЗОСТИ И ОТНОШЕНИЙ (<IntimacyMilestones>):", w))
        self.list_milestones = QListWidget(w)
        self.list_milestones.setFixedHeight(70)
        v.addWidget(self.list_milestones)

        row_ms_btn = QHBoxLayout()
        btn_add_ms = PushButton("Добавить веху", w, icon=FIF.ADD)
        btn_add_ms.clicked.connect(self._add_intimacy_milestone)
        row_ms_btn.addWidget(btn_add_ms)
        btn_del_ms = PushButton("Удалить веху", w, icon=FIF.DELETE)
        btn_del_ms.clicked.connect(self._delete_intimacy_milestone)
        row_ms_btn.addWidget(btn_del_ms)
        row_ms_btn.addStretch()
        v.addLayout(row_ms_btn)

        # World State
        v.addWidget(QLabel("🌍 ПРАВИЛА И ОПАСНОСТИ МИРА (<WorldState>):", w))
        self.list_world_state = QListWidget(w)
        self.list_world_state.setFixedHeight(70)
        v.addWidget(self.list_world_state)

        row_ws_btn = QHBoxLayout()
        btn_add_ws = PushButton("Добавить правило", w, icon=FIF.ADD)
        btn_add_ws.clicked.connect(self._add_world_state)
        row_ws_btn.addWidget(btn_add_ws)
        btn_del_ws = PushButton("Удалить правило", w, icon=FIF.DELETE)
        btn_del_ws.clicked.connect(self._delete_world_state)
        row_ws_btn.addWidget(btn_del_ws)
        row_ws_btn.addStretch()
        v.addLayout(row_ws_btn)

        # Stats
        v.addWidget(QLabel("📊 ШКАЛЫ СОСТОЯНИЯ И ПАРАМЕТРОВ (<Stats>):", w))
        self.list_stats = QListWidget(w)
        self.list_stats.setFixedHeight(65)
        v.addWidget(self.list_stats)

        row_stat_btn = QHBoxLayout()
        btn_add_stat = PushButton("Изменить / Добавить шкалу", w, icon=FIF.ADD)
        btn_add_stat.clicked.connect(self._edit_stat)
        row_stat_btn.addWidget(btn_add_stat)
        btn_del_stat = PushButton("Удалить шкалу", w, icon=FIF.DELETE)
        btn_del_stat.clicked.connect(self._delete_stat)
        row_stat_btn.addWidget(btn_del_stat)
        row_stat_btn.addStretch()
        v.addLayout(row_stat_btn)

        return w

    def _on_focus_companion_changed(self, index: int):
        if index < 0 or not hasattr(self, "combo_focus_comp"):
            return
        selected_name = self.combo_focus_comp.itemData(index) or self.combo_focus_comp.currentText().replace("🛡️ ", "").strip()
        if not selected_name:
            return
        u_name, c_name = self._get_actor_names()
        cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
        if RPCastManager.switch_focus(cast_data, selected_name):
            RPCastManager.save_cast(self.session_id, cast_data)
            self.load_data()

    def _format_entity_summary(self, state: Dict[str, Any]) -> str:
        vital = state.get("vital_status", "alive")
        if vital == "dead":
            v_str = "🔴 ПОГИБ(ЛА) (МЁРТВ)"
        elif vital == "critical":
            v_str = "🟡 При смерти / Тяжело ранен(а)"
        else:
            v_str = "🟢 Жив(а)"

        muts = state.get("mutations", [])
        muts_str = f" | Мутации/раны: {', '.join(muts)}" if muts else " | Без ранений"
        outfit = state.get("current_outfit", "")
        outfit_str = f" | Наряд: {outfit}" if outfit else ""
        return f"{v_str}{muts_str}{outfit_str}"

    def _edit_entity(self, key: str, display_name: str):
        u_name, c_name = self._get_actor_names()
        cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
        state_key = "player_state" if key == "player" else "companion_state"
        cur_state = cast_data.setdefault(state_key, {})
        dlg = EntityConditionDialog(display_name, cur_state, parent=self)
        if dlg.exec():
            cur_state.update(dlg.get_data())
            if key != "player":
                focus_c = cast_data.get("focus_companion", "")
                for ent in cast_data.get("entities", {}).values():
                    if isinstance(ent, dict) and ent.get("role") == "companion" and ent.get("canonical_name", "").lower().strip() == focus_c.lower().strip():
                        ent["companion_state"] = dict(cur_state)
                        break
            RPCastManager.save_cast(self.session_id, cast_data)
            if key == "player":
                self.lbl_p_status.setText(self._format_entity_summary(cur_state))
            else:
                self.lbl_c_status.setText(self._format_entity_summary(cur_state))

    def _complete_quest(self):
        u_name, c_name = self._get_actor_names()
        cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
        cast_data["active_quest"] = ""
        RPCastManager.save_cast(self.session_id, cast_data)
        self.lbl_q_status.setText(" [СВОБОДНО]")

    def _edit_quest(self):
        u_name, c_name = self._get_actor_names()
        cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
        cur = cast_data.get("active_quest", "")
        text, ok = QInputDialog.getText(self, "Цель сцены", "Текущая сюжетная задача:", text=cur)
        if ok:
            t = text.strip()
            cast_data["active_quest"] = t
            RPCastManager.save_cast(self.session_id, cast_data)
            self.lbl_q_status.setText(f" [АКТИВНО: {t}]" if t else " [СВОБОДНО]")

    def _add_intimacy_milestone(self):
        text, ok = QInputDialog.getText(self, "Обновление связи со спутницей", "Новый статус союза / веха:")
        if ok and text.strip():
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            rel_ctx = cast_data.setdefault("relationship_context", {
                "dynamic_tone": "",
                "foundation": "",
                "current_bond": ""
            })
            clean_text = text.strip()
            if not rel_ctx.get("foundation"):
                rel_ctx["foundation"] = clean_text
            else:
                rel_ctx["current_bond"] = clean_text
            
            cast_data.setdefault("intimacy_milestones", []).append(clean_text)
            RPCastManager.save_cast(self.session_id, cast_data)
            self.load_data()

    def _delete_intimacy_milestone(self):
        row = self.list_milestones.currentRow()
        if row >= 0:
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            rel_ctx = cast_data.setdefault("relationship_context", {
                "dynamic_tone": "",
                "foundation": "",
                "current_bond": ""
            })
            item = self.list_milestones.item(row)
            if item:
                txt = item.text()
                if "Фундамент" in txt:
                    rel_ctx["foundation"] = ""
                elif "Статус" in txt:
                    rel_ctx["current_bond"] = ""
                elif "Тон" in txt:
                    rel_ctx["dynamic_tone"] = ""
                    cast_data["relationship_state"] = ""
                else:
                    ms = cast_data.get("intimacy_milestones", [])
                    clean_val = txt.lstrip("• ").strip()
                    if clean_val in ms:
                        ms.remove(clean_val)
            RPCastManager.save_cast(self.session_id, cast_data)
            self.load_data()

    def _add_world_state(self):
        text, ok = QInputDialog.getText(self, "Новое правило мира", "Опасность / правило:")
        if ok and text.strip():
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            cast_data.setdefault("world_state", []).append(text.strip())
            RPCastManager.save_cast(self.session_id, cast_data)
            self.list_world_state.addItem(QListWidgetItem(f"• {text.strip()}"))

    def _delete_world_state(self):
        row = self.list_world_state.currentRow()
        if row >= 0:
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            ws = cast_data.get("world_state", [])
            if row < len(ws):
                ws.pop(row)
                RPCastManager.save_cast(self.session_id, cast_data)
                self.list_world_state.takeItem(row)

    def _edit_stat(self):
        name, ok1 = QInputDialog.getText(self, "Шкала", "Имя параметра (например: Привязанность):")
        if ok1 and name.strip():
            val, ok2 = QInputDialog.getText(self, "Значение", f"Значение для '{name.strip()}':")
            if ok2 and val.strip():
                u_name, c_name = self._get_actor_names()
                cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
                cast_data.setdefault("stats", {})[name.strip()] = val.strip()
                RPCastManager.save_cast(self.session_id, cast_data)
                self.load_data()

    def _delete_stat(self):
        row = self.list_stats.currentRow()
        if row >= 0:
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            stats = cast_data.get("stats", {})
            keys = list(stats.keys())
            if row < len(keys):
                del stats[keys[row]]
                RPCastManager.save_cast(self.session_id, cast_data)
                self.load_data()

    # --- 2. ИНВЕНТАРЬ ---
    def _create_inventory_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(10)

        self.inventory_items = []

        layout.addWidget(QLabel("🎒 ПРЕДМЕТЫ ИНВЕНТАРЯ ОТРЯДА (<Inventory>):", w))
        self.list_inventory = QListWidget(w)
        layout.addWidget(self.list_inventory, 1)

        row_btn = QHBoxLayout()
        row_btn.setSpacing(8)
        btn_add = PushButton("Добавить предмет", w, icon=FIF.ADD)
        btn_add.clicked.connect(self._add_inventory_item)
        row_btn.addWidget(btn_add)
        btn_edit = PushButton("Изменить", w, icon=FIF.EDIT)
        btn_edit.clicked.connect(self._edit_inventory_item)
        row_btn.addWidget(btn_edit)
        btn_del = PushButton("Удалить", w, icon=FIF.DELETE)
        btn_del.clicked.connect(self._delete_inventory_item)
        row_btn.addWidget(btn_del)
        row_btn.addStretch()
        layout.addLayout(row_btn)

        lbl_hint = QLabel("💡 Инвентарь автоматически учитывается моделью при описании действий и решений.", w)
        lbl_hint.setStyleSheet("color: #64748B; font-size: 11px;")
        layout.addWidget(lbl_hint)

        return w

    def _refresh_inventory_ui(self):
        self.list_inventory.clear()
        for it in self.inventory_items:
            it_name = it.get("name", str(it)) if isinstance(it, dict) else str(it)
            self.list_inventory.addItem(QListWidgetItem(f"📦 {it_name}"))

    def _add_inventory_item(self):
        text, ok = QInputDialog.getText(self, "Новый предмет", "Название предмета:")
        if ok and text.strip():
            item_name = text.strip()
            self.inventory_items.append(item_name)
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            cast_data["inventory"] = list(self.inventory_items)
            RPCastManager.save_cast(self.session_id, cast_data)
            self._refresh_inventory_ui()

    def _edit_inventory_item(self):
        row = self.list_inventory.currentRow()
        if 0 <= row < len(self.inventory_items):
            cur = self.inventory_items[row]
            cur_name = cur.get("name", str(cur)) if isinstance(cur, dict) else str(cur)
            text, ok = QInputDialog.getText(self, "Изменить предмет", "Название предмета:", text=cur_name)
            if ok and text.strip():
                self.inventory_items[row] = text.strip()
                u_name, c_name = self._get_actor_names()
                cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
                cast_data["inventory"] = list(self.inventory_items)
                RPCastManager.save_cast(self.session_id, cast_data)
                self._refresh_inventory_ui()

    def _delete_inventory_item(self):
        row = self.list_inventory.currentRow()
        if 0 <= row < len(self.inventory_items):
            self.inventory_items.pop(row)
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            cast_data["inventory"] = list(self.inventory_items)
            RPCastManager.save_cast(self.session_id, cast_data)
            self._refresh_inventory_ui()

    # --- 3. СЦЕНАРИЙ И ЗАВЯЗКА ---
    def _create_lore_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 8)
        self.tb_lore = QTextBrowser(w)
        v.addWidget(self.tb_lore)
        return w

    # --- 4. ВТОРОСТЕПЕННЫЕ ПЕРСОНАЖИ (КАСТ) ---
    def _create_cast_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(8)

        self.cast_entities: Dict[str, Dict[str, Any]] = {}

        self.list_cast = QListWidget(w)
        self.list_cast.currentRowChanged.connect(self._on_cast_row_changed)
        layout.addWidget(self.list_cast, 1)

        self.tb_npc_details = QTextBrowser(w)
        self.tb_npc_details.setFixedHeight(90)
        layout.addWidget(self.tb_npc_details)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_add = PushButton("Добавить NPC", w, icon=FIF.ADD)
        btn_add.clicked.connect(self._add_npc)
        btn_row.addWidget(btn_add)

        btn_edit = PushButton("Изменить", w, icon=FIF.EDIT)
        btn_edit.clicked.connect(self._edit_npc)
        btn_row.addWidget(btn_edit)

        self.btn_scene = PushButton("В сцене / В памяти", w, icon=FIF.SYNC)
        self.btn_scene.clicked.connect(self._toggle_npc_presence)
        btn_row.addWidget(self.btn_scene)

        self.btn_del = PushButton("Удалить", w, icon=FIF.DELETE)
        self.btn_del.clicked.connect(self._delete_npc)
        btn_row.addWidget(self.btn_del)
        layout.addLayout(btn_row)

        return w

    def _get_entity_keys(self) -> List[str]:
        return list(self.cast_entities.keys())

    def _refresh_cast_ui(self):
        self.list_cast.clear()
        for eid in self._get_entity_keys():
            npc = self.cast_entities[eid]
            name = npc.get("canonical_name", eid)
            v_stat = npc.get("vital_status", "alive")
            presence = npc.get("presence", "physical")
            role = npc.get("role", "npc")

            if role == "companion":
                stat_badge = "🛡️ [Спутник отряда]"
            elif v_stat == "dead":
                stat_badge = "💀 [МЁРТВ]"
            elif v_stat == "critical":
                stat_badge = "🟡 [Тяжело ранен]"
            elif presence == "physical":
                stat_badge = "🟢 [В сцене]"
            else:
                stat_badge = "💭 [В памяти]"

            facts = "; ".join(npc.get("facts", []))
            facts_preview = f" — {facts[:45]}..." if facts else ""
            self.list_cast.addItem(QListWidgetItem(f"{stat_badge} {name}{facts_preview}"))

    def _on_cast_row_changed(self, row: int):
        keys = self._get_entity_keys()
        if 0 <= row < len(keys):
            eid = keys[row]
            npc = self.cast_entities[eid]
            name = npc.get("canonical_name", eid)
            v_stat = npc.get("vital_status", "alive")
            presence = npc.get("presence", "physical")
            role = npc.get("role", "npc")
            dna = npc.get("visual_dna", "")
            facts = "\n".join(f"• {f}" for f in npc.get("facts", []))
            aliases = ", ".join(npc.get("aliases", []))
            role_label = " | Роль: Спутник отряда" if role == "companion" else ""
            details = (
                f"Имя: {name} (ID: {eid}) | Статус: {v_stat} | Присутствие: {presence}{role_label}\n"
                f"Теги внешности (Visual DNA): {dna or 'нет'}\n"
                f"Алиасы: {aliases}\n"
                f"Факты:\n{facts or 'нет'}"
            )
            self.tb_npc_details.setPlainText(details)

            is_companion = (role == "companion")
            if hasattr(self, "btn_del"):
                self.btn_del.setEnabled(not is_companion)
            if hasattr(self, "btn_scene"):
                self.btn_scene.setEnabled(not is_companion)
        else:
            self.tb_npc_details.clear()
            if hasattr(self, "btn_del"):
                self.btn_del.setEnabled(True)
            if hasattr(self, "btn_scene"):
                self.btn_scene.setEnabled(True)

    def _add_npc(self):
        dlg = CastEditDialog(parent=self)
        if dlg.exec():
            data = dlg.get_data()
            name_clean = data["canonical_name"].strip()
            eid = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', name_clean.lower())
            self.cast_entities[eid] = data
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            cast_data["entities"] = self.cast_entities
            RPCastManager.save_cast(self.session_id, cast_data)
            self._refresh_cast_ui()

    def _edit_npc(self):
        keys = self._get_entity_keys()
        row = self.list_cast.currentRow()
        if 0 <= row < len(keys):
            eid = keys[row]
            dlg = CastEditDialog(npc_id=eid, npc=self.cast_entities[eid], parent=self)
            if dlg.exec():
                self.cast_entities[eid] = dlg.get_data()
                u_name, c_name = self._get_actor_names()
                cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
                cast_data["entities"] = self.cast_entities
                RPCastManager.save_cast(self.session_id, cast_data)
                self._refresh_cast_ui()
                self._on_cast_row_changed(row)

    def _toggle_npc_presence(self):
        keys = self._get_entity_keys()
        row = self.list_cast.currentRow()
        if 0 <= row < len(keys):
            eid = keys[row]
            npc = self.cast_entities[eid]
            if npc.get("role") == "companion":
                return
            if npc.get("vital_status") == "dead":
                QMessageBox.warning(self, "Внимание", "Мёртвый персонаж не может быть переведён в активную сцену.")
                return
            new_pres = "memory" if npc.get("presence", "physical") == "physical" else "physical"
            npc["presence"] = new_pres
            npc["ttl"] = 3 if new_pres == "physical" else 0
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            cast_data["entities"] = self.cast_entities
            RPCastManager.save_cast(self.session_id, cast_data)
            self._refresh_cast_ui()
            self._on_cast_row_changed(row)

    def _delete_npc(self):
        keys = self._get_entity_keys()
        row = self.list_cast.currentRow()
        if 0 <= row < len(keys):
            eid = keys[row]
            if self.cast_entities.get(eid, {}).get("role") == "companion":
                return
            del self.cast_entities[eid]
            u_name, c_name = self._get_actor_names()
            cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
            cast_data["entities"] = self.cast_entities
            RPCastManager.save_cast(self.session_id, cast_data)
            self._refresh_cast_ui()
            self.tb_npc_details.clear()

    # --- 5. ЛОКАЦИЯ И АТМОСФЕРА ---
    def _create_location_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(10)

        layout.addWidget(QLabel("📍 НАЗВАНИЕ ТЕКУЩЕЙ ЛОКАЦИИ:"))
        self.loc_name_input = LineEdit(w)
        self.loc_name_input.setPlaceholderText("Например: Заброшенный склеп, Таверна 'Пьяный Дракон'...")
        layout.addWidget(self.loc_name_input)

        self.cb_is_enclosed = CheckBox("🔒 Замкнутое укрытие / подземелье (изоляция от открытого неба)", w)
        self.cb_is_enclosed.setToolTip("Если включено, в сценах и артах подавляется естественный дневной свет и открытое небо.")
        layout.addWidget(self.cb_is_enclosed)

        layout.addWidget(QLabel("🕯️ СВЕТ И АТМОСФЕРА СЦЕНЫ (Сенсорный срез момента):"))
        self.loc_ambience_input = LineEdit(w)
        self.loc_ambience_input.setPlaceholderText("Например: Затхлый полумрак, дрожащий свет факела, сырой камень...")
        layout.addWidget(self.loc_ambience_input)

        layout.addWidget(QLabel("🪐 БАЗОВЫЙ КУПОЛ МИРА (Глобальный сеттинг):"))
        self.world_baseline_input = LineEdit(w)
        self.world_baseline_input.setPlaceholderText("По умолчанию: Земной мир, естественное освещение")
        layout.addWidget(self.world_baseline_input)

        layout.addWidget(QLabel("🎨 ПОСЛЕДНИЙ ВИЗУАЛЬНЫЙ КАДР / АРТ-ПРОМПТ (SD FORGE TAGS):"))
        self.loc_visual_input = TextEdit(w)
        self.loc_visual_input.setFixedHeight(75)
        self.loc_visual_input.setPlaceholderText("Например: ancient crypt, stone sarcophagus, torchlight, volumetric dust...")
        layout.addWidget(self.loc_visual_input)

        btn_row = QHBoxLayout()
        btn_sync = PushButton("Извлечь из чата", w, icon=FIF.SEARCH)
        btn_sync.clicked.connect(self._sync_location_from_chat)
        btn_row.addWidget(btn_sync)
        btn_row.addStretch()

        btn_save = PrimaryPushButton("Сохранить параметры локации", w, icon=FIF.SAVE)
        btn_save.clicked.connect(self._save_location)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)
        layout.addStretch()
        return w

    def _sync_location_from_chat(self):
        messages = self.session.get("messages", [])
        status_pattern = re.compile(r'\[(?:Локация|Location)[:\s]+([^\]\n]+)\]', re.IGNORECASE)
        found_loc = ""
        for m in reversed(messages):
            txt = str(m.get("text", "") or m.get("content", ""))
            match = status_pattern.search(txt)
            if match:
                found_loc = match.group(1).strip()
                break
        if found_loc:
            self.loc_name_input.setText(found_loc)
            QMessageBox.information(self, "Локация найдена", f"Обнаружена локация: {found_loc}")
        else:
            QMessageBox.information(self, "Поиск", "В недавних сообщениях нет тега [Локация: ...].")

    def _save_location(self):
        u_name, c_name = self._get_actor_names()
        cast_data = RPCastManager.load_cast(self.session_id, user_name=u_name, companion_name=c_name)
        
        loc = self.loc_name_input.text().strip()
        v_prompt = self.loc_visual_input.toPlainText().strip()
        amb = self.loc_ambience_input.text().strip()
        base = self.world_baseline_input.text().strip()
        is_enc = self.cb_is_enclosed.isChecked()

        cast_data["current_location"] = loc
        cast_data["last_visual_prompt"] = v_prompt if v_prompt else None

        env = cast_data.setdefault("environment", {})
        env["scene_ambience"] = amb
        env["world_baseline"] = base or "Земной мир, естественное освещение"
        env["is_enclosed"] = is_enc
        if v_prompt:
            env["location_dna"] = v_prompt
        elif not env.get("location_dna") and loc:
            env["location_dna"] = loc

        RPCastManager.save_cast(self.session_id, cast_data)
        QMessageBox.information(self, "Успешно", "Параметры локации, атмосферы и визуального кадра сохранены.")
