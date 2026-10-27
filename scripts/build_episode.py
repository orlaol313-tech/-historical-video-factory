#!/usr/bin/env python3
"""Сборка серии одной командой.

  python3 scripts/build_episode.py episodes/episode_001            # полная серия
  python3 scripts/build_episode.py episodes/episode_001 --preview 6  # быстрый тест, 6 секунд

Шаги: фоны -> музыка и эффекты -> кадры -> MP4 -> проверка -> отчёт.
Лог:   reports/build_log_<серия>_<время>.txt
Отчёт: reports/report_<серия>_<время>.md (и reports/latest_report.md)
Ничего не публикуется: на выходе только файл в renders/.
"""
import argparse
import hashlib
import multiprocessing as mp
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bos_audio  # noqa: E402
import bos_render  # noqa: E402
import verify_video  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_R = None


def _frame_bytes(i):
    return _R.frame(i).tobytes()


def sha(paths):
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(Path(p).read_bytes())
    return h.hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode_dir")
    ap.add_argument("--preview", type=float, default=None, help="собрать только первые N секунд")
    ap.add_argument("--workers", type=int, default=mp.cpu_count())
    a = ap.parse_args()

    ep_dir = (ROOT / a.episode_dir) if not Path(a.episode_dir).is_absolute() else Path(a.episode_dir)
    ep = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    eid = ep["id"] + ("_preview" if a.preview else "")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "renders").mkdir(exist_ok=True)
    build = ep_dir / "build"
    build.mkdir(exist_ok=True)
    log_path = ROOT / "reports" / f"build_log_{eid}_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def log(msg):
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    out_mp4 = ROOT / "renders" / f"{eid}.mp4"
    dur = float(a.preview or ep["format"]["duration_sec"])
    t_start = time.time()
    status, error, checks = "FAIL", None, []
    try:
        log(f"Сборка {eid}: {dur:.0f} с, {ep['format']['width']}x{ep['format']['height']}, {ep['format']['fps']} к/с")
        global _R
        t0 = time.time()
        _R = bos_render.Renderer(ep_dir, preview=a.preview)
        log(f"1/4 Персонажи и фоны готовы ({time.time() - t0:.1f} с)")

        t0 = time.time()
        wav = bos_audio.synth_episode(ep, build / "audio.wav", duration=dur)
        log(f"2/4 Музыка и эффекты синтезированы ({time.time() - t0:.1f} с): {wav.name}")

        t0 = time.time()
        silent = build / "video_silent.mp4"
        f = ep["format"]
        enc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{f['width']}x{f['height']}", "-r", str(f["fps"]), "-i", "-",
             "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", str(silent)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        n = _R.n_frames
        ctx = mp.get_context("fork")
        with ctx.Pool(a.workers) as pool:
            for k, data in enumerate(pool.imap(_frame_bytes, range(n), chunksize=6)):
                enc.stdin.write(data)
                if k % 240 == 0:
                    log(f"    кадры: {k}/{n}")
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace")
        if enc.wait() != 0:
            raise RuntimeError("ffmpeg (видео) завершился с ошибкой:\n" + err)
        log(f"3/4 Кадры отрисованы и закодированы ({time.time() - t0:.1f} с)")

        mux = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(wav), "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", str(dur), "-movflags", "+faststart", str(out_mp4)],
            capture_output=True, text=True)
        if mux.returncode != 0:
            raise RuntimeError("ffmpeg (склейка звука) завершился с ошибкой:\n" + mux.stderr)
        log(f"4/4 Звук добавлен: {out_mp4.relative_to(ROOT)}")

        checks = verify_video.verify(out_mp4, duration=dur, width=f["width"], height=f["height"], fps=f["fps"])
        for name, ok, detail in checks:
            log(f'    [{"OK" if ok else "FAIL"}] {name}: {detail}')
        status = "OK" if all(c[1] for c in checks) else "FAIL"
    except Exception:
        error = traceback.format_exc()
        log("ОШИБКА:\n" + error)
    finally:
        sources = [ep_dir / "episode.yaml"] + [ROOT / "characters" / c / "character.yaml" for c in ep["characters"]]
        sources += [ROOT / "scripts" / n for n in ("bos_render.py", "bos_audio.py", "build_episode.py")]
        ff = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
        lines = [f"# Отчёт о сборке: {eid}", "",
                 f"- Результат: **{status}**",
                 f"- Дата: {datetime.now():%Y-%m-%d %H:%M:%S}",
                 f"- Файл: `{out_mp4.relative_to(ROOT)}`" + ("" if out_mp4.exists() else " (не создан)"),
                 f"- Время сборки: {time.time() - t_start:.0f} с",
                 f"- Отпечаток исходников (sha256, 12 симв.): `{sha(sources)}` — одинаковые исходники дают одинаковое видео",
                 f"- Инструменты: {ff}; Python {sys.version.split()[0]}",
                 f"- Лог: `{log_path.relative_to(ROOT)}`", "", "## Проверки", ""]
        lines += [f'- {"✅" if ok else "❌"} {n}: {d}' for n, ok, d in checks] or ["- (проверки не выполнялись)"]
        if error:
            lines += ["", "## Ошибка", "", "```", error.strip(), "```"]
        lines += ["", "## Что не проверяется автоматически", "",
                  "- Как выглядит и звучит мультфильм — это нужно посмотреть и послушать самому.",
                  "- Публикация не выполнялась и не предусмотрена."]
        text = "\n".join(lines) + "\n"
        rp = ROOT / "reports" / f"report_{eid}_{stamp}.md"
        rp.write_text(text, encoding="utf-8")
        (ROOT / "reports" / "latest_report.md").write_text(text, encoding="utf-8")
        log(f"Отчёт: {rp.relative_to(ROOT)}  Итог: {status}")
        logf.close()
    sys.exit(0 if status == "OK" else 1)


if __name__ == "__main__":
    main()
