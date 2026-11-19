"""Countdown-Ende-Ton: synthetische Standardtöne, Lautstärke, eigene WAV, Fallback."""
from __future__ import annotations

import array
import io
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import countdown_sound as cs  # noqa: E402


def _samples(wav: bytes) -> array.array:
    with wave.open(io.BytesIO(wav), "rb") as r:
        assert r.getsampwidth() == 2
        a = array.array("h")
        a.frombytes(r.readframes(r.getnframes()))
    return a


@pytest.mark.parametrize("tone", [t for t in cs.tone_ids() if t != cs.CUSTOM_TONE])
def test_standardtoene_sind_gueltige_wavs(tone):
    s = _samples(cs.synth_tone(tone))
    assert len(s) > cs.SAMPLE_RATE * 0.3
    assert max(abs(x) for x in s) > 10000


def test_lautstaerke_skaliert_und_null_ist_stumm():
    wav = cs.synth_tone("doppelpiep")
    full = max(abs(x) for x in _samples(cs.scale_volume(wav, 100)))
    half = max(abs(x) for x in _samples(cs.scale_volume(wav, 50)))
    assert abs(half - full / 2) <= 2
    assert max(abs(x) for x in _samples(cs.scale_volume(wav, 0))) == 0


def test_eigene_datei_und_fallback(tmp_path):
    own = tmp_path / "ton.wav"
    own.write_bytes(cs.synth_tone("gong"))
    assert cs.build_sound(cs.CUSTOM_TONE, str(own), 100) == cs.scale_volume(own.read_bytes(), 100)
    # Fehlende / kaputte Datei → Standardton statt Stille
    fallback = cs.build_sound(cs.CUSTOM_TONE, str(tmp_path / "fehlt.wav"), 100)
    assert fallback == cs.scale_volume(cs.synth_tone(cs.DEFAULT_TONE), 100)
    broken = tmp_path / "kaputt.wav"
    broken.write_bytes(b"keine wav")
    assert cs.build_sound(cs.CUSTOM_TONE, str(broken), 100) == fallback
    with pytest.raises(Exception):
        cs.load_wav_file(str(broken))


def test_8bit_wav_wird_skaliert(tmp_path):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(bytes([128, 228, 28, 128]))
    out = cs.scale_volume(buf.getvalue(), 50)
    with wave.open(io.BytesIO(out), "rb") as r:
        assert list(r.readframes(4)) == [128, 178, 78, 128]


def test_unbekannte_ton_id_faellt_auf_default():
    assert cs.build_sound("gibtsnicht", "", 80) == cs.scale_volume(cs.synth_tone(cs.DEFAULT_TONE), 80)
