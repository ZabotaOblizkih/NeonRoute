"""
ui/dialogs/forge_settings_dialog.py
===================================
Диалог настройки SD WebUI Forge в стиле Windows 11 Fluent Design
(FramelessDialog, FluentTitleBar, Dynamic Adaptive Palette).
Позволяет настраивать путь к установке Forge, проверять лаунчер и статус патчей,
а также переключать аппаратную FP8-оптимизацию SDXL и режим автогенерации иллюстраций.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QWidget,
    QFrame,
    QFileDialog,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    LineEdit,
    SwitchButton,
    SegmentedWidget,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
    IconWidget,
    FluentTitleBar,
)

import config
from core.forge_locator import (
    is_valid_forge_dir,
    update_server_config_forge_root,
    FORGE_LAUNCHER_CANDIDATES,
)
from services.forge_patcher import ForgePatcher, ForgePatchStatus
from ui.common.palette_utils import extract_adaptive_palette
from ui.common.toast_helper import toast_success, toast_info, toast_warning, toast_error
from utils.config_manager import ConfigManager

logger = logging.getLogger("NeonRoute.ForgeSettingsDialog")


class ForgeSettingsDialog(FramelessDialog):
    """
    Модальный диалог настройки SD WebUI Forge:
    - Выбор и валидация рабочей директории Forge
    - Проверка наличия лаунчера (forge_launcher.bat / webui-user.bat)
    - Информационный статус совместимости (наличие патчей VRAM и options)
    - FP8 квантование SDXL UNet (forge_sdxl_fp8_enabled)
    - Режим автогенерации артов (auto_generate_art)
    - Динамическая адаптивная палитра под цвет обоев
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self.setWindowTitle("Настройки SD WebUI Forge — NeonRoute")
        self.setObjectName("ForgeSettingsDialog")
        self.resize(580, 640)
        self.setMinimumSize(520, 560)
        self.setModal(True)

        # 1. Fluent TitleBar с [X]
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
            self.titleBar.closeBtn.clicked.connect(self.reject)

        self._current_palette: Dict[str, str] = {}

        self._load_current_settings()
        self._init_ui()
        self.update_adaptive_palette()
        self._sync_with_current_config()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои в иерархии окон для адаптивной палитры."""
        parent_win = getattr(self, "_parent_ref", None) or self.parent()
        p = parent_win
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = getattr(p, "_parent_ref", None) or (p.parent() if hasattr(p, "parent") else None)
        return None

    def update_adaptive_palette(self):
        """Адаптирует стили карточек и контролов под активные обои."""
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            ForgeSettingsDialog#ForgeSettingsDialog,
            ForgeSettingsDialog {{
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
            QWidget#ForgeSettingsBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #ForgeSettingsBodyWidget QWidget {{
                background-color: transparent;
            }}
            QLabel#dialogTitle {{
                color: {palette['group_title']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#dialogSubtitle {{
                color: {palette['text_secondary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
            }}
            QFrame#cardFrame {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame#cardFrame:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            LineEdit {{
                background-color: {palette['bg']};
                color: {palette['text_primary']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                padding: 5px 10px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
            }}
            LineEdit:focus {{
                border-color: {palette['accent']};
            }}
            PushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 500;
                font-size: 12px;
                padding: 6px 16px;
                min-height: 20px;
            }}
            PushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
            PrimaryPushButton {{
                background-color: {palette['accent']};
                border: 1px solid {palette['accent']};
                border-radius: 6px;
                color: #0A0D14;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 12px;
                padding: 6px 20px;
                min-height: 20px;
            }}
            PrimaryPushButton:hover {{
                background-color: {palette['group_title']};
                border-color: {palette['group_title']};
            }}
        """)

    def showEvent(self, event):
        super().showEvent(event)
        self.update_adaptive_palette()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _load_current_settings(self):
        """Загрузка сохраненных параметров Forge."""
        self.app_cfg = ConfigManager.load_config()
        self.current_forge_root = str(config.FORGE_ROOT_DIR.resolve()) if config.FORGE_ROOT_DIR else ""
        self.current_sdxl_fp8 = bool(self.app_cfg.get("forge_sdxl_fp8_enabled", True))
        self.current_auto_art = bool(self.app_cfg.get("auto_generate_art", False))

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("ForgeSettingsBodyWidget")
        main_layout = QVBoxLayout(self.body_widget)
        main_layout.setContentsMargins(24, 14, 24, 20)
        main_layout.setSpacing(12)

        # 1. Заголовок диалога
        header_box = QVBoxLayout()
        header_box.setSpacing(3)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        header_icon = IconWidget(FIF.PALETTE, self.body_widget)
        header_icon.setFixedSize(22, 22)
        header_layout.addWidget(header_icon)

        title_label = QLabel("🎨 НАСТРОЙКИ SD FORGE", self.body_widget)
        title_label.setObjectName("dialogTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_box.addLayout(header_layout)

        subtitle = CaptionLabel("Управление рабочей директорией, FP8-квантованием и режимами рендера", self.body_widget)
        subtitle.setObjectName("dialogSubtitle")
        header_box.addWidget(subtitle)
        main_layout.addLayout(header_box)

        # Разделитель
        sep = QFrame(self.body_widget)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        sep.setFixedHeight(1)
        main_layout.addWidget(sep)

        # 2. Карточка «Папка SD WebUI Forge»
        self.card_path = QFrame(self.body_widget)
        self.card_path.setObjectName("cardFrame")
        path_layout = QVBoxLayout(self.card_path)
        path_layout.setContentsMargins(16, 12, 16, 12)
        path_layout.setSpacing(8)

        lbl_path_title = BodyLabel("Директория установки SD WebUI Forge:", self.card_path)
        lbl_path_title.setStyleSheet("font-weight: 600; color: #CBD5E1;")
        path_layout.addWidget(lbl_path_title)

        edit_row = QHBoxLayout()
        edit_row.setSpacing(8)
        self.line_path = LineEdit(self.card_path)
        self.line_path.setPlaceholderText("Например: C:\\neyroGPD или C:\\sd-webui-forge")
        self.line_path.textChanged.connect(self._on_path_text_changed)
        edit_row.addWidget(self.line_path, 1)

        self.btn_browse = PushButton(FIF.FOLDER, "Обзор...", self.card_path)
        self.btn_browse.clicked.connect(self._on_browse_clicked)
        edit_row.addWidget(self.btn_browse)
        path_layout.addLayout(edit_row)

        self.lbl_launcher_status = CaptionLabel("", self.card_path)
        self.lbl_launcher_status.setStyleSheet("font-size: 11px;")
        path_layout.addWidget(self.lbl_launcher_status)

        main_layout.addWidget(self.card_path)

        # 3. Карточка «Совместимость с NeonRoute (Патч Forge)»
        self.card_patch = QFrame(self.body_widget)
        self.card_patch.setObjectName("cardFrame")
        patch_layout = QVBoxLayout(self.card_patch)
        patch_layout.setContentsMargins(16, 12, 16, 12)
        patch_layout.setSpacing(8)

        patch_header = QHBoxLayout()
        patch_header.setSpacing(8)
        patch_title = BodyLabel("Совместимость с NeonRoute (Патчи VRAM и API):", self.card_patch)
        patch_title.setStyleSheet("font-weight: 600; color: #CBD5E1;")
        patch_header.addWidget(patch_title)
        patch_header.addStretch()

        self.btn_check_patch = PushButton(FIF.SYNC, "Проверить патчи", self.card_patch)
        self.btn_check_patch.clicked.connect(self._on_check_patch_clicked)
        patch_header.addWidget(self.btn_check_patch)
        patch_layout.addLayout(patch_header)

        self.lbl_patch_status = CaptionLabel("", self.card_patch)
        self.lbl_patch_status.setWordWrap(True)
        self.lbl_patch_status.setStyleSheet("font-size: 11px; line-height: 1.4;")
        patch_layout.addWidget(self.lbl_patch_status)

        main_layout.addWidget(self.card_patch)

        # 4. Карточка «Оптимизация VRAM и генерации»
        self.card_opt = QFrame(self.body_widget)
        self.card_opt.setObjectName("cardFrame")
        opt_layout = QVBoxLayout(self.card_opt)
        opt_layout.setContentsMargins(16, 12, 16, 12)
        opt_layout.setSpacing(12)

        # 4.1. Switch FP8
        fp8_row = QHBoxLayout()
        fp8_row.setSpacing(12)
        fp8_text_box = QVBoxLayout()
        fp8_text_box.setSpacing(2)
        fp8_title = BodyLabel("Квантование SDXL UNet в FP8 (fp8e4)", self.card_opt)
        fp8_title.setStyleSheet("font-weight: 600; color: #E2E8F0;")
        fp8_text_box.addWidget(fp8_title)

        fp8_desc = CaptionLabel("Высвобождает до 2.5 ГБ VRAM без видимой деградации качества арта.", self.card_opt)
        fp8_desc.setStyleSheet("color: #94A3B8; font-size: 11px;")
        fp8_text_box.addWidget(fp8_desc)
        fp8_row.addLayout(fp8_text_box)
        fp8_row.addStretch()

        self.switch_fp8 = SwitchButton(self.card_opt)
        self.switch_fp8.setOnText("ВКЛ")
        self.switch_fp8.setOffText("ВЫКЛ")
        self.switch_fp8.setToolTip("Переводит веса UNet SDXL в формат fp8e4 при передаче в Forge REST API.")
        fp8_row.addWidget(self.switch_fp8)
        opt_layout.addLayout(fp8_row)

        # Разделитель внутри группы
        sub_sep = QFrame(self.card_opt)
        sub_sep.setFrameShape(QFrame.Shape.HLine)
        sub_sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.05);")
        sub_sep.setFixedHeight(1)
        opt_layout.addWidget(sub_sep)

        # 4.2. Автогенерация артов
        art_row = QHBoxLayout()
        art_row.setSpacing(12)
        art_text_box = QVBoxLayout()
        art_text_box.setSpacing(2)
        art_title = BodyLabel("Генерация иллюстраций по промпту из чата", self.card_opt)
        art_title.setStyleSheet("font-weight: 600; color: #E2E8F0;")
        art_text_box.addWidget(art_title)

        art_desc = CaptionLabel("Автоматический рендер сразу при получении описания или подтверждение кнопкой.", self.card_opt)
        art_desc.setStyleSheet("color: #94A3B8; font-size: 11px;")
        art_text_box.addWidget(art_desc)
        art_row.addLayout(art_text_box)
        art_row.addStretch()

        self.segmented_art = SegmentedWidget(self.card_opt)
        self.segmented_art.addItem("manual", "Вручную")
        self.segmented_art.addItem("auto", "Авто")
        art_row.addWidget(self.segmented_art)
        opt_layout.addLayout(art_row)

        main_layout.addWidget(self.card_opt)

        main_layout.addStretch()

        # 5. Кнопки сохранения / отмены
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.btn_cancel = PushButton("Отмена", self.body_widget)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = PrimaryPushButton(FIF.ACCEPT, "Сохранить", self.body_widget)
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(self.btn_save)

        main_layout.addLayout(btn_layout)
        root_layout.addWidget(self.body_widget)

    def _sync_with_current_config(self):
        """Синхронизирует элементы интерфейса с текущими параметрами."""
        self.line_path.setText(self.current_forge_root)
        self._update_path_and_patch_status(self.current_forge_root)

        self.switch_fp8.setChecked(self.current_sdxl_fp8)
        self.segmented_art.setCurrentItem("auto" if self.current_auto_art else "manual")

    def _resolve_launcher_info(self, dir_path_str: str) -> Tuple[Optional[Path], str]:
        """Определяет наличие и имя лаунчера Forge в указанной папке."""
        if not dir_path_str:
            return None, "Путь не указан."
        try:
            p = Path(dir_path_str).resolve()
            if not p.is_dir():
                return None, "Директория не существует на диске."

            for cand in FORGE_LAUNCHER_CANDIDATES:
                launcher_p = p / cand
                if launcher_p.is_file():
                    return launcher_p, f"Обнаружен файл запуска: {cand}"

            # Проверяем вложенную webui/
            sub_webui = p / "webui_forge_cu121_torch231" / "webui"
            if sub_webui.is_dir():
                for cand in FORGE_LAUNCHER_CANDIDATES:
                    launcher_p = sub_webui / cand
                    if launcher_p.is_file():
                        return launcher_p, f"Обнаружен файл запуска в webui: {cand}"

            return None, "Файлы запуска (forge_launcher.bat, webui-user.bat) не найдены."
        except Exception as e:
            return None, f"Ошибка проверки пути: {e}"

    def _update_path_and_patch_status(self, dir_path_str: str):
        """Обновляет текстовые метки статуса лаунчера и патчей, а также состояние кнопки действия."""
        launcher_p, launcher_msg = self._resolve_launcher_info(dir_path_str)
        if launcher_p:
            self.lbl_launcher_status.setText(f"🟢 {launcher_msg}")
            self.lbl_launcher_status.setStyleSheet("color: #10B981; font-size: 11px;")
        else:
            self.lbl_launcher_status.setText(f"⚠️ {launcher_msg}")
            self.lbl_launcher_status.setStyleSheet("color: #F59E0B; font-size: 11px;")

        status, patch_msg = ForgePatcher.check_status(dir_path_str)
        has_bak = ForgePatcher.has_backup(dir_path_str) if dir_path_str else False

        self._current_patch_status = status
        self._current_has_backup = has_bak

        if status == ForgePatchStatus.PATCHED:
            if has_bak:
                self.lbl_patch_status.setText(f"{patch_msg}\n📦 Резервная копия сохранена в .neonroute_backup/original/")
                self.btn_check_patch.setText("Откатить патч")
                self.btn_check_patch.setIcon(FIF.HISTORY)
                self.btn_check_patch.setEnabled(True)
            else:
                self.lbl_patch_status.setText(f"{patch_msg}\n⚙️ Патч установлен вручную (резервная копия не требуется).")
                self.btn_check_patch.setText("Патч активен ✓")
                self.btn_check_patch.setIcon(FIF.ACCEPT)
                self.btn_check_patch.setEnabled(False)
            self.lbl_patch_status.setStyleSheet("color: #10B981; font-size: 11px;")

        elif status == ForgePatchStatus.NEEDS_PATCH:
            self.lbl_patch_status.setText(f"🟡 {patch_msg}\n💡 Резервная копия оригиналов будет сохранена автоматически перед изменением.")
            self.lbl_patch_status.setStyleSheet("color: #F59E0B; font-size: 11px;")
            self.btn_check_patch.setText("Применить патч")
            self.btn_check_patch.setIcon(FIF.ACCEPT)
            self.btn_check_patch.setEnabled(True)

        elif status == ForgePatchStatus.INCOMPATIBLE:
            self.lbl_patch_status.setText(f"⚠️ {patch_msg}")
            self.lbl_patch_status.setStyleSheet("color: #EF4444; font-size: 11px;")
            self.btn_check_patch.setText("Несовместимо")
            self.btn_check_patch.setIcon(FIF.CLOSE)
            self.btn_check_patch.setEnabled(False)

        else:  # NOT_FOUND
            self.lbl_patch_status.setText(f"⚪ {patch_msg}")
            self.lbl_patch_status.setStyleSheet("color: #94A3B8; font-size: 11px;")
            self.btn_check_patch.setText("Не найдено")
            self.btn_check_patch.setIcon(FIF.SEARCH)
            self.btn_check_patch.setEnabled(False)

    def _on_path_text_changed(self, text: str):
        """Реактивное обновление статуса при ручном вводе пути."""
        self._update_path_and_patch_status(text.strip())

    def _on_browse_clicked(self):
        """Интерактивный выбор папки через диалог ОС."""
        current_dir = self.line_path.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self,
            "Выберите директорию установки SD WebUI Forge",
            current_dir,
        )
        if selected:
            self.line_path.setText(selected)
            self._update_path_and_patch_status(selected)
            # Сброс флага игнорирования для нового пути
            try:
                cfg = ConfigManager.load_config()
                ignored = cfg.get("forge_patch_ignored_paths", [])
                norm_sel = str(Path(selected).resolve())
                if norm_sel in ignored:
                    ignored.remove(norm_sel)
                    cfg["forge_patch_ignored_paths"] = ignored
                    ConfigManager.save_config(cfg)
            except Exception as e:
                logger.debug(f"[ForgeSettingsDialog] Ошибка сброса флага игнорирования: {e}")

    def _on_check_patch_clicked(self):
        """Обработчик кнопки действия над патчем (Применить / Откатить)."""
        curr_path = self.line_path.text().strip()
        status, _ = ForgePatcher.check_status(curr_path)
        has_bak = ForgePatcher.has_backup(curr_path)

        if status == ForgePatchStatus.NEEDS_PATCH:
            ok, res_msg = ForgePatcher.apply_patch(curr_path)
            self._update_path_and_patch_status(curr_path)
            if ok:
                toast_success(self, "Патч применён", "Патчи совместимости успешно установлены. Создан бэкап в .neonroute_backup/original/.")
            else:
                toast_error(self, "Ошибка патчинга", res_msg)

        elif status == ForgePatchStatus.PATCHED and has_bak:
            ok, res_msg = ForgePatcher.restore_backup(curr_path)
            self._update_path_and_patch_status(curr_path)
            if ok:
                toast_info(self, "Откат патча", "Оригинальные файлы Forge успешно восстановлены из резервной копии.")
            else:
                toast_error(self, "Ошибка отката", res_msg)
        else:
            self._update_path_and_patch_status(curr_path)

    def _on_save_clicked(self):
        """Сохранение пути в server_config.ini и параметров в config.json."""
        new_path_str = self.line_path.text().strip()
        parent_win = self._parent_ref or self.parent()

        # 1. Валидация и сохранение пути
        path_changed = False
        if new_path_str:
            try:
                new_path = Path(new_path_str).resolve()
                old_path = config.FORGE_ROOT_DIR.resolve() if config.FORGE_ROOT_DIR else None
                if old_path != new_path:
                    if not is_valid_forge_dir(new_path):
                        toast_warning(
                            self,
                            "Внимание",
                            "В указанной папке не найдены файлы запуска Forge. Проверьте путь.",
                        )
                        return

                    path_changed = True
                    ok = update_server_config_forge_root(new_path)
                    if not ok:
                        toast_error(self, "Ошибка сохранения", "Не удалось записать forge_root_dir в server_config.ini.")
                        return

                    logger.info(f"[ForgeSettingsDialog] Обновлен путь Forge: {new_path}")
                    if parent_win and hasattr(parent_win, "btn_forge_toggle") and parent_win.btn_forge_toggle:
                        parent_win.btn_forge_toggle.setToolTip(f"Запустить SD WebUI Forge ({config.FORGE_LAUNCHER_PATH})")
                    if parent_win and hasattr(parent_win, "_refresh_forge_models"):
                        try:
                            parent_win._refresh_forge_models()
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[ForgeSettingsDialog] Ошибка валидации пути: {e}")

        # 2. Сохранение конфигурации
        sdxl_fp8 = self.switch_fp8.isChecked()
        auto_art = (self.segmented_art.currentRouteKey() == "auto")

        try:
            cfg = ConfigManager.load_config()
            cfg["forge_sdxl_fp8_enabled"] = sdxl_fp8
            cfg["auto_generate_art"] = auto_art
            ConfigManager.save_config(cfg)

            if parent_win and hasattr(parent_win, "app_config") and isinstance(parent_win.app_config, dict):
                parent_win.app_config["forge_sdxl_fp8_enabled"] = sdxl_fp8
                parent_win.app_config["auto_generate_art"] = auto_art

            logger.info(f"[ForgeSettingsDialog] Сохранено: forge_sdxl_fp8_enabled={sdxl_fp8}, auto_generate_art={auto_art}")

            toast_success(
                parent_win or self,
                "Настройки сохранены",
                "Параметры SD WebUI Forge успешно зафиксированы.",
            )
            self.accept()
        except Exception as e:
            logger.error(f"[ForgeSettingsDialog] Ошибка сохранения параметров: {e}")
            toast_error(self, "Ошибка сохранения", str(e))
