"""BonnieAndOllieStudio: логика движения (чистый Python, без Pillow).
Общая для 2D (bos_render.py) и 3D (blender/bos_blender.py): позы, ключевые кадры, шарик.
"""
import math
from bisect import bisect_right

BLEND = 0.2   # сек на плавный переход между анимациями


def lerp(a, b, s):
    return a + (b - a) * s


def smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


POSE0 = dict(dy=0.0, lift=0.0, char_rot=0.0, body_rot=0.0, head_rot=0.0,
             arm_l=0.0, arm_r=0.0, leg_l=0.0, leg_r=0.0, face="normal")
W = 2 * math.pi


def pose(anim, t, u):
    """Поза персонажа в момент t (сек). u — прогресс отрезка 0..1."""
    P = dict(POSE0)
    sn = math.sin
    if anim == "walk":
        s = sn(t * W * 1.3)
        P.update(leg_l=32 * s, leg_r=-32 * s, arm_l=-28 * s, arm_r=28 * s,
                 dy=-abs(s) * 7, body_rot=2 * s, head_rot=-1.5 * s)
    elif anim == "run":
        s = sn(t * W * 2.0)
        P.update(leg_l=52 * s, leg_r=-52 * s, arm_l=-62 * s, arm_r=62 * s,
                 dy=-abs(s) * 18, char_rot=-7, head_rot=2 * s, face="laugh")
    elif anim == "jump":
        s = abs(sn(t * W * 0.55))
        P.update(dy=-150 * s, leg_l=14 * (1 - s) + 6, leg_r=-14 * (1 - s) - 6,
                 arm_l=-150, arm_r=150, face="laugh")
    elif anim == "wave":
        s = sn(t * W * 2.2)
        P.update(arm_r=150 + 22 * s, arm_l=4, head_rot=3 * s, dy=-abs(sn(t * W * 1.1)) * 3)
    elif anim == "laugh":
        s = sn(t * W * 3)
        P.update(dy=-abs(s) * 14, head_rot=6 * s, body_rot=3 * s, arm_l=22, arm_r=-22, face="laugh")
    elif anim == "surprise":
        P.update(arm_l=-55 + 3 * sn(t * W * 6), arm_r=55 - 3 * sn(t * W * 6),
                 head_rot=-4, dy=-6, face="oh")
    elif anim == "point":
        P.update(arm_r=105 + 3 * sn(t * W * 2), arm_l=3, head_rot=-5, body_rot=-3)
    elif anim == "trip":
        a = smooth(u / 0.28)
        b = smooth((u - 0.72) / 0.28)
        fall = a * (1 - b)
        P.update(char_rot=-88 * fall, lift=-55 * fall, arm_l=-120 * fall, arm_r=120 * fall,
                 leg_l=20 * fall, leg_r=-20 * fall, face="oh" if u < 0.72 else "laugh")
    else:  # idle
        s = sn(t * W * 0.5)
        P.update(dy=-abs(s) * 3, arm_l=-4 + 3 * s, arm_r=4 - 3 * s, head_rot=2 * s)
    return P


def mix_pose(a, b, k):
    out = {key: lerp(a[key], b[key], k) for key in a if key != "face"}
    out["face"] = b["face"]
    return out




class Timeline:
    """Положение и поза героев и шарика в любой момент времени t (сек)."""

    def __init__(self, ep, char_k):
        self.ground = ep["stage"]["ground_y"]
        self.ck = char_k
        self.tracks = {a: sorted(kfs, key=lambda k: k["t"]) for a, kfs in ep["actors"].items()}
        self.faces = {a: self._faces(kfs) for a, kfs in self.tracks.items()}
        self.props = {n: sorted(p["track"], key=lambda k: k["t"]) for n, p in ep.get("props", {}).items()}

    @staticmethod
    def _faces(kfs):
        out, cur = [], 1
        for i, k in enumerate(kfs):
            dx = (kfs[i + 1]["x"] - k["x"]) if i + 1 < len(kfs) else 0
            if dx:
                cur = 1 if dx > 0 else -1
            elif "face" in k:
                cur = k["face"]
            out.append(cur)
        return out

    def actor_state(self, aid, t):
        kfs, faces = self.tracks[aid], self.faces[aid]
        ts = [k["t"] for k in kfs]
        i = max(0, bisect_right(ts, t) - 1)
        k0 = kfs[i]
        if t <= ts[0] or i == len(kfs) - 1:
            x, u = k0["x"], 1.0
        else:
            k1 = kfs[i + 1]
            u = (t - k0["t"]) / (k1["t"] - k0["t"])
            x = lerp(k0["x"], k1["x"], smooth(u) if k0.get("ease") == "smooth" else u)
        return dict(x=x, face=faces[i], anim=k0.get("anim", "idle"), u=u,
                    tau=max(0.0, t - k0["t"]),
                    prev=kfs[i - 1].get("anim", "idle") if i > 0 else None)

    def actor_pose(self, st, t):
        P = pose(st["anim"], t, st["u"])
        if st["prev"] and st["tau"] < BLEND:
            P = mix_pose(pose(st["prev"], t, 1.0), P, st["tau"] / BLEND)
        return P

    def prop_state(self, name, t):
        kfs = self.props[name]
        ts = [k["t"] for k in kfs]
        i = max(0, bisect_right(ts, t) - 1)

        def at(kf):
            sc = kf.get("scale", 1.0)
            if "attach" in kf:
                st = self.actor_state(kf["attach"], t)
                k = self.ck[kf["attach"]]
                f = st["face"]
                hand = (st["x"] + 70 * f * k, self.ground - 190 * k)
                return (hand[0] + 20 * f, hand[1] - 240), hand, sc
            return (kf["x"], kf["y"]), None, sc

        p0, e0, s0 = at(kfs[i])
        if t <= ts[0] or i == len(kfs) - 1:
            pos, sc, s, e1 = p0, s0, 0.0, None
            p1 = None
        else:
            p1, e1, s1 = at(kfs[i + 1])
            s = (t - ts[i]) / (ts[i + 1] - ts[i])
            pos = (lerp(p0[0], p1[0], s), lerp(p0[1], p1[1], s))
            sc = lerp(s0, s1, s)
        sway = math.sin(t * 2.1) * 14
        bob = math.sin(t * 3.0) * 4
        pos = (pos[0], pos[1] + bob)
        free = (pos[0] + sway, pos[1] + (65 + 170) * sc)
        end0 = e0 or free
        end = end0 if p1 is None else (lerp(end0[0], (e1 or free)[0], s), lerp(end0[1], (e1 or free)[1], s))
        return pos, end, sc

