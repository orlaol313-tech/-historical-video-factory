"""BonnieAndOllieStudio: движок 2D-«кукольной» анимации (только Pillow + numpy).

Персонаж собирается из частей (тело, голова, руки, ноги). Каждый кадр части
поворачиваются по формулам анимации и накладываются друг на друга.
"""
import math
import random
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont, ImageOps

from bos_anim import POSE0, Timeline, lerp, mix_pose, pose, smooth  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
BG_DIR = ROOT / "assets" / "backgrounds"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

SS = 3                     # суперсэмплинг спрайтов (сглаживание краёв)
INK = (43, 43, 43, 255)    # цвет контура
CW, CH, CX, GY = 1000, 1250, 500, 1000   # холст персонажа и точка «земли» на нём


def hexrgb(h, a=255):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def tint(c, f):
    return tuple(int(v + (255 - v) * f) for v in c[:3]) + (255,)


def darker(c, f):
    return tuple(int(v * (1 - f)) for v in c[:3]) + (255,)


# --------------------------------------------------------------------- рисование
class Pen:
    """Рисует с увеличением в s раз; потом картинка уменьшается (гладкие края)."""

    def __init__(self, img, s):
        self.d = ImageDraw.Draw(img)
        self.s = s

    def _b(self, box):
        return [v * self.s for v in box]

    def ellipse(self, box, fill, ink=True, w=5):
        self.d.ellipse(self._b(box), fill=fill, outline=INK if ink else None,
                       width=w * self.s if ink else 0)

    def rrect(self, box, r, fill, ink=True, w=5):
        self.d.rounded_rectangle(self._b(box), radius=r * self.s, fill=fill,
                                 outline=INK if ink else None, width=w * self.s if ink else 0)

    def chord(self, box, a0, a1, fill, w=4):
        self.d.chord(self._b(box), a0, a1, fill=fill, outline=INK, width=w * self.s)

    def arc(self, box, a0, a1, w=5):
        self.d.arc(self._b(box), a0, a1, fill=INK, width=w * self.s)

    def poly(self, pts, fill, ink=True, w=5):
        pts = [(x * self.s, y * self.s) for x, y in pts]
        self.d.polygon(pts, fill=fill)
        if ink:
            self.d.line(pts + [pts[0]], fill=INK, width=w * self.s, joint="curve")

    def line(self, pts, w=5, color=INK):
        self.d.line([(x * self.s, y * self.s) for x, y in pts], fill=color,
                    width=w * self.s, joint="curve")

    def rect(self, box, fill):
        self.d.rectangle(self._b(box), fill=fill)



