"""
Workspace Process & Folder Launcher for NeonRoute.
Handles interactive execution of Python scripts in Windows cmd.exe,
opening documents (Word/Excel), and opening chat folders in Explorer.
"""

import os
import sys
import re
import subprocess
import logging
from pathlib import Path
from typing import Optional, Tuple
from config import WORKSPACE_DIR
from services.sandbox_manager import SandboxManager
from services.session_manager import SessionManager
from utils.file_utils import get_python_cli_executable

logger = logging.getLogger("NeonRoute.WorkspaceLauncher")


class WorkspaceLauncher:
    @staticmethod
    def execute_file(script_path: str) -> Tuple[bool, str]:
        """
        Runs Python scripts interactively in cmd.exe /k, or opens Word/Excel documents.
        Returns (success: bool, message: str).
        """
        p = Path(script_path)
        if not p.is_absolute():
            sess_ws = SandboxManager.get_workspace_dir()
            cand_sess = (sess_ws / p).resolve()
            if cand_sess.exists():
                p = cand_sess
            else:
                p = (WORKSPACE_DIR / p.name).resolve()

        if not p.exists():
            return False, f"Файл не найден: {p.name}"

        filename = p.name
        ws_dir = p.parent

        if p.suffix.lower() == ".py":
            try:
                clean_title = re.sub(r'["&|<>]', '', filename)
                subprocess.Popen(
                    ["cmd.exe", "/c", "start", f"{clean_title} — NeonRoute", "cmd.exe", "/k", get_python_cli_executable(), str(p.resolve())],
                    cwd=str(ws_dir),
                    shell=False
                )
                return True, f"⚡ Запуск Python: {filename}..."
            except Exception as e:
                logger.error(f"Error launching cmd console: {e}")
                return False, f"Ошибка запуска: {e}"
        else:
            try:
                os.startfile(str(p.resolve()))
                return True, f"📄 Открытие документа: {filename}..."
            except Exception as e:
                logger.error(f"Error opening application: {e}")
                return False, f"Ошибка открытия: {e}"

    @staticmethod
    def open_chat_folder(session_id: Optional[str], mode: str = "assistant"):
        """Opens chat session directory or workspace directory in Windows Explorer."""
        if mode == "assistant":
            ws_dir = SandboxManager.get_workspace_dir()
            sess_dir = SessionManager.get_session_dir(session_id)
            target_dir = sess_dir if (session_id and any(sess_dir.iterdir())) else ws_dir
        else:
            target_dir = SessionManager.get_session_dir(session_id)

        target_dir.mkdir(parents=True, exist_ok=True)
        norm = os.path.normpath(str(target_dir))
        subprocess.Popen(f'explorer "{norm}"')
