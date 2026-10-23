"""
Reminder Controller for NeonRoute.
Periodically polls SQLite for due reminders and dispatches Windows Tray,
PowerShell Toasts, and in-app neon Toast widgets.
"""

import subprocess
import logging
from typing import List, Optional
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QSystemTrayIcon, QStyle, QWidget
from ui.components.reminder_toast import ReminderToastWidget

logger = logging.getLogger("NeonRoute.ReminderController")


class ReminderController(QObject):
    reminder_triggered = pyqtSignal(str)

    def __init__(self, parent_window: QWidget):
        super().__init__(parent_window)
        self.window = parent_window
        self._active_toasts: List[ReminderToastWidget] = []

        # System Tray Icon
        self.tray_icon = QSystemTrayIcon(self.window)
        app_icon = self.window.windowIcon()
        if not app_icon.isNull():
            self.tray_icon.setIcon(app_icon)
        else:
            self.tray_icon.setIcon(self.window.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        self.tray_icon.show()

        # Heartbeat timer (every 4 seconds)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_reminders)
        self._timer.start(4000)

    def _check_reminders(self):
        try:
            from services.memory_db import memory_db
            due_list = memory_db.get_due_reminders()
            for item in due_list:
                r_id = item["id"]
                r_text = item["text"]
                r_time = item["remind_at"]
                memory_db.mark_reminder_done(r_id)

                self._dispatch_toast(r_text, r_time)
                self.reminder_triggered.emit(r_text)
        except Exception as e:
            logger.debug(f"[ReminderController] Heartbeat error: {e}")

    def _dispatch_toast(self, text: str, remind_at: str):
        # 1. Native Windows Tray
        try:
            self.tray_icon.showMessage(
                "Элария • Напоминание",
                f"{text}\n({remind_at})",
                QSystemTrayIcon.MessageIcon.Information,
                12000
            )
        except Exception as te:
            logger.debug(f"[ReminderController] Tray error: {te}")

        # 2. PowerShell Toast Notification
        try:
            ps_cmd = (
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; '
                f'$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
                f'$textNodes = $template.GetElementsByTagName("text"); '
                f'$textNodes.Item(0).AppendChild($template.CreateTextNode("Элария • Напоминание")) > $null; '
                f'$textNodes.Item(1).AppendChild($template.CreateTextNode("{text}")) > $null; '
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($template); '
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("NeonRoute").Show($toast);'
            )
            subprocess.Popen(["powershell", "-WindowStyle", "Hidden", "-Command", ps_cmd], creationflags=0x08000000)
        except Exception:
            pass

        # 3. In-App Neon Toast Widget
        toast = ReminderToastWidget(text, remind_at, self.window)
        toast.adjustSize()
        toast.setFixedWidth(320)

        margin_x = 24
        margin_y = 48
        x = self.window.width() - toast.width() - margin_x
        y = self.window.height() - toast.height() - margin_y

        active = [t for t in self._active_toasts if t.isVisible()]
        if active:
            y -= len(active) * (toast.height() + 10)

        toast.move(max(10, x), max(10, y))
        toast.show()
        toast.raise_()
        self._active_toasts.append(toast)
