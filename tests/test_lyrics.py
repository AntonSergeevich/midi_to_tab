"""midi2tab.lyrics.transcribe(): настоящий вызов engine.transcribe(), не
подменённый целиком -- в отличие от tests/test_studio.py, где lyrics_mod.transcribe
мокается одной строкой и баги внутри самой функции не ловятся."""

import sys
import types

from midi2tab import lyrics as lyrics_mod


class FakeInfo:
    language = "ru"
    duration = 1.0


class FakeEngine:
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio_path, **kwargs):
        FakeEngine.calls.append(kwargs)
        return [], FakeInfo()


def _install_fake_whisper(monkeypatch):
    FakeEngine.calls = []
    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeEngine
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return FakeEngine.calls


def test_transcribe_song_mode_calls_whisper_without_crashing(monkeypatch):
    """song=True (web/studio.py: распознавание текста песни, «Распознать
    точнее») передавало no_speech_threshold и именованным аргументом, и
    через **kwargs одновременно -- TypeError на каждом вызове."""
    calls = _install_fake_whisper(monkeypatch)
    result = lyrics_mod.transcribe("song.wav", song=True)
    assert result.lines == []
    assert calls[0]["no_speech_threshold"] == 0.75
    assert calls[0]["vad_parameters"] == lyrics_mod.SONG_VAD


def test_transcribe_speech_mode_keeps_previous_thresholds(monkeypatch):
    calls = _install_fake_whisper(monkeypatch)
    lyrics_mod.transcribe("speech.wav", song=False)
    assert calls[0]["no_speech_threshold"] == 0.5
    assert calls[0]["vad_parameters"] == {"min_silence_duration_ms": 700, "speech_pad_ms": 300}
