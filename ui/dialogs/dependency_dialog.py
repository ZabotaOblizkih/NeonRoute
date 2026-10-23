from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QScrollArea, QWidget, QFrame, QMessageBox, QProgressBar
)
from PyQt6.QtCore import Qt
from services.environment_doctor import EnvironmentDoctor, PipInstallWorker
from utils.config_manager import ConfigManager

class DependencyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Проверка компонентов NeonRoute")
        self.resize(640, 520)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background-color: #1a1a1f; color: #ffffff; }
            QLabel { color: #ffffff; }
            QScrollArea { border: 1px solid #2d2d38; background: transparent; }
            QScrollBar:vertical { border: none; background: #1a1a1f; width: 8px; }
            QScrollBar::handle:vertical { background: #3d3d4d; border-radius: 4px; }
        """)

        self.missing_items = EnvironmentDoctor.check_missing()
        self.checkboxes = {}
        self.worker = None

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        h_lbl = QLabel("Дополнительные компоненты для режима Инженер")
        h_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #00e5ff;")
        layout.addWidget(h_lbl)

        sub_lbl = QLabel("Для полной автономной работы модулей Инженера рекомендуются следующие библиотеки:")
        sub_lbl.setStyleSheet("font-size: 13px; color: #a0a0b0;")
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        c_layout = QVBoxLayout(container)
        c_layout.setSpacing(10)
        c_layout.setContentsMargins(10, 10, 10, 10)

        if not self.missing_items:
            all_ok_lbl = QLabel("✅ Все необходимые компоненты и библиотеки уже установлены!")
            all_ok_lbl.setStyleSheet("font-size: 14px; color: #00e676; padding: 20px;")
            c_layout.addWidget(all_ok_lbl)
        else:
            for item in self.missing_items:
                card = QFrame()
                card.setStyleSheet("background-color: #22222c; border: 1px solid #323242; border-radius: 8px; padding: 6px;")
                card_l = QVBoxLayout(card)

                cb = QCheckBox(item["name"])
                cb.setStyleSheet("font-size: 14px; font-weight: bold; color: #ffffff;")
                if item["type"] == "python":
                    cb.setChecked(True)
                    self.checkboxes[item["package"]] = cb
                else:
                    cb.setChecked(False)
                    cb.setEnabled(False)

                desc = QLabel(item["description"])
                desc.setStyleSheet("font-size: 12px; color: #c0c0d0; margin-left: 20px;")
                desc.setWordWrap(True)

                conseq = QLabel(f"Если не ставить: {item['consequence']}")
                conseq.setStyleSheet("font-size: 11px; color: #ffb74d; margin-left: 20px;")
                conseq.setWordWrap(True)

                card_l.addWidget(cb)
                card_l.addWidget(desc)
                card_l.addWidget(conseq)

                if item["type"] == "binary" and "install_guide" in item:
                    guide = QLabel(f"Подсказка: {item['install_guide']}")
                    guide.setStyleSheet("font-size: 11px; color: #80cbc4; margin-left: 20px;")
                    guide.setWordWrap(True)
                    card_l.addWidget(guide)

                c_layout.addWidget(card)

        c_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #3d3d4d; border-radius: 4px; text-align: center; color: white; background: #15151a; height: 18px; }
            QProgressBar::chunk { background-color: #00e5ff; }
        """)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("font-size: 12px; color: #00e5ff;")
        self.status_lbl.hide()
        layout.addWidget(self.status_lbl)

        # Чекбокс «Больше не показывать»
        self.cb_dont_show = QCheckBox("Больше не показывать это окно при запуске")
        self.cb_dont_show.setStyleSheet("color: #a0a0b0; font-size: 12px;")
        layout.addWidget(self.cb_dont_show)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_skip = QPushButton("Пропустить")
        self.btn_skip.setStyleSheet("""
            QPushButton { background-color: #2d2d38; color: #ffffff; border-radius: 6px; padding: 8px 16px; font-size: 13px; }
            QPushButton:hover { background-color: #3d3d4d; }
        """)
        self.btn_skip.clicked.connect(self._on_skip)
        btn_layout.addWidget(self.btn_skip)

        self.btn_install = QPushButton("Установить выбранное")
        self.btn_install.setStyleSheet("""
            QPushButton { background-color: #00b0ff; color: #000000; font-weight: bold; border-radius: 6px; padding: 8px 18px; font-size: 13px; }
            QPushButton:hover { background-color: #40c4ff; }
            QPushButton:disabled { background-color: #555566; color: #888888; }
        """)
        self.btn_install.setEnabled(bool(self.checkboxes))
        self.btn_install.clicked.connect(self._on_install)
        btn_layout.addWidget(self.btn_install)

        layout.addLayout(btn_layout)

    def _on_skip(self):
        if self.cb_dont_show.isChecked():
            cfg = ConfigManager.load_config()
            cfg["check_dependencies_on_startup"] = False
            ConfigManager.save_config(cfg)

            QMessageBox.information(
                self,
                "Проверка компонентов",
                "Договорились, больше не буду беспокоить при старте!\n\n"
                "Если компоненты понадобятся позже — вы всегда можете запустить эту проверку вручную через верхнее меню:\n"
                "«Справка» → «Проверка компонентов...»."
            )
        self.reject()

    def _on_install(self):
        to_install = [pkg for pkg, cb in self.checkboxes.items() if cb.isChecked()]
        if not to_install:
            QMessageBox.warning(self, "Внимание", "Не выбран ни один компонент для установки.")
            return

        self.btn_install.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.progress_bar.show()
        self.status_lbl.show()

        self.worker = PipInstallWorker(to_install)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, msg: str):
        self.status_lbl.setText(msg)

    def _on_finished(self, success: bool, msg: str):
        self.progress_bar.hide()
        self.status_lbl.hide()
        self.btn_skip.setEnabled(True)
        self.btn_install.setEnabled(True)

        if success:
            QMessageBox.information(self, "Успех", msg)
            self.accept()
        else:
            QMessageBox.critical(self, "Ошибка установки", msg)
