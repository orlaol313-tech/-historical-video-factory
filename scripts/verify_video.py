#!/usr/bin/env python3
"""Проверка готового MP4: длительность, разрешение, кадры/сек, звук, целостность.

Использование:
  python3 scripts/verify_video.py renders/episode_001.mp4 [--duration 60 --width 1920 --height 1080 --fps 24]
Код выхода: 0 — все проверки пройдены, 1 — есть ошибки.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def verify(path, duration=60.0, width=1920, height=1080, fps=24, tol=0.5):
    """Вернуть список проверок: [(название, ок?, подробности)]."""
    checks = []

    def add(name, ok, detail):
        checks.append((name, bool(ok), detail))

    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        add("Файл существует и не пуст", False, f"{path}")
        return checks
    add("Файл существует и не пуст", True, f"{path.stat().st_size / 1e6:.1f} МБ")

    pr = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    if pr.returncode != 0:
        add("ffprobe читает файл", False, pr.stderr.strip()[:300])
        return checks
    info = json.loads(pr.stdout)
    vs = [s for s in info["streams"] if s["codec_type"] == "video"]
    au = [s for s in info["streams"] if s["codec_type"] == "audio"]

    add("Есть видеодорожка", len(vs) == 1, f"найдено: {len(vs)}")
    if vs:
        v = vs[0]
        add("Разрешение", (v["width"], v["height"]) == (width, height), f'{v["width"]}x{v["height"]}, нужно {width}x{height}')
        add("Формат 16:9", abs(v["width"] / v["height"] - 16 / 9) < 0.01, f'{v["width"] / v["height"]:.3f}')
        num, den = map(int, v["r_frame_rate"].split("/"))
        add("Кадров в секунду", abs(num / den - fps) < 0.01, f"{num / den:.2f}, нужно {fps}")
        add("Кодек видео H.264", v["codec_name"] == "h264", v["codec_name"])
        add("Пиксельный формат yuv420p", v.get("pix_fmt") == "yuv420p", str(v.get("pix_fmt")))
    dur = float(info["format"]["duration"])
    add("Длительность", abs(dur - duration) <= tol, f"{dur:.2f} с, нужно {duration} ± {tol}")

    add("Есть аудиодорожка", len(au) == 1, f"найдено: {len(au)}")
    if au:
        a = au[0]
        add("Кодек звука AAC", a["codec_name"] == "aac", a["codec_name"])
        adur = float(a.get("duration", dur))
        add("Звук не короче видео", adur >= duration - tol, f"{adur:.2f} с")
        vd = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"])
        m = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", vd.stderr)
        mx = re.search(r"max_volume: (-?[\d.]+|-inf) dB", vd.stderr)
        if m and mx and "inf" not in m.group(1):
            mean, peak = float(m.group(1)), float(mx.group(1))
            add("Звук не беззвучный", mean > -50, f"средняя громкость {mean} dB")
            add("Звук без перегрузки", peak < -0.1, f"пик {peak} dB")
        else:
            add("Звук не беззвучный", False, "громкость не определена (тишина?)")

    dec = _run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
    add("Файл целиком декодируется без ошибок", dec.returncode == 0 and not dec.stderr.strip(),
        dec.stderr.strip()[:300] or "ошибок нет")
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=float, default=24)
    ap.add_argument("--tol", type=float, default=0.5)
    a = ap.parse_args()
    res = verify(a.video, a.duration, a.width, a.height, a.fps, a.tol)
    for name, ok, detail in res:
        print(f'[{"OK  " if ok else "FAIL"}] {name}: {detail}')
    bad = [r for r in res if not r[1]]
    print("\nИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not bad else f"ОШИБОК: {len(bad)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
