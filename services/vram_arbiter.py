import logging
from enum import Enum
from typing import Optional, Any

logger = logging.getLogger("NeonRoute.VRAMArbiter")


class GPUOwner(str, Enum):
    NONE = "none"
    LLM = "llm"
    DIFFUSION = "diffusion"


class VRAMArbiter:
    """
    Централизованный арбитр видеопамяти для систем с 8 ГБ VRAM (RTX 5050 / RTX 4060).
    Исключает конфликты за память между языковыми моделями (Qwen 7B) и диффузией (SD WebUI Forge).
    Гарантирует детерминированное освобождение ресурсов перед началом инференса.
    """
    _instance: Optional["VRAMArbiter"] = None

    def __init__(self, lm_client: Optional[Any] = None, forge_client: Optional[Any] = None):
        self.lm_client = lm_client
        self.forge_client = forge_client
        self._current_owner: GPUOwner = GPUOwner.NONE

    @classmethod
    def get_instance(cls) -> "VRAMArbiter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_clients(self, lm_client: Any, forge_client: Any):
        """Регистрирует клиентов инференса в арбитре."""
        self.lm_client = lm_client
        self.forge_client = forge_client

    def prepare_for_task(self, task_type: str, metadata: Optional[dict] = None) -> None:
        """
        Синхронно вызывается внутри рабочего потока TaskQueueWorker перед выполнением задачи.
        Гарантирует, что GPU подготовлен под целевой тип инференса.
        """
        meta = metadata or {}

        # ── 1. ЗАДАЧИ ЯЗЫКОВОЙ МОДЕЛИ (LLM, Текстовый диалог, Перевод, Инженер) ──
        if task_type in ("llm", "translation", "file") or meta.get("is_ephemeral_doc"):
            if self._current_owner != GPUOwner.LLM:
                self._release_forge_vram()
                self._current_owner = GPUOwner.LLM
                logger.info("[VRAMArbiter] GPU монопольно передан под LLM/Текст/Инженер.")

        # ── 2. ЗАДАЧИ ДИФФУЗИИ (Forge txt2img, scene_art, scene_background) ──
        elif task_type in ("image", "scene_art", "scene_background"):
            if meta.get("needs_llm_prompt_gen"):
                logger.info("[VRAMArbiter] Пропуск предварительной выгрузки LLM: сначала требуется генерация арт-промта в РП.")
                return
            self.release_llm_for_diffusion()

        # ── 3. СЕРВИСНЫЕ И CPU ЗАДАЧИ (Memory, File/Sandbox, TTS) ──
        else:
            # Не требуют монопольной смены владельца GPU
            pass

    def release_llm_for_diffusion(self) -> None:
        """Гарантированно выгружает LLM из VRAM в RAM и ожидает освобождения памяти драйвером перед стартом Forge."""
        if self.lm_client and hasattr(self.lm_client, "unload_models_cli"):
            logger.info("[VRAMArbiter] Синхронная выгрузка LLM перед стартом диффузии...")
            self.lm_client.unload_models_cli()
        if self.forge_client:
            self.forge_client._last_vram_wait_ts = 0.0
            if hasattr(self.forge_client, "_wait_for_vram_clear"):
                self.forge_client._wait_for_vram_clear()
        self._current_owner = GPUOwner.DIFFUSION

    def _release_forge_vram(self):
        if not self.forge_client:
            return
        is_ready = False
        try:
            is_ready = self.forge_client.is_server_ready(timeout=0.3)
        except Exception:
            is_ready = False
        if is_ready:
            try:
                if hasattr(self.forge_client, "unload_checkpoint"):
                    self.forge_client.unload_checkpoint()
                if hasattr(self.forge_client, "free_vram"):
                    self.forge_client.free_vram()
                logger.info("[VRAMArbiter] Чекпоинт Forge успешно выгружен в RAM.")
            except Exception as e:
                logger.debug(f"[VRAMArbiter] Ошибка выгрузки Forge: {e}")
