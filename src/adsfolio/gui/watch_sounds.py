"""Choose and play optional sounds for new watched PDFs.

The caller decides which watch events are new arrivals. This module does not
inspect files or subscribe to watch events.
"""

from __future__ import annotations

import logging
import math
from array import array

from PySide6.QtCore import QBuffer, QIODevice, QObject, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget

try:
    # Some PySide versions expose the signal as QAudio::State even when
    # QAudioSink.state() returns QtAudio.State. Import both namespaces.
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

    try:
        from PySide6.QtMultimedia import QAudio
    except ImportError:
        QAudio = None

    try:
        from PySide6.QtMultimedia import QtAudio as AudioNamespace
    except ImportError:
        # Qt 6.6 used QAudio for these enums.
        AudioNamespace = QAudio
    if AudioNamespace is None:
        raise ImportError("Qt audio state enums are unavailable")
except (ImportError, OSError):
    # Keep the selector usable when a Qt Multimedia library cannot load.
    QAudio = AudioNamespace = QAudioFormat = QAudioSink = QMediaDevices = None

__all__ = [
    "SILENT_SOUND",
    "SOUND_CHOICES",
    "WatchSoundChoice",
    "WatchSoundPlayer",
    "normalize_sound_id",
]

logger = logging.getLogger(__name__)

SILENT_SOUND = "silent"
SOUND_CHOICES: tuple[tuple[str, str], ...] = (
    (SILENT_SOUND, "Silent"),
    ("bell", "Bell"),
    ("bright", "Bright"),
    ("chime", "Chime"),
    ("crystal", "Crystal"),
    ("ding", "Ding"),
    ("droplet", "Droplet"),
    ("echo", "Echo"),
    ("glow", "Glow"),
    ("harp", "Harp"),
    ("marimba", "Marimba"),
    ("pulse", "Pulse"),
    ("ripple", "Ripple"),
)

# Each note is a frequency in hertz and a duration in seconds. These patterns
# are generated in memory, so bundles do not need separate audio assets.
_TONE_PATTERNS: dict[str, tuple[tuple[float, float], ...]] = {
    "bell": ((659.25, 0.30),),
    "bright": ((783.99, 0.10), (1046.50, 0.20)),
    "chime": ((523.25, 0.14), (659.25, 0.22)),
    "crystal": ((1046.50, 0.10), (1318.51, 0.10), (1567.98, 0.20)),
    "ding": ((880.00, 0.20),),
    "droplet": ((1174.66, 0.09), (783.99, 0.18)),
    "echo": ((659.25, 0.14), (659.25, 0.22)),
    "glow": ((392.00, 0.16), (493.88, 0.24)),
    "harp": ((392.00, 0.10), (493.88, 0.10), (587.33, 0.20)),
    "marimba": ((523.25, 0.08), (392.00, 0.08), (523.25, 0.16)),
    "pulse": ((440.00, 0.10), (440.00, 0.10), (440.00, 0.16)),
    "ripple": ((659.25, 0.09), (880.00, 0.09), (659.25, 0.18)),
}


def normalize_sound_id(sound_id: str | None) -> str:
    """Return a known sound ID, or the silent ID for an unknown value.

    Parameters
    ----------
    sound_id : str or None
        Persisted sound ID.

    Returns
    -------
    str
        A supported ID. Unknown values select silence.
    """
    if isinstance(sound_id, str) and sound_id in _TONE_PATTERNS:
        return sound_id
    return SILENT_SOUND


def _render_sound(sound_id: str, audio_format: QAudioFormat) -> bytes:
    """Render a tone pattern as interleaved native-endian PCM samples."""
    formats = {
        QAudioFormat.SampleFormat.UInt8: ("B", 127),
        QAudioFormat.SampleFormat.Int16: ("h", 32767),
        QAudioFormat.SampleFormat.Int32: ("i", 2147483647),
        QAudioFormat.SampleFormat.Float: ("f", 1),
    }
    sample_format = audio_format.sampleFormat()
    if not audio_format.isValid() or sample_format not in formats:
        raise ValueError("Unsupported audio format")
    typecode, scale = formats[sample_format]
    rate = audio_format.sampleRate()
    channels = audio_format.channelCount()
    samples = array(typecode)
    attack = max(1, round(rate * 0.008))
    release = max(1, round(rate * 0.040))

    for frequency, duration in _TONE_PATTERNS[sound_id]:
        frames = round(rate * duration)
        for frame in range(frames):
            envelope = min(1.0, frame / attack, (frames - 1 - frame) / release)
            level = 0.4 * envelope * math.sin(2.0 * math.pi * frequency * frame / rate)
            if sample_format == QAudioFormat.SampleFormat.Float:
                value = level
            else:
                value = round(level * scale)
                if sample_format == QAudioFormat.SampleFormat.UInt8:
                    value += 128
            samples.extend([value] * channels)
    return samples.tobytes()


