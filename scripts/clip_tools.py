#!/usr/bin/env python3
"""Мелкие помощники для работы с клипами нейросетей.

  python3 scripts/clip_tools.py info  clip.mp4              # длительность, разрешение, есть ли звук
  python3 scripts/clip_tools.py lastframe clip_01.mp4 out.png   # последний кадр: стартовая картинка для следующего клипа
"""
import json
import subprocess
import sys


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"не удаётся прочитать {path}: {r.stderr.strip()[:200]}")
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise RuntimeError(f"в {path} нет видео")
    num, den = map(int, v["r_frame_rate"].split("/"))
    return {"duration": float(info["format"]["duration"]), "width": v["width"], "height": v["height"],
            "fps": num / den if den else 0.0, "audio": any(s["codec_type"] == "audio" for s in info["streams"])}


def last_frame(src, out):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.2", "-i", str(src), "-update", "1",
                        "-frames:v", "1", "-q:v", "2", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "info":
        for p in sys.argv[2:]:
            i = probe(p)
            print(f'{p}: {i["duration"]:.2f} с, {i["width"]}x{i["height"]}, {i["fps"]:.1f} к/с, звук: {"да" if i["audio"] else "нет"}')
    elif len(sys.argv) == 4 and sys.argv[1] == "lastframe":
        last_frame(sys.argv[2], sys.argv[3])
        print("Сохранено:", sys.argv[3])
    else:
        print(__doc__)
        sys.exit(2)