def _shade(img):
    """Псевдо-объём: свет слева сверху, мягкая тень справа снизу (только на закрашенных пикселях)."""
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    lum = 1.07 - 0.20 * (0.6 * xx / max(w - 1, 1) + 0.4 * yy / max(h - 1, 1))
    a[:, :, :3] = np.clip(a[:, :, :3] * lum[:, :, None], 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGBA")

class Sprite:
    """Часть куклы. Картинка дополнена так, что точка вращения лежит в центре."""

    def __init__(self, w, h, pivot, k, draw, shade=True, sx=1.0):
        big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        draw(Pen(big, SS))
        nw, nh = max(1, round(w * k * sx)), max(1, round(h * k))
        small = big.resize((nw, nh), Image.LANCZOS)
        if shade:
            small = _shade(small)
        px, py = pivot[0] * k * sx, pivot[1] * k
        R = math.ceil(math.hypot(max(px, nw - px), max(py, nh - py))) + 3
        self.R = R
        self.img = Image.new("RGBA", (2 * R, 2 * R), (0, 0, 0, 0))
        self.img.paste(small, (round(R - px), round(R - py)))

    def put(self, canvas, xy, angle):
        im = self.img.rotate(angle, resample=Image.BICUBIC) if abs(angle) > 0.05 else self.img
        canvas.alpha_composite(im, (round(xy[0] - self.R), round(xy[1] - self.R)))


def rot(v, deg):
    """Повернуть вектор против часовой стрелки (в координатах картинки)."""
    a = math.radians(deg)
    return (v[0] * math.cos(a) + v[1] * math.sin(a), -v[0] * math.sin(a) + v[1] * math.cos(a))


# ----------------------------------------------------------------------- персонаж
class Character:
    """Эталон персонажа. Строится из characters/<id>/character.yaml."""

    def __init__(self, cdef):
        d = cdef["design"]
        self.id = cdef["id"]
        self.k = d["height_px"] / 420.0
        fur = hexrgb(d["body_color"])
        accent = hexrgb(d["accent_color"])
        belly = hexrgb(d.get("belly_color", "#F3E6D3"))
        cloth = hexrgb(d.get("outfit_color", "#3F66A8"))
        eye = hexrgb(d.get("eye_color", "#5A3A1A"))
        nose = hexrgb(d.get("nose_color", "#3B2418"))
        outfit = d.get("outfit", "none")
        ears = d.get("ears", "round")
        lashes = d.get("lashes", False)
        tuft = d.get("tuft", False)
        k = self.k
        WHITE = (255, 255, 255, 255)
        YELLOW = (240, 200, 60, 255)

        def draw_body(p):
            p.ellipse((10, 5, 160, 195), fur)
            p.ellipse((38, 62, 132, 175), belly, ink=False)
            if outfit == "overalls":
                p.rrect((18, 126, 152, 199), 16, cloth)
                p.rrect((14, 184, 156, 202), 8, tint(cloth, 0.18))
                p.rrect((46, 62, 124, 140), 12, cloth)
                p.line([(52, 66), (40, 12)], w=9, color=cloth)
                p.line([(118, 66), (130, 12)], w=9, color=cloth)
                p.ellipse((51, 72, 67, 88), YELLOW, w=3)
                p.ellipse((103, 72, 119, 88), YELLOW, w=3)
                p.rrect((66, 104, 104, 128), 6, darker(cloth, 0.12), w=3)
            elif outfit == "hood":
                p.ellipse((22, 0, 148, 66), cloth)
                p.line([(66, 52), (64, 106)], w=4, color=(200, 200, 205, 255))
                p.line([(104, 52), (106, 100)], w=4, color=(200, 200, 205, 255))
                p.ellipse((60, 102, 70, 114), (200, 200, 205, 255), w=3)
                p.ellipse((101, 96, 111, 108), (200, 200, 205, 255), w=3)
        self.sx = float(d.get("chubby", 1.0))
        self.body = Sprite(170, 204, (85, 185), k, draw_body, sx=self.sx)

        def make_head(face):
            def draw(p):
                if outfit == "hood":
                    p.ellipse((12, 134, 208, 334), cloth)
                if ears == "long":
                    p.ellipse((48, 0, 98, 172), fur)
                    p.ellipse((122, 0, 172, 172), fur)
                    p.ellipse((60, 24, 88, 142), accent, ink=False)
                    p.ellipse((134, 24, 162, 142), accent, ink=False)
                else:
                    p.ellipse((12, 118, 88, 196), fur)
                    p.ellipse((132, 118, 208, 196), fur)
                    p.ellipse((30, 136, 70, 178), accent, ink=False)
                    p.ellipse((150, 136, 190, 178), accent, ink=False)
                if tuft:
                    p.poly([(96, 150), (104, 108), (110, 146), (118, 104), (124, 150)], tint(fur, 0.25), w=3)
                hh = (40, 160, 180, 312) if outfit == "hood" else (25, 150, 195, 320)
                p.ellipse(hh, fur)
                p.ellipse((70, 232, 150, 300), belly, ink=False)         # мордочка
                p.ellipse((38, 246, 74, 274), (245, 150, 150, 255), ink=False)
                p.ellipse((146, 246, 182, 274), (245, 150, 150, 255), ink=False)
                if face in ("normal", "laugh"):
                    pass
                if face == "laugh":
                    for ex in (60, 116):
                        p.arc((ex, 208, ex + 46, 256), 200, 340, w=6)
                    p.chord((82, 252, 138, 308), 0, 180, (150, 40, 60, 255))
                    p.ellipse((96, 284, 124, 302), (240, 120, 130, 255), ink=False)
                    p.ellipse((98, 240, 122, 258), nose, w=3)
                else:
                    big_ = 6 if face == "oh" else 0
                    for ex in (58, 114):
                        p.ellipse((ex - big_, 196 - big_, ex + 48 + big_, 262 + big_), WHITE, w=3)
                        p.ellipse((ex + 6, 206, ex + 42, 252 + big_), eye, ink=False)
                        p.ellipse((ex + 14, 214, ex + 36, 246 + big_), INK, ink=False)
                        p.ellipse((ex + 20, 218, ex + 30, 230), WHITE, ink=False)
                        p.ellipse((ex + 32, 238, ex + 37, 243), WHITE, ink=False)
                        if lashes:
                            p.line([(ex + 2, 206), (ex - 8, 196)], w=3)
                            p.line([(ex + 14, 198), (ex + 10, 186)], w=3)
                    p.ellipse((98, 240, 122, 258), nose, w=3)
                    if face == "oh":
                        p.ellipse((98, 268, 122, 298), (150, 40, 60, 255), w=4)
                    else:
                        p.arc((82, 252, 138, 292), 25, 155, w=5)
                for ex in (56, 118):          # брови
                    p.arc((ex + 2, 168, ex + 46, 196), 205, 335, w=3)
            return Sprite(220, 340, (110, 300), k, draw)
        self.heads = {f: make_head(f) for f in ("normal", "laugh", "oh")}

        def draw_arm(p):
            p.rrect((2, 2, 34, 118), 16, fur)
            p.ellipse((0, 98, 36, 136), tint(fur, 0.2))
        self.arm = Sprite(36, 140, (18, 20), k, draw_arm)

        def draw_leg(p):
            p.rrect((14, 0, 58, 100), 12, fur)
            p.ellipse((6, 90, 72, 128), tint(fur, 0.25))
            p.ellipse((14, 104, 28, 118), accent, ink=False)
            p.ellipse((34, 106, 48, 120), accent, ink=False)
        self.leg = Sprite(76, 132, (38, 15), k, draw_leg)

    def render(self, P):
        """Собрать позу P (словарь углов) на холст CW x CH."""
        k = self.k
        cv = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        gy = GY + P["dy"]
        hip = gy - 113 * k
        self.leg.put(cv, (CX - 30 * k * self.sx, hip), P["leg_l"])
        self.leg.put(cv, (CX + 30 * k * self.sx, hip), P["leg_r"])
        bp = (CX, hip + 8 * k)
        br = P["body_rot"]
        self.body.put(cv, bp, br)
        neck = rot((0, -170 * k), br)
        self.heads[P["face"]].put(cv, (bp[0] + neck[0], bp[1] + neck[1]), br + P["head_rot"])
        for side, key in ((-64, "arm_l"), (64, "arm_r")):
            sh = rot((side * k * self.sx, -140 * k), br)
            self.arm.put(cv, (bp[0] + sh[0], bp[1] + sh[1]), br + P[key])
        if abs(P["char_rot"]) > 0.05:
            cv = cv.rotate(P["char_rot"], resample=Image.BICUBIC, center=(CX, GY))
        return cv


# ------------------------------------------------------------------------- фоны
def vgrad(w, h, stops):
    """Вертикальный градиент. stops: [(позиция 0..1, (r,g,b)), ...]."""
    ys = np.linspace(0, 1, h)
    pos = [s[0] for s in stops]
    arr = np.zeros((h, w, 3), np.uint8)
    for c in range(3):
        col = np.interp(ys, pos, [s[1][c] for s in stops])
        arr[:, :, c] = col[:, None]
    return Image.fromarray(arr, "RGB")


def _cloud(p, x, y, sc, color):
    for dx, dy, r in ((0, 0, 60), (55, -22, 70), (115, 0, 60), (60, 18, 62)):
        p.ellipse((x + (dx - r) * sc, y + (dy - r * 0.6) * sc,
                   x + (dx + r) * sc, y + (dy + r * 0.6) * sc), color, ink=False)


def _tree(p, x, base, h, trunk, leaf):
    p.rrect((x - h * 0.06, base - h * 0.55, x + h * 0.06, base), 10, trunk, ink=False)
    for dx, dy, r in ((0, -0.7, 0.28), (-0.2, -0.55, 0.22), (0.2, -0.55, 0.22), (0, -0.5, 0.26)):
        p.ellipse((x + (dx - r) * h, base + (dy - r) * h, x + (dx + r) * h, base + (dy + r) * h),
                  leaf, ink=False)


def bg_yard(f=2):
    W_, H_ = 1920 * f, 1080 * f
    img = vgrad(W_, H_, [(0, (120, 200, 250)), (0.62, (215, 240, 255)), (0.62, (110, 190, 80)), (1, (66, 140, 58))])
    p = Pen(img, f)
    rng = random.Random(11)
    p.ellipse((1560, 70, 1740, 250), (255, 235, 120, 255), ink=False)
    for cx, cy, sc in ((300, 150, 1.0), (900, 230, 1.3), (1450, 330, 0.9)):
        _cloud(p, cx, cy, sc, (255, 255, 255, 255))
    p.ellipse((-300, 520, 900, 900), (150, 205, 125, 255), ink=False)
    p.ellipse((650, 560, 2150, 980), (125, 190, 108, 255), ink=False)
    img2 = vgrad(W_, H_ // 3 + 2, [(0, (108, 188, 78)), (1, (62, 136, 56))])
    img.paste(img2, (0, int(740 * f)))
    p = Pen(img, f)
    # дом
    p.rect((90, 430, 560, 780), (250, 232, 196, 255))
    p.poly([(55, 436), (325, 250), (595, 436)], (190, 80, 70, 255))
    p.rrect((270, 600, 370, 780), 8, (140, 90, 60, 255), w=4)
    for wx in (140, 430):
        p.rrect((wx, 500, wx + 100, 600), 6, (170, 220, 245, 255), w=4)
        p.line([(wx + 50, 500), (wx + 50, 600)], w=4)
        p.line([(wx, 550), (wx + 100, 550)], w=4)
    # дерево справа
    _tree(p, 1760, 800, 560, (130, 90, 55, 255), (70, 160, 70, 255))
    # забор
    for x in range(640, 1700, 74):
        p.rrect((x, 700, x + 42, 835), 8, (255, 255, 255, 255), w=3)
    p.rect((620, 735, 1700, 755), (240, 240, 240, 255))
    p.rect((620, 790, 1700, 810), (240, 240, 240, 255))
    # дорожка и цветы
    p.poly([(0, 960), (1920, 930), (1920, 1010), (0, 1060)], (226, 205, 150, 255), ink=False)
    for _ in range(90):
        x, y = rng.randint(0, 1900), rng.randint(790, 1070)
        col = rng.choice([(255, 90, 120), (255, 220, 70), (255, 255, 255), (170, 120, 240)])
        r = rng.randint(5, 9)
        p.ellipse((x - r, y - r, x + r, y + r), col + (255,), ink=False)
    return img.resize((1920, 1080), Image.LANCZOS)


def bg_park(f=2):
    W_, H_ = 1920 * f, 1080 * f
    img = vgrad(W_, H_, [(0, (150, 210, 250)), (0.6, (235, 245, 255)), (0.6, (96, 176, 84)), (1, (58, 126, 60))])
    p = Pen(img, f)
    rng = random.Random(23)
    for cx, cy, sc in ((250, 130, 1.1), (1050, 190, 0.9), (1600, 100, 1.2)):
        _cloud(p, cx, cy, sc, (255, 255, 255, 255))
    p.ellipse((-200, 480, 1100, 900), (128, 190, 112, 255), ink=False)
    p.ellipse((800, 520, 2300, 930), (108, 172, 98, 255), ink=False)
    img2 = vgrad(W_, H_ // 3 + 2, [(0, (92, 172, 80)), (1, (58, 126, 60))])
    img.paste(img2, (0, int(720 * f)))
    p = Pen(img, f)
    # пруд
    p.ellipse((520, 760, 1180, 880), (120, 190, 235, 255), w=4)
    p.ellipse((600, 790, 780, 830), (190, 230, 255, 255), ink=False)
    # деревья
    for x, h in ((140, 640), (470, 520), (1450, 600), (1810, 700)):
        _tree(p, x, 770, h, (126, 88, 56, 255), (58, 150, 66, 255))
    # скамейка
    p.rrect((1050, 680, 1330, 710), 6, (150, 100, 60, 255), w=4)
    p.rrect((1050, 725, 1330, 755), 6, (150, 100, 60, 255), w=4)
    for lx in (1070, 1300):
        p.rect((lx, 750, lx + 16, 810), (80, 55, 40, 255))
    # дорожка
    p.poly([(0, 950), (1920, 920), (1920, 1020), (0, 1070)], (230, 212, 160, 255), ink=False)
    for _ in range(120):
        x, y = rng.randint(0, 1900), rng.randint(780, 1070)
        col = rng.choice([(255, 90, 120), (255, 220, 70), (255, 255, 255)])
        r = rng.randint(4, 8)
        p.ellipse((x - r, y - r, x + r, y + r), col + (255,), ink=False)
    return img.resize((1920, 1080), Image.LANCZOS)


def bg_sky(f=2):
    W_, H_ = 1920 * f, 1080 * f
    img = vgrad(W_, H_, [(0, (36, 48, 112)), (0.35, (110, 90, 160)), (0.6, (245, 150, 120)), (0.74, (255, 205, 130)), (0.74, (52, 110, 92)), (1, (34, 78, 70))])
    p = Pen(img, f)
    rng = random.Random(5)
    for _ in range(70):
        x, y, r = rng.randint(0, 1920), rng.randint(0, 420), rng.choice([2, 2, 3, 4])
        p.ellipse((x - r, y - r, x + r, y + r), (255, 250, 220, 255), ink=False)
    p.ellipse((420, 620, 780, 980), (255, 240, 170, 255), ink=False)
    for cx, cy, sc in ((300, 380, 1.4), (1100, 300, 1.0), (1600, 480, 1.2)):
        _cloud(p, cx, cy, sc, (255, 190, 170, 255))
    p.poly([(0, 800), (260, 600), (560, 790), (900, 560), (1300, 800), (1600, 640), (1920, 800), (1920, 840), (0, 840)],
           (84, 80, 130, 255), ink=False)
    p.ellipse((-400, 830, 2320, 1700), (52, 112, 94, 255), ink=False)
    for _ in range(110):
        x, y = rng.randint(0, 1900), rng.randint(880, 1070)
        p.line([(x, y), (x - 6, y - 22)], w=3, color=(90, 160, 120, 255))
        p.line([(x, y), (x + 6, y - 20)], w=3, color=(90, 160, 120, 255))
    return img.resize((1920, 1080), Image.LANCZOS)


BG_MAKERS = {"yard": bg_yard, "park": bg_park, "sky": bg_sky}


def ensure_backgrounds(names):
    BG_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for n in set(names):
        path = BG_DIR / f"{n}.png"
        if not path.exists():
            BG_MAKERS[n]().save(path)
        out[n] = Image.open(path).convert("RGB")
    return out


# ---------------------------------------------------------------------- рендерер
def make_balloon(color):
    def draw(p):
        p.ellipse((10, 8, 110, 138), color)
        p.ellipse((30, 24, 52, 58), tint(color, 0.55), ink=False)
        p.poly([(52, 138), (68, 138), (60, 154)], darker(color, 0.15), w=4)
    return Sprite(120, 160, (60, 73), 1.0, draw)


class Renderer:
    def __init__(self, ep_dir, preview=None):
        ep_dir = Path(ep_dir)
        self.ep = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
        fmt = self.ep["format"]
        self.w, self.h, self.fps = fmt["width"], fmt["height"], fmt["fps"]
        self.duration = float(preview or fmt["duration_sec"])
        self.n_frames = int(round(self.duration * self.fps))
        self.ground = self.ep["stage"]["ground_y"]
        self.scenes = self.ep["scenes"]
        self.bgs = ensure_backgrounds([s["bg"] for s in self.scenes])
        self.chars = {}
        for cid in self.ep["characters"]:
            cdef = yaml.safe_load((ROOT / "characters" / cid / "character.yaml").read_text(encoding="utf-8"))
            self.chars[cid] = Character(cdef)
        self.tl = Timeline(self.ep, {cid: ch.k for cid, ch in self.chars.items()})
        self.tracks = self.tl.tracks
        self.props = {}
        for name, pdef in self.ep.get("props", {}).items():
            self.props[name] = dict(pdef, track=self.tl.props[name],
                                    sprite=make_balloon(hexrgb(pdef["color"])))
        sh = Image.new("RGBA", (300, 60), (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse((0, 0, 299, 59), fill=(0, 0, 0, 70))
        self.shadow = sh
        self._overlay_cache = {}

    # --- состояния (логика движения вынесена в bos_anim.py и общая для 2D и 3D)
    def actor_state(self, aid, t):
        return self.tl.actor_state(aid, t)

    def actor_pose(self, st, t):
        return self.tl.actor_pose(st, t)

    def prop_state(self, name, t):
        return self.tl.prop_state(name, t)

    # --- кадр
    def background(self, t):
        cur = max(i for i, sc in enumerate(self.scenes) if sc["t"][0] <= t + 1e-9)
        img = self.bgs[self.scenes[cur]["bg"]]
        for i in range(1, len(self.scenes)):
            b = self.scenes[i]["t"][0]
            if abs(t - b) < 0.25:
                a, c = self.bgs[self.scenes[i - 1]["bg"]], self.bgs[self.scenes[i]["bg"]]
                if a is not c:
                    return Image.blend(a, c, (t - b + 0.25) / 0.5)
        return img.copy()

    def _text_overlay(self, key, lines):
        if key not in self._overlay_cache:
            ov = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
            d = ImageDraw.Draw(ov)
            fonts = [ImageFont.truetype(FONT_BOLD, s) for s, _ in lines]
            tot = sum(f.size * 1.3 for f in fonts)
            wmax = max(d.textlength(txt, font=f) for f, (_, txt) in zip(fonts, lines))
            x0, y0 = (self.w - wmax) / 2 - 60, (self.h - tot) / 2 - 40
            d.rounded_rectangle((x0, y0, x0 + wmax + 120, y0 + tot + 60), 40, fill=(20, 24, 50, 150))
            y = y0 + 30
            for f, (_, txt) in zip(fonts, lines):
                d.text((self.w / 2 - d.textlength(txt, font=f) / 2, y), txt, font=f, fill=(255, 255, 255, 255))
                y += f.size * 1.3
            self._overlay_cache[key] = ov
        return self._overlay_cache[key]

    def frame(self, i):
        t = i / self.fps
        img = self.background(t)
        states = {a: self.actor_state(a, t) for a in self.tracks}
        # тени
        for a, st in states.items():
            P = self.actor_pose(st, t)
            k = self.chars[a].k
            sc = max(0.4, 1 + P["dy"] / 500.0)
            sh = self.shadow.resize((int(300 * k * sc), int(56 * k * sc)))
            img.paste(sh, (round(st["x"] - sh.width / 2), round(self.ground - sh.height / 2 + 4)), sh)
        # персонажи
        for a, st in states.items():
            P = self.actor_pose(st, t)
            cv = self.chars[a].render(P)
            if st["face"] < 0:
                cv = ImageOps.mirror(cv)
            img.paste(cv, (round(st["x"] - CX), round(self.ground - GY + P["lift"])), cv)
        # шарик
        d = ImageDraw.Draw(img)
        for name, pr in self.props.items():
            if t < pr["track"][0]["t"] - 1e-9:
                continue
            pos, end, sc = self.prop_state(name, t)
            start = (pos[0], pos[1] + 66 * sc)
            mid = ((start[0] + end[0]) / 2 + 10 * math.sin(t * 3), (start[1] + end[1]) / 2)
            pts = [((1 - u) ** 2 * start[0] + 2 * u * (1 - u) * mid[0] + u * u * end[0],
                    (1 - u) ** 2 * start[1] + 2 * u * (1 - u) * mid[1] + u * u * end[1])
                   for u in np.linspace(0, 1, 14)]
            d.line(pts, fill=(70, 70, 70), width=max(2, round(3 * sc)))
            spr = pr["sprite"].img
            if abs(sc - 1) > 0.01:
                spr = spr.resize((max(1, round(spr.width * sc)), max(1, round(spr.height * sc))), Image.LANCZOS)
            img.paste(spr, (round(pos[0] - spr.width / 2), round(pos[1] - spr.height / 2)), spr)
        # титры
        for ov in self.ep.get("overlays", []):
            a, b = ov["t"]
            if a <= t <= b:
                alpha = min(1.0, (t - a) / 0.5, (b - t) / 0.5)
                layer = self._text_overlay(ov["id"], [(l["size"], l["text"]) for l in ov["lines"]])
                if alpha < 1:
                    layer = layer.copy()
                    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
                img.paste(layer, (0, 0), layer)
        fade = self.ep.get("fade_out")
        if fade and t >= fade[0]:
            k = min(1.0, (t - fade[0]) / (fade[1] - fade[0]))
            img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), k)
            end_ov = self.ep.get("end_card")
            if end_ov and k > 0.55:
                layer = self._text_overlay("end", [(l["size"], l["text"]) for l in end_ov])
                layer = layer.copy()
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * min(1.0, (k - 0.55) / 0.3))))
                img.paste(layer, (0, 0), layer)
        return img
