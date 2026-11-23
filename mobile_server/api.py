"""
FastAPI application and embedded lifecycle helpers for NeonRoute Mobile Server.
"""

import logging
import socket
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from mobile_server.config import settings
from mobile_server.routes import system, sessions, chat, characters, art, media

logger = logging.getLogger("NeonRoute.MobileServer")

app = FastAPI(
    title="NeonRoute Mobile Gateway",
    description="Headless Backend-for-Frontend (BFF) REST/SSE API for mobile clients",
    version="1.0.0",
)

# CORS Configuration for local network and VPN (Tailscale)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_cache_mobile_shell_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/static/sw.js") or path.startswith(("/static/js/", "/static/css/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Mount API routers
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(characters.router, prefix="/api/characters", tags=["Characters"])
app.include_router(art.router, prefix="/api/art", tags=["Art"])
app.include_router(media.router, prefix="/media", tags=["Media"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": "NeonRoute Mobile Gateway"}


# Mount static assets and serve root index.html
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(static_dir / "index.html")


# ─── In-Process Lifecycle Controls (for MainWindow integration) ───

_server_lock = threading.Lock()
_uvicorn_server: Optional[object] = None
_server_thread: Optional[threading.Thread] = None
_server_error: Optional[str] = None


def _is_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_mobile_server_urls(port: Optional[int] = None) -> List[str]:
    """
    Returns candidate HTTP URLs reachable from mobile devices (LAN Wi-Fi first, then Tailscale/other private IPs).
    """
    target_port = int(port or settings.PORT)
    ips: List[str] = []

    # 1. Primary outbound LAN IP via OS routing table (no packets sent)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            primary_ip = sock.getsockname()[0]
            if primary_ip and not primary_ip.startswith(("127.", "169.254.")):
                ips.append(primary_ip)
        finally:
            sock.close()
    except OSError:
        pass

    # 2. Additional active IPv4 interfaces (e.g. Wi-Fi / Tailscale 100.x)
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    if not ip or ip.startswith(("127.", "169.254.")) or ip in ips:
                        continue
                    ips.append(ip)
    except Exception:
        pass

    if not ips:
        try:
            host_ip = socket.gethostbyname(socket.gethostname())
            if host_ip and not host_ip.startswith("127."):
                ips.append(host_ip)
        except OSError:
            pass

    if not ips:
        ips.append("127.0.0.1")

    return [f"http://{ip}:{target_port}" for ip in ips]


def is_mobile_server_running() -> bool:
    with _server_lock:
        if _server_thread is not None and _server_thread.is_alive():
            return True
    return _is_port_listening(settings.PORT)


def start_mobile_server_thread(timeout: float = 3.0) -> Tuple[bool, str]:
    """Starts the FastAPI Mobile Gateway inside a background thread sharing TaskQueueWorker & VRAMArbiter."""
    global _uvicorn_server, _server_thread, _server_error

    with _server_lock:
        if _server_thread is not None and _server_thread.is_alive():
            return True, f"Мобильный сервер уже запущен (порт {settings.PORT})."

        if _is_port_listening(settings.PORT):
            # Быстрый L7-хэндшейк (timeout=0.3s) для проверки, что это именно наш сервер
            is_our_server = False
            try:
                import urllib.request
                req = urllib.request.Request(f"http://127.0.0.1:{settings.PORT}/api/system/health")
                with urllib.request.urlopen(req, timeout=0.3) as resp:
                    if resp.status == 200:
                        is_our_server = True
            except Exception:
                is_our_server = False

            if is_our_server:
                return True, f"Мобильный сервер уже доступен на порту {settings.PORT}."
            else:
                from services.process_manager import inspect_port_occupant
                is_occ, occ_pid, occ_name = inspect_port_occupant(settings.PORT)
                occ_desc = f"процессом '{occ_name}' (PID: {occ_pid})" if is_occ and occ_pid else "посторонней службой"
                err_msg = f"Порт {settings.PORT} занят {occ_desc}, не являющимся NeonRoute Mobile Gateway."
                logger.error(f"[MobileServer] {err_msg}")
                return False, err_msg

        import uvicorn
        _server_error = None
        config = uvicorn.Config(
            app,
            host=settings.HOST,
            port=settings.PORT,
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # safe in non-main thread

        def _runner():
            global _server_error
            try:
                server.run()
            except Exception as exc:
                _server_error = str(exc)
                logger.error(f"[MobileServer] Server thread error: {exc}", exc_info=True)

        thread = threading.Thread(target=_runner, name="NeonRouteMobileGateway", daemon=True)
        _uvicorn_server = server
        _server_thread = thread
        thread.start()

    try:
        from PyQt6.QtWidgets import QApplication
        qapp = QApplication.instance()
    except Exception:
        qapp = None

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if qapp is not None:
            qapp.processEvents()
        if _server_error:
            return False, f"Ошибка запуска мобильного сервера: {_server_error}"
        if getattr(_uvicorn_server, "started", False) or _is_port_listening(settings.PORT, timeout=0.1):
            return True, f"Мобильный сервер запущен на порту {settings.PORT}."
        if _server_thread is None or not _server_thread.is_alive():
            return False, f"Сервер завершился при запуске ({_server_error or 'порт занят'})."
        time.sleep(0.02)

    return _is_port_listening(settings.PORT), (
        f"Мобильный сервер запущен на порту {settings.PORT}."
        if _is_port_listening(settings.PORT)
        else "Не удалось дождаться старта мобильного сервера."
    )


def stop_mobile_server_thread(timeout: float = 2.0) -> bool:
    """Gracefully stops the in-process FastAPI Mobile Gateway thread."""
    global _uvicorn_server, _server_thread
    with _server_lock:
        server = _uvicorn_server
        thread = _server_thread

    if server is not None:
        server.should_exit = True
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)

    stopped = thread is None or not thread.is_alive()
    if stopped:
        with _server_lock:
            _uvicorn_server = None
            _server_thread = None
    return stopped
