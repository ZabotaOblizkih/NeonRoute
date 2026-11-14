"""
Asynchronous Backend Health Checker for NeonRoute
Ultra-fast non-blocking socket checks with status deduplication.
"""

import socket
import logging
from typing import Dict, Any, Optional
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("NeonRoute.HealthChecker")


class HealthCheckWorker(QThread):
    health_updated = pyqtSignal(dict)

    def __init__(self, interval_ms: int = 5000):
        super().__init__()
        self.interval_ms = max(interval_ms, 4000)
        self._running = True
        self._last_state: Optional[Dict[str, Any]] = None

    def run(self):
        while self._running:
            try:
                results = self._perform_health_checks()
                self._emit_if_changed(results)
            except Exception as e:
                logger.debug(f"Health check worker error: {e}")

            elapsed = 0
            while elapsed < self.interval_ms and self._running:
                self.msleep(100)
                elapsed += 100

    def stop(self):
        self._running = False
        self.wait(1000)

    def _is_port_open(self, host: str, port: int, timeout: float = 0.1) -> bool:
        # Для 127.0.0.1 таймаута 100 мс более чем достаточно
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            res = s.connect_ex((host, port))
            return res == 0
        except Exception:
            return False
        finally:
            s.close()

    def _emit_if_changed(self, results: Dict[str, Any]):
        if self._last_state is None:
            self._last_state = results
            self.health_updated.emit(results)
            return

        # Шлем сигнал только если статус хотя бы одного сервера реально изменился
        changed = (
            results["lm_ok"] != self._last_state["lm_ok"] or
            results["forge_ok"] != self._last_state["forge_ok"] or
            results["tts_ok"] != self._last_state["tts_ok"]
        )

        if changed:
            self._last_state = results
            self.health_updated.emit(results)

    def _is_forge_ready(self) -> bool:
        if not self._is_port_open("127.0.0.1", 7860, timeout=0.1):
            return False
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://127.0.0.1:7860/sdapi/v1/progress?skip_current_image=true",
                headers={"User-Agent": "NeonRoute"}
            )
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_health_snapshot(self) -> Dict[str, Any]:
        """Публичный синхронный снимок доступности серверов (LM Studio, Forge, TTS)."""
        return self._perform_health_checks()

    def _perform_health_checks(self) -> Dict[str, Any]:
        lm_online = self._is_port_open("127.0.0.1", 1234, timeout=0.1)
        lm_msg = "LM Studio Online" if lm_online else "LM Studio Offline"

        forge_online = self._is_forge_ready()
        forge_msg = "SD Forge Online" if forge_online else "SD Forge Offline"

        tts_online = self._is_port_open("127.0.0.1", 5010, timeout=0.1)
        tts_msg = "F5-TTS Online (CPU)" if tts_online else "F5-TTS Offline (CPU Mode)"

        return {
            "lm_ok": lm_online,
            "lm_msg": lm_msg,
            "forge_ok": forge_online,
            "forge_msg": forge_msg,
            "tts_ok": tts_online,
            "tts_msg": tts_msg
        }


HealthChecker = HealthCheckWorker