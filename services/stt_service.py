"""
Speech-to-Text (STT) Service Stub for NeonRoute
Designed for future Faster-Whisper integration.
Provides recording management, callback signals, and audio buffer processing.
"""

import logging
from typing import Optional, Callable
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger("NeonRoute.STTService")


class AudioInputService(QObject):
    """
    STT Service Stub for voice input integration.
    Allows capturing microphone input and transcribing it into text
    for insertion directly into the chat prompt.
    """
    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal()
    transcription_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, model_size: str = "base", language: str = "ru"):
        super().__init__()
        self.model_size = model_size
        self.language = language
        self._is_recording = False
        self._whisper_model = None

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def start_recording(self):
        """Starts recording audio from microphone."""
        if self._is_recording:
            return
        self._is_recording = True
        logger.info("Microphone recording started (STT stub).")
        self.recording_started.emit()

    def stop_recording_and_transcribe(self):
        """Stops recording, processes captured audio, and emits transcription."""
        if not self._is_recording:
            return
        self._is_recording = False
        logger.info("Microphone recording stopped. Transcribing...")
        self.recording_stopped.emit()

        # In stub mode, simulate or notify user of readiness for Faster-Whisper
        # When Faster-Whisper is installed, this will invoke model.transcribe()
        transcription_text = "[Голосовой ввод: готов к интеграции Faster-Whisper]"
        self.transcription_ready.emit(transcription_text)

    def load_whisper_engine(self):
        """
        Placeholder method to lazy-load Faster-Whisper on CPU or CUDA
        when dependencies are provided.
        """
        try:
            # Future import:
            # from faster_whisper import WhisperModel
            # self._whisper_model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            pass
        except Exception as e:
            logger.warning(f"Faster-Whisper not installed or failed to load: {e}")
