"""Tests for watched-PDF sound selection and playback."""

from __future__ import annotations

import os
import subprocess
import sys
from array import array

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QObject, Signal

from adsfolio.gui import watch_sounds
from adsfolio.gui.watch_sounds import (
    SILENT_SOUND,
    SOUND_CHOICES,
    WatchSoundChoice,
    WatchSoundPlayer,
    normalize_sound_id,
)


@pytest.fixture
def audio_format():
    if watch_sounds.QAudioFormat is None:
        pytest.skip("Qt Multimedia is unavailable")
    audio_format = watch_sounds.QAudioFormat()
    audio_format.setSampleRate(8000)
    audio_format.setChannelCount(2)
    audio_format.setSampleFormat(watch_sounds.QAudioFormat.SampleFormat.Int16)
    return audio_format


class FakeDevice:
    def __init__(self, audio_format, *, null=False, supported=True):
        self.audio_format = audio_format
        self.null = null
        self.supported = supported

    def isNull(self):
        return self.null

    def preferredFormat(self):
        return self.audio_format

    def isFormatSupported(self, audio_format):
        return self.supported and audio_format == self.audio_format


@pytest.fixture
def fake_audio(monkeypatch, audio_format):
    audio = watch_sounds.AudioNamespace
    sinks = []
    device = FakeDevice(audio_format)

    class FakeSink(QObject):
        stateChanged = Signal(object)

        def __init__(self, output, fmt, parent):
            super().__init__(parent)
            self.output = output
            self.audio_format = fmt
            self.error_value = audio.Error.NoError
            self.started_data = None
            self.stopped = False
            self.volume = None
            self.start_failure = False
            sinks.append(self)

        def setVolume(self, volume):
            self.volume = volume

        def start(self, source):
            self.started_data = bytes(source.data())
            if self.start_failure:
                raise RuntimeError("Audio output unavailable")

        def stop(self):
            self.stopped = True
            self.stateChanged.emit(audio.State.StoppedState)

        def error(self):
            return self.error_value

    class FakeMediaDevices:
        @staticmethod
        def defaultAudioOutput():
            return device

    monkeypatch.setattr(watch_sounds, "QMediaDevices", FakeMediaDevices)
    monkeypatch.setattr(watch_sounds, "QAudioSink", FakeSink)
    return device, sinks


def test_choices_default_to_silence_and_have_distinct_ids_and_labels():
    assert SOUND_CHOICES[0] == (SILENT_SOUND, "Silent")
    ids, labels = zip(*SOUND_CHOICES)
    assert len(ids) >= 13
    assert len(set(ids)) == len(ids)
    assert len(set(labels)) == len(labels)
    assert normalize_sound_id(None) == SILENT_SOUND
    assert normalize_sound_id("unknown") == SILENT_SOUND
    assert all(normalize_sound_id(sound_id) == sound_id for sound_id in ids)


def test_audio_namespace_matches_actual_sink_enums(qtbot):
    if watch_sounds.QAudioSink is None:
        pytest.skip("Qt Multimedia is unavailable")
    sink = watch_sounds.QAudioSink()
    assert type(sink.error()) is watch_sounds.AudioNamespace.Error
    assert type(sink.state()) is watch_sounds.AudioNamespace.State


def test_each_choice_produces_a_distinct_non_silent_pattern(audio_format):
    rendered = [
        watch_sounds._render_sound(sound_id, audio_format)
        for sound_id, _label in SOUND_CHOICES[1:]
    ]
    assert len(set(rendered)) == len(rendered)
    assert all(any(data) for data in rendered)


@pytest.mark.parametrize(
    ("sample_format", "typecode", "silence", "limit"),
    [
        ("UInt8", "B", 128, 128),
        ("Int16", "h", 0, 32767),
        ("Int32", "i", 0, 2147483647),
        ("Float", "f", 0, 1),
    ],
)
def test_pcm_matches_output_format_and_has_equal_channels(
    audio_format, sample_format, typecode, silence, limit
):
    audio_format.setSampleFormat(
        getattr(watch_sounds.QAudioFormat.SampleFormat, sample_format)
    )
    data = watch_sounds._render_sound("chime", audio_format)
    samples = array(typecode)
    samples.frombytes(data)

    assert len(data) % audio_format.bytesPerFrame() == 0
    assert samples[0] == samples[1] == silence
    assert samples[-1] == samples[-2] == silence
    assert samples[::2] == samples[1::2]
    assert any(sample != silence for sample in samples)
    assert all(abs(sample - silence) <= limit for sample in samples)


def test_selector_previews_only_on_request_and_reports_changes(qtbot):
    class Player:
        def __init__(self):
            self.played = []

        def play(self, sound_id):
            self.played.append(sound_id)
            return True

    player = Player()
    choice = WatchSoundChoice(player=player)
    qtbot.addWidget(choice)
    changes = []
    choice.sound_changed.connect(changes.append)

    assert choice.sound_id() == SILENT_SOUND
    assert not choice.preview_button.isEnabled()
    choice.set_sound("chime")
    assert changes == ["chime"]
    assert player.played == []
    assert choice.preview_button.isEnabled()
    choice.preview_button.click()
    assert player.played == ["chime"]
    choice.set_sound("no-longer-available")
    assert choice.sound_id() == SILENT_SOUND
    assert changes == ["chime", SILENT_SOUND]
    assert not choice.preview_button.isEnabled()


