"""
Embedded Audio Player Component for NeonRoute
Uses PyQt6 QMediaPlayer and QAudioOutput for sleek, in-chat WAV playback.
"""

from pathlib import Path
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QSlider, QLabel, QStyle
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


class AudioPlayerWidget(QWidget):
    """
    Compact Cyberpunk audio player widget embedded directly into chat messages.
    """
    def __init__(self, audio_file_path: Path | str, parent=None):
        super().__init__(parent)
        self.audio_path = Path(audio_file_path)

        # Multimedia objects
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.85)

        self._setup_ui()
        self._connect_signals()

        if self.audio_path.exists():
            self.player.setSource(QUrl.fromLocalFile(str(self.audio_path.resolve())))

    def _setup_ui(self):
        self.setObjectName("AudioPlayerWidget")
        self.setStyleSheet("""
            #AudioPlayerWidget {
                background-color: #121824;
                border: 1px solid #1F2A3E;
                border-radius: 8px;
                padding: 4px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # Play / Pause button
        self.play_btn = QPushButton("▶", self)
        self.play_btn.setFixedSize(32, 32)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background-color: #00F0FF;
                color: #0A0E17;
                font-size: 14px;
                font-weight: bold;
                border-radius: 16px;
                border: none;
            }
            QPushButton:hover {
                background-color: #4DF4FF;
            }
        """)
        self.play_btn.clicked.connect(self.toggle_playback)
        layout.addWidget(self.play_btn)

        # Position slider
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self.set_position)
        layout.addWidget(self.slider)

        # Time label
        self.time_label = QLabel("00:00 / 00:00", self)
        self.time_label.setStyleSheet("color: #94A3B8; font-family: monospace; font-size: 11px;")
        layout.addWidget(self.time_label)

    def _connect_signals(self):
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def set_position(self, position_ratio):
        duration = self.player.duration()
        if duration > 0:
            target_ms = int((position_ratio / 1000.0) * duration)
            self.player.setPosition(target_ms)

    def _on_position_changed(self, pos_ms: int):
        duration = self.player.duration()
        if duration > 0 and not self.slider.isSliderDown():
            ratio = int((pos_ms / duration) * 1000)
            self.slider.setValue(ratio)
        self._update_time_label(pos_ms, duration)

    def _on_duration_changed(self, duration_ms: int):
        self._update_time_label(self.player.position(), duration_ms)

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText("⏸")
        else:
            self.play_btn.setText("▶")
            if self.player.position() >= self.player.duration() and self.player.duration() > 0:
                self.player.setPosition(0)
                self.slider.setValue(0)

    def _update_time_label(self, pos_ms: int, dur_ms: int):
        cur_sec = max(0, pos_ms // 1000)
        dur_sec = max(0, dur_ms // 1000)
        self.time_label.setText(f"{cur_sec // 60:02d}:{cur_sec % 60:02d} / {dur_sec // 60:02d}:{dur_sec % 60:02d}")

    def cleanup(self):
        """Stops player and releases audio output when parent message is removed."""
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:
            pass
