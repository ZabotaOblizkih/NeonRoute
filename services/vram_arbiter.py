import logging
from enum import Enum
from typing import Optional, Any, Dict

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

    def release_llm_for_diffusion(self) -> bool:
        """Гарантированно выгружает LLM из VRAM в RAM и ожидает освобождения памяти драйвером перед стартом Forge."""
        if self._current_owner == GPUOwner.DIFFUSION:
            lm_unloaded = getattr(self.lm_client, "_is_model_unloaded", False) is True
            pm = getattr(self.lm_client, "process_manager", None)
            pm_evicted = False
            if pm is not None:
                state = getattr(pm, "_server_state", None)
                state_val = getattr(state, "value", state)
                if state_val in ("EVICTED_FOR_FORGE", "STOPPED"):
                    pm_evicted = True
            if lm_unloaded or pm_evicted:
                logger.debug("[VRAMArbiter] LLM уже выгружен и GPU принадлежит DIFFUSION — пропуск повторной выгрузки.")
                return True

        if self.lm_client and hasattr(self.lm_client, "unload_models_cli"):
            logger.info("[VRAMArbiter] Синхронная выгрузка LLM перед стартом диффузии...")
            self.lm_client.unload_models_cli()
        if self.forge_client:
            self.forge_client._last_vram_wait_ts = 0.0
            if hasattr(self.forge_client, "_wait_for_vram_clear"):
                self.forge_client._wait_for_vram_clear()
        self._current_owner = GPUOwner.DIFFUSION
        return True

    def _release_forge_vram(self, force: bool = False):
        if not self.forge_client:
            return
        if not force and getattr(self.forge_client, "_checkpoint_unloaded", False) is True:
            logger.debug("[VRAMArbiter] Чекпоинт Forge уже выгружен (_checkpoint_unloaded=True) — пропуск повторного запроса.")
            self._wait_for_vram_stabilization()
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
                    self.forge_client.free_vram(unload_models=True, force=True)
                logger.info("[VRAMArbiter] Чекпоинт Forge успешно выгружен в RAM.")
            except Exception as e:
                logger.debug(f"[VRAMArbiter] Ошибка выгрузки Forge: {e}")

        # Цикл стабилизации VRAM перед передачей GPU под LLM (до 1.5 с с шагом 0.1 с)
        self._wait_for_vram_stabilization()

    def _wait_for_vram_stabilization(self, timeout_s: float = 1.5, step_s: float = 0.1):
        """
        Ожидает, пока фактическая занятость VRAM опустится ниже 750 МБ или стабилизируется
        (разница < 15 МБ между двумя соседними замерами), прежде чем отдавать владение GPUOwner.LLM.
        """
        import time
        from services.layer_budgeter import LayerBudgeter

        start_t = time.time()
        prev_used = None
        while time.time() - start_t < timeout_s:
            curr_used = LayerBudgeter.get_current_used_vram_mb()
            if curr_used <= 0.0 or curr_used < 750.0:
                logger.debug(f"[VRAMArbiter] VRAM стабилизирована: {curr_used:.1f} MB (< 750 MB).")
                break
            if prev_used is not None and abs(curr_used - prev_used) < 15.0:
                logger.debug(f"[VRAMArbiter] VRAM стабилизирована по дельте: {curr_used:.1f} MB (дельта < 15 MB).")
                break
            prev_used = curr_used
            time.sleep(step_s)

    def release_diffusion_for_llm(self, force: bool = True) -> bool:
        """Освобождает VRAM от диффузии и стабилизирует память перед запуском LLM."""
        self._release_forge_vram(force=force)
        self._current_owner = GPUOwner.LLM
        return True

    def release_all(self) -> Dict[str, str]:
        """Полностью освобождает VRAM от обеих подсистем (LLM и Forge) и сбрасывает владельца GPU."""
        details: Dict[str, str] = {}

        if self.lm_client and hasattr(self.lm_client, "unload_models_cli"):
            try:
                self.lm_client.unload_models_cli()
                details["lm_studio"] = "unloaded"
            except Exception as e:
                details["lm_studio"] = f"error: {e}"
        else:
            details["lm_studio"] = "skipped"

        if self.forge_client:
            if getattr(self.forge_client, "_checkpoint_unloaded", False) is True:
                details["forge"] = "unloaded"
            else:
                try:
                    if hasattr(self.forge_client, "unload_checkpoint"):
                        self.forge_client.unload_checkpoint()
                    if hasattr(self.forge_client, "free_vram"):
                        self.forge_client.free_vram(unload_models=True, force=True)
                    details["forge"] = "unloaded"
                except Exception as e:
                    details["forge"] = f"error: {e}"
        else:
            details["forge"] = "skipped"

        self._current_owner = GPUOwner.NONE
        logger.info(f"[VRAMArbiter] Полная очистка VRAM завершена: {details}")
        return details
