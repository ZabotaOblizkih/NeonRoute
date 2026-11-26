"""
ui/dialogs/updates_dialog.py
============================
Окно проверки и выборочного применения обновлений NeonRoute (FramelessDialog).
Поддерживает быструю проверку по aggregate_hash, 3-way анализ локальных правок
и безопасное применение выбранных файлов через временную директорию.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    FluentIcon as FIF,
    FluentTitleBar,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SubtitleLabel,
)

from config import APP_VERSION, BASE_DIR
from services.integrity_service import (
    FileUpdateStatus,
    ThreeWayFileEntry,
    ThreeWayReport,
    UpdateApplyResult,
    get_last_integrity_report,
)
from ui.common.palette_utils import extract_adaptive_palette
from ui.common.toast_helper import show_toast
from ui.workers.integrity_worker import (
    RemoteUpdateCheckWorker,
    SelectiveUpdateApplyWorker,
)

logger = logging.getLogger("NeonRoute.UpdatesDialog")


class UpdatesDialog(FramelessDialog):
    """Модальный диалог проверки и выборочного применения обновлений."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.window = parent
        self.setWindowTitle(f"Обновления — NeonRoute v{APP_VERSION}")
        self.resize(700, 560)
        self.setMinimumSize(580, 460)

        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.close)

        self.setObjectName("UpdatesDialog")
        self._check_worker: Optional[RemoteUpdateCheckWorker] = None
        self._apply_worker: Optional[SelectiveUpdateApplyWorker] = None
        self._current_report: Optional[ThreeWayReport] = None
        self._current_meta: Optional[dict] = None
        self._entry_map: Dict[str, ThreeWayFileEntry] = {}
        self._row_checkboxes: Dict[str, CheckBox] = {}

        self._setup_ui()
        self._apply_palette()
        self._populate_local_status()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        p = self.parent()
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            p = p.parent()
        return None

    def _apply_palette(self) -> None:
        palette = extract_adaptive_palette(self._resolve_pixmap())
        self.setStyleSheet(f"""
            UpdatesDialog#UpdatesDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#UpdatesBody {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            QWidget#UpdatesBody QLabel {{
                background-color: transparent;
            }}
            QFrame#InfoCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
                padding: 12px;
            }}
            QListWidget#FilesList {{
                background-color: rgba(15, 23, 42, 0.90);
                border: 1px solid rgba(148, 163, 184, 0.25);
                border-radius: 8px;
                color: #F8FAFC;
                padding: 6px;
                font-size: 12px;
                outline: none;
            }}
            QListWidget#FilesList::item {{
                background-color: rgba(30, 41, 59, 0.65);
                border: 1px solid rgba(148, 163, 184, 0.14);
                border-radius: 6px;
                margin: 2px 0px;
                padding: 2px 4px;
                color: #F8FAFC;
            }}
            QListWidget#FilesList::item:hover {{
                background-color: rgba(0, 240, 255, 0.12);
                border-color: rgba(0, 240, 255, 0.35);
            }}
            QListWidget#FilesList::item:selected {{
                background-color: rgba(0, 240, 255, 0.16);
                border-color: rgba(0, 240, 255, 0.45);
            }}
        """)

    def _setup_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 46, 0, 0)

        body = QWidget(self)
        body.setObjectName("UpdatesBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 14, 20, 18)
        layout.setSpacing(12)

        header = SubtitleLabel("Обновления и целостность NeonRoute", body)
        header.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 18px;")
        layout.addWidget(header)

        info_card = QFrame(body)
        info_card.setObjectName("InfoCard")
        card_layout = QVBoxLayout(info_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        self.lbl_version = BodyLabel(f"Текущая версия: v{APP_VERSION}", info_card)
        self.lbl_version.setStyleSheet("color: #E2E8F0; font-weight: 600;")
        card_layout.addWidget(self.lbl_version)

        self.lbl_local_integrity = CaptionLabel("", info_card)
        self.lbl_local_integrity.setStyleSheet("color: #94A3B8;")
        card_layout.addWidget(self.lbl_local_integrity)

        self.lbl_remote_status = CaptionLabel(
            "Нажмите «Проверить», чтобы сверить файлы с последним релизом GitHub.",
            info_card,
        )
        self.lbl_remote_status.setStyleSheet("color: #38BDF8;")
        self.lbl_remote_status.setWordWrap(True)
        card_layout.addWidget(self.lbl_remote_status)
        layout.addWidget(info_card)

        sel_row = QHBoxLayout()
        sel_row.setSpacing(8)
        self.btn_select_all = PushButton("Выбрать все", body)
        self.btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
        self.btn_deselect_all = PushButton("Снять все", body)
        self.btn_deselect_all.clicked.connect(lambda: self._set_all_checked(False))
        self.btn_select_all.setEnabled(False)
        self.btn_deselect_all.setEnabled(False)
        sel_row.addWidget(self.btn_select_all)
        sel_row.addWidget(self.btn_deselect_all)
        sel_row.addStretch(1)
        layout.addLayout(sel_row)

        self.files_list = QListWidget(body)
        self.files_list.setObjectName("FilesList")
        layout.addWidget(self.files_list, 1)

        self.progress_bar = ProgressBar(body)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_check = PrimaryPushButton(FIF.SYNC, "Проверить", body)
        self.btn_check.clicked.connect(self._on_check_clicked)

        self.btn_apply = PrimaryPushButton(FIF.DOWNLOAD, "Обновить выбранные", body)
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._on_apply_clicked)

        self.btn_close = PushButton("Закрыть", body)
        self.btn_close.clicked.connect(self.close)

        btn_row.addWidget(self.btn_check)
        btn_row.addWidget(self.btn_apply)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        root_layout.addWidget(body)

    def _populate_local_status(self) -> None:
        report = get_last_integrity_report()
        if report is None:
            self.lbl_local_integrity.setText("Локальная проверка целостности: выполняется или не запускалась.")
        elif not report.manifest_present:
            self.lbl_local_integrity.setText(
                "Локальный release_manifest.json не найден (базовый эталон B недоступен)."
            )
        elif not report.mismatches:
            self.lbl_local_integrity.setText(
                f"Локальная целостность: OK ({report.checked_count} файлов проверено)."
            )
        else:
            self.lbl_local_integrity.setText(
                f"Локальная целостность: обнаружены расхождения ({len(report.mismatches)} из {report.checked_count})."
            )
            self.lbl_local_integrity.setStyleSheet("color: #FBBF24;")

    def _set_all_checked(self, checked: bool) -> None:
        for cb in self._row_checkboxes.values():
            cb.setChecked(checked)

    def _on_check_clicked(self) -> None:
        if self._check_worker and self._check_worker.isRunning():
            return
        self.btn_check.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.files_list.clear()
        self._row_checkboxes.clear()
        self.lbl_remote_status.setStyleSheet("color: #38BDF8;")
        self.lbl_remote_status.setText("Запрос последнего релиза и проверка aggregate_hash...")

        self._check_worker = RemoteUpdateCheckWorker(BASE_DIR, APP_VERSION)
        self._check_worker.check_finished.connect(self._on_check_finished)
        self._check_worker.start()

    def _on_check_finished(self, report: Optional[ThreeWayReport], meta: Optional[dict], error: Optional[str]) -> None:
        self.btn_check.setEnabled(True)
        if error or report is None:
            self.lbl_remote_status.setStyleSheet("color: #F87171;")
            self.lbl_remote_status.setText(error or "Не удалось проверить обновления.")
            return

        self._current_report = report
        self._current_meta = meta or {}
        self._entry_map = {e.rel_path: e for e in report.entries}
        self.files_list.clear()
        self._row_checkboxes.clear()

        if report.is_up_to_date:
            self.lbl_remote_status.setStyleSheet("color: #4ADE80;")
            self.lbl_remote_status.setText(
                f"Обновлений не найдено. Установлена актуальная версия v{report.remote_version or APP_VERSION}."
            )
            self.btn_select_all.setEnabled(False)
            self.btn_deselect_all.setEnabled(False)
            self.btn_apply.setEnabled(False)
            return

        self.lbl_remote_status.setStyleSheet("color: #38BDF8;")
        self.lbl_remote_status.setText(
            f"Доступен релиз {report.remote_tag} ({len(report.entries)} изменённых файлов). "
            "Локально изменённые файлы сняты с выбора по умолчанию."
        )

        for entry in report.entries:
            if entry.status == FileUpdateStatus.UPDATE_SAFE:
                tag_lbl = "Новый файл" if entry.local_sha is None else "Обновление"
                badge_color = "#4ADE80"
                badge_bg = "rgba(74, 222, 128, 0.14)"
            elif entry.status == FileUpdateStatus.CONFLICT_LOCAL:
                tag_lbl = "Изменён локально (есть обновление)"
                badge_color = "#FBBF24"
                badge_bg = "rgba(251, 191, 36, 0.14)"
            else:
                tag_lbl = "Нет эталона B (требует выбора)"
                badge_color = "#FDBA74"
                badge_bg = "rgba(253, 186, 116, 0.14)"

            size_kb = max(1, entry.remote_size // 1024) if entry.remote_size else 1

            row_widget = QWidget(self.files_list)
            row_widget.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(10)

            cb = CheckBox(entry.rel_path, row_widget)
            cb.setChecked(entry.default_checked)
            cb.setStyleSheet(
                "CheckBox { color: #F8FAFC; font-weight: 600; font-size: 12px; background: transparent; }"
            )

            badge = QLabel(f"{tag_lbl} • ~{size_kb} КБ", row_widget)
            badge.setStyleSheet(
                f"color: {badge_color}; background-color: {badge_bg}; "
                f"border: 1px solid {badge_color}; border-radius: 5px; "
                "padding: 2px 8px; font-size: 11px; font-weight: 600;"
            )

            row_layout.addWidget(cb, 1)
            row_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignRight)

            item = QListWidgetItem(self.files_list)
            item.setData(Qt.ItemDataRole.UserRole, entry.rel_path)
            item.setSizeHint(row_widget.sizeHint())
            self.files_list.addItem(item)
            self.files_list.setItemWidget(item, row_widget)
            self._row_checkboxes[entry.rel_path] = cb

        has_items = self.files_list.count() > 0
        self.btn_select_all.setEnabled(has_items)
        self.btn_deselect_all.setEnabled(has_items)
        self.btn_apply.setEnabled(has_items and bool(self._current_meta.get("zip_url")))

    def _get_selected_files(self) -> List[str]:
        return [rel_path for rel_path, cb in self._row_checkboxes.items() if cb.isChecked()]

    def _on_apply_clicked(self) -> None:
        selected = self._get_selected_files()
        if not selected:
            show_toast(self, "Обновление", "Выберите хотя бы один файл для обновления.", kind="warning")
            return

        zip_url = (self._current_meta or {}).get("zip_url")
        remote_manifest = (self._current_meta or {}).get("remote_manifest")
        if not zip_url or not remote_manifest:
            show_toast(self, "Обновление", "В релизе отсутствует ZIP-архив или манифест.", kind="error")
            return

        self.btn_check.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.lbl_remote_status.setText(f"Загрузка и применение {len(selected)} файлов...")

        self._apply_worker = SelectiveUpdateApplyWorker(
            zip_url=zip_url,
            base_dir=BASE_DIR,
            selected_files=selected,
            remote_manifest=remote_manifest,
            entry_map=self._entry_map,
        )
        self._apply_worker.progress_updated.connect(self._on_apply_progress)
        self._apply_worker.apply_finished.connect(self._on_apply_finished)
        self._apply_worker.start()

    def _on_apply_progress(self, cur: int, tot: int, msg: str) -> None:
        if tot > 0:
            pct = max(0, min(100, int((cur / tot) * 100)))
            self.progress_bar.setValue(pct)
        self.lbl_remote_status.setText(msg)

    def _on_apply_finished(self, result: Optional[UpdateApplyResult], error: Optional[str]) -> None:
        self.btn_check.setEnabled(True)
        self.progress_bar.setVisible(False)

        if error or result is None:
            self.btn_apply.setEnabled(True)
            self.lbl_remote_status.setStyleSheet("color: #F87171;")
            self.lbl_remote_status.setText(f"Ошибка обновления: {error}")
            show_toast(self, "Ошибка обновления", str(error), kind="error")
            return

        parts = [f"Обновлено файлов: {len(result.applied_files)}"]
        if result.backup_files:
            parts.append(f"резервных копий (.bak): {len(result.backup_files)}")
        if result.locked_files:
            parts.append(f"требуют перезапуска/заняты: {len(result.locked_files)}")
        if result.errors:
            parts.append(f"ошибок: {len(result.errors)}")

        summary = ", ".join(parts) + "."
        if result.locked_files or result.errors:
            self.lbl_remote_status.setStyleSheet("color: #FBBF24;")
            locked_preview = ", ".join(result.locked_files[:3])
            self.lbl_remote_status.setText(f"{summary} Занятые файлы: {locked_preview}")
        else:
            self.lbl_remote_status.setStyleSheet("color: #4ADE80;")
            self.lbl_remote_status.setText(summary)

        if result.applied_files:
            show_toast(
                self.window or self,
                "Обновление завершено",
                f"{summary} Рекомендуется перезапустить NeonRoute для применения изменений.",
                kind="success" if not result.locked_files else "warning",
                duration=5500,
            )

    def closeEvent(self, event) -> None:
        if self._apply_worker and self._apply_worker.isRunning():
            self._apply_worker.cancel()
            self._apply_worker.quit()
            self._apply_worker.wait(1500)
        if self._check_worker and self._check_worker.isRunning():
            self._check_worker.quit()
            self._check_worker.wait(1000)
        super().closeEvent(event)