def test_selector_round_trips_all_ids_and_initial_value(qtbot):
    choice = WatchSoundChoice(sound_id="ripple")
    qtbot.addWidget(choice)
    assert choice.sound_id() == "ripple"
    for sound_id, _label in SOUND_CHOICES:
        choice.set_sound(sound_id)
        assert choice.sound_id() == sound_id


def test_silence_and_unknown_ids_do_not_access_the_audio_device(qtbot, monkeypatch):
    class UnexpectedDevice:
        @staticmethod
        def defaultAudioOutput():
            pytest.fail("Silent selections must not access the audio device")

    monkeypatch.setattr(watch_sounds, "QMediaDevices", UnexpectedDevice)
    player = WatchSoundPlayer()
    assert player.play(SILENT_SOUND) is False
    assert player.play("unknown") is False


@pytest.mark.parametrize("problem", ["no_device", "unsupported", "missing_backend"])
def test_unavailable_audio_returns_false(qtbot, monkeypatch, fake_audio, problem):
    device, sinks = fake_audio
    if problem == "no_device":
        device.null = True
    elif problem == "unsupported":
        device.supported = False
    else:
        monkeypatch.setattr(watch_sounds, "QAudioSink", None)

    player = WatchSoundPlayer()
    assert player.play("chime") is False
    assert sinks == []


def test_player_retains_buffer_and_replaces_previous_playback(qtbot, fake_audio):
    device, sinks = fake_audio
    player = WatchSoundPlayer()

    assert player.play("bell") is True
    first = sinks[-1]
    assert first.output is device
    assert first.volume == 0.5
    assert first.started_data
    assert player._buffer.isOpen()

    assert player.play("chime") is True
    second = sinks[-1]
    assert first.stopped
    assert second.started_data != first.started_data
    first.stateChanged.emit(watch_sounds.AudioNamespace.State.IdleState)
    assert player._sink is second

    player.stop()
    assert second.stopped
    assert player._sink is None
    assert player._buffer is None


def test_selecting_silence_stops_playback_without_opening_another_sink(
    qtbot, fake_audio
):
    _device, sinks = fake_audio
    player = WatchSoundPlayer()
    assert player.play("bell") is True
    sink = sinks[-1]

    assert player.play(SILENT_SOUND) is False
    assert sink.stopped
    assert len(sinks) == 1
    assert player._buffer is None


@pytest.mark.parametrize("state", ["IdleState", "StoppedState"])
def test_player_releases_resources_after_completion_or_failure(
    qtbot, fake_audio, state
):
    _device, sinks = fake_audio
    player = WatchSoundPlayer()
    assert player.play("bell") is True
    sink = sinks[-1]
    if state == "StoppedState":
        sink.error_value = watch_sounds.AudioNamespace.Error.IOError

    sink.stateChanged.emit(getattr(watch_sounds.AudioNamespace.State, state))
    assert sink.stopped
    assert player._sink is None
    assert player._buffer is None


def test_player_accepts_legacy_signal_enum_with_current_sink_enums(qtbot, fake_audio):
    if watch_sounds.QAudio is None:
        pytest.skip("Qt Multimedia is unavailable")
    _device, sinks = fake_audio
    player = WatchSoundPlayer()
    assert player.play("bell") is True

    sinks[-1].stateChanged.emit(watch_sounds.QAudio.State.IdleState)

    assert player._sink is None
    assert player._buffer is None


def test_start_failure_releases_resources_and_allows_retry(
    qtbot, monkeypatch, fake_audio
):
    _device, sinks = fake_audio
    original_sink = watch_sounds.QAudioSink

    def failing_sink(*args):
        sink = original_sink(*args)
        sink.start_failure = True
        return sink

    monkeypatch.setattr(watch_sounds, "QAudioSink", failing_sink)
    player = WatchSoundPlayer()
    assert player.play("bell") is False
    assert sinks[-1].stopped
    assert player._sink is None
    assert player._buffer is None

    monkeypatch.setattr(watch_sounds, "QAudioSink", original_sink)
    assert player.play("bell") is True
    player.stop()


def test_backend_open_error_returns_false(qtbot, monkeypatch, fake_audio):
    _device, sinks = fake_audio
    original_sink = watch_sounds.QAudioSink

    def failing_sink(*args):
        sink = original_sink(*args)
        sink.error_value = watch_sounds.AudioNamespace.Error.OpenError
        return sink

    monkeypatch.setattr(watch_sounds, "QAudioSink", failing_sink)
    player = WatchSoundPlayer()
    assert player.play("bell") is False
    assert sinks[-1].stopped
    assert player._sink is None
    assert player._buffer is None


@pytest.mark.parametrize("error_name", ["ImportError", "OSError"])
def test_multimedia_import_failure_keeps_the_selector_usable(error_name):
    script = """
import builtins
original_import = builtins.__import__
def without_multimedia(name, *args, **kwargs):
    if name == 'PySide6.QtMultimedia':
        raise ERROR_TYPE('Audio library unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_multimedia
from PySide6.QtWidgets import QApplication
from adsfolio.gui.watch_sounds import WatchSoundChoice, WatchSoundPlayer
app = QApplication([])
choice = WatchSoundChoice(sound_id='chime')
assert choice.sound_id() == 'chime'
assert choice.preview_sound() is False
assert WatchSoundPlayer().play('chime') is False
""".replace("ERROR_TYPE", error_name)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
