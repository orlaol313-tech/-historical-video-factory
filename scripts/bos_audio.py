"""BonnieAndOllieStudio: синтез музыки и звуковых эффектов (numpy, без внешних файлов).

Всё звучание вычисляется формулами, поэтому оно оригинальное и свободное от лицензий.
"""
import wave
from pathlib import Path

import numpy as np
import yaml

SR = 44100


def _t(d):
    return np.arange(int(d * SR)) / SR


def _lp(x, n):
    return np.convolve(x, np.ones(n) / n, mode="same")


def midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


# ------------------------------------------------------------------ инструменты
def marimba(f, d=0.55):
    t = _t(d)
    env = np.minimum(1, t / 0.004) * np.exp(-t * 7)
    return (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 18)) * env


def bass(f, d=0.7):
    t = _t(d)
    env = np.minimum(1, t / 0.01) * np.exp(-t * 4.5)
    return (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 2 * f * t)) * env


def pad(f, d):
    t = _t(d)
    env = np.minimum(1, t / 0.4) * np.minimum(1, (d - t) / 0.4)
    return (np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * 2.003 * f * t)) * env


def hat(rng):
    t = _t(0.06)
    n = np.diff(rng.standard_normal(len(t) + 1))
    return n * np.exp(-t * 70)


def kick():
    t = _t(0.22)
    f = 50 + 90 * np.exp(-t * 25)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 16)


# --------------------------------------------------------------------- эффекты
def sfx_boing(rng):
    t = _t(0.5)
    f = 230 * (1 + 2.0 * np.exp(-t * 8)) + 35 * np.sin(2 * np.pi * 22 * t)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6.5) * 0.8


def sfx_gasp(rng):
    t = _t(0.4)
    n = _lp(rng.standard_normal(len(t)), 5) - _lp(rng.standard_normal(len(t)), 40)
    env = np.sin(np.pi * np.minimum(1, t / 0.4) ** 0.8) ** 2
    tone = np.sin(2 * np.pi * np.cumsum(420 + 600 * t / 0.4) / SR) * 0.25
    return (n * 1.2 + tone) * env * 0.7


def sfx_whoosh(rng):
    t = _t(1.0)
    n = _lp(rng.standard_normal(len(t)), 4) - _lp(rng.standard_normal(len(t)), 90)
    return n * np.sin(np.pi * t) ** 2 * 1.4


def sfx_thud(rng):
    t = _t(0.35)
    return (np.sin(2 * np.pi * 70 * t) * np.exp(-t * 12)
            + 0.5 * _lp(rng.standard_normal(len(t)), 20) * np.exp(-t * 28)) * 1.2


def sfx_slide(rng):
    t = _t(0.7)
    f = 1100 * np.exp(-t * 2.3) + 220 + 25 * np.sin(2 * np.pi * 9 * t)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.minimum(1, (0.7 - t) / 0.1) * 0.6


def sfx_giggle(rng):
    out = np.zeros(int(0.7 * SR))
    for j in range(5):
        d = 0.1
        t = _t(d)
        f = 620 + 55 * j + 70 * np.sin(2 * np.pi * 14 * t)
        pulse = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.sin(np.pi * t / d) ** 1.5
        s = int((j * 0.115) * SR)
        out[s:s + len(pulse)] += pulse
    return out * 0.5


def sfx_chime(rng):
    out = np.zeros(int(1.0 * SR))
    for j, m in enumerate((88, 91, 96)):
        t = _t(0.8)
        n = np.sin(2 * np.pi * midi(m) * t) * np.exp(-t * 5)
        s = int(j * 0.09 * SR)
        out[s:s + len(n)] += n
    return out * 0.35


def sfx_ding(rng):
    t = _t(0.7)
    return np.sin(2 * np.pi * 1760 * t) * np.exp(-t * 7) * 0.5


SFX = {"boing": sfx_boing, "gasp": sfx_gasp, "whoosh": sfx_whoosh, "thud": sfx_thud,
       "slide": sfx_slide, "giggle": sfx_giggle, "chime": sfx_chime, "ding": sfx_ding}


