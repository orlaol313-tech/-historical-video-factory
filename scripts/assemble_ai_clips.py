#!/usr/bin/env python3
"""Сборка серии из клипов, сделанных в нейросетях.

  python3 scripts/assemble_ai_clips.py episodes/episode_001            # клипы из episodes/episode_001/ai_clips/*.mp4 (по имени)
  python3 scripts/assemble_ai_clips.py episodes/episode_001 --xfade 0.3 --music --target 60

Что делает: приводит все клипы к 1920x1080, 24 к/с (чёрные поля, если пропорции другие),
склеивает по порядку имён, добавляет оригинальную музыку (создаётся кодом), титры и затухание,
затем проверяет итог (длительность, разрешение, звук, целостность) и пишет отчёт в reports/.
Звук самих клипов сохраняется, если он есть. Ничего не публикуется.
"""
import argparse
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bos_audio  # noqa: E402
import clip_tools  # noqa: E402
import verify_video  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H, FPS = 1920, 1080, 24


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]}: {r.stderr.strip()[-600:]}")


def normalize(src, dst, has_audio, max_len, fit="pad"):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    if fit == "crop":      # заполнить кадр, лишнее по краям обрезается
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,format=yuv420p"
    else:                  # чёрные поля, ничего не обрезается
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
              f"fps={FPS},setsar=1,format=yuv420p")
    cmd += ["-vf", vf, "-af", "aresample=44100,aformat=channel_layouts=stereo",
            "-map", "0:v", "-map", "0:a" if has_audio else "1:a", "-shortest"]
    if max_len:
        cmd += ["-t", str(max_len)]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-c:a", "aac", "-b:a", "192k", str(dst)]
    run(cmd)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode_dir")
    ap.add_argument("--clips", help="папка с клипами (по умолчанию <серия>/ai_clips)")
    ap.add_argument("--xfade", type=float, default=0.0, help="плавный переход между клипами, сек (0 = резкая склейка)")
    ap.add_argument("--music", action="store_true", help="добавить оригинальную музыку (создаётся кодом)")
    ap.add_argument("--music-gain", type=float, default=0.35, help="громкость музыки относительно звука клипов")
    ap.add_argument("--target", type=float, help="ожидаемая длительность, сек (по умолчанию 60)")
    ap.add_argument("--max-clip", type=float, help="обрезать каждый клип до N секунд")
    ap.add_argument("--fit", choices=["pad", "crop"], default="pad", help="если пропорции клипа не 16:9: pad — чёрные поля, crop — обрезать края")
    ap.add_argument("--title", action="store_true", help="показать титры из episode.yaml в начале")
    ap.add_argument("--out", help="имя выходного файла в renders/")
    a = ap.parse_args()

    ep_dir = ROOT / a.episode_dir
    ep = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    clips_dir = Path(a.clips) if a.clips else ep_dir / "ai_clips"
    clips = sorted(clips_dir.glob("*.mp4"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "renders" / (a.out or f"{ep['id']}_ai.mp4")
    log = []
    status, error, checks = "FAIL", None, []
    try:
        if not clips:
            raise RuntimeError(f"В папке {clips_dir} нет клипов .mp4. Положите туда clip_01.mp4, clip_02.mp4 …")
        target = a.target or float(ep["format"]["duration_sec"])
        with tempfile.TemporaryDirectory(dir=ep_dir) as tmp:
            tmp = Path(tmp)
            norm, durs = [], []
            for i, c in enumerate(clips):
                info = clip_tools.probe(c)
                log.append(f"{c.name}: {info['duration']:.2f} с, {info['width']}x{info['height']}, {info['fps']:.0f} к/с, "
                           f"звук: {'да' if info['audio'] else 'нет'}")
                n = tmp / f"n{i:02d}.mp4"
                normalize(c, n, info["audio"], a.max_clip, a.fit)
                norm.append(n)
                durs.append(min(info["duration"], a.max_clip) if a.max_clip else info["duration"])
            xf = a.xfade
            joined = tmp / "joined.mp4"
            if len(norm) == 1:
                joined = norm[0]
                total = durs[0]
            else:
                ins = [x for n in norm for x in ("-i", str(n))]
                if xf > 0:
                    v = a_ = ""
                    parts, acc = [], durs[0]
                    pv, pa = "[0:v]", "[0:a]"
                    for k in range(1, len(norm)):
                        off = acc - xf
                        parts.append(f"{pv}[{k}:v]xfade=transition=fade:duration={xf}:offset={off:.3f}[v{k}]")
                        parts.append(f"{pa}[{k}:a]acrossfade=d={xf}[a{k}]")
                        pv, pa = f"[v{k}]", f"[a{k}]"
                        acc += durs[k] - xf
                    fc, mv, ma, total = ";".join(parts), pv, pa, acc
                else:
                    fc = "".join(f"[{k}:v][{k}:a]" for k in range(len(norm))) + f"concat=n={len(norm)}:v=1:a=1[v][a]"
                    mv, ma, total = "[v]", "[a]", sum(durs)
                run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fc, "-map", mv, "-map", ma,
                     "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-c:a", "aac", "-b:a", "192k", str(joined)])
            dur = min(total, target + 0.0) if total > target else total
            log.append(f"Склейка: {total:.2f} с (цель {target:.0f} с)")

            # финальный проход: титры, затухание, музыка
            vf = ["[0:v]null[v0]"]
            cur = "v0"
            if a.title and ep.get("overlays"):
                ov = ep["overlays"][0]
                t0, t1 = ov["t"]
                y = 330
                for ln in ov["lines"]:
                    vf.append(f"[{cur}]drawtext=fontfile={FONT}:text='{ln['text']}':fontsize={ln['size']}:fontcolor=white:"
                              f"x=(w-text_w)/2:y={y}:box=1:boxcolor=0x14183296:boxborderw=30:enable='between(t,{t0},{t1})':"
                              f"alpha='if(lt(t,{t0 + 0.5}),(t-{t0})/0.5,if(lt(t,{t1 - 0.5}),1,({t1}-t)/0.5))'[t{y}]")
                    cur = f"t{y}"
                    y += int(ln["size"] * 1.5)
            fo = max(0.0, dur - 1.5)
            vf.append(f"[{cur}]fade=t=out:st={fo:.2f}:d=1.5:color=black[vout]")
            if a.music:
                spec = {**ep, "format": {**ep["format"], "duration_sec": dur}, "sfx": [],
                        "scenes": [{"id": f"s{i + 1}", "t": [dur * i / 4, dur * (i + 1) / 4]} for i in range(4)]}
                wav = bos_audio.synth_episode(spec, tmp / "music.wav", duration=dur)
                vf.append(f"[0:a]volume=1.0[ca];[1:a]volume={a.music_gain},afade=t=out:st={fo:.2f}:d=1.5[mu];"
                          f"[ca][mu]amix=inputs=2:duration=first:normalize=0,afade=t=out:st={fo:.2f}:d=1.5[aout]")
                ains = ["-i", str(wav)]
            else:
                vf.append(f"[0:a]afade=t=out:st={fo:.2f}:d=1.5[aout]")
                ains = []
            fs = tmp / "post.txt"
            fs.write_text(";\n".join(vf), encoding="utf-8")
            out.parent.mkdir(exist_ok=True)
            run(["ffmpeg", "-y", "-v", "error", "-i", str(joined), *ains, "-filter_complex_script", str(fs),
                 "-map", "[vout]", "-map", "[aout]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "medium",
                 "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
        checks = verify_video.verify(out, duration=target, width=W, height=H, fps=FPS)
        status = "OK" if all(c[1] for c in checks) else "FAIL"
    except Exception as e:  # noqa: BLE001
        error = str(e)
    finally:
        lines = [f"# Отчёт о сборке из клипов нейросетей: {ep['id']}", "", f"- Результат: **{status}**",
                 f"- Дата: {datetime.now():%Y-%m-%d %H:%M:%S}", f"- Файл: `{out.relative_to(ROOT)}`",
                 f"- Параметры: переход {a.xfade} с, музыка {'да' if a.music else 'нет'}", "", "## Клипы", ""]
        lines += [f"- {l}" for l in log] or ["- (нет)"]
        lines += ["", "## Проверки", ""] + ([f'- {"✅" if ok else "❌"} {n}: {d}' for n, ok, d in checks] or ["- (не выполнялись)"])
        if error:
            lines += ["", "## Ошибка", "", "```", error, "```"]
        lines += ["", "## Не проверяется автоматически", "",
                  "- Единство персонажей и качество картинки между клипами — смотрите глазами.",
                  "- Публикация не выполнялась."]
        text = "\n".join(lines) + "\n"
        (ROOT / "reports" / f"report_ai_{ep['id']}_{stamp}.md").write_text(text, encoding="utf-8")
        (ROOT / "reports" / "latest_report.md").write_text(text, encoding="utf-8")
        print(text)
    sys.exit(0 if status == "OK" else 1)


if __name__ == "__main__":
    main()
