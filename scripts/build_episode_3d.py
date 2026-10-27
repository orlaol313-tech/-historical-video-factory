#!/usr/bin/env python3
"""3D-сборка серии в Blender (Cycles на процессоре, бесплатно).

  python3 scripts/build_episode_3d.py episodes/episode_001 --only 150,500 --res 960x540   # пробные кадры
  python3 scripts/build_episode_3d.py episodes/episode_001 --res 1280x720 --samples 24 --twos   # вся серия

Можно прерывать и запускать снова: готовые кадры пропускаются.
Blender ищется в BLENDER_BIN или в /home/user/tools/blender/*/blender.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from PIL import ImageFilter, Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bos_audio  # noqa: E402
import verify_video  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def find_blender():
    cands = [os.environ.get("BLENDER_BIN", "")] + sorted(glob.glob("/home/user/tools/blender/*/blender"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise SystemExit("Blender не найден (BLENDER_BIN или /home/user/tools/blender/*/blender)")


def prepare(ep_dir, ep, b3):
    """scene.json и размытые фоны (глубина резкости: фон «не в фокусе»)."""
    import bos_render
    bgs = bos_render.ensure_backgrounds([s["bg"] for s in ep["scenes"]])
    blur = b3 / "bg_blur"
    blur.mkdir(parents=True, exist_ok=True)
    for name, im in bgs.items():
        im.filter(ImageFilter.GaussianBlur(5)).save(blur / f"{name}.png")
    chars = {c: yaml.safe_load((ROOT / "characters" / c / "character.yaml").read_text(encoding="utf-8"))
             for c in ep["characters"]}
    (b3 / "scene.json").write_text(json.dumps(
        {"episode": ep, "characters": chars, "scripts_dir": str(ROOT / "scripts"), "blur_dir": str(blur)},
        ensure_ascii=False), encoding="utf-8")


def post_filter(ep, W, H, fade):
    f = ep["format"]
    t0, t1 = ep["overlays"][0]["t"]
    lines = ep["overlays"][0]["lines"]
    parts = [f"scale={f['width']}:{f['height']}:flags=lanczos"]
    y = 330
    for ln in lines:
        parts.append(
            f"drawtext=fontfile={FONT}:text='{ln['text']}':fontsize={ln['size']}:fontcolor=white:"
            f"x=(w-text_w)/2:y={y}:box=1:boxcolor=0x14183296:boxborderw=30:enable='between(t,{t0},{t1})':"
            f"alpha='if(lt(t,{t0 + 0.5}),(t-{t0})/0.5,if(lt(t,{t1 - 0.5}),1,({t1}-t)/0.5))'")
        y += int(ln["size"] * 1.5)
    parts.append(f"fade=t=out:st={fade[0]}:d={fade[1] - fade[0]}:color=black")
    ec = ep["end_card"][0]
    parts.append(
        f"drawtext=fontfile={FONT}:text='{ec['text']}':fontsize={ec['size']}:fontcolor=white:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:enable='gte(t,{fade[0] + 1.2})':alpha='min(1,(t-{fade[0] + 1.2})/0.8)'")
    return ",".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode_dir")
    ap.add_argument("--res", default="1280x720")
    ap.add_argument("--samples", type=int, default=24)
    ap.add_argument("--only", help="номера кадров через запятую (пробный рендер, без сборки видео)")
    ap.add_argument("--twos", action="store_true", help="рендерить каждый 2-й кадр (12 к/с), остальные дублировать")
    ap.add_argument("--no-assemble", action="store_true")
    a = ap.parse_args()
    ep_dir = ROOT / a.episode_dir
    ep = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    W, H = map(int, a.res.split("x"))
    b3 = ep_dir / "build3d"
    prepare(ep_dir, ep, b3)
    # кадры привязаны к отпечатку сценария и кода: изменили исходники — рендер начнётся заново
    h = hashlib.sha256()
    for f_ in (b3 / "scene.json", ROOT / "scripts/blender/bos_blender.py", ROOT / "scripts/bos_anim.py"):
        h.update(f_.read_bytes())
    frames = b3 / f"frames_{W}x{H}_s{a.samples}_{h.hexdigest()[:8]}"
    frames.mkdir(parents=True, exist_ok=True)
    fps, dur = ep["format"]["fps"], ep["format"]["duration_sec"]
    n = int(dur * fps)
    env = dict(os.environ)
    if a.only:
        env["BOS_FRAMES"] = a.only
    elif a.twos:
        env["BOS_FRAMES"] = ",".join(str(i) for i in range(0, n, 2))
    cmd = [find_blender(), "-b", "-P", str(ROOT / "scripts/blender/bos_blender.py"), "--",
           str(b3 / "scene.json"), str(frames), str(W), str(H), str(a.samples), "0", str(n)]
    t0 = time.time()
    log = open(ROOT / "reports" / f"blender_log_{datetime.now():%Y%m%d_%H%M%S}.txt", "w")
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    failed = False
    for line in p.stdout:
        log.write(line)
        if "Traceback" in line or line.startswith("Error:"):
            failed = True
        if line.startswith("BOS_") or failed:
            print(line.rstrip(), flush=True)
    if p.wait() != 0 or failed:
        raise SystemExit("Blender завершился с ошибкой, см. reports/blender_log_*.txt")
    print(f"Рендер: {time.time() - t0:.0f} с")
    if a.only or a.no_assemble:
        return
    # сборка: недостающий кадр заменяется предыдущим (дубли — в отдельной папке, рендер не портится)
    seq = b3 / "assembled"
    shutil.rmtree(seq, ignore_errors=True)
    seq.mkdir()
    last = None
    for i in range(n):
        src = frames / f"frame_{i:05d}.png"
        if src.exists():
            last = src
        if last is None:
            raise SystemExit(f"Нет первого кадра {i}")
        os.link(last, seq / f"frame_{i:05d}.png")
    real = sum(1 for i in range(n) if (frames / f"frame_{i:05d}.png").exists())
    print(f"Кадров нарисовано: {real} из {n} (остальные — повтор предыдущего)")
    wav = bos_audio.synth_episode(ep, b3 / "audio.wav")
    fade = ep["fade_out"]
    out = ROOT / "renders" / f"{ep['id']}_3d.mp4"
    flt = post_filter(ep, W, H, fade)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps), "-i", str(seq / "frame_%05d.png"),
                        "-i", str(wav), "-vf", flt, "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", str(dur),
                        "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg: " + r.stderr)
    f = ep["format"]
    res = verify_video.verify(out, duration=dur, width=f["width"], height=f["height"], fps=fps)
    for name, ok, detail in res:
        print(f'[{"OK" if ok else "FAIL"}] {name}: {detail}')
    sys.exit(0 if all(c[1] for c in res) else 1)


if __name__ == "__main__":
    main()