# ----------------------------------------------------------------------- сборка
def add(buf, start, sig, gain=1.0):
    s = int(start * SR)
    if s >= len(buf):
        return
    sig = sig[: len(buf) - s]
    buf[s:s + len(sig)] += sig * gain


CHORDS = [([60, 64, 67], 36), ([57, 60, 64], 33), ([53, 57, 60], 29), ([55, 59, 62], 31)]
PENTA = [60, 62, 64, 67, 69, 72, 74, 76, 79, 81]


def intensity(t, scenes):
    """0 — тихо, 1 — энергично. Зависит от сцены."""
    for s in scenes:
        if s["t"][0] <= t < s["t"][1]:
            return {"s1": 0.3, "s2": 0.55, "s3": 1.0, "s4": 0.35}.get(s["id"], 0.5)
    return 0.3


def synth_episode(ep, out_wav, duration=None):
    """Музыка + эффекты -> стерео WAV. Возвращает путь."""
    dur = float(duration or ep["format"]["duration_sec"])
    rng = np.random.default_rng(ep.get("seed", 1))
    bpm = ep.get("music", {}).get("bpm", 100)
    beat = 60.0 / bpm
    bar = 4 * beat
    n = int((dur + 1.0) * SR)
    music = np.zeros(n)
    nbars = int(np.ceil(dur / bar))
    prev = 7
    for b in range(nbars):
        tb = b * bar
        chord, root = CHORDS[b % 4]
        it = intensity(tb, ep["scenes"])
        last = (b == nbars - 1) or tb + bar > dur - 0.01
        if last:
            chord, root = CHORDS[0]
        for note in chord:
            add(music, tb, pad(midi(note - 12), bar if not last else bar + 1.0), 0.07)
        add(music, tb, bass(midi(root)), 0.35)
        add(music, tb + 2 * beat, bass(midi(root + (7 if it > 0.5 else 0))), 0.28)
        if it > 0.5:
            for q in range(4):
                add(music, tb + q * beat, kick(), 0.35 if it > 0.9 else 0.2)
            step = 8 if it > 0.9 else 4
            for q in range(step):
                add(music, tb + q * (bar / step) + (bar / step) / 2 * (step == 4), hat(rng), 0.10)
        rest_p = 0.55 - 0.45 * it
        for e in range(8):
            if rng.random() < rest_p and not last:
                continue
            te = tb + e * beat / 2
            if e % 2 == 0:
                cands = [m + o for m in chord for o in (0, 12)]
                m = min(cands, key=lambda c: abs(c - PENTA[prev]) + rng.random() * 3)
                prev = int(np.argmin([abs(p_ - m) for p_ in PENTA]))
                m = m if m in PENTA else PENTA[prev]
            else:
                prev = int(np.clip(prev + rng.choice([-1, 1]), 0, len(PENTA) - 1))
                m = PENTA[prev]
            add(music, te, marimba(midi(m + 12 * (it > 0.9 and e % 4 == 0))), 0.22)
            if last:
                break
    sfx = np.zeros(n)
    for t0, kind in ep.get("sfx", []):
        if t0 < dur:
            add(sfx, t0, SFX[kind](rng), 0.9)
    mixed = music[: int(dur * SR)] * 0.55 + sfx[: int(dur * SR)] * 0.8
    # плавное начало и затухание
    nn = len(mixed)
    mixed *= np.minimum(1, np.arange(nn) / (0.15 * SR))
    mixed *= np.minimum(1, (nn - np.arange(nn)) / (1.8 * SR))
    peak = np.max(np.abs(mixed)) or 1.0
    mixed = np.tanh(mixed / peak * 1.1) / np.tanh(1.1) * 0.89
    pcm = (mixed * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())
    return out_wav


if __name__ == "__main__":
    import sys
    ep_dir = Path(sys.argv[1])
    ep = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    print(synth_episode(ep, ep_dir / "build" / "audio.wav"))
