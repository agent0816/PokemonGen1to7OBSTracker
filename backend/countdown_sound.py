"""Countdown-Ende-Ton: Standardtöne (synthetisch), eigene WAV-Datei, Lautstärke.

Kivy-frei und ohne neue Abhängigkeiten: die Standardtöne werden zur Laufzeit als
16-bit-PCM-WAV im Speicher erzeugt (keine Asset-Dateien → nichts für den
Nuitka-Build zu packen), abgespielt wird per ``winsound.PlaySound(SND_MEMORY)``.
Lautstärke = Samples skalieren. Eigene Dateien: WAV (PCM 8/16 bit), andere
Formate kann winsound nicht abspielen.

``play_blocking`` blockiert bis zum Ende → nur im Executor aufrufen.
"""
from __future__ import annotations

import array
import io
import math
import os
import sys
import traceback
import wave

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/countdown_sound.log')

SAMPLE_RATE = 22050
DEFAULT_TONE = "doppelpiep"
CUSTOM_TONE = "datei"
# id → Anzeigename; Reihenfolge = Reihenfolge im Dropdown
TONE_LABELS: dict[str, str] = {
    "doppelpiep": "Doppel-Piep",
    "gong": "Gong",
    "fanfare": "Fanfare",
    "alarm": "Alarm",
    CUSTOM_TONE: "Eigene Datei (WAV)",
}
# Noten: (Frequenz Hz, Dauer s, Pause danach s); 0 Hz = Stille
_TONES: dict[str, list[tuple[float, float, float]]] = {
    # wie bisher winsound.Beep(1000, 400) + Beep(1500, 400)
    "doppelpiep": [(1000, 0.4, 0.0), (1500, 0.4, 0.0)],
    "gong": [(392, 1.6, 0.0)],
    "fanfare": [(523, 0.15, 0.03), (659, 0.15, 0.03), (784, 0.15, 0.03), (1047, 0.5, 0.0)],
    "alarm": [(880, 0.18, 0.07)] * 6,
}


def tone_ids() -> list[str]:
    return list(TONE_LABELS)


def _wav_bytes(samples: array.array, rate: int, width: int = 2, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def synth_tone(tone_id: str) -> bytes:
    """Standardton als 16-bit-mono-WAV. Gong mit Ausklingen, sonst kurze Rampen
    gegen Knacken."""
    notes = _TONES.get(tone_id) or _TONES[DEFAULT_TONE]
    out = array.array("h")
    amp = 0.6 * 32767
    for freq, dur, pause in notes:
        n = int(dur * SAMPLE_RATE)
        ramp = max(1, int(0.01 * SAMPLE_RATE))
        for i in range(n):
            if tone_id == "gong":
                env = math.exp(-3.0 * i / n)
                val = (math.sin(2 * math.pi * freq * i / SAMPLE_RATE)
                       + 0.4 * math.sin(2 * math.pi * freq * 2.01 * i / SAMPLE_RATE))
                val *= env / 1.4
            else:
                env = min(1.0, i / ramp, (n - i) / ramp)
                val = math.sin(2 * math.pi * freq * i / SAMPLE_RATE) * env
            out.append(int(amp * val))
        out.extend([0] * int(pause * SAMPLE_RATE))
    return _wav_bytes(out, SAMPLE_RATE)


def scale_volume(wav: bytes, volume: int) -> bytes:
    """Skaliert PCM-Samples (8/16 bit) auf ``volume`` Prozent (0–100)."""
    factor = max(0, min(100, int(volume))) / 100.0
    with wave.open(io.BytesIO(wav), "rb") as r:
        params = r.getparams()
        frames = r.readframes(params.nframes)
    if params.sampwidth == 2:
        data = array.array("h")
        data.frombytes(frames)
        if sys.byteorder == "big":
            data.byteswap()
        data = array.array("h", (int(s * factor) for s in data))
        if sys.byteorder == "big":
            data.byteswap()
    elif params.sampwidth == 1:  # 8 bit unsigned, Mitte 128
        data = array.array("B", (int(128 + (b - 128) * factor) for b in frames))
    else:
        raise ValueError(f"WAV mit {params.sampwidth * 8} bit wird nicht unterstützt (nur 8/16 bit)")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(data.tobytes())
    return buf.getvalue()


def check_wav_file(path: str) -> None:
    """Nur Header prüfen (UI-Thread-tauglich, liest nicht die ganze Datei)."""
    with wave.open(path, "rb") as r:
        if r.getsampwidth() not in (1, 2):
            raise ValueError(f"WAV mit {r.getsampwidth() * 8} bit wird nicht unterstützt (nur 8/16 bit)")


def load_wav_file(path: str) -> bytes:
    """Liest eine eigene WAV-Datei und prüft, dass sie lesbar ist."""
    with open(path, "rb") as fh:
        data = fh.read()
    with wave.open(io.BytesIO(data), "rb") as r:
        if r.getsampwidth() not in (1, 2):
            raise ValueError(f"WAV mit {r.getsampwidth() * 8} bit wird nicht unterstützt (nur 8/16 bit)")
    return data


def build_sound(tone_id: str, custom_path: str, volume: int) -> bytes:
    """WAV-Bytes für die Einstellung. Eigene Datei fehlt/kaputt → Standardton
    (geloggt), damit der Countdown nie stumm endet."""
    wav = None
    if tone_id == CUSTOM_TONE:
        if custom_path and os.path.isfile(custom_path):
            try:
                wav = load_wav_file(custom_path)
            except Exception as err:
                logger.warning(f"Eigene Countdown-Datei unbrauchbar ({custom_path}): {err} "
                               f"— nutze Standardton")
        else:
            logger.warning(f"Eigene Countdown-Datei fehlt: {custom_path!r} — nutze Standardton")
    if wav is None:
        wav = synth_tone(tone_id if tone_id in _TONES else DEFAULT_TONE)
    return scale_volume(wav, volume)


def play_blocking(tone_id: str, custom_path: str, volume: int) -> None:
    """Spielt den Ton synchron ab (Executor!). Fehler nur loggen."""
    if int(volume) <= 0:
        logger.info("Countdown-Ton stumm (Lautstärke 0)")
        return
    try:
        data = build_sound(tone_id, custom_path, volume)
        import winsound
        winsound.PlaySound(data, winsound.SND_MEMORY)
        logger.info(f"Countdown-Ton gespielt: tone={tone_id} volume={volume}")
    except Exception as err:
        logger.error(f"Countdown-Ton fehlgeschlagen: {type(err)},{err}")
        logger.error(traceback.format_exc())