class WatchSoundPlayer(QObject):
    """Play one generated notification sound through the default Qt output.

    Create and call the player on the Qt GUI thread. Keep it alive while a
    sound plays. Starting another sound stops the previous sound.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sink: QAudioSink | None = None
        self._buffer: QBuffer | None = None

    def play(self, sound_id: str | None) -> bool:
        """Request playback of a sound.

        Parameters
        ----------
        sound_id : str or None
            A stable ID from `SOUND_CHOICES`. Unknown IDs select silence.

        Returns
        -------
        bool
            True when Qt accepts the request. This does not guarantee audible
            output. Return False for silence or an unavailable audio output.
        """
        self.stop()
        sound_id = normalize_sound_id(sound_id)
        if sound_id == SILENT_SOUND or QAudioSink is None:
            return False

        try:
            device = QMediaDevices.defaultAudioOutput()
            if device.isNull():
                logger.debug("No audio output is available for the watch sound")
                return False
            audio_format = device.preferredFormat()
            if not audio_format.isValid() or not device.isFormatSupported(audio_format):
                logger.debug("The audio output has no supported preferred format")
                return False
            data = _render_sound(sound_id, audio_format)
            self._buffer = QBuffer(self)
            self._buffer.setData(data)
            if not self._buffer.open(QIODevice.ReadOnly):
                self.stop()
                return False
            sink = QAudioSink(device, audio_format, self)
            self._sink = sink
            sink.setVolume(0.5)
            sink.stateChanged.connect(self._state_changed)
            sink.start(self._buffer)
            if self._sink is not sink or sink.error() != AudioNamespace.Error.NoError:
                self.stop()
                return False
            return True
        except (OSError, RuntimeError, TypeError, ValueError, OverflowError):
            logger.debug("Cannot play the watch sound", exc_info=True)
            self.stop()
            return False

    def stop(self) -> None:
        """Stop the current sound and release its audio resources."""
        sink, buffer = self._sink, self._buffer
        self._sink = None
        self._buffer = None
        if sink is not None:
            try:
                sink.stateChanged.disconnect(self._state_changed)
            except (RuntimeError, TypeError):
                logger.debug("The audio signal was already disconnected", exc_info=True)
            try:
                sink.stop()
                sink.deleteLater()
            except RuntimeError:
                logger.debug("The audio output was already released", exc_info=True)
        if buffer is not None:
            try:
                buffer.close()
                buffer.deleteLater()
            except RuntimeError:
                logger.debug("The audio buffer was already released", exc_info=True)

    def _state_changed(self, state: AudioNamespace.State) -> None:
        sink = self.sender()
        if sink is not self._sink:
            return
        # QtAudio and the older signal's QAudio enums can be distinct classes.
        if state.value == AudioNamespace.State.IdleState.value:
            self.stop()
        elif state.value == AudioNamespace.State.StoppedState.value:
            if sink.error() != AudioNamespace.Error.NoError:
                logger.debug("The watch sound stopped because audio output failed")
            self.stop()


class WatchSoundChoice(QWidget):
    """Select and preview a sound without saving settings.

    Parameters
    ----------
    parent : QWidget or None
        Parent widget.
    sound_id : str or None, optional
        Initial sound ID. The default is silent.
    player : WatchSoundPlayer or None, optional
        Shared player for previews. The widget creates a player when omitted.
    """

    sound_changed = Signal(str)
    preview_failed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        sound_id: str | None = SILENT_SOUND,
        player: WatchSoundPlayer | None = None,
    ) -> None:
        super().__init__(parent)
        self._player = player if player is not None else WatchSoundPlayer(self)
        self.combo = QComboBox(self)
        self.combo.setAccessibleName("Sound for new PDFs")
        self.combo.setToolTip("Choose a sound for new watched PDFs.")
        for choice_id, label in SOUND_CHOICES:
            self.combo.addItem(label, choice_id)
        self.preview_button = QPushButton("Preview", self)
        self.preview_button.setAccessibleName("Preview sound for new PDFs")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo)
        layout.addWidget(self.preview_button)
        self.set_sound(sound_id)
        self.combo.currentIndexChanged.connect(self._selection_changed)
        self.preview_button.clicked.connect(self.preview_sound)
        self._update_preview_button()

    def sound_id(self) -> str:
        """Return the selected stable sound ID."""
        return self.combo.currentData()

    def set_sound(self, sound_id: str | None) -> None:
        """Select a sound ID, or silence when the ID is unknown."""
        index = self.combo.findData(normalize_sound_id(sound_id))
        self.combo.setCurrentIndex(index)

    def preview_sound(self) -> bool:
        """Request a preview and return whether Qt accepted it."""
        played = self._player.play(self.sound_id())
        if not played and self.sound_id() != SILENT_SOUND:
            self.preview_failed.emit()
        return played

    def _selection_changed(self) -> None:
        self._update_preview_button()
        self.sound_changed.emit(self.sound_id())

    def _update_preview_button(self) -> None:
        enabled = self.sound_id() != SILENT_SOUND
        self.preview_button.setEnabled(enabled)
        self.preview_button.setToolTip(
            "Play the selected sound." if enabled else "Select a sound to preview."
        )
