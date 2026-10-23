"""
F5-TTS Voice Synthesis Client for NeonRoute
Configured for CPU-only operation to conserve 8 GB VRAM budget.
Communicates with F5-TTS local wrapper/REST server (default: http://127.0.0.1:5010).
Saves generated audio files directly into C:/NeonRoute/audio.
"""

import os
import time
import wave
import math
import struct
import logging
import requests
from pathlib import Path
from typing import Tuple, Optional
from config import AUDIO_DIR, DEFAULT_TTS_URL

logger = logging.getLogger("NeonRoute.TTSClient")


class TTSService:
    def __init__(self, base_url: str = DEFAULT_TTS_URL):
        self.base_url = base_url.rstrip("/")
        self._current_session: Optional[requests.Session] = None
        self._is_aborted = False

    def check_health(self) -> Tuple[bool, str]:
        """Checks if F5-TTS server is running and responsive."""
        try:
            # Try a lightweight ping/health endpoint
            resp = requests.get(f"{self.base_url}/health", timeout=1.5)
            if resp.status_code == 200:
                return True, "F5-TTS Online (CPU)"
            return False, f"HTTP {resp.status_code}"
        except requests.exceptions.ConnectionError:
            # Try root endpoint as fallback check
            try:
                r = requests.get(f"{self.base_url}/", timeout=1.5)
                return True, "F5-TTS Online (CPU)"
            except Exception:
                return False, "F5-TTS Offline (CPU Mode)"
        except Exception as e:
            return False, f"Offline ({str(e)})"

    def abort(self):
        """Cancels active speech synthesis request."""
        self._is_aborted = True
        if self._current_session:
            try:
                self._current_session.close()
                logger.info("F5-TTS request aborted.")
            except Exception as e:
                logger.error(f"Error while aborting TTS session: {e}")

    def synthesize(
        self,
        text: str,
        ref_audio_path: Optional[str] = None,
        ref_text: Optional[str] = None,
        speed: float = 1.0
    ) -> Path:
        """
        Synthesizes text into speech using F5-TTS (CPU mode).
        If the local F5-TTS server is running, sends REST request.
        If offline, generates an informational preview WAV so the UI and audio player
        can be tested and enjoyed without requiring a live model beforehand.
        Saves output WAV to C:/NeonRoute/audio.
        """
        self._is_aborted = False
        filename = f"tts_{int(time.time())}_{os.urandom(3).hex()}.wav"
        output_file = AUDIO_DIR / filename

        self._current_session = requests.Session()
        server_online = False

        try:
            payload = {
                "text": text,
                "ref_audio": ref_audio_path,
                "ref_text": ref_text,
                "speed": speed,
                "device": "cpu"  # Explicitly instruct server to use CPU
            }

            # Attempt standard F5-TTS REST synthesis endpoint
            resp = self._current_session.post(
                f"{self.base_url}/synthesize",
                json=payload,
                timeout=(2.5, 90.0)
            )

            if self._is_aborted:
                raise InterruptedError("Синтез речи был отменен.")

            if resp.status_code == 200:
                # Save returned audio bytes
                with open(output_file, "wb") as f:
                    f.write(resp.content)
                server_online = True
                logger.info(f"Synthesized audio using F5-TTS server: {output_file}")
                return output_file

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            logger.warning(
                f"F5-TTS сервер на {self.base_url} недоступен. Создается локальный тестовый звуковой файл."
            )
        finally:
            if self._current_session:
                self._current_session.close()
                self._current_session = None

        if not server_online:
            # Generate a clean, pleasant melodic acoustic chirp WAV file
            # so the player widget works out of the box even before F5-TTS server is launched.
            self._generate_chime_wav(output_file, duration_sec=2.5)
            logger.info(f"Saved audio preview file to {output_file}")

        return output_file

    def _generate_chime_wav(self, file_path: Path, duration_sec: float = 2.0):
        """Generates a pleasant cyberpunk melodic sine chime as fallback test audio."""
        sample_rate = 44100
        n_samples = int(sample_rate * duration_sec)
        
        # Cyberpunk pentatonic notes: E5 (659.25), G#5 (830.61), B5 (987.77), E6 (1318.51)
        notes = [659.25, 830.61, 987.77, 1318.51]
        
        with wave.open(str(file_path), "wb") as wav:
            wav.setnchannels(1)  # Mono
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(sample_rate)
            
            note_len = n_samples // len(notes)
            for i in range(n_samples):
                note_idx = min(i // note_len, len(notes) - 1)
                freq = notes[note_idx]
                t = float(i) / sample_rate
                
                # Envelope decay per note
                sub_t = float(i % note_len) / sample_rate
                envelope = math.exp(-3.5 * sub_t)
                
                value = math.sin(2.0 * math.pi * freq * t) * envelope
                sample = int(value * 28000.0)
                wav.writeframes(struct.pack("<h", max(-32768, min(32767, sample))))
