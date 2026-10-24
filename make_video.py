#!/usr/bin/env python3
"""Procedural black-and-white "stoic motivation" edit: engraved art, kinetic type,
glitch cuts and a synthesized dark score. Everything is generated, no external assets.

usage: python3 make_video.py [out.mp4]
"""
import math
import os
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

N = 720            # square frame
FPS = 30
DUR = 20.0
SR = 44100
rng = np.random.default_rng(7)

# ---------------------------------------------------------------- fonts
def font(cands, size):
    for c in cands:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()

NARROW = ["/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Regular.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
          "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
SERIF_B = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
           "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"]
SERIF = ["/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSerif.ttf"]
SERIF_I = ["/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"]

# ---------------------------------------------------------------- helpers
yy, xx = np.mgrid[0:N, 0:N].astype(np.float32)
BAYER = np.array([[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
                  [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
                  [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
                  [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]],
                 np.float32) / 64.0
BAYER_T = np.tile(BAYER, (N // 8, N // 8))


def dither(v, bias=0.0):
    return (v + bias > BAYER_T).astype(np.float32)


def smooth_noise(size, seed):
    r = np.random.default_rng(seed).random((size, size)).astype(np.float32)
    im = Image.fromarray((r * 255).astype(np.uint8)).resize((N, N), Image.BICUBIC)
    return np.asarray(im, np.float32) / 255.0


FBM = [smooth_noise(s, i) for i, s in enumerate((6, 12, 24, 48))]


def fbm(t, speed=1.0):
    out = np.zeros((N, N), np.float32)
    amp, tot = 1.0, 0.0
    for i, layer in enumerate(FBM):
        sh = int(t * speed * (14 + i * 9))
        out += amp * np.roll(np.roll(layer, sh, 1), -sh // 2, 0)
        tot += amp
        amp *= 0.55
    return out / tot


def to_img(a):
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L")


def to_arr(im):
    return np.asarray(im, np.float32) / 255.0


def zoom(a, z, cx=0.5, cy=0.5, rot=0.0):
    im = to_img(a)
    w = N / z
    x0, y0 = cx * N - w / 2, cy * N - w / 2
    im = im.transform((N, N), Image.AFFINE, (w / N, 0, x0, 0, w / N, y0), Image.BICUBIC)
    if rot:
        im = im.rotate(rot, Image.BICUBIC)
    return to_arr(im)


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def text_layer(items):
    """items: list of (text, font, cx, y, alpha[0..1], anchor)"""
    im = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(im)
    for txt, f, x, y, a, anc in items:
        d.text((x, y), txt, font=f, fill=int(255 * a), anchor=anc)
    return to_arr(im)


# ---------------------------------------------------------------- figures
def limb(d, p, q, w, fill):
    d.line([p, q], fill=fill, width=int(w))
    r = w / 2
    for c in (p, q):
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=fill)


def figure(d, cx, by, h, pose, fill=255):
    """Stick-silhouette. pose: dict of angles (deg from straight down) for limbs."""
    w = h * 0.10
    hip = (cx, by - h * 0.48)
    sh = (cx + pose.get("lean", 0) * h, by - h * 0.82)
    L = h * 0.26

    def seg(o, ang, ln):
        a = math.radians(ang)
        return (o[0] + math.sin(a) * ln, o[1] + math.cos(a) * ln)

    limb(d, hip, sh, w * 1.5, fill)
    r = h * 0.075
    hd = (sh[0] + pose.get("lean", 0) * h * 0.3, sh[1] - h * 0.10)
    d.ellipse([hd[0] - r, hd[1] - r, hd[0] + r, hd[1] + r], fill=fill)
    for side in ("a1", "a2"):
        s_ang, e_ang = pose[side]
        e = seg(sh, s_ang, L)
        limb(d, sh, e, w, fill)
        limb(d, e, seg(e, e_ang, L), w * 0.9, fill)
    for side in ("l1", "l2"):
        s_ang, e_ang = pose[side]
        k = seg(hip, s_ang, L * 1.05)
        limb(d, hip, k, w * 1.15, fill)
        limb(d, k, seg(k, e_ang, L * 1.05), w, fill)


def run_pose(ph):
    s = math.sin(ph)
    return {"lean": 0.07,
            "a1": (-40 * s, -40 * s + 80), "a2": (40 * s, 40 * s + 80),
            "l1": (45 * s, 45 * s - 40 - 30 * s),
            "l2": (-45 * s, -45 * s - 40 + 30 * s)}


# ---------------------------------------------------------------- scenes (each returns float image)
def scene_temple(t):
    """Engraved Greek columns, line-shaded, slow push-in."""
    img = np.zeros((N, N), np.float32)
    base = int(N * 0.86)
    cols = 5
    cw = N * 0.115
    gap = (N - cols * cw) / (cols + 1)
    for i in range(cols):
        x0 = gap + i * (cw + gap)
        u = np.clip((xx - x0) / cw, 0, 1)
        inside = (xx >= x0) & (xx <= x0 + cw) & (yy > N * 0.30) & (yy < base)
        lam = 0.15 + 0.85 * np.clip(np.cos((u - 0.32) * 2.4), 0, 1)
        flutes = 0.5 + 0.5 * np.cos(u * math.pi * 14)
        lines = (np.sin(yy * 1.9 + u * 6) * 0.5 + 0.5)
        shade = lam * (0.55 + 0.45 * flutes)
        img = np.where(inside, (lines < shade * 1.25).astype(np.float32) * (0.4 + 0.6 * lam), img)
        cap = (xx >= x0 - cw * .18) & (xx <= x0 + cw * 1.18) & (yy > N * 0.27) & (yy <= N * 0.31)
        img = np.where(cap, 0.85, img)
    arch = (yy > N * 0.19) & (yy <= N * 0.27)
    img = np.where(arch & (np.sin(xx * 0.9) > -0.2), 0.6, img)
    tri = (yy < N * 0.19) & (yy > N * 0.07 + np.abs(xx - N / 2) * 0.16)
    img = np.where(tri, (np.sin(yy * 1.4 + xx * 0.12) > 0.1) * 0.7, img)
    for k in range(5):
        step = (yy > base + k * 11) & (yy <= base + k * 11 + 7)
        img = np.where(step, 0.35 + 0.1 * k, img)
    img *= np.clip(1.15 - ((xx - N / 2) ** 2 + (yy - N * 0.55) ** 2) / (N * 0.62) ** 2, 0, 1)
    return zoom(img, 0.84 + 0.07 * t, 0.5, 0.36)


def bolt(d, x, y, ex, ey, width, rs):
    pts = [(x, y)]
    segs = 16
    for i in range(1, segs + 1):
        k = i / segs
        pts.append((x + (ex - x) * k + rs.normal(0, 22) * (1 - k * 0.3),
                    y + (ey - y) * k + rs.normal(0, 8)))
    d.line(pts, fill=255, width=width, joint="curve")
    for _ in range(3):
        j = rs.integers(3, 12)
        bx, by = pts[j]
        d.line([(bx, by), (bx + rs.normal(0, 70), by + rs.uniform(30, 90))], fill=255, width=max(2, width // 2))


def scene_storm(t):
    n = fbm(t, 1.8)
    sky = dither(np.clip((n - 0.25) * 1.5, 0, 1) * 0.85, 0.0)
    im = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(im)
    rs = np.random.default_rng(int(t * 12))
    flash = 1.0 if (t * 2.5) % 1.0 < 0.3 else 0.0
    if flash:
        bolt(d, N * 0.82, 0, N * 0.50, N * 0.46, 7, rs)
    # figure on a rock, arm raised to the sky
    d.polygon([(0, N), (0, N * 0.82), (N * 0.25, N * 0.74), (N * 0.55, N * 0.78), (N, N * 0.7), (N, N)], fill=40)
    pose = {"lean": 0.02, "a1": (170, 178), "a2": (95, 120),
            "l1": (10, 5), "l2": (-12, -5)}
    figure(d, N * 0.5, N * 0.77, N * 0.38, pose, fill=235)
    fig = to_arr(im)
    glow = to_arr(im.filter(__import__("PIL.ImageFilter", fromlist=["x"]).GaussianBlur(14)))
    out = np.maximum(sky * (0.35 + 0.65 * flash * 0.0 + 0.25), fig)
    out = np.clip(out + glow * 0.9 * (0.5 + flash), 0, 1)
    jitter = 1.0 + 0.012 * math.sin(t * 40)
    return zoom(out, jitter + 0.04 * t, 0.5, 0.5)


def scene_swirl(t):
    """Bright dithered vortex with a dark runner, radial smear."""
    cx = cy = N / 2
    ang = np.arctan2(yy - cy, xx - cx)
    rad = np.hypot(xx - cx, yy - cy) / (N / 2)
    sw = np.sin(ang * 5 + rad * 9 - t * 6) * 0.5 + 0.5
    n = fbm(t * 0.8, 1.2)
    v = np.clip(0.25 + 0.65 * np.clip(1 - rad, 0, 1) ** 0.7 * (0.5 + 0.5 * sw) + 0.3 * (n - 0.5), 0, 1)
    bg = dither(v)
    bg = 0.15 + 0.8 * bg * (0.5 + 0.5 * (1 - rad))
    im = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(im)
    figure(d, N * 0.5, N * 0.80, N * 0.50, run_pose(t * 11), fill=255)
    fig = to_arr(im)
    fig_s = to_arr(im.filter(__import__("PIL.ImageFilter", fromlist=["x"]).GaussianBlur(2)))
    out = bg * (1 - fig_s) + 0.04 * fig_s
    acc = np.zeros_like(out)
    for k in range(5):
        acc += zoom(out, 1.0 + 0.018 * k, rot=0.5 * k * math.sin(t * 3))
    return acc / 5


def scene_card(t):
    f_big = font(SERIF_B, 92)
    f_sm = font(SERIF, 21)
    f_it = font(SERIF_I, 26)
    a = ease(t / 0.35)
    ty = 0.0
    items = [
        ("Discipline", f_big, 66, 300 - ty, a, "ls"),
        ("NOUN /ˈdɪsəplɪn/", font(SERIF, 30), 66, 352, ease((t - 0.2) / 0.3), "ls"),
        ("Doing what must be done, every single day,", f_sm, 66, 400, ease((t - 0.5) / 0.3), "ls"),
        ("long after the mood to do it has gone.", f_sm, 66, 428, ease((t - 0.8) / 0.3), "ls"),
        ("— Marcus, the Stoic way", f_it, 66, 480, ease((t - 1.3) / 0.4) * 0.7, "ls"),
    ]
    out = text_layer(items)
    out[int(314):int(316), 66:int(66 + 590 * ease((t - 0.1) / 0.5))] = 0.6
    return out


def scene_choice(t):
    """White poster card; ends in a smeared dissolve."""
    img = np.full((N, N), 0.93, np.float32)
    im = Image.new("L", (N, N), 255)
    d = ImageDraw.Draw(im)
    f1 = font(SERIF_B, 78)
    d.text((N / 2, 150), "The Choice", font=f1, fill=20, anchor="ms")
    d.text((N / 2, 232), "Is Yours.", font=f1, fill=20, anchor="ms")
    d.rectangle([110, 272, N - 110, 530], outline=30, width=3)
    # defeated (hunched) vs strong
    d.ellipse([200, 400, 238, 438], fill=20)
    d.line([(215, 438), (260, 490)], fill=20, width=26)
    d.line([(245, 480), (330, 505)], fill=20, width=22)
    d.line([(240, 490), (200, 518)], fill=20, width=18)
    pose = {"lean": 0.0, "a1": (90, 180), "a2": (270, 180),
            "l1": (10, 4), "l2": (-10, -4)}
    figure(d, 470, 522, 205, pose, fill=25)
    f2 = font(SERIF, 21)
    d.text((N / 2, 585), "One year from now, you will either be complaining that you never", font=f2, fill=40, anchor="ms")
    d.text((N / 2, 612), "started, or celebrating all that you've done. The choice is yours.", font=f2, fill=40, anchor="ms")
    d.line([(110, 640), (N - 110, 640)], fill=120, width=1)
    base = to_arr(im) * 0.95
    k = ease((t - 2.0) / 0.5)
    if k > 0:
        sm = np.zeros_like(base)
        for i in range(9):
            off = int(i * k * 18)
            sm += np.roll(np.roll(base, off, 1), -off // 2, 0)
        sm /= 9
        nz = fbm(t, 3)
        base = base * (1 - k) + (sm * (0.6 + 0.4 * nz)) * k
    return base


def bust(t, kind):
    """Lit ellipsoid-union statue, dithered, starfield option."""
    X = (xx - N / 2) / (N / 2)
    Y = (yy - N * 0.52) / (N / 2)
    h = np.zeros((N, N), np.float32)

    def ell(cx, cy, rx, ry, rz):
        q = 1 - ((X - cx) / rx) ** 2 - ((Y - cy) / ry) ** 2
        return np.where(q > 0, np.sqrt(np.clip(q, 0, 1)) * rz, 0)

    if kind == 0:   # helmeted warrior bust
        parts = [ell(0, -0.12, 0.30, 0.38, 0.45), ell(0, -0.20, 0.34, 0.26, 0.55),
                 ell(0, 0.72, 0.85, 0.45, 0.40), ell(0, 0.24, 0.16, 0.30, 0.25),
                 ell(0, -0.60, 0.05, 0.22, 0.30), ell(-0.55, 0.45, 0.35, 0.2, 0.4)]
    else:           # laurel head + shoulder, turned
        parts = [ell(0.05, -0.18, 0.27, 0.36, 0.42), ell(-0.05, 0.62, 0.9, 0.5, 0.45),
                 ell(0.08, 0.22, 0.14, 0.25, 0.22), ell(0.04, -0.46, 0.30, 0.10, 0.34)]
    for p in parts:
        h = np.maximum(h, p)
    gy, gx = np.gradient(h)
    a = t * 0.7 + (0.6 if kind else 0.0)
    L = np.array([math.cos(a) * 0.6, -0.5, 0.7])
    nrm = np.stack([-gx * 420, -gy * 420, np.ones_like(h)])
    nrm /= np.linalg.norm(nrm, axis=0)
    lam = np.clip((nrm * L[:, None, None]).sum(0) / np.linalg.norm(L), 0, 1)
    lam = (lam ** 1.7) * 1.25 * (h > 0.01)
    grain = (rng.random((N, N)).astype(np.float32) - 0.5) * 0.18
    body = dither(np.clip(lam + grain, 0, 1)) * 0.85 * (h > 0.01)
    # stars
    star_rs = np.random.default_rng(3 + kind)
    st = np.zeros((N, N), np.float32)
    for _ in range(140):
        sx, sy = star_rs.integers(0, N, 2)
        tw = 0.5 + 0.5 * math.sin(t * 4 + sx)
        st[sy:sy + 2, sx:sx + 2] = 0.9 * tw
    bg = fbm(t * 0.3, 0.5)
    out = np.where(h > 0.01, body, np.clip(st + (bg - 0.45) * 0.25, 0, 1))
    return zoom(out, 1.0 + 0.07 * t, 0.5, 0.45)


def scene_end(t):
    f = font(NARROW, 46)
    a = ease(t / 0.5) * (1 - ease((t - 1.1) / 0.5))
    return text_layer([("KEEP MOVING FORWARD", f, N / 2, N / 2, a, "mm")])


# ---------------------------------------------------------------- timeline
# (start, end, renderer)
CUTS = [0.0, 3.2, 6.0, 8.4, 11.0, 14.0, 16.4, 18.4, DUR]


def kinetic_overlay(t, words, t0, y0, f):
    items = []
    for i, w in enumerate(words):
        a = ease((t - (t0 + i * 0.7)) / 0.18)
        if a > 0:
            items.append((w, f, N / 2, y0 + i * 40, a, "mm"))
    return text_layer(items) if items else 0


def caption(t, s, t0, t1, y=N * 0.89, size=30):
    a = ease((t - t0) / 0.25) * (1 - ease((t - t1) / 0.25))
    if a <= 0:
        return 0
    return text_layer([(s, font(NARROW, size), N / 2, y, a, "mm")])


def render(t):
    seg = next(i for i in range(len(CUTS) - 1) if CUTS[i] <= t < CUTS[i + 1])
    lt = t - CUTS[seg]
    if seg == 0:
        fr = scene_temple(lt)
        fr = np.maximum(fr, kinetic_overlay(lt, ["KEEP", "MOVING", "FORWARD"], 0.5, 45, font(NARROW, 40)))
    elif seg == 1:
        fr = np.maximum(scene_storm(lt), caption(lt, "THEY WILL TEST YOU.", 0.4, 2.6))
    elif seg == 2:
        fr = scene_card(lt)
    elif seg == 3:
        fr = scene_swirl(lt)
        fr = np.maximum(fr, caption(lt, "Forget the old you.", 0.3, 2.4, y=N * 0.12, size=44) * 0.9)
    elif seg == 4:
        fr = scene_choice(lt)
    elif seg == 5:
        fr = np.maximum(bust(lt, 0), caption(lt, "Be the one who stays.", 0.5, 2.2, y=N * 0.09, size=40))
    elif seg == 6:
        fr = np.maximum(bust(lt, 1), caption(lt, "Silence the noise. Do the work.", 0.4, 1.9, y=N * 0.09, size=40))
    else:
        fr = scene_end(lt)
    return fr, seg, lt


def post(fr, t):
    # glitch near cuts
    g = 0.0
    for c in CUTS[1:-1]:
        d = t - c
        if -0.05 < d < 0.22:
            g = max(g, 1 - (d + 0.05) / 0.27)
    if g > 0:
        fr = fr.copy()
        for _ in range(int(4 + 8 * g)):
            y0 = rng.integers(0, N - 20)
            hh = rng.integers(6, 50)
            fr[y0:y0 + hh] = np.roll(fr[y0:y0 + hh], int(rng.normal(0, 60 * g)), 1)
        if g > 0.8:
            fr = 1 - fr
    # grain + vignette
    fr = fr + (rng.random((N, N)).astype(np.float32) - 0.5) * 0.09
    v = 1 - 0.35 * (((xx - N / 2) ** 2 + (yy - N / 2) ** 2) / (N * 0.75) ** 2)
    fr = fr * v
    # fade in / out
    fr = fr * ease(t / 0.3) * (1 - ease((t - (DUR - 0.4)) / 0.4))
    rgb = (np.clip(fr, 0, 1) * 255).astype(np.uint8)
    return np.stack([rgb] * 3, -1)


# ---------------------------------------------------------------- audio
def lowpass(x, fc):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X *= 1 / (1 + (f / fc) ** 4)
    return np.fft.irfft(X, len(x))


def make_audio(path):
    n = int(DUR * SR)
    tt = np.arange(n) / SR
    saw = lambda f: 2 * ((tt * f) % 1) - 1
    drone = (saw(55.0) + saw(55.4) + 0.7 * saw(82.4) + 0.5 * saw(110.3) + 0.4 * saw(77.9))
    drone = lowpass(drone, 380) * (0.55 + 0.45 * np.sin(tt * 0.5 + 1)) * 0.22
    pad = lowpass(rng.normal(0, 1, n), 900) * 0.10 * (0.5 + 0.5 * np.sin(tt * 0.31))
    mix = drone + pad
    # heartbeat
    for b in np.arange(0.5, DUR - 0.5, 0.92):
        for off, amp in ((0.0, 1.0), (0.2, 0.6)):
            i = int((b + off) * SR)
            m = min(int(0.3 * SR), n - i)
            k = np.arange(m) / SR
            mix[i:i + m] += amp * 0.32 * np.sin(2 * math.pi * (95 * k - 150 * k * k)) * np.exp(-k * 14)
    # riser + boom at each cut
    for c in CUTS[1:-1]:
        r = int(0.9 * SR)
        i = int((c - 0.9) * SR)
        env = (np.arange(r) / r) ** 2.2
        mix[i:i + r] += lowpass(rng.normal(0, 1, r), 3500) * env * 0.12
        m = int(1.4 * SR)
        j = int(c * SR)
        m = min(m, n - j)
        k = np.arange(m) / SR
        mix[j:j + m] += 0.7 * np.sin(2 * math.pi * (38 * k + 60 * (1 - np.exp(-k * 6)) / 6 * 6)) * np.exp(-k * 3.2)
        mix[j:j + int(0.12 * SR)] += rng.normal(0, 1, int(0.12 * SR)) * np.exp(-np.arange(int(0.12 * SR)) / (0.02 * SR)) * 0.25
    # lightning crack
    for c in (3.2, 3.2 + 0.0):
        pass
    # text ticks
    for tk in (0.5, 1.2, 1.9, 6.2, 6.8, 7.3):
        i = int(tk * SR)
        m = int(0.05 * SR)
        mix[i:i + m] += rng.normal(0, 1, m) * np.exp(-np.arange(m) / (0.008 * SR)) * 0.12
    mix *= np.minimum(1, tt / 0.3) * np.minimum(1, (DUR - tt) / 1.2)
    mix = np.tanh(mix * 1.6) * 0.8
    pcm = (mix * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- main
def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "out/video.mp4"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    wav = out + ".wav"
    make_audio(wav)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{N}x{N}",
           "-r", str(FPS), "-i", "-", "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", "27", "-c:a", "aac", "-b:a", "160k", "-shortest", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(int(DUR * FPS)):
        t = f / FPS
        fr, _, _ = render(t)
        p.stdin.write(post(fr, t).tobytes())
    p.stdin.close()
    p.wait()
    os.remove(wav)
    print("wrote", out)


if __name__ == "__main__":
    main()
